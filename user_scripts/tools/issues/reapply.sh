#!/usr/bin/env bash
# Re-apply the codebuff/freebuff CPU crash fix after a fresh
# `npm i -g codebuff freebuff` (which overwrites the launchers and can
# replace the binaries).
#
# Safe to run at any time:
#   - binary patch is idempotent (skips already-patched binaries)
#   - launcher hook is only inserted when missing
#   - nothing is written if the expected markers are absent (it fails loudly
#     instead of corrupting a version it does not know)
#
# Full explanation: ~/user_scripts/tools/issues/codebuff-freebuff-cpu-crash-fix.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GLOBAL_ROOT="$(npm root -g 2>/dev/null || echo "$HOME/.local/lib/node_modules")"
BINARIES=(codebuff freebuff)

apply_launcher_hook() {
  python3 - "$1" <<'PYEOF'
import sys

path = sys.argv[1]
src = open(path).read()

fn = '  function ensureCpusFixApplied() {'
call = '    ensureCpusFixApplied()'

if fn in src and call in src:
    print('    hook already present')
    sys.exit(0)

spawn_marker = '  function spawnInstalledBinary(options = {}) {'
call_marker = "    // spawn() only emits 'error' asynchronously"

hook = r'''  // Bun's os.cpus() crashes with "Failed to get CPU information" when the
  // container exposes sparse CPU indices (e.g. cpuset 0,6-13): the parsed
  // /proc/cpuinfo processor index can exceed the count of cpuN lines in
  // /proc/stat. Patch the embedded Bun runtime JS so os.cpus() falls back
  // to stub entries instead of throwing. Re-applied on every launch so app
  // self-updates (which replace the binary) cannot silently re-break it.
  // The write is atomic (unique temp file + rename) so concurrent launches
  // can never exec a torn binary (ETXTBSY) while this rewrites it.
  function ensureCpusFixApplied() {
    const p = CONFIG.binaryPath
    let tmp
    try {
      if (!fs.existsSync(p)) return
      const data = fs.readFileSync(p)
      const risky = Buffer.from('let results = cpus(), length = results.length;')
      if (data.indexOf(risky) < 0) return
      const marker = Buffer.from('function lazyCpus({ cpus, hostCpuCount }) {')
      const start = data.indexOf(marker)
      if (start < 0) return
      const tail = Buffer.from('\n    return array;\n  };\n}')
      const end = data.indexOf(tail, start)
      if (end < 0) return
      const origLen = end - start + tail.length
      const replacement = Buffer.from(
        'function lazyCpus({ cpus, hostCpuCount }) {\n' +
          '  return () => {\n' +
          '    let array;\n' +
          '    try {\n' +
          '      array = cpus();\n' +
          '    } catch (e) {\n' +
          '      array = new @Array(hostCpuCount);\n' +
          '      for (let i = 0; i < array.length; i++)\n' +
          '        array[i] = {\n' +
          '          model: "unknown",\n' +
          '          speed: 0,\n' +
          '          times: { user: 0, nice: 0, sys: 0, idle: 0, irq: 0 },\n' +
          '        };\n' +
          '    }\n' +
          '    return array;\n' +
          '  };\n' +
          '}',
      )
      if (replacement.length > origLen) return
      const pad = Buffer.alloc(origLen - replacement.length, 0x20)
      const patched = Buffer.concat([
        data.slice(0, start),
        replacement,
        pad,
        data.slice(end + tail.length),
      ])
      tmp = p + '.cpusfix.' + process.pid
      fs.writeFileSync(tmp, patched)
      fs.chmodSync(tmp, fs.statSync(p).mode & 0o7777)
      fs.renameSync(tmp, p)
      tmp = null
    } catch {
      if (tmp) {
        try {
          fs.unlinkSync(tmp)
        } catch {
          // temp cleanup is best effort
        }
      }
      // best effort; the app would just crash with the known CPU error
    }
  }
'''

if fn not in src and spawn_marker not in src:
    print(f'    ERROR: insertion marker not found in {path}')
    print('    launcher version differs from the one this script knows;')
    print('    apply the hook manually (see incident doc section 5.3).')
    sys.exit(1)

if call not in src and src.count(call_marker) != 1:
    print(f'    ERROR: call-site marker not found (or not unique) in {path}')
    print('    launcher version differs; no changes were written.')
    sys.exit(1)

if fn not in src:
    src = src.replace(spawn_marker, hook.rstrip('\n') + '\n\n' + spawn_marker, 1)

if call not in src:
    src = src.replace(call_marker, call + '\n\n' + call_marker, 1)

open(path, 'w').write(src)
print('    launcher hook inserted')
PYEOF
}

echo "==> Step 1/2: patching embedded Bun runtime JS in binaries"
fail=0
for name in "${BINARIES[@]}"; do
  bin="${HOME}/.config/manicode/${name}"
  if [ -f "$bin" ]; then
    python3 "$SCRIPT_DIR/patch_cpus.py" "$bin" || true
  else
    echo "    FAIL: $bin not present"
    echo "    run the app once first (it downloads the binary on first launch)"
    fail=1
  fi
done

echo "==> Step 2/2: ensuring launcher auto-repair hooks"
for name in "${BINARIES[@]}"; do
  launcher="$GLOBAL_ROOT/$name/launcher.js"
  echo "  $name: $launcher"
  if [ ! -f "$launcher" ]; then
    echo "    FAIL: launcher not found (package '$name' not installed?)"
    echo "    run: npm i -g $name"
    fail=1
    continue
  fi
  if ! apply_launcher_hook "$launcher"; then
    echo "    FAIL: could not hook $launcher"
    fail=1
  fi
done

echo "==> Verification"
for name in "${BINARIES[@]}"; do
  launcher="$GLOBAL_ROOT/$name/launcher.js"
  bin="${HOME}/.config/manicode/${name}"
  if [ -f "$launcher" ]; then
    if grep -q "ensureCpusFixApplied" "$launcher"; then
      node --check "$launcher" >/dev/null 2>&1 || { echo "    FAIL: $launcher is not valid JS"; fail=1; }
      echo "    ok: $launcher hook present and valid"
    else
      echo "    FAIL: $launcher is missing the hook"
      fail=1
    fi
  fi
  if [ -f "$bin" ] && strings -n 8 "$bin" | grep -q "let results = cpus(), length = results.length;"; then
    echo "    FAIL: $bin still contains the crashing code (patch not applied)"
    fail=1
  elif [ -f "$bin" ]; then
    echo "    ok: $bin patched"
  fi
done
exit $fail

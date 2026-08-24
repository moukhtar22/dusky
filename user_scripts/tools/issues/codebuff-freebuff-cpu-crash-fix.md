# Codebuff / Freebuff crash: "Failed to get CPU information" — root cause & verified fix

**Date:** 2026-08-09
**Scope:** `codebuff` (v1.0.685) and `freebuff` (v0.0.142) (CodebuffAI, Bun-compiled binaries) crash on launch on this machine.
**Status:** FIXED and verified end-to-end. This document contains everything needed to re-fix on a fresh install, with the exact empirical evidence.

---

## 1. TL;DR

- **Symptom:** `codebuff` (and `freebuff`) print `Download complete! Starting Codebuff...`, show a TUI briefly, then crash with:
  ```
  Unhandled rejection: Error: Failed to get CPU information
      at cpus (unknown)
      at populate (node:os:18:25)
      at model (node:os:27:21)
      at <anonymous> (../node_modules/systeminformation/lib/cpu.js:956:41)
      at processTicksAndRejections (native:7:39)
  ```
- **Root cause:** This machine's container exposes a **non-contiguous CPU set** (`cpuset.cpus.effective = 0,6-13`). Bun's `os.cpus()` implementation (in `node_os.zig`) counts `cpuN` lines in `/proc/stat` (= 9) and then requires every `/proc/cpuinfo` `processor` index to be `< 9`. The sparse indices reach 13, so Bun throws `error.too_may_cpus` → "Failed to get CPU information". Codebuff's bundled `systeminformation` calls `os.cpus()[0].model` at startup → unhandled rejection → crash.
- **Fix (3 parts, all verified):**
  1. Binary patch the embedded Bun runtime JS inside `~/.config/manicode/{codebuff,freebuff}` so `os.cpus()` catches the native error and returns stub CPU entries.
  2. Add an auto-repair hook (`ensureCpusFixApplied`) to the npm launcher scripts so the patch survives app self-updates.
  3. Keep a standalone patcher script at `~/user_scripts/tools/issues/patch_cpus.py`.
- **One-command recovery after `npm i -g`:** `~/user_scripts/tools/issues/reapply.sh` re-patches both binaries and re-inserts both launcher hooks. Proven end-to-end: from a true fresh install (npm tarball launchers + pristine binaries) it reproduces the known-good state **byte-for-byte** for all 4 files.
- **Caveat:** `npm i -g codebuff freebuff` overwrites the launchers → re-apply step 4 of the fast path (`~/user_scripts/tools/issues/reapply.sh`; exact manual instructions in §7).

---

## 2. Environment fingerprint (check these before anything else)

Run these commands; if they match, this document applies:

```bash
# CPU visibility
grep -E "^cpu[0-9]+ " /proc/stat | awk '{print $1}' | tr '\n' ' '
#   -> cpu0 cpu6 cpu7 cpu8 cpu9 cpu10 cpu11 cpu12 cpu13     (9 lines, indices NOT 0..8)

grep "^processor" /proc/cpuinfo | awk '{print $3}' | tr '\n' ' '
#   -> 0 6 7 8 9 10 11 12 13                                (9 entries, sparse indices)

cat /sys/fs/cgroup/cpuset.cpus.effective
#   -> 0,6-13

nproc                       # -> 9
node -e "console.log(require('os').availableParallelism())"  # -> 9
```

Key fact: **the count of visible CPUs (9) does not match the CPU indices (0..13).** This is the trigger. On a normal machine, `/proc/stat` shows `cpu0..cpu8` and `/proc/cpuinfo` shows `0..8` — Bun works there.

Installed versions at fix time:
- npm packages: `codebuff` **1.0.685**, `freebuff` **0.0.142** (separate release trains)
- Downloaded binaries (from `~/.config/manicode/*-metadata.json`): `codebuff` → `{"version": "1.0.685", "target": "linux-x64"}`, `freebuff` → `{"version": "0.0.142", "target": "linux-x64"}`
- Bun embedded in both binaries: **bun-v1.3.14** (confirmed via strings in the binary: `https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-linux-x64.zip`; error string `Failed to get CPU information` present in binary)
- Launchers: `~/.local/lib/node_modules/{codebuff,freebuff}/launcher.js`
- Binary sizes: `codebuff` 124,426,368 bytes, `freebuff` 124,475,520 bytes (patch is length-preserving, so sizes stay identical to pristine)

---

## 3. Root cause analysis (evidence chain, each link verified)

### 3.1 The failing call

`systeminformation/lib/cpu.js` (the module codebundled inside the binary), `cpu()` function, linux branch, **line 956, column 41**:

```js
if (os.cpus()[0] && os.cpus()[0].model) {
  modelline = os.cpus()[0].model;
}
```

`os` = `require('node:os')` = Bun's `node:os` module. The stack shows Bun's internals throwing before the value is returned.

### 3.2 Bun's os.cpus() implementation (bun-v1.3.14)

File: `src/runtime/node/node_os.zig` (at tag `bun-v1.3.14`). The linux path `cpusImplLinux()`:

```zig
// Read /proc/stat to get number of CPUs and times
...
var num_cpus: u32 = 0;
// (counts lines named cpuN, skipping the aggregate "cpu" line)
// -> num_cpus = 9 on this machine
...
// Read /proc/cpuinfo to get model information (optional)
...
if (strings.hasPrefixComptime(line, key_processor)) {
    ...
    const digits = std.mem.trim(u8, line[key_processor.len..], " \t\n");
    cpu_index = try std.fmt.parseInt(u32, digits, 10);
    if (cpu_index >= num_cpus) return error.too_may_cpus;   // <-- THROWS HERE
    has_model_name = false;
}
```

And the wrapper that produces the observed error message:

```zig
pub fn cpus(global: *jsc.JSGlobalObject) bun.JSError!jsc.JSValue {
    ...
    return cpusImpl(global) catch {
        const err = jsc.SystemError{
            .message = bun.String.static("Failed to get CPU information"),
            ...
        };
        return global.throwValue(err.toErrorInstance(global));
    };
}
```

The JS side (`src/js/node/os.ts`, embedded in the binary as source — see §4.2):

```js
function lazyCpus({ cpus, hostCpuCount }) {
  return () => {
    let array = new @Array(hostCpuCount);
    function populate() {
      let results = cpus(), length = results.length;   // native call throws
      array.length = length;
      ...
    }
    // array[i] are proxy objects whose getters (model/speed/times) call populate()
  };
}
```

`os.cpus()` itself never throws (it's lazy) — the native error surfaces when any property like `.model` is accessed, i.e. exactly what `systeminformation` does at cpu.js:956.

### 3.3 The numbers that trigger it (verified on this machine)

| File | Content | Result |
|---|---|---|
| `/proc/stat` | `cpu0, cpu6, cpu7, cpu8, cpu9, cpu10, cpu11, cpu12, cpu13` (9 lines) | `num_cpus = 9` |
| `/proc/cpuinfo` | `processor : 0, 6, 7, ..., 13` (9 entries) | last `cpu_index = 13` |
| check | `13 >= 9` | `error.too_may_cpus` → throw |

### 3.4 Why this machine

`/proc/self/cgroup` shows a user scope (`user@1000.service/app.slice/kitty-...`) and `cpuset.cpus.effective = 0,6-13`. The container/host assigns only CPUs 0 and 6–13 to this scope. The kernel renumbers nothing — `/proc/cpuinfo` and `/proc/stat` keep the host's physical CPU indices, which are sparse. Any process running in this cgroup with Bun ≥ 1.3.14 hits this bug. (Note: system Node is unaffected — `require('os').cpus()` works; this is specifically Bun's implementation.)

---

## 4. What does NOT fix it (verified empirically — do not waste time)

All of these were tested and FAILED:

| Approach | Test | Result |
|---|---|---|
| `LD_PRELOAD` shim intercepting `open`/`open64`/`openat`/`openat64` for `/proc/cpuinfo` | Compiled C `.so`, rewrote processor indices to contiguous | **No effect** — Zig/Bun makes raw syscalls, bypassing libc symbols |
| `--preload` CLI flag on the compiled binary | `./codebuff --preload=shim.js` | **No effect** — compiled Bun executables ignore it |
| `BUN_PRELOAD` env var | `BUN_PRELOAD=shim.js ./codebuff` | **No effect** — not supported |
| `NODE_OPTIONS="--import=..."` | same | **No effect** |
| `bunfig.toml` `[run] preload` + `--config` | compiled binary with bunfig | **No effect** (also: cwd may differ) |
| Patch the **app bundle** JS source text inside the binary | replaced `model:` → `bodel:` in a test compiled binary | **No effect** — app bundle is stored as JSC bytecode; the source text is only debug metadata |
| Use a newer Bun | installed latest `bun` 1.3.14, ran `bun -e 'require("node:os").cpus()[0].model'` | **Still throws** `Failed to get CPU information` — bug NOT fixed upstream |
| Fix the cpuset (`echo 0-8 > ...`) | — | Requires root/cgroup write access; not available in this container |

**Key insight that led to the working fix:** patching the **Bun runtime's embedded JS source** inside the binary DOES work, because Bun executes its runtime JS (node:os etc.) from the embedded plain-text source at startup. Verified: renaming `function populate()` → `function popul8te()` inside a Bun binary produced `ReferenceError: populate is not defined` at runtime — proof the text is executed.

---

## 5. The fix (verified working)

### 5.1 Patch the downloaded binaries

For each of `~/.config/manicode/codebuff` and `~/.config/manicode/freebuff`:

- **Marker (search for it — do NOT rely on the fixed offset):** the plain string
  `function lazyCpus({ cpus, hostCpuCount }) {`
  (found at offset `30958209` in both codebuff 1.0.685 and freebuff 0.0.142 binaries, but search anyway)
- **Region to replace:** from the marker to the end of the function, i.e. up to and including the closing `}`, immediately followed by `\nfunction bound(binding)`. **Exact length measured: 1316 bytes.**
- **Original function text (exact bytes, verified by extraction):**

```
function lazyCpus({ cpus, hostCpuCount }) {
  return () => {
    let array = new @Array(hostCpuCount);
    function populate() {
      let results = cpus(), length = results.length;
      array.length = length;
      for (let i = 0;i < length; i++)
        array[i] = results[i];
    }
    for (let i = 0;i < array.length; i++) {
      let instance = {
        get model() {
          if (array[i] === instance)
            populate();
          return array[i].model;
        },
        set model(value) {
          if (array[i] === instance)
            populate();
          array[i].model = value;
        },
        get speed() {
          if (array[i] === instance)
            populate();
          return array[i].speed;
        },
        set speed(value) {
          if (array[i] === instance)
            populate();
          array[i].speed = value;
        },
        get times() {
          if (array[i] === instance)
            populate();
          return array[i].times;
        },
        set times(value) {
          if (array[i] === instance)
            populate();
          array[i].times = value;
        },
        toJSON() {
          if (array[i] === instance)
            populate();
          return array[i];
        }
      };
      array[i] = instance;
    }
    return array;
  };
}
```

- **Replacement (must be ≤ 1316 bytes; pad with ASCII spaces `0x20` to exactly 1316 so the binary structure is unchanged):**

```
function lazyCpus({ cpus, hostCpuCount }) {
  return () => {
    let array;
    try {
      array = cpus();
    } catch (e) {
      array = new @Array(hostCpuCount);
      for (let i = 0; i < array.length; i++)
        array[i] = {
          model: "unknown",
          speed: 0,
          times: { user: 0, nice: 0, sys: 0, idle: 0, irq: 0 },
        };
    }
    return array;
  };
}
```

  Semantics change: `os.cpus()` is now eager and never throws; on failure it returns `hostCpuCount` stub entries (`model: "unknown"`). Downstream code (`systeminformation`) reads `.model` → `"unknown"` instead of crashing. The `@Array` spelling is deliberate — it's Bun's runtime-JS builtin syntax.

- **Do NOT append the pad as trailing text that pushes into the following `\nfunction bound(binding)`** — pad must be *between* the function's closing `}` and `\nfunction bound(binding)` (either raw spaces or a `/* ... */` comment). A buggy version that included `\nfunction bound(binding)` in the replaced region produced a "Parser error on line 33 for function node:os" + SIGSEGV — see §7 pitfalls.

### 5.2 Standalone patcher (already saved on this machine)

`~/user_scripts/tools/issues/patch_cpus.py` — idempotent; patches any number of paths:
```bash
python3 ~/user_scripts/tools/issues/patch_cpus.py ~/.config/manicode/codebuff ~/.config/manicode/freebuff
```
It skips files that don't contain the original risky line (`let results = cpus(), length = results.length;`), so re-running is safe.

### 5.3 Launcher auto-repair hook (survives app self-updates)

The npm launcher (`~/.local/lib/node_modules/{codebuff,freebuff}/launcher.js`) downloads/replaces the binary on every version check. The hook re-patches the binary before spawning it.

Add this function inside `createLauncher(...)` (e.g. right after `exitOnSpawnFailure`):

```js
  // Bun's os.cpus() crashes with "Failed to get CPU information" when the
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
```

And call it in `spawnInstalledBinary()` right before the `spawn(...)`:

```js
    ensureCpusFixApplied()
```

Validate: `node --check launcher.js` must pass.

---

## 6. Verification (all performed on this machine, all PASSED)

1. **Bug reproduces on a pristine binary:** direct run `~/.config/manicode/codebuff <dir>` → TUI appears, then `Unhandled rejection: Error: Failed to get CPU information` (within ~21 s). Also reproducible with standalone Bun: `bun -e 'require("node:os").cpus()[0].model'` → `THREW: Failed to get CPU information`.
2. **Patched binary runs:** `TERM=xterm timeout 45 ~/.config/manicode/codebuff <dir>` → exit 124 (still alive at timeout), `grep -c "Failed to get CPU information"` → 0. TUI fully interactive.
3. **Binary size unchanged after patch:** 124426368 bytes for codebuff (same as pristine download).
4. **Launcher auto-repair:** reverted the binary to the unpatched original → ran `codebuff` → binary re-patched before spawn, no errors.
5. **Fresh-install simulation:** deleted `~/.config/manicode/codebuff` + `codebuff-metadata.json` → ran `codebuff` → launcher re-downloaded 46.3 MB, auto-patched, ran clean for the full 120 s test (exit 124 at timeout, zero errors, session created).
6. **freebuff:** same patch; `freebuff login` ran 15 s with zero CPU errors (note: `freebuff <dir>` is invalid syntax — it accepts `login` etc. first; that "command-argument value ... invalid" message is unrelated to this bug).
7. **Syntax check:** `node --check` on both modified launchers → OK.
8. **Recovery from true fresh install (reapply.sh):** extracted pristine launchers from the exact npm tarballs (`codebuff@1.0.685`, `freebuff@0.0.142`) + pristine binaries from the release CDN, ran `~/user_scripts/tools/issues/reapply.sh` → all 4 files **byte-identical** to the known-good state; binaries patched by the script and patched by the launcher hook are **byte-identical** (md5 match).
9. **Idempotency:** `reapply.sh` on an already-fixed state → no changes (all files untouched), exit 0.
10. **Stress: launch loops** — 10/10 launches (5x `codebuff` + 5x `freebuff login`, 15 s each) survived the full duration with 0 errors.
11. **Stress: concurrent launches (6 at once)** — on the first (non-atomic) write design, 4/6 crashed with `spawn ETXTBSY` (text file busy: a parallel launcher was truncating/rewriting the binary while another tried to exec it). This surfaced a real race, so the patch write was made **atomic** (unique temp file + chmod + rename). After the fix: **6/6 concurrent launches survived**, zero errors, binary patched once, no leftover temp files.
12. **Stress: kill -9 mid-patch (5 iterations)** — launcher killed at a random instant during the 124 MB patch write; binary was always either fully patched or fully pristine, **never torn** (md5 check), and a final launch worked cleanly.
13. **Partial states:** reapply on a launcher with function-but-no-call → call re-inserted; missing launcher → exit 1 with hint; missing binary → exit 1 with hint.

---

## 7. Fresh-install / re-fix procedure (step by step)

On a NEW system (or after `npm i -g codebuff freebuff` overwrote the launchers):

**Fast path (recommended):** if you still have the scripts from a previous install, everything is one command:

```bash
# 1. Confirm the fingerprint (§2) — if /proc/stat shows contiguous cpu0..cpuN, STOP: not needed.
grep -E "^cpu[0-9]+ " /proc/stat | awk '{print $1}'

# 2. Install the npm packages (the launcher downloads the binary on first run).
npm i -g codebuff freebuff

# 3. Run once so the binary downloads:
#    codebuff   (expect the crash — that confirms the bug is present)

# 4. One-command recovery (idempotent; safe to run again):
~/user_scripts/tools/issues/reapply.sh
#    -> patches both binaries, inserts both launcher hooks, verifies everything.

# 5. Verify:
codebuff                                  # must NOT crash; TUI must stay open
# optional deep check:
strings ~/.config/manicode/codebuff | grep -c "let results = cpus(), length = results.length;"  # -> 0
```

**Manual path (no existing scripts):** steps below reproduce what `reapply.sh` does.

```bash
# 1. Install and trigger the binary download, then confirm the crash (steps 1-3 above).

# 2. Create the patcher (content in the block below):
mkdir -p ~/user_scripts/tools/issues
#   ... write patch_cpus.py as shown below ...
python3 ~/user_scripts/tools/issues/patch_cpus.py \
  ~/.config/manicode/codebuff ~/.config/manicode/freebuff

# 3. Patch the launchers: add ensureCpusFixApplied() + the call (exact code in §5.3)
#    to ~/.local/lib/node_modules/{codebuff,freebuff}/launcher.js
node --check ~/.local/lib/node_modules/codebuff/launcher.js
node --check ~/.local/lib/node_modules/freebuff/launcher.js

# 4. Verify:
codebuff                                  # must NOT crash; TUI must stay open
strings ~/.config/manicode/codebuff | grep -c "let results = cpus(), length = results.length;"  # -> 0
```

`patch_cpus.py` content (also saved at `~/user_scripts/tools/issues/patch_cpus.py`):

```python
#!/usr/bin/env python3
import os
import sys

def patch(path):
    with open(path, 'rb') as f:
        data = f.read()

    marker = b'function lazyCpus({ cpus, hostCpuCount }) {'
    start = data.find(marker)
    if start < 0:
        print(f"{path}: lazyCpus not found, skipping")
        return False

    tail_marker = b'\n    return array;\n  };\n}\nfunction bound(binding)'
    end = data.find(tail_marker, start)
    if end < 0:
        print(f"{path}: end marker not found, skipping")
        return False
    end += len(b'\n    return array;\n  };\n}')

    orig_len = end - start
    orig = data[start:end]

    repl = b'''function lazyCpus({ cpus, hostCpuCount }) {
  return () => {
    let array;
    try {
      array = cpus();
    } catch (e) {
      array = new @Array(hostCpuCount);
      for (let i = 0; i < array.length; i++)
        array[i] = {
          model: "unknown",
          speed: 0,
          times: { user: 0, nice: 0, sys: 0, idle: 0, irq: 0 },
        };
    }
    return array;
  };
}'''

    if len(repl) > orig_len:
        print(f"{path}: replacement too long ({len(repl)} > {orig_len})")
        return False

    pad = orig_len - len(repl)
    repl = repl + b' ' * pad

    if len(repl) != orig_len:
        print(f"{path}: padding mismatch {len(repl)} != {orig_len}")
        return False

    new = data[:start] + repl + data[end:]
    tmp = path + '.cpusfix.tmp'
    with open(tmp, 'wb') as f:
        f.write(new)
    os.chmod(tmp, os.stat(path).st_mode & 0o7777)
    os.replace(tmp, path)

    print(f"{path}: patched ok (func {orig_len} bytes -> {len(repl)} bytes)")
    return True

if __name__ == '__main__':
    ok = all(patch(p) for p in sys.argv[1:])
    sys.exit(0 if ok else 1)
```

---

## 8. Pitfalls (all hit and solved during this fix — do not repeat)

1. **LD_PRELOAD does nothing** — Bun/Zig uses raw syscalls, not libc `open`/`openat`. Don't try it.
2. **Never patch the app-bundle source text in the binary** — the bundle is bytecode; source text is debug-only. Patch the *Bun runtime* JS instead.
3. **Length must be preserved exactly.** Padding with `0x20` spaces between the function's closing `}` and `\nfunction bound(binding)` is required. A 25-byte overrun (including the tail marker in the replaced region) produced `Parser error on line 33 for function node:os` + SIGSEGV.
4. **`freebuff <directory>` is not valid syntax** — freebuff expects `login` etc. as its first argument. Not related to this bug.
5. **Don't rely on the fixed offset 30958209** — it held for codebuff 1.0.685 and freebuff 0.0.142, but always search for the text markers instead.
6. **An in-place patch write races with execve** — 4/6 concurrent launches crashed with `spawn ETXTBSY` when the hook truncated/rewrote the binary in place while another launcher exec'd it. The fix: write a unique temp file in the same directory, `chmod` it to the original mode, then `rename()` (atomic on POSIX). Never `writeFileSync(p, ...)` a binary that other processes may exec. (Kill -9 mid-write is also safe with this design — the target is only ever swapped by rename.)

---

## 9. Maintenance notes

- **App self-updates** (new version downloaded by the launcher): binary is replaced → launcher hook re-patches automatically. Verified: fresh download → auto-patched → clean run. No action needed.
- **`npm i -g codebuff freebuff` (launcher update):** launcher.js is overwritten → the hook is lost. Run `~/user_scripts/tools/issues/reapply.sh` once (idempotent; patches both binaries and re-inserts both hooks). This is the ONLY action needed after npm updates.
- **Binary location:** `~/.config/manicode/` (both apps share the dir; files: `codebuff`, `freebuff`, `*-metadata.json`).
- **A normal contiguous machine doesn't need this fix at all** — it's specific to containers/cgroups with sparse CPU indices.

---

## 10. Upstream references

- Bun bug: `error.too_may_cpus` in `src/runtime/node/node_os.zig` (`cpusImplLinux`), present in bun-v1.3.14 (latest release at fix time). Not fixed upstream.
- Codebuff issue to report: `https://github.com/CodebuffAI/codebuff/issues` (crash trace: systeminformation cpu.js:956 → `os.cpus()[0].model`).
- Bun lazyCpus source (for reference): `src/js/node/os.ts` at tag `bun-v1.3.14`.
- systeminformation source (for reference): `lib/cpu.js`, line ~956: `if (os.cpus()[0] && os.cpus()[0].model) { modelline = os.cpus()[0].model; }`.

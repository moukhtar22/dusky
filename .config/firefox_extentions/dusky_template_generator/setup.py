#!/usr/bin/env python3
"""
🦊 Dusky Template Generator - native host installer.

    python3 setup.py              install or repair (safe to re-run any time)
    python3 setup.py --uninstall  remove the host manifests again (or --purge)

What it does:
  1. byte-compiles host/dusky_template_host.py to catch syntax errors early
  2. marks host/dusky_template_host.py executable for its owner
  3. writes <browser root>/native-messaging-hosts/dusky_template_generator.json
     atomically - always for ~/.mozilla (Firefox, Dev Edition, Nightly), and for
     LibreWolf / Zen / Waterfox / Floorp only when that browser's profile root
     already exists (no empty dot-directories are ever created)
  4. ensures the templates directory exists (~/.config/dusky_sites)
  5. runs the host once over the real stdio protocol, exec'd exactly the way
     Firefox will exec it, and refuses to report success unless it answers correctly

It never touches ~/.config/dusky_sites/*.css, extensions.json or browser profiles.
"""

from __future__ import annotations

import json
import os
import stat
import struct
import subprocess
import sys
from pathlib import Path

HOST_NAME = "dusky_template_generator"
EXTENSION_ID = "dusky_template_generator@dusk.com"
HERE = Path(__file__).resolve().parent
HOST_SCRIPT = HERE / "host" / "dusky_template_host.py"
LOCAL_MANIFEST = HERE / "host" / f"{HOST_NAME}.json"
HOME = Path.home()

# (profile root, always install?)  Firefox-family browsers read <root>/native-messaging-hosts/.
BROWSER_ROOTS = [
    (HOME / ".mozilla", True),
    (HOME / ".librewolf", False),
    (HOME / ".zen", False),
    (HOME / ".waterfox", False),
    (HOME / ".floorp", False),
]

TTY = sys.stdout.isatty()
OK = "\033[32m+\033[0m" if TTY else "+"
WARN = "\033[33m!\033[0m" if TTY else "!"
BAD = "\033[31mx\033[0m" if TTY else "x"
BOLD = "\033[1m" if TTY else ""
DIM = "\033[2m" if TTY else ""
RESET = "\033[0m" if TTY else ""


def manifest_text() -> str:
    payload = {
        "name": HOST_NAME,
        "description": "Dusky Template Generator - writes ~/.config/dusky_sites/<domain>.css",
        "path": str(HOST_SCRIPT),
        "type": "stdio",
        "allowed_extensions": [EXTENSION_ID],
    }
    return json.dumps(payload, indent=2) + "\n"


def write_atomic(path: Path, text: str) -> bool:
    """Write text to path via rename. Returns False when the file already had this content."""
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def target_dirs(existing_only: bool) -> list[Path]:
    dirs = []
    for root, always in BROWSER_ROOTS:
        if root.is_dir() or (always and not existing_only):
            dirs.append(root / "native-messaging-hosts")
    return dirs


def probe_host() -> dict:
    """Send one 'ping' frame to the host, exec'ing it via its shebang like Firefox does."""
    body = json.dumps({"type": "ping"}).encode("utf-8")
    proc = subprocess.run(
        [str(HOST_SCRIPT)],
        input=struct.pack("=I", len(body)) + body,
        capture_output=True,
        timeout=15,
        check=False,
    )
    out = proc.stdout
    if len(out) < 4:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(err or f"host exited with status {proc.returncode} and no reply")
    (length,) = struct.unpack("=I", out[:4])
    return json.loads(out[4:4 + length].decode("utf-8"))


def install() -> int:
    print(f"\n{BOLD}🦊 Dusky Template Generator - host setup{RESET}\n")
    if not HOST_SCRIPT.is_file():
        print(f"{BAD} host script missing: {HOST_SCRIPT}")
        return 1

    try:
        compile(HOST_SCRIPT.read_text(encoding="utf-8"), str(HOST_SCRIPT), "exec")
    except SyntaxError as exc:
        print(f"{BAD} host does not compile: {exc}")
        return 1
    print(f"{OK} host compiles      {HOST_SCRIPT}")

    mode = HOST_SCRIPT.stat().st_mode
    if not (mode & stat.S_IXUSR):
        HOST_SCRIPT.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"{OK} host executable   {HOST_SCRIPT}")

    text = manifest_text()
    write_atomic(LOCAL_MANIFEST, text)

    for d in target_dirs(existing_only=False):
        try:
            changed = write_atomic(d / f"{HOST_NAME}.json", text)
            print(f"{OK} manifest {'written  ' if changed else 'unchanged'} {d / (HOST_NAME + '.json')}")
        except OSError as exc:
            print(f"{WARN} skipped {d}: {exc}")

    try:
        reply = probe_host()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"\n{BAD} the host does not answer when exec'd like Firefox will exec it:\n   {exc}")
        print("   Check: is python3 on PATH? is the extension folder on a filesystem mounted 'noexec'?")
        print(f"   Debug: {DIM}python3 {HOST_SCRIPT} --selftest{RESET}")
        return 1
    if not reply.get("ok"):
        print(f"\n{BAD} host replied with an error: {reply}")
        return 1
    templates = Path(reply["dir"])
    templates.mkdir(parents=True, exist_ok=True)
    print(f"{OK} host answers      version {reply.get('version')} on python {reply.get('python')}")
    print(f"{OK} templates dir     {templates}")

    print(f"\n{BOLD}Load the extension in Firefox:{RESET}")
    print(f"  1. Go to {BOLD}about:debugging#/runtime/this-firefox{RESET}")
    print(f"  2. Click {BOLD}'Load Temporary Add-on...'{RESET}")
    print(f"  3. Select: {BOLD}{HERE / 'manifest.json'}{RESET}")
    print(f"  4. Open any site, click the toolbar icon or press {BOLD}Alt+Shift+P{RESET} to start visual picking.")
    print(f"\n{DIM}Re-run this script after moving the folder. Remove with: python3 setup.py --uninstall{RESET}\n")
    return 0


def uninstall() -> int:
    removed = 0
    for d in target_dirs(existing_only=True):
        manifest = d / f"{HOST_NAME}.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if data.get("allowed_extensions") == [EXTENSION_ID]:
                manifest.unlink()
                removed += 1
                print(f"{OK} removed {manifest}")
    print(f"\n{OK} done - {removed} manifest(s) removed; your templates in ~/.config/dusky_sites were left untouched.\n")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("--uninstall", "--purge"):
        return uninstall()
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    return install()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

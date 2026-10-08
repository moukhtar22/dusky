#!/usr/bin/env python3
"""
216_systemd_accounting_optimizer.py
Target: Arch Linux (Linux Kernel 7.3+, systemd 262+)
Scope: Tune systemd default resource accounting and task limits.
Balances lowest RAM usage with uncompromised performance and system stability.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

SYSTEMD_CONF_DIR = Path("/etc/systemd/system.conf.d")
DROPIN_FILE = SYSTEMD_CONF_DIR / "99-default-accounting.conf"
USER_CONF_DIR = Path("/etc/systemd/user.conf.d")
USER_DROPIN_FILE = USER_CONF_DIR / "99-default-accounting.conf"

VALID_KEYS = [
    "DefaultMemoryAccounting",
    "DefaultTasksAccounting",
    "DefaultIOAccounting",
    "DefaultIPAccounting",
]

DESIRED_STATE: dict[str, str] = {
    "DefaultMemoryAccounting": "yes",
    "DefaultTasksAccounting": "yes",
    "DefaultIOAccounting": "no",
    "DefaultIPAccounting": "no",
}

class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    RST = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for name in ("BOLD", "DIM", "RED", "GRN", "YLW", "BLU", "RST"):
            setattr(cls, name, "")

QUIET = False

def info(msg: str) -> None:
    if not QUIET:
        print(f"{C.BLU}[INFO]{C.RST} {msg}")

def ok(msg: str) -> None:
    if not QUIET:
        print(f"{C.GRN}[ OK ]{C.RST} {msg}")

def warn(msg: str) -> None:
    print(f"{C.YLW}[WARN]{C.RST} {msg}")

def err(msg: str) -> None:
    print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)

def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)

def run(*cmd: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), text=True, capture_output=True, check=check)

def is_systemd_running() -> bool:
    try:
        return Path("/run/systemd/system").exists()
    except Exception:
        return False

def get_manager_defaults() -> dict[str, str]:
    args = ["systemctl", "show"]
    for k in VALID_KEYS:
        args.extend(["-p", k])
    try:
        r = run(*args, check=True)
    except FileNotFoundError:
        die("systemctl not found in PATH")
    except subprocess.CalledProcessError as e:
        die(f"Failed to query manager defaults: {e.stderr.strip() or e}")

    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k in VALID_KEYS:
            out[k] = v

    for k in VALID_KEYS:
        out.setdefault(k, "")
    return out



def write_dropin_atomic(target: Path, content: str, *, force: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not force and target.exists() and target.read_text(encoding="utf-8") == content and target.stat().st_mode & 0o777 == 0o644:
        return
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, target)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass

def generate_payload() -> str:
    return """# Managed by 216_systemd_accounting_optimizer.py
# Target: Arch Linux (Linux Kernel 7.3+, systemd 262+)
# Scope: Enable default Memory and Tasks accounting for systemd-oomd and task limits,
# while leaving IO and IP accounting disabled to avoid unnecessary kernel overhead.

[Manager]
DefaultMemoryAccounting=yes
DefaultTasksAccounting=yes
DefaultIOAccounting=no
DefaultIPAccounting=no
"""

def reexec_systemd_manager() -> None:
    info("Re-executing systemd manager (daemon-reexec) to apply [Manager] settings...")
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            run("systemctl", "daemon-reexec", check=True)
            return
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "") + (e.stdout or "")
            if "rate limit" in stderr.lower() or "limit" in stderr.lower():
                if attempt < max_retries:
                    wait = attempt * 2
                    warn(f"daemon-reexec rate-limited, retrying in {wait}s (attempt {attempt}/{max_retries})...")
                    time.sleep(wait)
                    continue
            die(f"daemon-reexec failed: {stderr.strip() or e}")

def verify_live_values() -> bool:
    info("Verifying live manager values...")
    for i in range(6):
        time.sleep(0.3 if i == 0 else 0.5)
        new_vals = get_manager_defaults()
        if all(new_vals.get(k) == DESIRED_STATE[k] for k in VALID_KEYS):
            ok("Verified live values in PID 1:")
            for k in VALID_KEYS:
                ok(f"  {k} = {new_vals.get(k)}")
            return True
    return False

def main(argv: list[str]) -> int:
    global QUIET
    ap = argparse.ArgumentParser(
        prog="216_systemd_accounting_optimizer.py",
        description="Optimize systemd default accounting and task limits for Arch Linux (systemd 262+, Kernel 7.3+). "
                    "Enables default Memory and Tasks accounting for systemd-oomd and task limits, "
                    "while leaving IO and IP accounting disabled to avoid unnecessary kernel overhead.",
    )
    ap.add_argument("-n", "--dry-run", action="store_true", help="Preview configuration without applying")
    ap.add_argument("--status", action="store_true", help="Show current manager defaults and exit")
    ap.add_argument("--restore", action="store_true", help="Remove drop-in configuration and restore system defaults")
    ap.add_argument("-f", "--force", action="store_true", help="Rewrite drop-in even if already matching")
    ap.add_argument("-q", "--quiet", action="store_true", help="Suppress info/ok messages")
    ap.add_argument("-y", "--yes", action="store_true", help="Accept defaults non-interactively")
    ap.add_argument("--no-color", action="store_true", help="Disable colored output")
    args = ap.parse_args(argv)

    if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        C.strip()
    QUIET = args.quiet

    if args.status:
        if not is_systemd_running():
            warn("systemd does not appear to be running as PID 1.")
        vals = get_manager_defaults() if is_systemd_running() else {}
        print(f"\n{C.BOLD}--- systemd Manager Defaults ---{C.RST}")
        for k in VALID_KEYS:
            cur = vals.get(k, "(unknown)")
            target = DESIRED_STATE[k]
            status_tag = f"{C.GRN}[MATCH]{C.RST}" if cur == target else f"{C.YLW}[DIFF (target: {target})]{C.RST}"
            print(f"  {k:25} = {cur:<10} {status_tag}")
        sys_dropin_status = f"{C.GRN}present{C.RST}" if DROPIN_FILE.exists() else f"{C.DIM}absent{C.RST}"
        user_dropin_status = f"{C.GRN}present{C.RST}" if USER_DROPIN_FILE.exists() else f"{C.DIM}absent{C.RST}"
        print(f"\nSystem Drop-in ({DROPIN_FILE}): {sys_dropin_status}")
        print(f"User Drop-in   ({USER_DROPIN_FILE}): {user_dropin_status}\n")
        return 0

    payload = generate_payload()
    if args.dry_run:
        print(f"\n{C.BOLD}[DRY RUN] Target files:{C.RST}")
        print(f"  System: {DROPIN_FILE}")
        print(f"  User:   {USER_DROPIN_FILE}\n")
        print(payload)
        return 0

    if os.geteuid() != 0:
        if shutil.which("sudo") is None:
            die("Root privileges required (sudo not found in PATH). Please run as root.")
        info("Root privileges required. Escalating via sudo...")
        os.execvp("sudo", ["sudo", "--", sys.executable, str(Path(__file__).resolve()), *argv])

    if not is_systemd_running():
        die("systemd does not appear to be running as PID 1 (no /run/systemd/system). Are you in a chroot/container?")

    if args.restore:
        removed_any = False
        for target in (DROPIN_FILE, USER_DROPIN_FILE):
            if target.exists():
                try:
                    target.unlink()
                    ok(f"Removed {target}")
                    removed_any = True
                except Exception as e:
                    die(f"Failed to remove {target}: {e}")
        if not removed_any:
            ok("No optimizer drop-ins exist to remove.")
            return 0
        reexec_systemd_manager()
        ok("Restore complete. Manager defaults reset to system defaults.")
        return 0

    vals = get_manager_defaults()
    already_opt = all(vals.get(k) == DESIRED_STATE[k] for k in VALID_KEYS)
    try:
        write_dropin_atomic(DROPIN_FILE, payload, force=args.force)
        ok(f"Wrote atomic configuration to {DROPIN_FILE} (0644)")
        write_dropin_atomic(USER_DROPIN_FILE, payload, force=args.force)
        ok(f"Wrote atomic configuration to {USER_DROPIN_FILE} (0644)")
    except Exception as e:
        die(f"Failed writing drop-in file: {e}")

    if already_opt and not args.force:
        ok("Live system manager defaults already match; no re-exec needed.")
        info("User defaults apply when each user manager next starts.")
        return 0

    reexec_systemd_manager()

    if verify_live_values():
        ok("Systemd accounting and task optimization completed successfully.")
        return 0

    warn("Manager re-exec completed, but some parameters did not immediately match.")
    warn(f"Check current values via: systemctl show {' '.join(f'-p {k}' for k in VALID_KEYS)}")
    return 1

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print(f"\n{C.YLW}Aborted by user.{C.RST}")
        sys.exit(130)

#!/usr/bin/env python3
#d: Tune systemd resource accounting defaults

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

VALID_KEYS = [
    "DefaultMemoryAccounting",
    "DefaultTasksAccounting",
    "DefaultIOAccounting",
    "DefaultIPAccounting",
]

DESIRED_STATE: dict[str, str] = {
    "DefaultMemoryAccounting": "no",
    "DefaultTasksAccounting": "no",
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
    """Robustly query manager properties via `systemctl show` without --value.

    Parsing key=value avoids fragility of --value ordering.
    """
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

    if len(out) != len(VALID_KEYS):
        try:
            props = ",".join(VALID_KEYS)
            r2 = run("systemctl", "show", "--all", f"-p{props}", check=True)
            for line in r2.stdout.splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() in VALID_KEYS:
                    out.setdefault(k.strip(), v.strip())
        except Exception:
            pass

    for k in VALID_KEYS:
        out.setdefault(k, "")
    return out

def is_oomd_active() -> bool:
    try:
        r = run("systemctl", "is-active", "--quiet", "systemd-oomd.service", check=False)
        return r.returncode == 0
    except Exception:
        return False

def write_dropin_atomic(target: Path, content: str) -> None:
    """Atomic write: tmpfile in same dir, chmod 0644, rename.

    Drop-ins in /etc/systemd/system.conf.d override main file.
    0644 is standard for systemd configs.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
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

def main(argv: list[str]) -> int:
    global QUIET
    ap = argparse.ArgumentParser(
        prog="systemd_accounting_optimizer",
        description="Optimize systemd default accounting (systemd 260+, Arch, kernel 7.1+). "
        "Disables DefaultMemoryAccounting/TasksAccounting to reduce userspace bookkeeping. "
        "Allows explicit overrides in slice configurations to preserve systemd-oomd.",
    )
    ap.add_argument("-n", "--dry-run", action="store_true", help="Preview without writing")
    ap.add_argument("--no-color", action="store_true", help="Disable colored output")
    ap.add_argument("-q", "--quiet", action="store_true", help="Suppress info/ok messages")
    ap.add_argument("-f", "--force", action="store_true", help="Rewrite even if already optimized")
    ap.add_argument("--restore", action="store_true", help="Remove optimizer drop-in and restore defaults")
    ap.add_argument("--status", action="store_true", help="Show current manager defaults and exit")
    ap.add_argument("-y", "--yes", action="store_true", help="Bypass systemd-oomd warning")
    args = ap.parse_args(argv)

    if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        C.strip()
    QUIET = args.quiet

    if not is_systemd_running():
        die("systemd does not appear to be running (no /run/systemd/system). Are you in a chroot/container?")

    if args.status:
        vals = get_manager_defaults()
        for k in VALID_KEYS:
            print(f"{k}={vals.get(k, '(unknown)')}")
        print(f"drop-in: {DROPIN_FILE} {'exists' if DROPIN_FILE.exists() else 'absent'}")
        return 0

    if args.restore:
        if os.geteuid() != 0:
            if shutil.which("sudo") is None:
                die("root required to restore; sudo not found")
            info("root required — re-exec via sudo")
            os.execvp("sudo", ["sudo", "--", sys.executable, str(Path(__file__).resolve()), "--restore", *([ "--no-color" ] if args.no_color else []), *(["--quiet"] if args.quiet else [])])
        if not DROPIN_FILE.exists():
            ok("No optimizer drop-in to remove.")
            return 0
        try:
            DROPIN_FILE.unlink()
            ok(f"Removed {DROPIN_FILE}")
        except Exception as e:
            die(f"Failed to remove {DROPIN_FILE}: {e}")
        info("Re-executing systemd manager to apply restore...")
        try:
            run("systemctl", "daemon-reexec", check=True)
        except subprocess.CalledProcessError as e:
            die(f"daemon-reexec failed after restore: {e.stderr}")
        ok("Restore complete. Defaults now reset to system default values.")
        return 0

    vals = get_manager_defaults()
    formatted = ", ".join(f"{k}={v or '(empty)'}" for k, v in vals.items())
    info(f"Current manager defaults: {formatted}")

    already_opt = all(vals.get(k) == DESIRED_STATE[k] for k in VALID_KEYS)
    if already_opt and not args.force:
        ok("Systemd default accounting is already optimized.")
        if DROPIN_FILE.exists():
            ok(f"Drop-in present: {DROPIN_FILE}")
        return 0

    if os.geteuid() != 0 and not args.dry_run and not args.status:
        if shutil.which("sudo") is None:
            die("root privileges required (sudo not found). Please run as root.")
        info("root privileges required — escalating via sudo")
        os.execvp("sudo", ["sudo", "--", sys.executable, str(Path(__file__).resolve()), *argv])

    if is_oomd_active() and not args.yes and not args.dry_run:
        warn("systemd-oomd is active. Disabling DefaultMemoryAccounting globally may affect it.")
        warn("However, if your slice configs (e.g. app.slice, background.slice) explicitly enable")
        warn("MemoryAccounting=yes, systemd-oomd will continue to function normally for those slices.")
        warn("Use --yes to bypass this prompt or ensure your slices explicitly enable memory accounting.")
        if sys.stdin.isatty():
            try:
                ans = input("Continue anyway? [y/N]: ").strip().lower()
                if ans not in ("y", "yes"):
                    info("Aborted by user.")
                    return 1
            except (EOFError, KeyboardInterrupt):
                print()
                return 130
        else:
            die("systemd-oomd is active and stdin is not a TTY. Use --yes to proceed.")

    payload = f"""# Managed by 216_systemd_accounting_optimizer.py
# Scope: Disable global systemd default accounting to reduce userspace bookkeeping.
# Note: On unified cgroup v2, CPU accounting is always available.
# Disabling global default accounting reduces cgroup metadata/bookkeeping overhead,
# while explicit slice configurations (e.g., app.slice, background.slice) can still
# enable MemoryAccounting=yes explicitly to allow systemd-oomd monitoring.
# See systemd-system.conf(5): valid keys DefaultMemoryAccounting, DefaultTasksAccounting,
# DefaultIOAccounting, DefaultIPAccounting.

[Manager]
DefaultMemoryAccounting=no
DefaultTasksAccounting=no
DefaultIOAccounting=no
DefaultIPAccounting=no
"""

    if args.dry_run:
        print(f"\n{C.BOLD}[DRY RUN] Would write to {DROPIN_FILE}:{C.RST}\n{payload}")
        return 0

    try:
        write_dropin_atomic(DROPIN_FILE, payload)
        ok(f"Wrote atomic drop-in to {DROPIN_FILE} (0644)")
    except Exception as e:
        die(f"Failed to write drop-in: {e}")

    info("Re-executing systemd manager (daemon-reexec) to apply [Manager] settings...")
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            run("systemctl", "daemon-reexec", check=True)
            break
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "") + (e.stdout or "")
            if "rate limit" in stderr.lower() or "limit" in stderr.lower():
                if attempt < max_retries:
                    wait = attempt * 2
                    warn(f"daemon-reexec rate-limited, retrying in {wait}s (attempt {attempt}/{max_retries})")
                    time.sleep(wait)
                    continue
            die(f"daemon-reexec failed: {stderr.strip() or e}")

    info("Verifying live manager values...")
    for i in range(6):
        time.sleep(0.4 if i == 0 else 0.5)
        new_vals = get_manager_defaults()
        if all(new_vals.get(k) == DESIRED_STATE[k] for k in VALID_KEYS):
            ok("Verified live values:")
            for k in VALID_KEYS:
                ok(f"  {k} = {new_vals.get(k)}")
            ok("Optimization complete.")
            return 0

    die(f"Verification failed after reexec: got {new_vals}. Try `systemctl daemon-reexec` again or reboot. "
        f"Check `systemctl show -p DefaultMemoryAccounting -p DefaultTasksAccounting`.")

if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print(f"\n{C.YLW}aborted — no further changes.{C.RST}")
        sys.exit(130)

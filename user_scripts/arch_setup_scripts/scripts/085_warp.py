#!/usr/bin/env python
#d: Install, configure, and manage Cloudflare WARP

import argparse
import fcntl
import getpass
import json
import os
import pathlib
import pwd
import pty
import re
import select
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn

# ─── Rich (hard requirement) ─────────────────────────────────────────────

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm
    from rich.table import Table
except ImportError:
    sys.stderr.write("Error: python-rich is required. Install: pacman -S python-rich\n")
    sys.exit(1)

# ─── Version Guard ───────────────────────────────────────────────────────

if sys.version_info < (3, 14):
    sys.stderr.write("This script requires Python 3.14 or later.\n")
    sys.exit(1)

# ─── Constants ───────────────────────────────────────────────────────────

AUR_REPO = "https://aur.archlinux.org/cloudflare-warp-nox-bin.git"
PKG_NAME = "cloudflare-warp-nox-bin"
SERVICE_NAME = "warp-svc.service"
SYSTEM_STATE_DIR = pathlib.Path("/var/lib/cloudflare-warp")
BUILD_DEPS: tuple[str, ...] = ("base-devel", "git")
LOCK_PATH = pathlib.Path("/run/warp-manager.lock")

type OptPath = str | pathlib.Path | None

# ─── Console & Logging ───────────────────────────────────────────────────

console = Console()
_DEBUG = False


def _set_debug(val: bool) -> None:
    global _DEBUG
    _DEBUG = val


def _debug(msg: str) -> None:
    if _DEBUG:
        sys.stderr.write(f"  DEBUG: {msg}\n")


def log_info(msg: str) -> None:
    console.print(f"  [bold blue]ℹ[/bold blue]  {msg}")


def log_success(msg: str) -> None:
    console.print(f"  [bold green]✓[/bold green]  {msg}")


def log_warn(msg: str) -> None:
    console.print(f"  [bold yellow]⚠[/bold yellow]  {msg}")


def log_error(msg: str) -> None:
    console.print(f"  [bold red]✖[/bold red]  {msg}")


def log_step(msg: str) -> None:
    console.print()
    console.print(f"[bold cyan]:: {msg}[/bold cyan]")


def die(msg: str, code: int = 1) -> NoReturn:
    log_error(msg)
    sys.exit(code)


# ─── Real-User Detection & Privilege Elevation ──────────────────────────

_real_user: str | None = None


def detect_real_user() -> str:
    """Detect the invoking unprivileged user across escalation vectors."""
    # 1. SUDO_USER is set whenever the script was invoked via sudo
    user = os.environ.get("SUDO_USER")
    if user and user != "root":
        return user

    # 2. PKEXEC_UID (polkit)
    pkexec_uid = os.environ.get("PKEXEC_UID")
    if pkexec_uid:
        try:
            pw = pwd.getpwuid(int(pkexec_uid))
            if pw.pw_name and pw.pw_name != "root":
                return pw.pw_name
        except (KeyError, ValueError):
            pass

    # 3. logname(1) — user owning the session
    try:
        out = subprocess.run(
            ["logname"], capture_output=True, text=True, timeout=3
        )
        if out.returncode == 0:
            name = out.stdout.strip()
            if name and name != "root":
                return name
    except Exception:
        pass

    # 4. Derive user from controlling TTY owner
    try:
        if sys.stdin.isatty():
            tty_path = os.ttyname(sys.stdin.fileno())
            pw = pwd.getpwuid(os.stat(tty_path).st_uid)
            if pw.pw_name and pw.pw_name != "root":
                return pw.pw_name
    except Exception:
        pass

    # 5. Last-resort: getpass
    name = getpass.getuser()
    if name and name != "root":
        return name

    return "nobody"


def real_user() -> str:
    """Return the real (unprivileged) user, detecting lazily on first call."""
    global _real_user
    if _real_user is None:
        _real_user = detect_real_user()
    return _real_user


def set_real_user(user: str) -> None:
    """Override the detected real user (e.g. via --user flag)."""
    global _real_user
    _real_user = user


def user_home(user: str | None = None) -> pathlib.Path:
    """Return the home directory for *user* (defaults to real_user())."""
    u = user or real_user()
    try:
        return pathlib.Path(pwd.getpwnam(u).pw_dir)
    except KeyError:
        return pathlib.Path(f"/home/{u}")


def elevate_privileges() -> None:
    """Re-exec the current script as root via sudo, preserving the caller's TTY."""
    if os.geteuid() == 0:
        return

    if not shutil.which("sudo"):
        die("sudo is required but not found. Install: pacman -S sudo")

    log_warn("Root privileges required — re-executing via sudo...")
    script = os.path.abspath(sys.argv[0])

    try:
        cmd = ["sudo", "--", sys.executable, script, *sys.argv[1:]]
        proc = subprocess.run(cmd)
        sys.exit(proc.returncode)
    except Exception as exc:
        die(f"Privilege elevation failed: {exc}")


# ─── Subprocess Helpers ─────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class CmdResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_as_user(
    cmd: list[str],
    *,
    cwd: OptPath = None,
    input_str: str | None = None,
    timeout: float = 60.0,
    capture: bool = True,
) -> CmdResult:
    """Run *cmd* as real_user() via ``sudo -H -u`` (HOME set to user's home)."""
    full = ["sudo", "-H", "-u", real_user(), "--", *cmd]
    _debug(f"run_as_user: {' '.join(cmd)}")
    try:
        p = subprocess.run(
            full,
            cwd=str(cwd) if cwd else None,
            input=input_str,
            text=True,
            capture_output=capture,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Command timed out after {timeout}s: {' '.join(cmd)}"
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError(f"Binary not found: {cmd[0]}") from e
    return CmdResult(p.returncode, p.stdout or "", p.stderr or "")


def run_root(
    cmd: list[str],
    *,
    cwd: OptPath = None,
    input_str: str | None = None,
    timeout: float = 120.0,
    capture: bool = False,
    check: bool = False,
) -> CmdResult:
    """Run *cmd* as root (we are already root after elevation)."""
    _debug(f"run_root: {' '.join(cmd)}")
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            input=input_str,
            text=True,
            capture_output=capture,
            timeout=timeout,
            check=check,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Command timed out after {timeout}s: {' '.join(cmd)}"
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError(f"Binary not found: {cmd[0]}") from e
    return CmdResult(p.returncode, p.stdout or "", p.stderr or "")


@contextmanager
def spinner(msg: str) -> Iterator[None]:
    """Rich status spinner context manager."""
    with console.status(f"[bold cyan]{msg}[/bold cyan]", spinner="dots"):
        yield


@contextmanager
def acquire_lock() -> Iterator[None]:
    """Prevent concurrent execution via flock on /run/warp-manager.lock."""
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        die(f"Cannot create lock file {LOCK_PATH}: {exc}")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die("Another warp-manager instance is already running.")
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


# ─── Backup Paths ───────────────────────────────────────────────────────


def backup_paths() -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Return (backup_dir, reg_backup, conf_backup) for real_user()."""
    d = user_home() / ".config" / "cloudflare-warp" / "registration_backup"
    return d, d / "reg.json", d / "conf.json"


# ─── Package Installation ───────────────────────────────────────────────


def pkg_installed(name: str = PKG_NAME) -> bool:
    return run_root(["pacman", "-Qi", name], capture=True).ok


def install_package(auto: bool) -> None:
    log_step("Package Installation")

    if pkg_installed():
        log_success(f"'{PKG_NAME}' is already installed.")
        return

    if not auto and sys.stdin.isatty():
        if not Confirm.ask(
            f"'{PKG_NAME}' is not installed. Build & install from AUR now?",
            default=True,
        ):
            log_info("Installation declined.")
            sys.exit(0)

    # Install build dependencies as root so makepkg never needs inner sudo
    with spinner("Installing build dependencies (base-devel, git)..."):
        r = run_root(
            ["pacman", "-S", "--noconfirm", "--needed", *BUILD_DEPS],
            capture=True,
            timeout=180,
        )
        if not r.ok:
            die(f"Failed to install build deps: {r.stderr.strip()}")

    build_dir = pathlib.Path(f"/tmp/warp_build_{os.getpid()}")
    try:
        build_dir.mkdir(parents=True, exist_ok=True)
        # Hand the directory to REAL_USER before any work in it
        pw = pwd.getpwnam(real_user())
        os.chown(build_dir, pw.pw_uid, pw.pw_gid)

        with spinner(f"Cloning AUR repo as {real_user()}..."):
            r = run_as_user(
                ["git", "clone", "--quiet", "--depth=1", AUR_REPO, str(build_dir)],
                capture=True,
                timeout=120,
            )
            if not r.ok:
                die(f"git clone failed: {r.stderr.strip()}")

        # No -s flag: we pre-installed build deps; pacman -U resolves runtime deps
        with spinner("Building package with makepkg..."):
            r = run_as_user(
                ["makepkg", "-f", "--noconfirm"],
                cwd=build_dir,
                capture=True,
                timeout=600,
            )
            if not r.ok:
                tail = (
                    "\n".join(r.stderr.strip().splitlines()[-5:])
                    if r.stderr
                    else "(no stderr)"
                )
                die(f"makepkg failed:\n{tail}")

        pkgs = sorted(build_dir.glob("*.pkg.tar.*"))
        if not pkgs:
            die("No .pkg.tar.* artifact produced by makepkg.")
        pkg_file = pkgs[0]

        with spinner(f"Installing {pkg_file.name} via pacman..."):
            r = run_root(
                ["pacman", "-U", "--noconfirm", str(pkg_file)],
                capture=True,
                timeout=120,
            )
            if not r.ok:
                die(f"pacman -U failed: {r.stderr.strip()}")

        log_success(f"Installed '{pkg_file.name}'.")
    finally:
        if build_dir.exists():
            log_info(f"Cleaning {build_dir}")
            shutil.rmtree(build_dir, ignore_errors=True)


# ─── Service Management ─────────────────────────────────────────────────


def _wait_for_socket(timeout: int = 15) -> bool:
    """Poll warp-cli status until the daemon socket responds."""
    for _ in range(timeout):
        r = run_as_user(
            ["warp-cli", "--accept-tos", "status"],
            capture=True,
            timeout=5,
        )
        if r.ok:
            return True
        time.sleep(1)
    return False


def configure_service() -> None:
    log_step("Service Initialisation")

    with spinner(f"Enabling & starting {SERVICE_NAME}..."):
        r = run_root(
            ["systemctl", "enable", "--now", SERVICE_NAME],
            capture=True,
        )
        if not r.ok:
            die(f"Failed to enable {SERVICE_NAME}: {r.stderr.strip()}")

    with spinner("Waiting for service activation..."):
        active = False
        for _ in range(30):
            if run_root(
                ["systemctl", "is-active", "--quiet", SERVICE_NAME]
            ).ok:
                active = True
                break
            time.sleep(1)
    if not active:
        die(f"'{SERVICE_NAME}' did not become active within 30 s.")

    with spinner("Waiting for daemon socket..."):
        if _wait_for_socket(15):
            log_success("Service active and daemon socket ready.")
        else:
            log_warn("Daemon socket check timed out — proceeding anyway.")


# ─── Backup & Restore ───────────────────────────────────────────────────


def _atomic_copy(
    src: pathlib.Path,
    dst: pathlib.Path,
    mode: int,
    uid: int,
    gid: int,
) -> None:
    """Copy *src* → *dst* atomically (temp + os.replace), then chmod/chown."""
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copy2(src, tmp)
    os.chmod(tmp, mode)
    os.chown(tmp, uid, gid)
    os.replace(tmp, dst)  # atomic on same filesystem


def _is_valid_reg(path: pathlib.Path) -> bool:
    """Check that *path* exists and contains a JSON object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(data, dict)
    except Exception:
        return False


def backup_registration() -> bool:
    """Back up reg.json/conf.json to real_user()'s config dir with 0600 perms."""
    reg = SYSTEM_STATE_DIR / "reg.json"
    conf = SYSTEM_STATE_DIR / "conf.json"

    if not reg.exists():
        log_warn("No reg.json in system state dir — nothing to back up.")
        return False

    bdir, breg, bconf = backup_paths()
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        pw = pwd.getpwnam(real_user())
        os.chown(bdir, pw.pw_uid, pw.pw_gid)
        os.chmod(bdir, 0o700)

        _atomic_copy(reg, breg, 0o600, pw.pw_uid, pw.pw_gid)
        if conf.exists():
            _atomic_copy(conf, bconf, 0o600, pw.pw_uid, pw.pw_gid)

        log_success(f"Registration backed up → {bdir}")
        return True
    except Exception as exc:
        log_error(f"Backup failed: {exc}")
        return False


def restore_registration(manage_service: bool = True) -> bool:
    """
    Restore reg.json/conf.json from backup.

    When *manage_service* is True (standalone command), stop the service
    before copying and restart afterwards. When False (pre-start during
    setup), just copy files — the service hasn't started yet.
    """
    _, breg, bconf = backup_paths()
    if not breg.exists():
        return False

    if not _is_valid_reg(breg):
        log_error("Backup reg.json is corrupt or invalid — aborting restore.")
        return False

    log_info("Registration backup found — restoring autonomously...")

    if manage_service:
        run_root(["systemctl", "stop", SERVICE_NAME], capture=True)

    try:
        SYSTEM_STATE_DIR.mkdir(parents=True, exist_ok=True)
        os.chown(SYSTEM_STATE_DIR, 0, 0)
        os.chmod(SYSTEM_STATE_DIR, 0o755)

        _atomic_copy(breg, SYSTEM_STATE_DIR / "reg.json", 0o600, 0, 0)
        if bconf.exists():
            _atomic_copy(bconf, SYSTEM_STATE_DIR / "conf.json", 0o600, 0, 0)

        log_success("Registration files restored.")
    except Exception as exc:
        log_error(f"Restore failed: {exc}")
        if manage_service:
            run_root(["systemctl", "start", SERVICE_NAME], capture=True)
        return False

    if manage_service:
        with spinner("Restarting service to load restored registration..."):
            run_root(
                ["systemctl", "start", SERVICE_NAME],
                capture=True,
                check=True,
            )
            if not _wait_for_socket(15):
                log_warn("Daemon socket not ready after restore — proceeding.")
            else:
                time.sleep(1)

    return True


# ─── Registration Delineation ───────────────────────────────────────────


def _mask(val: object) -> str:
    """Mask sensitive values for display."""
    if val is None:
        return "[italic dim]—None—[/italic dim]"
    s = str(val)
    if len(s) <= 8:
        return "[dim]" + "•" * len(s) + "[/dim]"
    return f"[dim]{s[:3]}[/dim]{'•' * (len(s) - 6)}[dim]{s[-3:]}[/dim]"


def _classify(key: str, val: object) -> tuple[str, str]:
    """Return (description, display_value) for a registration key/value pair."""
    kl = key.lower()
    if "token" in kl:
        return "API authentication token", _mask(val)
    if "secret" in kl or "priv" in kl:
        return "Private key (WireGuard)", _mask(val)
    if "public" in kl or "pub" in kl:
        return "Public key", _mask(val)
    if "id" in kl:
        return "Unique identifier", _mask(val)
    if "license" in kl:
        return "Licence / account key", _mask(val)
    if "mode" in kl:
        return "Operational mode", str(val)
    if "gateway" in kl:
        return "Teams gateway ID", str(val)
    if isinstance(val, (dict, list)):
        return "Nested configuration", _mask(val)
    if len(str(val)) > 24:
        return "Configuration value", _mask(val)
    return "Configuration value", str(val)


def _build_reg_table(title: str, data: dict, border: str) -> Table | None:
    """Build a Rich table from a registration dict, or None if empty."""
    if not data:
        return None
    table = Table(
        title=title,
        show_header=True,
        header_style="bold magenta",
        border_style=border,
        title_style="bold yellow",
    )
    table.add_column("Property", style="cyan", no_wrap=True)
    table.add_column("Key", style="green", no_wrap=True)
    table.add_column("Description", style="white")
    table.add_column("Value", style="yellow")

    for k, v in data.items():
        desc, display = _classify(k, v)
        table.add_row(k.replace("_", " ").title(), k, desc, display)
    return table


def delineate_registration() -> None:
    """Pretty-print registration details in Rich tables with masked secrets."""
    reg = SYSTEM_STATE_DIR / "reg.json"
    conf = SYSTEM_STATE_DIR / "conf.json"

    if not reg.exists():
        log_warn("No active registration to delineate.")
        return

    try:
        reg_data = json.loads(reg.read_text(encoding="utf-8"))
    except Exception as exc:
        log_error(f"Cannot parse reg.json: {exc}")
        reg_data = {}

    try:
        conf_data = (
            json.loads(conf.read_text(encoding="utf-8"))
            if conf.exists()
            else {}
        )
    except Exception:
        conf_data = {}

    console.print()
    if reg_t := _build_reg_table("Registration (reg.json)", reg_data, "blue"):
        console.print(reg_t)
    if conf_t := _build_reg_table("Configuration (conf.json)", conf_data, "cyan"):
        console.print(conf_t)


# ─── PTY-based TOS Acceptance ───────────────────────────────────────────


def accept_tos_via_pty() -> bool:
    """
    Run ``warp-cli status`` (without --accept-tos) inside a pseudo-terminal
    so that any interactive TOS prompt is satisfied by sending 'y'.

    This persists TOS acceptance for ALL future invocations, including those
    without the --accept-tos flag.

    Returns True if a prompt was detected and answered, False otherwise.
    """
    if not shutil.which("warp-cli"):
        return False

    # NB: intentionally NO --accept-tos here; we want the prompt to appear
    cmd = ["sudo", "-H", "-u", real_user(), "--", "warp-cli", "status"]

    log_info("Persisting TOS acceptance via PTY session...")

    try:
        pid, fd = pty.fork()
    except OSError as exc:
        log_warn(f"pty.fork() failed: {exc}")
        return False

    if pid == 0:
        # ── Child ───────────────────────────────────────────────────
        try:
            os.execvp(cmd[0], cmd)
        except OSError:
            os._exit(127)

    # ── Parent ──────────────────────────────────────────────────────
    prompt_re = re.compile(
        r"accept|terms|\[y/n\]|y/N|Do you|agree", re.IGNORECASE
    )
    buf = b""
    answered = False
    deadline = time.monotonic() + 12.0

    try:
        while time.monotonic() < deadline:
            try:
                r, _, _ = select.select([fd], [], [], 0.5)
            except (OSError, ValueError):
                break

            if r:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break  # EIO — slave closed
                if not chunk:
                    break

                buf += chunk

                # Search accumulated buffer so prompts split across
                # multiple reads are still caught
                if not answered:
                    text = buf.decode(errors="replace")
                    if prompt_re.search(text):
                        try:
                            os.write(fd, b"y\n")
                            answered = True
                        except OSError:
                            pass
            else:
                # No data ready — check whether child exited
                try:
                    wpid, _ = os.waitpid(pid, os.WNOHANG)
                    if wpid != 0:
                        # Drain remaining output
                        try:
                            while True:
                                chunk = os.read(fd, 4096)
                                if not chunk:
                                    break
                                buf += chunk
                        except OSError:
                            pass
                        break
                except ChildProcessError:
                    break
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass

    if answered:
        log_success("TOS acceptance persisted.")
    else:
        log_info("No interactive TOS prompt detected (likely already accepted).")
    return answered


# ─── Connection Management ──────────────────────────────────────────────


def warp_status() -> str | None:
    """Return stdout of `warp-cli --accept-tos status`, or None on failure."""
    r = run_as_user(
        ["warp-cli", "--accept-tos", "status"],
        capture=True,
        timeout=10,
    )
    return r.stdout if r.ok else None


def connect_warp() -> None:
    log_step("Connecting to Cloudflare WARP")

    # Persist TOS before connecting (in case first-run prompt blocks connect)
    accept_tos_via_pty()

    status = warp_status()
    if status and "Connected" in status:
        log_success("WARP is already Connected.")
        return

    r = run_as_user(
        ["warp-cli", "--accept-tos", "connect"],
        capture=True,
        timeout=15,
    )
    if not r.ok:
        log_warn(f"warp-cli connect non-zero exit: {r.stderr.strip()}")

    with spinner("Verifying secure tunnel..."):
        connected = False
        for _ in range(20):
            s = warp_status()
            if s and "Connected" in s:
                connected = True
                break
            time.sleep(1)

    if connected:
        log_success("WARP Connected and Secured.")
    else:
        log_warn("Connection verification timed out — check 'warp-cli status'.")


# ─── Setup Flow ─────────────────────────────────────────────────────────


def _wait_for_reg(reg_path: pathlib.Path, timeout: int = 10) -> bool:
    """Poll until reg_path exists and contains valid JSON."""
    for _ in range(timeout):
        if _is_valid_reg(reg_path):
            return True
        time.sleep(1)
    return False


def run_setup(auto: bool) -> None:
    console.print(Panel(
        f"[bold]User[/bold]    {real_user()}\n"
        f"[bold]Home[/bold]    {user_home()}\n"
        f"[bold]Package[/bold] {PKG_NAME}\n"
        f"[bold]Service[/bold] {SERVICE_NAME}",
        title="[bold cyan]Cloudflare WARP Manager[/bold cyan]",
        border_style="cyan",
        expand=False,
    ))

    log_info(f"Starting WARP setup for user: {real_user()}")

    # 1. Install package
    install_package(auto=auto)

    # 2. Pre-restore registration if system state is empty but backup exists
    reg = SYSTEM_STATE_DIR / "reg.json"
    _, breg, _ = backup_paths()

    if not reg.exists() and breg.exists():
        log_step("Pre-start Registration Restore")
        # Service not started yet — just copy files, no stop/restart needed
        restore_registration(manage_service=False)

    # 3. Start service
    configure_service()

    # 4. Check registration state
    reg = SYSTEM_STATE_DIR / "reg.json"  # re-read in case restore created it
    if not reg.exists():
        # No backup was available — register a new client
        log_step("Registering New Client")
        ok = False
        for attempt in range(1, 4):
            log_info(f"Registration attempt {attempt}/3...")
            r = run_as_user(
                ["warp-cli", "--accept-tos", "registration", "new"],
                capture=True,
                timeout=30,
            )
            if r.ok:
                ok = True
                break
            log_warn(f"Attempt {attempt} failed: {r.stderr.strip()}")
            time.sleep(2)

        if not ok:
            die("Failed to register with Cloudflare after 3 attempts.")

        log_success("Registration successful.")

        # Wait for reg files to be fully written before backing up
        if not _wait_for_reg(reg, timeout=10):
            log_warn("reg.json not fully written — backup may be incomplete.")
        backup_registration()
    else:
        log_success("Client is already registered.")
        # Ensure backup exists for future restores
        _, breg_now, _ = backup_paths()
        if not breg_now.exists():
            backup_registration()

    # 5. Delineate registration
    delineate_registration()

    # 6. Connect
    connect_warp()

    # 7. Summary
    log_step("All Done — Traffic is Secured.")
    console.print(Panel(
        f"[green]✓[/green] Package:  {PKG_NAME}\n"
        f"[green]✓[/green] Service:  {SERVICE_NAME} (active)\n"
        f"[green]✓[/green] User:     {real_user()}\n"
        f"[green]✓[/green] Backup:   {backup_paths()[0]}",
        title="[bold green]Setup Summary[/bold green]",
        border_style="green",
        expand=False,
    ))


# ─── Status Command ─────────────────────────────────────────────────────


def show_status() -> None:
    log_step("Cloudflare WARP Status")

    # Gather all state up front so each command runs at most once
    pkg_ok = pkg_installed()
    svc_ok = run_root(
        ["systemctl", "is-active", "--quiet", SERVICE_NAME]
    ).ok

    client_result: CmdResult | None = None
    settings_result: CmdResult | None = None
    if shutil.which("warp-cli"):
        client_result = run_as_user(
            ["warp-cli", "--accept-tos", "status"],
            capture=True,
            timeout=10,
        )
        settings_result = run_as_user(
            ["warp-cli", "--accept-tos", "settings"],
            capture=True,
            timeout=10,
        )

    bdir, breg, _ = backup_paths()

    # ── Overview Table ────────────────────────────────────────────
    table = Table(
        show_header=True,
        header_style="bold magenta",
        border_style="blue",
        title_style="bold yellow",
    )
    table.add_column("Component", style="cyan", no_wrap=True)
    table.add_column("State")
    table.add_column("Detail", style="dim")

    if pkg_ok:
        table.add_row("Package", "[bold green]installed[/bold green]", PKG_NAME)
    else:
        table.add_row("Package", "[bold red]not installed[/bold red]", "")

    if svc_ok:
        table.add_row(
            "Service", "[bold green]active (running)[/bold green]", SERVICE_NAME
        )
    else:
        table.add_row("Service", "[bold red]inactive[/bold red]", SERVICE_NAME)

    if client_result and client_result.ok:
        first = (
            client_result.stdout.splitlines()[0].strip()
            if client_result.stdout.strip()
            else "OK"
        )
        table.add_row("Client", "[bold green]responsive[/bold green]", first)
    elif client_result:
        table.add_row(
            "Client", "[bold red]error[/bold red]", client_result.stderr.strip()
        )
    else:
        table.add_row("Client", "[bold yellow]not installed[/bold yellow]", "")

    if breg.exists():
        table.add_row("Backup", "[bold green]available[/bold green]", str(bdir))
    else:
        table.add_row("Backup", "[bold yellow]none[/bold yellow]", "")

    console.print()
    console.print(table)

    # ── Full Client Status ────────────────────────────────────────
    if client_result and client_result.ok and client_result.stdout.strip():
        console.print()
        console.print("[bold cyan]Client Status:[/bold cyan]")
        for line in client_result.stdout.splitlines():
            if line.strip():
                console.print(f"  {line.strip()}")

    # ── Settings ──────────────────────────────────────────────────
    if settings_result and settings_result.ok and settings_result.stdout.strip():
        console.print()
        console.print("[bold cyan]Settings:[/bold cyan]")
        for line in settings_result.stdout.splitlines():
            if line.strip():
                console.print(f"  {line.strip()}")

    # ── Registration Details ──────────────────────────────────────
    delineate_registration()


# ─── Disconnect Command ─────────────────────────────────────────────────


def disconnect_warp() -> None:
    if not shutil.which("warp-cli"):
        die("warp-cli is not installed. Run setup first.")
    log_info("Disconnecting from WARP...")
    r = run_as_user(
        ["warp-cli", "--accept-tos", "disconnect"],
        capture=True,
        timeout=15,
    )
    if r.ok:
        log_success("Disconnected.")
    else:
        die(f"Failed to disconnect: {r.stderr.strip()}")


# ─── Logs Command ───────────────────────────────────────────────────────


def show_logs() -> None:
    if not pkg_installed():
        die("Package not installed — nothing to show.")
    log_step("Recent warp-svc.service Logs")
    r = run_root(
        ["journalctl", "-u", SERVICE_NAME, "-n", "50", "--no-pager"],
        capture=True,
        timeout=10,
    )
    if r.ok:
        console.print(r.stdout)
    else:
        die(f"Failed to read logs: {r.stderr.strip()}")


# ─── Restart Command ────────────────────────────────────────────────────


def restart_service() -> None:
    if not pkg_installed():
        die("Package not installed — run setup first.")
    log_step("Restarting WARP Service")
    with spinner(f"Restarting {SERVICE_NAME}..."):
        r = run_root(["systemctl", "restart", SERVICE_NAME], capture=True)
        if not r.ok:
            die(f"Failed to restart {SERVICE_NAME}: {r.stderr.strip()}")
    with spinner("Waiting for daemon socket..."):
        if _wait_for_socket(15):
            log_success("Service restarted and daemon socket ready.")
        else:
            log_warn(
                "Daemon socket check timed out — check 'warp-manager status'."
            )


# ─── Mode Command ───────────────────────────────────────────────────────


def set_warp_mode(mode: str) -> None:
    if not shutil.which("warp-cli"):
        die("warp-cli is not installed. Run setup first.")
    log_step(f"Setting WARP mode to '{mode}'")
    r = run_as_user(
        ["warp-cli", "--accept-tos", "mode", mode],
        capture=True,
        timeout=10,
    )
    if r.ok:
        log_success(f"Mode set to '{mode}'.")
    else:
        die(f"Failed to set mode: {r.stderr.strip()}")


# ─── Uninstall Command ──────────────────────────────────────────────────


def run_uninstall() -> None:
    log_step("Uninstalling Cloudflare WARP")

    with spinner("Stopping & disabling service..."):
        run_root(["systemctl", "stop", SERVICE_NAME], capture=True)
        run_root(["systemctl", "disable", SERVICE_NAME], capture=True)

    if shutil.which("warp-cli"):
        with spinner("Deleting registration..."):
            run_as_user(
                ["warp-cli", "--accept-tos", "registration", "delete"],
                capture=True,
                timeout=15,
            )

    if pkg_installed():
        with spinner(f"Removing {PKG_NAME} via pacman..."):
            r = run_root(
                ["pacman", "-Rns", "--noconfirm", PKG_NAME],
                capture=True,
                timeout=120,
            )
            if r.ok:
                log_success(f"Removed '{PKG_NAME}'.")
            else:
                log_warn(f"pacman removal failed: {r.stderr.strip()}")
    else:
        log_info("Package not installed.")

    with spinner("Cleaning system state directory..."):
        shutil.rmtree(SYSTEM_STATE_DIR, ignore_errors=True)

    bdir, _, _ = backup_paths()
    if bdir.exists():
        with spinner(f"Removing backup at {bdir}..."):
            shutil.rmtree(bdir, ignore_errors=True)

    # Also remove user config dir if empty
    user_cfg = user_home() / ".config" / "cloudflare-warp"
    if user_cfg.exists():
        try:
            user_cfg.rmdir()  # only succeeds if empty
        except OSError:
            pass  # not empty — leave it

    log_success("Cloudflare WARP fully removed.")


# ─── CLI ────────────────────────────────────────────────────────────────


class Command(StrEnum):
    STATUS = "status"
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    BACKUP = "backup"
    RESTORE = "restore"
    UNINSTALL = "uninstall"
    LOGS = "logs"
    RESTART = "restart"
    MODE = "mode"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="warp-manager",
        description="Autonomous Cloudflare WARP manager for Arch Linux.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "With no subcommand, runs the full setup flow (install, configure,\n"
            "register, connect). Use -y for non-interactive setup.\n\n"
            "Examples:\n"
            "  warp-manager                # full interactive setup\n"
            "  warp-manager -y             # full non-interactive setup\n"
            "  warp-manager status         # show status\n"
            "  warp-manager mode warp      # set WARP mode\n"
            "  warp-manager logs           # show recent service logs\n"
            "  warp-manager -u alice setup # manage WARP for user 'alice'"
        ),
    )
    p.add_argument(
        "-y",
        "--auto",
        action="store_true",
        help="Run setup non-interactively",
    )
    p.add_argument(
        "-u",
        "--user",
        metavar="USER",
        help="Override detected real user",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug command logging to stderr",
    )
    sub = p.add_subparsers(dest="command")

    sub.add_parser(Command.STATUS.value, help="Show service, client & backup status")
    sub.add_parser(Command.CONNECT.value, help="Connect to WARP")
    sub.add_parser(Command.DISCONNECT.value, help="Disconnect from WARP")
    sub.add_parser(Command.BACKUP.value, help="Backup current registration credentials")
    sub.add_parser(Command.RESTORE.value, help="Restore registration from backup")
    sub.add_parser(
        Command.UNINSTALL.value,
        help="Completely uninstall WARP, configs, and backups",
    )
    sub.add_parser(Command.LOGS.value, help="Show recent warp-svc.service logs")
    sub.add_parser(Command.RESTART.value, help="Restart the WARP service")

    mode_p = sub.add_parser(Command.MODE.value, help="Set WARP mode")
    mode_p.add_argument(
        "mode",
        choices=["warp", "proxy", "doh"],
        help="WARP mode: warp (tunnel), proxy (SOCKS5), doh (DNS-over-HTTPS)",
    )

    return p


def main() -> None:
    elevate_privileges()

    args = build_parser().parse_args()

    if args.debug:
        _set_debug(True)

    if args.user:
        set_real_user(args.user)

    # Arch Linux guard
    if not pathlib.Path("/etc/arch-release").exists():
        die("This script is designed for Arch Linux only.")

    with acquire_lock():
        match args.command:
            case Command.STATUS:
                show_status()
            case Command.CONNECT:
                if not shutil.which("warp-cli"):
                    die("warp-cli not installed — run setup first.")
                connect_warp()
            case Command.DISCONNECT:
                disconnect_warp()
            case Command.BACKUP:
                backup_registration()
            case Command.RESTORE:
                if not restore_registration(manage_service=True):
                    log_warn("No backup available to restore.")
            case Command.UNINSTALL:
                run_uninstall()
            case Command.LOGS:
                show_logs()
            case Command.RESTART:
                restart_service()
            case Command.MODE:
                set_warp_mode(args.mode)
            case None:
                run_setup(auto=args.auto)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]✖ Interrupted by user.[/bold red]")
        sys.exit(130)
    except RuntimeError as exc:
        die(str(exc))

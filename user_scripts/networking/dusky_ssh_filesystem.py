#!/usr/bin/env -S python3 -I
"""Dusky SSH File System Mounter for Arch Linux with multi-mount support, Python Rich UI, and robust error recovery."""

import os
import sys

# This program should run as the normal user.
# It only needs escalation for pacman, not for mounting.
if os.geteuid() == 0 and any(
    var in os.environ
    for var in ("SUDO_USER", "SUDO_UID", "PKEXEC_UID", "DOAS_USER", "RUN0_UID")
):
    print(
        "[-] Run this script as your normal user, not via sudo/doas/run0/pkexec.\n"
        "    It will ask for administrative rights only when installing packages.",
        file=sys.stderr,
    )
    sys.exit(1)

import contextlib
import errno
import json
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Final, NamedTuple, NoReturn

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console()
error_console = Console(stderr=True)

try:
    HOME: Final[Path] = Path.home()
except (RuntimeError, KeyError) as exc:
    error_console.print(f"[bold red][-] Cannot determine home directory: {exc}[/bold red]")
    sys.exit(1)

# --- Fixed paths ---
STATE_FILE: Final[Path] = HOME / ".config/dusky/settings/sshfiles/sshfs"
BASE_MOUNT_DIR: Final[Path] = HOME / "Documents/sshfs"

MAX_HISTORY: Final[int] = 10

# sshfs/FUSE/SSH options for a stable interactive mount.
SSHFS_OPTIONS: Final[tuple[str, ...]] = (
    "reconnect",
    "ServerAliveInterval=15",
    "ServerAliveCountMax=3",
    "ConnectTimeout=10",
    "ConnectionAttempts=5",
    "StrictHostKeyChecking=accept-new",
)


def fail(message: str, code: int = 1) -> NoReturn:
    error_console.print(f"[bold red][-] {message}[/bold red]")
    sys.exit(code)


def root_hint(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    if st.st_uid == 0 and os.geteuid() != 0:
        quoted = shlex.quote(str(path))
        return f" If owned by root, fix with: sudo chown -R {os.getuid()}:{os.getgid()} {quoted}"
    return ""


def find_executable(name: str) -> str | None:
    path = shutil.which(name)
    if path:
        return path

    for directory in ("/usr/local/bin", "/usr/bin", "/bin"):
        candidate = Path(directory, name)
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


class ParsedTarget(NamedTuple):
    user: str | None
    host: str
    port: int
    remote_path: str
    raw_input: str

    @property
    def sshfs_target_spec(self) -> str:
        prefix = f"{self.user}@" if self.user else ""
        path = self.remote_path if self.remote_path else "/"
        return f"{prefix}{self.host}:{path}"

    @property
    def canonical_string(self) -> str:
        prefix = f"{self.user}@" if self.user else ""
        path_part = self.remote_path if self.remote_path else "/"
        if self.port != 22:
            if path_part.startswith("/"):
                return f"ssh://{prefix}{self.host}:{self.port}{path_part}"
            else:
                return f"{prefix}{self.host}:{self.port}:{path_part}"
        else:
            return f"{prefix}{self.host}:{path_part}"


class ActiveMount(NamedTuple):
    source: str
    mount_point: Path
    fstype: str


def render_banner() -> None:
    """Render main application header fitted strictly to content width."""
    grid = Table.grid(expand=False)
    grid.add_column(justify="center")
    grid.add_row(Text("󰒍 Dusky SSH File System Mounter", style="bold cyan"))
    grid.add_row(Text("Arch Linux Multi-Mount Utility", style="dim white"))
    console.print(Panel.fit(grid, border_style="cyan", padding=(0, 3)))


def render_active_mounts(mounts: list[ActiveMount]) -> None:
    """Render active mounts table formatted to content width."""
    if not mounts:
        console.print(
            Panel.fit(
                f"[dim italic]No active SSHFS mounts in {BASE_MOUNT_DIR}[/dim italic]",
                title="[bold yellow]Active Mounts[/bold yellow]",
                border_style="dim",
                padding=(0, 2),
            )
        )
        return

    table = Table(
        title=f"[bold green]Active SSHFS Mounts ({len(mounts)})[/bold green]",
        border_style="green",
        expand=False,
    )
    table.add_column("#", style="bold cyan", width=4, justify="center")
    table.add_column("Remote Target", style="bold white")
    table.add_column("Local Mount Point", style="bold yellow")
    table.add_column("Filesystem", style="dim cyan")
    table.add_column("Status", justify="center")

    for idx, m in enumerate(mounts, 1):
        is_healthy = False
        with contextlib.suppress(OSError):
            is_healthy = m.mount_point.is_dir() and os.access(m.mount_point, os.R_OK)

        status_badge = "[bold green]󰄬 Active[/bold green]" if is_healthy else "[bold red]󰅙 Broken[/bold red]"
        table.add_row(str(idx), m.source, str(m.mount_point), m.fstype, status_badge)

    console.print(table)


def render_history(history: list[str]) -> None:
    """Render recent target connections fitted to content width."""
    if not history:
        return

    table = Table(
        title="[bold blue]Recent Connections[/bold blue]",
        border_style="blue",
        expand=False,
    )
    table.add_column("#", style="bold cyan", width=4, justify="center")
    table.add_column("Target Spec", style="bold white")

    for idx, entry in enumerate(history, 1):
        default_tag = " [dim cyan](latest)[/dim cyan]" if idx == 1 else ""
        table.add_row(str(idx), f"{entry}{default_tag}")

    console.print(table)


def parse_target(raw: str) -> ParsedTarget | None:
    """
    Validate and parse an SSH target string into a structured ParsedTarget.

    Robustly handles:
      - Stripping leading `ssh ` command (e.g., `ssh user@host`)
      - Parsing `-p <port>` or `-p<port>` SSH flags
      - Standard `user@host` / `user@host:/path` / `user@host:port`
      - `ssh://[user@]host[:port][/path]` URI format
    """
    target = raw.strip()
    if not target or not target.isprintable():
        return None
    if any(ch in target for ch in "\r\n\t\v\f"):
        return None

    # Automatically clean up leading 'ssh' command token if entered
    if target == "ssh":
        return None
    if target.startswith("ssh ") or target.startswith("ssh\t"):
        target = target[3:].strip()

    if not target:
        return None

    extracted_port: int | None = None

    # Handle leading flags like -p 2222 or -p2222
    while target.startswith("-"):
        tokens = target.split(maxsplit=2)
        if not tokens:
            return None
        flag = tokens[0]
        if flag in ("-p", "-P") and len(tokens) >= 2:
            try:
                extracted_port = int(tokens[1])
                target = tokens[2] if len(tokens) > 2 else ""
            except ValueError:
                return None
        elif (flag.startswith("-p") or flag.startswith("-P")) and flag[2:].isdigit():
            extracted_port = int(flag[2:])
            target = tokens[1] if len(tokens) > 1 else ""
        else:
            # Skip unrecognized options/flags if user passed e.g. -i keyfile target
            if len(tokens) >= 2 and not tokens[1].startswith("-") and "@" not in tokens[0] and "." not in tokens[0]:
                target = tokens[2] if len(tokens) > 2 else ""
            else:
                target = tokens[1] if len(tokens) > 1 else ""

    if not target:
        return None

    # Handle ssh:// style target
    if target.lower().startswith("ssh://"):
        rest = target[6:]
        if not rest:
            return None
        parts = rest.split("/", 1)
        authority = parts[0]
        remote_path = "/" + parts[1] if len(parts) > 1 else "/"
        if not authority:
            return None

        if "@" in authority:
            user, hostport = authority.rsplit("@", 1)
            if not user or ":" in user or "@" in user or "/" in user:
                return None
        else:
            user, hostport = None, authority

        if hostport.startswith("["):
            bracket_end = hostport.find("]")
            if bracket_end == -1:
                return None
            host = hostport[: bracket_end + 1]
            port_part = hostport[bracket_end + 1 :]
            port_str = port_part[1:] if port_part.startswith(":") else "22"
        elif ":" in hostport:
            host, port_str = hostport.rsplit(":", 1)
        else:
            host, port_str = hostport, "22"

        if not host or host.startswith("-"):
            return None

        try:
            port = extracted_port or int(port_str or 22)
            if not (1 <= port <= 65535):
                return None
        except ValueError:
            return None

        return ParsedTarget(
            user=user,
            host=host,
            port=port,
            remote_path=remote_path,
            raw_input=raw,
        )

    # Standard / scp / user-friendly syntax
    user = None
    if "@" in target:
        user, rest = target.rsplit("@", 1)
        if not user or user.startswith("-") or ":" in user or "@" in user or "/" in user:
            return None
    else:
        rest = target

    if rest.startswith("["):
        bracket_end = rest.find("]")
        if bracket_end == -1:
            return None
        host = rest[: bracket_end + 1]
        after_host = rest[bracket_end + 1 :]
        if after_host.startswith(":"):
            after_host = after_host[1:]
    elif ":" in rest:
        host, after_host = rest.split(":", 1)
    else:
        host = rest
        after_host = ""

    if not host or host.startswith("-") or "/" in host or "@" in host:
        return None

    if not after_host:
        port = extracted_port or 22
        remote_path = "/"
    elif after_host.isdigit():
        port = extracted_port or int(after_host)
        if not (1 <= port <= 65535):
            return None
        remote_path = "/"
    elif ":" in after_host:
        parts = after_host.split(":", 1)
        if not parts[0].isdigit():
            return None
        port = extracted_port or int(parts[0])
        if not (1 <= port <= 65535):
            return None
        remote_path = parts[1] or "/"
    else:
        m = re.match(r"^(\d+)/(.*)$", after_host)
        if m and (1 <= int(m.group(1)) <= 65535):
            port = extracted_port or int(m.group(1))
            remote_path = "/" + m.group(2)
        else:
            port = extracted_port or 22
            remote_path = after_host or "/"

    return ParsedTarget(
        user=user,
        host=host,
        port=port,
        remote_path=remote_path,
        raw_input=raw,
    )


def derive_mount_point(target: ParsedTarget, custom_path: str | None = None) -> Path:
    """Derive local mount path for a target."""
    if custom_path and custom_path.strip():
        c = custom_path.strip()
        p = Path(c).expanduser()
        if p.is_absolute():
            return p.resolve()

        # Explicit relative path starting with . or .. or containing path separators
        if c.startswith("./") or c.startswith("../") or "/" in c or c in (".", ".."):
            return (Path.cwd() / p).resolve()

        # Simple folder name -> resolve inside BASE_MOUNT_DIR
        return (BASE_MOUNT_DIR / c).resolve()

    host_clean = re.sub(r"[^\w.-]", "_", target.host)
    user_prefix = f"{target.user}_" if target.user and target.user != os.environ.get("USER") else ""

    path_clean = ""
    if target.remote_path and target.remote_path != "/":
        path_clean = "_" + re.sub(r"[^\w.-]", "_", target.remote_path.strip("/"))

    dir_name = f"{user_prefix}{host_clean}{path_clean}".strip("_")
    if not dir_name:
        dir_name = "default"

    return BASE_MOUNT_DIR / dir_name


def normalize_target(raw: str) -> str | None:
    parsed = parse_target(raw)
    return parsed.canonical_string if parsed else None


def _unescape_proc_field(field: bytes) -> bytes:
    """Unescape octal sequences from /proc/mounts fields."""
    out = bytearray()
    i = 0
    while i < len(field):
        if field[i] == 0x5C and i + 4 <= len(field):
            digits = field[i + 1 : i + 4]
            if all(0x30 <= b <= 0x37 for b in digits):
                value = int(digits.decode("ascii"), 8)
                if value <= 0xFF:
                    out.append(value)
                    i += 4
                    continue
        out.append(field[i])
        i += 1
    return bytes(out)


def _mount_entries() -> Iterator[tuple[str, str, str]]:
    """Yield (source, target, fstype) from /proc/mounts."""
    try:
        with open("/proc/mounts", "rb") as handle:
            for line in handle:
                fields = line.rstrip(b"\n").split(b" ")
                if len(fields) < 3:
                    continue

                source = os.fsdecode(_unescape_proc_field(fields[0]))
                target = os.fsdecode(_unescape_proc_field(fields[1]))
                fstype = os.fsdecode(_unescape_proc_field(fields[2]))
                yield source, target, fstype
    except OSError:
        return


def get_active_mounts() -> list[ActiveMount]:
    """Return all active SSHFS mounts under BASE_MOUNT_DIR or mounted by sshfs."""
    active: list[ActiveMount] = []
    base_str = os.path.normpath(str(BASE_MOUNT_DIR))
    for source, target, fstype in _mount_entries():
        if fstype == "fuse.sshfs" or fstype.startswith("fuse."):
            norm_target = os.path.normpath(target)
            if norm_target == base_str or norm_target.startswith(base_str + os.sep):
                active.append(ActiveMount(source, Path(target), fstype))
    return active


def get_mount_info_for(mount_point: Path) -> tuple[str, str] | None:
    """Return (source, fstype) if mount_point is currently mounted."""
    wanted = os.path.normpath(str(mount_point))
    for source, target, fstype in _mount_entries():
        if os.path.normpath(target) == wanted:
            return source, fstype
    return None


def cleanup_stale_mount(mount_point: Path) -> bool:
    """Attempt a lazy unmount and kill orphaned background sshfs daemons for stale mounts."""
    target_str = str(mount_point)

    # 1. Terminate any orphaned background sshfs daemon for this mount point
    pkill = find_executable("pkill")
    if pkill:
        with contextlib.suppress(OSError):
            subprocess.run([pkill, "-9", "-f", f"sshfs.*{target_str}"], check=False, capture_output=True)

    # 2. Unmount FUSE mount point
    fusermount = find_executable("fusermount3")
    if fusermount:
        with contextlib.suppress(OSError):
            subprocess.run(
                [fusermount, "-u", "-z", target_str],
                check=False,
                text=True,
                capture_output=True,
            )

    umount_bin = find_executable("umount")
    if umount_bin:
        with contextlib.suppress(OSError):
            subprocess.run(
                [umount_bin, "-l", target_str],
                check=False,
                text=True,
                capture_output=True,
            )

    return wait_until_unmounted(mount_point, timeout=2.0)


def state_destination() -> Path:
    """Preserve symlinks on STATE_FILE."""
    if STATE_FILE.is_symlink():
        try:
            return STATE_FILE.resolve(strict=False)
        except OSError:
            return STATE_FILE
    return STATE_FILE


def write_state(history: list[str]) -> None:
    """Atomically write state JSON."""
    destination = state_destination()
    text = json.dumps({"history": history}, indent=4, ensure_ascii=False) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        prefix=destination.name + ".",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, destination)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise

    with contextlib.suppress(OSError):
        dir_fd = os.open(destination.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def load_state() -> list[str]:
    """Load history from STATE_FILE."""
    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        error_console.print(f"[bold yellow][!] Cannot read state file: {exc}[/bold yellow]")
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        error_console.print(f"[bold yellow][!] State file corrupt; starting clean ({exc})[/bold yellow]")
        return []

    if isinstance(data, dict):
        history = data.get("history", [])
    elif isinstance(data, list):
        history = data
    else:
        history = []

    if not isinstance(history, list):
        return []

    cleaned: list[str] = []
    for item in history:
        if not isinstance(item, str):
            continue
        normalized = normalize_target(item)
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)

    return cleaned[:MAX_HISTORY]


def save_state(history: list[str], new_entry: str | None = None) -> list[str]:
    """Deduplicate, trim to MAX_HISTORY, and atomically save."""
    cleaned: list[str] = []
    candidates: list[str] = []

    if new_entry is not None:
        candidates.append(new_entry)
    candidates.extend(history)

    for item in candidates:
        normalized = normalize_target(item) if isinstance(item, str) else None
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)

    cleaned = cleaned[:MAX_HISTORY]
    write_state(cleaned)
    return cleaned


def update_history(history: list[str], target: str) -> list[str]:
    try:
        return save_state(history, target)
    except OSError as exc:
        error_console.print(f"[bold yellow][!] Could not save history: {exc}[/bold yellow]")
        hint = root_hint(state_destination()) or root_hint(state_destination().parent)
        if hint:
            error_console.print(f"[dim]{hint}[/dim]")
        return history


def ensure_state_file() -> None:
    if not state_destination().exists():
        try:
            write_state([])
        except OSError as exc:
            fail(f"Cannot create state file {STATE_FILE}: {exc}{root_hint(STATE_FILE.parent)}")


def ensure_directories() -> None:
    if not STATE_FILE.is_absolute() or not BASE_MOUNT_DIR.is_absolute():
        fail("State and mount paths must be absolute. Check HOME.")

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        fail(f"Cannot create state directory {STATE_FILE.parent}: {exc}{root_hint(STATE_FILE.parent)}")

    destination = state_destination()

    if destination.is_dir():
        fail(f"{destination} is a directory; expected a state file.")

    if not os.access(destination.parent, os.W_OK | os.X_OK):
        fail(f"State directory {destination.parent} is not writable.{root_hint(destination.parent)}")

    if destination.exists() and not os.access(destination, os.R_OK | os.W_OK):
        fail(f"State file {destination} is not readable/writable.{root_hint(destination)}")

    try:
        BASE_MOUNT_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        if exc.errno in (errno.ENOTCONN, errno.EBUSY):
            cleanup_stale_mount(BASE_MOUNT_DIR)
        else:
            fail(f"Cannot create base mount directory {BASE_MOUNT_DIR}: {exc}{root_hint(BASE_MOUNT_DIR.parent)}")


def install_package(package: str) -> bool:
    pacman = find_executable("pacman")
    if not pacman:
        error_console.print("[bold red][-] pacman package manager not found.[/bold red]")
        return False

    base: list[str] | None = None

    if os.geteuid() == 0:
        base = []
    else:
        for escalator in ("sudo", "doas", "run0"):
            path = find_executable(escalator)
            if path:
                base = [path]
                break

    if base is None:
        error_console.print(
            f"[bold red][-] No privilege escalator (sudo/doas/run0) found. Install '{package}' manually.[/bold red]"
        )
        return False

    cmd = base + [pacman, "-S", "--noconfirm", "--noprogressbar", package]

    console.print(f"[bold cyan][*] Installing '{package}' via pacman...[/bold cyan]")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        error_console.print(f"[bold red][-] pacman failed with exit code {exc.returncode}.[/bold red]")
        if Path("/var/lib/pacman/db.lck").exists():
            error_console.print("    [dim]Hint: Pacman is locked by another process.[/dim]")
        return False
    except OSError as exc:
        error_console.print(f"[bold red][-] Failed to run pacman: {exc}[/bold red]")
        return False


def ensure_unmount_dependencies() -> bool:
    if find_executable("fusermount3"):
        return True

    console.print("[bold yellow][!] fusermount3 missing. Installing 'fuse3'...[/bold yellow]")
    if not install_package("fuse3"):
        return False

    return bool(find_executable("fusermount3"))


def ensure_mount_dependencies() -> bool:
    requirements = (
        ("sshfs", "sshfs"),
        ("fusermount3", "fuse3"),
        ("ssh", "openssh"),
    )

    for binary, package in requirements:
        if find_executable(binary):
            continue

        console.print(f"[bold yellow][!] '{binary}' missing. Installing package '{package}'...[/bold yellow]")
        if not install_package(package):
            return False

        if not find_executable(binary):
            error_console.print(
                f"[bold red][-] '{binary}' is still missing after installing '{package}'.[/bold red]"
            )
            return False

    return True


def check_fuse_device() -> bool:
    fuse = Path("/dev/fuse")

    if not fuse.exists():
        error_console.print(
            "[bold red][-] /dev/fuse is missing. Ensure fuse3 is installed and fuse kernel module is loaded.[/bold red]"
        )
        return False

    if os.geteuid() != 0 and not os.access(fuse, os.R_OK | os.W_OK):
        error_console.print("[bold red][-] /dev/fuse is not accessible by your user account.[/bold red]")
        return False

    return True


def probe_tcp_connection(host: str, port: int, timeout: float = 3.0) -> tuple[bool, str]:
    """Pre-flight check to verify if remote SSH port is open and reachable."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "Reachable"
    except socket.timeout:
        return False, f"Connection timed out (port {port} unreachable)"
    except ConnectionRefusedError:
        return False, f"Connection refused on port {port} (sshd service may be down)"
    except OSError as exc:
        return False, f"Network error: {exc.strerror or exc}"


def wait_until_unmounted(mount_point: Path, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_mount_info_for(mount_point) is None:
            return True
        time.sleep(0.1)
    return get_mount_info_for(mount_point) is None


def mount_appeared(mount_point: Path) -> bool:
    if get_mount_info_for(mount_point) is not None:
        return True

    if mount_point.is_symlink():
        with contextlib.suppress(OSError):
            return os.path.ismount(mount_point)

    return False


def wait_until_mounted(mount_point: Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if mount_appeared(mount_point):
            return True
        time.sleep(0.1)
    return mount_appeared(mount_point)


def unmount(
    target_path: Path | str | None = None,
    *,
    all_mounts: bool = False,
    lazy: bool = False,
    interactive: bool = False,
) -> bool:
    if all_mounts:
        mounts = get_active_mounts()
        if not mounts:
            console.print("[bold yellow][*] No active SSHFS connections to unmount.[/bold yellow]")
            return True
        success = True
        for m in mounts:
            console.print(f"[bold cyan][*] Unmounting {m.mount_point} ({m.source})...[/bold cyan]")
            if not unmount(m.mount_point, lazy=lazy, interactive=False):
                success = False
        return success

    if target_path is None:
        mounts = get_active_mounts()
        if not mounts:
            console.print("[bold yellow][*] No directory is currently mounted.[/bold yellow]")
            return True

        if len(mounts) == 1:
            target_path = mounts[0].mount_point
        elif interactive:
            table = Table(
                title="[bold yellow]Select Connection to Unmount[/bold yellow]",
                border_style="yellow",
                expand=False,
            )
            table.add_column("#", style="bold cyan", width=4, justify="center")
            table.add_column("Source Target", style="bold white")
            table.add_column("Mount Point", style="bold yellow")

            for i, m in enumerate(mounts, 1):
                table.add_row(str(i), m.source, str(m.mount_point))
            table.add_row(str(len(mounts) + 1), "[bold red]ALL CONNECTIONS[/bold red]", "Unmount everything")

            console.print(table)

            raw = Prompt.ask(
                f"Select connection to unmount (1-{len(mounts) + 1})",
                default="1",
            ).strip()

            if raw == str(len(mounts) + 1) or raw.lower() in ("all", "a"):
                return unmount(all_mounts=True, lazy=lazy, interactive=False)
            else:
                try:
                    idx = int(raw)
                    if 1 <= idx <= len(mounts):
                        target_path = mounts[idx - 1].mount_point
                    else:
                        error_console.print("[bold red][-] Invalid selection.[/bold red]")
                        return False
                except ValueError:
                    error_console.print("[bold red][-] Invalid selection.[/bold red]")
                    return False
        else:
            target_path = mounts[0].mount_point

    target_p = Path(target_path).expanduser().resolve()
    info = get_mount_info_for(target_p)

    if info is None:
        fusermount = find_executable("fusermount3")
        if fusermount:
            with contextlib.suppress(OSError):
                subprocess.run(
                    [fusermount, "-u", str(target_p)],
                    check=False,
                    text=True,
                    capture_output=True,
                )
            if wait_until_unmounted(target_p):
                console.print(f"[bold green][+] Successfully unmounted {target_p}.[/bold green]")
                return True
        console.print(f"[bold yellow][*] {target_p} is not currently mounted.[/bold yellow]")
        return True

    source, fstype = info

    if not (fstype == "fuse" or fstype.startswith("fuse.")):
        error_console.print(
            f"[bold red][-] {target_p} is mounted as '{fstype}', not FUSE. Refusing to unmount.[/bold red]"
        )
        return False

    fusermount = find_executable("fusermount3")
    if not fusermount:
        if not ensure_unmount_dependencies():
            return False
        fusermount = find_executable("fusermount3")
        if not fusermount:
            return False

    cmd = [fusermount, "-u"]
    if lazy:
        cmd.append("-z")
    cmd.append(str(target_p))

    action = "Lazily unmounting" if lazy else "Unmounting"
    console.print(f"[bold cyan][*] {action} {target_p} ({source})...[/bold cyan]")

    try:
        proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    except OSError as exc:
        error_console.print(f"[bold red][-] Failed to execute fusermount3: {exc}[/bold red]")
        return False

    if proc.returncode == 0:
        if wait_until_unmounted(target_p):
            console.print(f"[bold green][+] Successfully unmounted {target_p}.[/bold green]")
            return True
        error_console.print(
            "[bold red][-] Unmount command returned success but mount point is still present.[/bold red]"
        )
        return False

    if not lazy:
        return unmount(target_p, lazy=True, interactive=interactive)

    output = (proc.stderr or proc.stdout or "").strip()
    if output:
        error_console.print(f"[bold red]{output}[/bold red]")
    return False


def display_mount_failure(
    target: ParsedTarget,
    mount_point: Path,
    returncode: int,
    stderr: str,
    probe_msg: str | None = None,
) -> None:
    """Render a detailed Rich diagnosis panel fitted tightly to content width."""
    canonical = target.canonical_string
    lines: list[str] = [
        f"[bold white]Target Spec:[/bold white] [cyan]{canonical}[/cyan]",
        f"[bold white]Mount Point:[/bold white] [yellow]{mount_point}[/yellow]",
        f"[bold white]Exit Code:[/bold white] [bold red]{returncode}[/bold red]",
    ]

    if probe_msg:
        lines.append(f"[bold white]Network Probe:[/bold white] [bold red]{probe_msg}[/bold red]")

    err_text = stderr.strip() if stderr else "No output returned by sshfs."

    # Diagnostics logic
    tips: list[str] = []
    if "Connection refused" in err_text or (probe_msg and "refused" in probe_msg):
        tips.append(f"• The remote SSH daemon (sshd) is not running on port {target.port}")
        tips.append("• Check if target IP is correct and SSH service is started on remote server.")
    elif "Permission denied" in err_text:
        user_str = target.user or "default"
        tips.append(f"• SSH key or password authentication failed for user '{user_str}'")
        target_prefix = f"{target.user}@" if target.user else ""
        tips.append(f"• Test manually with: [cyan]ssh {target_prefix}{target.host}[/cyan]")
    elif "subsystem request failed" in err_text or "sftp" in err_text.lower():
        tips.append("• The remote SSH server refused SFTP access.")
        tips.append("• Ensure Subsystem sftp /usr/lib/ssh/sftp-server is enabled in remote /etc/ssh/sshd_config")
    elif "Host key verification failed" in err_text:
        tips.append("• Remote SSH host key changed or is not trusted.")
        tips.append(f"• Run [cyan]ssh {target.host}[/cyan] once to accept the key.")
    elif "Connection reset" in err_text:
        tips.append("• OpenSSH 9.8+ PerSourcePenalty (IP rate limit penalty ban) active on remote server.")
        tips.append("• OpenSSH penalized client IP due to rapid connections or failed authentication.")
        tips.append("• Wait 15-20 seconds for the penalty window to expire before retrying.")
    elif "not empty" in err_text:
        tips.append("• Mount point directory is not empty.")
    else:
        tips.append("• Verify target IP/hostname, remote path, SSH credentials, and firewall rules.")

    grid = Table.grid(expand=False)
    grid.add_column()
    grid.add_row(Text.from_markup("\n".join(lines)))
    grid.add_row(Text(""))
    grid.add_row(Text.from_markup("[bold white]Error Output:[/bold white]"))
    grid.add_row(Syntax(err_text, "text", theme="ansi_dark", word_wrap=True))
    grid.add_row(Text(""))
    grid.add_row(Text.from_markup("[bold yellow]Troubleshooting Tips:[/bold yellow]"))
    grid.add_row(Text.from_markup("\n".join(tips)))

    console.print(
        Panel.fit(
            grid,
            title="[bold red]󰅙 SSHFS Mount Operation Failed[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
    )


def mount(raw_target: str, custom_mount_path: str | None = None, open_gui_prompt: bool = False) -> bool:
    parsed = parse_target(raw_target)
    if parsed is None:
        error_console.print(
            "[bold red][-] Invalid target. Accepted formats: user@host, host, user@host:/path, user@host:port, or ssh://user@host[:port]/path[/bold red]"
        )
        return False

    mount_point = derive_mount_point(parsed, custom_mount_path)
    target_spec = parsed.sshfs_target_spec
    canonical = parsed.canonical_string

    # Clean up any stale/broken FUSE mounts on local machine for target host (e.g. after VM snapshot revert)
    for active_m in get_active_mounts():
        if parsed.host in active_m.source:
            is_h = False
            with contextlib.suppress(OSError):
                is_h = active_m.mount_point.is_dir() and os.access(active_m.mount_point, os.R_OK)
            if not is_h:
                console.print(
                    f"[bold yellow][!] Unresponsive mount for {parsed.host} detected at {active_m.mount_point} (VM snapshot reset?). Cleaning up...[/bold yellow]"
                )
                cleanup_stale_mount(active_m.mount_point)

    info = get_mount_info_for(mount_point)
    if info is not None:
        is_healthy = False
        with contextlib.suppress(OSError):
            is_healthy = mount_point.is_dir() and os.access(mount_point, os.R_OK)

        if not is_healthy:
            console.print(
                f"[bold yellow][!] Unresponsive mount detected at {mount_point}. Cleaning up...[/bold yellow]"
            )
            cleanup_stale_mount(mount_point)
        elif info[0] == target_spec or info[0] == canonical:
            console.print(f"[bold green][+] {mount_point} is already mounted to {canonical}.[/bold green]")
            return True
        else:
            console.print(
                f"[bold yellow][*] {mount_point} currently points to {info[0]}. Unmounting to switch target...[/bold yellow]"
            )
            if not unmount(mount_point, interactive=False):
                error_console.print(
                    "[bold red][-] Cannot mount while existing mount point is active.[/bold red]"
                )
                return False

    extra_options: list[str] = []
    if parsed.port != 22:
        extra_options.extend(["-p", str(parsed.port)])

    try:
        if not mount_point.is_symlink():
            mount_point.mkdir(parents=True, exist_ok=True, mode=0o700)

            if not mount_point.is_dir():
                error_console.print(f"[bold red][-] {mount_point} exists and is not a directory.[/bold red]")
                return False

            try:
                if any(mount_point.iterdir()):
                    if not Confirm.ask(
                        f"[yellow][!] {mount_point} is not empty. Mounting will obscure existing files. Continue?[/yellow]",
                        default=False,
                    ):
                        console.print("[bold yellow][*] Mount cancelled by user.[/bold yellow]")
                        return False

                    extra_options.extend(("-o", "nonempty"))
            except OSError as exc:
                if exc.errno in (errno.ENOTCONN, errno.EBUSY):
                    console.print("[bold yellow][!] Stale mount detected. Cleaning up...[/bold yellow]")
                    if not cleanup_stale_mount(mount_point):
                        error_console.print(f"[bold red][-] Stale mount cleanup failed: {exc}[/bold red]")
                        return False
                else:
                    error_console.print(f"[bold red][-] Cannot inspect mount point: {exc}[/bold red]")
                    return False
    except OSError as exc:
        if exc.errno in (errno.ENOTCONN, errno.EBUSY):
            console.print("[bold yellow][!] Stale mount detected on directory creation. Cleaning up...[/bold yellow]")
            cleanup_stale_mount(mount_point)
        else:
            error_console.print(f"[bold red][-] Cannot prepare mount directory: {exc}[/bold red]")
            return False

    if not ensure_mount_dependencies():
        return False

    if not check_fuse_device():
        return False

    sshfs = find_executable("sshfs")
    if not sshfs:
        error_console.print("[bold red][-] sshfs executable missing.[/bold red]")
        return False

    if mount_point.is_symlink():
        cleanup_stale_mount(mount_point)

    options = list(SSHFS_OPTIONS)
    with contextlib.suppress(AttributeError, OSError):
        options.append(f"uid={os.getuid()}")
        options.append(f"gid={os.getgid()}")

    if "SSHPASS" in os.environ and find_executable("sshpass"):
        options.append("ssh_command=sshpass -e ssh")

    cmd = [sshfs]
    for option in options:
        cmd.extend(("-o", option))

    if parsed.port != 22:
        cmd.extend(("-p", str(parsed.port)))

    if "nonempty" in extra_options:
        cmd.extend(("-o", "nonempty"))

    cmd.extend((target_spec, str(mount_point)))

    # Print clean progress without an interactive TTY-clashing spinner thread
    console.print(f"[bold cyan][*] Connecting to {canonical}...[/bold cyan]")

    try:
        # Run sshfs allowing TTY passthrough if password authentication is requested by OpenSSH
        proc = subprocess.run(
            cmd,
            check=False,
            text=True,
        )
        if proc.returncode != 0:
            display_mount_failure(parsed, mount_point, proc.returncode, "sshfs exited with non-zero status.")
            return False
    except OSError as exc:
        error_console.print(f"[bold red][-] Failed to launch sshfs: {exc}[/bold red]")
        return False

    mounted = wait_until_mounted(mount_point)

    if mounted:
        console.print(
            Panel.fit(
                f"[bold green]󰄬 Filesystem successfully mounted![/bold green]\n"
                f"[white]Remote:[/white] [cyan]{canonical}[/cyan]\n"
                f"[white]Local Path:[/white] [bold yellow]{mount_point}[/bold yellow]",
                border_style="green",
            )
        )
        xdg_open = find_executable("xdg-open")
        if open_gui_prompt and xdg_open and sys.stdin.isatty() and Confirm.ask("Open mounted folder in GUI file manager?", default=False):
            with contextlib.suppress(OSError):
                subprocess.Popen([xdg_open, str(mount_point)])
        return True

    display_mount_failure(
        parsed,
        mount_point,
        returncode=1,
        stderr="sshfs command exited cleanly but mount point did not register in /proc/mounts.",
    )
    return False


def smart_penalty_backoff(seconds: int = 15) -> None:
    """Live visual countdown to allow OpenSSH PerSourcePenalty timer to expire."""
    with console.status("[bold cyan][*] OpenSSH rate limit penalty active. Waiting for ban to expire...[/bold cyan]") as status:
        for remaining in range(seconds, 0, -1):
            status.update(f"[bold cyan][*] OpenSSH rate limit penalty active. Waiting {remaining}s for ban to expire...[/bold cyan]")
            time.sleep(1.0)
    console.print("[bold green][+] Penalty window expired. Retrying connection now...[/bold green]")


def handle_mount_flow(
    parsed_target: ParsedTarget, initial_custom_path: str | None, history: list[str]
) -> list[str]:
    """Handle mounting, interactive retries on failure, and state updates."""
    current_target = parsed_target
    custom_path = initial_custom_path

    while True:
        target_str = current_target.canonical_string
        success = mount(target_str, custom_path)
        if success:
            history = update_history(history, target_str)
            Prompt.ask("Press Enter to return to main menu...")
            return history

        # Mount failed -> offer retry options instead of forcing restart from scratch!
        console.print()
        table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
        table.add_column("Key", style="bold cyan", justify="right")
        table.add_column("Action", style="bold white")
        table.add_row("1", "Retry connection [dim](immediate)[/dim]")
        table.add_row("w", "Wait 15s for OpenSSH rate limit penalty ban to expire & auto-retry")
        table.add_row("2", "Change SSH target / username")
        table.add_row("0", "Return to Main Menu")

        console.print(Panel.fit(table, title="[bold yellow]Connection Failure Options[/bold yellow]", border_style="yellow"))

        try:
            choice = Prompt.ask("Select option", default="1").strip().lower()
        except (KeyboardInterrupt, EOFError):
            return history

        if choice in ("1", "retry", "r"):
            console.print(f"[bold cyan][*] Retrying connection to {target_str}...[/bold cyan]")
            continue
        elif choice in ("w", "wait", "auto", "a"):
            smart_penalty_backoff(15)
            continue
        elif choice in ("2", "change"):
            new_target = Prompt.ask("Enter new SSH target", default=target_str).strip()
            parsed_new = parse_target(new_target)
            if parsed_new:
                parsed_user = select_remote_user(parsed_new)
                if parsed_user:
                    current_target = parsed_user
                    default_dir = derive_mount_point(current_target)
                    folder_input = Prompt.ask("Enter local mount path", default=str(default_dir)).strip()
                    custom_path = folder_input if folder_input and folder_input != str(default_dir) else None
            continue
        else:
            return history


def select_remote_user(parsed: ParsedTarget) -> ParsedTarget | None:
    """Ensure a remote username is set without unneeded prompts if already provided."""
    if parsed.user:
        return parsed

    default_user = os.environ.get("USER", "root")
    console.print(f"[dim]No SSH user specified for target host '{parsed.host}'.[/dim]")

    user_input = Prompt.ask(
        "Enter remote SSH username",
        default=default_user,
    ).strip()

    if not user_input:
        user_input = default_user

    return ParsedTarget(
        user=user_input,
        host=parsed.host,
        port=parsed.port,
        remote_path=parsed.remote_path,
        raw_input=parsed.raw_input,
    )


def print_usage() -> None:
    render_banner()
    console.print("\n[bold white]Usage:[/bold white]")
    console.print("  [cyan]dusky_ssh_filesystem.py [TARGET] [MOUNT_PATH][/cyan]  Mount target directly")
    console.print("  [cyan]dusky_ssh_filesystem.py -u | --unmount [PATH|all][/cyan] Unmount connection(s)")
    console.print("  [cyan]dusky_ssh_filesystem.py -s | --status[/cyan]         Show all active mounts")
    console.print("  [cyan]dusky_ssh_filesystem.py -h | --help[/cyan]           Show this help message")
    console.print("\n[bold white]Examples:[/bold white]")
    console.print("  [dim]dusky_ssh_filesystem.py user@192.168.1.50[/dim]")
    console.print("  [dim]dusky_ssh_filesystem.py root@host ~/Documents/sshfs/server1[/dim]")
    console.print("  [dim]dusky_ssh_filesystem.py -u all[/dim]")


def main() -> int:
    ensure_directories()
    history = load_state()
    ensure_state_file()

    # CLI mode
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        if arg in ("-h", "--help"):
            print_usage()
            return 0
        elif arg in ("-u", "--unmount"):
            target_arg = sys.argv[2].strip() if len(sys.argv) > 2 else None
            if target_arg and target_arg.lower() in ("all", "a"):
                return 0 if unmount(all_mounts=True, interactive=False) else 1
            elif target_arg:
                return 0 if unmount(target_path=target_arg, interactive=False) else 1
            else:
                return 0 if unmount(interactive=False) else 1
        elif arg in ("-s", "--status"):
            render_banner()
            active = get_active_mounts()
            render_active_mounts(active)
            return 0
        else:
            parsed = parse_target(arg)
            if not parsed:
                error_console.print(f"[bold red][-] Invalid target argument: '{arg}'[/bold red]")
                return 1
            custom_path = sys.argv[2].strip() if len(sys.argv) > 2 else None
            if mount(parsed.canonical_string, custom_path):
                update_history(history, parsed.canonical_string)
                return 0
            return 1

    while True:
        console.clear()
        render_banner()
        console.print()

        active = get_active_mounts()
        render_active_mounts(active)
        console.print()

        if history:
            render_history(history)
            console.print()

        menu_table = Table(show_header=False, box=None, padding=(0, 1), expand=False)
        menu_table.add_column("Key", style="bold cyan", justify="right")
        menu_table.add_column("Action", style="bold white")

        menu_table.add_row("1", "Connect to a new server [dim](mount alongside existing)[/dim]")
        if history:
            menu_table.add_row("2", "Quick connect to recent server")
        menu_table.add_row("3", "Unmount connection(s)")
        menu_table.add_row("4", "Refresh mount status")
        if active and find_executable("xdg-open"):
            menu_table.add_row("o", "Open mounted folder in GUI file manager")
        menu_table.add_row("0", "Exit")

        console.print(Panel.fit(menu_table, title="[bold yellow]Menu Options[/bold yellow]", border_style="yellow"))

        try:
            choice = Prompt.ask("Select an option", default="1").strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold yellow][*] Exiting...[/bold yellow]")
            break

        if choice in ("0", "x", "q", "quit", "exit"):
            console.print("[bold yellow][*] Goodbye![/bold yellow]")
            break

        match choice:
            case "1" | "c" | "connect":
                prompt_default = history[0] if history else ""
                target_raw = Prompt.ask(
                    "Enter SSH target (e.g. user@host, host:/path, or ssh user@host)",
                    default=prompt_default if prompt_default else None,
                )
                if not target_raw:
                    continue

                parsed = parse_target(target_raw)
                if parsed is None:
                    error_console.print(
                        "[bold red][-] Invalid SSH target format. Examples: user@host, host, user@host:port[/bold red]"
                    )
                    Prompt.ask("Press Enter to continue...")
                    continue

                parsed_with_user = select_remote_user(parsed)
                if parsed_with_user is None:
                    continue

                default_dir = derive_mount_point(parsed_with_user)
                folder_input = Prompt.ask(
                    "Enter local mount path",
                    default=str(default_dir),
                ).strip()

                # Guard against user entering single-digit menu choice by mistake
                if folder_input in ("1", "2", "3", "4") and folder_input != str(default_dir):
                    if not Confirm.ask(
                        f"[bold yellow]You entered '{folder_input}'. Did you mean folder path '{BASE_MOUNT_DIR / folder_input}'?[/bold yellow]",
                        default=False,
                    ):
                        folder_input = str(default_dir)

                custom_path = folder_input if folder_input and folder_input != str(default_dir) else None

                history = handle_mount_flow(parsed_with_user, custom_path, history)

            case "2" | "quick" if history:
                raw_idx = Prompt.ask(f"Select connection (1-{len(history)})", default="1").strip()
                try:
                    idx = int(raw_idx)
                    if not 1 <= idx <= len(history):
                        error_console.print("[bold red][-] Invalid selection.[/bold red]")
                        Prompt.ask("Press Enter to continue...")
                        continue
                except ValueError:
                    error_console.print("[bold red][-] Invalid selection.[/bold red]")
                    Prompt.ask("Press Enter to continue...")
                    continue

                target = history[idx - 1]
                parsed = parse_target(target)
                if parsed is None:
                    error_console.print("[bold red][-] Invalid entry in history.[/bold red]")
                    Prompt.ask("Press Enter to continue...")
                    continue

                parsed_with_user = select_remote_user(parsed)
                if parsed_with_user is None:
                    continue

                default_dir = derive_mount_point(parsed_with_user)
                folder_input = Prompt.ask(
                    "Enter local mount path",
                    default=str(default_dir),
                ).strip()

                custom_path = folder_input if folder_input and folder_input != str(default_dir) else None

                history = handle_mount_flow(parsed_with_user, custom_path, history)

            case "3" | "u" | "unmount":
                unmount(interactive=True)
                Prompt.ask("Press Enter to continue...")

            case "4" | "r" | "refresh":
                continue

            case "o" | "open" if active:
                xdg_open = find_executable("xdg-open")
                if xdg_open:
                    for m in active:
                        with contextlib.suppress(OSError):
                            subprocess.Popen([xdg_open, str(m.mount_point)])
                    console.print("[bold green][+] Opened mount folder(s) in file manager.[/bold green]")
                    time.sleep(1)
                continue

            case _:
                error_console.print("[bold red][-] Invalid option. Enter a valid menu choice.[/bold red]")
                Prompt.ask("Press Enter to continue...")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[bold yellow][*] Exiting...[/bold yellow]")
        sys.exit(0)
    except BrokenPipeError:
        with contextlib.suppress(OSError):
            sys.stdout.close()
        sys.exit(0)

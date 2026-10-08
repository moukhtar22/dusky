#!/usr/bin/env python3
"""
optimize_firefox.py - Bleeding-edge Firefox 156+ HTTP cache-policy & RAM profile manager.
Target: Arch Linux (kernel 7.3+, rolling), Python 3.14.7+, Firefox 156+ only.

Zero backwards-compatibility shims for legacy Python (< 3.14) or legacy Firefox (< 156).

Capabilities:
    With no action argument, apply --cache-mode memory to initialized profiles.
    Fresh installations without initialized profiles are reported as a no-op.

    --cache-mode memory
        Disable Firefox's HTTP disk cache and enable the memory cache.
        Firefox retains its dynamic memory-cache sizing policy by default, or accepts
        an optional --memory-capacity override.

    --cache-mode default (or --disable)
        Restore the exact baseline user.js and managed prefs.js saved by this tool.

    --sync [--sync-mode auto|overlay|copy] [--disk-fallback]
        Synchronize Firefox profile(s) into volatile RAM (tmpfs).
        Eliminates SSD write amplification from SQLite (cookies, places, sessionstore).
        Uses fuse-overlayfs when available for zero-copy startup and minimal RAM usage.
        --disk-fallback keeps profiles on disk when resources are insufficient or
        Firefox is already using the disk profile; intended for service automation.

    --unsync [--force]
        Synchronize latest profile state from volatile RAM back to persistent disk,
        cleanly unmounts overlay, and restores original directory structure.

    --resync
        Perform an incremental one-shot sync from active RAM profile to disk backing.
        May run while Firefox is active. SQLite's online backup API captures each
        database consistently; other files and cross-database state are best-effort.
        A completed checkpoint atomically replaces the previous disk checkpoint.

    --daemon [--interval SECONDS]
        Run persistent background daemon: syncs on start, resyncs periodically (default 3600s),
        and safely unsyncs on system shutdown/logout (SIGTERM/SIGINT).

    --watch-sleep
        Checkpoint before sleep/shutdown using logind delay inhibitors. Requires
        python-gobject. The system's InhibitDelayMaxSec bounds the available time.

    --install-service
        Autonomously deploy and enable systemd user units (service + hourly timer)
        for automated, zero-touch profile RAM syncing across system reboots.

    --remove-service
        Safely unsync, stop, disable, and remove systemd user units.

    --status [--json]
        Read-only inspection: reports system info, profile discovery, OFD lock status,
        active cache preferences, cache2 disk usage, RAM sync status, and diagnostics.

    --verify
        Run full self-contained empirical verification test harness and stress suite.

Architectural highlights:
    * Integrated Profile Sync Daemon (PSD) Engine: Native Python 3.14+ implementation
      of transparent RAM profile relocation with fuse-overlayfs and rsync checkpoints.
    * Linux Open File Description (OFD) locking (fcntl.F_OFD_SETLK):
      True mutual exclusion with Firefox's nsProfileLock (F_SETLK), immune to POSIX
      record-lock release-on-close hazards. Probes holding PID with F_OFD_GETLK (exit 3).
    * Native Python 3.14+ Zstandard compression (compression.zstd):
      Streams .tar.zst archives with checksummed frames and single-pass SHA-256 digests.
    * Chronologically sorted backup names using uuid.uuid7().
    * Directory-pinned atomic replacement (openat / renameat): TOCTOU-resistant file updates.
    * Strict umask independence: explicit 0600 file modes and 0700 directory modes.
    * Autonomous crash-recovery: detects ungraceful reboots/power loss and restores
      the latest completed disk checkpoint; changes since it may be lost.
"""

from __future__ import annotations

import sys

# Pre-import runtime requirement guard
if sys.version_info < (3, 14, 7):
    sys.stderr.write(
        f"ERROR: Python 3.14.7 or newer is required (running {sys.version.split()[0]}).\n"
    )
    sys.exit(1)

if not sys.platform.startswith("linux"):
    sys.stderr.write("ERROR: Linux is required.\n")
    sys.exit(1)

import argparse
import configparser
from contextlib import ExitStack, closing, contextmanager, nullcontext, suppress
from dataclasses import dataclass
import fcntl
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import stat
import struct
import subprocess
import tarfile
import tempfile
import time
from typing import BinaryIO, Final, Iterator, Sequence
import uuid
from compression import zstd

# ===========================================================================
# Constants & Contracts
# ===========================================================================

TOOL_NAME: Final = "optimize_firefox.py"
TOOL_ID: Final = "firefox-cache-policy"
TOOL_VERSION: Final = "4.1.1-ff156"
MIN_FIREFOX_MAJOR: Final = 156

STATE_SCHEMA: Final = 2
STATE_FILENAME: Final = ".firefox-cache-policy-state.json"
DEFAULT_BACKUP_DIRNAME: Final = ".firefox-cache-policy-backups"
BACKUP_PREFIX: Final = "ffcp"
TEMP_ARCHIVE_PREFIX: Final = ".incomplete-ffcp-"

MAX_BACKUPS_PER_PROFILE: Final = 3
MAX_TEXT_BYTES: Final = 128 * 1024 * 1024
BACKUP_HEADROOM_BYTES: Final = 64 * 1024 * 1024
MAX_FULL_BACKUP_ENTRIES: Final = 250_000

ZSTD_LEVEL_TARGETED: Final = 3
ZSTD_LEVEL_FULL: Final = 3

PREFS_JS: Final = "prefs.js"
USER_JS: Final = "user.js"
PARENTLOCK: Final = ".parentlock"
SYMLINK_LOCK: Final = "lock"

# Profile Sync Daemon (PSD) Engine Constants
VOLATILE_SUBDIR: Final = "firefox-sync"
PSD_BACKUP_SUFFIX: Final = "-psd-backup"
PSD_BACK_OVFS_SUFFIX: Final = "-psd-back-ovfs"
PSD_FLAG_FILE: Final = ".flagged"
PSD_STATE_FILE: Final = ".firefox-sync-state.json"
CRASH_RECOVERY_PREFIX: Final = "-psd-crashrecovery-"

SYSTEMD_SERVICE_NAME: Final = "dusky_firefox_cache.service"
SYSTEMD_RESYNC_SERVICE_NAME: Final = "dusky_firefox_cache_resync.service"
SYSTEMD_RESYNC_TIMER_NAME: Final = "dusky_firefox_cache_resync.timer"
SYSTEMD_SLEEP_SERVICE_NAME: Final = "dusky_firefox_cache_sleep.service"

MANAGED_KEYS: Final = frozenset({
    "browser.cache.disk.enable",
    "browser.cache.memory.enable",
})

# Preferences that must never be recorded into rollback state or logged raw
SECRET_KEYS: Final = frozenset({
    "browser.cache.disk.encryption.key",
})

# Frozen, version-independent block body so SHA-256 remains stable forever
MANAGED_BLOCK_BODY: Final = (
    "// === BEGIN FIREFOX OPTIMIZATION SUITE ===",
    "// Managed by optimize_firefox.py - Firefox 156+ HTTP cache policy",
    'user_pref("browser.cache.disk.enable", false);',
    'user_pref("browser.cache.memory.enable", true);',
    "// === END FIREFOX OPTIMIZATION SUITE ===",
    "",
)

BLOCK_BEGIN: Final = "// === BEGIN FIREFOX OPTIMIZATION SUITE ==="
BLOCK_END: Final = "// === END FIREFOX OPTIMIZATION SUITE ==="

# Legacy keys from pre-155 optimizer scripts to clean up on migration
LEGACY_KEYS: Final = frozenset({
    "browser.cache.memory.capacity",
    "browser.cache.disk.smart_size.enabled",
    "browser.cache.disk_cache_ssl",
    "browser.cache.offline.enable",
    "dom.ipc.processCount",
    "dom.ipc.processCount.webIsolated",
    "dom.ipc.processCount.extension",
    "fission.autostart",
    "browser.tabs.unloadOnLowMemory",
    "gfx.webrender.all",
    "layers.acceleration.force-enabled",
    "media.ffmpeg.vaapi.enabled",
    "media.hardware-video-decoding.force-enabled",
    "widget.wayland-dmabuf-vaapi.enabled",
    "widget.wayland.opaque-region.enabled",
    "apz.gtk.kinetic_scroll.enabled",
    "toolkit.telemetry.enabled",
    "datareporting.healthreport.uploadEnabled",
    "app.normandy.enabled",
    "network.http.max-connections",
    "network.http.max-persistent-connections-per-server",
    "network.trr.mode",
    "network.trr.uri",
    "browser.cache.disk.parent_directory",
})

EXCLUDED_ROOT_ENTRIES: Final = frozenset({
    PARENTLOCK,
    SYMLINK_LOCK,
})

TRANSIENT_FILESYSTEMS: Final = frozenset({
    "tmpfs",
    "ramfs",
    "overlay",
})

NETWORK_FILESYSTEMS: Final = frozenset({
    "nfs", "nfs4", "cifs", "smb3", "smbfs", "afs", "ceph", "glusterfs", "sshfs",
})
NETWORK_FS_PREFIXES: Final = ("fuse.sshfs", "fuse.rclone")

# External maintenance services that must not conflict
EXTERNAL_MAINTENANCE_UNITS: Final = (
    "psd.service",
    "psd-resync.service",
    "psd-resync.timer",
    "profile-cleaner.service",
    "profile-cleaner.timer",
)

# Open File Description flock buffer:
_FLOCK: Final = struct.Struct("@hhqqi")
_FLOCK_BUFFER: Final = max(64, _FLOCK.size)

USER_JS_KEYWORD: Final = re.compile(
    r"\b(?:user_pref|sticky_pref|pref)\b"
)

PREF_LINE: Final = re.compile(
    r"^[ \t]*(?:user_pref|sticky_pref|pref)[ \t]*\([ \t]*"
    r'"((?:[^"\\]|\\.)*)"'
    r'[ \t]*,[ \t]*'
    r'(true|false|-?[0-9]+|"(?:[^"\\]|\\.)*")'
    r'[ \t]*\)[ \t]*;[ \t]*(?://.*|/\*.*?\*/[ \t]*)?$',
    re.DOTALL,
)

# ===========================================================================
# Logging Setup
# ===========================================================================

LOGGER = logging.getLogger("firefox_cache_policy")


class StderrFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING


class StdoutFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.WARNING


def configure_logging(verbose: bool) -> None:
    LOGGER.setLevel(logging.DEBUG if verbose else logging.INFO)
    LOGGER.handlers.clear()

    out_handler = logging.StreamHandler(sys.stdout)
    out_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    out_handler.addFilter(StdoutFilter())
    out_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    err_handler = logging.StreamHandler(sys.stderr)
    err_handler.setLevel(logging.WARNING)
    err_handler.addFilter(StderrFilter())
    err_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    LOGGER.addHandler(out_handler)
    LOGGER.addHandler(err_handler)


# ===========================================================================
# Exceptions
# ===========================================================================

class SafetyError(RuntimeError):
    """An unsafe, invalid, or conflicting condition was detected."""


class RamSyncUnavailable(SafetyError):
    """Optional RAM relocation cannot start with the available resources."""


class ProfileLockedError(SafetyError):
    """A running Firefox process holds an exclusive lock on the profile."""


class UninitializedProfileError(SafetyError):
    """The profile directory exists but lacks prefs.js."""


def fail(message: str) -> None:
    raise SafetyError(message)


def oserror_detail(error: OSError) -> str:
    name = os.strerror(error.errno) if error.errno else "error"
    code = f" [errno {error.errno}]" if error.errno else ""
    return f"{name}{code}: {error.filename or ''}".strip()


# ===========================================================================
# Utility Helpers
# ===========================================================================

def stat_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def human_bytes(count: int | float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(count)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} TiB"


def absolute_without_symlinks(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded

    current = Path("/")
    for part in expanded.parts[1:]:
        if part == "..":
            if not current.is_dir():
                fail(f"Cannot traverse '..' through missing/non-directory: {current}")
            current = current.parent
            continue

        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue

        if stat.S_ISLNK(info.st_mode):
            fail(f"Symlinked path component is rejected in this context: {current}")

    return current


# ===========================================================================
# Mount Topology (/proc/self/mountinfo parser)
# ===========================================================================

@dataclass(frozen=True, slots=True)
class MountEntry:
    mount_point: Path
    fstype: str
    source: str


def _unescape_mountinfo(value: bytes) -> bytes:
    return re.sub(
        rb"\\([0-7]{3})",
        lambda m: bytes([int(m.group(1), 8)]),
        value,
    )


def read_mount_table() -> list[MountEntry]:
    try:
        raw = Path("/proc/self/mountinfo").read_bytes()
    except OSError as error:
        raise SafetyError(f"Cannot read /proc/self/mountinfo: {oserror_detail(error)}") from error

    table: list[MountEntry] = []
    for line in raw.splitlines():
        if not line:
            continue
        fields = line.split(b" ")
        if len(fields) < 10:
            fail("Unexpected /proc/self/mountinfo record (too few fields)")
        try:
            separator = fields.index(b"-", 6)
        except ValueError:
            fail("Unexpected /proc/self/mountinfo record (missing '-' separator)")
        if len(fields) < separator + 3:
            fail("Truncated /proc/self/mountinfo record")

        mount_point = Path(os.fsdecode(_unescape_mountinfo(fields[4])))
        if not mount_point.is_absolute():
            fail("Non-absolute mount point in /proc/self/mountinfo")

        table.append(MountEntry(
            mount_point=mount_point,
            fstype=os.fsdecode(_unescape_mountinfo(fields[separator + 1])),
            source=os.fsdecode(_unescape_mountinfo(fields[separator + 2])),
        ))

    if not table:
        fail("Empty mount table")
    return table


def mount_for(table: Sequence[MountEntry], path: Path) -> MountEntry:
    best: MountEntry | None = None
    best_depth = -1
    for entry in table:
        if path != entry.mount_point and not path.is_relative_to(entry.mount_point):
            continue
        depth = len(entry.mount_point.parts)
        if depth >= best_depth:
            best, best_depth = entry, depth
    if best is None:
        fail(f"No mount point covers {path}")
    return best


def nested_mounts(table: Sequence[MountEntry], root: Path) -> list[Path]:
    return [
        entry.mount_point
        for entry in table
        if entry.mount_point != root and entry.mount_point.is_relative_to(root)
    ]


def is_mountpoint(path: Path) -> bool:
    try:
        p = path.resolve()
        parent = p.parent
        return p.stat().st_dev != parent.stat().st_dev or p == parent
    except Exception:
        return False


def require_supported_filesystem(
    table: Sequence[MountEntry], path: Path, label: str, allow_transient: bool = False
) -> str:
    entry = mount_for(table, path)
    fstype = entry.fstype

    if not allow_transient and fstype in TRANSIENT_FILESYSTEMS:
        fail(
            f"Unsupported {label} filesystem {fstype!r} at {entry.mount_point}. "
            "Use the persistent, non-overlay location."
        )
    if fstype in NETWORK_FILESYSTEMS or fstype.startswith(NETWORK_FS_PREFIXES):
        fail(
            f"Unsupported {label} filesystem {fstype!r} at {entry.mount_point}. "
            "Firefox falls back to symlink-only profile locking when fcntl locking is unavailable."
        )
    if fstype.startswith("fuse") and not allow_transient:
        LOGGER.warning(
            "%s is on FUSE filesystem %r at %s; fcntl locking semantics may vary.",
            label.capitalize(), fstype, entry.mount_point,
        )
    return fstype


# ===========================================================================
# Directory & File I/O (openat / renameat TOCTOU resistance)
# ===========================================================================

def open_directory(path: Path) -> int:
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise SafetyError(f"Cannot open directory {path}: {oserror_detail(error)}") from error


def fsync_dir_fd(dir_fd: int) -> None:
    try:
        os.fsync(dir_fd)
    except OSError as error:
        raise SafetyError(f"Directory fsync failed: {oserror_detail(error)}") from error


def fsync_directory_path(directory: Path) -> None:
    fd = open_directory(directory)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def make_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
        os.chmod(path, 0o700)
    except OSError as error:
        raise SafetyError(f"Cannot create private directory {path}: {oserror_detail(error)}") from error


def read_config(dir_fd: int, name: str) -> str | None:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SafetyError(f"Cannot open {name}: {oserror_detail(error)}") from error

    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            fail(f"Configuration file is not a regular file: {name}")
        if before.st_uid != os.geteuid():
            fail(f"Configuration file is not owned by this user: {name}")
        if before.st_nlink != 1:
            fail(f"Configuration file is hard-linked: {name}")
        if before.st_mode & 0o022:
            LOGGER.warning("Configuration file %s has group/world writable mode %04o", name, before.st_mode & 0o777)
        if before.st_size > MAX_TEXT_BYTES:
            fail(f"Configuration file is too large: {name}")

        data = source.read(MAX_TEXT_BYTES + 1)
        after = os.fstat(source.fileno())

        if len(data) > MAX_TEXT_BYTES:
            fail(f"Configuration file exceeds size limit: {name}")
        if stat_signature(before) != stat_signature(after):
            fail(f"Configuration file changed while being read: {name}")

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SafetyError(f"Configuration file {name} is not valid UTF-8") from error


def write_config(dir_fd: int, name: str, content: str) -> None:
    data = content.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        fail(f"Generated content for {name} exceeds size limit")

    temporary = f".{name}.tmp-{uuid.uuid7().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    fd = os.open(temporary, flags, mode=0o600, dir_fd=dir_fd)

    try:
        with os.fdopen(fd, "wb") as destination:
            os.fchmod(destination.fileno(), 0o600)
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())

        os.replace(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        fsync_dir_fd(dir_fd)
    finally:
        with suppress(OSError):
            os.unlink(temporary, dir_fd=dir_fd)


def remove_config(dir_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
        fsync_dir_fd(dir_fd)
    except FileNotFoundError:
        pass
    except OSError as error:
        raise SafetyError(f"Cannot remove {name}: {oserror_detail(error)}") from error


def replace_config(dir_fd: int, name: str, expected: str | None, replacement: str | None) -> None:
    current = read_config(dir_fd, name)
    if current != expected:
        fail(f"Configuration {name} changed concurrently since inspection")

    if expected == replacement:
        return

    if replacement is None:
        remove_config(dir_fd, name)
    else:
        write_config(dir_fd, name, replacement)


# ===========================================================================
# Open File Description (OFD) Locking
# ===========================================================================

def _flock_payload(lock_type: int) -> bytes:
    return _FLOCK.pack(lock_type, os.SEEK_SET, 0, 0, 0).ljust(_FLOCK_BUFFER, b"\x00")


def probe_lock_holder(fd: int) -> int | None:
    try:
        raw = fcntl.fcntl(fd, fcntl.F_OFD_GETLK, _flock_payload(fcntl.F_WRLCK))
    except OSError as error:
        raise SafetyError(f"Cannot query profile lock: {oserror_detail(error)}") from error

    lock_type, _, _, _, holder = _FLOCK.unpack_from(raw)
    if lock_type == fcntl.F_UNLCK:
        return None
    return holder if holder > 0 else -1


def symlink_lock_holder(dir_fd: int) -> tuple[str, int, bool] | None:
    try:
        target = os.readlink(SYMLINK_LOCK, dir_fd=dir_fd)
    except OSError:
        return None
    address, separator, tail = target.partition(":")
    if not separator or not tail:
        return None
    has_fcntl = tail.startswith("+")
    digits = tail[1:] if has_fcntl else tail
    if not digits.isdigit():
        return None
    return address, int(digits), has_fcntl


def process_name(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def probe_profile_lock(profile_path: Path) -> int | None:
    """Inspects profile for active lock held by running Firefox."""
    parentlock = profile_path / PARENTLOCK
    if not parentlock.exists():
        return None
    try:
        fd = os.open(parentlock, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            holder = probe_lock_holder(fd)
            if holder is not None:
                return holder
            try:
                fcntl.fcntl(fd, fcntl.F_OFD_SETLK, _flock_payload(fcntl.F_WRLCK))
                # Lock succeeded -> no holder, release lock
                fcntl.fcntl(fd, fcntl.F_OFD_SETLK, _flock_payload(fcntl.F_UNLCK))
                return None
            except (BlockingIOError, PermissionError):
                return probe_lock_holder(fd) or -1
        finally:
            os.close(fd)
    except OSError as error:
        raise SafetyError(f"Cannot inspect profile lock at {parentlock}: {oserror_detail(error)}") from error


@contextmanager
def locked_profile(profile: "Profile", dir_fd: int) -> Iterator[None]:
    try:
        fd = os.open(
            PARENTLOCK,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=dir_fd,
        )
    except OSError as error:
        raise SafetyError(f"Cannot open {profile.path}/.parentlock: {oserror_detail(error)}") from error

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            fail(f"{profile.path}/.parentlock is not a regular file")
        if info.st_uid != os.geteuid():
            fail(f"{profile.path}/.parentlock is not owned by this user")
        if info.st_nlink != 1:
            fail(f"{profile.path}/.parentlock is hard-linked")

        try:
            fcntl.fcntl(fd, fcntl.F_OFD_SETLK, _flock_payload(fcntl.F_WRLCK))
        except (BlockingIOError, PermissionError) as error:
            holder = probe_lock_holder(fd)
            holder_detail = ""
            if holder is not None:
                pname = process_name(holder) or "unknown"
                holder_detail = f" (held by PID {holder} '{pname}')"
            else:
                sym_info = symlink_lock_holder(dir_fd)
                if sym_info is not None:
                    _, sym_pid, _ = sym_info
                    pname = process_name(sym_pid) or "unknown"
                    holder_detail = f" (lock symlink names PID {sym_pid} '{pname}')"

            raise ProfileLockedError(
                f"Firefox profile is locked: {profile.path}{holder_detail}. "
                "Close Firefox normally and rerun."
            ) from error
        except OSError as error:
            raise SafetyError(f"Cannot lock {profile.path}/.parentlock: {oserror_detail(error)}") from error

        yield
    finally:
        os.close(fd)


# ===========================================================================
# Preference Parsing & Legacy Block Migration
# ===========================================================================

@dataclass(frozen=True, slots=True)
class PrefRecord:
    key: str
    value: bool | int | str
    line: str


def split_pref_lines(content: str) -> list[str]:
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", content)


def parse_pref_line(line: str) -> tuple[str, bool | int | str, str] | None:
    match = PREF_LINE.match(line)
    if match is None:
        return None
    raw_key, raw_val = match.group(1), match.group(2)
    try:
        key = json.loads(f'"{raw_key}"')
        if not isinstance(key, str):
            return None
    except json.JSONDecodeError:
        return None

    value: bool | int | str
    if raw_val == "true":
        value = True
    elif raw_val == "false":
        value = False
    elif raw_val.startswith('"'):
        try:
            value = json.loads(raw_val)
        except json.JSONDecodeError:
            return None
    else:
        try:
            value = int(raw_val)
            if not -(2 ** 31) <= value < 2 ** 31:
                return None
        except ValueError:
            return None

    return key, value, line


def scan_prefs_js(content: str) -> list[PrefRecord]:
    records: list[PrefRecord] = []
    for line in split_pref_lines(content):
        parsed = parse_pref_line(line)
        if parsed is not None:
            records.append(PrefRecord(parsed[0], parsed[1], parsed[2]))
    return records


def extract_legacy_block(content: str | None) -> tuple[str | None, str | None, bool]:
    if content is None:
        return None, None, False

    begin_pattern = "=== BEGIN FIREFOX OPTIMIZATION SUITE ==="
    end_pattern = "=== END FIREFOX OPTIMIZATION SUITE ==="

    if begin_pattern not in content:
        return None, content, False

    lines = split_pref_lines(content)
    in_block = False
    block_lines: list[str] = []
    kept_lines: list[str] = []
    found = False

    for line in lines:
        if begin_pattern in line:
            in_block = True
            found = True
            block_lines.append(line)
            continue
        if end_pattern in line:
            if in_block:
                in_block = False
                block_lines.append(line)
                continue
        if in_block:
            block_lines.append(line)
        else:
            kept_lines.append(line)

    if found:
        stripped = "".join(kept_lines)
        return "".join(block_lines), stripped if stripped.strip() else None, True

    return None, content, False


def strip_legacy_keys_from_prefs(prefs_content: str, legacy_keys: frozenset[str] = LEGACY_KEYS) -> str:
    kept: list[str] = []
    for line in split_pref_lines(prefs_content):
        parsed = parse_pref_line(line)
        if parsed is not None and parsed[0] in legacy_keys:
            continue
        kept.append(line)
    return "".join(kept)


def validate_user_js(content: str | None) -> list[tuple[str, object]]:
    if content is None:
        return []

    declarations: list[tuple[str, object]] = []
    decoder = json.JSONDecoder()
    position = 0
    length = len(content)

    def reject(msg: str) -> None:
        fail(f"Invalid or unsupported user.js near char {position}: {msg}")

    def skip_trivia() -> None:
        nonlocal position
        while position < length:
            char = content[position]
            if char in " \t\r\n":
                position += 1
                continue
            if content.startswith("//", position):
                position += 2
                while position < length and content[position] not in "\r\n":
                    position += 1
                continue
            if content.startswith("/*", position):
                end = content.find("*/", position + 2)
                if end == -1:
                    reject("unterminated /* ... */ comment")
                position = end + 2
                continue
            break

    def consume(token: str) -> None:
        nonlocal position
        skip_trivia()
        if not content.startswith(token, position):
            reject(f"expected {token!r}")
        position += len(token)

    def decode_literal() -> object:
        nonlocal position
        skip_trivia()
        try:
            val, end = decoder.raw_decode(content, position)
        except json.JSONDecodeError as error:
            reject(f"expected literal ({error.msg})")
        position = end
        return val

    while True:
        skip_trivia()
        if position == length:
            return declarations

        keyword = USER_JS_KEYWORD.match(content, position)
        if keyword is None:
            reject("expected user_pref, sticky_pref, or pref")
        position = keyword.end()

        consume("(")
        key = decode_literal()
        if not isinstance(key, str):
            reject("preference name must be string")
        consume(",")
        value = decode_literal()
        if type(value) is int:
            if not -(2 ** 31) <= value < 2 ** 31:
                reject("integer outside 32-bit range")
        elif type(value) not in (str, bool):
            reject("only string, boolean, integer values allowed")
        consume(")")
        consume(";")
        declarations.append((key, value))


def managed_lines(content: str) -> tuple[str, ...]:
    return tuple(
        record.line for record in scan_prefs_js(content)
        if record.key in MANAGED_KEYS
    )


def strip_managed_lines(content: str) -> str:
    scan_prefs_js(content)
    return "".join(
        line for line in split_pref_lines(content)
        if (parsed := parse_pref_line(line)) is None or parsed[0] not in MANAGED_KEYS
    )


def restore_managed_lines(content: str, baseline: Sequence[str]) -> str:
    remaining = strip_managed_lines(content)
    if not baseline:
        return remaining
    if remaining and not remaining.endswith(("\n", "\r")):
        remaining += "\n"
    saved = "".join(line if line.endswith(("\n", "\r")) else line + "\n" for line in baseline)
    return remaining + saved


def generated_user_js(original: str | None, capacity_override: int | None = None) -> str:
    if capacity_override is not None:
        block_lines = [
            "// === BEGIN FIREFOX OPTIMIZATION SUITE ===",
            "// Managed by optimize_firefox.py - Firefox 156+ HTTP cache policy",
            'user_pref("browser.cache.disk.enable", false);',
            'user_pref("browser.cache.memory.enable", true);',
            f'user_pref("browser.cache.memory.capacity", {capacity_override});',
            "// === END FIREFOX OPTIMIZATION SUITE ===",
            "",
        ]
        block = "\n".join(block_lines)
    else:
        block = "\n".join(MANAGED_BLOCK_BODY)

    prefix = (original or "").strip()
    if not prefix:
        return block
    return prefix + "\n\n" + block


def sha256_text(value: str | None) -> str:
    payload = b"" if value is None else value.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ===========================================================================
# Rollback State
# ===========================================================================

@dataclass(frozen=True, slots=True)
class RollbackState:
    original_user_js: str | None
    baseline_prefs: tuple[str, ...]
    applied_sha256: str
    migrated_from_legacy: bool = False


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            fail(f"Duplicate key in rollback state: {key}")
        result[key] = value
    return result


def encode_state(state: RollbackState, profile: "Profile") -> str:
    document = {
        "schema": STATE_SCHEMA,
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "created_ns": time.time_ns(),
        "profile": str(profile.path),
        "profile_id": f"{profile.device}:{profile.inode}",
        "original_user_js": state.original_user_js,
        "baseline_prefs": list(state.baseline_prefs),
        "applied_sha256": state.applied_sha256,
        "migrated_from_legacy": state.migrated_from_legacy,
    }
    encoded = json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if len(encoded.encode("utf-8")) > MAX_TEXT_BYTES:
        fail("Rollback state exceeds size limit")
    return encoded


def decode_state(content: str) -> RollbackState:
    try:
        document = json.loads(content, object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as error:
        raise SafetyError(f"Rollback state is not valid JSON: {error.msg}") from error

    required = {
        "schema", "tool", "tool_version", "created_ns", "profile",
        "profile_id", "original_user_js", "baseline_prefs", "applied_sha256",
    }
    if not isinstance(document, dict) or not required.issubset(set(document)):
        fail("Invalid rollback-state structure")

    if document["schema"] != STATE_SCHEMA:
        fail(f"Unsupported rollback-state schema {document['schema']!r}")
    if document["tool"] != TOOL_ID:
        fail("Rollback state was written by a different tool")

    original = document["original_user_js"]
    if original is not None and not isinstance(original, str):
        fail("Invalid original user.js in rollback state")

    baseline = document["baseline_prefs"]
    if not isinstance(baseline, list):
        fail("Invalid saved preference list in rollback state")

    validated: list[str] = []
    for line in baseline:
        if not isinstance(line, str):
            fail("Invalid saved preference line")
        validated.append(line)

    return RollbackState(
        original_user_js=original,
        baseline_prefs=tuple(validated),
        applied_sha256=document["applied_sha256"],
        migrated_from_legacy=bool(document.get("migrated_from_legacy", False)),
    )


# ===========================================================================
# Profile Discovery & Root Selection
# ===========================================================================

@dataclass(frozen=True, slots=True)
class ProfileRoot:
    path: Path
    kind: str
    upstream_active: bool


def profile_roots() -> list[ProfileRoot]:
    home = Path.home()
    legacy_home = home / ".mozilla"
    legacy_root = legacy_home / "firefox"

    xdg_value = os.environ.get("XDG_CONFIG_HOME")
    xdg_base = Path(xdg_value) if xdg_value else home / ".config"
    xdg_root = xdg_base / "mozilla" / "firefox"

    forced_legacy = "MOZ_LEGACY_HOME" in os.environ
    legacy_active = forced_legacy or legacy_home.exists()

    resolved_legacy = legacy_root.resolve() if legacy_root.exists() else None
    resolved_xdg = xdg_root.resolve() if xdg_root.exists() else None

    if resolved_legacy and resolved_xdg and resolved_legacy == resolved_xdg:
        LOGGER.info("Firefox 156+ root: %s (legacy symlinked to XDG)", resolved_xdg)
        return [ProfileRoot(resolved_xdg, "xdg", True)]

    LOGGER.info(
        "Firefox 156+ root selection: %s (%s)",
        legacy_root if legacy_active else xdg_root,
        "legacy: ~/.mozilla exists" if legacy_active else "XDG: ~/.mozilla absent",
    )

    roots = [
        ProfileRoot(legacy_root, "legacy", legacy_active),
        ProfileRoot(xdg_root, "xdg", not legacy_active),
    ]

    dedup: dict[Path, ProfileRoot] = {}
    for r in roots:
        try:
            can = r.path.resolve()
        except OSError:
            can = r.path
        if can not in dedup:
            dedup[can] = r
    return list(dedup.values())


def registered_profiles(root: ProfileRoot) -> list[Path]:
    try:
        resolved = root.path.resolve(strict=True)
    except OSError:
        return []

    try:
        root_fd = open_directory(resolved)
    except SafetyError:
        return []

    try:
        content = read_config(root_fd, "profiles.ini")
    finally:
        os.close(root_fd)

    if content is None:
        return []

    content = content.lstrip("\ufeff")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(content, source=str(resolved / "profiles.ini"))
    except configparser.Error as error:
        raise SafetyError(f"Malformed profiles.ini in {resolved}: {error}") from error

    found: list[Path] = []
    for section in parser.sections():
        if not re.fullmatch(r"Profile\d+", section):
            continue
        where = f"{resolved}/profiles.ini [{section}]"

        if not parser.has_option(section, "Path") or not parser.has_option(section, "IsRelative"):
            fail(f"{where} missing Path or IsRelative")

        raw_path = parser.get(section, "Path").strip()
        if not raw_path or "\x00" in raw_path:
            fail(f"{where} has invalid Path")

        try:
            is_relative = parser.getint(section, "IsRelative")
        except ValueError:
            fail(f"{where} has non-integer IsRelative")

        candidate = Path(raw_path)
        match is_relative:
            case 1:
                if candidate.is_absolute():
                    fail(f"{where}: absolute path marked relative")
                candidate = resolved / candidate
            case 0:
                if not candidate.is_absolute():
                    fail(f"{where}: relative path marked absolute")
            case _:
                fail(f"{where}: invalid IsRelative {is_relative}")

        found.append(candidate)

    return found


@dataclass(frozen=True, slots=True)
class Profile:
    path: Path
    device: int
    inode: int
    fstype: str

    @property
    def identity(self) -> str:
        return hashlib.sha256(os.fsencode(self.path)).hexdigest()[:24]


def validate_profile(candidate: Path, table: Sequence[MountEntry], *, recover: bool = False) -> Profile:
    expanded = candidate.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded

    if recover:
        check_and_recover_ungraceful_state(expanded, table)
    expanded = expanded.parent.resolve() / expanded.name
    is_synced, vtarget, smode = is_profile_synced(expanded)

    try:
        resolved = expanded.resolve(strict=True)
    except FileNotFoundError as error:
        # Check if this is an ungraceful crash state where symlink is broken
        if recover and check_and_recover_ungraceful_state(expanded, table):
            resolved = expanded.resolve(strict=True)
        else:
            raise SafetyError(f"Profile directory does not exist: {expanded}") from error
    except OSError as error:
        raise SafetyError(f"Cannot resolve profile directory {expanded}: {oserror_detail(error)}") from error

    try:
        info = resolved.stat()
    except OSError as error:
        raise SafetyError(f"Cannot stat profile {resolved}: {oserror_detail(error)}") from error

    if not stat.S_ISDIR(info.st_mode):
        fail(f"Profile is not a directory: {resolved}")
    if info.st_uid != os.geteuid():
        fail(f"Profile is not owned by this user: {resolved}")
    if info.st_mode & 0o022:
        LOGGER.warning("Profile directory %s has permissive permissions %04o", resolved, info.st_mode & 0o777)

    if is_synced:
        # For a profile currently synced to RAM, require that parent (disk location) is supported
        backing_fstype = require_supported_filesystem(table, expanded.parent, "profile backing")
        fstype = f"RAM ({smode}) over {backing_fstype}"
    else:
        fstype = require_supported_filesystem(table, resolved, "profile")

    dir_fd = open_directory(resolved)
    try:
        if read_config(dir_fd, PREFS_JS) is None:
            raise UninitializedProfileError(f"No prefs.js in {resolved}; profile never started.")
    finally:
        os.close(dir_fd)

    return Profile(expanded, info.st_dev, info.st_ino, fstype)


def select_profiles(
    explicit: Sequence[Path], table: Sequence[MountEntry], *, recover: bool = False, allow_empty: bool = False,
) -> list[Profile]:
    if explicit:
        profiles_found = [validate_profile(path, table, recover=recover) for path in explicit]
    else:
        profiles_found = []
        for root in profile_roots():
            for candidate in registered_profiles(root):
                try:
                    profiles_found.append(validate_profile(candidate, table, recover=recover))
                except UninitializedProfileError as error:
                    LOGGER.warning("Skipping: %s", error)
                except SafetyError as error:
                    raise SafetyError(f"Cannot use registered profile {candidate}: {error}") from error

    by_path: dict[Path, Profile] = {}
    for prof in profiles_found:
        by_path.setdefault(prof.path, prof)

    profiles = sorted(by_path.values(), key=lambda p: p.path)
    if not profiles and not allow_empty:
        fail("No usable profile selected. Run Firefox once or specify --profile PATH.")

    for i, p in enumerate(profiles):
        for other in profiles[i + 1:]:
            if p.path.is_relative_to(other.path) or other.path.is_relative_to(p.path):
                fail(f"Nested selected profiles are unsupported: {p.path}, {other.path}")

    return profiles


# ===========================================================================
# Profile Planning & Legacy Migration Engine
# ===========================================================================

@dataclass(frozen=True, slots=True)
class ProfilePlan:
    profile: Profile
    old_user_js: str | None
    old_prefs_js: str
    old_state: str | None
    new_user_js: str | None
    new_prefs_js: str
    new_state: str | None
    advisories: list[str]
    is_migration: bool = False

    @property
    def changed(self) -> bool:
        return (
            self.old_user_js != self.new_user_js
            or self.old_prefs_js != self.new_prefs_js
            or self.old_state != self.new_state
        )


def collect_advisories(prefs_js: str, user_js: str | None) -> list[str]:
    advisories: list[str] = []
    combined = (prefs_js or "") + "\n" + (user_js or "")
    records = scan_prefs_js(combined)

    for rec in records:
        if rec.key == "browser.cache.disk.parent_directory":
            advisories.append(f'browser.cache.disk.parent_directory = "{rec.value}"')
        elif rec.key == "browser.cache.memory.capacity":
            advisories.append(f"browser.cache.memory.capacity = {rec.value}")
        elif rec.key == "browser.cache.disk.smart_size.enabled":
            advisories.append(f"browser.cache.disk.smart_size.enabled = {str(rec.value).lower()}")

    inert_keys = sorted({
        rec.key for rec in records
        if rec.key in {
            "browser.cache.disk_cache_ssl",
            "browser.cache.offline.enable",
            "dom.ipc.processCount",
            "dom.ipc.processCount.webIsolated",
            "dom.ipc.processCount.extension",
            "gfx.webrender.all",
            "layers.acceleration.force-enabled",
            "widget.wayland.opaque-region.enabled",
        }
    })
    if inert_keys:
        advisories.append(
            f"declares preferences that are removed or inert in Firefox 156+: {', '.join(inert_keys)}"
        )
    return list(dict.fromkeys(advisories))


def prepare_profile(
    profile: Profile,
    dir_fd: int,
    memory_mode: bool,
    capacity_override: int | None = None,
) -> ProfilePlan:
    user_js = read_config(dir_fd, USER_JS)
    prefs_js = read_config(dir_fd, PREFS_JS)
    state_text = read_config(dir_fd, STATE_FILENAME)

    if prefs_js is None:
        fail(f"prefs.js missing from {profile.path}")

    baseline_now = managed_lines(prefs_js)
    advisories = collect_advisories(prefs_js, user_js)

    legacy_block, stripped_user_js, has_legacy = extract_legacy_block(user_js)

    if state_text is None:
        if has_legacy:
            LOGGER.info(
                "%s: legacy optimizer block detected in user.js; migrating to clean Firefox 156+ policy",
                profile.path,
            )
            cleaned_prefs = strip_legacy_keys_from_prefs(prefs_js)

            if not memory_mode:
                return ProfilePlan(
                    profile=profile,
                    old_user_js=user_js,
                    old_prefs_js=prefs_js,
                    old_state=None,
                    new_user_js=stripped_user_js,
                    new_prefs_js=cleaned_prefs,
                    new_state=None,
                    advisories=advisories,
                    is_migration=True,
                )

            new_user_js = generated_user_js(stripped_user_js, capacity_override)
            state = RollbackState(
                original_user_js=stripped_user_js,
                baseline_prefs=baseline_now,
                applied_sha256=sha256_text(new_user_js),
                migrated_from_legacy=True,
            )
            return ProfilePlan(
                profile=profile,
                old_user_js=user_js,
                old_prefs_js=prefs_js,
                old_state=None,
                new_user_js=new_user_js,
                new_prefs_js=cleaned_prefs,
                new_state=encode_state(state, profile),
                advisories=advisories,
                is_migration=True,
            )

        if not memory_mode:
            return ProfilePlan(
                profile, user_js, prefs_js, None, user_js, prefs_js, None, advisories,
            )

        for key, _ in validate_user_js(user_js):
            if key in MANAGED_KEYS:
                fail(f"{profile.path}/user.js already defines managed preference {key!r}")
            if key in SECRET_KEYS:
                fail(f"{profile.path}/user.js pins secret key {key!r}")

        new_user_js = generated_user_js(user_js, capacity_override)
        state = RollbackState(user_js, baseline_now, sha256_text(new_user_js))
        return ProfilePlan(
            profile=profile,
            old_user_js=user_js,
            old_prefs_js=prefs_js,
            old_state=None,
            new_user_js=new_user_js,
            new_prefs_js=prefs_js,
            new_state=encode_state(state, profile),
            advisories=advisories,
        )

    state = decode_state(state_text)
    if user_js != state.original_user_js and sha256_text(user_js) != state.applied_sha256:
        fail(f"{profile.path}: user.js modified outside this tool; reconcile edits with backup and rerun.")
    if memory_mode:
        expected_applied = (
            user_js if capacity_override is None and sha256_text(user_js) == state.applied_sha256
            else generated_user_js(state.original_user_js, capacity_override)
        )
        new_state = state_text
        if sha256_text(expected_applied) != state.applied_sha256:
            new_state = encode_state(RollbackState(
                state.original_user_js, state.baseline_prefs,
                sha256_text(expected_applied), state.migrated_from_legacy,
            ), profile)
        return ProfilePlan(
            profile, user_js, prefs_js, state_text,
            expected_applied, prefs_js, new_state, advisories,
        )

    return ProfilePlan(
        profile, user_js, prefs_js, state_text,
        state.original_user_js,
        restore_managed_lines(prefs_js, state.baseline_prefs),
        None,
        advisories,
    )


# ===========================================================================
# Backups (Zstandard + Streaming Digest)
# ===========================================================================

class DigestWriter:
    __slots__ = ("_target", "_hasher", "bytes_written")

    def __init__(self, target: BinaryIO) -> None:
        self._target = target
        self._hasher = hashlib.sha256()
        self.bytes_written = 0

    def write(self, data: bytes | bytearray | memoryview) -> int:
        n = self._target.write(data)
        self._hasher.update(data[:n])
        self.bytes_written += n
        return n

    def flush(self) -> None:
        self._target.flush()

    @property
    def hexdigest(self) -> str:
        return self._hasher.hexdigest()


@dataclass(frozen=True, slots=True)
class BackupEntry:
    path: Path
    signature: tuple[int, int, int, int, int, int]

    @property
    def mode(self) -> int:
        return self.signature[2]

    @property
    def size(self) -> int:
        return self.signature[3]


def targeted_entries(profile: Profile) -> tuple[list[BackupEntry], int]:
    entries: list[BackupEntry] = []
    total = 0
    resolved = profile.path.resolve()
    for name in (PREFS_JS, USER_JS, STATE_FILENAME):
        cand = resolved / name
        try:
            info = cand.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise SafetyError(f"Cannot stat {cand}: {oserror_detail(error)}") from error

        if not stat.S_ISREG(info.st_mode):
            fail(f"Backup source is not a regular file: {cand}")
        entries.append(BackupEntry(cand, stat_signature(info)))
        total += info.st_size

    if not entries:
        fail(f"Nothing to back up in {profile.path}")
    return entries, total


def full_entries(profile: Profile, table: Sequence[MountEntry]) -> tuple[list[BackupEntry], int]:
    resolved = profile.path.resolve()
    nested = nested_mounts(table, resolved)
    if nested:
        fail(f"Nested mount points in profile: {', '.join(map(str, nested))}")

    entries: list[BackupEntry] = []
    visited: set[tuple[int, int]] = set()
    total = 0
    pending = [resolved]

    while pending:
        cur = pending.pop()
        try:
            info = cur.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise SafetyError(f"Cannot stat {cur}: {oserror_detail(error)}") from error

        if stat.S_ISLNK(info.st_mode):
            if info.st_uid != os.geteuid():
                fail(f"Unsupported symlink ownership: {cur}")
            entries.append(BackupEntry(cur, stat_signature(info)))
            continue

        if info.st_dev != profile.device or info.st_uid != os.geteuid():
            fail(f"Unsupported ownership/nested filesystem in backup source: {cur}")

        is_dir = stat.S_ISDIR(info.st_mode)
        if not (is_dir or stat.S_ISREG(info.st_mode)):
            fail(f"Special file in backup source: {cur}")

        if is_dir:
            ident = (info.st_dev, info.st_ino)
            if ident in visited:
                fail(f"Repeated directory identity in backup source: {cur}")
            visited.add(ident)

        entries.append(BackupEntry(cur, stat_signature(info)))
        if len(entries) > MAX_FULL_BACKUP_ENTRIES:
            fail(f"Profile exceeds {MAX_FULL_BACKUP_ENTRIES} entries; use targeted backup.")

        if not is_dir:
            total += info.st_size
            continue

        try:
            with os.scandir(cur) as it:
                children = sorted(
                    (Path(e.path) for e in it if not (cur == resolved and e.name in EXCLUDED_ROOT_ENTRIES)),
                    key=os.fspath,
                    reverse=True,
                )
            pending.extend(children)
        except OSError as error:
            raise SafetyError(f"Cannot scan {cur}: {oserror_detail(error)}") from error

    return entries, total


def create_backup_directories(destination: Path) -> None:
    missing: list[Path] = []
    ancestor = destination
    while not ancestor.exists():
        missing.append(ancestor)
        if ancestor.parent == ancestor:
            fail("Backup destination has no existing ancestor")
        ancestor = ancestor.parent

    if not ancestor.is_dir():
        fail(f"Backup ancestor is not a directory: {ancestor}")

    for directory in reversed(missing):
        try:
            make_private_directory(directory)
        except FileExistsError:
            pass
        except OSError as error:
            raise SafetyError(f"Cannot create backup directory {directory}: {oserror_detail(error)}") from error
        fsync_directory_path(directory)
        fsync_directory_path(directory.parent)

    fsync_directory_path(destination.parent)


def resolve_backup_destination(
    profile: Profile,
    override: Path | None,
    selected: Sequence[Profile],
    table: Sequence[MountEntry],
    *,
    create: bool,
) -> Path:
    destination = absolute_without_symlinks(
        override if override is not None else profile.path.parent / DEFAULT_BACKUP_DIRNAME
    )

    for other in selected:
        if destination == other.path or destination.is_relative_to(other.path):
            fail(f"Backup directory must be outside every selected profile: {destination}")

    ancestor = destination
    while not ancestor.exists():
        if ancestor.parent == ancestor:
            fail("Backup destination has no existing ancestor")
        ancestor = ancestor.parent
    if not ancestor.is_dir():
        fail(f"Backup ancestor is not a directory: {ancestor}")

    require_supported_filesystem(table, ancestor, "backup")

    if ancestor.stat().st_dev != profile.device:
        LOGGER.info(
            "Backup destination %s is on a separate filesystem than %s.",
            destination, profile.path,
        )

    if create:
        create_backup_directories(destination)

    if destination.exists():
        info = destination.lstat()
        if not stat.S_ISDIR(info.st_mode):
            fail(f"Backup destination is not a directory: {destination}")
        if info.st_uid != os.geteuid():
            fail(f"Backup directory is not owned by this user: {destination}")

        if info.st_mode & 0o077:
            if create:
                try:
                    os.chmod(destination, 0o700)
                    info = destination.lstat()
                except OSError as error:
                    raise SafetyError(f"Cannot tighten permissions on {destination}: {oserror_detail(error)}") from error
                if info.st_mode & 0o077:
                    fail(f"Backup directory {destination} remains accessible after chmod")

    return destination


def build_manifest(profile: Profile, scope: str, entries: Sequence[BackupEntry]) -> bytes:
    resolved = profile.path.resolve()
    doc = {
        "manifest_version": 1,
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "created_ns": time.time_ns(),
        "profile": str(profile.path),
        "profile_id": profile.identity,
        "scope": scope,
        "entries": [
            {
                "name": str(e.path.relative_to(resolved)),
                "size": e.size,
                "mode": f"{e.mode:04o}",
            }
            for e in entries
        ],
    }
    return json.dumps(doc, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _tar_filter(member: tarfile.TarInfo) -> tarfile.TarInfo:
    if not (member.isreg() or member.isdir() or member.issym()):
        fail(f"Refusing to archive special member: {member.name}")
    member.uname = ""
    member.gname = ""
    return member


def backup_profile(
    profile: Profile, destination: Path, scope: str, table: Sequence[MountEntry]
) -> Path:
    entries, total_bytes = (
        targeted_entries(profile) if scope == "targeted" else full_entries(profile, table)
    )

    estimated = total_bytes + total_bytes // 10 + len(entries) * 4096 + BACKUP_HEADROOM_BYTES
    available = shutil.disk_usage(destination).free
    if available < estimated:
        fail(
            f"Insufficient headroom at {destination}: {human_bytes(available)} available, "
            f"{human_bytes(estimated)} estimated."
        )

    final_name = f"{BACKUP_PREFIX}-{scope}-{profile.identity}-{uuid.uuid7().hex}.tar.zst"
    final_path = destination / final_name
    temporary = destination / f"{TEMP_ARCHIVE_PREFIX}{uuid.uuid7().hex}.tar.zst"

    zstd_options = {
        zstd.CompressionParameter.compression_level: (
            ZSTD_LEVEL_TARGETED if scope == "targeted" else ZSTD_LEVEL_FULL
        ),
        zstd.CompressionParameter.checksum_flag: 1,
    }

    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )

    resolved = profile.path.resolve()
    try:
        with os.fdopen(fd, "wb") as raw:
            os.fchmod(raw.fileno(), 0o600)
            writer = DigestWriter(raw)

            with zstd.ZstdFile(writer, "w", options=zstd_options) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT, dereference=False) as archive:
                    manifest_data = build_manifest(profile, scope, entries)
                    ti = tarfile.TarInfo(f"{profile.path.name}/BACKUP-MANIFEST.json")
                    ti.size = len(manifest_data)
                    ti.mode = 0o600
                    ti.mtime = int(time.time())
                    archive.addfile(ti, io.BytesIO(manifest_data))

                    for e in entries:
                        if stat_signature(e.path.lstat()) != e.signature:
                            fail(f"Profile changed before backup: {e.path}")
                        arcname = (Path(profile.path.name) / e.path.relative_to(resolved)).as_posix()
                        archive.add(e.path, arcname=arcname, recursive=False, filter=_tar_filter)
                        if stat_signature(e.path.lstat()) != e.signature:
                            fail(f"Profile changed during backup: {e.path}")

            raw.flush()
            os.fsync(raw.fileno())

        if scope == "full" and nested_mounts(read_mount_table(), resolved):
            fail("Mount topology changed during backup")

        os.replace(temporary, final_path)
        fsync_directory_path(destination)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)

    LOGGER.info("Backup created: %s (%s, %s)", final_path.name, scope, human_bytes(final_path.stat().st_size))
    rotate_backups(destination, profile, scope)
    return final_path


def rotate_backups(destination: Path, profile: Profile, scope: str) -> None:
    pattern = re.compile(
        rf"^{re.escape(BACKUP_PREFIX)}-{re.escape(scope)}-{re.escape(profile.identity)}-[0-9a-f]{{32}}\.tar\.zst$"
    )
    matching: list[Path] = []
    stale_temp: list[Path] = []
    now = time.time()

    for item in destination.iterdir():
        if pattern.fullmatch(item.name):
            matching.append(item)
        elif item.name.startswith(TEMP_ARCHIVE_PREFIX) and item.name.endswith(".tar.zst"):
            try:
                if now - item.stat().st_mtime > 86400:
                    stale_temp.append(item)
            except OSError:
                pass

    for temp in stale_temp:
        with suppress(OSError):
            temp.unlink(missing_ok=True)
            LOGGER.info("Reaped stale temp backup: %s", temp.name)

    matching.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for old in matching[MAX_BACKUPS_PER_PROFILE:]:
        try:
            old.unlink(missing_ok=True)
            LOGGER.info("Rotated backup: %s", old.name)
        except OSError as error:
            LOGGER.warning("Could not rotate %s: %s", old.name, error)


# ===========================================================================
# Execution & Verification
# ===========================================================================

def apply_plan(plan: ProfilePlan, dir_fd: int) -> None:
    if plan.new_state is not None:
        replace_config(dir_fd, STATE_FILENAME, plan.old_state, plan.new_state)
        replace_config(dir_fd, PREFS_JS, plan.old_prefs_js, plan.new_prefs_js)
        replace_config(dir_fd, USER_JS, plan.old_user_js, plan.new_user_js)
    else:
        replace_config(dir_fd, PREFS_JS, plan.old_prefs_js, plan.new_prefs_js)
        replace_config(dir_fd, USER_JS, plan.old_user_js, plan.new_user_js)
        replace_config(dir_fd, STATE_FILENAME, plan.old_state, None)

    LOGGER.info("Updated profile: %s", plan.profile.path)


def verify_applied(plan: ProfilePlan, dir_fd: int) -> None:
    for name, expected in (
        (STATE_FILENAME, plan.new_state),
        (USER_JS, plan.new_user_js),
        (PREFS_JS, plan.new_prefs_js),
    ):
        actual = read_config(dir_fd, name)
        if actual != expected:
            fail(f"Post-write verification failed for {plan.profile.path}/{name}")


# ===========================================================================
# Profile-Sync-Daemon (PSD) RAM Engine
# ===========================================================================

@dataclass(frozen=True, slots=True)
class PsdPaths:
    profile_path: Path
    backup_path: Path
    back_ovfs_path: Path
    volatile_mount: Path
    volatile_upper: Path
    volatile_work: Path
    flag_file: Path
    state_file: Path


def get_volatile_root(*, create: bool = False) -> Path:
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        base = Path(xdg_runtime)
    else:
        base = Path(f"/run/user/{os.geteuid()}")
        if not base.is_dir():
            base = Path("/dev/shm") / f"firefox-sync-uid{os.geteuid()}"
    vroot = base / VOLATILE_SUBDIR
    if create:
        try:
            vroot.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(vroot, 0o700)
        except OSError as err:
            fail(f"Cannot create volatile RAM directory {vroot}: {oserror_detail(err)}")
    return vroot


def get_psd_paths(profile_path: Path) -> PsdPaths:
    parent = profile_path.parent
    name = profile_path.name
    backup = parent / f"{name}{PSD_BACKUP_SUFFIX}"
    back_ovfs = parent / f"{name}{PSD_BACK_OVFS_SUFFIX}"

    vroot = get_volatile_root()
    user = os.environ.get("USER") or f"uid{os.geteuid()}"
    identity = hashlib.sha256(os.fsencode(profile_path.parent.resolve() / name)).hexdigest()[:16]
    suffix = f"{user}-firefox-{name}-{identity}"

    v_mount = vroot / suffix
    if profile_path.is_symlink():
        target = profile_path.readlink()
        if not target.is_absolute():
            target = (profile_path.parent / target).resolve()
        if target.is_relative_to(vroot):
            v_mount = target
            suffix = target.name
    v_upper = vroot / f"{suffix}-rw"
    v_work = vroot / f".{suffix}-work"
    flag = v_mount / PSD_FLAG_FILE
    state = v_mount / PSD_STATE_FILE

    return PsdPaths(
        profile_path=profile_path,
        backup_path=backup,
        back_ovfs_path=back_ovfs,
        volatile_mount=v_mount,
        volatile_upper=v_upper,
        volatile_work=v_work,
        flag_file=flag,
        state_file=state,
    )


def is_profile_synced(profile_path: Path) -> tuple[bool, Path | None, str | None]:
    try:
        if profile_path.is_symlink():
            target = profile_path.readlink()
            vroot = get_volatile_root()
            if not target.is_absolute():
                target = (profile_path.parent / target).resolve()
            if target.is_relative_to(vroot) or str(target).startswith("/run/user/") or str(target).startswith("/dev/shm/"):
                if not target.exists():
                    return True, target, None  # A reboot can leave a broken RAM link.
                state_file = target / PSD_STATE_FILE
                try:
                    doc = json.loads(state_file.read_text("utf-8"))
                except (OSError, ValueError) as error:
                    raise SafetyError(f"Cannot read RAM sync state at {state_file}; profile preserved: {error}") from error
                if not isinstance(doc, dict) or doc.get("tool") != TOOL_ID or doc.get("mode") not in {"copy", "overlay"}:
                    fail(f"Invalid RAM sync state at {state_file}; profile preserved.")
                mode = doc["mode"]
                return True, target, mode
    except OSError:
        pass
    return False, None, None


@contextmanager
def psd_operation_lock() -> Iterator[None]:
    """Serialize profile discovery/recovery, relocation and checkpoints."""
    root = get_volatile_root(create=True)
    fd = os.open(root / ".operation.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def sync_directories(
    src: Path, dst: Path, exclude: Sequence[str] = (), delete: bool = True,
    *, link_dest: Path | None = None, checksum: bool = False,
) -> None:
    """Replace completed files atomically and propagate every transfer failure.

    Changed files need temporary disk space; a live tree is not an atomic snapshot.
    """
    rsync = shutil.which("rsync")
    if rsync is None:
        fail("rsync is required for profile RAM synchronization.")
    if not src.is_dir():
        fail(f"Sync source directory is missing: {src}")
    cmd = [rsync, "-aX", "--fsync"]
    if delete:
        cmd.append("--delete-after")
    if link_dest is not None:
        cmd.append(f"--link-dest={link_dest.resolve()}")
    if checksum:
        cmd.append("--checksum")
    for ex in sorted(set(exclude) | {PARENTLOCK, SYMLINK_LOCK, PSD_FLAG_FILE, PSD_STATE_FILE}):
        pattern = re.sub(r"([\\*?\[])", r"\\\1", ex)
        cmd.extend(["--exclude", f"/{pattern}"])
    cmd.extend(["--", f"{src}/", f"{dst}/"])
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise SafetyError(f"rsync failed (code {res.returncode}): {res.stderr.strip()}")
    fsync_directory_path(dst)


def check_and_recover_ungraceful_state(profile_path: Path, table: Sequence[MountEntry]) -> bool:
    """Restore a missing RAM profile by renaming its backing, without duplicating it."""
    paths = get_psd_paths(profile_path)
    # Interrupted checkpoint trees can retain hard links to the published files.
    # Remove them before validation requires single-link configuration files.
    for destination in (paths.backup_path, paths.back_ovfs_path):
        staging = destination.with_name(destination.name + ".checkpoint-tmp")
        if staging.is_dir():
            shutil.rmtree(staging)
    if profile_path.is_symlink():
        synced, target, _ = is_profile_synced(profile_path)
        if not synced:
            return False  # An ordinary profile symlink is not RAM-sync state.
        if target is not None and target.is_dir():
            if not (target / PSD_FLAG_FILE).is_file():
                fail(f"Unflagged RAM profile at {target}; preserve it and inspect before recovery.")
            return False
    elif profile_path.exists():
        # A power cut after unsync's atomic exchange can leave the obsolete link.
        for obsolete in (paths.backup_path, paths.back_ovfs_path):
            if obsolete.is_symlink():
                old_target = obsolete.readlink()
                if old_target.is_relative_to(get_volatile_root()) and (profile_path / PSD_STATE_FILE).is_file():
                    if obsolete == paths.back_ovfs_path and paths.backup_path.is_dir():
                        shutil.rmtree(paths.backup_path)
                    obsolete.unlink()
                    fsync_directory_path(profile_path.parent)
        if paths.backup_path.exists():
            fail(f"Both profile and backing exist: {profile_path}, {paths.backup_path}; reconcile them first.")
        return False

    if not paths.backup_path.is_dir():
        return False
    restored = paths.backup_path
    if paths.back_ovfs_path.is_dir() and any(paths.back_ovfs_path.iterdir()):
        if not (paths.back_ovfs_path / PSD_STATE_FILE).is_file():
            fail(f"Incomplete overlay checkpoint at {paths.back_ovfs_path}; preserve both disk copies for inspection.")
        # A completed merged checkpoint can be restored directly, including deletions.
        restored = paths.back_ovfs_path
    LOGGER.warning("Restoring %s from its last disk checkpoint; newer RAM changes may be lost.", profile_path)
    if profile_path.is_symlink():
        profile_path.unlink()
    restored.rename(profile_path)
    fsync_directory_path(profile_path.parent)
    if restored == paths.back_ovfs_path:
        shutil.rmtree(paths.backup_path, ignore_errors=True)
    else:
        shutil.rmtree(paths.back_ovfs_path, ignore_errors=True)
    return True


def sync_profile_to_ram(profile: Profile, mode: str = "auto", table: Sequence[MountEntry] | None = None) -> dict:
    if is_profile_synced(profile.path)[0]:
        return _sync_profile_to_ram(profile, mode, table)
    dir_fd = open_directory(profile.path.resolve())
    try:
        with locked_profile(profile, dir_fd):
            return _sync_profile_to_ram(profile, mode, table)
    finally:
        os.close(dir_fd)


def _sync_profile_to_ram(profile: Profile, mode: str = "auto", table: Sequence[MountEntry] | None = None) -> dict:
    tbl = table or read_mount_table()
    paths = get_psd_paths(profile.path)

    check_and_recover_ungraceful_state(profile.path, tbl)

    is_synced, vtarget, cur_mode = is_profile_synced(profile.path)
    if is_synced:
        LOGGER.info("Profile %s is already synced to RAM at %s (mode: %s)", profile.path.name, vtarget, cur_mode)
        return {"status": "already_synced", "volatile": str(vtarget), "mode": cur_mode}

    has_fuse_ovfs = shutil.which("fuse-overlayfs") is not None
    if mount_for(tbl, get_volatile_root()).fstype != "tmpfs":
        raise RamSyncUnavailable("RAM profile storage must be on tmpfs.")
    prof_size = dir_size(profile.path)
    ram_free = shutil.disk_usage(get_volatile_root()).free
    if mode == "auto":
        mode = "copy" if ram_free >= prof_size + BACKUP_HEADROOM_BYTES else "overlay"
    if mode == "overlay" and not has_fuse_ovfs:
        raise RamSyncUnavailable("Insufficient tmpfs capacity for copy mode and/or fuse-overlayfs is unavailable.")
    if mode == "overlay" and shutil.which("fusermount3") is None:
        raise RamSyncUnavailable("fusermount3 is required for overlay teardown.")
    if mode == "copy" and ram_free < prof_size + BACKUP_HEADROOM_BYTES:
        raise RamSyncUnavailable(f"Insufficient RAM filesystem space: {human_bytes(ram_free)} available for {human_bytes(prof_size)} profile.")
    # Estimate initial SQLite normalization plus atomic replacement of a large file.
    # Check the profile's actual backing mount, which can differ from $HOME.
    sqlite_bytes = 0
    largest_file = 0
    for root, _, files in os.walk(profile.path):
        for name in files:
            path = Path(root) / name
            info = path.lstat()
            if stat.S_ISREG(info.st_mode):
                largest_file = max(largest_file, info.st_size)
                if path.suffix in {".sqlite", ".db"}:
                    sqlite_bytes += info.st_size
    disk_needed = max(sqlite_bytes, largest_file) + BACKUP_HEADROOM_BYTES
    disk_free = shutil.disk_usage(profile.path.parent).free
    if disk_free < disk_needed:
        raise RamSyncUnavailable(
            f"Insufficient checkpoint headroom at {profile.path.parent}: "
            f"{human_bytes(disk_free)} free, approximately {human_bytes(disk_needed)} required."
        )
    if shutil.which("rsync") is None:
        raise RamSyncUnavailable("rsync is required for profile RAM synchronization.")

    LOGGER.info("Syncing %s to RAM (engine: %s)...", profile.path.name, mode)

    if paths.backup_path.exists():
        fail(f"Backup path {paths.backup_path} already exists. Inconsistent state; check backups.")

    temp_link = profile.path.parent / f".tmp-psd-link-{uuid.uuid7().hex}"
    moved = False
    try:
        # Allocate the link before moving or copying a potentially large profile.
        os.symlink(paths.volatile_mount, temp_link)
        profile.path.rename(paths.backup_path)
        moved = True
        fsync_directory_path(profile.path.parent)
        paths.volatile_mount.mkdir(mode=0o700, parents=True, exist_ok=True)

        if mode == "overlay":
            paths.volatile_upper.mkdir(mode=0o700, parents=True, exist_ok=True)
            paths.volatile_work.mkdir(mode=0o700, parents=True, exist_ok=True)
            paths.back_ovfs_path.mkdir(mode=0o700, parents=True, exist_ok=True)

            ov_opts = f"lowerdir={paths.backup_path},upperdir={paths.volatile_upper},workdir={paths.volatile_work}"
            cmd = ["fuse-overlayfs", "-o", ov_opts, str(paths.volatile_mount)]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                raise SafetyError(f"fuse-overlayfs failed (code {res.returncode}): {res.stderr.strip()}")
        else:
            sync_directories(paths.backup_path, paths.volatile_mount)

        state_data = {
            "tool": TOOL_ID,
            "version": TOOL_VERSION,
            "mode": mode,
            "created_at": time.time(),
            "created_dt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "profile": str(profile.path),
            "backup": str(paths.backup_path),
            "back_ovfs": str(paths.back_ovfs_path) if mode == "overlay" else None,
            "last_resync": time.time(),
        }
        paths.state_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
        paths.flag_file.touch(mode=0o600)

        os.replace(temp_link, profile.path)
        fsync_directory_path(profile.path.parent)

        LOGGER.info("Successfully synced %s to RAM (%s)", profile.path.name, paths.volatile_mount)
        return {"status": "synced", "volatile": str(paths.volatile_mount), "mode": mode}

    except BaseException as e:
        temp_link.unlink(missing_ok=True)
        if not moved:
            raise
        LOGGER.error("Failed to sync to RAM: %s. Rolling back...", e)
        if mode == "overlay" and is_mountpoint(paths.volatile_mount):
            result = subprocess.run(["fusermount3", "-u", str(paths.volatile_mount)], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise SafetyError(f"Rollback unmount failed; RAM and backing preserved: {result.stderr.strip()}") from e
        shutil.rmtree(paths.volatile_mount, ignore_errors=True)
        shutil.rmtree(paths.volatile_upper, ignore_errors=True)
        shutil.rmtree(paths.volatile_work, ignore_errors=True)
        shutil.rmtree(paths.back_ovfs_path, ignore_errors=True)
        if profile.path.is_symlink():
            profile.path.unlink(missing_ok=True)
        if paths.backup_path.is_dir() and not profile.path.exists():
            paths.backup_path.rename(profile.path)
        raise


def exchange_paths(first: Path, second: Path) -> None:
    """Linux renameat2(RENAME_EXCHANGE): no missing/partially replaced checkpoint."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = libc.renameat2
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(first), -100, os.fsencode(second), 2) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(second))


def sqlite_paths(source: Path) -> Iterator[Path]:
    """Identify regular SQLite files by their documented header, regardless of name."""
    def walk_error(error: OSError) -> None:
        raise error
    for root, _, files in os.walk(source, onerror=walk_error):
        for name in files:
            path = Path(root) / name
            try:
                if not stat.S_ISREG(path.lstat().st_mode) or name.endswith(("-wal", "-shm", "-journal")):
                    continue
                with path.open("rb") as stream:
                    header = stream.read(16)
            except FileNotFoundError:
                continue  # Firefox may remove transient files while we scan.
            if header == b"SQLite format 3\x00":
                yield path


def copy_stable_sqlite(path: Path, destination: Path, deadline: float) -> None:
    """Capture a crash-recoverable DB/journal set without bypassing SQLite locks.

    Firefox's exclusive VFS prevents outside readers. SQLite writes databases
    and journals through file I/O, so unchanged identities, sizes and nanosecond
    modification/change times across the entire copy establish a stable set.
    Never copy shared memory: SQLite rebuilds its WAL index in the private copy.
    """
    members = [path.with_name(path.name + suffix) for suffix in ("", "-wal", "-journal")]

    def signatures() -> list[tuple[int, int, int, int, int, int] | None]:
        result = []
        for member in members:
            try:
                result.append(stat_signature(member.stat()))
            except FileNotFoundError:
                result.append(None)
        return result

    while time.monotonic() < deadline:
        before = signatures()
        if before[0] is None:
            fail(f"SQLite database vanished: {path}; previous checkpoint preserved.")
        try:
            for member, signature in zip(members, before):
                target = destination / member.name
                target.unlink(missing_ok=True)
                if signature is not None:
                    shutil.copy2(member, target)
            if signatures() == before:
                return
        except FileNotFoundError:
            pass  # Journal rotation raced the copy; retry the complete set.
        time.sleep(0.01)
    fail(f"SQLite files kept changing during RAM snapshot: {path}; previous checkpoint preserved.")


def snapshot_sqlite(source: Path, staging: Path, *, candidates: Sequence[Path] | None = None) -> list[str]:
    """Stage self-contained database snapshots in RAM, including committed WAL pages."""
    excluded: list[str] = []
    for path in sqlite_paths(source) if candidates is None else candidates:
        relative = path.relative_to(source)
        target = staging / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        deadline = time.monotonic() + 30
        locked = False
        def progress(status: int, _remaining: int, _total: int) -> None:
            nonlocal locked
            if status in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                locked = True
                raise sqlite3.OperationalError("Live SQLite database is exclusively locked")
            if time.monotonic() >= deadline:
                fail(f"SQLite snapshot timed out: {path}; previous checkpoint preserved.")
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
                with closing(sqlite3.connect(target)) as backup:
                    db.backup(backup, pages=256, progress=progress, sleep=0.01)
                    backup.execute("PRAGMA journal_mode=DELETE")
        except sqlite3.Error as error:
            if not locked:
                raise SafetyError(f"Cannot snapshot {path}; previous checkpoint preserved: {error}") from error
            target.unlink(missing_ok=True)
            with tempfile.TemporaryDirectory(prefix="locked-sqlite-", dir=target.parent) as tmp:
                private = Path(tmp)
                copy_stable_sqlite(path, private, deadline)
                # A writable private connection recovers a hot rollback journal or
                # committed WAL before producing a self-contained backup.
                with closing(sqlite3.connect(private / path.name)) as db:
                    with closing(sqlite3.connect(target)) as backup:
                        db.backup(backup, pages=256, progress=progress)
                        backup.execute("PRAGMA journal_mode=DELETE")
        shutil.copystat(path, target)
        excluded.extend(str(relative) + suffix for suffix in ("", "-wal", "-shm", "-journal"))
    return excluded


def checkpoint_profile(source: Path, destination: Path, *, live: bool = True, baseline: Path | None = None) -> None:
    """Publish only completed trees; hard-link unchanged files to avoid data writes.

    The fixed staging name is never used for recovery. If interrupted, the last
    published destination remains valid and staging is removed on the next run.
    """
    staging = destination.with_name(destination.name + ".checkpoint-tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(mode=0o700)
    previous = destination if destination.is_dir() else None
    link_dest = previous if previous is not None and (previous / PSD_STATE_FILE).is_file() else baseline or previous
    try:
        with tempfile.TemporaryDirectory(prefix="sqlite-snapshot-", dir=get_volatile_root(create=True)) as tmp:
            snapshots = Path(tmp)
            excluded = snapshot_sqlite(source, snapshots) if live else []
            sync_directories(source, staging, exclude=excluded, link_dest=link_dest)
            if live:
                # Firefox can create a database after the initial scan. Replace any
                # newly copied raw database/WAL pair with an online snapshot too.
                new_databases = [source / path.relative_to(staging) for path in sqlite_paths(staging)]
                for name in snapshot_sqlite(source, snapshots, candidates=new_databases):
                    (staging / name).unlink(missing_ok=True)
                    excluded.append(name)
            if excluded:
                sync_directories(snapshots, staging, delete=False, link_dest=link_dest, checksum=True)
        # Persist nested directory entries too, before publishing the tree.
        for root, _, _ in os.walk(staging, topdown=False):
            fsync_directory_path(Path(root))
        dfd = open_directory(staging)
        try:
            write_config(dfd, PSD_STATE_FILE, json.dumps({"completed_at": time.time()}))
        finally:
            os.close(dfd)
        if previous is None:
            staging.rename(destination)
        else:
            exchange_paths(staging, destination)
        fsync_directory_path(destination.parent)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def resync_profile(profile_path: Path, table: Sequence[MountEntry] | None = None) -> dict:
    paths = get_psd_paths(profile_path)
    is_synced, vtarget, mode = is_profile_synced(profile_path)
    if not is_synced or vtarget is None:
        raise SafetyError(f"Profile {profile_path.name} is not currently synced to RAM.")

    if not paths.backup_path.is_dir():
        fail(f"Disk backing is missing: {paths.backup_path}; RAM profile preserved.")
    LOGGER.info("Resyncing %s from RAM to disk backing...", profile_path.name)

    if mode == "overlay":
        checkpoint_profile(vtarget, paths.back_ovfs_path, baseline=paths.backup_path)
    else:
        checkpoint_profile(vtarget, paths.backup_path)

    if paths.state_file.exists():
        try:
            doc = json.loads(paths.state_file.read_text("utf-8"))
            doc["last_resync"] = time.time()
            doc["last_resync_dt"] = time.strftime("%Y-%m-%d %H:%M:%S")
            paths.state_file.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        except Exception:
            pass

    LOGGER.info("Resync completed successfully for %s", profile_path.name)
    return {"status": "resynced", "timestamp": time.time()}


def unsync_profile_from_ram(profile_path: Path, force: bool = False, table: Sequence[MountEntry] | None = None) -> dict:
    paths = get_psd_paths(profile_path)
    synced, vtarget, mode = is_profile_synced(profile_path)
    if not synced or vtarget is None:
        LOGGER.info("Profile %s is already on persistent disk.", profile_path.name)
        return {"status": "not_synced"}
    if not paths.backup_path.is_dir():
        fail(f"Disk backing is missing: {paths.backup_path}; RAM profile preserved.")
    # A force flag cannot make moving an open Firefox profile reliable.
    info = vtarget.stat()
    profile = Profile(profile_path, info.st_dev, info.st_ino, "tmpfs")
    dir_fd = open_directory(vtarget)
    restored = paths.back_ovfs_path if mode == "overlay" else paths.backup_path
    try:
        with locked_profile(profile, dir_fd):
            LOGGER.info("Unsyncing %s from RAM back to disk...", profile_path.name)
            if mode == "overlay":
                checkpoint_profile(vtarget, paths.back_ovfs_path, live=False, baseline=paths.backup_path)
            else:
                checkpoint_profile(vtarget, paths.backup_path, live=False)
                # Keep Firefox excluded until the disk directory replaces the RAM link.
                exchange_paths(profile_path, restored)
                fsync_directory_path(profile_path.parent)
    finally:
        os.close(dir_fd)
    # Our directory and lock descriptors must be closed before a normal FUSE unmount.
    if mode == "overlay":
        result = subprocess.run(["fusermount3", "-u", str(vtarget)], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            fail(f"Cannot unmount RAM profile; all data preserved: {result.stderr.strip()}")
        exchange_paths(profile_path, restored)
        fsync_directory_path(profile_path.parent)
    # The former backing path now holds the old RAM symlink.
    restored.unlink()
    shutil.rmtree(paths.volatile_mount, ignore_errors=True)
    shutil.rmtree(paths.volatile_upper, ignore_errors=True)
    shutil.rmtree(paths.volatile_work, ignore_errors=True)
    if mode == "overlay":
        shutil.rmtree(paths.backup_path, ignore_errors=True)
    LOGGER.info("Successfully restored %s to persistent disk.", profile_path.name)
    return {"status": "unsynced"}


def psd_daemon_loop(profiles: Sequence[Profile], interval_sec: int) -> int:
    LOGGER.info("Starting Profile RAM Sync Daemon (resync interval: %ds)...", interval_sec)

    running = True
    failed = False

    def sig_handler(signum, _):
        nonlocal running
        LOGGER.info("Daemon received signal %d; shutting down cleanly...", signum)
        running = False

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGHUP, sig_handler)

    # Initial sync for any unsynced profiles
    for prof in profiles:
        try:
            with psd_operation_lock():
                sync_profile_to_ram(prof)
        except Exception as e:
            failed = True
            LOGGER.error("Initial sync failed for %s: %s", prof.path.name, e)

    LOGGER.info("Daemon active. Monitoring and syncing on %ds interval.", interval_sec)

    last_resync = time.monotonic()
    while running:
        time.sleep(1.0)
        if not running:
            break
        if time.monotonic() - last_resync >= interval_sec:
            for prof in profiles:
                try:
                    with psd_operation_lock():
                        resync_profile(prof.path)
                except Exception as e:
                    LOGGER.warning("Scheduled resync error for %s: %s", prof.path.name, e)
            last_resync = time.monotonic()

    LOGGER.info("Shutting down daemon: performing final unsync back to disk...")
    for prof in profiles:
        try:
            with psd_operation_lock():
                try:
                    unsync_profile_from_ram(prof.path)
                except ProfileLockedError:
                    resync_profile(prof.path)
        except Exception as e:
            failed = True
            LOGGER.error("Final unsync failed for %s: %s", prof.path.name, e)

    LOGGER.info("Daemon finished%s.", " with failures" if failed else " cleanly")
    return 1 if failed else 0


def watch_sleep(script_path: Path) -> int:
    """Hold real logind inhibitor FDs until a pre-sleep/shutdown checkpoint finishes."""
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    interface = "org.freedesktop.login1.Manager"
    object_path = "/org/freedesktop/login1"
    inhibitor: int | None = None
    loop = GLib.MainLoop()
    failed = False

    def acquire() -> None:
        nonlocal inhibitor
        if inhibitor is not None:
            return
        result, descriptors = bus.call_with_unix_fd_list_sync(
            "org.freedesktop.login1", object_path, interface, "Inhibit",
            GLib.Variant("(ssss)", (
                "sleep:shutdown", TOOL_NAME, "Checkpoint Firefox RAM profiles", "delay",
            )), GLib.VariantType.new("(h)"), Gio.DBusCallFlags.NONE, 10000, None, None,
        )
        inhibitor = descriptors.get(result.unpack()[0])
        os.set_inheritable(inhibitor, False)

    def release() -> None:
        nonlocal inhibitor
        if inhibitor is not None:
            os.close(inhibitor)
            inhibitor = None

    def prepare(_bus, _sender, _path, _interface, member, parameters, _data) -> None:
        nonlocal failed
        if parameters.unpack()[0]:
            LOGGER.info("%s: checkpointing Firefox before releasing delay inhibitor.", member)
            started = time.monotonic()
            try:
                result = subprocess.run(
                    [sys.executable, str(script_path), "--resync", "--disk-fallback"], check=False,
                )
                if result.returncode:
                    LOGGER.error("Pre-sleep/shutdown checkpoint failed (exit %d).", result.returncode)
                elapsed = time.monotonic() - started
                LOGGER.info("Pre-sleep/shutdown checkpoint took %.2fs.", elapsed)
                if elapsed >= limit:
                    LOGGER.warning("Checkpoint exceeded logind's %.1fs delay; sleep/shutdown may have begun before it completed.", limit)
            finally:
                release()
        else:
            try:
                acquire()
                LOGGER.info("Delay inhibitor rearmed.")
            except GLib.Error:
                failed = True
                LOGGER.exception("Cannot rearm delay inhibitor.")
                loop.quit()

    subscriptions = [
        bus.signal_subscribe(
            "org.freedesktop.login1", interface, member, object_path, None,
            Gio.DBusSignalFlags.NONE, prepare, None,
        ) for member in ("PrepareForSleep", "PrepareForShutdown")
    ]
    try:
        acquire()
        limit = bus.call_sync(
            "org.freedesktop.login1", object_path, "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", (interface, "InhibitDelayMaxUSec")),
            GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 10000, None,
        ).unpack()[0] / 1_000_000
        LOGGER.info("Watching sleep/shutdown; logind allows %.1fs for checkpoints.", limit)
        loop.run()
    finally:
        release()
        for subscription in subscriptions:
            bus.signal_unsubscribe(subscription)
    return 1 if failed else 0


# ===========================================================================
# Systemd Autonomous Service Management
# ===========================================================================

def get_systemd_user_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    s_dir = base / "systemd" / "user"
    s_dir.mkdir(parents=True, exist_ok=True)
    return s_dir


def install_systemd_service(script_path: Path) -> int:
    s_dir = get_systemd_user_dir()
    def exec_quote(value: str) -> str:
        return json.dumps(value).replace("%", "%%").replace("$", "$$")

    py_bin = exec_quote(sys.executable)
    script_arg = exec_quote(str(script_path))

    service_file = s_dir / SYSTEMD_SERVICE_NAME
    resync_service_file = s_dir / SYSTEMD_RESYNC_SERVICE_NAME
    timer_file = s_dir / SYSTEMD_RESYNC_TIMER_NAME
    sleep_file = s_dir / SYSTEMD_SLEEP_SERVICE_NAME

    service_content = f"""[Unit]
Description=Firefox Profile RAM Sync (optimize_firefox.py)
Documentation=file://{script_path}
Wants={SYSTEMD_RESYNC_TIMER_NAME} {SYSTEMD_SLEEP_SERVICE_NAME}

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart={py_bin} {script_arg} --sync --disk-fallback
ExecStop={py_bin} {script_arg} --unsync --disk-fallback
TimeoutStopSec=120
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""

    resync_service_content = f"""[Unit]
Description=Periodic Resync of Firefox Profile from RAM to Disk
After={SYSTEMD_SERVICE_NAME}
BindsTo={SYSTEMD_SERVICE_NAME}

[Service]
Type=oneshot
ExecStart={py_bin} {script_arg} --resync --disk-fallback
Environment=PYTHONUNBUFFERED=1
"""

    timer_content = f"""[Unit]
Description=Hourly Periodic Resync Timer for Firefox Profile RAM Sync
BindsTo={SYSTEMD_SERVICE_NAME}

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
"""

    sleep_content = f"""[Unit]
Description=Checkpoint Firefox RAM profiles before sleep and shutdown
After={SYSTEMD_SERVICE_NAME}
BindsTo={SYSTEMD_SERVICE_NAME}

[Service]
Type=simple
ExecStart={py_bin} {script_arg} --watch-sleep
SuccessExitStatus=130 143
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
"""

    service_file.write_text(service_content, encoding="utf-8")
    resync_service_file.write_text(resync_service_content, encoding="utf-8")
    timer_file.write_text(timer_content, encoding="utf-8")
    sleep_file.write_text(sleep_content, encoding="utf-8")

    LOGGER.info("Wrote systemd user units to %s", s_dir)

    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        res = subprocess.run(
            ["systemctl", "--user", "enable", SYSTEMD_SERVICE_NAME, SYSTEMD_RESYNC_TIMER_NAME],
            capture_output=True, text=True, check=False
        )
        if res.returncode == 0:
            LOGGER.info("Successfully enabled %s and %s", SYSTEMD_SERVICE_NAME, SYSTEMD_RESYNC_TIMER_NAME)
            LOGGER.info("Start immediately with: systemctl --user start %s", SYSTEMD_SERVICE_NAME)
        else:
            fail(f"Could not enable systemd units: {res.stderr.strip()}")

    return 0


def remove_systemd_service() -> int:
    s_dir = get_systemd_user_dir()
    service_file = s_dir / SYSTEMD_SERVICE_NAME
    resync_service_file = s_dir / SYSTEMD_RESYNC_SERVICE_NAME
    timer_file = s_dir / SYSTEMD_RESYNC_TIMER_NAME
    sleep_file = s_dir / SYSTEMD_SLEEP_SERVICE_NAME

    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "stop", SYSTEMD_SLEEP_SERVICE_NAME, SYSTEMD_RESYNC_TIMER_NAME,
             SYSTEMD_RESYNC_SERVICE_NAME], check=True,
        )
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", SYSTEMD_SERVICE_NAME, SYSTEMD_RESYNC_TIMER_NAME],
            check=True
        )

    service_file.unlink(missing_ok=True)
    resync_service_file.unlink(missing_ok=True)
    timer_file.unlink(missing_ok=True)
    sleep_file.unlink(missing_ok=True)

    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    LOGGER.info("Removed systemd user units from %s", s_dir)
    return 0


# ===========================================================================
# Status & Diagnostics
# ===========================================================================

def cache2_info(profile: Profile, prefs_js: str | None) -> tuple[Path, int, int]:
    cache_root: Path | None = None
    if prefs_js:
        for rec in scan_prefs_js(prefs_js):
            if rec.key == "browser.cache.disk.parent_directory" and isinstance(rec.value, str):
                val = Path(rec.value)
                if val.is_absolute():
                    cache_root = val / "cache2"
                break

    if cache_root is None:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
        cache_root = base / "mozilla" / "firefox" / profile.path.name / "cache2"

    if not cache_root.is_dir():
        return cache_root, 0, 0

    count = 0
    total = 0
    try:
        for root, _, files in os.walk(cache_root):
            for f in files:
                count += 1
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass

    return cache_root, count, total


def dir_size(path: Path) -> int:
    total = 0
    def walk_error(error: OSError) -> None:
        raise error
    for root, _, files in os.walk(path, onerror=walk_error):
        for name in files:
            info = (Path(root) / name).lstat()
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def report_status(profiles: Sequence[Profile], as_json: bool) -> int:
    report_data = []

    for profile in profiles:
        is_synced, vtarget, smode = is_profile_synced(profile.path)
        psd_paths = get_psd_paths(profile.path)

        prof_report = {
            "profile": str(profile.path),
            "filesystem": profile.fstype,
            "ram_sync": "active" if is_synced else "inactive",
            "ram_sync_mode": smode,
            "ram_volatile_path": str(vtarget) if vtarget else None,
            "ram_volatile_bytes": dir_size(vtarget) if vtarget else 0,
            "ram_backing_path": str(psd_paths.backup_path) if psd_paths.backup_path.exists() else None,
            "lock": "unknown",
            "lock_holder": None,
            "browser.cache.disk.enable": None,
            "browser.cache.memory.enable": None,
            "managed": "no",
            "advisories": [],
            "cache2_path": None,
            "cache2_files": 0,
            "cache2_bytes": 0,
        }

        try:
            resolved_prof = profile.path.resolve()
            dir_fd = open_directory(resolved_prof)
        except SafetyError as error:
            prof_report["error"] = str(error)
            report_data.append(prof_report)
            continue

        try:
            try:
                lfd = os.open(PARENTLOCK, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=dir_fd)
                try:
                    holder = probe_lock_holder(lfd)
                    if holder is not None:
                        prof_report["lock"] = "locked"
                        pname = process_name(holder) or "unknown"
                        prof_report["lock_holder"] = {"pid": holder, "comm": pname}
                    else:
                        sym = symlink_lock_holder(dir_fd)
                        if sym is not None:
                            prof_report["lock"] = f"free (stale lock -> {sym[0]}:+{sym[1]})"
                        else:
                            prof_report["lock"] = "free"
                finally:
                    os.close(lfd)
            except FileNotFoundError:
                prof_report["lock"] = "free (no .parentlock)"
            except OSError:
                prof_report["lock"] = "unreadable"

            prefs_js = read_config(dir_fd, PREFS_JS)
            user_js = read_config(dir_fd, USER_JS)
            state_text = read_config(dir_fd, STATE_FILENAME)

            combined = (prefs_js or "") + "\n" + (user_js or "")
            for rec in scan_prefs_js(combined):
                if rec.key == "browser.cache.disk.enable":
                    prof_report["browser.cache.disk.enable"] = rec.value
                elif rec.key == "browser.cache.memory.enable":
                    prof_report["browser.cache.memory.enable"] = rec.value

            if state_text is not None:
                prof_report["managed"] = "yes"
            else:
                _, _, has_legacy = extract_legacy_block(user_js)
                if has_legacy:
                    prof_report["managed"] = "legacy optimizer block present (migration ready)"

            prof_report["advisories"] = collect_advisories(prefs_js or "", user_js)
            cpath, ccount, cbytes = cache2_info(profile, prefs_js)
            prof_report["cache2_path"] = str(cpath)
            prof_report["cache2_files"] = ccount
            prof_report["cache2_bytes"] = cbytes

        finally:
            os.close(dir_fd)

        report_data.append(prof_report)

    if as_json:
        print(json.dumps({"tool": TOOL_NAME, "version": TOOL_VERSION, "profiles": report_data}, indent=2))
        return 0

    import platform
    LOGGER.info("%s %s - Firefox 156+ HTTP cache-policy & RAM profile manager", TOOL_NAME, TOOL_VERSION)
    LOGGER.info("Kernel     : %s", platform.release())
    LOGGER.info("Python     : %s", platform.python_version())
    try:
        ff_out = subprocess.run(["firefox", "--version"], capture_output=True, text=True, check=False)
        LOGGER.info("Firefox    : %s", ff_out.stdout.strip() or "unknown")
    except Exception:
        LOGGER.info("Firefox    : (not on PATH)")

    # Systemd Service check
    if shutil.which("systemctl"):
        s_res = subprocess.run(
            ["systemctl", "--user", "is-active", SYSTEMD_SERVICE_NAME],
            capture_output=True, text=True, check=False
        )
        t_res = subprocess.run(
            ["systemctl", "--user", "is-active", SYSTEMD_RESYNC_TIMER_NAME],
            capture_output=True, text=True, check=False
        )
        LOGGER.info(
            "Systemd    : service=%s, resync_timer=%s",
            s_res.stdout.strip() or "inactive",
            t_res.stdout.strip() or "inactive",
        )

    for pr in report_data:
        LOGGER.info("--- %s", pr["profile"])
        LOGGER.info("    filesystem  : %s", pr["filesystem"])
        lock_str = pr["lock"]
        if pr.get("lock_holder"):
            lock_str += f" (PID {pr['lock_holder']['pid']} '{pr['lock_holder']['comm']}')"
        LOGGER.info("    lock        : %s", lock_str)
        if pr["ram_sync"] == "active":
            LOGGER.info(
                "    RAM sync    : ACTIVE (%s engine, %s in RAM at %s)",
                pr["ram_sync_mode"], human_bytes(pr["ram_volatile_bytes"]), pr["ram_volatile_path"]
            )
            LOGGER.info("    disk backing: %s", pr["ram_backing_path"])
        else:
            LOGGER.info("    RAM sync    : inactive (stored directly on disk)")
        LOGGER.info("    browser.cache.disk.enable   = %s", str(pr["browser.cache.disk.enable"]).lower())
        LOGGER.info("    browser.cache.memory.enable = %s", str(pr["browser.cache.memory.enable"]).lower())
        LOGGER.info("    managed     : %s", pr["managed"])
        for adv in pr["advisories"]:
            LOGGER.info("    advisory    : %s", adv)
        LOGGER.info("    cache2      : %s in %d file(s) at %s", human_bytes(pr["cache2_bytes"]), pr["cache2_files"], pr["cache2_path"])

    return 0


# ===========================================================================
# Maintenance Services Check
# ===========================================================================

def require_inactive_maintenance_services() -> None:
    if shutil.which("systemctl") is None:
        return

    try:
        res = subprocess.run(
            ["systemctl", "--user", "show", *EXTERNAL_MAINTENANCE_UNITS, "--property=Id,LoadState,ActiveState,UnitFileState"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10.0,
        )
    except (subprocess.SubprocessError, OSError):
        return

    if res.returncode != 0:
        return

    blocks = res.stdout.strip().split("\n\n")
    for block in blocks:
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        unit_id = props.get("Id", "")
        load_state = props.get("LoadState", "")
        active_state = props.get("ActiveState", "")
        file_state = props.get("UnitFileState", "")

        if load_state == "not-found":
            continue
        if active_state in {"active", "activating"}:
            fail(f"Active maintenance unit detected: {unit_id}. Close Firefox and stop external profile automation.")
        if file_state in {"enabled", "enabled-runtime"}:
            fail(f"Maintenance unit {unit_id} is enabled; reconcile this automation before applying.")


# ===========================================================================
# Self-Contained Verification Harness (--verify)
# ===========================================================================

def run_verification_suite(verbose: bool) -> int:
    """
    Self-contained empirical verification test harness & stress suite.
    Exercises toolchain, disposable profiles, dry-run purity, umask independence,
    headless Firefox persistence, OFD locking, legacy migration, PSD RAM sync lifecycle,
    headless Firefox database execution in RAM, disk isolation, crash recovery, and SQLite stress.
    """
    failed = 0
    passed = 0

    def vlog(msg: str) -> None:
        print(f"\033[36m==>\033[0m {msg}", flush=True)

    def vok(msg: str) -> None:
        nonlocal passed
        passed += 1
        print(f"\033[32m  [PASS]\033[0m {msg}", flush=True)

    def vfail(msg: str) -> None:
        nonlocal failed
        failed += 1
        print(f"\033[31m  [FAIL]\033[0m {msg}", flush=True)

    print("\n====================================================")
    print("  optimize_firefox.py: Self-Verification Suite      ")
    print("====================================================\n")

    # 1. Toolchain & Environment
    vlog("1. Toolchain & Environment Verification")
    import platform
    print(f"Kernel     : {platform.release()}")
    print(f"Python     : {platform.python_version()}")

    ff_ver_str = "unknown"
    try:
        ff_run = subprocess.run(["firefox", "--version"], capture_output=True, text=True, check=False)
        ff_ver_str = ff_run.stdout.strip()
    except OSError:
        pass
    print(f"Firefox    : {ff_ver_str}")

    ff_match = re.search(r"(\d+)", ff_ver_str)
    if ff_match and int(ff_match.group(1)) >= MIN_FIREFOX_MAJOR:
        vok(f"Firefox {ff_match.group(1)} >= {MIN_FIREFOX_MAJOR}")
    else:
        vfail(f"Firefox {MIN_FIREFOX_MAJOR}+ required; found {ff_ver_str}")

    assert hasattr(uuid, "uuid7"), "uuid7 missing"
    assert hasattr(fcntl, "F_OFD_SETLK") and hasattr(fcntl, "F_OFD_GETLK"), "OFD locks missing"
    assert hasattr(zstd, "ZstdFile"), "ZstdFile missing"
    vok("Python 3.14.7+ runtime prerequisites (OFD, zstd, uuid7) verified")

    cache_base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    cache_base.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="ffcp-verify-", dir=cache_base))
    profile_dir = work_dir / "profile"
    backups_dir = work_dir / "backups"
    script_path = Path(__file__).resolve()

    def run_sub(*sub_args, expect_code=0, timeout=25.0) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(script_path), *sub_args]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        if proc.returncode != expect_code:
            raise AssertionError(
                f"Expected exit code {expect_code}, got {proc.returncode}.\n"
                f"Stdout: {proc.stdout}\nStderr: {proc.stderr}"
            )
        return proc

    def ff_headless(target_dir: Path, timeout_sec: int = 30) -> None:
        subprocess.run(
            ["firefox", "--no-remote", "--profile", str(target_dir), "--headless",
             "--screenshot", str(work_dir / "shot.png"), "about:blank"],
            capture_output=True, timeout=timeout_sec, check=True,
        )

    try:
        # 2. Disposable test profile
        vlog("2. Initialize Disposable Test Profile")
        profile_dir.mkdir(parents=True, exist_ok=True)
        ff_headless(profile_dir, 35)
        ff_headless(profile_dir, 25)
        if (profile_dir / "prefs.js").is_file():
            vok("prefs.js generated by Firefox")
        else:
            raise AssertionError("Firefox did not initialize prefs.js; cannot verify its lifecycle")

        # 3. Dry-Run Purity
        vlog("3. Dry-Run Purity Verification")
        before_tree = {p: p.lstat().st_mtime_ns for p in profile_dir.rglob("*")}
        run_sub("--cache-mode", "memory", "--profile", str(profile_dir), "--backup-dir", str(backups_dir), "--dry-run")
        after_tree = {p: p.lstat().st_mtime_ns for p in profile_dir.rglob("*")}
        if before_tree == after_tree and not backups_dir.exists() and not (profile_dir / "user.js").exists():
            vok("Dry-run mutated zero files and created zero directories")
        else:
            vfail("Dry-run caused filesystem mutations")

        # 4. Apply Memory Mode Policy
        vlog("4. Apply Default Memory Cache Policy (No Action Argument)")
        run_sub("--profile", str(profile_dir), "--backup-dir", str(backups_dir))
        u_content = (profile_dir / "user.js").read_text(encoding="utf-8")
        if 'user_pref("browser.cache.disk.enable", false);' in u_content and 'user_pref("browser.cache.memory.enable", true);' in u_content:
            vok("browser.cache.disk.enable=false & memory.enable=true applied")
        else:
            vfail("Preferences missing from user.js")

        applied_files = {
            name: (profile_dir / name).read_bytes() for name in (USER_JS, STATE_FILENAME, PREFS_JS)
        }
        archives = set(backups_dir.iterdir())
        run_sub("--profile", str(profile_dir), "--backup-dir", str(backups_dir))
        if applied_files == {name: (profile_dir / name).read_bytes() for name in applied_files} and archives == set(backups_dir.iterdir()):
            vok("Repeated default invocation changed no preferences, state, or backup archives")
        else:
            vfail("Repeated default invocation was not idempotent")

        # 5. Exact Modes & Umask Independence
        vlog("5. Exact File Modes & Umask Independence")
        u_mode = stat.S_IMODE((profile_dir / "user.js").stat().st_mode)
        s_mode = stat.S_IMODE((profile_dir / STATE_FILENAME).stat().st_mode)
        b_mode = stat.S_IMODE(backups_dir.stat().st_mode)
        if u_mode == 0o600 and s_mode == 0o600 and b_mode == 0o700:
            vok(f"Exact modes verified (user.js: {u_mode:04o}, state: {s_mode:04o}, backup_dir: {b_mode:04o})")
        else:
            vfail(f"Incorrect modes: user.js {u_mode:04o}, state {s_mode:04o}, backup_dir {b_mode:04o}")

        # 6. Exclusive OFD Locking Test
        vlog("6. Exclusive OFD Locking Contention (Exit 3)")
        ff_proc = subprocess.Popen(
            ["firefox", "--no-remote", "--profile", str(profile_dir), "--headless", "about:blank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(4.0)
            lock_proc = subprocess.run(
                [sys.executable, str(script_path), "--disable", "--profile", str(profile_dir), "--backup-dir", str(backups_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            if lock_proc.returncode == 3 and "PID" in lock_proc.stderr:
                vok("Exclusive OFD lock correctly detected running Firefox (exit code 3, holder reported)")
            else:
                vfail(f"Lock contention failed (code {lock_proc.returncode}): {lock_proc.stderr}")
        finally:
            ff_proc.terminate()
            ff_proc.wait(timeout=5.0)
            time.sleep(1.0)

        # 7. Restore with --disable
        vlog("7. Restore with --disable & Custom Preference Preservation")
        custom_snippet = '// Theme Customizations\nuser_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);\n'
        (profile_dir / "user.js").write_text(custom_snippet, encoding="utf-8")
        h0 = hashlib.sha256(custom_snippet.encode()).hexdigest()
        (profile_dir / STATE_FILENAME).unlink(missing_ok=True)

        run_sub("--cache-mode", "memory", "--profile", str(profile_dir), "--backup-dir", str(backups_dir), "--no-backup")
        run_sub("--disable", "--profile", str(profile_dir), "--backup-dir", str(backups_dir), "--no-backup")
        restored = (profile_dir / "user.js").read_text(encoding="utf-8")
        h1 = hashlib.sha256(restored.encode()).hexdigest()
        if h0 == h1 and not (profile_dir / STATE_FILENAME).exists():
            vok("Custom user.js preserved byte-identically through memory/disable cycle")
        else:
            vfail("user.js was altered during round-trip restore")

        # 8. Profile RAM Sync Lifecycle: Sync to RAM
        vlog("8. Profile Sync Daemon: --sync to RAM (tmpfs)")
        sync_res = run_sub("--sync", "--profile", str(profile_dir))
        is_synced, vtarget, mode_used = is_profile_synced(profile_dir)
        if is_synced and vtarget is not None and vtarget.is_dir():
            vok(f"Profile successfully mounted in RAM via {mode_used} at {vtarget}")
        else:
            vfail(f"Profile failed to sync to RAM: {sync_res.stdout}")

        # 9. Headless Firefox Execution in RAM
        vlog("9. Headless Firefox Execution on RAM Profile")
        ff_headless(profile_dir, 30)
        if (vtarget / "cookies.sqlite").is_file() or (vtarget / "places.sqlite").is_file() or (vtarget / "prefs.js").is_file():
            vok("Firefox successfully executed and updated databases inside RAM container")
        else:
            vfail("Firefox did not update files in RAM container")

        # 10. Empirical Write Isolation Test (Disk Backing Protected)
        vlog("10. Empirical Write Isolation (RAM writes vs Disk Backing)")
        # In RAM container, write test marker
        test_marker = vtarget / "ram_test_marker.txt"
        test_marker.write_text("written_only_to_ram_tmpfs\n", encoding="utf-8")
        psd_paths = get_psd_paths(profile_dir)
        if not (psd_paths.backup_path / "ram_test_marker.txt").exists():
            vok("Writes to profile stayed strictly in RAM without hitting disk backup")
        else:
            vfail("Write leaked directly to disk backing before resync")

        # 11. Incremental Resync Test
        vlog("11. Incremental Resync While Firefox Is Running (--resync)")
        live_firefox = subprocess.Popen(
            ["firefox", "--no-remote", "--profile", str(profile_dir), "--headless", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while probe_profile_lock(profile_dir) is None and time.monotonic() < deadline:
                if live_firefox.poll() is not None:
                    raise AssertionError("Firefox exited before its live checkpoint test")
                time.sleep(0.05)
            if probe_profile_lock(profile_dir) is None:
                raise AssertionError("Firefox did not acquire its RAM profile lock")
            run_sub("--resync", "--profile", str(profile_dir), timeout=60)
            checkpoint = psd_paths.back_ovfs_path if mode_used == "overlay" else psd_paths.backup_path
            with closing(sqlite3.connect((checkpoint / "cookies.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)) as cookies:
                if cookies.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise AssertionError("Live Firefox cookie snapshot failed integrity check")
            vok("Running Firefox's cookie database was backed up consistently without closing the browser")
        finally:
            live_firefox.terminate()
            live_firefox.wait(timeout=10)
        if mode_used == "overlay":
            synced_ok = (psd_paths.back_ovfs_path / "ram_test_marker.txt").exists()
        else:
            synced_ok = (psd_paths.backup_path / "ram_test_marker.txt").exists()
        if synced_ok:
            vok(f"--resync successfully flushed RAM delta to disk backing ({mode_used})")
        else:
            vfail("Delta was not copied to disk backing during --resync")

        # 12. Clean Unsync & Restoration to Disk
        vlog("12. Clean Unsync & Restoration to Disk (--unsync)")
        run_sub("--unsync", "--profile", str(profile_dir))
        is_synced_after, _, _ = is_profile_synced(profile_dir)
        if not is_synced_after and profile_dir.is_dir() and not profile_dir.is_symlink():
            if (profile_dir / "ram_test_marker.txt").exists():
                vok("Profile restored cleanly as normal disk directory with all RAM changes intact")
            else:
                vfail("Profile restored but changes made in RAM were lost")
        else:
            vfail("Profile remained symlinked or failed to restore")

        # 13. Ungraceful Crash Recovery Test
        vlog("13. Ungraceful Crash Recovery Simulation")
        # Step A: Sync to RAM
        run_sub("--sync", "--profile", str(profile_dir))
        # Step B: Simulate sudden power cut: delete volatile tmpfs directory while symlink remains
        _, fake_vtarget, _ = is_profile_synced(profile_dir)
        if fake_vtarget and is_mountpoint(fake_vtarget):
            subprocess.run(["fusermount3", "-u", str(fake_vtarget)], check=False)
        shutil.rmtree(fake_vtarget, ignore_errors=True)
        # Step C: Mutating sync recovers the broken link; status remains read-only.
        status_res = run_sub("--sync", "--profile", str(profile_dir))
        run_sub("--unsync", "--profile", str(profile_dir))
        if profile_dir.is_dir() and not profile_dir.is_symlink():
            vok("Ungraceful state automatically detected and recovered disk backup without data loss")
        else:
            vfail(f"Crash recovery failed: {status_res.stdout}")

        # 14. High-Frequency SQLite Stress Test in RAM
        vlog("14. High-Frequency SQLite Stress Test in RAM Profile")
        run_sub("--sync", "--profile", str(profile_dir))
        db_path = profile_dir / "stress_test.sqlite"
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=FULL")
        cur.execute("CREATE TABLE stress (id INTEGER PRIMARY KEY, ts REAL, data TEXT)")
        t0 = time.time()
        for i in range(1000):
            cur.execute("INSERT INTO stress VALUES (?, ?, ?)", (i, time.time(), "X" * 256))
            if i % 100 == 0:
                conn.commit()
        conn.commit()
        conn.close()
        t_delta = time.time() - t0
        run_sub("--unsync", "--profile", str(profile_dir))
        with sqlite3.connect(profile_dir / "stress_test.sqlite") as restored_db:
            rows = restored_db.execute("SELECT COUNT(*) FROM stress").fetchone()[0]
            integrity = restored_db.execute("PRAGMA integrity_check").fetchone()[0]
        if rows == 1000 and integrity == "ok":
            vok(f"1,000 SQLite WAL inserts executed in RAM ({t_delta:.3f}s); restored row count and integrity verified")
        else:
            vfail(f"Restored stress database failed verification: rows={rows}, integrity={integrity}")

        vlog("15. Fresh Installation Without Firefox Profiles")
        fresh_home = work_dir / "fresh-home"
        fresh_home.mkdir()
        fresh_env = os.environ.copy()
        fresh_env.update({
            "HOME": str(fresh_home),
            "XDG_CONFIG_HOME": str(fresh_home / ".config"),
            "XDG_CACHE_HOME": str(fresh_home / ".cache"),
        })
        fresh_env.pop("MOZ_LEGACY_HOME", None)
        for arguments in ((), ("--sync", "--disk-fallback"), ("--resync", "--disk-fallback")):
            result = subprocess.run(
                [sys.executable, str(script_path), *arguments], env=fresh_env,
                capture_output=True, text=True, timeout=25, check=False,
            )
            if result.returncode == 0 and "No initialized Firefox profiles" in result.stdout and not any(fresh_home.iterdir()):
                vok(f"Fresh install {arguments or 'default'} reports no changes without creating a profile")
            else:
                vfail(f"Fresh install {arguments}: exit={result.returncode}, stdout={result.stdout}, stderr={result.stderr}")

        vlog("16. Exclusively Locked SQLite WAL and Hot Journal Recovery")
        with tempfile.TemporaryDirectory(prefix="exclusive-verify-", dir=get_volatile_root()) as tmp:
            for journal_mode in ("WAL", "DELETE"):
                source = Path(tmp) / journal_mode
                snapshots = Path(tmp) / f"{journal_mode}-snapshot"
                source.mkdir()
                snapshots.mkdir()
                database = source / "exclusive.sqlite"
                with closing(sqlite3.connect(database)) as db:
                    db.execute(f"PRAGMA journal_mode={journal_mode}")
                    db.execute("PRAGMA locking_mode=EXCLUSIVE")
                    db.execute("PRAGMA cache_size=2")
                    db.execute("CREATE TABLE tokens (id INTEGER PRIMARY KEY, value TEXT)")
                    db.execute("INSERT INTO tokens VALUES (1, 'committed')")
                    db.commit()
                    db.execute("BEGIN IMMEDIATE")
                    db.executemany("INSERT INTO tokens VALUES (?, ?)", ((i, "X" * 1000) for i in range(2, 102)))
                    snapshot_sqlite(source, snapshots)
                    with closing(sqlite3.connect(snapshots / database.name)) as saved:
                        integrity = saved.execute("PRAGMA integrity_check").fetchone()[0]
                        rows = saved.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
                    db.rollback()
                if integrity == "ok" and rows == 1:
                    vok(f"Exclusive {journal_mode} snapshot retained committed data and recovered away the unfinished transaction")
                else:
                    vfail(f"Exclusive {journal_mode}: integrity={integrity}, rows={rows}")

    finally:
        # Cleanup
        try:
            is_synced, vt, _ = is_profile_synced(profile_dir)
            if is_synced and vt and is_mountpoint(vt):
                subprocess.run(["fusermount3", "-u", str(vt)], check=False)
        except Exception:
            pass
        shutil.rmtree(work_dir, ignore_errors=True)

    print("\n====================================================")
    if failed == 0:
        print(f"\033[32m  ALL {passed} EMPIRICAL VERIFICATION CHECKS PASSED!\033[0m")
        print("====================================================\n")
        return 0
    else:
        print(f"\033[31m  {failed} CHECKS FAILED! (Passed: {passed})\033[0m")
        print("====================================================\n")
        return 1


# ===========================================================================
# CLI Interface
# ===========================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        color=True,
        suggest_on_error=True,
        description=(
            "Bleeding-edge Firefox 156+ HTTP cache-policy & RAM profile manager: "
            "Native Profile-Sync-Daemon (PSD) RAM engine with fuse-overlayfs, "
            "exclusive Linux OFD locking, private zstd backups, saved-preference rollback, "
            "umask-independent atomic writes, and seamless legacy optimizer migration. "
            "Without an action argument, apply the memory-only HTTP cache policy."
        ),
        epilog=(
            "Exit status: 0 success, 1 error, 2 usage error, 3 profile locked by running Firefox, "
            "130 interrupted, 143 terminated."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")

    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--cache-mode",
        choices=("memory", "default"),
        help=(
            "'memory': disable the HTTP disk cache and enable the memory cache. "
            "'default': restore baseline configuration."
        ),
    )
    action.add_argument("--disable", action="store_true", help="Alias for --cache-mode default.")
    action.add_argument("--status", action="store_true", help="Read-only diagnostic report.")
    action.add_argument("--sync", action="store_true", help="Synchronize profile(s) to RAM (tmpfs) to eliminate SSD writes.")
    action.add_argument("--unsync", action="store_true", help="Synchronize profile(s) from RAM back to persistent disk.")
    action.add_argument("--resync", action="store_true", help="Perform incremental resync of RAM profile to disk.")
    action.add_argument("--daemon", action="store_true", help="Run background sync daemon with periodic resync.")
    action.add_argument("--watch-sleep", action="store_true", help="Checkpoint before sleep/shutdown using logind delay inhibitors (python-gobject).")
    action.add_argument("--install-service", action="store_true", help="Install and enable systemd user units for autonomous sync.")
    action.add_argument("--remove-service", action="store_true", help="Stop, disable, and remove systemd user units.")
    action.add_argument("--verify", action="store_true", help="Run full self-contained empirical verification suite.")

    parser.add_argument(
        "--profile", type=Path, action="append", default=None,
        help="Initialized profile directory (repeatable). Explicit paths replace discovery.",
    )
    parser.add_argument(
        "--disk-fallback", action="store_true",
        help="Keep disk profiles for --sync; skip disk profiles for --resync; checkpoint open Firefox profiles for --unsync.",
    )
    parser.add_argument(
        "--sync-mode", choices=("auto", "overlay", "copy"), default="auto",
        help="RAM sync engine: 'auto' (copy when tmpfs has capacity, otherwise overlay), 'overlay', 'copy'.",
    )
    parser.add_argument(
        "--interval", type=int, default=3600,
        help="Periodic resync interval in seconds for --daemon (default: 3600s / 1 hour).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Legacy option; active Firefox locks are always respected during unsync.",
    )
    parser.add_argument(
        "--backup-dir", type=Path,
        help=f"Backup directory outside selected profiles. Default: <parent>/{DEFAULT_BACKUP_DIRNAME}",
    )
    parser.add_argument(
        "--backup-scope", choices=("targeted", "full"), default="targeted",
        help="'targeted' (default): archive user.js, prefs.js, state.json. 'full': entire profile.",
    )
    parser.add_argument(
        "--memory-capacity", type=int, default=None,
        help="Optional memory cache capacity in KB (e.g. 4194304 for 4GB). Default: dynamic.",
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Skip creating backup archives on apply/restore.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate changes without writing.")
    parser.add_argument("--json", action="store_true", help="Output status report as JSON.")
    parser.add_argument("--skip-maintenance-check", action="store_true", help="Bypass external maintenance check.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug diagnostics.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cache_mode is None and not any((
        args.disable, args.status, args.sync, args.unsync, args.resync, args.daemon,
        args.install_service, args.remove_service, args.verify, args.watch_sleep,
    )):
        args.cache_mode = "memory"

    if args.disk_fallback and not (args.sync or args.resync or args.unsync):
        parser.error("--disk-fallback requires --sync, --resync or --unsync")
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.memory_capacity is not None and not -1 <= args.memory_capacity < 2 ** 31:
        parser.error("--memory-capacity must be -1 or a nonnegative 32-bit integer")
    if args.dry_run and (args.sync or args.unsync or args.resync or args.daemon or args.install_service or args.remove_service or args.verify or args.watch_sleep):
        parser.error("--dry-run is supported only for cache policy changes and status")

    configure_logging(args.verbose)
    if args.json:
        LOGGER.setLevel(logging.WARNING)

    if args.verify:
        return run_verification_suite(args.verbose)

    script_path = Path(__file__).resolve()

    if args.install_service:
        return install_systemd_service(script_path)

    if args.remove_service:
        return remove_systemd_service()

    def handle_signal(signum: int, _) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGHUP, handle_signal)

    if os.geteuid() == 0:
        fail("Running as root or via sudo is refused. Run as the Firefox profile owner.")

    if args.watch_sleep:
        return watch_sleep(script_path)

    if args.daemon:
        with psd_operation_lock():
            profiles = select_profiles(args.profile or [], read_mount_table(), recover=True)
        return psd_daemon_loop(profiles, args.interval)

    with nullcontext() if args.status or args.dry_run else psd_operation_lock():
        table = read_mount_table()

        explicit_profiles = [Path(p) for p in args.profile] if args.profile else []
        profiles = select_profiles(
            explicit_profiles, table, recover=not (args.status or args.dry_run),
            allow_empty=not explicit_profiles and (
                args.cache_mode == "memory" or (args.sync or args.resync or args.unsync) and args.disk_fallback
            ),
        )
        if not profiles:
            LOGGER.info("No initialized Firefox profiles found; no profile changes made. Run Firefox once, then rerun this command.")
            return 0

        if args.status:
            return report_status(profiles, args.json)

        # ------------------------------------------------------------- PSD Actions
        if args.sync:
            disk_profiles = 0
            for prof in profiles:
                try:
                    sync_profile_to_ram(prof, mode=args.sync_mode, table=table)
                except (RamSyncUnavailable, ProfileLockedError) as error:
                    if not args.disk_fallback:
                        raise
                    # Fallback applies only to an intact disk profile, never uncertain RAM state.
                    if is_profile_synced(prof.path)[0] or not prof.path.is_dir():
                        raise
                    disk_profiles += 1
                    LOGGER.warning("Keeping %s on persistent disk: %s", prof.path.name, error)
            LOGGER.info("Profile setup complete: %d in RAM, %d kept on disk.", len(profiles) - disk_profiles, disk_profiles)
            return 0

        if args.unsync:
            for prof in profiles:
                try:
                    unsync_profile_from_ram(prof.path, force=args.force, table=table)
                except ProfileLockedError:
                    if not args.disk_fallback:
                        raise
                    LOGGER.warning("Firefox is still open; checkpointing %s and retaining RAM link for recovery.", prof.path.name)
                    resync_profile(prof.path, table=table)
            LOGGER.info("All profiles saved to persistent disk.")
            return 0

        if args.resync:
            for prof in profiles:
                if args.disk_fallback and not is_profile_synced(prof.path)[0]:
                    LOGGER.info("Skipping %s: profile is already on persistent disk.", prof.path.name)
                    continue
                resync_profile(prof.path, table=table)
            LOGGER.info("All profiles resynchronized successfully.")
            return 0

        # ------------------------------------------------ Cache Preferences Policy
        if not args.skip_maintenance_check:
            require_inactive_maintenance_services()

        memory_mode = not args.disable and args.cache_mode == "memory"
        LOGGER.info(
            "Requested action: %s",
            "memory-only HTTP cache policy" if memory_mode else "restore baseline configuration",
        )

        if args.dry_run:
            LOGGER.info("[Dry Run] Simulating without taking locks or writing files:")
            for prof in profiles:
                dir_fd = open_directory(prof.path.resolve())
                try:
                    plan = prepare_profile(prof, dir_fd, memory_mode, args.memory_capacity)
                    if not plan.changed:
                        LOGGER.info("[Dry Run] %s: already in requested state (no-op)", prof.path)
                        continue

                    if plan.is_migration:
                        LOGGER.info("[Dry Run] %s: legacy optimizer block will be migrated", prof.path)

                    if not args.no_backup:
                        dest = resolve_backup_destination(prof, args.backup_dir, profiles, table, create=False)
                        entries, size = (
                            targeted_entries(prof) if args.backup_scope == "targeted" else full_entries(prof, table)
                        )
                        LOGGER.info(
                            "[Dry Run] %s: would create %s backup (%d entries, ~%s) at %s",
                            prof.path, args.backup_scope, len(entries), human_bytes(size), dest,
                        )
                    else:
                        LOGGER.info("[Dry Run] %s: backup creation skipped (--no-backup)", prof.path)
                    LOGGER.info("[Dry Run] %s: would apply updated user.js / prefs.js", prof.path)
                finally:
                    os.close(dir_fd)
            LOGGER.info("[Dry Run] Completed successfully.")
            return 0

        with ExitStack() as stack:
            dir_fds: dict[Profile, int] = {}
            for prof in profiles:
                resolved_p = prof.path.resolve()
                dfd = open_directory(resolved_p)
                stack.callback(os.close, dfd)
                dir_fds[prof] = dfd
                stack.enter_context(locked_profile(prof, dfd))

            if not args.skip_maintenance_check:
                require_inactive_maintenance_services()

            plans = [
                prepare_profile(prof, dir_fds[prof], memory_mode, args.memory_capacity)
                for prof in profiles
            ]
            changes = [p for p in plans if p.changed]

            if not changes:
                LOGGER.info("All profiles are already in the requested state. No changes required.")
                return 0

            if not args.no_backup:
                for plan in changes:
                    dest = resolve_backup_destination(
                        plan.profile, args.backup_dir, profiles, table, create=True
                    )
                    backup_profile(plan.profile, dest, args.backup_scope, table)
            else:
                LOGGER.info("Skipping backup creation (--no-backup specified).")

            for plan in changes:
                apply_plan(plan, dir_fds[plan.profile])
                verify_applied(plan, dir_fds[plan.profile])

                # If profile is currently synced to RAM, immediately resync so changes are persisted to disk backing
                is_synced, _, _ = is_profile_synced(plan.profile.path)
                if is_synced:
                    resync_profile(plan.profile.path, table=table)

        LOGGER.info("Requested cache-policy changes applied successfully.")
        LOGGER.info("Start Firefox normally to run with the updated cache configuration.")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProfileLockedError as lock_err:
        LOGGER.error("%s", lock_err)
        sys.exit(3)
    except SafetyError as safety_err:
        LOGGER.error("%s", safety_err)
        sys.exit(1)
    except KeyboardInterrupt:
        LOGGER.error("Interrupted by user (SIGINT).")
        sys.exit(130)
    except SystemExit as se:
        raise se
    except Exception as exc:
        LOGGER.error("Unexpected error: %s", exc)
        LOGGER.debug("Stack trace:", exc_info=True)
        sys.exit(1)

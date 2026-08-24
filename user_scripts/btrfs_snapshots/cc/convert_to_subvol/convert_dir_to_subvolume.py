#!/usr/bin/env python3
"""
Convert a regular directory into an isolated top-level Btrfs subvolume (subvolid=5),
or UNDO the conversion to return it back to a normal directory inside /home.

Borrowing battle-tested safety logic from Dusky Snapshot Manager (v3.2.0) and
snapper setup script (137):
  * Process/host-wide exclusive locking via fcntl.flock (/run/dusky/dusky.lock)
  * Signal-blocked critical section across activation and directory swap
  * Private subvolid=5 mount with rprivate propagation and UUID/subvolid verification
  * Data migration using cp -a (preserves mode, ownership, timestamps, symlinks, xattrs)
  * Durable /etc/fstab write (write -> fsync -> replace -> fsync dir) & findmnt --verify
  * Live mount verification using kernel subvolume query and findmnt
  * Full atomic unwind / rollback if any step fails
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from typing import Iterator

DUSKY_VERSION = "3.2.0-converter"
BTRFS_FS_TREE_OBJECTID = 5

RUN_DIR = Path("/run/dusky")
MNT_ROOT = RUN_DIR / "mnt"
LOCK_PATH = RUN_DIR / "dusky.lock"

SAFE_NAME_RE = re.compile(r"\A@[A-Za-z0-9_.-]{1,180}\Z")
MOUNT_DIR_RE = re.compile(r"\Atop_(?P<pid>\d+)_(?P<tag>[A-Za-z0-9_]+)\Z")
UUID_RE = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")

# Transient & system subvolume protection
TRANSIENT_PATTERNS = ("_to_delete_", "_dusky_new_", ".tmp_send_", ".dusky_probe_")
PROTECTED_SUBVOLUMES = {"@", "@home", "@snapshots", "@home_snapshots", "@var_log", "@var_cache", "@var_tmp", "@swap"}

_ENV_PASSTHROUGH = ("TERM", "TERMINFO", "COLORTERM", "TZ")
SUBPROCESS_ENV = {
    **{k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ},
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
    "LC_ALL": "C.UTF-8",
    "LANG": "C.UTF-8",
    "HOME": "/root",
}


class ConversionError(RuntimeError):
    pass


def die(msg: str, exit_code: int = 1) -> None:
    print(f"\033[1;31m[FATAL]\033[0m {msg}", file=sys.stderr)
    sys.exit(exit_code)


def info(msg: str) -> None:
    print(f"\033[1;32m[INFO]\033[0m {msg}")


def warn(msg: str) -> None:
    print(f"\033[1;33m[WARN]\033[0m {msg}", file=sys.stderr)


def good(msg: str) -> None:
    print(f"\033[1;32m{msg}\033[0m")


def run(*argv: str, check: bool = True, timeout: float | None = 300.0) -> subprocess.CompletedProcess[str]:
    cmd = [str(a) for a in argv]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=SUBPROCESS_ENV,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ConversionError(f"Missing executable: {cmd[0]} ({exc})") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"Timed out after {timeout}s: {shlex.join(cmd)}") from exc

    if check and proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise ConversionError(f"Command failed ({proc.returncode}): {shlex.join(cmd)}\n    {err}")
    return proc


def ensure_root() -> None:
    if os.geteuid() != 0:
        if shutil.which("sudo", path=SUBPROCESS_ENV["PATH"]) is None:
            die("Root privileges required and sudo is not installed.")
        argv = ["sudo", "--", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
        os.execvp("sudo", argv)


# =============================================================================
# LOCKING & SIGNALS
# =============================================================================
_LOCK_FD: int | None = None
_LOCK_DEPTH = 0


@contextmanager
def dusky_lock(*, wait: bool = True) -> Iterator[None]:
    global _LOCK_FD, _LOCK_DEPTH
    if _LOCK_DEPTH > 0:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return

    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(RUN_DIR, 0o700)
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        die(f"Cannot create lock {LOCK_PATH}: {exc}")

    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if not wait:
                die("Another Dusky operation holds the lock. Refusing to queue.")
            holder = ""
            with suppress(OSError):
                holder = os.pread(fd, 64, 0).decode(errors="replace").strip()
            warn(f"Waiting for Dusky lock (held by pid {holder or '?'})...")
            fcntl.flock(fd, fcntl.LOCK_EX)
        acquired = True
        with suppress(OSError):
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        _LOCK_FD, _LOCK_DEPTH = fd, 1
        yield
    finally:
        if acquired:
            _LOCK_DEPTH = max(0, _LOCK_DEPTH - 1)
        if _LOCK_DEPTH == 0:
            _LOCK_FD = None
            with suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        with suppress(OSError):
            if _LOCK_FD is None:
                os.close(fd)


@contextmanager
def critical_section() -> Iterator[None]:
    blocked = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGPIPE}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def fsync_path(path: Path, *, is_dir: bool = False) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC | (os.O_DIRECTORY if is_dir else 0)
    with suppress(OSError):
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_file_durable(path: Path, content: str, mode: int = 0o644) -> None:
    tmp = path.with_name(f".{path.name}.dusky-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, mode)
    try:
        os.write(fd, content.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    fsync_path(path.parent, is_dir=True)


# =============================================================================
# MOUNT & SUBVOLUME UTILITIES
# =============================================================================
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def is_mountpoint(path: Path) -> bool:
    return run("mountpoint", "-q", "--", str(path), check=False).returncode == 0


def path_is_subvolume(path: Path) -> bool:
    return run("btrfs", "subvolume", "show", "--", str(path), check=False).returncode == 0


def get_subvolume_id(path: Path) -> int | None:
    proc = run("btrfs", "subvolume", "show", "--", str(path), check=False)
    if proc.returncode != 0:
        return None
    match = re.search(r"Subvolume ID:\s+(\d+)", proc.stdout)
    return int(match.group(1)) if match else None


def get_subvolume_name_from_mount(path: Path) -> str | None:
    proc = run("btrfs", "subvolume", "show", "--", str(path), check=False)
    if proc.returncode != 0:
        return None
    lines = proc.stdout.splitlines()
    if lines:
        name = lines[0].strip()
        if name and not name.startswith("Subvolume"):
            return name.strip("/")
    match = re.search(r"^\s*Name:\s+(.+)$", proc.stdout, re.MULTILINE)
    return match.group(1).strip() if match else None


def sweep_stale_mounts() -> None:
    if not MNT_ROOT.is_dir():
        return
    for child in sorted(MNT_ROOT.iterdir()):
        match = MOUNT_DIR_RE.fullmatch(child.name)
        if not child.is_dir() or match is None:
            continue
        if _pid_alive(int(match.group("pid"))):
            continue
        if is_mountpoint(child):
            if run("umount", "--", str(child), check=False).returncode != 0:
                run("umount", "--lazy", "--", str(child), check=False)
        with suppress(OSError):
            child.rmdir()


def _ensure_private_mnt_root() -> None:
    MNT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(MNT_ROOT, 0o700)
    if not is_mountpoint(MNT_ROOT):
        run("mount", "--bind", str(MNT_ROOT), str(MNT_ROOT))
    run("mount", "--make-rprivate", str(MNT_ROOT))


@contextmanager
def top_level(fs_uuid: str) -> Iterator[Path]:
    if not UUID_RE.match(fs_uuid):
        die(f"Refusing to mount malformed filesystem UUID {fs_uuid!r}.")

    _ensure_private_mnt_root()
    sweep_stale_mounts()

    mnt = Path(tempfile.mkdtemp(prefix=f"top_{os.getpid()}_", dir=str(MNT_ROOT)))
    opts = "subvolid=5,nodev,nosuid,noexec,noatime"
    src = f"UUID={fs_uuid}"

    mounted = run("mount", "-t", "btrfs", "-o", opts, src, str(mnt), check=False, timeout=60.0)
    if mounted.returncode != 0:
        with suppress(OSError):
            mnt.rmdir()
        die(f"Failed to mount subvolid=5 for UUID={fs_uuid}:\n    {mounted.stderr}")

    try:
        seen_uuid = get_mount_info(mnt).get("uuid", "")
        if seen_uuid and seen_uuid != fs_uuid:
            die(f"REFUSING TO CONTINUE: mounted UUID={seen_uuid} but expected UUID={fs_uuid}.")
        sid = get_subvolume_id(mnt)
        if sid != BTRFS_FS_TREE_OBJECTID:
            die(f"{mnt} reports subvolume id {sid}, not 5. Refusing to operate.")
        yield mnt
    finally:
        detached = False
        for attempt in range(5):
            if run("umount", "--", str(mnt), check=False).returncode == 0:
                detached = True
                break
            time.sleep(0.2 * (attempt + 1))
        if not detached:
            run("umount", "--lazy", "--", str(mnt), check=False)
        with suppress(OSError):
            mnt.rmdir()


def get_mount_info(target: Path) -> dict[str, str]:
    proc = run("findmnt", "-T", str(target), "--json", "-o", "SOURCE,TARGET,FSTYPE,OPTIONS,UUID", check=False)
    if proc.returncode != 0 or not proc.stdout:
        return {}
    try:
        data = json.loads(proc.stdout)
        filesystems = data.get("filesystems", [])
        if not filesystems:
            return {}
        fs = filesystems[-1]
        return {
            "source": str(fs.get("source") or ""),
            "target": str(fs.get("target") or ""),
            "fstype": str(fs.get("fstype") or ""),
            "options": str(fs.get("options") or ""),
            "uuid": str(fs.get("uuid") or ""),
        }
    except (json.JSONDecodeError, KeyError, IndexError):
        return {}


def clean_mount_opts(opts: str) -> str:
    parts = opts.split(",")
    kept = []
    for opt in parts:
        opt_str = opt.strip()
        if opt_str.startswith(("subvol=", "subvolid=", "ro")):
            continue
        if opt_str:
            kept.append(opt_str)
    return ",".join(kept)


def derive_subvol_name(target_path: Path) -> str:
    clean_parts = [p for p in target_path.parts if p not in ("/", "\\")]
    subvol_name = "@" + "_".join(clean_parts)
    if not SAFE_NAME_RE.fullmatch(subvol_name):
        subvol_name = "@" + re.sub(r"[^A-Za-z0-9_.-]", "_", "_".join(clean_parts))
    return subvol_name


def datetime_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def update_fstab_add(fs_uuid: str, mountpoint: Path, subvol_name: str, base_opts: str) -> None:
    fstab_path = Path("/etc/fstab")
    cleaned_opts = clean_mount_opts(base_opts)
    if cleaned_opts:
        cleaned_opts += ","
    mount_opts = f"{cleaned_opts}subvol=/{subvol_name.lstrip('/')}"
    canonical_target = str(mountpoint.resolve())

    newline = f"UUID={fs_uuid} {canonical_target} btrfs {mount_opts} 0 0"

    content = fstab_path.read_text(encoding="utf-8", errors="replace").splitlines()
    new_lines = []
    replaced = False

    for line in content:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            mp = parts[1].rstrip("/") or "/"
            if mp == canonical_target:
                if not replaced:
                    new_lines.append(newline)
                    replaced = True
                continue
        new_lines.append(line)

    if not replaced:
        new_lines.append(newline)

    full_text = "\n".join(new_lines) + "\n"

    tmp_fstab = fstab_path.with_name(".fstab.dusky-tmp")
    tmp_fstab.write_text(full_text, encoding="utf-8")
    os.chmod(tmp_fstab, 0o644)

    val_proc = run("findmnt", "--verify", "--tab-file", str(tmp_fstab), check=False)
    tmp_fstab.unlink(missing_ok=True)
    if val_proc.returncode != 0:
        raise ConversionError(f"Generated fstab failed findmnt validation:\n{val_proc.stderr}")

    stamp = datetime_stamp()
    backup_fstab = fstab_path.with_name(f"fstab.bak.{stamp}")
    shutil.copy2(fstab_path, backup_fstab)

    write_file_durable(fstab_path, full_text, 0o644)
    run("systemctl", "daemon-reload")
    info(f"Updated /etc/fstab for {canonical_target}")


def update_fstab_remove(mountpoint: Path) -> None:
    fstab_path = Path("/etc/fstab")
    canonical_target = str(mountpoint.resolve())

    content = fstab_path.read_text(encoding="utf-8", errors="replace").splitlines()
    new_lines = []
    removed = False

    for line in content:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            mp = parts[1].rstrip("/") or "/"
            if mp == canonical_target:
                removed = True
                continue
        new_lines.append(line)

    if not removed:
        return

    full_text = "\n".join(new_lines) + "\n"

    tmp_fstab = fstab_path.with_name(".fstab.dusky-tmp")
    tmp_fstab.write_text(full_text, encoding="utf-8")
    os.chmod(tmp_fstab, 0o644)

    val_proc = run("findmnt", "--verify", "--tab-file", str(tmp_fstab), check=False)
    tmp_fstab.unlink(missing_ok=True)
    if val_proc.returncode != 0:
        raise ConversionError(f"Generated fstab failed findmnt validation after entry removal:\n{val_proc.stderr}")

    stamp = datetime_stamp()
    backup_fstab = fstab_path.with_name(f"fstab.bak.{stamp}")
    shutil.copy2(fstab_path, backup_fstab)

    write_file_durable(fstab_path, full_text, 0o644)
    run("systemctl", "daemon-reload")
    info(f"Removed /etc/fstab entry for {canonical_target}")


# =============================================================================
# CONVERT ENGINE
# =============================================================================
def convert_directory(target_path: Path, custom_subvol_name: str | None = None) -> None:
    target_path = target_path.resolve()
    info(f"Inspecting target directory: {target_path}")

    if not target_path.exists():
        die(f"Target path does not exist: {target_path}")
    if not target_path.is_dir():
        die(f"Target path is not a directory: {target_path}")
    if is_mountpoint(target_path):
        die(f"Target path {target_path} is already a mount point.")
    if path_is_subvolume(target_path):
        die(f"Target path {target_path} is already a Btrfs subvolume.")

    stat_info = target_path.stat()
    uid, gid, mode = stat_info.st_uid, stat_info.st_gid, stat_info.st_mode & 0o7777

    mnt_info = get_mount_info(target_path)
    if not mnt_info or mnt_info.get("fstype") != "btrfs":
        die(f"Target path {target_path} is on fstype={mnt_info.get('fstype')!r}, not Btrfs.")

    fs_uuid = mnt_info["uuid"]
    if not fs_uuid or not UUID_RE.match(fs_uuid):
        die(f"Could not resolve valid Btrfs filesystem UUID for {target_path}.")

    subvol_name = custom_subvol_name or derive_subvol_name(target_path)
    if not SAFE_NAME_RE.fullmatch(subvol_name):
        die(f"Invalid subvolume name: {subvol_name!r}. Must match @[A-Za-z0-9_.-]+")

    if any(pat in subvol_name for pat in TRANSIENT_PATTERNS):
        die(f"Subvolume name {subvol_name!r} collides with Dusky transient grammar.")

    info(f"Filesystem UUID : {fs_uuid}")
    info(f"Subvolume Name  : {subvol_name}")
    info(f"Target Directory: {target_path}")

    subvol_created = False
    backup_dir: Path | None = None

    with dusky_lock():
        with top_level(fs_uuid) as top_dir:
            target_subvol = top_dir / subvol_name.lstrip("/")
            if target_subvol.exists():
                die(f"Top-level subvolume {subvol_name} already exists on FS_TREE.")

            info(f"Creating top-level subvolume {subvol_name}...")
            run("btrfs", "subvolume", "create", "--", str(target_subvol))
            subvol_created = True

            os.chown(target_subvol, uid, gid)
            os.chmod(target_subvol, mode)

            entries = list(target_path.iterdir())
            if entries:
                info(f"Migrating {len(entries)} item(s) into {subvol_name} (cp -a)...")
                run("cp", "-a", "--", *[str(e) for e in entries], str(target_subvol))

            run("btrfs", "filesystem", "sync", str(top_dir))

        try:
            with critical_section():
                stamp = datetime_stamp()
                backup_dir = target_path.with_name(f"{target_path.name}.bak.{stamp}")
                info(f"Renaming original directory to {backup_dir.name}...")
                target_path.rename(backup_dir)

                target_path.mkdir(parents=True, exist_ok=True, mode=mode)
                os.chown(target_path, uid, gid)

                update_fstab_add(fs_uuid, target_path, subvol_name, mnt_info["options"])
                info(f"Mounting {target_path}...")
                run("mount", str(target_path))

            if not is_mountpoint(target_path):
                raise ConversionError(f"Failed to mount {target_path} after updating /etc/fstab.")

            mounted_subvol_id = get_subvolume_id(target_path)
            if mounted_subvol_id == BTRFS_FS_TREE_OBJECTID:
                raise ConversionError(f"{target_path} mounted as subvolid=5 instead of {subvol_name}.")

            info(f"Mounted subvol=/{subvol_name} (id {mounted_subvol_id}) at {target_path}.")
            info("Cleaning up temporary directory backup...")
            if backup_dir and backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

            good(f"[+] SUCCESS: Converted {target_path} into top-level subvolume {subvol_name}.")
        except Exception as exc:
            warn(f"Error during activation/mount: {exc}. Rolling back changes...")
            with suppress(Exception):
                if is_mountpoint(target_path):
                    run("umount", str(target_path), check=False)
                if target_path.exists():
                    shutil.rmtree(target_path, ignore_errors=True)
                if backup_dir and backup_dir.exists():
                    backup_dir.rename(target_path)

                if subvol_created:
                    with top_level(fs_uuid) as top_dir:
                        sub_path = top_dir / subvol_name.lstrip("/")
                        if sub_path.exists():
                            run("btrfs", "subvolume", "delete", "--", str(sub_path), check=False)

            raise


# =============================================================================
# REVERT / UNDO ENGINE
# =============================================================================
def revert_directory(target_path: Path) -> None:
    target_path = target_path.resolve()
    info(f"Inspecting target directory for UNDO: {target_path}")

    if not target_path.exists():
        die(f"Target path does not exist: {target_path}")
    if not is_mountpoint(target_path):
        die(f"Target path {target_path} is NOT currently a mount point; nothing to undo.")
    if not path_is_subvolume(target_path):
        die(f"Target path {target_path} is not a Btrfs subvolume.")

    subvol_id = get_subvolume_id(target_path)
    if subvol_id == BTRFS_FS_TREE_OBJECTID:
        die(f"Refusing to undo {target_path}: it is subvolid=5.")

    subvol_name = get_subvolume_name_from_mount(target_path)
    if not subvol_name:
        die(f"Could not resolve subvolume name for mount {target_path}.")

    if subvol_name in PROTECTED_SUBVOLUMES:
        die(f"Refusing to undo protected subvolume {subvol_name!r}.")

    mnt_info = get_mount_info(target_path)
    fs_uuid = mnt_info.get("uuid", "")
    if not fs_uuid or not UUID_RE.match(fs_uuid):
        die(f"Could not resolve valid Btrfs filesystem UUID for {target_path}.")

    stat_info = target_path.stat()
    uid, gid, mode = stat_info.st_uid, stat_info.st_gid, stat_info.st_mode & 0o7777

    info(f"Filesystem UUID : {fs_uuid}")
    info(f"Subvolume Name  : {subvol_name} (id {subvol_id})")
    info(f"Target Directory: {target_path}")

    stamp = datetime_stamp()
    tmp_backup = target_path.with_name(f"{target_path.name}.tmp_undo_{stamp}")

    with dusky_lock():
        info("Extracting subvolume contents into temporary buffer...")
        entries = list(target_path.iterdir())
        if entries:
            tmp_backup.mkdir(parents=True, exist_ok=True, mode=mode)
            os.chown(tmp_backup, uid, gid)
            run("cp", "-a", "--", *[str(e) for e in entries], str(tmp_backup))
        else:
            tmp_backup.mkdir(parents=True, exist_ok=True, mode=mode)
            os.chown(tmp_backup, uid, gid)

        with critical_section():
            info(f"Unmounting {target_path}...")
            run("umount", str(target_path))
            update_fstab_remove(target_path)

            # Replace empty mountpoint dir with restored contents
            shutil.rmtree(target_path, ignore_errors=True)
            tmp_backup.rename(target_path)
            os.chown(target_path, uid, gid)
            os.chmod(target_path, mode)

        info(f"Deleting top-level subvolume {subvol_name} from FS_TREE...")
        with top_level(fs_uuid) as top_dir:
            target_subvol = top_dir / subvol_name.lstrip("/")
            if target_subvol.exists():
                run("btrfs", "subvolume", "delete", "--", str(target_subvol))
            run("btrfs", "filesystem", "sync", str(top_dir))

    good(f"[+] UNDO COMPLETE: {target_path} is now a standard directory in /home.")


def main() -> None:
    ensure_root()

    parser = argparse.ArgumentParser(
        description="Convert a regular directory into an isolated top-level Btrfs subvolume, or UNDO a conversion."
    )
    parser.add_argument("path", type=Path, help="Directory path to convert or undo")
    parser.add_argument("-n", "--name", help="Custom top-level subvolume name (e.g., @home_user_dir)")
    parser.add_argument("-u", "--undo", action="store_true", help="Undo conversion: return subvolume to normal directory")

    args = parser.parse_args()

    try:
        if args.undo:
            revert_directory(args.path)
        else:
            convert_directory(args.path, custom_subvol_name=args.name)
    except ConversionError as exc:
        die(str(exc))


if __name__ == "__main__":
    main()

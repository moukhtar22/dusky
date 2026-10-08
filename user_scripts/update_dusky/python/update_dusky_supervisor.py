#!/usr/bin/env python3
from __future__ import annotations

import errno
import fcntl
import hashlib
from contextlib import suppress
import json
import os
from pathlib import Path
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

if sys.version_info < (3, 14):
    raise SystemExit("Dusky supervisor requires Python 3.14+")

SCRIPT = Path(__file__).with_name("update_dusky.py").resolve()
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "dusky-updater-supervisor"
KNOWN = STATE / "known_good"
PREVIOUS = STATE / "known_good.previous"
CONTROL = STATE / "control"
MANIFEST = KNOWN / "manifest.json"
ROLLBACK_JOURNAL = STATE / "rollback_pending.json"
REJECTED = STATE / "rejected_candidate.json"
REJECTED_TTL_SEC = 7 * 24 * 3600
_SUPERVISOR_LOCK_FD: int | None = None
_SUPERVISION_FINISHED = False


def recover_lock_conflict(fd: int, *, grace: float = 15.0) -> bool:
    """Offer interactive recovery; retain the inode and acquire its actual flock.

    Identify ownership from the kernel, never from the stale PID in a file.
    pidfds pin process identities throughout confirmation and termination.
    """
    def acquire() -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def owner() -> int | None:
        st = os.fstat(fd)
        candidates: set[int] = set()
        for line in Path('/proc/locks').read_text().splitlines():
            fields = line.split()
            if len(fields) < 8 or fields[1:4] != ['FLOCK', 'ADVISORY', 'WRITE']:
                continue
            inode = int(fields[5].rsplit(':', 1)[1])
            if inode == st.st_ino:
                pid = int(fields[4])
                if pid > 0:
                    candidates.add(pid)

        def holds_lock(pid: int) -> bool:
            # Btrfs reports the superblock device in /proc/locks, whereas
            # stat() reports the subvolume device. Compare stat identities on
            # both descriptors, then require a lock on that exact descriptor.
            proc = Path('/proc') / str(pid)
            for link in proc.joinpath('fd').glob('*'):
                try:
                    actual = link.stat()
                    if (actual.st_dev, actual.st_ino) != (st.st_dev, st.st_ino):
                        continue
                    info = proc.joinpath('fdinfo', link.name).read_text()
                    for line in info.splitlines():
                        fields = line.split()
                        if (len(fields) >= 9 and fields[0] == 'lock:'
                                and fields[2:5] == ['FLOCK', 'ADVISORY', 'WRITE']
                                and int(fields[6].rsplit(':', 1)[1]) == st.st_ino):
                            return True
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    continue
            return False

        for pid in sorted(candidates):
            if holds_lock(pid):
                return pid
        # A flock can survive its acquiring process through an inherited fd.
        # If its recorded PID vanished, inspect only live updater processes.
        for proc in Path('/proc').iterdir():
            if proc.name.isdecimal():
                pid = int(proc.name)
                if pid not in candidates and is_updater(pid) and holds_lock(pid):
                    return pid
        return None

    def is_updater(pid: int) -> bool:
        proc = Path('/proc') / str(pid)
        try:
            if proc.stat().st_uid != os.getuid():
                return False
            args = proc.joinpath('cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError):
            return False
        scripts = {str(SCRIPT), str(Path(__file__).resolve())}
        return len(args) > 1 and os.fsdecode(args[1]) in scripts

    def confirm(message: str) -> bool:
        try:
            return input(message).strip().lower() in {'y', 'yes'}
        except (EOFError, KeyboardInterrupt):
            return False

    handles: dict[int, int] = {}
    stopped: set[int] = set()

    def freeze_process(pid: int, handle: int) -> None:
        signal.pidfd_send_signal(handle, signal.SIGSTOP)
        stopped.add(pid)
        deadline = time.monotonic() + 2.0
        while alive(handle):
            try:
                status = (Path('/proc') / str(pid) / 'status').read_text()
            except (FileNotFoundError, ProcessLookupError):
                return
            state = next(line.split()[1] for line in status.splitlines() if line.startswith('State:'))
            if state in {'T', 't'}:
                return
            if time.monotonic() >= deadline:
                raise OSError(f'PID {pid} cannot be stopped; refusing concurrent recovery')
            time.sleep(0.01)

    def capture(pid: int, expected_parent: int | None = None, freeze: bool = False) -> None:
        if pid in handles:
            return
        try:
            handle = os.pidfd_open(pid)
        except ProcessLookupError:
            return
        if expected_parent is not None:
            try:
                status = (Path('/proc') / str(pid) / 'status').read_text()
                actual_parent = int(next(line.split()[1] for line in status.splitlines() if line.startswith('PPid:')))
                if actual_parent != expected_parent or not alive(handle):
                    os.close(handle)
                    return
            except (FileNotFoundError, ProcessLookupError):
                os.close(handle)
                return
        handles[pid] = handle
        if freeze and alive(handle):
            freeze_process(pid, handle)
        # Tasks can launch from any thread, not just the process leader.
        for path in (Path('/proc') / str(pid) / 'task').glob('*/children'):
            try:
                children = path.read_text().split()
            except FileNotFoundError:
                continue
            for child in children:
                capture(int(child), pid, freeze)

    def alive(handle: int) -> bool:
        return not select.select([handle], [], [], 0)[0]

    def wait_all(seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while any(alive(handle) for handle in handles.values()):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    try:
        if acquire():
            return True
        print('[INFO] Dusky Updater is already updating.', file=sys.stderr)
        if not sys.stdin.isatty():
            return False
        pid = owner()
        if pid is None:
            print('[WARN] Cannot identify the live lock owner; retry after it exits.', file=sys.stderr)
            return acquire()
        capture(pid)
        if pid not in handles or not alive(handles[pid]) or owner() != pid:
            return acquire()
        if not is_updater(pid):
            print('[WARN] Lock owner is not a verified updater; it will not be stopped.', file=sys.stderr)
            return False
        # When a direct launch encounters a supervised worker, wait for its
        # parent too: rollback must finish before a replacement update starts.
        status = (Path('/proc') / str(pid) / 'status').read_text()
        parent = int(next(line.split()[1] for line in status.splitlines() if line.startswith('PPid:')))
        if parent > 1 and is_updater(parent):
            capture(parent)
        if not confirm(f'Stop existing updater PID {pid} and retry? [y/N] '):
            return False
        if not alive(handles[pid]) or owner() != pid:
            return wait_all(grace) and acquire()
        # Signal workers first, allowing Textual to cancel/reap task groups
        # and the supervisor to finish its rollback normally.
        workers = [p for p in handles if alive(handles[p]) and is_updater(p)
                   and str(SCRIPT).encode() in (Path('/proc') / str(p) / 'cmdline').read_bytes().split(b'\0')]
        for worker in workers or [pid]:
            signal.pidfd_send_signal(handles[worker], signal.SIGTERM)
        if not wait_all(grace):
            if not confirm('Updater or its tasks are still running. Force kill them? An interrupted update may need repair. [y/N] '):
                return False
            # Stop the tree before refreshing it: a hung task must not fork
            # another mutating child between discovery and force termination.
            for p, handle in handles.items():
                if alive(handle):
                    freeze_process(p, handle)
            for p, handle in list(handles.items()):
                if alive(handle):
                    for path in (Path('/proc') / str(p) / 'task').glob('*/children'):
                        with suppress(FileNotFoundError):
                            for child in path.read_text().split():
                                capture(int(child), p, freeze=True)
            # Task processes first, then workers; give the supervisor a chance
            # to reap the killed worker and complete candidate rollback.
            order = [p for p in handles if p not in workers and p != pid and p != parent]
            order += workers
            order += [p for p in (pid, parent) if p in handles and p not in order]
            for p in order:
                handle = handles[p]
                if alive(handle):
                    if p not in workers and is_updater(p):
                        signal.pidfd_send_signal(handle, signal.SIGCONT)
                        stopped.discard(p)
                        if wait_all(5.0):
                            break
                    with suppress(ProcessLookupError):
                        signal.pidfd_send_signal(handle, signal.SIGKILL)
            if not wait_all(5.0):
                print('[WARN] Some tasks remain alive; recovery cannot proceed.', file=sys.stderr)
                return False
        return acquire()
    except (OSError, ValueError, StopIteration) as e:
        print(f'[WARN] Recovery could not complete: {e}. Existing lock retained.', file=sys.stderr)
        return False
    finally:
        for p in stopped:
            with suppress(OSError):
                signal.pidfd_send_signal(handles[p], signal.SIGCONT)
        for handle in handles.values():
            os.close(handle)


def digest(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode):
            return None
    except OSError:
        return None
    h = hashlib.blake2b(digest_size=16)
    try:
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def copy_durable(src: Path, dst: Path) -> None:
    st = src.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeError(f"known-good bundle member must be a regular file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=False)
    fd = os.open(str(dst), os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_dir(dst.parent)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def read_manifest(root: Path = KNOWN) -> dict[str, Any] | None:
    data = read_json(root / "manifest.json")
    if not data or data.get("schema") != 2:
        return None
    files = data.get("files")
    if not isinstance(files, list) or not files:
        return None
    seen: set[str] = set()
    for rec in files:
        if not isinstance(rec, dict):
            return None
        kind = rec.get("kind")
        installed = rec.get("installed")
        saved = rec.get("saved")
        expected = rec.get("digest")
        present = rec.get("present", True)
        if kind not in {"script", "profile", "settings"} or kind in seen:
            return None
        if not isinstance(installed, str) or not installed or not Path(installed).is_absolute():
            return None
        if not isinstance(present, bool):
            return None
        if present:
            if not isinstance(saved, str) or not saved or "/" in saved or "\\" in saved:
                return None
            if not isinstance(expected, str) or not expected:
                return None
            src = root / saved
            if digest(src) != expected:
                return None
        else:
            if kind != "settings" or saved not in ("", None) or expected not in ("", None):
                return None
        seen.add(kind)
    if "script" not in seen or "profile" not in seen:
        return None
    return data


def bundle_valid(root: Path) -> bool:
    return read_manifest(root) is not None


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
        fsync_dir(path.parent)


def recover_supervisor_state() -> None:
    ensure_private_dir(STATE)
    # If publication was interrupted between KNOWN -> PREVIOUS and tmp -> KNOWN,
    # PREVIOUS is a complete durable generation and is preferable to guessing.
    if not bundle_valid(KNOWN) and bundle_valid(PREVIOUS):
        if KNOWN.exists():
            corrupt = STATE / f"known_good.corrupt.{int(time.time())}"
            os.replace(KNOWN, corrupt)
            fsync_dir(STATE)
        os.replace(PREVIOUS, KNOWN)
        fsync_dir(STATE)
    elif bundle_valid(KNOWN) and PREVIOUS.exists():
        _remove_tree(PREVIOUS)

    if ROLLBACK_JOURNAL.exists():
        if not bundle_valid(KNOWN) or not restore_known_good():
            raise RuntimeError(
                f"an interrupted rollback could not be completed; recovery data retained at {KNOWN}"
            )


def _health_members(health: dict[str, Any]) -> list[tuple[str, Path, str | None]]:
    """Validate the worker health bundle and return snapshot members.

    The updater script and active profile are mandatory regular files.

    The settings file is intentionally optional: the worker supports a
    settings-free configuration and reports that state as
    ``settings_digest=None``. Absence is valid only when the pathname truly
    does not exist. An existing non-regular settings object is not equivalent
    to absence and is rejected.

    Validation is race-aware: after lstat() establishes the expected object
    type, digest() reopens and hashes the file. If the object disappears or
    changes type between those operations, digest() returns None and the health
    checkpoint is rejected rather than blessing an ambiguous bundle.
    """
    if health.get("schema") != 1:
        raise RuntimeError("unsupported worker health schema")

    launch_id = health.get("launch_id")
    if not isinstance(launch_id, str) or not launch_id or "\x00" in launch_id:
        raise RuntimeError("worker health missing or malformed launch_id")

    members: list[tuple[str, Path, str | None]] = []

    for key in ("script", "profile", "settings"):
        raw_path = health.get(key)
        claimed_digest = health.get(f"{key}_digest")

        if (
            not isinstance(raw_path, str)
            or not raw_path
            or "\x00" in raw_path
        ):
            raise RuntimeError(f"worker health missing or malformed {key} pathname")

        path = Path(raw_path)
        if not path.is_absolute():
            raise RuntimeError(f"worker health {key} pathname is not absolute: {path}")

        if claimed_digest is not None and (
            not isinstance(claimed_digest, str) or not claimed_digest
        ):
            raise RuntimeError(f"worker health malformed {key} digest")

        try:
            st = path.lstat()
        except FileNotFoundError:
            if key == "settings" and claimed_digest is None:
                members.append((key, path, None))
                continue

            raise RuntimeError(
                f"worker health required file is missing: {path}"
            ) from None
        except OSError as exc:
            raise RuntimeError(
                f"worker health cannot inspect {key}: {path}: {exc}"
            ) from exc

        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError(
                f"worker health {key} is not a regular file: {path}"
            )

        actual_digest = digest(path)
        if actual_digest is None:
            raise RuntimeError(
                f"worker health cannot read stable {key} contents: {path}"
            )

        if claimed_digest is None:
            raise RuntimeError(
                f"worker health missing digest for existing {key}: {path}"
            )

        if actual_digest != claimed_digest:
            raise RuntimeError(
                f"worker health {key} changed before supervisor snapshot: {path}"
            )

        members.append((key, path, actual_digest))

    return members


def publish_known_good(health: dict[str, Any]) -> None:
    ensure_private_dir(STATE)
    members = _health_members(health)
    tmp = Path(tempfile.mkdtemp(prefix="known_good.", dir=str(STATE)))
    os.chmod(tmp, 0o700)
    try:
        files: list[dict[str, str]] = []
        for key, src, expected in members:
            if expected is None:
                files.append(
                    {"kind": key, "installed": str(src), "saved": "", "digest": "", "present": False}
                )
                continue
            name = f"{key}{src.suffix or '.dat'}"
            dst = tmp / name
            copy_durable(src, dst)
            saved_digest = digest(dst)
            if saved_digest != expected:
                raise RuntimeError(f"known-good snapshot verification failed for {key}")
            files.append(
                {"kind": key, "installed": str(src), "saved": name, "digest": expected, "present": True}
            )
        payload = {
            "schema": 2,
            "published": time.time(),
            "launch_id": health.get("launch_id", ""),
            "work_tree": str(health.get("work_tree", "")),
            "git_dir": str(health.get("git_dir", "")),
            "files": files,
        }
        atomic_json_write(tmp / "manifest.json", payload)
        fsync_dir(tmp)

        # Two-generation atomic publication. A crash at every rename boundary is
        # recoverable by recover_supervisor_state().
        if PREVIOUS.exists():
            _remove_tree(PREVIOUS)
        if KNOWN.exists():
            os.replace(KNOWN, PREVIOUS)
            fsync_dir(STATE)
        os.replace(tmp, KNOWN)
        fsync_dir(STATE)
        if not bundle_valid(KNOWN):
            raise RuntimeError("published known-good bundle failed self-verification")
        if PREVIOUS.exists():
            _remove_tree(PREVIOUS)
    except BaseException:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        # Restore the previous durable generation immediately whenever the new
        # publication is absent or fails verification. Startup recovery performs
        # the same operation after a process/power interruption.
        if not bundle_valid(KNOWN) and bundle_valid(PREVIOUS):
            if KNOWN.exists():
                failed = STATE / f"known_good.failed.{time.time_ns()}"
                os.replace(KNOWN, failed)
                fsync_dir(STATE)
            os.replace(PREVIOUS, KNOWN)
            fsync_dir(STATE)
        raise


def installed_signature(manifest: dict[str, Any] | None = None) -> list[dict[str, str]]:
    manifest = manifest or read_manifest(KNOWN)
    if not manifest:
        return []
    result: list[dict[str, str]] = []
    for rec in manifest["files"]:
        installed = Path(rec["installed"])
        result.append(
            {
                "kind": str(rec["kind"]),
                "installed": str(installed),
                "digest": digest(installed) or "",
            }
        )
    return result


def remember_rejected_candidate() -> None:
    files = installed_signature()
    if not files:
        return
    atomic_json_write(
        REJECTED,
        {
            "schema": 1,
            "rejected_epoch": time.time(),
            "expires_epoch": time.time() + REJECTED_TTL_SEC,
            "files": files,
        },
    )


def _fsync_path(path: Path) -> bool:
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            fsync_dir(path.parent)
            return True
        if stat.S_ISREG(st.st_mode):
            fd = os.open(str(path), os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            fsync_dir(path.parent)
            return True
        if stat.S_ISDIR(st.st_mode):
            for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
                root_p = Path(root)
                for name in files:
                    fp = root_p / name
                    fst = fp.lstat()
                    if stat.S_ISREG(fst.st_mode):
                        fd = os.open(str(fp), os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                for name in dirs:
                    dp = root_p / name
                    if not stat.S_ISLNK(dp.lstat().st_mode):
                        fsync_dir(dp)
                fsync_dir(root_p)
            fsync_dir(path.parent)
            return True
    except OSError:
        return False
    return False


def _preserve_conflict(path: Path, index: int) -> Path:
    conflict_root = STATE / "rollback_conflicts" / str(time.time_ns())
    ensure_private_dir(conflict_root)
    conflict = conflict_root / f"{index}_{path.name}"
    try:
        os.replace(path, conflict)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        shutil.move(str(path), str(conflict))
    if not _fsync_path(conflict):
        raise RuntimeError(f"could not make rejected candidate recovery durable: {conflict}")
    fsync_dir(path.parent)
    fsync_dir(conflict_root)
    return conflict


def restore_known_good() -> bool:
    data = read_manifest(KNOWN)
    if not data:
        return False
    staged: list[tuple[Path, Path, str]] = []
    try:
        ensure_private_dir(STATE)
        atomic_json_write(
            ROLLBACK_JOURNAL,
            {"schema": 1, "started_epoch": time.time(), "next_index": 0, "count": len(data["files"])},
        )
        missing_targets: list[Path] = []
        for rec in data["files"]:
            dst = Path(rec["installed"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not rec.get("present", True):
                missing_targets.append(dst)
                continue
            src = KNOWN / rec["saved"]
            if digest(src) != rec["digest"]:
                raise RuntimeError(f"known-good payload failed verification: {src}")
            fd, temp_name = tempfile.mkstemp(prefix=f".{dst.name}.recover.", dir=str(dst.parent))
            os.close(fd)
            tmp = Path(temp_name)
            copy_durable(src, tmp)
            if digest(tmp) != rec["digest"]:
                raise RuntimeError(f"staged rollback payload failed verification: {dst}")
            staged.append((tmp, dst, rec["digest"]))

        total_ops = len(staged) + len(missing_targets)
        for index, (tmp, dst, expected) in enumerate(staged):
            try:
                st = dst.lstat()
            except FileNotFoundError:
                st = None
            if st is not None:
                current = digest(dst)
                # Preserve every displaced non-known-good object, including a
                # locally edited regular file, symlink, or unexpected directory.
                if current != expected or not stat.S_ISREG(st.st_mode):
                    _preserve_conflict(dst, index)
            os.replace(tmp, dst)
            fsync_dir(dst.parent)
            if digest(dst) != expected:
                raise RuntimeError(f"rollback verification failed: {dst}")
            atomic_json_write(
                ROLLBACK_JOURNAL,
                {"schema": 1, "started_epoch": time.time(), "next_index": index + 1, "count": total_ops},
            )

        for offset, dst in enumerate(missing_targets, start=len(staged)):
            try:
                st = dst.lstat()
            except FileNotFoundError:
                st = None
            if st is not None:
                _preserve_conflict(dst, offset)
            atomic_json_write(
                ROLLBACK_JOURNAL,
                {"schema": 1, "started_epoch": time.time(), "next_index": offset + 1, "count": total_ops},
            )

        ROLLBACK_JOURNAL.unlink(missing_ok=True)
        fsync_dir(STATE)
        return installed_matches_known()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        for tmp, _, _ in staged:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def installed_matches_known() -> bool:
    data = read_manifest(KNOWN)
    if not data:
        return False
    for rec in data["files"]:
        actual = digest(Path(rec["installed"]))
        if rec.get("present", True):
            if actual != rec["digest"]:
                return False
        else:
            try:
                Path(rec["installed"]).lstat()
            except FileNotFoundError:
                pass
            except OSError:
                return False
            else:
                return False
    return True


def run_once(argv: list[str]) -> tuple[int, int]:
    global CONTROL, _SUPERVISOR_LOCK_FD, _SUPERVISION_FINISHED
    # Completed report windows can coexist with the next update. Give each
    # worker private health/completion files so old windows cannot consume or
    # delete another run's acknowledgements.
    control_root = STATE / "control"
    ensure_private_dir(control_root)
    CONTROL = Path(tempfile.mkdtemp(prefix="run-", dir=control_root))
    _SUPERVISION_FINISHED = False
    env = os.environ.copy()
    env["DUSKY_SUPERVISOR_CONTROL_DIR"] = str(CONTROL)
    env["DUSKY_SUPERVISOR_STATE_DIR"] = str(STATE)
    env["DUSKY_SUPERVISOR_FINISH_PROTOCOL"] = "1"
    proc = subprocess.Popen([sys.executable, str(SCRIPT), *argv], env=env)
    last_launch = ""
    healthy_count = 0
    finish_rc = 0
    try:
        while proc.poll() is None:
            if _SUPERVISION_FINISHED:
                return proc.wait() or finish_rc, healthy_count
            health = read_json(CONTROL / "health.json")
            if isinstance(health, dict):
                launch_id = health.get("launch_id")
                if isinstance(launch_id, str) and launch_id and launch_id != last_launch:
                    try:
                        publish_known_good(health)
                        ack = CONTROL / f"ack_{launch_id}"
                        fd = os.open(
                            str(ack),
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                            0o600,
                        )
                        os.close(fd)
                        fsync_dir(CONTROL)
                    except Exception as e:
                        print(f"[FATAL] cannot publish durable known-good bundle: {e}", file=sys.stderr)
                        with suppress(OSError, ProcessLookupError):
                            proc.send_signal(signal.SIGTERM)
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        return 70, healthy_count
                    last_launch = launch_id
                    healthy_count += 1
            finished = read_json(CONTROL / "finished.json")
            if (last_launch and isinstance(finished, dict)
                    and finished.get("schema") == 1
                    and finished.get("launch_id") == last_launch
                    and finished.get("pid") == proc.pid):
                # Finish rollback while still serialized. Once ownership is
                # released this supervisor only waits for its report window;
                # it must never inspect/restore a later updater's candidate.
                finish_rc = _finish_candidate(0)
                _SUPERVISION_FINISHED = True
                if _SUPERVISOR_LOCK_FD is not None:
                    os.close(_SUPERVISOR_LOCK_FD)
                    _SUPERVISOR_LOCK_FD = None
                (CONTROL / f"finished_ack_{last_launch}").touch(mode=0o600)
            time.sleep(0.05)
        return proc.wait() or finish_rc, healthy_count
    except KeyboardInterrupt:
        try:
            proc.send_signal(signal.SIGINT)
            return proc.wait(timeout=10), healthy_count
        except Exception:
            proc.kill()
            proc.wait()
            return 130, healthy_count
    finally:
        shutil.rmtree(CONTROL, ignore_errors=True)


def _finish_candidate(rc: int) -> int:
    # Roll back only when the installed bundle differs from the last durable
    # startup-health acknowledgement. Ordinary child-task failure after a health
    # checkpoint does not trigger updater rollback.
    if bundle_valid(KNOWN) and not installed_matches_known():
        print(
            "[WARN] candidate did not reach a durable startup-health checkpoint; "
            "restoring the previous known-good bundle",
            file=sys.stderr,
        )
        try:
            remember_rejected_candidate()
        except Exception as e:
            print(f"[WARN] could not record rejected candidate identity: {e}", file=sys.stderr)
        if not restore_known_good():
            print(
                f"[FATAL] automatic rollback failed; recovery bundle retained at {KNOWN}",
                file=sys.stderr,
            )
            return rc if rc != 0 else 70
        # Do not immediately relaunch: doing so can fetch/select the exact same
        # rejected candidate and create a rollback loop. A later normal launch
        # can accept a different candidate; the worker blocks the recorded bad
        # bundle for a bounded seven-day rejection window.
        print(
            "[WARN] known-good bundle restored; rejected candidate will not be relaunched automatically",
            file=sys.stderr,
        )
        return rc if rc != 0 else 75
    return rc


def _run_supervised() -> int:
    # A preview must remain filesystem-read-only, including supervisor state.
    if "--dry-run" in sys.argv[1:]:
        if not SCRIPT.is_file():
            print(f"[FATAL] updater not found: {SCRIPT}", file=sys.stderr)
            return 1
        return subprocess.run([sys.executable, str(SCRIPT), *sys.argv[1:]], check=False).returncode

    try:
        recover_supervisor_state()
    except Exception as e:
        print(f"[FATAL] supervisor recovery failed: {e}", file=sys.stderr)
        return 70

    # A between-run bundle mismatch can be either an interrupted activation or
    # an intentional local edit. Do not overwrite a runnable edited bundle just
    # because it differs from the previous generation: launch it as a candidate
    # and require it to reach the startup-health checkpoint. If it fails before
    # health, the post-run rollback below restores known-good while preserving
    # the displaced candidate in rollback_conflicts.
    #
    # A missing/non-regular updater cannot be launched to prove itself, so that
    # specific case must restore known-good before process creation.
    if digest(SCRIPT) is None:
        if bundle_valid(KNOWN):
            print(
                "[WARN] updater is missing or non-regular; restoring known-good generation before launch",
                file=sys.stderr,
            )
            try:
                remember_rejected_candidate()
            except Exception as e:
                print(f"[WARN] could not record rejected candidate identity: {e}", file=sys.stderr)
            if not restore_known_good():
                print(
                    f"[FATAL] startup rollback failed; recovery bundle retained at {KNOWN}",
                    file=sys.stderr,
                )
                return 70
        else:
            print(f"[FATAL] updater not found and no recoverable known-good script exists: {SCRIPT}", file=sys.stderr)
            return 1

    if digest(SCRIPT) is None:
        print(f"[FATAL] updater is still unavailable after recovery: {SCRIPT}", file=sys.stderr)
        return 1

    rc, _healthy = run_once(sys.argv[1:])

    if _SUPERVISION_FINISHED or (rc == 0 and _healthy == 0):
        return rc
    return _finish_candidate(rc)


def main() -> int:
    global _SUPERVISOR_LOCK_FD
    # Informational commands must not publish or restore an unrelated bundle.
    # The worker remains responsible for full argument validation.
    passthrough = {"--dry-run", "--help", "-h", "--version", "--doctor", "--list", "--list-once", "--forget-once"}
    if any(arg in passthrough for arg in sys.argv[1:]):
        env = os.environ.copy()
        env.pop("DUSKY_SUPERVISOR_CONTROL_DIR", None)
        env.pop("DUSKY_SUPERVISOR_STATE_DIR", None)
        return subprocess.run([sys.executable, str(SCRIPT), *sys.argv[1:]], env=env, check=False).returncode

    # The worker's runtime lock is acquired too late to protect supervisor
    # recovery/control files. Serialize the complete launch/publication/rollback
    # lifecycle, including the interval after the worker exits.
    ensure_private_dir(STATE)
    fd = os.open(str(STATE / "supervisor.lock"), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    _SUPERVISOR_LOCK_FD = fd
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if not recover_lock_conflict(fd):
                return 0
        return _run_supervised()
    finally:
        if _SUPERVISOR_LOCK_FD is not None:
            os.close(_SUPERVISOR_LOCK_FD)
            _SUPERVISOR_LOCK_FD = None


if __name__ == "__main__":
    raise SystemExit(main())

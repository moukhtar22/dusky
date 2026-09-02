#!/usr/bin/env python3
"""Dusky Core Runner — strict hybrid-aware CPU-affinity launcher.

Target : Arch Linux (rolling 2026+), Linux 7.x, Python 3.14.6+
Deps   : util-linux taskset, python-rich

Contract
--------
    core_runner.py [-h|--help]
    core_runner.py [-s|--status]
    core_runner.py [-i|--interactive] [-t|--type pcores|ecores|all]
                   [-c|--custom CPULIST] [-d|--detach] [--] COMMAND [ARGS...]

    CPULIST grammar (kernel/taskset compatible): N | N-N | N-N:S, comma-joined
Priority: help -> status -> (no command => TUI on foreground tty, else error)
          -> -c -> -t -> saved profile -> checklist -> P-core fallback.

Guarantees
----------
* argv in, argv out: the target runs under taskset with shell=False.
  NOTE: util-linux taskset has no end-of-options marker; a COMMAND whose
  name begins with '-' must be invoked by its path (leading '/' stops getopt).
* Leading NAME=VALUE assignments are moved into the child environment.
* Topology detection never toggles CPUs; only explicit launches may wake or
  sleep cores, and exactly the initially-offline subset is restored afterwards.
* Persistent state lives under XDG dirs (0700 dirs, 0600 files), symlink-safe
  atomic writes, machine-id stored only as a SHA-256 digest.
* Detached runs are double-forked daemons acknowledging spawn status over a
  pipe and recording the final exit code under XDG_STATE_HOME .../jobs/.

Legacy compatibility
--------------------
Settings from older releases (bare {"name":[cores]} maps or the versioned
{"version":2,"profiles":{...}} shape) load transparently and upgrade to
version 3 on the next save. The world-readable /var/tmp topology cache of old
releases is never read.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import re
import select
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import termios
import time
import tty
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final, Literal

if sys.version_info < (3, 14, 6):
    sys.stderr.write("core_runner: requires Python 3.14.6+\n")
    raise SystemExit(1)

try:
    from rich import box
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.prompt import Confirm
    from rich.table import Table
except ModuleNotFoundError as exc:
    if exc.name != "rich":
        raise
    sys.stderr.write("core_runner: python-rich missing (pacman -S python-rich)\n")
    raise SystemExit(1) from exc


type Kind = Literal["P", "E"]
type TypeChoice = Literal["pcores", "ecores", "all"]
type Json = dict[str, Any]

SYS_CPU: Final = Path("/sys/devices/system/cpu")
MAX_CPU_ID: Final = 1_048_575
CACHE_VERSION: Final = 4
SETTINGS_VERSION: Final = 3
JOB_RECORD_VERSION: Final = 1
MAX_DATA_BYTES: Final = 1_048_576
HELPER_TIMEOUT_S: Final = 15.0
SETTLE_DEADLINE_S: Final = 3.0
ACK_TIMEOUT_S: Final = 15.0
WATCHED_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM, signal.SIGQUIT, signal.SIGHUP)

_SPEC: Final = re.compile(r"^(0|[1-9][0-9]*)(?:-(0|[1-9][0-9]*)(?::([1-9][0-9]*))?)?$")
_ENV_ASSIGN: Final = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.ASCII)


class RunnerError(RuntimeError):
    """User-facing failure with a controlled exit path."""


class UsageError(RunnerError):
    """Bad CLI usage; exits 2."""


class Cancelled(Exception):
    """Interactive cancellation (q / Esc / Ctrl-C); exits 130."""


# ---------------------------------------------------------------------------
# Paths (XDG Base Directory Spec 0.8)
# ---------------------------------------------------------------------------
def xdg(var: str, fallback: str) -> Path:
    raw = os.environ.get(var)
    if raw and Path(raw).is_absolute():
        return Path(raw)
    return Path.home() / fallback


CONFIG_DIR: Final = xdg("XDG_CONFIG_HOME", ".config") / "dusky" / "settings"
CACHE_DIR: Final = xdg("XDG_CACHE_HOME", ".cache") / "dusky" / "core_runner"
STATE_DIR: Final = xdg("XDG_STATE_HOME", ".local/state") / "dusky" / "core_runner"
SETTINGS_FILE: Final = CONFIG_DIR / "core_runner"
SETTINGS_LOCK: Final = CONFIG_DIR / ".core_runner.lock"
CACHE_FILE: Final = CACHE_DIR / "topology.json"
JOBS_DIR: Final = STATE_DIR / "jobs"

console: Final = Console(highlight=False, soft_wrap=False)
err_console: Final = Console(stderr=True, highlight=False, soft_wrap=False)


def say(msg: str) -> None:
    console.print(msg)


def warn(msg: str) -> None:
    err_console.print(f"[yellow]core:[/yellow] {msg}")


def fail(msg: str) -> None:
    err_console.print(f"[bold red]core:[/bold red] {msg}")


# ---------------------------------------------------------------------------
# Hardened filesystem primitives
# ---------------------------------------------------------------------------
def private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    meta = path.lstat()
    if stat.S_ISLNK(meta.st_mode) or not stat.S_ISDIR(meta.st_mode):
        raise RunnerError(f"refusing unsafe directory: {path}")
    if meta.st_uid != os.getuid():
        raise RunnerError(f"directory not owned by this user: {path}")
    if stat.S_IMODE(meta.st_mode) != 0o700:
        path.chmod(0o700)


def secure_read(path: Path) -> str | None:
    try:
        meta = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        warn(f"cannot inspect {path}: {exc.strerror}")
        return None
    if stat.S_ISLNK(meta.st_mode) or not stat.S_ISREG(meta.st_mode):
        warn(f"refusing non-regular data file: {path}")
        return None
    if meta.st_uid != os.getuid():
        warn(f"data file not owned by this user: {path}")
        return None
    if meta.st_size > MAX_DATA_BYTES:
        warn(f"data file unexpectedly large: {path}")
        return None
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            return stream.read(MAX_DATA_BYTES + 1)
    except OSError as exc:
        warn(f"cannot read {path}: {exc}")
        return None


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    private_dir(path.parent)
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    tmp = f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        fd = os.open(tmp,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                     mode, dir_fd=dir_fd)
        try:
            os.write(fd, text.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        sync_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(sync_fd)
        finally:
            os.close(sync_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(dir_fd)


def atomic_write_json(path: Path, payload: Json) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


@contextmanager
def settings_lock():
    private_dir(CONFIG_DIR)
    fd = os.open(SETTINGS_LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def sysfs_text(path: Path) -> str | None:
    try:
        if path.is_file():
            return text if (text := path.read_text(encoding="utf-8", errors="replace").strip()) else None
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# CPU-list grammar
# ---------------------------------------------------------------------------
def parse_cpu_list(spec: str) -> list[int]:
    if not spec or spec.startswith(",") or spec.endswith(",") or ",," in spec:
        raise UsageError(f"invalid CPU list: {spec!r}")
    out: set[int] = set()
    for token in spec.split(","):
        if (match := _SPEC.fullmatch(token)) is None:
            raise UsageError(f"invalid CPU list token: {token!r} (examples: 3  0-7  0-10:2)")
        start = int(match.group(1))
        if match.group(2) is None:
            end, step = start, 1
        else:
            end = int(match.group(2))
            step = int(match.group(3)) if match.group(3) else 1
        if step < 1:
            raise UsageError(f"stride must be >= 1: {token!r}")
        if end < start:
            raise UsageError(f"inverted CPU range: {token!r}")
        if end > MAX_CPU_ID:
            raise UsageError(f"CPU id exceeds {MAX_CPU_ID}: {token!r}")
        out.update(range(start, end + 1, step))
    if not out:
        raise UsageError(f"empty CPU list: {spec!r}")
    return sorted(out)


def format_cpu_list(ids: Any) -> str:
    ordered = sorted({int(i) for i in ids})
    if not ordered:
        return ""
    parts: list[str] = []
    lo = prev = ordered[0]
    for cpu in ordered[1:]:
        if cpu == prev + 1:
            prev = cpu
            continue
        parts.append(str(lo) if lo == prev else f"{lo}-{prev}")
        lo = prev = cpu
    parts.append(str(lo) if lo == prev else f"{lo}-{prev}")
    return ",".join(parts)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class Profile:
    label: str
    cores: tuple[int, ...]


def _coerce_cores(raw: Any) -> tuple[int, ...] | None:
    if not isinstance(raw, list):
        return None
    try:
        cores = tuple(sorted({int(c) for c in raw}))
    except (TypeError, ValueError):
        return None
    if not cores or any(c < 0 or c > MAX_CPU_ID for c in cores):
        return None
    return cores


def profiles_payload(profiles: dict[str, Profile]) -> Json:
    return {
        "version": SETTINGS_VERSION,
        "profiles": {
            name: {"label": p.label, "cores": sorted(set(p.cores))}
            for name, p in sorted(profiles.items())
        },
    }


def load_profiles() -> dict[str, Profile]:
    raw = secure_read(SETTINGS_FILE)
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        warn(f"profile settings unreadable ({exc}); starting empty")
        return {}
    if not isinstance(data, dict):
        warn("profile settings have an unknown shape; starting empty")
        return {}

    entries: Any = data.get("profiles") if isinstance(data.get("profiles"), dict) else data
    profiles: dict[str, Profile] = {}
    for name, value in entries.items():
        label = Path(str(name)).name
        coerced: tuple[int, ...] | None = None
        if isinstance(value, dict):
            if isinstance(value.get("label"), str):
                label = value["label"]
            coerced = _coerce_cores(value.get("cores"))
        elif isinstance(value, list):
            coerced = _coerce_cores(value)
        if coerced is None:
            warn(f"skipping malformed profile entry: {name!r}")
            continue
        profiles[str(name)] = Profile(label=label, cores=coerced)
    return profiles


def mutate_profiles(action: Callable[[dict[str, Profile]], None]) -> None:
    with settings_lock():
        profiles = load_profiles()
        action(profiles)
        atomic_write_json(SETTINGS_FILE, profiles_payload(profiles))


def sanitize_profile(name: str, profile: Profile, topo: Topology) -> list[int]:
    valid = [c for c in profile.cores if c in topo]
    stale = [c for c in profile.cores if c not in topo]
    if stale:
        warn(f"profile '{profile.label}' drops stale CPUs {format_cpu_list(stale)}")
        try:
            if valid:
                repaired = Profile(profile.label, tuple(valid))
                mutate_profiles(lambda ps: ps.__setitem__(name, repaired))
            else:
                mutate_profiles(lambda ps: ps.pop(name, None))
        except (RunnerError, OSError) as exc:
            warn(f"could not repair profile: {exc}")
    return valid


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class Core:
    kind: Kind
    online: bool
    smt_group: tuple[int, ...]
    source: str


Topology = dict[int, Core]


def present_ids() -> list[int]:
    raw = sysfs_text(SYS_CPU / "present")
    if raw is None:
        fail(f"cannot read {SYS_CPU}/present")
        raise SystemExit(1)
    try:
        return parse_cpu_list(raw)
    except UsageError:
        fail(f"kernel returned unparsable present list: {raw!r}")
        raise SystemExit(1)


def online_mask() -> frozenset[int]:
    raw = sysfs_text(SYS_CPU / "online")
    if raw is None:
        return frozenset(present_ids())
    try:
        return frozenset(parse_cpu_list(raw))
    except UsageError:
        return frozenset()


def system_signature(present: list[int]) -> str:
    model = next(
        (line.split(":", 1)[1].strip()
         for line in (sysfs_text(Path("/proc/cpuinfo")) or "").splitlines()
         if line.startswith("model name")),
        "unknown",
    )
    machine_id = (sysfs_text(Path("/etc/machine-id"))
                  or sysfs_text(Path("/var/lib/dbus/machine-id")) or "")
    blob = "\0".join((
        model,
        platform.release(),
        ",".join(map(str, present)),
        hashlib.sha256(machine_id.encode()).hexdigest(),
    ))
    return hashlib.sha256(blob.encode()).hexdigest()


def _propagate(split: dict[int, Kind], cpu_ids: list[int],
               smt: dict[int, tuple[int, ...]]) -> dict[int, Kind] | None:
    filled = dict(split)
    for cpu in cpu_ids:
        if cpu in filled:
            continue
        inherited = next((filled[s] for s in smt.get(cpu, ()) if s in filled), None)
        if inherited is None:
            return None
        filled[cpu] = inherited
    return filled


def _max_split(values: dict[int, int]) -> dict[int, Kind] | None:
    distinct = set(values.values())
    if len(distinct) < 2:
        return None
    peak = max(distinct)
    return {cpu: ("P" if val == peak else "E") for cpu, val in values.items()}


def _read_int_attr(cpu: int, *parts: str) -> int | None:
    raw = sysfs_text(SYS_CPU / f"cpu{cpu}" / "/".join(parts))
    return int(raw) if raw is not None and raw.isdigit() else None


def classify_cpus(cpu_ids: list[int], smt: dict[int, tuple[int, ...]]) -> tuple[dict[int, Kind], str]:
    pmu_p = sysfs_text(Path("/sys/bus/event_source/devices/cpu_core/cpus"))
    pmu_e = sysfs_text(Path("/sys/bus/event_source/devices/cpu_atom/cpus"))
    if pmu_p and pmu_e:
        try:
            p_set, e_set = set(parse_cpu_list(pmu_p)), set(parse_cpu_list(pmu_e))
            if p_set and e_set and not (p_set & e_set):
                split = {c: ("P" if c in p_set else "E")
                         for c in cpu_ids if c in p_set or c in e_set}
                if split and (filled := _propagate(split, cpu_ids, smt)):
                    return filled, "intel-hybrid-pmu"
        except UsageError:
            pass

    probes: tuple[tuple[Callable[[int], int | None], str], ...] = (
        (lambda c: _read_int_attr(c, "cpu_capacity"), "scheduler-capacity"),
        (lambda c: _read_int_attr(c, "acpi_cppc", "highest_perf"), "acpi-cppc-highest-perf"),
    )
    for reader, source in probes:
        values = {c: v for c in cpu_ids if (v := reader(c)) is not None}
        if (split := _max_split(values)) and (filled := _propagate(split, cpu_ids, smt)):
            return filled, source

    named: dict[int, Kind] = {}
    p_names = {"intel_core", "intelcore", "core", "0x40", "64"}
    e_names = {"intel_atom", "intelatom", "atom", "0x20", "32"}
    for cpu in cpu_ids:
        token = (sysfs_text(SYS_CPU / f"cpu{cpu}" / "topology" / "core_type") or "").strip().lower()
        if token in p_names:
            named[cpu] = "P"
        elif token in e_names:
            named[cpu] = "E"
    if len(named) == len(cpu_ids) and {"P", "E"} <= set(named.values()):
        return named, "experimental-core-type"

    freq_values = {
        c: v for c in cpu_ids
        if (v := _read_int_attr(c, "cpufreq", "cpuinfo_max_freq")
            or _policy_freq(c)) is not None
    }
    if (split := _max_split(freq_values)) and (filled := _propagate(split, cpu_ids, smt)):
        return filled, "maximum-frequency"

    sizes = {c: len(smt.get(c, (c,))) for c in cpu_ids}
    if min(sizes.values()) == 1 and max(sizes.values()) > 1:
        return ({c: ("P" if sizes[c] > 1 else "E") for c in cpu_ids}, "smt-heuristic")

    return {c: "P" for c in cpu_ids}, "homogeneous"


def _policy_freq(cpu: int) -> int | None:
    raw = sysfs_text(SYS_CPU / "cpufreq" / f"policy{cpu}" / "cpuinfo_max_freq")
    if raw is None or not raw.isdigit():
        return None
    return int(raw)


def topology_from_cache(signature: str, present: list[int]) -> Topology | None:
    raw = secure_read(CACHE_FILE)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        warn("topology cache corrupt; rebuilding")
        CACHE_FILE.unlink(missing_ok=True)
        return None
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        return None
    if data.get("signature") != signature:
        return None
    cached = data.get("topology")
    if not isinstance(cached, dict) or not cached:
        CACHE_FILE.unlink(missing_ok=True)
        return None
    live_online = online_mask()
    topo: Topology = {}
    for key, item in cached.items():
        try:
            cpu = int(key)
            group = tuple(sorted({int(s) for s in item["smt_group"]}))
            kind = item["kind"]
        except (KeyError, TypeError, ValueError):
            warn("topology cache malformed; rebuilding")
            CACHE_FILE.unlink(missing_ok=True)
            return None
        if kind not in ("P", "E"):
            warn("topology cache invalid; rebuilding")
            CACHE_FILE.unlink(missing_ok=True)
            return None
        source = str(data.get("source") or item.get("source") or "cached")
        topo[cpu] = Core(kind=kind, online=cpu in live_online, smt_group=group, source=source)
    if set(topo) != set(present):
        return None
    return topo


def detect_topology() -> Topology:
    present = present_ids()
    signature = system_signature(present)
    if (cached := topology_from_cache(signature, present)) is not None:
        return cached

    live_online = online_mask()
    smt: dict[int, tuple[int, ...]] = {}
    for cpu in present:
        raw = sysfs_text(SYS_CPU / f"cpu{cpu}" / "topology" / "core_cpus_list")
        try:
            group = tuple(parse_cpu_list(raw)) if raw else (cpu,)
        except UsageError:
            group = (cpu,)
        smt[cpu] = group or (cpu,)

    kinds, source = classify_cpus(present, smt)
    topo: Topology = {
        cpu: Core(kind=kinds[cpu], online=cpu in live_online, smt_group=smt[cpu], source=source)
        for cpu in present
    }
    if not (any(c.kind == "P" for c in topo.values()) and any(c.kind == "E" for c in topo.values())):
        topo = {cpu: Core("P", c.online, c.smt_group, "homogeneous-failsafe")
                for cpu, c in topo.items()}
        source = "homogeneous-failsafe"

    try:
        atomic_write_json(CACHE_FILE, {
            "version": CACHE_VERSION,
            "signature": signature,
            "source": source,
            "topology": {
                str(cpu): {"kind": c.kind, "smt_group": list(c.smt_group)}
                for cpu, c in sorted(topo.items())
            },
        })
    except (RunnerError, OSError) as exc:
        warn(f"topology cache not saved: {exc}")
    return topo


# ---------------------------------------------------------------------------
# Privileged hotplug
# ---------------------------------------------------------------------------
HELPER_FIXED: Final = Path("/usr/local/libexec/dusky-core-helper")


def helper_path() -> Path:
    override = os.environ.get("CORE_HELPER_PATH")
    if override:
        return Path(override)
    try:
        meta = HELPER_FIXED.lstat()
        if (stat.S_ISREG(meta.st_mode) and meta.st_uid == 0
                and meta.st_mode & 0o111 and not meta.st_mode & 0o022):
            return HELPER_FIXED
    except OSError:
        pass
    return Path(__file__).resolve().parent / "core_helper.py"


def change_core_state(cpu_ids: list[int], *, online: bool) -> bool:
    wanted = sorted({int(c) for c in cpu_ids})
    if not wanted:
        return True
    helper = helper_path()
    if not helper.is_file():
        fail(f"hotplug helper missing: {helper}")
        return False
    flag = "--online" if online else "--offline"
    spec = ",".join(map(str, wanted))
    if os.geteuid() == 0:
        argv = [sys.executable, str(helper), flag, spec]
    else:
        sudo = shutil.which("sudo")
        if sudo is None:
            fail("sudo is not installed; cannot change CPU state")
            return False
        argv = [sudo, "-n", "--", sys.executable, str(helper), flag, spec]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=HELPER_TIMEOUT_S, check=False,
                              stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        fail(f"cannot execute hotplug command: {argv[0]}")
        return False
    except subprocess.TimeoutExpired:
        fail(f"hotplug helper exceeded its {HELPER_TIMEOUT_S:.0f}s deadline")
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if "password" in detail.lower() or "sudoers" in detail.lower():
            fail("sudo requires a password (non-interactive mode). Run 'sudo -v' "
                 "first, install the helper root-owned at "
                 "/usr/local/libexec/dusky-core-helper, or add a NOPASSWD rule.")
        elif detail:
            fail(detail[:800])
        else:
            fail(f"helper exited {proc.returncode}")
        return False

    deadline = time.monotonic() + SETTLE_DEADLINE_S
    delay = 0.01
    while True:
        state = online_mask()
        if all((cpu in state) == online for cpu in wanted):
            return True
        if time.monotonic() >= deadline:
            break
        time.sleep(delay)
        delay = min(delay * 1.5, 0.1)
    stuck = [str(c) for c in wanted if (c in online_mask()) != online]
    fail(f"CPUs {','.join(stuck)} did not reach the requested state in time")
    return False


# ---------------------------------------------------------------------------
# Command preparation
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class PreparedCommand:
    argv: tuple[str, ...]
    env: dict[str, str]
    profile_key: str
    label: str


def prepare_command(command: list[str]) -> PreparedCommand:
    if not command:
        raise UsageError("no target command provided")
    env = os.environ.copy()
    index = 0
    while index < len(command):
        if (assign := _ENV_ASSIGN.match(command[index])) is None:
            break
        env[assign.group(1)] = assign.group(2)
        index += 1
    if index == len(command):
        raise UsageError("environment assignments given without an executable")

    head = os.path.expanduser(command[index])
    if "/" in head:
        candidate = Path(head)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise UsageError(f"target not found: {head}") from exc
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise UsageError(f"target is not executable: {resolved}")
        executable = str(resolved)
    else:
        found = shutil.which(head, path=env.get("PATH"))
        if found is None:
            raise UsageError(f"command not found on PATH: {head}")
        executable = found

    return PreparedCommand(
        argv=(executable, *command[index + 1 :]),
        env=env,
        profile_key=Path(executable).name,
        label=Path(executable).name,
    )


# ---------------------------------------------------------------------------
# Terminal plumbing
# ---------------------------------------------------------------------------
def interactive_ready() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        return os.tcgetpgrp(sys.stdin.fileno()) == os.getpgrp()
    except OSError:
        return False


class KeyReader:
    def __init__(self) -> None:
        self._fd = sys.stdin.fileno()
        self._saved: list[Any] | None = None

    def __enter__(self) -> "KeyReader":
        if not interactive_ready():
            raise RunnerError("interactive screens need a foreground terminal")
        self._saved = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._saved is not None:
            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            except termios.error:
                pass

    def key(self) -> str:
        chunk = os.read(self._fd, 1).decode("utf-8", errors="ignore")
        if chunk == "\x1b" and select.select([self._fd], [], [], 0.05)[0]:
            chunk += os.read(self._fd, 6).decode("utf-8", errors="ignore")
            return chunk
        if chunk == "g" and select.select([self._fd], [], [], 0.12)[0]:
            follow = os.read(self._fd, 1).decode("utf-8", errors="ignore")
            return "gg" if follow == "g" else chunk
        return chunk


def menu_select(options: list[str], title: str, subtitle: str) -> int:
    if not options:
        return -1
    idx = 0

    def panel() -> Panel:
        table = Table(box=None, show_header=False, padding=(0, 1))
        for i, opt in enumerate(options):
            style = "bold cyan reverse" if i == idx else "white"
            table.add_row(">" if i == idx else " ", opt, style=style)
        return Panel(table, title=title, subtitle=f"[dim]{subtitle}[/dim]",
                     border_style="cyan", expand=False)

    with KeyReader() as keys, Live(panel(), console=console, refresh_per_second=20,
                                   transient=True) as live:
        while True:
            match keys.key():
                case "\r" | "\n":
                    return idx
                case "\x1b[A" | "k" | "h":
                    idx = (idx - 1) % len(options)
                case "\x1b[B" | "j" | "l":
                    idx = (idx + 1) % len(options)
                case "gg":
                    idx = 0
                case "G":
                    idx = len(options) - 1
                case "\x15":
                    idx = max(0, idx - 5)
                case "\x04":
                    idx = min(len(options) - 1, idx + 5)
                case "q" | "Q" | "\x03" | "\x1b":
                    raise Cancelled
            live.update(panel())


def checklist(topo: Topology, subject: str) -> list[int]:
    cpus = sorted(topo)
    chosen: set[int] = {c for c, info in topo.items() if info.kind == "P"} or set(cpus)
    idx = 0

    def panel() -> Panel:
        table = Table(box=box.SIMPLE_HEAVY, show_edge=False)
        table.add_column("", justify="center", width=2)
        table.add_column("CPU", justify="right")
        table.add_column("Kind", justify="center")
        table.add_column("State", justify="center")
        table.add_column("SMT group")
        for i, cpu in enumerate(cpus):
            info = topo[cpu]
            kind = "[bold cyan]P[/bold cyan]" if info.kind == "P" else "[bold magenta]E[/bold magenta]"
            state = "[green]online[/green]" if info.online else "[dim red]offline[/dim red]"
            row_style = "reverse" if i == idx else ""
            table.add_row(
                ">" if i == idx else "",
                str(cpu),
                kind,
                state,
                format_cpu_list(info.smt_group),
                style=row_style,
            )
        return Panel(
            table,
            title=f"[bold white]Affinity for[/bold white] [bold yellow]{subject}[/bold yellow]",
            subtitle="[dim]Space toggle · Enter confirm · a all · p/e class · "
                     "j/k/h/l/gg/G/C-u/C-d · q quit[/dim]",
            border_style="cyan",
        )

    with KeyReader() as keys, Live(panel(), console=console, refresh_per_second=20,
                                   transient=True) as live:
        while True:
            key = keys.key()
            match key:
                case "\r" | "\n":
                    break
                case " ":
                    chosen ^= {cpus[idx]}
                case "\x1b[A" | "k" | "h":
                    idx = max(0, idx - 1)
                case "\x1b[B" | "j" | "l":
                    idx = min(len(cpus) - 1, idx + 1)
                case "gg":
                    idx = 0
                case "G":
                    idx = len(cpus) - 1
                case "\x15":
                    idx = max(0, idx - 5)
                case "\x04":
                    idx = min(len(cpus) - 1, idx + 5)
                case "a":
                    chosen = set() if len(chosen) == len(cpus) else set(cpus)
                case "p" | "e":
                    group = {c for c, i in topo.items() if i.kind == key.upper()}
                    if group <= chosen:
                        chosen -= group
                    else:
                        chosen |= group
                case "q" | "Q" | "\x03" | "\x1b":
                    raise Cancelled
            live.update(panel())
    return sorted(chosen)


def flush_stdin() -> None:
    if sys.stdin.isatty():
        try:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except termios.error:
            pass


def confirm(question: str, *, default: bool) -> bool:
    if not interactive_ready():
        return default
    flush_stdin()
    try:
        return bool(Confirm.ask(question, default=default, console=console))
    except (KeyboardInterrupt, EOFError) as exc:
        raise Cancelled from exc


def read_line(prompt: str) -> str:
    flush_stdin()
    console.print(prompt, end="")
    try:
        return input().strip()
    except (KeyboardInterrupt, EOFError) as exc:
        raise Cancelled from exc


def pause() -> None:
    try:
        input("\nPress Enter to continue...")
    except (KeyboardInterrupt, EOFError):
        pass


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def show_status(topo: Topology) -> None:
    table = Table(title="CPU Topology", box=box.SIMPLE_HEAVY, header_style="bold magenta")
    table.add_column("CPU", justify="right")
    table.add_column("Kind", justify="center")
    table.add_column("State", justify="center")
    table.add_column("SMT group")
    table.add_column("Detected via")
    sources = {info.source for info in topo.values()}
    source_label = next(iter(sources)) if len(sources) == 1 else "mixed"
    for cpu, info in sorted(topo.items()):
        kind = "[bold cyan]P[/bold cyan]" if info.kind == "P" else "[bold magenta]E[/bold magenta]"
        state = "[green]online[/green]" if info.online else "[dim red]offline[/dim red]"
        table.add_row(str(cpu), kind, state, format_cpu_list(info.smt_group), info.source)
    console.print(table)
    err_console.print(f"[dim]detection: {source_label}[/dim]")


def show_help() -> None:
    console.print(Panel("[bold green]Dusky Core Runner[/bold green]",
                        border_style="green", expand=False))
    usage = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    usage.add_row("[bold]core[/bold]", "[dim]interactive launcher (foreground tty)[/dim]")
    usage.add_row("[bold]core[/bold] [white]-s[/white]", "[dim]topology status[/dim]")
    usage.add_row("[bold]core[/bold] [white]<cpulist> <cmd>…[/white]",
                  "[dim]pin to CPUs: 0-3 · 0,2,4-7 · 0-10:2[/dim]")
    usage.add_row("[bold]core[/bold] [white]-- <cmd>…[/white]",
                  "[dim]escape hatch: next token is the command[/dim]")
    console.print("\n[bold yellow]Usage[/bold yellow]", usage)

    flags = Table(show_header=True, header_style="bold magenta", box=box.SIMPLE)
    flags.add_column("Flag")
    flags.add_column("Effect")
    flags.add_row("-h, --help", "this dashboard (exit 0)")
    flags.add_row("-s, --status", "print topology table and exit")
    flags.add_row("-i, --interactive", "ignore saved profile; force the checklist")
    flags.add_row("-t, --type pcores|ecores|all", "select by detected hybrid class")
    flags.add_row("-c, --custom CPULIST", "explicit pin (unions with positional specs)")
    flags.add_row("-d, --detach",
                  "daemonize; reports spawn result, final exit recorded in jobs/")
    flags.add_row("--", "everything after this is the command, verbatim")
    console.print("\n[bold yellow]Flags[/bold yellow]", flags)
    console.print(
        "\n[bold yellow]Notes[/bold yellow]\n"
        f"[dim]Profiles : {SETTINGS_FILE}\n"
        f"Cache    : {CACHE_FILE}\n"
        f"Jobs     : {JOBS_DIR}/\n"
        f"Helper   : {helper_path()}   (override: CORE_HELPER_PATH)\n\n"
        "Hotplug uses `sudo -n`; pre-authenticate with `sudo -v`, install the\n"
        "helper root-owned at /usr/local/libexec/dusky-core-helper, or add a\n"
        "NOPASSWD sudoers rule for it.\n"
        "taskset has no end-of-options marker: launch dash-named binaries by path.[/dim]"
    )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def normalized_rc(code: int) -> int:
    return 128 - code if code < 0 else code


def run_attached(argv: list[str], env: dict[str, str], restore: list[int]) -> int:
    holder: dict[str, subprocess.Popen[Any] | None] = {"proc": None}
    saved: dict[signal.Signals, Any] = {}

    def forward(signum: int, _frame: object) -> None:
        proc = holder["proc"]
        if proc is not None and proc.poll() is None:
            try:
                proc.send_signal(signum)
            except ProcessLookupError:
                pass

    try:
        try:
            holder["proc"] = proc = subprocess.Popen(argv, shell=False, env=env)
        except FileNotFoundError as exc:
            fail(f"execution failed: {exc}")
            return 127
        except PermissionError as exc:
            fail(f"execution failed: {exc}")
            return 126
        except OSError as exc:
            fail(f"execution failed: {exc}")
            return 1
        for sig in WATCHED_SIGNALS:
            saved[sig] = signal.getsignal(sig)
            signal.signal(sig, forward)
        return normalized_rc(proc.wait())
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
        if restore:
            warn(f"restoring initially-offline CPUs {format_cpu_list(restore)}")
            change_core_state(restore, online=False)


def _daemon_fail(ack_w: int, base: Json, path: Path, code: int, error: str) -> None:
    write_job_record(path, base | {"state": "launch-failed", "error": error})
    os.write(ack_w, struct.pack("<II", code, 0))
    os.close(ack_w)
    os._exit(code)


def run_detached(argv: list[str], env: dict[str, str], restore: list[int], label: str) -> int:
    try:
        private_dir(JOBS_DIR)
    except (RunnerError, OSError) as exc:
        fail(str(exc))
        return 1

    ack_r, ack_w = os.pipe2(os.O_CLOEXEC)
    try:
        middle = os.fork()
    except OSError as exc:
        os.close(ack_r)
        os.close(ack_w)
        fail(f"could not fork detached monitor: {exc}")
        return 1

    if middle > 0:
        os.close(ack_w)
        payload = bytearray()
        outcome = 1
        try:
            while len(payload) < 8:
                if not select.select([ack_r], [], [], ACK_TIMEOUT_S)[0]:
                    break
                chunk = os.read(ack_r, 8 - len(payload))
                if not chunk:
                    break
                payload.extend(chunk)
            if len(payload) == 8:
                code, child_pid = struct.unpack("<II", bytes(payload))
                outcome = code
                if code == 0:
                    err_console.print(
                        f"[green]detached[/green] pid {child_pid} — final exit: "
                        f"{job_path(child_pid)}"
                    )
                else:
                    fail(f"detached spawn failed (exit {code})")
            else:
                fail(f"detached monitor did not acknowledge within {ACK_TIMEOUT_S:.0f}s")
        finally:
            os.close(ack_r)
            os.waitpid(middle, 0)
        return outcome

    os.close(ack_r)
    try:
        os.setsid()
    except OSError:
        pass
    if os.fork() > 0:
        os._exit(0)

    daemon_pid = os.getpid()
    record_path = job_path(daemon_pid)
    log_path = JOBS_DIR / f"{daemon_pid}.log"
    base: Json = {
        "version": JOB_RECORD_VERSION,
        "label": label,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "affinity": argv[2] if len(argv) > 2 else "",
        "log": str(log_path),
    }

    try:
        devnull = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
        os.dup2(devnull, 0)
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(devnull)
        os.close(log_fd)
    except OSError as exc:
        _daemon_fail(ack_w, base, record_path, 1, f"log setup failed: {exc}")

    try:
        proc = subprocess.Popen(argv, shell=False, env=env)
    except FileNotFoundError as exc:
        _daemon_fail(ack_w, base, record_path, 127, str(exc))
    except PermissionError as exc:
        _daemon_fail(ack_w, base, record_path, 126, str(exc))
    except OSError as exc:
        _daemon_fail(ack_w, base, record_path, 1, str(exc))

    write_job_record(record_path, base | {"state": "running", "target_pid": proc.pid})
    os.write(ack_w, struct.pack("<II", 0, proc.pid))
    os.close(ack_w)
    rc = normalized_rc(proc.wait())
    restored = change_core_state(restore, online=False) if restore else True
    write_job_record(record_path, base | {
        "state": "finished",
        "target_pid": proc.pid,
        "return_code": rc,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "initially_offline_restored": restored,
    })
    os._exit(rc)


def job_path(pid: int) -> Path:
    return JOBS_DIR / f"{pid}.json"


def write_job_record(path: Path, payload: Json) -> None:
    try:
        atomic_write_json(path, payload)
    except (RunnerError, OSError):
        pass


def execute(prepared: PreparedCommand, cores: list[int], topo: Topology, *,
            detach: bool) -> int:
    cores = sorted(set(cores))
    if not cores:
        fail("the selected affinity is empty")
        return 1
    missing = [c for c in cores if c not in topo]
    if missing:
        fail(f"selected CPUs not present on this system: {missing}")
        return 1
    taskset = shutil.which("taskset")
    if taskset is None:
        fail("util-linux taskset is required")
        return 1

    wake = [c for c in cores if c not in online_mask()]
    if wake:
        warn(f"waking offline targets {format_cpu_list(wake)}")
        if not change_core_state(wake, online=True):
            fail("hardware modification failed; refusing to launch")
            return 1

    spec = format_cpu_list(cores)
    argv = [taskset, "-c", spec, *prepared.argv]
    say(f"[bold green]Bounding[/bold green] {prepared.label} to CPUs [white]{spec}[/white]")
    if detach:
        return run_detached(argv, prepared.env, wake, prepared.label)
    return run_attached(argv, prepared.env, wake)


# ---------------------------------------------------------------------------
# Affinity resolution
# ---------------------------------------------------------------------------
def cores_of_kind(topo: Topology, kind: Kind) -> list[int]:
    return sorted(c for c, i in topo.items() if i.kind == kind)


def resolve_type(choice: TypeChoice, topo: Topology) -> list[int]:
    match choice:
        case "all":
            return sorted(topo)
        case "pcores":
            return cores_of_kind(topo, "P") or sorted(topo)
        case "ecores":
            picked = cores_of_kind(topo, "E")
            if picked:
                return picked
            warn("no E-cores detected; falling back to P-cores")
            return cores_of_kind(topo, "P") or sorted(topo)


def maybe_save_profile(prepared: PreparedCommand, cores: list[int]) -> None:
    if confirm(f"Save CPUs {format_cpu_list(cores)} as default for {prepared.label}?",
               default=True):
        try:
            mutate_profiles(lambda ps: ps.__setitem__(
                prepared.profile_key,
                Profile(prepared.label, tuple(sorted(set(cores)))),
            ))
            say("[green]Profile saved.[/green]")
        except (RunnerError, OSError) as exc:
            warn(f"could not save profile: {exc}")


def resolve_affinity(cli: Cli, prepared: PreparedCommand, topo: Topology) -> list[int]:
    assert cli.command is not None
    if cli.custom is not None:
        return parse_cpu_list(cli.custom)
    if cli.type_choice is not None:
        return resolve_type(cli.type_choice, topo)

    profiles = load_profiles()
    profile = profiles.get(prepared.profile_key)
    if profile is not None and not cli.interactive:
        valid = sanitize_profile(prepared.profile_key, profile, topo)
        if valid:
            say(f"[green]Loaded saved affinity for {prepared.label}: [/green]"
                f"{format_cpu_list(valid)}")
            return valid
        warn(f"profile for {prepared.label} has no usable CPUs on this machine")

    if interactive_ready():
        try:
            picked = checklist(topo, prepared.label)
        except Cancelled:
            fail("cancelled")
            raise SystemExit(130) from None
        if not picked:
            fail("nothing selected")
            raise SystemExit(130)
        maybe_save_profile(prepared, picked)
        return picked

    warn("no foreground terminal; defaulting to P-cores")
    return resolve_type("pcores", topo)


# ---------------------------------------------------------------------------
# Interactive flows
# ---------------------------------------------------------------------------
def flow_run_new(topo: Topology) -> int | None:
    try:
        raw = read_line("[bold yellow]Command to run:[/bold yellow] ")
        if not raw:
            fail("no command entered")
            return None
        words = shlex.split(raw)
        prepared = prepare_command(words)
    except Cancelled:
        return None
    except ValueError as exc:
        fail(f"quoting error: {exc}")
        return None
    except UsageError as exc:
        fail(str(exc))
        return None

    profiles = load_profiles()
    saved = (sanitize_profile(prepared.profile_key, profiles[prepared.profile_key], topo)
             if prepared.profile_key in profiles else [])

    options = ([f"Saved ({format_cpu_list(saved)})"] if saved else []) + [
        "P-Cores (fast)",
        "E-Cores (efficient)",
        "All CPUs",
        "Custom checklist",
    ]
    try:
        pick = menu_select(options, f"Affinity / {prepared.label}",
                           "arrows · j/k · h/l · Enter selects · q cancels")
        choice = options[pick]
        cores = (saved if choice.startswith("Saved")
                 else resolve_type("pcores", topo) if choice.startswith("P-Cores")
                 else resolve_type("ecores", topo) if choice.startswith("E-Cores")
                 else resolve_type("all", topo) if choice == "All CPUs"
                 else checklist(topo, prepared.label))
    except Cancelled:
        return None
    if not cores:
        fail("aborted")
        return None

    maybe_save_profile(prepared, cores)
    detach = confirm("Run detached?", default=False)
    return execute(prepared, cores, topo, detach=detach)


def flow_profiles(topo: Topology) -> int | None:
    while True:
        profiles = load_profiles()
        if not profiles:
            warn("no profiles saved yet")
            pause()
            return None
        names = sorted(profiles)
        options = [f"{profiles[n].label} ({format_cpu_list(profiles[n].cores)})"
                   for n in names] + ["Back"]
        try:
            pick = menu_select(options, "Profiles", "Enter selects · q returns")
            if pick < 0 or pick == len(names):
                return None
            name = names[pick]
            profile = profiles[name]
            act = ["Launch", "Modify affinity", "Delete", "Back"][
                menu_select(["Launch", "Modify affinity", "Delete", "Back"],
                            f"Profile: {profile.label}", name)
            ]
        except Cancelled:
            return None

        match act:
            case "Back":
                continue
            case "Delete":
                if confirm(f"Delete profile {profile.label}?", default=False):
                    try:
                        mutate_profiles(lambda ps: ps.pop(name, None))
                        say("[green]Profile deleted.[/green]")
                    except (RunnerError, OSError) as exc:
                        warn(f"could not delete: {exc}")
                continue
            case "Modify affinity":
                try:
                    fresh = checklist(topo, profile.label)
                except Cancelled:
                    continue
                if fresh:
                    try:
                        updated = Profile(profile.label, tuple(fresh))
                        mutate_profiles(lambda ps: ps.__setitem__(name, updated))
                        say("[green]Affinity updated.[/green]")
                    except (RunnerError, OSError) as exc:
                        warn(f"could not update: {exc}")
                continue
            case "Launch":
                valid = sanitize_profile(name, profile, topo)
                if not valid:
                    fail("profile has no usable CPUs here; modify it first")
                    pause()
                    continue
                try:
                    extra_raw = read_line(
                        f"[bold yellow]Extra args for {profile.label} (empty = none):"
                        "[/bold yellow] ")
                except Cancelled:
                    continue
                try:
                    extra = shlex.split(extra_raw) if extra_raw else []
                except ValueError as exc:
                    fail(f"quoting error: {exc}")
                    continue
                prepared = PreparedCommand(
                    argv=(name, *extra),
                    env=os.environ.copy(),
                    profile_key=name,
                    label=profile.label,
                )
                detach = confirm("Run detached?", default=False)
                return execute(prepared, valid, topo, detach=detach)
    return None


def launcher(topo: Topology) -> int:
    options = ["Run new command", "Profiles", "Topology status", "Help", "Exit"]
    while True:
        try:
            pick = menu_select(options, "Dusky Core Runner",
                               "arrows · j/k · h/l · Enter · q quits")
        except Cancelled:
            return 130
        try:
            match options[pick]:
                case "Exit":
                    return 0
                case "Topology status":
                    show_status(topo)
                    pause()
                case "Help":
                    show_help()
                    pause()
                case "Profiles":
                    if (launched := flow_profiles(topo)) is not None:
                        return launched
                case "Run new command":
                    if (launched := flow_run_new(topo)) is not None:
                        return launched
        except Cancelled:
            continue


# ---------------------------------------------------------------------------
# CLI scanning (deliberately not argparse.REMAINDER — bpo-17050)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Cli:
    help: bool = False
    status: bool = False
    interactive: bool = False
    detach: bool = False
    type_choice: TypeChoice | None = None
    custom: str | None = None
    command: list[str] | None = None


FLAG_HELP: Final = frozenset({"-h", "--help"})
FLAG_STATUS: Final = frozenset({"-s", "--status"})
FLAG_INTERACTIVE: Final = frozenset({"-i", "--interactive"})
FLAG_DETACH: Final = frozenset({"-d", "--detach"})
FLAG_TYPE: Final = frozenset({"-t", "--type"})
FLAG_CUSTOM: Final = frozenset({"-c", "--custom"})
TYPE_CHOICES: Final = frozenset({"pcores", "ecores", "all"})


def scan_cli(argv: list[str]) -> Cli:
    cli = Cli()
    i, n = 0, len(argv)
    while i < n:
        tok = argv[i]
        if tok == "--":
            cli.command = list(argv[i + 1 :])
            break
        if tok in FLAG_HELP:
            cli.help = True
        elif tok in FLAG_STATUS:
            cli.status = True
        elif tok in FLAG_INTERACTIVE:
            cli.interactive = True
        elif tok in FLAG_DETACH:
            cli.detach = True
        elif tok in FLAG_TYPE or tok.startswith("--type="):
            if "=" in tok:
                value, step = tok.split("=", 1)[1], 1
            else:
                if i + 1 >= n:
                    raise UsageError(f"{tok} requires pcores|ecores|all")
                value, step = argv[i + 1], 2
            if value not in TYPE_CHOICES:
                raise UsageError(f"invalid --type {value!r} (expected pcores|ecores|all)")
            if cli.type_choice is not None and cli.type_choice != value:
                raise UsageError(f"conflicting --type values ({cli.type_choice} vs {value})")
            cli.type_choice = value  # type: ignore[assignment]
            i += step
            continue
        elif tok in FLAG_CUSTOM or tok.startswith("--custom="):
            if "=" in tok:
                value, step = tok.split("=", 1)[1], 1
            else:
                if i + 1 >= n:
                    raise UsageError(f"{tok} requires a CPU list")
                value, step = argv[i + 1], 2
            cli.custom = value if cli.custom is None else f"{cli.custom},{value}"
            i += step
            continue
        elif tok.startswith("-") and tok != "-":
            raise UsageError(f"unknown option: {tok} (use '--' to pass it to the command)")
        else:
            cli.command = list(argv[i:])
            break
        i += 1
    if cli.command is None:
        cli.command = []
    return cli


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    try:
        cli = scan_cli(list(sys.argv[1:] if argv is None else argv))
    except UsageError as exc:
        fail(str(exc))
        return 2

    if cli.help:
        show_help()
        return 0

    if shutil.which("taskset") is None:
        fail("util-linux taskset is required")
        return 1

    topo = detect_topology()

    if cli.status:
        show_status(topo)
        return 0

    if not cli.command:
        if (cli.custom is not None or cli.type_choice is not None
                or cli.detach or cli.interactive):
            fail("these options require a target command (-h shows usage)")
            return 2
        if interactive_ready():
            return launcher(topo)
        fail("no target command provided and no foreground terminal available")
        return 2

    try:
        prepared = prepare_command(cli.command)
        cores = resolve_affinity(cli, prepared, topo)
    except UsageError as exc:
        fail(str(exc))
        return 2
    except Cancelled:
        return 130

    return execute(prepared, cores, topo, detach=cli.detach)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None

#!/usr/bin/env python3
# =============================================================================
#  Dusky RAM Analyzer & Balloon Benchmark  -  v3.0 (Arch Linux bleeding-edge)
#  Target : Arch Linux (rolling) | Linux 7.2+ | Python 3.14+ | Textual 8.x
#  Scope  : Live ZRAM / reclaim / PSI / writeback forensics plus synthetic,
#           multi-category memory-pressure ballooning:
#             [1] Dormant Anon  MADV_COLD at birth, MADV_PAGEOUT on demand
#             [2] Active Anon   kept hot: one store per page every 1.5 s
#             [3] Clean Cache   O_TMPFILE page cache, fdatasync'd (clean)
#             [4] Dirty Cache   O_TMPFILE page cache, re-dirtied every 1.5 s
#             [5] Shmem         memfd (tmpfs/shmem) pages, swap-backed
#             [6] Thrash        continuous store-per-page cycling through swap
#  Threads: Textual UI loop | sampler | balloon owner | pressure loop.
#           Page-granular work runs inside the kernel with the GIL released
#           (ctypes madvise/memmove, os.pwritev, os.fdatasync); Python issues
#           O(size / 8 MiB) calls instead of O(size / 4 KiB) bytecode loops.
# =============================================================================

import argparse
import ctypes
import errno
import heapq
import json
import mmap
import os
import pwd
import queue
import re
import select
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    from rich.segment import Segment
    from rich.style import Style
    from rich.text import Text
    from textual import events, on
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.color import Color
    from textual.containers import Container, Horizontal, VerticalScroll
    from textual.message import Message
    from textual.screen import ModalScreen
    from textual.strip import Strip
    from textual.theme import Theme
    from textual.widget import Widget
    from textual.widgets import Button, Static
except ImportError as exc:
    raise SystemExit(
        f"[fatal] missing dependency: {exc.name}\n"
        "        sudo pacman -S --needed python-textual python-rich"
    )

# --------------------------------------------------------------------------- #
#  constants & kernel ABI                                                     #
# --------------------------------------------------------------------------- #
PAGE = mmap.PAGESIZE
MIB = 1 << 20
TEMPLATE = 2 * MIB            # 512 distinct pages; stays cache-hot while replicated
SLICE = 8 * MIB               # pressure-loop unit: bounds per-step lock and GIL hold
IOV_SPAN = 512 * MIB          # bytes per pwritev(2) call (256 iovecs, far below IOV_MAX)
TOUCH_PERIOD = 1.5            # active-anon touch / dirty re-dirty cadence (s)
THRASH_PAUSE = 0.02           # pause between thrash block passes (s)
STATS_PERIOD = 2.0            # balloon residency (mincore/cachestat) cadence (s)
TOP_N = 7
MISSING, DENIED = "\x00missing", "\x00denied"
CATS = ("dormant", "active", "clean", "dirty", "shmem", "thrash")

MADV_COLD, MADV_PAGEOUT, MADV_POPULATE_WRITE = 20, 21, 23   # uapi asm-generic/mman-common.h
PR_SET_VMA, PR_SET_VMA_ANON_NAME = 0x53564D41, 0           # uapi linux/prctl.h (5.17+)
MFD_NOEXEC_SEAL = 0x0008                                   # uapi linux/memfd.h (6.3+)
NR_CACHESTAT = 451                                         # asm-generic unistd (6.5+)

# dlopen(NULL): glibc is already mapped -> no ctypes.util.find_library() ldconfig fork.
# CDLL calls release the GIL for their whole duration (mmap.madvise() does not).
_libc = ctypes.CDLL(None, use_errno=True)
_libc.madvise.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
_libc.madvise.restype = ctypes.c_int
_libc.mincore.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p)
_libc.mincore.restype = ctypes.c_int
_libc.prctl.restype = ctypes.c_int
_libc.syscall.restype = ctypes.c_long
_memmove = ctypes.memmove                                  # CFUNCTYPE -> GIL released
_LSB = bytes(i & 1 for i in range(256))                    # mincore(2): only bit 0 is defined


class _CacheRange(ctypes.Structure):
    _fields_ = [("off", ctypes.c_uint64), ("len", ctypes.c_uint64)]


class _CacheStat(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("cache", "dirty", "writeback", "evicted", "recent")]


def madvise(addr: int, length: int, advice: int) -> int:
    """GIL-free madvise(2). Returns 0 or errno."""
    return 0 if _libc.madvise(addr, length, advice) == 0 else ctypes.get_errno()


def name_vma(addr: int, length: int, name: bytes) -> None:
    """Label an anonymous VMA -> '[anon:<name>]' in /proc/PID/{maps,smaps}. Best effort."""
    _libc.prctl(PR_SET_VMA, ctypes.c_ulong(PR_SET_VMA_ANON_NAME), ctypes.c_ulong(addr),
                ctypes.c_ulong(length), ctypes.c_char_p(name))


# --------------------------------------------------------------------------- #
#  Matugen theme                                                              #
# --------------------------------------------------------------------------- #
_THEME_DEFAULTS = {
    "bg": "#191113", "fg": "#efdfe1", "accent": "#ffb1c8", "error": "#ffb4ab",
    "warning": "#e3bdc6", "success": "#efbd94", "muted": "#514347",
}


def _config_home() -> Path:
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg)
    if os.geteuid() == 0 and (user := os.environ.get("SUDO_USER")):
        try:
            return Path(pwd.getpwnam(user).pw_dir) / ".config"   # keep the invoking user's theme
        except KeyError:
            pass
    return Path.home() / ".config"


def load_theme() -> dict[str, str]:
    try:
        raw = json.loads((_config_home() / "matugen/generated/dusky_tui.json").read_bytes())
    except (OSError, ValueError):
        raw = {}
    theme = {}
    for key, default in _THEME_DEFAULTS.items():
        try:
            theme[key] = Color.parse(str(raw.get(key, default))).hex   # reject values Textual CSS can't parse
        except Exception:
            theme[key] = default
    return theme


THEME = load_theme()
BG, FG, ACCENT, ERROR, WARNING, SUCCESS, MUTED = (
    THEME[k] for k in ("bg", "fg", "accent", "error", "warning", "success", "muted"))

# Pre-built Rich styles: rows are Segment tuples, so the render path never parses markup.
K = Style(color=FG, bold=True)
V = Style(color=SUCCESS)
D = Style(dim=True)
FGS = Style(color=FG)
FGB = Style(color=FG, bold=True)
A = Style(color=ACCENT)
AB = Style(color=ACCENT, bold=True)
AD = Style(color=ACCENT, dim=True)
W = Style(color=WARNING)
WB = Style(color=WARNING, bold=True)
E = Style(color=ERROR)
EB = Style(color=ERROR, bold=True)
OK = Style(color=SUCCESS)
OKB = Style(color=SUCCESS, bold=True)
M = Style(color=MUTED)
CRIT = Style(color="#ffffff", bgcolor=ERROR, bold=True)

Row = tuple[tuple[Segment, ...], tuple[Segment, ...]]
GAP: Row = ((), ())


def t(text: str, style: Style = V) -> Segment:
    return Segment(text, style)


def L(label: str, note: str = "", style: Style = K) -> tuple[Segment, ...]:
    return (Segment(label, style), Segment(f" ({note})", D)) if note else (Segment(label, style),)


def R(*parts: Segment | str) -> tuple[Segment, ...]:
    return tuple(p if isinstance(p, Segment) else Segment(p, V) for p in parts)


def fmt_bytes(n: float | None) -> str:
    if n is None:
        return "n/a"
    v = float(n)
    if abs(v) < 1024.0:
        return f"{v:.0f} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        v /= 1024.0
        if abs(v) < 1024.0:
            return f"{v:.2f} {unit}"
    return f"{v / 1024.0:.2f} PiB"


def bar(frac: float, width: int = 16) -> tuple[Segment, ...]:
    f = min(max(frac, 0.0), 1.0)
    n = round(f * width)
    return (Segment("█" * n, OK if f < 0.70 else W if f < 0.90 else E), Segment("░" * (width - n), M))


def grade(v: float, lo: float, hi: float) -> Style:
    return OK if v < lo else W if v < hi else EB


def cell(value: str, style: Style | None = None) -> tuple[Segment, ...]:
    if value == MISSING:
        return (t("n/a", D),)
    if value == DENIED:
        return (t("root only", D),)
    return (t(value, style or V),)


# --------------------------------------------------------------------------- #
#  procfs / sysfs access: persistent fds, one pread(2) per sample             #
# --------------------------------------------------------------------------- #
class KFile:
    """Persistent read-only fd. seq_file (procfs, PSI) and kernfs (sysfs, cgroupfs)
    regenerate content on every read at offset 0, so each sample is exactly one
    pread(2): no openat/fstat/close, no Python file-object construction."""

    __slots__ = ("path", "fd", "cap")

    def __init__(self, path: str, cap: int = 4096) -> None:
        self.path, self.cap = path, cap
        self.fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)

    @classmethod
    def maybe(cls, path: str, cap: int = 4096) -> "KFile | None":
        try:
            return cls(path, cap)
        except OSError:
            return None

    @classmethod
    def state(cls, path: str, cap: int = 256) -> "KFile | str":
        try:
            return cls(path, cap)
        except PermissionError:
            return DENIED
        except OSError:
            return MISSING

    def read(self) -> bytes:
        while len(data := os.pread(self.fd, self.cap, 0)) >= self.cap:
            self.cap *= 2                        # content outgrew the buffer: retry larger
        return data

    def text(self) -> str:
        try:
            return self.read().strip().decode(errors="replace")
        except PermissionError:
            return DENIED
        except OSError:
            return MISSING

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


_MEM_RE = re.compile(rb"^([^:\n]+):\s+(\d+)( kB)?$", re.M)
_PSI_RE = re.compile(rb"^(some|full) avg10=([\d.]+) avg60=([\d.]+) avg300=([\d.]+) total=(\d+)", re.M)
VM_KEYS = ("pswpin", "pswpout", "pgmajfault", "oom_kill", "pgscan_kswapd", "pgscan_direct",
           "pgsteal_kswapd", "pgsteal_direct", "compact_stall",
           "workingset_refault_anon", "workingset_refault_file")
_VM_RE = re.compile(rb"^(" + b"|".join(k.encode() for k in VM_KEYS) + rb") (\d+)$", re.M)
_SEL_RE = re.compile(r"\[([^\]]+)\]")

Psi = dict[str, tuple[float, float, float, int]]   # "some"/"full" -> (avg10, avg60, avg300, total_us)


def parse_meminfo(data: bytes) -> dict[str, int]:
    return {k.decode(): int(v) << 10 if kb else int(v) for k, v, kb in _MEM_RE.findall(data)}


def parse_vmstat(data: bytes) -> dict[str, int]:
    return {k.decode(): int(v) for k, v in _VM_RE.findall(data)}


def parse_psi(data: bytes) -> Psi:
    return {k.decode(): (float(a), float(b), float(c), int(tot)) for k, a, b, c, tot in _PSI_RE.findall(data)}


def selected(raw: str) -> str:
    """'lzo [zstd] lz4' -> 'zstd' (sentinels pass through)."""
    if raw in (MISSING, DENIED):
        return raw
    m = _SEL_RE.search(raw)
    return m.group(1) if m else raw


@dataclass(slots=True, frozen=True)
class SwapSplit:
    zram_total: int = 0
    zram_used: int = 0
    disk_total: int = 0
    disk_used: int = 0


def parse_swaps(data: bytes) -> tuple[SwapSplit, frozenset[str]]:
    zt = zu = dt = du = 0
    devs = []
    for line in data.splitlines()[1:]:
        f = line.split()
        if len(f) < 5:
            continue
        name = f[0].decode(errors="replace")
        try:
            size, used = int(f[2]) << 10, int(f[3]) << 10
        except ValueError:
            continue
        devs.append(name)
        if name.startswith("/dev/zram"):
            zt, zu = zt + size, zu + used
        else:
            dt, du = dt + size, du + used
    return SwapSplit(zt, zu, dt, du), frozenset(devs)


def parse_mounts(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in data.splitlines():
        if line.startswith(b"/dev/zram"):
            src, mnt = line.split(b" ", 2)[:2]
            out[src.decode()] = mnt.decode(errors="replace").replace("\\040", " ")
    return out


class RateMeter:
    """Per-second deltas of monotonic counters (vmstat events, PSI total µs).
    Spans shorter than 250 ms (forced refreshes) reuse the previous rates instead of
    amplifying counter quantisation noise."""

    __slots__ = ("prev", "stamp", "rates")

    def __init__(self) -> None:
        self.prev: dict[str, float] | None = None
        self.stamp = 0.0
        self.rates: dict[str, float] = {}

    def update(self, sample: dict[str, float], now: float) -> dict[str, float]:
        if self.prev is not None:
            span = now - self.stamp
            if span < 0.25:
                return self.rates
            prev = self.prev
            self.rates = {k: (v - p) / span for k, v in sample.items()
                          if (p := prev.get(k)) is not None and v >= p}
        self.prev, self.stamp = sample, now
        return self.rates


def pick_cgroup() -> tuple[str, KFile | None]:
    """Our own cgroup-v2 chain (balloons are charged here): prefer the user@UID.service
    ancestor (systemd-oomd's usual ManagedOOMMemoryPressure target), else the nearest slice.
    Under sudo the process stays in the invoking user's scope, so this also holds for --root."""
    try:
        rel = next(line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines()
                   if line.startswith("0::"))
    except (OSError, StopIteration):
        return "", None
    parts = [p for p in rel.split("/") if p]
    chain = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    pick = (next((c for c in chain if c.rsplit("/", 1)[-1].startswith("user@")), None)
            or next((c for c in chain if c.endswith(".slice")), None)
            or (chain[0] if chain else None))
    if pick is None:
        return "", None
    kf = KFile.maybe(f"/sys/fs/cgroup/{pick}/memory.pressure", 512)
    return (pick.rsplit("/", 1)[-1], kf) if kf else ("", None)


def _status_field(buf: bytes, key: bytes) -> bytes:
    i = buf.find(key)
    if i < 0:
        return b""
    j = buf.find(b"\n", i + len(key))
    return buf[i + len(key): j if j >= 0 else None].strip()


def scan_top(limit: int = TOP_N) -> list[tuple[int, int, int, str]]:
    """Rank every process by /proc/PID/statm (tiny, cheap kernel format), then read the
    expensive /proc/PID/status (name + VmSwap) only for the top `limit` winners."""
    ranked: list[tuple[int, int]] = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            fd = os.open(f"/proc/{name}/statm", os.O_RDONLY)
        except OSError:
            continue
        try:
            fields = os.read(fd, 256).split(maxsplit=2)
        except OSError:
            continue
        finally:
            os.close(fd)
        try:
            rss = int(fields[1]) if len(fields) >= 2 else 0
        except ValueError:
            continue
        if rss:
            ranked.append((rss, int(name)))
    rows = []
    for rss, pid in heapq.nlargest(limit, ranked):
        try:
            fd = os.open(f"/proc/{pid}/status", os.O_RDONLY)
            try:
                st = os.read(fd, 16384)
            finally:
                os.close(fd)
        except OSError:
            continue
        swap = _status_field(st, b"\nVmSwap:").split()
        rows.append((rss * PAGE, int(swap[0]) << 10 if swap else 0, pid,
                     _status_field(st, b"Name:").decode(errors="replace")))
    return rows


# --------------------------------------------------------------------------- #
#  systemd-oomd model (oomd.conf [OOM] + systemd 261 *.oomrule rulesets)      #
# --------------------------------------------------------------------------- #
SYSTEMD_DIRS = ("/etc/systemd", "/run/systemd", "/usr/local/lib/systemd", "/usr/lib/systemd")
OOMD_UNIT_LINK = "/run/systemd/units/invocation:systemd-oomd.service"
OOMD_CGROUP = "/sys/fs/cgroup/system.slice/systemd-oomd.service"


@dataclass(slots=True, frozen=True)
class OomRule:
    name: str
    swap_max: float | None
    psi_above: float | None
    lasting: float
    action: str


@dataclass(slots=True, frozen=True)
class OomdConf:
    swap_limit: float = 0.90        # SwapUsedLimit: memory AND swap used fraction must exceed
    psi_limit: float = 0.60         # DefaultMemoryPressureLimit: cgroup PSI "full avg10"
    psi_duration: float = 30.0      # DefaultMemoryPressureDurationSec
    rules: tuple[OomRule, ...] = ()


def oomd_active() -> bool:
    """Two stat(2) calls; systemd maintains both while the unit runs (no /proc walk)."""
    return os.path.lexists(OOMD_UNIT_LINK) or os.path.isdir(OOMD_CGROUP)


def _ini(path: str, section: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return out
    cur = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line[0] == "[" and line[-1] == "]":
            cur = line[1:-1].strip()
        elif cur == section and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _dropins(sub: str, suffix: str) -> list[str]:
    """systemd drop-in semantics: same file name -> highest-priority dir wins, symlink to
    /dev/null masks, surviving files are applied in lexicographic order."""
    seen: dict[str, str] = {}
    for d in SYSTEMD_DIRS:
        try:
            names = os.listdir(f"{d}/{sub}")
        except OSError:
            continue
        for n in names:
            if n.endswith(suffix):
                seen.setdefault(n, f"{d}/{sub}/{n}")
    return [p for n in sorted(seen) if os.path.realpath(p := seen[n]) != "/dev/null"]


def _frac(v: str | None) -> float | None:
    if not v:
        return None
    v = v.strip()
    for suffix, div in (("%", 1e2), ("‰", 1e3), ("‱", 1e4)):
        if v.endswith(suffix):
            try:
                return float(v[: -len(suffix)]) / div
            except ValueError:
                return None
    return None


_SPAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]*)")
_SPAN_UNITS = {"": 1.0, "s": 1.0, "sec": 1.0, "second": 1.0, "seconds": 1.0, "ms": 1e-3,
               "msec": 1e-3, "us": 1e-6, "usec": 1e-6, "m": 60.0, "min": 60.0, "minute": 60.0,
               "minutes": 60.0, "h": 3600.0, "hr": 3600.0, "hour": 3600.0, "hours": 3600.0}


def _span(v: str | None, default: float) -> float:
    if not v:
        return default
    total = 0.0
    for num, unit in _SPAN_RE.findall(v.lower()):
        if unit not in _SPAN_UNITS:
            return default
        total += float(num) * _SPAN_UNITS[unit]
    return total or default


def load_oomd_conf() -> OomdConf:
    main = next((p for d in SYSTEMD_DIRS if os.path.isfile(p := f"{d}/oomd.conf")), None)
    kv = _ini(main, "OOM") if main else {}
    for p in _dropins("oomd.conf.d", ".conf"):
        kv.update(_ini(p, "OOM"))
    rules = []
    for p in _dropins("oomd/rules.d", ".oomrule"):
        r = _ini(p, "Rule")
        swap, psi, act = _frac(r.get("SwapUsageMax")), _frac(r.get("MemoryPressureAbove")), r.get("Action", "")
        if act and (swap is not None or psi is not None):       # oomd ignores incomplete rules
            rules.append(OomRule(Path(p).stem, swap, psi, _span(r.get("LastingSec"), 0.0), act))
    swap_limit, psi_limit = _frac(kv.get("SwapUsedLimit")), _frac(kv.get("DefaultMemoryPressureLimit"))
    return OomdConf(
        swap_limit=0.90 if swap_limit is None else swap_limit,
        psi_limit=0.60 if psi_limit is None else psi_limit,
        psi_duration=_span(kv.get("DefaultMemoryPressureDurationSec"), 30.0),
        rules=tuple(rules),
    )


# --------------------------------------------------------------------------- #
#  panel row builders (pure; executed on the sampler thread)                  #
# --------------------------------------------------------------------------- #
def rows_memory(mem: dict[str, int], sw: SwapSplit) -> list[Row]:
    if not mem:
        return [(L("/proc/meminfo unreadable", style=EB), ())]
    g = mem.get
    total, avail = g("MemTotal", 0), g("MemAvailable", 0)
    used = max(total - avail, 0)
    uf = used / total if total else 0.0
    shmem, dirty = g("Shmem", 0), g("Dirty", 0)
    st = g("SwapTotal", 0)
    su = max(st - g("SwapFree", 0), 0)
    sf = su / st if st else 0.0
    rows = [
        (L("Total physical RAM"), R(t(fmt_bytes(total), FGB))),
        (L("Used", "Total − Available"), R(fmt_bytes(used), t(f" ({uf:.1%})", D))),
        ((), bar(uf)),
        (L("Available", "reclaim headroom"), R(t(fmt_bytes(avail), OKB))),
        (L("Free", "strict"), R(fmt_bytes(g("MemFree", 0)))),
        (L("  ↳ Anon pages"), R(fmt_bytes(g("AnonPages", 0)))),
        (L("  ↳ File cache", "Cached − Shmem"), R(fmt_bytes(max(g("Cached", 0) - shmem, 0)))),
        (L("  ↳ Shmem / tmpfs / memfd"), R(fmt_bytes(shmem))),
        (L("  ↳ Buffers + SReclaimable"), R(fmt_bytes(g("Buffers", 0) + g("SReclaimable", 0)))),
        (L("  ↳ Dirty / Writeback"),
         R(t(fmt_bytes(dirty), WB if dirty > 64 * MIB else V), t(" / ", D), fmt_bytes(g("Writeback", 0)))),
        (L("  ↳ Unevictable / Mlocked"),
         R(fmt_bytes(g("Unevictable", 0)), t(" / ", D), fmt_bytes(g("Mlocked", 0)))),
        GAP,
        (L("Swap total"), R(t(fmt_bytes(st), FGB))),
        (L("Swap used"), R(fmt_bytes(su), t(f" ({sf:.1%})", D))),
        ((), bar(sf)),
        (L("  ↳ ZRAM swap pool"), R(t(f"{fmt_bytes(sw.zram_used)} / {fmt_bytes(sw.zram_total)}", A))),
        (L("  ↳ Disk swap spillover"),
         R(t(f"{fmt_bytes(sw.disk_used)} / {fmt_bytes(sw.disk_total)}", EB if sw.disk_used else V))),
        (L("  ↳ SwapCached"), R(fmt_bytes(g("SwapCached", 0)))),
    ]
    if g("Zswap"):
        rows.append((L("  ↳ Zswap pool / stored"), R(f"{fmt_bytes(g('Zswap'))} / {fmt_bytes(g('Zswapped', 0))}")))
    return rows


def _live(rates: dict[str, float], key: str) -> float:
    return min(100.0, rates.get(key, 0.0) / 1e4)          # Δµs stalled per s -> percent


def rows_pressure(psi: dict[str, Psi], cg: Psi, cg_label: str, rates: dict[str, float],
                  vm: dict[str, int], active: bool, conf: OomdConf,
                  risk: tuple[str, Style, str], session_kills: int) -> list[Row]:
    rows: list[Row] = []
    if m := psi.get("memory"):
        for kind, lo, hi in (("some", 5.0, 20.0), ("full", 1.0, 10.0)):
            a10, a60, a300, _ = m.get(kind, (0.0, 0.0, 0.0, 0))
            live = _live(rates, f"memory.{kind}")
            st = grade(max(a10, live), lo, hi)
            rows.append((L(f"Memory PSI {kind}", "live · 10/60/300 s"),
                         R(t(f"{live:4.1f}%", st), t(" · ", D), t(f"{a10:.2f}", st), f"/{a60:.2f}/{a300:.2f}")))
    else:
        rows.append((L("Pressure (PSI)"), R(t("unavailable (CONFIG_PSI / psi=0)", D))))
    if c := psi.get("cpu"):
        a10, a60, _, _ = c.get("some", (0.0, 0.0, 0.0, 0))
        live = _live(rates, "cpu.some")
        st = grade(max(a10, live), 15.0, 50.0)
        rows.append((L("CPU PSI some", "contention"), R(t(f"{live:4.1f}%", st), t(" · ", D), t(f"{a10:.2f}", st), f"/{a60:.2f}")))
    if io := psi.get("io"):
        s10, f10 = io.get("some", (0.0,))[0], io.get("full", (0.0,))[0]
        rows.append((L("I/O PSI some / full", "disk / swap"),
                     R(f"{_live(rates, 'io.some'):4.1f}%", t(" · ", D), f"{s10:.2f}", t(" some / ", D), f"{f10:.2f}", t(" full", D))))
    if cg:
        s10, f10 = cg.get("some", (0.0,))[0], cg.get("full", (0.0,))[0]
        st = grade(f10, 10.0, conf.psi_limit * 100.0)
        rows.append((L("cgroup PSI", cg_label),
                     R(f"{_live(rates, 'cg.some'):4.1f}% some", t(" · ", D), t(f"full {_live(rates, 'cg.full'):4.1f}%", st),
                       t(" · avg10 ", D), f"{s10:.2f}/", t(f"{f10:.2f}", st))))
    rows.append(GAP)
    sin, sout = rates.get("pswpin", 0.0) * PAGE, rates.get("pswpout", 0.0) * PAGE
    rows.append((L("Swap I/O", "in=decompress / out=compress"),
                 R(f"in {fmt_bytes(sin)}/s", t(" · ", D), t(f"out {fmt_bytes(sout)}/s", AB))))
    direct, kswapd = rates.get("pgscan_direct", 0.0), rates.get("pgscan_kswapd", 0.0)
    rows.append((L("Reclaim scan", "direct / kswapd"),
                 R(t(f"{direct:.0f}/s direct", EB if direct else V), t(" · ", D), f"{kswapd:.0f}/s kswapd")))
    rows.append((L("Reclaim steal", "direct / kswapd"),
                 R(f"{rates.get('pgsteal_direct', 0.0):.0f}/s", t(" · ", D), f"{rates.get('pgsteal_kswapd', 0.0):.0f}/s")))
    ra, rf = rates.get("workingset_refault_anon", 0.0), rates.get("workingset_refault_file", 0.0)
    rows.append((L("Refaults", "anon / file"), R(t(f"{ra:.0f}/s", WB if ra > 1000 else V), t(" · ", D), f"{rf:.0f}/s")))
    rows.append((L("Major faults · compaction stalls"),
                 R(f"{rates.get('pgmajfault', 0.0):.0f}/s", t(" · ", D), f"{rates.get('compact_stall', 0.0):.0f}/s")))
    rows.append(GAP)
    rows.append((L("systemd-oomd"),
                 R(t("active", OKB) if active else t("inactive", D),
                   t(f" · swap>{conf.swap_limit:.0%} (mem∧swap) · full>{conf.psi_limit:.0%}/{conf.psi_duration:.0f}s", D))))
    for r in conf.rules[:3]:
        cond = [f"swap>{r.swap_max:.0%}"] if r.swap_max is not None else []
        if r.psi_above is not None:
            cond.append(f"full>{r.psi_above:.0%}")
        lasting = f" {r.lasting:.0f}s" if r.lasting else ""
        rows.append((L(f"  ↳ rule {r.name}", style=FGS), R(t(f"{' ∧ '.join(cond)}{lasting} → {r.action}", D))))
    level, style, why = risk
    rows.append((L("OOM risk", "oomd model"), R(t(f" {level} ", style), t(f" {why}", D))))
    boot = vm.get("oom_kill", 0)
    rows.append((L("Kernel OOM kills"),
                 R(t(str(boot), EB if boot else V), t(" boot · ", D), t(f"+{session_kills}", EB if session_kills else V), t(" session", D))))
    return rows


def rows_procs(procs: list[tuple[int, int, int, str]], total: int) -> list[Row]:
    rows: list[Row] = [((t(f"{'PID':>7}  PROCESS", D),), (t(f"{'RSS':>10} {'SWAP':>10} {'%':>5}", D),))]
    for rss, swap, pid, name in procs:
        rows.append(((t(f"{pid:>7}  ", AD), t(name[:24], FGB)),
                     (t(f"{fmt_bytes(rss):>10} ", W), t(f"{fmt_bytes(swap) if swap else '-':>10} ", AB if swap else D),
                      t(f"{rss / total * 100.0 if total else 0.0:5.1f}", E))))
    if not procs:
        rows.append((L("scanning /proc …", style=D), ()))
    return rows


@dataclass(slots=True, frozen=True)
class ZStat:
    name: str
    role: str
    algo: str
    disksize: int
    orig: int          # orig_data_size   (bytes, includes same-filled pages)
    compr: int         # compr_data_size  (bytes, huge pages count PAGE_SIZE each)
    used: int          # mem_used_total   (bytes, incl. zsmalloc fragmentation/metadata)
    limit: int         # mem_limit
    peak: int          # mem_used_max
    same: int          # same_pages      -> bytes
    compacted: int     # pages_compacted -> bytes (cumulative)
    huge: int          # huge_pages      -> bytes


def _codec_ratio(orig: int, compr: int, same: int, huge: int) -> float:
    """Compression ratio over pages that were actually compressed: same-filled pages add
    to orig but cost no compressed bytes; huge pages are stored raw (PAGE_SIZE each)."""
    num, den = orig - same - huge, compr - huge
    return num / den if num > 0 and den > 0 else 0.0


def rows_zram(devs: list[ZStat]) -> list[Row]:
    if not devs:
        return [(L("No ZRAM devices detected", style=D), ()),
                (L("enable", style=D), R(t("systemctl start systemd-zram-setup@zram0", D)))]
    rows: list[Row] = []
    for i, d in enumerate(devs):
        if i:
            rows.append(GAP)
        rows.append(((t(f"/dev/{d.name}", AB), t(f" {d.role}", W)), R(t(f"{d.algo} · {fmt_bytes(d.disksize)}", D))))
        if not d.orig:
            rows.append((L("  empty", style=D), R(t("0 B stored", D))))
            continue
        rows.append((L("  Stored → compressed"),
                     R(f"{fmt_bytes(d.orig)} → {fmt_bytes(d.compr)}",
                       t(f" ({_codec_ratio(d.orig, d.compr, d.same, d.huge):.2f}× codec)", D))))
        rows.append((L("  RAM used", "incl. allocator"), R(fmt_bytes(d.used), t(f" ({d.orig / d.used if d.used else 0:.2f}× eff)", D))))
        rows.append((L("  RAM saved"), R(t(f"+{fmt_bytes(max(d.orig - d.used, 0))}", OKB))))
        rows.append((L("  Same-filled / huge pages"), R(f"{fmt_bytes(d.same)} / ", t(fmt_bytes(d.huge), WB if d.huge else V))))
        rows.append((L("  Peak used · limit · compacted"),
                     R(f"{fmt_bytes(d.peak)} · {fmt_bytes(d.limit) if d.limit else 'none'} · {fmt_bytes(d.compacted)}")))
    if len(devs) > 1:
        o, c, u = sum(d.orig for d in devs), sum(d.compr for d in devs), sum(d.used for d in devs)
        rows.append(GAP)
        rows.append((L("Total ZRAM pool"), R(t(f"{fmt_bytes(sum(d.disksize for d in devs))} capacity", FGB))))
        rows.append((L("  Stored → compressed → RAM"), R(f"{fmt_bytes(o)} → {fmt_bytes(c)} → {fmt_bytes(u)}")))
        rows.append((L("  Effective ratio / saved"),
                     R(t(f"{o / u if u else 0:.2f}×", OKB), t(" · ", D), t(f"+{fmt_bytes(max(o - u, 0))}", AB))))
    return rows


TUNABLES = {
    "swappiness": "/proc/sys/vm/swappiness",
    "page-cluster": "/proc/sys/vm/page-cluster",
    "watermark_scale_factor": "/proc/sys/vm/watermark_scale_factor",
    "watermark_boost_factor": "/proc/sys/vm/watermark_boost_factor",
    "vfs_cache_pressure": "/proc/sys/vm/vfs_cache_pressure",
    "compaction_proactiveness": "/proc/sys/vm/compaction_proactiveness",
    "compact_unevictable_allowed": "/proc/sys/vm/compact_unevictable_allowed",
    "min_free_kbytes": "/proc/sys/vm/min_free_kbytes",
    "overcommit_memory": "/proc/sys/vm/overcommit_memory",
    "stat_interval": "/proc/sys/vm/stat_interval",
    "dirty_writeback_centisecs": "/proc/sys/vm/dirty_writeback_centisecs",
    "dirty_expire_centisecs": "/proc/sys/vm/dirty_expire_centisecs",
    "dirty_background_bytes": "/proc/sys/vm/dirty_background_bytes",
    "dirty_background_ratio": "/proc/sys/vm/dirty_background_ratio",
    "dirty_bytes": "/proc/sys/vm/dirty_bytes",
    "dirty_ratio": "/proc/sys/vm/dirty_ratio",
    "max_map_count": "/proc/sys/vm/max_map_count",
    "lru_gen": "/sys/kernel/mm/lru_gen/enabled",
    "lru_gen_ttl": "/sys/kernel/mm/lru_gen/min_ttl_ms",
    "thp": "/sys/kernel/mm/transparent_hugepage/enabled",
    "thp_defrag": "/sys/kernel/mm/transparent_hugepage/defrag",
    "thp_shmem": "/sys/kernel/mm/transparent_hugepage/shmem_enabled",
    "khuge_ptes_none": "/sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_none",
    "zswap": "/sys/module/zswap/parameters/enabled",
}


def _num(raw: str, fmt: Callable[[int], str], style: Style | None = None) -> tuple[Segment, ...]:
    try:
        return (t(fmt(int(raw)), style or V),)
    except ValueError:
        return cell(raw)


def _dirty_limit(v: dict[str, str], stem: str) -> tuple[Segment, ...]:
    """The kernel uses *_bytes when non-zero, otherwise *_ratio."""
    try:
        b = int(v[f"{stem}_bytes"])
    except ValueError:
        b = 0
    return (t(fmt_bytes(b)),) if b else _num(v[f"{stem}_ratio"], lambda x: f"{x}% of dirtyable")


def rows_vm(v: dict[str, str]) -> list[Row]:
    pc, boost, zswap = v["page-cluster"], v["watermark_boost_factor"], v["zswap"]
    secs = lambda x: f"{x / 100:.1f} s"   # noqa: E731
    if zswap in (MISSING, DENIED):
        zcell = cell(zswap)
    elif zswap.upper().startswith("Y"):
        zcell = (t("on ", WB), t("(double-compresses in front of ZRAM!)", EB))
    else:
        zcell = (t("off", OK), t(" (clean)", D))
    return [
        (L("vm.swappiness"), cell(v["swappiness"])),
        (L("vm.page-cluster", "0 = no swap readahead"), cell(pc, OKB if pc == "0" else WB)),
        (L("vm.watermark_scale_factor"), cell(v["watermark_scale_factor"])),
        (L("vm.watermark_boost_factor"), cell(boost, OKB if boost == "0" else None)),
        (L("vm.vfs_cache_pressure"), cell(v["vfs_cache_pressure"])),
        (L("vm.compaction_proactiveness"), cell(v["compaction_proactiveness"])),
        (L("vm.compact_unevictable_allowed"), cell(v["compact_unevictable_allowed"])),
        (L("vm.min_free_kbytes"), _num(v["min_free_kbytes"], lambda x: fmt_bytes(x << 10))),
        (L("vm.overcommit_memory"), cell(v["overcommit_memory"])),
        (L("vm.stat_interval"), _num(v["stat_interval"], lambda x: f"{x} s")),
        (L("vm.dirty_writeback / expire"),
         (*_num(v["dirty_writeback_centisecs"], secs), t(" / ", D), *_num(v["dirty_expire_centisecs"], secs))),
        (L("vm.dirty_background", "writeback starts"), _dirty_limit(v, "dirty_background")),
        (L("vm.dirty", "writer throttling"), _dirty_limit(v, "dirty")),
        (L("vm.max_map_count"), _num(v["max_map_count"], lambda x: f"{x:,}")),
        GAP,
        (L("mglru.enabled"), cell(v["lru_gen"], OK)),
        (L("mglru.min_ttl_ms"), cell(v["lru_gen_ttl"])),
        (L("thp.enabled"), cell(selected(v["thp"]))),
        (L("thp.defrag"), cell(selected(v["thp_defrag"]))),
        (L("thp.shmem_enabled"), cell(selected(v["thp_shmem"]))),
        (L("khugepaged.max_ptes_none"), cell(v["khuge_ptes_none"])),
        (L("zswap"), zcell),
    ]


# --------------------------------------------------------------------------- #
#  Sampler thread                                                             #
# --------------------------------------------------------------------------- #
class SampleReady(Message):
    def __init__(self, panels: dict[str, list[Row]]) -> None:
        super().__init__()
        self.panels = panels


class Toast(Message):
    def __init__(self, text: str, severity: str = "information", timeout: float = 3.0) -> None:
        super().__init__()
        self.text, self.severity, self.timeout = text, severity, timeout


class ZDev:
    __slots__ = ("name", "mm", "ds", "algo_f", "disksize", "algo")

    def __init__(self, name: str) -> None:
        base = f"/sys/block/{name}"
        self.name = name
        self.mm = KFile(f"{base}/mm_stat", 256)
        self.ds = KFile.maybe(f"{base}/disksize", 64)
        self.algo_f = KFile.maybe(f"{base}/comp_algorithm", 512)
        self.disksize, self.algo = 0, "?"
        self.refresh_meta()

    def refresh_meta(self) -> None:
        if self.ds:
            self.disksize = int(self.ds.read() or 0)
        if self.algo_f:
            self.algo = selected(self.algo_f.read().decode(errors="replace").strip())

    def close(self) -> None:
        for kf in (self.mm, self.ds, self.algo_f):
            if kf:
                kf.close()


class Sampler(threading.Thread):
    """Fixed-cadence collector. Hot files are persistent fds (one pread each per tick);
    tunables, zram metadata and the oomd probe run on a 5 s cadence; the process ranking
    runs every max(2 s, interval). Rows are built here, so the UI thread only swaps them."""

    def __init__(self, post: Callable[[Message], None], interval: float) -> None:
        super().__init__(name="dusky-sampler", daemon=True)
        self.post, self.interval = post, interval
        self.halt, self.kick = threading.Event(), threading.Event()
        self.slow_every = max(1, round(5.0 / interval))
        self.scan_every = max(1, round(max(2.0, interval) / interval))
        self.meminfo = KFile("/proc/meminfo", 8192)
        self.vmstat = KFile("/proc/vmstat", 16384)
        self.swaps = KFile("/proc/swaps", 4096)
        self.psi = {r: f for r in ("memory", "cpu", "io") if (f := KFile.maybe(f"/proc/pressure/{r}", 512))}
        self.cg_label, self.cg = pick_cgroup()
        self.mounts = KFile("/proc/self/mounts", 16384)
        self.poller = select.poll()                      # mount-table changes raise POLLPRI|POLLERR
        self.poller.register(self.mounts.fd, select.POLLPRI | select.POLLERR)
        self.mount_map = parse_mounts(self.mounts.read())
        self.tun = {k: KFile.state(p) for k, p in TUNABLES.items()}
        self.tun_vals: dict[str, str] | None = None
        self.zdevs: dict[str, ZDev] = {}
        self.zdirty = True
        self.rates = RateMeter()
        self.oomd_on = oomd_active()
        self.oomd = load_oomd_conf()
        self.oom_base: int | None = None
        self.over_since: float | None = None
        self.rule_since: dict[str, float] = {}
        self.last_error = ""

    def run(self) -> None:
        tick, deadline = 0, time.monotonic()
        while not self.halt.is_set():
            forced = self.kick.is_set()
            self.kick.clear()
            try:
                panels = self.sample(forced or tick % self.slow_every == 0,
                                     forced or tick % self.scan_every == 0, forced)
            except Exception as exc:                     # keep sampling; report each new error once
                if (msg := f"sampler: {exc!r}") != self.last_error:
                    self.last_error = msg
                    self.post(Toast(msg, "error", 6.0))
            else:
                self.last_error = ""
                self.post(SampleReady(panels))
            tick += 1
            deadline += self.interval
            now = time.monotonic()
            if deadline <= now:                          # overran: re-phase instead of bursting
                deadline = now + self.interval
            self.kick.wait(deadline - now)

    def sample(self, slow: bool, scan: bool, forced: bool) -> dict[str, list[Row]]:
        now = time.monotonic()
        mem = parse_meminfo(self.meminfo.read())
        vm = parse_vmstat(self.vmstat.read())
        psi = {r: parse_psi(f.read()) for r, f in self.psi.items()}
        cg = parse_psi(self.cg.read()) if self.cg else {}
        sw, swapdevs = parse_swaps(self.swaps.read())
        if any(ev & (select.POLLPRI | select.POLLERR) for _, ev in self.poller.poll(0)):
            self.mount_map = parse_mounts(self.mounts.read())
        if slow or self.zdirty:
            self._discover_zram()
        zram = self._read_zram(slow, swapdevs)

        counters: dict[str, float] = dict(vm)
        for res, d in psi.items():
            for kind, v in d.items():
                counters[f"{res}.{kind}"] = v[3]
        for kind, v in cg.items():
            counters[f"cg.{kind}"] = v[3]
        rates = self.rates.update(counters, now)

        if slow:
            on_now = oomd_active()
            if forced or on_now != self.oomd_on:
                self.oomd = load_oomd_conf()
            self.oomd_on = on_now
        kills = vm.get("oom_kill", 0)
        if self.oom_base is None:
            self.oom_base = kills
        panels = {
            "p_mem": rows_memory(mem, sw),
            "p_psi": rows_pressure(psi, cg, self.cg_label, rates, vm, self.oomd_on, self.oomd,
                                   self._risk(mem, psi, cg, rates, now), kills - self.oom_base),
            "p_zram": rows_zram(zram),
        }
        if scan:
            panels["p_proc"] = rows_procs(scan_top(TOP_N), mem.get("MemTotal", 0))
        if slow:
            if forced:                                   # e.g. zswap module loaded after start
                self.tun = {k: f if isinstance(f, KFile) else KFile.state(TUNABLES[k]) for k, f in self.tun.items()}
            vals = {k: f.text() if isinstance(f, KFile) else f for k, f in self.tun.items()}
            if vals != self.tun_vals:
                self.tun_vals = vals
                panels["p_vm"] = rows_vm(vals)
        return panels

    def _discover_zram(self) -> None:
        try:
            names = {n for n in os.listdir("/sys/block") if n.startswith("zram") and n[4:].isdigit()}
        except OSError:
            names = set()
        for n in self.zdevs.keys() - names:
            self.zdevs.pop(n).close()
        for n in names - self.zdevs.keys():
            try:
                self.zdevs[n] = ZDev(n)
            except (OSError, ValueError):
                pass
        self.zdirty = False

    def _read_zram(self, slow: bool, swapdevs: frozenset[str]) -> list[ZStat]:
        out = []
        for name in sorted(self.zdevs, key=lambda n: int(n[4:])):
            d = self.zdevs[name]
            try:
                if slow:
                    d.refresh_meta()
                v = [int(x) for x in d.mm.read().split()]
            except (OSError, ValueError):                # hot-removed / reset mid-read
                self.zdirty = True
                continue
            v += [0] * (9 - len(v))
            dev = f"/dev/{name}"
            role = "SWAP" if dev in swapdevs else self.mount_map.get(dev, "idle")
            out.append(ZStat(name, role, d.algo, d.disksize, v[0], v[1], v[2], v[3], v[4],
                             v[5] * PAGE, v[6] * PAGE, v[7] * PAGE))
        return out

    def _risk(self, mem: dict[str, int], psi: dict[str, Psi], cg: Psi,
              rates: dict[str, float], now: float) -> tuple[str, Style, str]:
        """Mirror systemd-oomd's triggers: SwapUsedLimit needs BOTH memory-used and
        swap-used fractions above the limit; pressure uses cgroup PSI 'full avg10'
        sustained for DefaultMemoryPressureDurationSec; *.oomrule rules AND their terms."""
        c = self.oomd
        total = mem.get("MemTotal", 0)
        mem_f = (total - mem.get("MemAvailable", 0)) / total if total else 0.0
        st = mem.get("SwapTotal", 0)
        swap_f = (st - mem.get("SwapFree", 0)) / st if st else 0.0
        full = (cg or psi.get("memory", {})).get("full", (0.0,))[0] / 100.0
        some = psi.get("memory", {}).get("some", (0.0,))[0]
        over = full > c.psi_limit
        self.over_since = (self.over_since or now) if over else None
        over_for = now - self.over_since if self.over_since else 0.0
        swap_trip = st > 0 and mem_f > c.swap_limit and swap_f > c.swap_limit
        live = {r.name for r in c.rules}
        for stale in set(self.rule_since) - live:
            self.rule_since.pop(stale, None)
        hits, pending = [], []
        for r in c.rules:
            active = ((r.swap_max is None or swap_f > r.swap_max)
                      and (r.psi_above is None or full > r.psi_above))
            if not active:
                self.rule_since.pop(r.name, None)
                continue
            since = self.rule_since.setdefault(r.name, now)
            if now - since >= r.lasting:
                hits.append(r.name)
            else:
                pending.append((r.name, r.lasting - (now - since)))
        if swap_trip or (over and over_for >= c.psi_duration) or hits:
            why = ("mem∧swap > limit" if swap_trip
                   else f"full {full:.0%} > {c.psi_limit:.0%} for {over_for:.0f}s" if over else f"rule {hits[0]}")
            return ("CRITICAL" if self.oomd_on else "TRIPPED (oomd off)", CRIT, why)
        if pending:
            name, left = pending[0]
            return ("HIGH", EB, f"rule {name} over threshold ({left:.0f}s to trip)")
        if over:
            return ("HIGH", EB, f"full {full:.0%} > {c.psi_limit:.0%} for {over_for:.0f}/{c.psi_duration:.0f}s")
        if st and mem_f > 0.9 * c.swap_limit and swap_f > 0.9 * c.swap_limit:
            return ("HIGH", EB, f"mem {mem_f:.0%} · swap {swap_f:.0%} near {c.swap_limit:.0%}")
        if rates.get("pgscan_direct", 0.0) > 0 or full > 0.10 or some > 20.0:
            return ("ELEVATED", WB, f"direct reclaim / stalls (full {full:.0%}, some {some:.0f}%)")
        return ("NORMAL", OKB, f"mem {mem_f:.0%} · swap {swap_f:.0%} · full {full:.0%}")


# --------------------------------------------------------------------------- #
#  RAM ballooning engine                                                      #
# --------------------------------------------------------------------------- #
CAT_META = {
    "dormant": ("[1] Dormant Anon", "MADV_COLD · p=PAGEOUT"),
    "active": ("[2] Active Anon", "1 store/page per 1.5 s"),
    "clean": ("[3] Clean Cache", "O_TMPFILE · fdatasync"),
    "dirty": ("[4] Dirty Cache", "re-dirtied per 1.5 s"),
    "shmem": ("[5] Shmem memfd", "tmpfs · swap-backed"),
    "thrash": ("[6] Thrash Stress", "continuous RW cycling"),
}


class Block:
    """One balloon chunk. `lock` serialises the owner's close() against the pressure loop;
    `anchor`/`mv` hold buffer exports so the mmap cannot be unmapped underneath a raw
    address (mmap.close() raises BufferError while exports exist)."""

    __slots__ = ("cat", "size", "fd", "mm", "mv", "anchor", "addr", "lock", "alive")

    def __init__(self, cat: str, size: int, fd: int = -1, mm: mmap.mmap | None = None) -> None:
        self.cat, self.size, self.fd, self.mm = cat, size, fd, mm
        self.lock = threading.Lock()
        self.alive = True
        if mm is not None:
            self.mv = memoryview(mm)
            self.anchor = ctypes.c_char.from_buffer(mm)
            self.addr = ctypes.addressof(self.anchor)
        else:
            self.mv = self.anchor = None
            self.addr = 0

    def close(self) -> None:
        with self.lock:
            if not self.alive:
                return
            self.alive = False
            if self.mm is not None:
                self.mv.release()
                self.mv = self.anchor = None              # drop exports before munmap
                self.mm.close()                           # munmap runs with the GIL released
                self.mm = None
            if self.fd >= 0:
                os.close(self.fd)                         # O_TMPFILE/memfd inode freed here
                self.fd = -1


@dataclass(slots=True, frozen=True)
class CatStat:
    blocks: int = 0
    size: int = 0
    resident: int = 0      # bytes; -1 = kernel query unavailable
    dirty: int = 0
    writeback: int = 0


@dataclass(slots=True, frozen=True)
class BalloonStats:
    cats: dict[str, CatStat]
    total_mb: int
    tmpl_ratio: float
    touch_errors: int


class BalloonUpdate(Message):
    def __init__(self, stats: BalloonStats) -> None:
        super().__init__()
        self.stats = stats


class PressureLoop(threading.Thread):
    """Active touch (1.5 s), dirty re-dirty (1.5 s) and round-robin thrash.
    Per 8 MiB slice: MADV_POPULATE_WRITE faults every evicted page back in and write-
    faults clean shared-file pages inside the kernel (GIL released), then — for anon
    categories — one strided memoryview store per page sets the hardware accessed/dirty
    bits that MGLRU aging actually samples. Pages are resident at that point, so the
    GIL-held store is ~2048 byte writes, never a chain of major faults."""

    def __init__(self, engine: "BalloonEngine") -> None:
        super().__init__(name="dusky-pressure", daemon=True)
        self.engine = engine
        self.wake, self.halt = threading.Event(), threading.Event()
        self.errors = 0          # written by this thread only
        self.stamp = 0
        self.rr = 0

    def request_stop(self) -> None:
        self.halt.set()
        self.wake.set()

    def run(self) -> None:
        eng = self.engine
        due = time.monotonic() + TOUCH_PERIOD
        while not self.halt.is_set():
            if time.monotonic() >= due:
                for b in eng.snapshot("active"):
                    self._pass(b, store=True)
                for b in eng.snapshot("dirty"):
                    self._pass(b, store=False)
                due = time.monotonic() + TOUCH_PERIOD
            if thrash := eng.snapshot("thrash"):
                self.rr %= len(thrash)
                self._pass(thrash[self.rr], store=True)
                self.rr += 1
                self.halt.wait(THRASH_PAUSE)
            else:
                self.wake.wait(max(0.0, due - time.monotonic()))
                self.wake.clear()

    def _pass(self, b: Block, store: bool) -> None:
        self.stamp = (self.stamp + 1) & 0xFF
        fill = memoryview(bytes((self.stamp,)) * (SLICE // PAGE))
        for off in range(0, b.size, SLICE):
            if self.halt.is_set():
                return
            with b.lock:
                if not b.alive:
                    return
                n = min(SLICE, b.size - off)
                if madvise(b.addr + off, n, MADV_POPULATE_WRITE):
                    self.errors += 1                     # ENOMEM/EINTR under OOM: skip the store
                    continue
                if store:
                    b.mv[off:off + n:PAGE] = fill[: n // PAGE]


class BalloonEngine:
    """Single owner thread for every block: allocation, free, pageout, sync, drop,
    compact and residency accounting are serialised through one command queue, so block
    lifetime needs no cross-thread reasoning except the pressure loop's per-block lock."""

    def __init__(self, chunk_mb: int, cache_dir: str, post: Callable[[Message], None]) -> None:
        self.chunk_mb, self.chunk = chunk_mb, chunk_mb * MIB
        self.cache_dir, self.post = cache_dir, post
        self.blocks: dict[str, list[Block]] = {c: [] for c in CATS}
        self.q: queue.SimpleQueue[tuple] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._gen = 0
        self.pending = dict.fromkeys(CATS, 0)
        self.current = ""                    # op in progress (engine writes, UI reads)
        self.released_mb = 0
        self.tmpl_ratio = 0.0
        self.thread = threading.Thread(target=self._run, name="dusky-balloon", daemon=True)
        self.pressure = PressureLoop(self)
        self._vec = bytearray(self.chunk // PAGE)
        self._vec_anchor = ctypes.c_char.from_buffer(self._vec)     # export pins the buffer (no resize)
        self._vec_addr = ctypes.addressof(self._vec_anchor)
        self._crange, self._cstat = _CacheRange(0, 0), _CacheStat()

    # -- UI-thread API --------------------------------------------------------
    def start(self) -> None:
        self.thread.start()
        self.pressure.start()

    def submit(self, *cmd: object) -> None:
        self.q.put(cmd)

    def submit_add(self, cat: str) -> int:
        with self._lock:
            self.pending[cat] += 1
            gen, n = self._gen, sum(self.pending.values())
        self.q.put(("add", cat, gen))
        return n

    def cancel_pending(self) -> None:
        with self._lock:
            self._gen += 1
            self.pending = dict.fromkeys(CATS, 0)

    def pending_snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self.pending)

    def snapshot(self, cat: str) -> tuple[Block, ...]:
        return tuple(self.blocks[cat])

    def shutdown(self, timeout: float = 15.0) -> int:
        self.pressure.request_stop()
        if self.thread.is_alive():
            self.q.put(("stop",))
            self.thread.join(timeout)
        else:
            self.released_mb = self._close_all()
        if self.pressure.is_alive():
            self.pressure.join(2.0)
        return self.released_mb

    # -- engine thread ---------------------------------------------------------
    @property
    def total_mb(self) -> int:
        return sum(map(len, self.blocks.values())) * self.chunk_mb

    def _toast(self, text: str, severity: str = "information", timeout: float = 3.0) -> None:
        self.post(Toast(text, severity, timeout))

    def _run(self) -> None:
        self._init_template()
        self._publish()
        while True:
            try:
                cmd = self.q.get(timeout=STATS_PERIOD)
            except queue.Empty:
                self._publish()
                continue
            op = cmd[0]
            if op == "stop":
                break
            self.current = f"{op} {cmd[1]}" if len(cmd) > 1 else op
            try:
                getattr(self, f"_op_{op}")(*cmd[1:])
            except Exception as exc:                     # the owner thread must never die
                self._toast(f"{op} failed: {exc}", "error", 5.0)
            self.current = ""
            self._publish()
        self.released_mb = self._close_all()

    def _init_template(self) -> None:
        """2 MiB template, per-page mix: 1/3 fresh entropy + 2/3 repeating pattern, so every
        4 KiB page compresses ~3:1 on its own (zram compresses page by page)."""
        rnd = PAGE // 3
        pattern = (b"DUSKY_ZRAM_" * (PAGE // 11 + 1))[: PAGE - rnd]
        entropy = os.urandom(rnd * (TEMPLATE // PAGE))
        mm = mmap.mmap(-1, TEMPLATE, flags=mmap.MAP_PRIVATE)
        for i in range(TEMPLATE // PAGE):
            o = i * PAGE
            mm[o:o + rnd] = entropy[i * rnd:(i + 1) * rnd]
            mm[o + rnd:o + PAGE] = pattern
        self.tmpl = mm
        self.tmpl_view = memoryview(mm)
        self._tmpl_anchor = ctypes.c_char.from_buffer(mm)
        self.tmpl_addr = ctypes.addressof(self._tmpl_anchor)
        name_vma(self.tmpl_addr, TEMPLATE, b"dusky:template")
        try:
            from compression import zstd                 # PEP 784 (3.14): per-page estimate, zram-style
            comp = sum(len(zstd.compress(self.tmpl_view[o:o + PAGE], level=1)) for o in range(0, TEMPLATE, PAGE))
            self.tmpl_ratio = TEMPLATE / comp
        except ImportError:
            self.tmpl_ratio = 0.0

    # -- fill paths (GIL released for all bulk work) ----------------------------
    def _fill_map(self, b: Block) -> None:
        if err := madvise(b.addr, b.size, MADV_POPULATE_WRITE):   # one syscall instead of 64k faults
            raise OSError(err, f"MADV_POPULATE_WRITE: {os.strerror(err)}")
        dst, src, end = b.addr, self.tmpl_addr, b.size
        full = end - end % TEMPLATE
        for off in range(0, full, TEMPLATE):
            _memmove(dst + off, src, TEMPLATE)
        if full < end:
            _memmove(dst + full, src, end - full)

    def _fill_fd(self, fd: int, size: int) -> None:
        off = 0
        while off < size:
            n = min(size - off, IOV_SPAN)
            whole, rem = divmod(n, TEMPLATE)
            iov = [self.tmpl_view] * whole
            if rem:
                iov.append(self.tmpl_view[:rem])
            written = os.pwritev(fd, iov, off)           # kernel copies the template n/2MiB times
            if written <= 0:
                raise OSError(errno.EIO, "short pwritev")
            off += written

    def _create(self, cat: str) -> Block:
        size = self.chunk
        if cat in ("dormant", "active", "thrash"):
            b = Block(cat, size, -1, mmap.mmap(-1, size, flags=mmap.MAP_PRIVATE))
            name_vma(b.addr, size, f"dusky:{cat}".encode())
            try:
                self._fill_map(b)
                if cat == "dormant" and (err := madvise(b.addr, size, MADV_COLD)):
                    raise OSError(err, f"MADV_COLD: {os.strerror(err)}")
            except BaseException:
                b.close()
                raise
            return b
        if cat == "shmem":
            fd = os.memfd_create("dusky:shmem", os.MFD_CLOEXEC | MFD_NOEXEC_SEAL)
        else:
            fd = os.open(self.cache_dir, os.O_TMPFILE | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            self._fill_fd(fd, size)
            if cat == "clean":
                os.fdatasync(fd)                         # make every page clean
            mm = (mmap.mmap(fd, size, flags=mmap.MAP_SHARED, trackfd=False)
                  if cat == "dirty" else None)           # mapping only needed for re-dirtying
        except BaseException:
            os.close(fd)
            raise
        return Block(cat, size, fd, mm)

    # -- residency accounting ---------------------------------------------------
    def _cachestat(self, fd: int, size: int) -> _CacheStat | None:
        self._crange.off, self._crange.len = 0, size
        rc = _libc.syscall(ctypes.c_long(NR_CACHESTAT), ctypes.c_long(fd), ctypes.byref(self._crange),
                           ctypes.byref(self._cstat), ctypes.c_long(0))
        return self._cstat if rc == 0 else None

    def _mincore(self, b: Block) -> int:
        pages = b.size // PAGE
        if _libc.mincore(b.addr, b.size, self._vec_addr) != 0:
            return -1
        return self._vec[:pages].translate(_LSB).count(1) * PAGE

    def _cat_stat(self, cat: str) -> CatStat:
        blocks = self.blocks[cat]
        if not blocks:
            return CatStat()
        res = dirty = wb = 0
        for b in blocks:
            if b.fd >= 0:
                if (cs := self._cachestat(b.fd, b.size)) is None:
                    res = -1
                    break
                res, dirty, wb = res + cs.cache * PAGE, dirty + cs.dirty * PAGE, wb + cs.writeback * PAGE
            elif (r := self._mincore(b)) >= 0:
                res += r
            else:
                res = -1
                break
        return CatStat(len(blocks), len(blocks) * self.chunk, res, dirty, wb)

    def _publish(self) -> None:
        self.post(BalloonUpdate(BalloonStats({c: self._cat_stat(c) for c in CATS}, self.total_mb,
                                             self.tmpl_ratio, self.pressure.errors)))

    def _close_all(self) -> int:
        mb = self.total_mb
        for blocks in self.blocks.values():
            while blocks:
                blocks.pop().close()
        return mb

    # -- commands -----------------------------------------------------------------
    def _op_add(self, cat: str, gen: int) -> None:
        with self._lock:
            if gen != self._gen:
                return                                   # cancelled by clear / earlier failure
            self.pending[cat] -= 1
        t0 = time.perf_counter()
        try:
            b = self._create(cat)
        except (OSError, MemoryError, ValueError) as exc:
            self.cancel_pending()
            self._toast(f"Allocation refused for {cat.upper()}: {exc} — queue cancelled", "error", 6.0)
            return
        self.blocks[cat].append(b)
        if cat == "thrash":
            self.pressure.wake.set()
        left = sum(self.pending_snapshot().values())
        self._toast(f"+{self.chunk_mb} MiB {cat.upper()} in {(time.perf_counter() - t0) * 1e3:.0f} ms · "
                    f"total {self.total_mb} MiB" + (f" · {left} queued" if left else ""))

    def _op_free(self, cat: str) -> None:
        if not self.blocks[cat]:
            self._toast(f"No {cat.upper()} blocks to free", "warning")
            return
        self.blocks[cat].pop().close()
        self._toast(f"-{self.chunk_mb} MiB {cat.upper()} · total {self.total_mb} MiB")

    def _op_clear(self) -> None:
        self._toast(f"Cleared all balloon blocks ({self._close_all()} MiB released)", "warning")

    def _op_stats(self) -> None:
        pass                                             # _run publishes after every command

    def _op_pageout(self) -> None:
        blocks = self.blocks["dormant"]
        if not blocks:
            self._toast("No DORMANT blocks to page out", "warning")
            return
        before = sum(max(self._mincore(b), 0) for b in blocks)
        t0 = time.perf_counter()
        errs = sum(1 for b in blocks if madvise(b.addr, b.size, MADV_PAGEOUT))
        dt = time.perf_counter() - t0
        after = sum(max(self._mincore(b), 0) for b in blocks)
        self._toast(f"MADV_PAGEOUT: {fmt_bytes(max(before - after, 0))} left RAM in {dt * 1e3:.0f} ms · "
                    f"{fmt_bytes(after)} still resident" + (f" · {errs} errors" if errs else ""),
                    "error" if errs else "information", 4.0)

    def _op_sync(self) -> None:
        blocks = self.blocks["dirty"]
        if not blocks:
            self._toast("No DIRTY blocks to sync", "warning")
            return
        dirty = sum(cs.dirty * PAGE for b in blocks if (cs := self._cachestat(b.fd, b.size)) is not None)
        t0 = time.perf_counter()
        for b in blocks:
            os.fdatasync(b.fd)                           # GIL released; page cache knows every dirty page
        self._toast(f"fdatasync ×{len(blocks)}: {fmt_bytes(dirty)} dirty flushed in {time.perf_counter() - t0:.2f} s")

    def _op_drop(self) -> None:
        t0 = time.perf_counter()
        os.sync()                                        # unprivileged; makes dirty pages droppable
        ok, msg = priv_write(["/proc/sys/vm/drop_caches"], "3\n")
        self._toast(f"drop_caches=3 done in {time.perf_counter() - t0:.2f} s" if ok else f"drop_caches: {msg}",
                    "information" if ok else "error", 5.0)

    def _op_compact(self) -> None:
        try:
            paths = sorted(f"/sys/block/{n}/compact" for n in os.listdir("/sys/block")
                           if n.startswith("zram") and os.path.exists(f"/sys/block/{n}/compact"))
        except OSError:
            paths = []
        if not paths:
            self._toast("No ZRAM devices to compact", "warning")
            return
        ok, msg = priv_write(paths, "1\n")
        self._toast(f"Compacted {len(paths)} ZRAM device(s)" if ok else f"compact: {msg}",
                    "information" if ok else "error", 5.0)


def priv_write(paths: list[str], value: str) -> tuple[bool, str]:
    """Root: direct writes. Otherwise one `sudo -n tee` (NOPASSWD rule or cached ticket);
    DUSKY_SUDO_PASSWORD, if exported, primes the ticket once via `sudo -S -v`."""
    if os.geteuid() == 0:
        errs = []
        for p in paths:
            try:
                fd = os.open(p, os.O_WRONLY)
                try:
                    os.write(fd, value.encode())
                finally:
                    os.close(fd)
            except OSError as exc:
                errs.append(f"{p}: {exc.strerror}")
        return not errs, "; ".join(errs)
    if (sudo := shutil.which("sudo")) is None:
        return False, "not root and sudo not found (run with --root)"
    tee = shutil.which("tee") or "/usr/bin/tee"
    try:
        if pw := os.environ.get("DUSKY_SUDO_PASSWORD"):
            subprocess.run([sudo, "-S", "-p", "", "-v"], input=f"{pw}\n".encode(),
                           capture_output=True, timeout=15)
        r = subprocess.run([sudo, "-n", tee, *paths], input=value.encode(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if r.returncode == 0:
        return True, ""
    err = r.stderr.decode(errors="replace").strip().splitlines()
    return False, (err[-1] if err else f"sudo exit {r.returncode}") + " (use --root or `sudo -v`)"


def rows_balloon(stats: BalloonStats | None, pending: dict[str, int], sel: str,
                 chunk_mb: int, backing: str, busy: str) -> list[Row]:
    rows: list[Row] = []
    for c in CATS:
        name, desc = CAT_META[c]
        cs = stats.cats[c] if stats else CatStat()
        on_ = c == sel
        left = (t("▶ " if on_ else "  ", OKB), t(name, OKB if on_ else FGS), t(f" ({desc})", D))
        right: list[Segment] = []
        if cs.blocks:
            right += [t(f"{cs.blocks}×{chunk_mb}M ", V), t(f"{cs.blocks * chunk_mb} MiB", WB)]
            if cs.resident >= 0:
                right.append(t(f" · {cs.resident / cs.size:.0%} res", D))
                if c == "dirty":
                    right.append(t(f" · {cs.dirty / cs.size:.0%} dirty · {cs.writeback / cs.size:.0%} wb", W))
        else:
            right.append(t("0 MiB", D))
        if p := pending.get(c):
            right.append(t(f" +{p} queued", A))
        rows.append((left, tuple(right)))
    rows.append(GAP)
    total_mb = stats.total_mb if stats else 0
    nblocks = sum(s.blocks for s in stats.cats.values()) if stats else 0
    rows.append((L("Total injected load"), R(t(f"{total_mb} MiB", WB), t(f" ({nblocks} blocks)", D))))
    rows.append((L("Engine"), R(t(f"running: {busy}", A) if busy else t("idle", D))))
    rows.append((L("Page-cache backing"), R(t(backing, D))))
    if stats and stats.tmpl_ratio:
        rows.append((L("Template", "per-page 1/3 entropy"), R(t(f"zstd-1 est. {stats.tmpl_ratio:.2f}× per page", D))))
    if stats and stats.touch_errors:
        rows.append((L("Pressure-loop populate errors"), R(t(str(stats.touch_errors), EB))))
    rows.append((L("Quick actions", style=D), R(t("p=PageOut s=Sync d=Drop k=Compact c=Clear", D))))
    return rows


# --------------------------------------------------------------------------- #
#  Textual UI                                                                 #
# --------------------------------------------------------------------------- #
class KVPanel(Widget):
    """Line-API key/value panel: no Rich Table measurement, no markup parsing, no
    double render for height:auto. Unchanged rows skip the refresh; relayout happens
    only when the row count changes."""

    def __init__(self, title: str, *, id: str) -> None:
        super().__init__(id=id)
        self.border_title = title
        self._rows: list[Row] = [(L("sampling …", style=D), ())]

    def set_rows(self, rows: list[Row]) -> None:
        if rows == self._rows:
            return
        relayout = len(rows) != len(self._rows)
        self._rows = rows
        self.refresh(layout=relayout)

    def get_content_height(self, container, viewport, width: int) -> int:
        return max(1, len(self._rows))

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        base = self.visual_style.rich_style
        if y >= len(self._rows):
            return Strip.blank(width, base)
        left, right = self._rows[y]
        rs = Strip(right)
        ls = Strip(left)
        room = width - rs.cell_length - (1 if right else 0)
        if ls.cell_length > room:
            ls = ls.crop(0, max(room, 0))
        pad = width - ls.cell_length - rs.cell_length
        return Strip([*ls, Segment(" " * max(pad, 0)), *rs]).crop(0, width).apply_style(base)


HELP_TEXT = Text.assemble(
    ("Synthetic balloon categories\n", f"bold {ACCENT}"),
    "  1 Dormant anon (MADV_COLD)          2 Active anon (kept hot)\n"
    "  3 Clean cache (fdatasync'd)          4 Dirty cache (re-dirtied)\n"
    "  5 Shmem (memfd / tmpfs)              6 Thrash (continuous cycling)\n\n",
    ("Memory pressure actions\n", f"bold {ACCENT}"),
    "  + / =  allocate one chunk            - / _  free one chunk\n"
    "  p      MADV_PAGEOUT dormant          s      fdatasync dirty blocks\n"
    "  d      sync + drop_caches (root)     k      compact ZRAM (root)\n"
    "  c      clear all balloons + queue\n\n",
    ("Navigation\n", f"bold {ACCENT}"),
    "  r      immediate full poll           F1 / ? toggle this help\n"
    "  q/Esc  release all memory and quit",
)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,f1,question_mark,q,enter,space", "close_help", "Close", show=False, priority=True)]

    def compose(self) -> ComposeResult:
        with Container(id="help_dialog"):
            yield Static("󰌌 Dusky RAM Analyzer & ZRAM Benchmark — Shortcuts", id="modal-title")
            yield Static(HELP_TEXT, id="modal-text")
            with Horizontal(id="modal_btn_container"):
                yield Button("Close (F1 / Esc)", id="btn_modal_close", action="screen.close_help")

    def action_close_help(self) -> None:
        self.dismiss(None)

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:                         # click outside the dialog
            self.dismiss(None)


def _css() -> str:
    btn = {
        "btn_help": ("#1e293b", "#38bdf8", "#0284c7", "#ffffff"),
        "btn_quit": ("#3f1d24", "#fca5a5", "#b91c1c", "#ffffff"),
        "btn_add": ("#14532d", "#bbf7d0", "#22c55e", "#000000"),
        "btn_free": ("#713f12", "#fef08a", "#f59e0b", "#000000"),
        "btn_pageout": ("#581c87", "#f3e8ff", "#a855f7", "#ffffff"),
        "btn_sync": ("#0369a1", "#e0f2fe", "#38bdf8", "#000000"),
        "btn_drop": ("#9a3412", "#ffedd5", "#ea580c", "#ffffff"),
        "btn_compact": ("#064e3b", "#a7f3d0", "#10b981", "#000000"),
        "btn_clear": ("#881337", "#ffe4e6", "#f43f5e", "#ffffff"),
        "btn_refresh": ("#334155", "#cbd5e1", "#64748b", "#ffffff"),
    }
    buttons = "\n".join(
        f"#{i} {{ background: {bg}; color: {fg}; }}\n#{i}:hover, #{i}:focus {{ background: {hbg}; color: {hfg}; }}"
        for i, (bg, fg, hbg, hfg) in btn.items())
    return f"""
    Screen {{ layout: vertical; background: {BG}; }}
    #main_container {{ layout: horizontal; height: 1fr; padding: 0 1; }}
    .column {{ width: 1fr; height: 100%; padding: 0 1; scrollbar-size: 1 1;
               scrollbar-background: {BG}; scrollbar-color: {MUTED}; scrollbar-color-hover: {ACCENT}; }}
    KVPanel {{ height: auto; margin-bottom: 1; padding: 0 1; background: {BG}; color: {FG};
               border-title-style: bold; }}
    #p_mem, #p_zram {{ border: round {ACCENT}; border-title-color: {ACCENT}; }}
    #p_psi {{ border: round {WARNING}; border-title-color: {WARNING}; }}
    #p_proc {{ border: round {MUTED}; border-title-color: {ACCENT}; }}
    #p_vm, #p_balloon {{ border: round {SUCCESS}; border-title-color: {SUCCESS}; }}
    #controls_container {{ dock: bottom; height: auto; background: {BG}; }}
    .btn-row {{ layout: horizontal; width: 100%; height: 1; padding: 0 1; }}
    .btn-group {{ width: auto; height: 1; }}
    .spacer {{ width: 1fr; height: 1; }}
    Button {{ height: 1; min-height: 1; min-width: 0; width: auto; border: none; padding: 0;
              margin-right: 1; text-style: bold; }}
    Button:last-child {{ margin-right: 0; }}
    .type-btn {{ background: #221c20; color: #a89ba0; }}
    .type-btn:hover, .type-btn:focus {{ background: #3b2d35; color: {FG}; }}
    .type-btn.active-type {{ background: {ACCENT}; color: {BG}; }}
    .type-btn.active-type:hover, .type-btn.active-type:focus {{ background: {SUCCESS}; color: {BG}; }}
    {buttons}
    HelpScreen {{ align: center middle; }}
    #help_dialog {{ width: 76; height: auto; max-height: 95%; overflow-y: auto; background: {BG};
                    border: heavy {ACCENT}; padding: 0 1; }}
    #modal-title {{ color: {ACCENT}; text-style: bold; text-align: center; }}
    #modal-text {{ color: {FG}; }}
    #modal_btn_container {{ height: 1; align-horizontal: center; margin-top: 1; }}
    Button#btn_modal_close {{ background: {ACCENT}; color: {BG}; padding: 0 1; margin: 0; }}
    Button#btn_modal_close:hover, Button#btn_modal_close:focus {{ background: {SUCCESS}; color: {BG}; }}
    """


class DuskyRAMAnalyzer(App):
    TITLE = "DUSKY RAM ANALYZER & BALLOON BENCHMARK"
    ENABLE_COMMAND_PALETTE = False
    CSS = _css()
    BINDINGS = [
        Binding("f1,question_mark", "help", "Help", priority=True),
        *(Binding(str(i), f"set_type('{c}')", c.title(), show=False) for i, c in enumerate(CATS, 1)),
        Binding("plus,equals_sign", "add_chunk", "Add"),
        Binding("minus,underscore", "free_chunk", "Free"),
        Binding("p", "pageout", "PageOut"),
        Binding("s", "sync_dirty", "Sync"),
        Binding("d", "drop_caches", "Drop"),
        Binding("k", "compact_zram", "Compact"),
        Binding("c", "clear_all", "Clear"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("q,escape", "quit_app", "Quit"),
    ]

    def __init__(self, interval: float = 1.0, chunk_mb: int = 250,
                 cache_dir: str = "/var/tmp", cache_fs: str = "?") -> None:
        super().__init__()
        self.chunk_mb = chunk_mb
        self.category = "dormant"
        self.backing = f"{cache_dir} ({cache_fs})"
        self.bstats: BalloonStats | None = None
        self.engine = BalloonEngine(chunk_mb, cache_dir, self._post)
        self.sampler = Sampler(self._post, interval)

    def _post(self, msg: Message) -> None:
        try:
            self.post_message(msg)                       # thread-safe: call_soon_threadsafe
        except RuntimeError:
            pass                                         # loop already closed during shutdown

    def compose(self) -> ComposeResult:
        with Horizontal(id="main_container"):
            with VerticalScroll(classes="column"):
                yield KVPanel("System Memory Topology", id="p_mem")
                yield KVPanel("Memory Pressure (PSI) & OOM Dynamics", id="p_psi")
                yield KVPanel("Top Memory Consumers (RSS & VmSwap)", id="p_proc")
            with VerticalScroll(classes="column"):
                yield KVPanel("ZRAM Multi-Device Diagnostics", id="p_zram")
                yield KVPanel("Live Kernel VM Policies & Tunables", id="p_vm")
                yield KVPanel("RAM Ballooning Engine", id="p_balloon")
        chunk = self.chunk_mb
        with Container(id="controls_container"):
            with Horizontal(classes="btn-row"):
                with Horizontal(classes="btn-group"):
                    for i, c in enumerate(CATS, 1):
                        yield Button(f"{i}:{c.title()}", id=f"type_{c}",
                                     classes="type-btn active-type" if c == self.category else "type-btn",
                                     action=f"app.set_type('{c}')")
                yield Static(classes="spacer")
                with Horizontal(classes="btn-group"):
                    yield Button("󰌌 F1 Help", id="btn_help", action="app.help")
                    yield Button("q Quit", id="btn_quit", action="app.quit_app")
            with Horizontal(classes="btn-row"):
                with Horizontal(classes="btn-group"):
                    yield Button(f"+ {chunk}M", id="btn_add", action="app.add_chunk")
                    yield Button(f"- {chunk}M", id="btn_free", action="app.free_chunk")
                yield Static(classes="spacer")
                with Horizontal(classes="btn-group"):
                    yield Button("p PageOut", id="btn_pageout", action="app.pageout")
                    yield Button("s Sync", id="btn_sync", action="app.sync_dirty")
                    yield Button("d Drop", id="btn_drop", action="app.drop_caches")
                    yield Button("k Compact", id="btn_compact", action="app.compact_zram")
                yield Static(classes="spacer")
                with Horizontal(classes="btn-group"):
                    yield Button("c Clear", id="btn_clear", action="app.clear_all")
                    yield Button("r Ref", id="btn_refresh", action="app.refresh_now")

    def on_mount(self) -> None:
        self.register_theme(Theme(name="dusky", primary=ACCENT, secondary=WARNING, accent=ACCENT,
                                  warning=WARNING, error=ERROR, success=SUCCESS, foreground=FG,
                                  background=BG, surface=BG, dark=True))
        self.theme = "dusky"                             # toasts/scrollbars follow the Matugen palette
        self.panels = {p.id: p for p in self.query(KVPanel)}
        self._render_balloon()
        self.engine.start()
        self.sampler.start()

    def shutdown(self) -> int:
        self.sampler.halt.set()
        self.sampler.kick.set()
        if self.sampler.is_alive():
            self.sampler.join(2.0)
        return self.engine.shutdown()

    # -- message handlers (UI thread) -------------------------------------------
    @on(SampleReady)
    def _on_sample(self, msg: SampleReady) -> None:
        for pid, rows in msg.panels.items():
            self.panels[pid].set_rows(rows)
        self._render_balloon()                           # keeps "engine running: …" live

    @on(BalloonUpdate)
    def _on_balloon(self, msg: BalloonUpdate) -> None:
        self.bstats = msg.stats
        self._render_balloon()

    @on(Toast)
    def _on_toast(self, msg: Toast) -> None:
        self.notify(msg.text, severity=msg.severity, timeout=msg.timeout, markup=False)

    def _render_balloon(self) -> None:
        panel = self.panels["p_balloon"]
        title = f"RAM Ballooning Engine — selected: {self.category.upper()}"
        if panel.border_title != title:
            panel.border_title = title
        panel.set_rows(rows_balloon(self.bstats, self.engine.pending_snapshot(), self.category,
                                    self.chunk_mb, self.backing, self.engine.current))

    def _say(self, text: str, severity: str = "information", timeout: float = 1.5) -> None:
        self.notify(text, severity=severity, timeout=timeout, markup=False)

    # -- actions -----------------------------------------------------------------
    def action_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss(None)
        else:
            self.push_screen(HelpScreen())

    def action_set_type(self, cat: str) -> None:
        if cat not in CATS or cat == self.category:
            return
        self.query_one(f"#type_{self.category}", Button).remove_class("active-type")
        self.query_one(f"#type_{cat}", Button).add_class("active-type")
        self.category = cat
        self._render_balloon()

    def action_add_chunk(self) -> None:
        n = self.engine.submit_add(self.category)
        self._say(f"Queued +{self.chunk_mb} MiB {self.category.upper()} ({n} pending)", "warning")
        self._render_balloon()

    def action_free_chunk(self) -> None:
        self.engine.submit("free", self.category)

    def action_pageout(self) -> None:
        self._say("MADV_PAGEOUT on dormant blocks …")
        self.engine.submit("pageout")

    def action_sync_dirty(self) -> None:
        self._say("fdatasync on dirty blocks …")
        self.engine.submit("sync")

    def action_drop_caches(self) -> None:
        self._say("sync + drop_caches …", "warning")
        self.engine.submit("drop")

    def action_compact_zram(self) -> None:
        self._say("Compacting ZRAM …", "warning")
        self.engine.submit("compact")

    def action_clear_all(self) -> None:
        self.engine.cancel_pending()                     # queued adds die before they allocate
        self.engine.submit("clear")
        self._render_balloon()

    def action_refresh_now(self) -> None:
        self.sampler.kick.set()
        self.engine.submit("stats")

    def action_quit_app(self) -> None:
        self.exit()


# --------------------------------------------------------------------------- #
#  privilege escalation, page-cache backing, CLI                              #
# --------------------------------------------------------------------------- #
_KEEP_ENV = {"TERM", "COLORTERM", "TERMINFO", "TERMINFO_DIRS", "LANG", "LC_ALL", "LC_CTYPE",
             "PATH", "XDG_CONFIG_HOME", "DUSKY_CACHE_DIR"}


def elevate() -> None:
    """Opt-in re-exec under sudo; forwards terminal/colour/locale (+ TEXTUAL_*) variables."""
    if os.geteuid() == 0:
        return
    if (sudo := shutil.which("sudo")) is None:
        print("[warn] sudo not found; continuing unprivileged.", file=sys.stderr)
        return
    keep = [f"{k}={v}" for k, v in os.environ.items() if k in _KEEP_ENV or k.startswith("TEXTUAL_")]
    argv = [sudo, "--", "/usr/bin/env", *keep, sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]]
    try:
        os.execv(sudo, argv)
    except OSError as exc:
        print(f"[warn] escalation failed ({exc}); continuing unprivileged.", file=sys.stderr)


def pick_cache_dir() -> tuple[str, str]:
    """A disk-backed directory for page-cache balloons: on tmpfs/ramfs (or a zram-backed
    fs) the 'file cache' categories would silently become shmem."""
    table = []
    try:
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            left, _, right = line.partition(" - ")
            lf, rf = left.split(), right.split()
            if len(lf) >= 5 and len(rf) >= 2:
                table.append((lf[4].replace("\\040", " "), rf[0], rf[1]))
    except OSError:
        pass

    def fs_of(path: str) -> tuple[str, str]:
        real, best = os.path.realpath(path), ("", "?", "?")
        for mnt, fstype, src in table:
            if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best[0]):
                best = (mnt, fstype, src)
        return best[1], best[2]

    xdg_cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    for cand in (os.environ.get("DUSKY_CACHE_DIR"), "/var/tmp", xdg_cache):
        if cand and os.path.isdir(cand) and os.access(cand, os.W_OK):
            fstype, src = fs_of(cand)
            if fstype not in ("tmpfs", "ramfs") and not src.startswith("/dev/zram"):
                return cand, fstype
    return "/var/tmp", fs_of("/var/tmp")[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="zram_test", description="Dusky RAM Analyzer & Synthetic Memory Balloon Benchmark TUI.")
    parser.add_argument("-i", "--interval", type=float, default=1.0,
                        help="dashboard sampling interval in seconds (default: 1.0, min 0.25)")
    parser.add_argument("-c", "--chunk", type=int, default=250,
                        help="MiB allocated per balloon chunk (default: 250)")
    parser.add_argument("-r", "--root", action="store_true",
                        help="opt-in privilege escalation via sudo (preserves terminal env)")
    args = parser.parse_args()
    if args.root:
        elevate()
    cache_dir, cache_fs = pick_cache_dir()
    app = DuskyRAMAnalyzer(interval=max(0.25, args.interval), chunk_mb=max(1, args.chunk),
                           cache_dir=cache_dir, cache_fs=cache_fs)
    try:
        app.run()
    finally:
        released = app.shutdown()
    print(f"\n[ok] Dusky RAM Analyzer terminated; released {released} MiB of synthetic balloon memory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

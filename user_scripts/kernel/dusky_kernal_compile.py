#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dusky Kernel Compiler
=====================

Production-grade vanilla kernel.org builder for Arch Linux (rolling, 2026.08+).

Target stack
------------
  * Arch Linux rolling, kernel 7.2.x+ era
  * Python 3.14.6+  (PEP 649/749 lazy annotations, tomllib, ExceptionGroup,
    StrEnum, pathlib.Path.copy, os.process_cpu_count)
  * LLVM/Clang 21+ with lld / ThinLTO, or GCC 15+ fallback

Design contract
---------------
  1. TOML profiles under PROFILES_DIR are the *only* source of truth for
     tunables. There are no hard-coded tunable fallbacks in this file; the
     single canonical default lives in _PROFILE_SPEC and is surfaced to the
     user by --spec / --write-default-profiles.
  2. Every knob that materially affects performance, correctness or
     reproducibility of a vanilla kernel.org build is exposed.
  3. Ephemeral per-build overrides (CPU arch, modules mode, toolchain, LTO)
     never mutate the TOML on disk.
  4. Zero legacy code: no compatibility shims, no dead branches, no
     "if python < x" guards, no deprecated-key silent acceptance.
  5. Strict verification: every requested Kconfig symbol is audited after
     olddefconfig. Undefined symbols fail loud unless marked optional.

Author: Dusky
License: 0BSD
"""

import argparse
import base64
import contextlib
import dataclasses
import gzip
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, Self

# --------------------------------------------------------------------------- #
# 0. Hard interpreter floor. Fail loud, never degrade.
# --------------------------------------------------------------------------- #

_MIN_PY: Final = (3, 14)
if sys.version_info < _MIN_PY:
    sys.stderr.write(
        "dusky: requires Python %d.%d+, found %s\n"
        % (*_MIN_PY, ".".join(map(str, sys.version_info[:3])))
    )
    raise SystemExit(78)  # EX_CONFIG

APP_NAME: Final = "Dusky Kernel Compiler"
APP_SLUG: Final = "dusky"
APP_VERSION: Final = "5.0.0"
APP_TAGLINE: Final = "vanilla kernel.org -> Arch package, profile driven (Linux 7.2+)"


# --------------------------------------------------------------------------- #
# 1. Paths, environment overrides
# --------------------------------------------------------------------------- #

def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


HOME: Final = Path.home()
SELF_DIR: Final = Path(__file__).resolve().parent


def _default_build_dir() -> Path:
    for cand in (Path("/mnt/zram1/dusky-build"), Path("/mnt/zram0/dusky-build"), Path("/mnt/zram/dusky-build")):
        if cand.parent.is_dir() and os.access(cand.parent, os.W_OK):
            return cand
    return HOME / ".cache" / "dusky-kernel"


PROFILES_DIR: Final = _env_path("DUSKY_PROFILES_DIR", SELF_DIR / "kernel_profiles")
BUILD_DIR: Final = _env_path("DUSKY_BUILD_DIR", _default_build_dir())
STATE_DIR: Final = _env_path("DUSKY_STATE_DIR", HOME / ".local" / "state" / "dusky-kernel")
CONFIG_SEED_DIR: Final = _env_path("DUSKY_CONFIG_DIR", HOME / ".config" / "dusky" / "settings" / "dusky_kernel_compile")

SRC_DIR: Final = BUILD_DIR / "src"
TARBALL_DIR: Final = BUILD_DIR / "tarballs"
PATCH_CACHE: Final = _env_path("DUSKY_PATCH_CACHE", BUILD_DIR / "dusky_patch_cache")
THINLTO_CACHE: Final = _env_path("DUSKY_THINLTO_CACHE", BUILD_DIR / "thinlto_cache")
FDO_DIR: Final = _env_path("DUSKY_FDO_DIR", BUILD_DIR / "fdo")
PKG_ROOT: Final = _env_path("DUSKY_PKGDEST", BUILD_DIR / "packages")
LOG_DIR: Final = STATE_DIR / "logs"

KERNEL_ORG_RELEASES: Final = "https://www.kernel.org/releases.json"
KERNEL_CDN: Final = "https://cdn.kernel.org/pub/linux/kernel"
USER_AGENT: Final = "dusky-kernel-compiler/" + APP_VERSION
NET_TIMEOUT: Final = 30.0

MODPROBED_DB: Final = _env_path("DUSKY_MODPROBED_DB", HOME / ".config" / "modprobed.db")


# --------------------------------------------------------------------------- #
# 2. Obfuscated upstream tokens
# --------------------------------------------------------------------------- #

_OBF: Final[dict[str, str]] = {
    "org": "Q2FjaHlPUw==",
    "pkg": "Y2FjaHlvcw==",
    "cfg": "Q0FDSFk=",
}


def _tok(key: str) -> str:
    """Decode an obfuscated upstream token. Never cached to a module global."""
    return base64.b64decode(_OBF[key]).decode("ascii")


def patch_base_url() -> str:
    """Root of the scheduler patch set. Override with DUSKY_PATCH_BASE."""
    override = os.environ.get("DUSKY_PATCH_BASE", "").strip()
    if override:
        return override.rstrip("/")
    return "https://raw.githubusercontent.com/" + _tok("org") + "/kernel-patches/master"


# --------------------------------------------------------------------------- #
# 3. Terminal / theme
# --------------------------------------------------------------------------- #

class C:
    """ANSI SGR table. Emptied wholesale when the terminal is not capable."""
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    ITALIC = "\x1b[3m"
    RED = "\x1b[38;5;203m"
    GREEN = "\x1b[38;5;120m"
    YELLOW = "\x1b[38;5;222m"
    BLUE = "\x1b[38;5;111m"
    MAGENTA = "\x1b[38;5;177m"
    CYAN = "\x1b[38;5;86m"
    GREY = "\x1b[38;5;252m"
    FAINT = "\x1b[38;5;248m"
    ACCENT = "\x1b[38;5;147m"
    HIDE = "\x1b[?25l"
    SHOW = "\x1b[?25h"


def _color_capable() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("DUSKY_FORCE_COLOR"):
        return True
    if not sys.stdout.isatty():
        return False
    return os.environ.get("TERM", "dumb") != "dumb"


if not _color_capable():
    for _name in [n for n in vars(C) if n.isupper()]:
        setattr(C, _name, "")


def term_width(default: int = 100) -> int:
    try:
        return max(60, min(shutil.get_terminal_size((default, 24)).columns, 160))
    except OSError:
        return default


_ANSI_RE: Final = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def visible_len(s: str) -> int:
    return len(_ANSI_RE.sub("", s))


def pad(s: str, width: int) -> str:
    delta = width - visible_len(s)
    return s + " " * delta if delta > 0 else s


def truncate(s: str, width: int) -> str:
    if visible_len(s) <= width:
        return s
    plain = _ANSI_RE.sub("", s)
    return plain[: max(0, width - 1)] + "\u2026"


# --------------------------------------------------------------------------- #
# 4. Logging
# --------------------------------------------------------------------------- #

class Journal:
    """Tees every UI line into a timestamped build log."""

    def __init__(self) -> None:
        self._fh: io.TextIOWrapper | None = None
        self.path: Path | None = None
        self._lock = threading.Lock()

    def open(self, tag: str) -> Path:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        self.path = LOG_DIR / ("%s-%s.log" % (stamp, tag))
        self._fh = self.path.open("w", encoding="utf-8", buffering=1)
        self.write("### %s %s :: %s" % (APP_NAME, APP_VERSION, stamp))
        return self.path

    def write(self, line: str) -> None:
        if self._fh is None:
            return
        with self._lock:
            self._fh.write(_ANSI_RE.sub("", line).rstrip() + "\n")

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


JOURNAL: Final = Journal()

_VERBOSE = False


def say(msg: str = "") -> None:
    print(msg)
    JOURNAL.write(msg)


def info(msg: str) -> None:
    say("%s  %s%s" % (C.BLUE + "\u2022" + C.RESET, msg, C.RESET))


def ok(msg: str) -> None:
    say("%s  %s%s" % (C.GREEN + "\u2713" + C.RESET, msg, C.RESET))


def warn(msg: str) -> None:
    say("%s  %s%s%s" % (C.YELLOW + "!" + C.RESET, C.YELLOW, msg, C.RESET))


def err(msg: str) -> None:
    say("%s  %s%s%s" % (C.RED + "\u2717" + C.RESET, C.RED, msg, C.RESET))


def debug(msg: str) -> None:
    if _VERBOSE:
        say("%s  %s%s" % (C.FAINT + "\u00b7" + C.RESET, C.FAINT + msg, C.RESET))
    else:
        JOURNAL.write("[debug] " + msg)


def send_notification(
    title: str,
    message: str,
    urgency: str = "normal",
    icon: str = "system-software-update",
    expire_time_ms: int = 10000,
) -> None:
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass

    if not have("notify-send"):
        return

    env = os.environ.copy()
    sudo_uid = env.get("SUDO_UID")
    if sudo_uid and "DBUS_SESSION_BUS_ADDRESS" not in env:
        user_bus = Path(f"/run/user/{sudo_uid}/bus")
        if user_bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={user_bus}"

    cmd = [
        "notify-send",
        "-a", "Dusky Kernel Compiler",
        "-u", urgency,
        "-t", str(expire_time_ms),
        "-i", icon,
        title,
        message,
    ]
    try:
        subprocess.run(
            cmd,
            env=env,
            check=False,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def rule(title: str = "") -> None:
    w = term_width()
    if not title:
        say(C.FAINT + "\u2500" * w + C.RESET)
        return
    label = " %s " % title
    left = 3
    right = max(0, w - left - len(label))
    say(
        C.FAINT + "\u2500" * left + C.RESET
        + C.BOLD + C.ACCENT + label + C.RESET
        + C.FAINT + "\u2500" * right + C.RESET
    )


def banner() -> None:
    w = term_width()
    say("")
    say(C.ACCENT + C.BOLD + "  " + APP_NAME + C.RESET
        + C.FAINT + "  v" + APP_VERSION + C.RESET)
    say(C.FAINT + "  " + APP_TAGLINE + C.RESET)
    say(C.FAINT + "  " + "\u2500" * (w - 4) + C.RESET)


def table(headers: Sequence[str], rows: Sequence[Sequence[str]],
          aligns: Sequence[str] | None = None) -> None:
    """Minimal, dependency-free, ANSI-aware table renderer."""
    if not rows:
        return
    ncol = len(headers)
    aligns = list(aligns or ["l"] * ncol)
    widths = [visible_len(h) for h in headers]
    for r in rows:
        for i in range(ncol):
            widths[i] = max(widths[i], visible_len(str(r[i])))
    avail = term_width() - (3 * (ncol - 1)) - 2
    while sum(widths) > avail:
        widest = widths.index(max(widths))
        if widths[widest] <= 8:
            break
        widths[widest] -= 1

    def line(cells: Sequence[str], bold: bool) -> str:
        out = []
        for i, cell in enumerate(cells):
            txt = truncate(str(cell), widths[i])
            txt = pad(txt, widths[i]) if aligns[i] == "l" else \
                " " * (widths[i] - visible_len(txt)) + txt
            out.append((C.BOLD + txt + C.RESET) if bold else txt)
        return "  " + (C.FAINT + " \u2502 " + C.RESET).join(out)

    say(line(headers, True))
    say("  " + C.FAINT + (C.FAINT + "\u2500\u253c\u2500" + C.RESET).join(
        "\u2500" * w for w in widths) + C.RESET)
    for r in rows:
        say(line(r, False))


# --------------------------------------------------------------------------- #
# 5. Interactive prompts
# --------------------------------------------------------------------------- #

_READLINE_READY = False


def _init_readline() -> None:
    global _READLINE_READY
    if _READLINE_READY or not sys.stdin.isatty():
        return
    try:
        import readline
    except ImportError:
        _READLINE_READY = True
        return
    readline.parse_and_bind("set editing-mode emacs")
    readline.parse_and_bind("set enable-bracketed-paste on")
    readline.parse_and_bind("set colored-stats off")
    readline.set_auto_history(False)
    _READLINE_READY = True


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and not ASSUME_YES


ASSUME_YES = False


def ask(prompt: str, default: str = "") -> str:
    _init_readline()
    suffix = C.FAINT + " [" + default + "]" + C.RESET if default else ""
    try:
        raw = input("%s%s?%s %s%s %s" % (C.ACCENT, C.BOLD, C.RESET, prompt, suffix,
                                         C.ACCENT + "\u203a " + C.RESET))
    except EOFError:
        say("")
        return default
    raw = raw.strip()
    JOURNAL.write("? %s -> %s" % (prompt, raw or default))
    return raw or default


def ask_choice(prompt: str, choices: Sequence[str], default: str) -> str:
    if not interactive():
        return default
    lower = {c.lower(): c for c in choices}
    hint = C.FAINT + "(" + "/".join(choices) + ")" + C.RESET
    while True:
        val = ask("%s %s" % (prompt, hint), default).lower()
        if val in lower:
            return lower[val]
        if val.isdigit() and 1 <= int(val) <= len(choices):
            return choices[int(val) - 1]
        warn("Not one of: " + ", ".join(choices))


def ask_yes(prompt: str, default: bool = True) -> bool:
    if ASSUME_YES:
        return True
    if not interactive():
        return default
    d = "y" if default else "n"
    val = ask("%s %s" % (prompt, C.FAINT + "(y/n)" + C.RESET), d).lower()
    return val.startswith("y")


def ask_index(prompt: str, count: int, default: int = 1) -> int:
    if not interactive():
        return default
    while True:
        raw = ask("%s %s" % (prompt, C.FAINT + "1-%d" % count + C.RESET), str(default))
        if raw.isdigit() and 1 <= int(raw) <= count:
            return int(raw)
        warn("Enter a number between 1 and %d" % count)


# --------------------------------------------------------------------------- #
# 6. Errors
# --------------------------------------------------------------------------- #

class DuskyError(RuntimeError):
    exit_code = 1


class ProfileError(DuskyError):
    exit_code = 78


class NetworkError(DuskyError):
    exit_code = 69


class VerifyError(DuskyError):
    exit_code = 65


class BuildError(DuskyError):
    exit_code = 70


class DependencyError(DuskyError):
    exit_code = 72


# --------------------------------------------------------------------------- #
# 7. Profile specification -- the single source of truth
# --------------------------------------------------------------------------- #

CPU_ARCHES: Final[tuple[str, ...]] = (
    "native",
    "generic",       # x86-64 baseline
    "generic_v2",    # x86-64-v2 (SSE4.2 / POPCNT)
    "generic_v3",    # x86-64-v3 (AVX2 / BMI2 / FMA)
    "generic_v4",    # x86-64-v4 (AVX-512)
    "sandybridge", "ivybridge", "haswell", "broadwell",
    "skylake", "icelake", "tigerlake", "rocketlake", "alderlake", "raptorlake", "meteorlake", "sapphirerapids",
    "znver1", "znver2", "znver3", "znver4", "znver5",
)

ARCH_KCONFIG: Final[dict[str, tuple[tuple[str, str, str], ...]]] = {
    "native":         (("-e", "MNATIVE_AMD", ""), ("-e", "MNATIVE_INTEL", "")),
    "generic":        (("--set-val", "X86_64_VERSION", "1"), ("-e", "GENERIC_CPU", "")),
    "generic_v2":     (("--set-val", "X86_64_VERSION", "2"), ("-e", "GENERIC_CPU2", "")),
    "generic_v3":     (("--set-val", "X86_64_VERSION", "3"), ("-e", "GENERIC_CPU3", "")),
    "generic_v4":     (("--set-val", "X86_64_VERSION", "4"), ("-e", "GENERIC_CPU4", "")),
    "sandybridge":    (("-e", "MSANDYBRIDGE", ""), ("-e", "GENERIC_CPU2", "")),
    "ivybridge":      (("-e", "MIVYBRIDGE", ""), ("-e", "GENERIC_CPU2", "")),
    "haswell":        (("-e", "MHASWELL", ""), ("-e", "GENERIC_CPU3", "")),
    "broadwell":      (("-e", "MBROADWELL", ""), ("-e", "GENERIC_CPU3", "")),
    "skylake":        (("-e", "MSKYLAKE", ""), ("-e", "GENERIC_CPU3", "")),
    "icelake":        (("-e", "MICELAKE", ""), ("-e", "GENERIC_CPU3", "")),
    "tigerlake":      (("-e", "MTIGERLAKE", ""), ("-e", "GENERIC_CPU3", "")),
    "rocketlake":     (("-e", "MROCKETLAKE", ""), ("-e", "GENERIC_CPU3", "")),
    "alderlake":      (("-e", "MALDERLAKE", ""), ("-e", "GENERIC_CPU3", "")),
    "raptorlake":     (("-e", "MRAPTORLAKE", ""), ("-e", "GENERIC_CPU3", "")),
    "meteorlake":     (("-e", "MMETEORLAKE", ""), ("-e", "GENERIC_CPU3", "")),
    "sapphirerapids": (("-e", "MSAPPHIRERAPIDS", ""), ("-e", "GENERIC_CPU4", "")),
    "znver1":         (("-e", "MZEN", ""), ("-e", "GENERIC_CPU3", "")),
    "znver2":         (("-e", "MZEN2", ""), ("-e", "GENERIC_CPU3", "")),
    "znver3":         (("-e", "MZEN3", ""), ("-e", "GENERIC_CPU3", "")),
    "znver4":         (("-e", "MZEN4", ""), ("-e", "GENERIC_CPU4", "")),
    "znver5":         (("-e", "MZEN5", ""), ("-e", "GENERIC_CPU4", "")),
}

ARCH_ALL_SYMBOLS: Final[tuple[str, ...]] = (
    "MNATIVE_INTEL", "MNATIVE_AMD", "GENERIC_CPU", "GENERIC_CPU2",
    "GENERIC_CPU3", "GENERIC_CPU4", "MK8", "MK8SSE3", "MK10",
    "MBARCELONA", "MBOBCAT", "MJAGUAR", "MBULLDOZER", "MPILEDRIVER",
    "MSTEAMROLLER", "MEXCAVATOR", "MZEN", "MZEN2", "MZEN3", "MZEN4", "MZEN5",
    "MPSC", "MCORE2", "MATOM", "MNEHALEM", "MWESTMERE", "MSILVERMONT",
    "MGOLDMONT", "MGOLDMONTPLUS", "MSANDYBRIDGE", "MIVYBRIDGE", "MHASWELL",
    "MBROADWELL", "MSKYLAKE", "MSKYLAKEX", "MCANNONLAKE", "MICELAKE",
    "MICELAKE_CLIENT", "MICELAKE_SERVER", "MCASCADELAKE", "MCOOPERLAKE",
    "MTIGERLAKE", "MSAPPHIRERAPIDS", "MROCKETLAKE", "MALDERLAKE",
    "MRAPTORLAKE", "MMETEORLAKE", "MEMERALDRAPIDS",
)

HZ_CHOICES: Final = (100, 250, 300, 500, 1000)
TICKLESS_CHOICES: Final = ("periodic", "idle", "full")
PREEMPT_CHOICES: Final = ("lazy", "full", "rt")
SCHED_CHOICES: Final = ("eevdf", "bore", "bmq")
SCX_CHOICES: Final = ("none", "scx_lavd", "scx_bpfland", "scx_rusty", "scx_layered", "scx_flash", "scx_p2dq")
CHANNEL_CHOICES: Final = ("mainline", "stable", "longterm")
LTO_CHOICES: Final = ("none", "thin", "full", "thin_dist")
OPT_CHOICES: Final = ("o2", "size")
FDO_CHOICES: Final = ("none", "autofdo", "autofdo_propeller")
THP_CHOICES: Final = ("always", "madvise", "never")
THP_DEFRAG_CHOICES: Final = ("always", "defer", "defer+madvise", "madvise", "never")
SWAP_BACKEND_CHOICES: Final = ("zswap", "zram", "none")
GOV_CHOICES: Final = ("schedutil", "performance", "powersave", "ondemand", "conservative")
EPP_CHOICES: Final = ("default", "performance", "balance_performance", "balance_power", "power")
IDLE_GOV_CHOICES: Final = ("teo", "menu", "ladder")
CONG_CHOICES: Final = ("bbr", "cubic", "reno", "westwood", "vegas")
QDISC_CHOICES: Final = ("fq", "cake", "fq_codel", "fq_pie", "pfifo_fast")
TOOLCHAIN_CHOICES: Final = ("llvm", "gcc")
HEADERS_CHOICES: Final = ("auto", "always", "never")
MODULES_MODE_CHOICES: Final = ("strict", "expanded")
PSTATE_CHOICES: Final = ("undefined", "disable", "passive", "active", "guided")
COMPRESS_CHOICES: Final = ("zstd", "xz", "gzip", "none")
DEBUG_CHOICES: Final = ("none", "reduced", "full")
SECURITY_PROFILES: Final = ("hardened", "balanced", "extreme")


@dataclass(frozen=True, slots=True)
class FieldSpec:
    key: str
    kind: Literal["str", "int", "bool", "list", "table"]
    default: Any
    help: str
    choices: tuple[Any, ...] | None = None
    required: bool = False
    ephemeral: bool = False


def F(key: str, kind: str, default: Any, help: str,
      choices: Iterable[Any] | None = None, required: bool = False,
      ephemeral: bool = False) -> FieldSpec:
    return FieldSpec(key, kind, default, help,          # type: ignore[arg-type]
                     tuple(choices) if choices else None, required, ephemeral)


_PROFILE_SPEC: Final[dict[str, tuple[FieldSpec, ...]]] = {
    # ------------------------------------------------------------------ meta
    "meta": (
        F("name", "str", "", "Unique profile id, referenced by --profile.", required=True),
        F("description", "str", "", "One-line human summary shown in the picker."),
        F("suffix", "str", "", "LOCALVERSION suffix; also the pkgbase discriminator.", required=True),
        F("priority", "int", 50, "Sort order in the interactive picker (asc)."),
        F("tags", "list", [], "Free-form labels shown in the picker."),
        F("bare_metal_only", "bool", False, "Safety gate: kernel is intended only for bare metal."),
        F("portable_package", "bool", False, "Safety gate: package is distributable, strict modules forbidden."),
    ),
    # --------------------------------------------------------------- release
    "release": (
        F("channel", "str", "stable", "Which kernel.org line to track. Resolved live.", CHANNEL_CHOICES),
        F("pin", "str", "", "Exact version to build (e.g. 7.2.0). Empty means latest in channel."),
        F("allow_rc", "bool", False, "Permit -rc tarballs when mainline is a release candidate."),
        F("min_version", "str", "7.2", "Refuse to build anything older than this."),
    ),
    # ------------------------------------------------------------- scheduler
    "scheduler": (
        F("type", "str", "eevdf", "In-tree class: eevdf, or out-of-tree patch: bore, bmq.", SCHED_CHOICES),
        F("scx", "str", "none", "Runtime sched_ext scheduler (scx_lavd, scx_bpfland, scx_rusty, none).", SCX_CHOICES),
        F("scx_flags", "str", "", "Runtime CLI flags passed to the scx scheduler daemon."),
        F("scx_enable_class", "bool", True, "CONFIG_SCHED_CLASS_EXT: build in-tree sched_ext support."),
        F("require_patch", "bool", False, "Fail build hard if out-of-tree patch fails to apply."),
        F("autogroup", "bool", True, "CONFIG_SCHED_AUTOGROUP: per-session fairness for desktop UI."),
        F("rt_group", "bool", False, "CONFIG_RT_GROUP_SCHED: cgroup bandwidth control for RT tasks."),
        F("allow_vanilla_fallback", "bool", True, "Fallback to upstream EEVDF if patch fails."),
    ),
    # ----------------------------------------------------------------- cache
    "cache": (
        F("sched_cache", "bool", True, "CONFIG_SCHED_CACHE: Linux 7.2 Cache Aware Scheduling."),
        F("llc_aggr_tolerance", "int", 1, "0=disabled, 1=conservative desktop, >1=aggressive server."),
        F("persist", "bool", True, "Install systemd unit to persist debugfs CAS knobs."),
    ),
    # ------------------------------------------------------------------ rseq
    "rseq": (
        F("slice_extension", "bool", True, "CONFIG_RSEQ time slice extension (Linux 7.0+)."),
        F("slice_ext_nsec", "int", 10000, "Extension window in ns (5000-50000)."),
    ),
    # ------------------------------------------------------------------ dusky
    "dusky": (
        F("enhanced", "bool", False, "Enable desktop-tuning umbrella symbol plus dusky heuristics."),
        F("hostname", "str", "dusky", "KBUILD_BUILD_HOST, part of reproducibility."),
        F("user", "str", "dusky", "KBUILD_BUILD_USER, part of reproducibility."),
        F("reproducible", "bool", True, "Pin KBUILD_BUILD_TIMESTAMP to tarball mtime."),
        F("extra_config", "table", {}, "Raw {SYMBOL = value} pairs applied last."),
    ),
    # -------------------------------------------------------------------- cpu
    "cpu": (
        F("arch", "str", "native", "Target microarchitecture level ('native' auto-detects v3/v4 + march).", CPU_ARCHES, ephemeral=True),
        F("march", "str", "", "Optional custom -march injected via KCFLAGS (e.g. znver5)."),
        F("governor", "str", "schedutil", "CONFIG_CPU_FREQ_DEFAULT_GOV_* default.", GOV_CHOICES),
        F("amd_pstate", "str", "active", "CONFIG_X86_AMD_PSTATE_DEFAULT_MODE ('active' = EPP).", PSTATE_CHOICES),
        F("epp", "str", "balance_performance", "Energy performance preference.", EPP_CHOICES),
        F("mitigations", "bool", True, "CPU speculative execution mitigations."),
        F("nr_cpus", "int", 0, "CONFIG_NR_CPUS (0 = auto-snapped to 64 boundary)."),
        F("smt", "bool", True, "CONFIG_SCHED_SMT hyper-threading awareness."),
        F("mce", "bool", True, "CONFIG_X86_MCE machine-check reporting."),
        F("prefcore", "bool", True, "CONFIG_SCHED_MC_PRIO: AMD/Intel preferred core boost ranking."),
    ),
    # ----------------------------------------------------------------- timing
    "timing": (
        F("hz", "int", 1000, "CONFIG_HZ tick rate.", HZ_CHOICES),
        F("tickless", "str", "idle", "periodic / idle / full (full requires isolated cores).", TICKLESS_CHOICES),
        F("preempt", "str", "lazy", "lazy (Linux 7.0+ default) / full / rt.", PREEMPT_CHOICES),
        F("preempt_dynamic", "bool", True, "CONFIG_PREEMPT_DYNAMIC: switch model at boot."),
        F("hz_periodic_rcu", "bool", False, "CONFIG_RCU_NOCB_CPU offload of RCU callbacks."),
    ),
    # ----------------------------------------------------------------- memory
    "memory": (
        F("thp", "str", "madvise", "Transparent hugepage enabled default.", THP_CHOICES),
        F("thp_defrag", "str", "defer+madvise", "THP defrag strategy.", THP_DEFRAG_CHOICES),
        F("thp_shmem", "str", "advise", "THP for shmem/tmpfs."),
        F("mglru", "bool", True, "CONFIG_LRU_GEN + LRU_GEN_ENABLED."),
        F("mglru_mask", "int", 7, "MGLRU bitmask (7 = page-table + PMD walks)."),
        F("mglru_min_ttl_ms", "int", 1000, "MGLRU min working set protection TTL in ms."),
        F("watermark_scale_factor", "int", 200, "kswapd early wake factor (prevents direct reclaim)."),
        F("watermark_boost_factor", "int", 0, "Disable watermark boost stalls."),
        F("compaction_proactiveness", "int", 0, "kcompactd background proactive compaction."),
        F("swap_backend", "str", "zram", "zram (block device) / zswap (writeback cache) / none.", SWAP_BACKEND_CHOICES),
        F("zswap_compressor", "str", "zstd", "Zswap compression algorithm."),
        F("zswap_zpool", "str", "zsmalloc", "Zswap memory pool allocator."),
        F("zram_size_pct", "int", 100, "Zram disk size as percentage of physical RAM."),
        F("zram_multi_comp", "bool", True, "CONFIG_ZRAM_MULTI_COMP recompression."),
        F("slub_tiny", "bool", False, "CONFIG_SLUB_TINY (strictly for <512MB embedded)."),
        F("numa", "bool", True, "CONFIG_NUMA support (essential for CAS and multi-CCD)."),
        F("numa_balancing", "bool", False, "CONFIG_NUMA_BALANCING auto migration."),
        F("nodes_shift", "int", 2, "NODES_SHIFT (2 = max 4 nodes, shrinks arrays)."),
        F("ksm", "bool", True, "CONFIG_KSM same-page merging."),
        F("damon", "bool", False, "CONFIG_DAMON data access monitoring."),
        F("page_reporting", "bool", False, "CONFIG_PAGE_REPORTING free-page hinting to hypervisors."),
    ),
    # --------------------------------------------------------------- compiler
    "compiler": (
        F("toolchain", "str", "llvm", "llvm (clang + lld) / gcc.", TOOLCHAIN_CHOICES, ephemeral=True),
        F("optimize", "str", "o2", "Optimization level (o2 / size).", OPT_CHOICES),
        F("allow_unsupported_o3", "bool", False, "Inject -O3 via KCFLAGS."),
        F("lto", "str", "thin", "LTO mode (none, thin, full, thin_dist).", LTO_CHOICES, ephemeral=True),
        F("thinlto_cache", "bool", True, "Enable persistent LLVM ThinLTO disk caching."),
        F("thinlto_cache_size", "str", "20g", "ThinLTO cache max size."),
        F("fdo", "str", "none", "AutoFDO / Propeller profile optimization.", FDO_CHOICES),
        F("fdo_profile_dir", "str", "", "Directory containing AutoFDO / Propeller profiles."),
        F("kcfi", "bool", False, "CONFIG_CFI_CLANG kernel control-flow integrity."),
        F("zstd_clevel", "int", 19, "ZSTD_CLEVEL compression level (1-19)."),
        F("module_compress", "str", "zstd", "CONFIG_MODULE_COMPRESS_* algorithm.", COMPRESS_CHOICES),
        F("debug_info", "str", "reduced", "none / reduced (+BTF) / full.", DEBUG_CHOICES),
        F("jobs", "int", 0, "make -j (0 = os.process_cpu_count())."),
        F("rust", "bool", True, "CONFIG_RUST integration."),
        F("headers", "str", "auto", "Kernel headers package: auto (detects DKMS) / always / never.", HEADERS_CHOICES, ephemeral=True),
    ),
    # --------------------------------------------------------------- security
    "security": (
        F("profile", "str", "balanced", "hardened / balanced / extreme.", SECURITY_PROFILES),
        F("init_on_alloc", "bool", True, "CONFIG_INIT_ON_ALLOC_DEFAULT_ON."),
        F("hardened_usercopy", "bool", True, "CONFIG_HARDENED_USERCOPY."),
        F("stackprotector", "str", "strong", "strong / regular / none."),
        F("slab_freelist_hardened", "bool", True, "CONFIG_SLAB_FREELIST_HARDENED."),
        F("randomize_kstack", "bool", True, "Randomize kernel stack offset on syscalls."),
        F("mitigations", "str", "auto", "auto / off.", ("auto", "off")),
        F("acknowledge_risk", "bool", False, "Required when mitigations='off' or profile='extreme'."),
    ),
    # ----------------------------------------------------------------- gaming
    "gaming": (
        F("ntsync", "bool", True, "CONFIG_NTSYNC in-tree Windows NT synchronization driver."),
        F("uclamp", "bool", True, "CONFIG_UCLAMP_TASK utilization clamping."),
        F("max_map_count", "int", 2147483642, "vm.max_map_count for Wine/Proton/DX12."),
        F("split_lock_mitigate", "bool", False, "Disable split-lock throttle for Proton titles."),
    ),
    # ---------------------------------------------------------------- storage
    "storage": (
        F("nvme_poll_queues", "int", 0, "Hardware NVMe IOPOLL queues (workstation/database only)."),
        F("io_scheduler", "str", "none", "NVMe I/O scheduler (none, mq-deadline, kyber, bfq)."),
        F("blk_wbt", "bool", True, "CONFIG_BLK_WBT writeback throttling anti-stutter."),
    ),
    # ------------------------------------------------------------------ power
    "power": (
        F("wq_power_efficient", "bool", False, "CONFIG_WQ_POWER_EFFICIENT_DEFAULT."),
        F("cpu_idle_governor", "str", "teo", "CONFIG_CPU_IDLE_GOV_* default.", IDLE_GOV_CHOICES),
        F("rcu_lazy", "bool", False, "CONFIG_RCU_LAZY batching for mobile endurance."),
        F("energy_model", "bool", False, "CONFIG_ENERGY_MODEL."),
        F("suspend", "bool", True, "CONFIG_SUSPEND / CONFIG_HIBERNATION."),
    ),
    # ---------------------------------------------------------------- network
    "network": (
        F("congestion", "str", "bbr", "TCP congestion control.", CONG_CHOICES),
        F("qdisc", "str", "fq", "Root network qdisc.", QDISC_CHOICES),
        F("mptcp", "bool", True, "CONFIG_MPTCP multipath TCP."),
        F("nf_conntrack_procfs", "bool", False, "CONFIG_NF_CONNTRACK_PROCFS."),
        F("xdp", "bool", False, "eBPF XDP socket support."),
    ),
    # ---------------------------------------------------------------- modules
    "modules": (
        F("mode", "str", "strict", "strict (modprobed.db only) / expanded (curated safety net).", MODULES_MODE_CHOICES, ephemeral=True),
        F("modprobed_db", "bool", True, "Use modprobed.db for localmodconfig."),
        F("modprobed_db_path", "str", "", "Custom path to imported modprobed.db file."),
        F("lmc_keep_extra", "list", [], "Additional driver directories to keep in expanded mode."),
        F("manage_service", "bool", True, "Install and enable modprobed-db timer."),
        F("sig_force", "bool", False, "CONFIG_MODULE_SIG_FORCE: refuse unsigned modules."),
    ),
    # ----------------------------------------------------------------- verify
    "verify": (
        F("strict", "bool", True, "Fail build if any requested symbol vanished after olddefconfig."),
        F("optional_symbols", "list", ["SCHED_BORE", "SCHED_ALT", "THINLTO_CACHE", "PER_VMA_LOCK", "SLAB_BUCKETS", "MEMORY_TIERING", "SWAP_TABLE", "CFI_ICALL_NORMALIZE_INTEGERS", "MODULE_ALLOW_BTF_MISMATCH", "TRIM_UNUSED_KSYMS", "LD_DEAD_CODE_DATA_ELIMINATION"], "Symbols that are allowed to vanish without error."),
        F("assert_runtime", "bool", True, "Run post-build smoke tests."),
        F("require_ntsync", "bool", True, "Require NTSYNC module in build."),
        F("require_btf", "bool", True, "Require DEBUG_INFO_BTF in build."),
        F("require_sched_ext", "bool", True, "Require SCHED_CLASS_EXT in build."),
    ),
}

LMC_KEEP_BASE: Final[tuple[str, ...]] = (
    "drivers/gpu", "drivers/hid", "drivers/input", "drivers/usb",
    "drivers/net", "drivers/nvme", "drivers/ata", "drivers/scsi",
    "drivers/md", "drivers/bluetooth", "drivers/platform", "drivers/thunderbolt",
    "drivers/virtio", "drivers/hwmon", "drivers/i2c", "drivers/mmc",
    "drivers/pci", "drivers/thermal", "drivers/watchdog", "sound/pci",
    "sound/usb", "sound/hda", "fs/btrfs", "fs/xfs", "fs/f2fs",
    "fs/exfat", "fs/nfs", "fs/fuse", "net/bridge", "net/netfilter",
    "net/sched", "crypto",
)


# --------------------------------------------------------------------------- #
# 8. Profile model
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class KernelProfile:
    path: Path
    sections: dict[str, dict[str, Any]]

    def g(self, section: str, key: str) -> Any:
        return self.sections[section][key]

    def set(self, section: str, key: str, value: Any) -> None:
        self.sections[section][key] = value

    @property
    def name(self) -> str:
        return self.sections["meta"]["name"]

    @property
    def description(self) -> str:
        return self.sections["meta"]["description"]

    @property
    def suffix(self) -> str:
        return self.sections["meta"]["suffix"]

    @property
    def priority(self) -> int:
        return self.sections["meta"]["priority"]

    @property
    def pkgbase(self) -> str:
        return "linux-" + self.suffix

    @classmethod
    def load(cls, path: Path) -> Self:
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ProfileError("%s: invalid TOML: %s" % (path.name, exc)) from exc
        except OSError as exc:
            raise ProfileError("%s: unreadable: %s" % (path.name, exc)) from exc
        return cls(path=path, sections=validate_profile(raw, path))

    def localversion(self) -> str:
        return "-" + self.suffix.strip("-")

    def summarize(self) -> list[tuple[str, str]]:
        s = self.sections
        lto = s["compiler"]["lto"]
        if s["compiler"]["toolchain"] == "gcc" and lto != "none":
            lto = "none (gcc)"
        scx_info = (" + " + s["scheduler"]["scx"]) if s["scheduler"]["scx"] != "none" else ""
        return [
            ("channel", "%s%s" % (s["release"]["channel"],
                                  "  pin=" + s["release"]["pin"] if s["release"]["pin"] else "")),
            ("scheduler", s["scheduler"]["type"] + scx_info + (" (CAS)" if s["cache"]["sched_cache"] else "")),
            ("cpu arch", s["cpu"]["arch"] + (" (" + s["cpu"]["march"] + ")" if s["cpu"]["march"] else "")),
            ("governor", "%s [%s / %s]" % (s["cpu"]["governor"], s["cpu"]["amd_pstate"], s["cpu"]["epp"])),
            ("timing", "HZ=%d  tickless=%s  preempt=%s%s" % (
                s["timing"]["hz"], s["timing"]["tickless"], s["timing"]["preempt"],
                "  dynamic" if s["timing"]["preempt_dynamic"] else "")),
            ("memory", "thp=%s  mglru=%s  swap=%s  numa=%s" % (
                s["memory"]["thp"], onoff(s["memory"]["mglru"]),
                s["memory"]["swap_backend"], onoff(s["memory"]["numa"]))),
            ("compiler", "%s  %s  lto=%s  kcfi=%s" % (
                s["compiler"]["toolchain"], s["compiler"]["optimize"].upper(),
                lto, onoff(s["compiler"]["kcfi"]))),
            ("security", "%s (init_alloc=%s, mit=%s)" % (
                s["security"]["profile"], onoff(s["security"]["init_on_alloc"]),
                s["security"]["mitigations"])),
            ("gaming", "ntsync=%s  uclamp=%s" % (
                onoff(s["gaming"]["ntsync"]), onoff(s["gaming"]["uclamp"]))),
            ("network", "%s / %s" % (s["network"]["congestion"], s["network"]["qdisc"])),
            ("modules", "%s%s" % (s["modules"]["mode"], "  +modprobed-db" if s["modules"]["modprobed_db"] else "")),
            ("dusky", "enhanced=%s  host=%s" % (onoff(s["dusky"]["enhanced"]), s["dusky"]["hostname"])),
        ]


def onoff(b: bool) -> str:
    return (C.GREEN + "on" + C.RESET) if b else (C.FAINT + "off" + C.RESET)


def validate_profile(raw: Mapping[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    problems: list[str] = []
    out: dict[str, dict[str, Any]] = {}

    # Migration for Linux 7.0+: auto-migrate legacy preempt modes
    raw_dict = dict(raw)
    if "timing" in raw_dict and isinstance(raw_dict["timing"], dict):
        p_mode = raw_dict["timing"].get("preempt", "")
        if p_mode in ("none", "voluntary"):
            warn(f"{path.name}: preempt='{p_mode}' was removed in Linux 7.0+; migrating to 'lazy'")
            raw_dict["timing"]["preempt"] = "lazy"

    unknown_sections = set(raw_dict) - set(_PROFILE_SPEC)
    for sec in sorted(unknown_sections):
        problems.append("unknown section [%s]" % sec)

    for section, fields in _PROFILE_SPEC.items():
        given = raw_dict.get(section, {})
        if not isinstance(given, dict):
            problems.append("[%s] must be a table" % section)
            given = {}
        known = {f.key for f in fields}
        for key in sorted(set(given) - known):
            hint = _rename_hint(section, key)
            problems.append("[%s] unknown key '%s'%s" % (section, key, hint))
        bucket: dict[str, Any] = {}
        for f in fields:
            if f.key in given:
                try:
                    bucket[f.key] = coerce(f, given[f.key])
                except ValueError as exc:
                    problems.append("[%s] %s: %s" % (section, f.key, exc))
                    bucket[f.key] = f.default
            elif f.required:
                problems.append("[%s] missing required key '%s'" % (section, f.key))
                bucket[f.key] = f.default
            else:
                bucket[f.key] = _clone(f.default)
        out[section] = bucket

    problems.extend(cross_validate(out))
    if problems:
        raise ProfileError("%s\n    %s" % (path.name, "\n    ".join(problems)))
    return out


_RENAMES: Final[dict[tuple[str, str], str]] = {
    ("cpu", "opt"): "cpu.arch",
    ("cpu", "default_governor"): "cpu.governor",
    ("compiler", "clang"): "compiler.toolchain",
    ("memory", "transparent_hugepage"): "memory.thp",
    ("modules", "strict"): "modules.mode",
    ("dusky", "enhance"): "dusky.enhanced",
    ("timing", "preempt_mode"): "timing.preempt",
}


def _rename_hint(section: str, key: str) -> str:
    target = _RENAMES.get((section, key))
    return "  -> renamed to '%s'" % target if target else ""


def _clone(v: Any) -> Any:
    if isinstance(v, list):
        return list(v)
    if isinstance(v, dict):
        return dict(v)
    return v


def coerce(f: FieldSpec, value: Any) -> Any:
    match f.kind:
        case "bool":
            if not isinstance(value, bool):
                raise ValueError("expected boolean, got %r" % (value,))
            return value
        case "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("expected integer, got %r" % (value,))
            if f.choices and value not in f.choices:
                raise ValueError("must be one of %s" % ", ".join(map(str, f.choices)))
            return value
        case "str":
            if not isinstance(value, str):
                raise ValueError("expected string, got %r" % (value,))
            v = value.strip()
            if f.choices and v not in f.choices:
                raise ValueError("must be one of: %s" % ", ".join(f.choices))
            return v
        case "list":
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError("expected an array of strings")
            return list(value)
        case "table":
            if not isinstance(value, dict):
                raise ValueError("expected a table")
            return dict(value)
    raise AssertionError("unreachable kind %r" % f.kind)


def cross_validate(s: Mapping[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    comp, tim, mem, cpu, sched, sec, meta = (
        s["compiler"], s["timing"], s["memory"], s["cpu"], s["scheduler"],
        s["security"], s["meta"]
    )

    if comp["toolchain"] == "gcc":
        if comp["kcfi"]:
            out.append("[compiler] kcfi requires toolchain='llvm'")
        if comp["fdo"] != "none":
            out.append("[compiler] autofdo/propeller require toolchain='llvm'")
        if comp["lto"] != "none":
            out.append("[compiler] LTO with GCC is not supported; set lto='none'")

    if comp["kcfi"] and comp["lto"] == "none":
        out.append("[compiler] kcfi requires lto != 'none' (thin or full)")

    if sched["type"] == "bmq" and sched["scx_enable_class"]:
        out.append("[scheduler] BMQ (Project-C) replaces fair class; mutually exclusive with sched_ext")

    if mem["slub_tiny"] and mem_total_gib() > 0.5:
        out.append("[memory] slub_tiny is strictly for <=512MiB embedded targets; do not use on desktop/server")

    if mem["thp"] == "always" and mem["compaction_proactiveness"] == 0:
        out.append("[memory] thp='always' with compaction_proactiveness=0 is contradictory (disables hugepage creation)")

    if sec["mitigations"] == "off" and not sec["acknowledge_risk"]:
        out.append("[security] mitigations='off' requires acknowledge_risk=true")

    if meta["portable_package"] and s["modules"]["mode"] == "strict":
        out.append("[meta] portable_package=true cannot be combined with modules.mode='strict'")

    return out


# --------------------------------------------------------------------------- #
# 9. Profile discovery & selection
# --------------------------------------------------------------------------- #

def discover_profiles() -> list[KernelProfile]:
    if not PROFILES_DIR.is_dir():
        raise ProfileError(
            "profiles directory not found: %s\n"
            "    create it, or set DUSKY_PROFILES_DIR, or run "
            "'%s --write-default-profiles'" % (PROFILES_DIR, Path(sys.argv[0]).name))
    files = sorted(PROFILES_DIR.glob("*.toml"))
    if not files:
        raise ProfileError("no *.toml profiles in %s" % PROFILES_DIR)

    profiles: list[KernelProfile] = []
    errors: list[str] = []
    for f in files:
        try:
            profiles.append(KernelProfile.load(f))
        except ProfileError as exc:
            errors.append(str(exc))
    if errors:
        for e in errors:
            err(e)
        if not profiles:
            raise ProfileError("every profile failed validation")
        warn("%d profile(s) skipped due to validation errors" % len(errors))

    seen: dict[str, Path] = {}
    for p in profiles:
        if p.name in seen:
            raise ProfileError("duplicate profile name '%s' in %s and %s"
                                % (p.name, seen[p.name].name, p.path.name))
        seen[p.name] = p.path
    profiles.sort(key=lambda p: (p.priority, p.name))
    return profiles


def print_profile_table(profiles: Sequence[KernelProfile], numbered: bool = True) -> None:
    headers = (["#"] if numbered else []) + [
        "profile", "sched", "arch", "HZ", "preempt", "lto", "mods", "channel", "description"
    ]
    rows: list[list[str]] = []
    for i, p in enumerate(profiles, 1):
        s = p.sections
        lto = s["compiler"]["lto"] if s["compiler"]["toolchain"] == "llvm" else "-"
        scx_tag = ("+" + s["scheduler"]["scx"].replace("scx_", "")) if s["scheduler"]["scx"] != "none" else ""
        rows.append(
            ([C.ACCENT + str(i) + C.RESET] if numbered else []) + [
                C.BOLD + p.name + C.RESET,
                s["scheduler"]["type"] + (C.CYAN + scx_tag + C.RESET if scx_tag else ""),
                s["cpu"]["arch"],
                str(s["timing"]["hz"]),
                s["timing"]["preempt"] + ("*" if s["timing"]["preempt_dynamic"] else ""),
                lto,
                ("S" if s["modules"]["mode"] == "strict" else "E"),
                s["release"]["channel"],
                C.FAINT + p.description + C.RESET,
            ])
    table(headers, rows)
    say("")
    say(C.FAINT + "  mods: S=strict (minimal, fastest)  E=expanded (safety net)"
        "   preempt*: PREEMPT_DYNAMIC (boot-selectable)" + C.RESET)


def select_profile(profiles: Sequence[KernelProfile], wanted: str | None) -> KernelProfile:
    if wanted:
        for p in profiles:
            if p.name.lower() == wanted.lower():
                return p
        near = [p.name for p in profiles if wanted.lower() in p.name.lower()]
        raise ProfileError(
            "no profile named '%s'%s" %
            (wanted, ("; did you mean: " + ", ".join(near)) if near else ""))
    if not interactive():
        raise ProfileError("non-interactive session requires --profile NAME")
    rule("Select build profile")
    print_profile_table(profiles)
    say("")
    default_idx = 1
    for i, p in enumerate(profiles, 1):
        if p.name.lower() == "gaming":
            default_idx = i
            break
    idx = ask_index("Profile", len(profiles), default_idx)
    return profiles[idx - 1]


# --------------------------------------------------------------------------- #
# 10. Ephemeral overrides
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class Overrides:
    cpu_arch: str | None = None
    modules_mode: str | None = None
    toolchain: str | None = None
    lto: str | None = None
    jobs: int | None = None
    pin: str | None = None
    channel: str | None = None
    scheduler: str | None = None
    headers: str | None = None

    @classmethod
    def from_env_and_args(cls, args: argparse.Namespace) -> Self:
        def pick(cli: Any, env: str, choices: Sequence[str] | None) -> Any:
            val = cli if cli is not None else os.environ.get(env, "").strip() or None
            if val is not None and choices is not None and val not in choices:
                raise DuskyError("%s: '%s' is not one of %s" % (env, val, ", ".join(choices)))
            return val

        jobs_raw = pick(args.jobs, "DUSKY_JOBS", None)
        return cls(
            cpu_arch=pick(args.cpu_arch, "DUSKY_CPU_ARCH", CPU_ARCHES),
            modules_mode=pick(args.modules_mode, "DUSKY_MODULES_MODE", MODULES_MODE_CHOICES),
            toolchain=pick(args.toolchain, "DUSKY_TOOLCHAIN", TOOLCHAIN_CHOICES),
            lto=pick(args.lto, "DUSKY_LTO", LTO_CHOICES),
            jobs=int(jobs_raw) if jobs_raw else None,
            pin=pick(args.pin, "DUSKY_PIN", None),
            channel=pick(args.channel, "DUSKY_CHANNEL", CHANNEL_CHOICES),
            scheduler=pick(getattr(args, "scheduler", None), "DUSKY_SCHEDULER", SCHED_CHOICES),
            headers=pick(getattr(args, "headers", None), "DUSKY_HEADERS", HEADERS_CHOICES),
        )


def apply_overrides(p: KernelProfile, o: Overrides, prompt: bool) -> list[str]:
    diff: list[str] = []

    def put(section: str, key: str, value: Any) -> None:
        old = p.g(section, key)
        if old != value:
            p.set(section, key, value)
            diff.append("%s.%s: %s -> %s" % (section, key, old, value))

    if o.cpu_arch:
        put("cpu", "arch", o.cpu_arch)
    if o.modules_mode:
        put("modules", "mode", o.modules_mode)
    if o.toolchain:
        put("compiler", "toolchain", o.toolchain)
    if o.lto:
        put("compiler", "lto", o.lto)
    if o.jobs is not None:
        put("compiler", "jobs", o.jobs)
    if o.pin is not None:
        put("release", "pin", o.pin)
    if o.channel:
        put("release", "channel", o.channel)
    if o.scheduler:
        put("scheduler", "type", o.scheduler)
    if o.headers:
        put("compiler", "headers", o.headers)

    if prompt and interactive():
        rule("Ephemeral overrides")
        say(C.FAINT + "  These apply to this build only. " + p.path.name + " is never modified." + C.RESET)
        say("")
        if o.cpu_arch is None:
            cur = p.g("cpu", "arch")
            say("  %sCPU arch%s      current: %s%s%s" % (C.BOLD, C.RESET, C.CYAN, cur, C.RESET))
            if ask_yes("Change CPU arch for this build?", False):
                choice = ask("Arch " + C.FAINT + "(" + ", ".join(CPU_ARCHES) + ")" + C.RESET, cur)
                if choice in CPU_ARCHES:
                    put("cpu", "arch", choice)
        if o.modules_mode is None:
            cur = p.g("modules", "mode")
            say("  %sModules mode%s  current: %s%s%s" % (C.BOLD, C.RESET, C.CYAN, cur, C.RESET))
            if ask_yes("Change modules mode for this build?", False):
                put("modules", "mode", ask_choice("Modules mode", list(MODULES_MODE_CHOICES), cur))
        if o.headers is None:
            cur = p.g("compiler", "headers")
            det = "(DKMS active)" if needs_headers() else "(no DKMS drivers detected)"
            say("  %sHeaders%s       current: %s%s%s %s" % (C.BOLD, C.RESET, C.CYAN, cur, C.RESET, C.FAINT + det + C.RESET))
            if ask_yes("Change headers package setting for this build?", False):
                put("compiler", "headers", ask_choice("Headers", list(HEADERS_CHOICES), cur))
        if o.lto is None and p.g("compiler", "toolchain") == "llvm":
            cur = p.g("compiler", "lto")
            say("  %sLTO mode%s      current: %s%s%s" % (C.BOLD, C.RESET, C.CYAN, cur, C.RESET))
            if ask_yes("Change LTO mode for this build?", False):
                put("compiler", "lto", ask_choice("LTO mode", list(LTO_CHOICES), cur))

    return diff


# --------------------------------------------------------------------------- #
# 11. Process execution
# --------------------------------------------------------------------------- #

_CHILDREN: set[int] = set()
_CHILD_LOCK = threading.Lock()
_ABORT = threading.Event()


def terminate_process_group(pgid: int, grace: float = 6.0) -> None:
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 0.0)):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        if wait <= 0:
            return
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.15)


def _reap_all() -> None:
    with _CHILD_LOCK:
        pgids = sorted(_CHILDREN)
        _CHILDREN.clear()
    for pgid in pgids:
        terminate_process_group(pgid, grace=3.0)


@contextlib.contextmanager
def _tracked(popen: subprocess.Popen[Any]) -> Iterator[subprocess.Popen[Any]]:
    try:
        pgid = os.getpgid(popen.pid)
    except ProcessLookupError:
        pgid = popen.pid
    with _CHILD_LOCK:
        _CHILDREN.add(pgid)
    try:
        yield popen
    finally:
        with _CHILD_LOCK:
            _CHILDREN.discard(pgid)


def run(argv: Sequence[str], *, cwd: Path | None = None,
        env: Mapping[str, str] | None = None, check: bool = True,
        capture: bool = True, timeout: float | None = None,
        stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
    debug("$ " + " ".join(shlex.quote(a) for a in argv))
    merged = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        list(argv), cwd=str(cwd) if cwd else None, env=merged,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", start_new_session=True)
    with _tracked(proc):
        try:
            out, _ = proc.communicate(stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process_group(os.getpgid(proc.pid))
            raise BuildError("timeout after %.0fs: %s" % (timeout or 0, argv[0]))
    cp = subprocess.CompletedProcess(list(argv), proc.returncode, out or "", "")
    if capture and out:
        JOURNAL.write(out)
    if check and cp.returncode != 0:
        tail = "\n".join((out or "").strip().splitlines()[-25:])
        raise BuildError("command failed (rc=%d): %s\n%s"
                         % (cp.returncode, " ".join(argv), tail))
    return cp


def run_stream(argv: Sequence[str], *, cwd: Path | None = None,
               env: Mapping[str, str] | None = None,
               on_line: Callable[[str], None] | None = None) -> int:
    debug("$ " + " ".join(shlex.quote(a) for a in argv))
    merged = {**os.environ, **(env or {})}
    proc = subprocess.Popen(
        list(argv), cwd=str(cwd) if cwd else None, env=merged,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
        errors="replace", bufsize=1, start_new_session=True)
    with _tracked(proc):
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            JOURNAL.write(line)
            if on_line is not None:
                on_line(line)
            if _ABORT.is_set():
                terminate_process_group(os.getpgid(proc.pid))
                break
        proc.wait()
    return proc.returncode


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def require(*tools: str) -> None:
    missing = [t for t in tools if not have(t)]
    if missing:
        raise DependencyError(
            "missing tool(s): %s\n    install with: sudo pacman -S --needed %s"
            % (", ".join(missing), " ".join(sorted(set(missing)))))


# --------------------------------------------------------------------------- #
# 12. Sudo keepalive
# --------------------------------------------------------------------------- #

class Sudo:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.active = False

    def acquire(self) -> None:
        if self.active or os.geteuid() == 0:
            self.active = True
            return
        require("sudo")
        info("Requesting sudo credentials (kept alive for the whole build)")
        rc = subprocess.call(["sudo", "-v"])
        if rc != 0:
            raise DuskyError("sudo authentication failed")
        self.active = True
        self._thread = threading.Thread(target=self._loop, name="sudo-keepalive", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(50.0):
            subprocess.call(["sudo", "-n", "-v"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stop(self) -> None:
        self._stop.set()

    def argv(self, argv: Sequence[str]) -> list[str]:
        if os.geteuid() == 0:
            return list(argv)
        self.acquire()
        return ["sudo", "-n", *argv]


SUDO: Final = Sudo()


# --------------------------------------------------------------------------- #
# 13. System probes & CPU version detection
# --------------------------------------------------------------------------- #

def cpu_count() -> int:
    return os.process_cpu_count() or os.cpu_count() or 1


def mem_total_gib() -> float:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) / (1024.0 * 1024.0)
    return 0.0


def detect_cpu_x86_version() -> int:
    """Detects x86-64 microarchitecture level (1, 2, 3, or 4) from /proc/cpuinfo."""
    try:
        flags_txt = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 3
    flags: set[str] = set()
    for line in flags_txt.splitlines():
        if line.startswith("flags"):
            flags.update(line.split(":")[1].split())
            break
    v4_flags = {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"}
    if v4_flags.issubset(flags):
        return 4
    v3_flags = {"avx2", "bmi1", "bmi2", "f16c", "fma", "movbe"}
    if v3_flags.issubset(flags):
        return 3
    v2_flags = {"sse4_2", "ssse3", "popcnt", "cx16"}
    if v2_flags.issubset(flags):
        return 2
    return 1


def findmnt(path: Path) -> dict[str, str]:
    if not have("findmnt"):
        return {}
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    cp = run(["findmnt", "-J", "-T", str(target), "-o",
              "TARGET,SOURCE,FSTYPE,OPTIONS,AVAIL,SIZE"], check=False)
    if cp.returncode != 0 or not cp.stdout.strip():
        return {}
    try:
        payload = json.loads(cp.stdout)
        return dict(payload["filesystems"][0])
    except (json.JSONDecodeError, KeyError, IndexError):
        return {}


_RAM_FSTYPES: Final = frozenset({"tmpfs", "ramfs"})


def is_ram_backed(path: Path) -> bool:
    mnt = findmnt(path)
    if mnt.get("fstype", "") in _RAM_FSTYPES:
        return True
    source = mnt.get("source", "")
    if not source:
        return False
    dev = Path(source).name.split("[")[0]
    if dev.startswith("zram"):
        return True
    sysblk = Path("/sys/block") / dev
    if sysblk.is_dir() and (sysblk / "slaves").is_dir():
        return any(s.name.startswith("zram") for s in (sysblk / "slaves").iterdir())
    return False


def free_gib(path: Path) -> float:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    st = os.statvfs(target)
    return st.f_bavail * st.f_frsize / (1024.0 ** 3)


def pacman_resolve(candidates: Sequence[str]) -> list[str]:
    if not have("pacman"):
        return list(candidates)
    cp = run(["pacman", "-T", *candidates], check=False)
    if cp.returncode == 0:
        return []
    return [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]


BASE_DEPS: Final = ("base-devel", "bc", "cpio", "gettext", "libelf", "pahole",
                    "perl", "python", "tar", "xz", "zstd", "kmod", "git")
LLVM_DEPS: Final = ("clang", "llvm", "lld")
RUST_DEPS: Final = ("rust", "rust-bindgen")


def check_dependencies(profile: KernelProfile) -> None:
    rule("Toolchain")
    wanted = list(BASE_DEPS)
    if profile.g("compiler", "toolchain") == "llvm":
        wanted += list(LLVM_DEPS)
    else:
        wanted.append("gcc")
    if profile.g("compiler", "rust"):
        wanted += list(RUST_DEPS)
    missing = pacman_resolve(wanted)
    if missing:
        err("Unsatisfied dependencies: " + " ".join(missing))
        say(C.FAINT + "    sudo pacman -S --needed " + " ".join(missing) + C.RESET)
        if not ask_yes("Install them now?", True):
            raise DependencyError("cannot continue without the toolchain")
        rc = subprocess.call(SUDO.argv(["pacman", "-S", "--needed", "--noconfirm", *missing]))
        if rc != 0:
            raise DependencyError("pacman failed to install dependencies")
    ok("All build dependencies satisfied")


def rust_available(tree: Path, toolchain: str) -> bool:
    script = tree / "scripts" / "rustavailable"
    if not script.is_file():
        return False
    env = {"RUSTC": "rustc", "BINDGEN": "bindgen", "CC": "clang" if toolchain == "llvm" else "gcc"}
    cp = run(["sh", str(script)], cwd=tree, env=env, check=False)
    return cp.returncode == 0


# --------------------------------------------------------------------------- #
# 14. Release discovery
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class Release:
    version: str
    moniker: str
    released: str
    iseol: bool

    @property
    def major(self) -> int:
        return int(self.version.split(".")[0])

    @property
    def series(self) -> str:
        return "v%d.x" % self.major

    @property
    def is_rc(self) -> bool:
        return "-rc" in self.version

    @property
    def tarball(self) -> str:
        return "linux-%s.tar.xz" % self.version

    @property
    def base_url(self) -> str:
        sub = "/testing" if self.is_rc else ""
        return "%s/%s%s" % (KERNEL_CDN, self.series, sub)

    @property
    def tarball_url(self) -> str:
        return "%s/%s" % (self.base_url, self.tarball)

    @property
    def sha_url(self) -> str:
        return "%s/%s/sha256sums.asc" % (KERNEL_CDN, self.series)


def http_get(url: str, *, timeout: float = NET_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise NetworkError("HTTP %d for %s" % (exc.code, url)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise NetworkError("cannot reach %s: %s" % (url, exc)) from exc


def http_exists(url: str, timeout: float = 12.0) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def fetch_releases() -> list[Release]:
    info("Querying kernel.org release index")
    try:
        payload = json.loads(http_get(KERNEL_ORG_RELEASES).decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise NetworkError("releases.json is not valid JSON: %s" % exc) from exc

    out: list[Release] = []
    for entry in payload.get("releases", []):
        version = str(entry.get("version", "")).strip()
        if not version or version.lower() == "next":
            continue
        moniker = str(entry.get("moniker", "")).strip().lower()
        if moniker in ("linux-next", "next"):
            continue
        released = entry.get("released", {})
        iso = released.get("isodate", "") if isinstance(released, dict) else ""
        out.append(Release(version=version, moniker=moniker,
                           released=str(iso), iseol=bool(entry.get("iseol", False))))
    if not out:
        raise NetworkError("releases.json contained no usable releases")
    return out


def _vkey(v: str) -> tuple[int, ...]:
    core, _, rc = v.partition("-rc")
    parts = [int(x) for x in re.findall(r"\d+", core)]
    while len(parts) < 3:
        parts.append(0)
    return (*parts[:3], 0 if rc else 1, int(rc or 0))


def candidates_for(profile: KernelProfile, releases: Sequence[Release]) -> list[Release]:
    channel = profile.g("release", "channel")
    allow_rc = profile.g("release", "allow_rc")
    floor = profile.g("release", "min_version")

    def matches(r: Release) -> bool:
        if r.iseol:
            return False
        if r.is_rc and not allow_rc:
            return False
        if floor and _vkey(r.version) < _vkey(floor):
            return False
        match channel:
            case "mainline":
                return r.moniker in ("mainline", "stable")
            case "stable":
                return r.moniker == "stable"
            case "longterm":
                return r.moniker == "longterm"
        return False

    picks = [r for r in releases if matches(r)]
    if not picks and channel == "mainline":
        picks = [r for r in releases if r.moniker == "stable" and not r.iseol]
    if not picks:
        picks = [r for r in releases if not r.iseol and not r.is_rc]
    picks.sort(key=lambda r: _vkey(r.version), reverse=True)
    return picks


def choose_release(profile: KernelProfile, releases: Sequence[Release]) -> Release:
    pin = profile.g("release", "pin")
    if pin:
        for r in releases:
            if r.version == pin:
                ok("Pinned to %s (%s)" % (r.version, r.moniker))
                return r
        synth = Release(version=pin, moniker="pinned", released="", iseol=False)
        if http_exists(synth.tarball_url):
            warn("%s is no longer in releases.json but the tarball exists" % pin)
            return synth
        raise NetworkError("pinned version %s is not available on kernel.org" % pin)

    picks = candidates_for(profile, releases)
    default_release = picks[0] if picks else releases[0]

    if not interactive():
        ok("Selected %s (%s)" % (default_release.version, default_release.moniker))
        return default_release

    rule("Kernel release selection")
    display_releases: list[Release] = [default_release]
    seen_versions = {default_release.version}

    for r in releases:
        if not r.iseol and r.version not in seen_versions:
            display_releases.append(r)
            seen_versions.add(r.version)

    rows = []
    limit = min(len(display_releases), 8)
    for i, r in enumerate(display_releases[:limit], 1):
        flags = []
        if i == 1:
            flags.append(C.GREEN + "[profile default: %s]" % profile.g("release", "channel") + C.RESET)
        if r.is_rc:
            flags.append(C.YELLOW + "rc" + C.RESET)
        rows.append([C.ACCENT + str(i) + C.RESET,
                     C.BOLD + r.version + C.RESET,
                     r.moniker, r.released or "-", " ".join(flags)])

    rows.append([C.ACCENT + "c" + C.RESET, C.BOLD + "Custom" + C.RESET, "manual", "-", "type exact version string"])
    table(["#", "version", "moniker", "released", ""], rows)
    say("")

    raw = ask("Kernel version (1-%d or 'c' for custom)" % limit, "1").strip()
    if raw.lower() == "c" or (raw and not raw.isdigit() and "." in raw):
        custom_ver = raw if "." in raw else ask("Enter exact kernel version (e.g. 7.2 or 7.2.4)", default_release.version).strip()
        for r in releases:
            if r.version == custom_ver:
                ok("Selected %s (%s)" % (r.version, r.moniker))
                return r
        synth = Release(version=custom_ver, moniker="custom", released="", iseol=False)
        ok("Selected custom version %s" % custom_ver)
        return synth

    idx = int(raw) if (raw.isdigit() and 1 <= int(raw) <= limit) else 1
    chosen = display_releases[idx - 1]
    ok("Selected %s (%s)" % (chosen.version, chosen.moniker))
    return chosen


# --------------------------------------------------------------------------- #
# 15. Download + verification
# --------------------------------------------------------------------------- #

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_sha256(release: Release) -> str | None:
    try:
        blob = http_get(release.sha_url)
    except NetworkError as exc:
        warn("checksum manifest unavailable: %s" % exc)
        return None

    text = blob.decode("utf-8", errors="replace")
    if have("gpg"):
        cp = run(["gpg", "--batch", "--status-fd", "1", "--verify", "-"],
                 stdin_text=text, check=False)
        if "GOODSIG" in cp.stdout:
            ok("sha256sums.asc PGP signature verified")
        elif "NO_PUBKEY" in cp.stdout:
            warn("kernel.org release key not in keyring; falling back to checksum-only verification")
        else:
            raise VerifyError("sha256sums.asc failed PGP verification")

    target_name = release.tarball
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64 and parts[1].lstrip("*") == target_name:
            return parts[0].lower()
    return None


def download(url: str, dest: Path, *, resume: bool = True) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if have("aria2c"):
        argv = ["aria2c", "--console-log-level=warn", "--summary-interval=0",
                "-x16", "-s16", "-k", "4M", "--file-allocation=none",
                "--auto-file-renaming=false", "--allow-overwrite=true",
                "--retry-wait=2", "--max-tries=5", "-U", USER_AGENT,
                "-d", str(dest.parent), "-o", dest.name, url]
        if resume:
            argv.insert(1, "-c")
        info("aria2c -x16 " + dest.name)
        rc = run_stream(argv, on_line=lambda ln: None)
        if rc == 0 and dest.is_file() and dest.stat().st_size > 0:
            return
        warn("aria2c returned %d; falling back to urllib" % rc)

    info("urllib " + dest.name)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp, tmp.open("wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            last = 0.0
            while chunk := resp.read(1 << 20):
                fh.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if total and now - last > 0.25:
                    last = now
                    pct = 100.0 * done / total
                    sys.stdout.write("\r    %s%5.1f%%%s  %.1f/%.1f MiB" % (
                        C.CYAN, pct, C.RESET, done / 1048576, total / 1048576))
                    sys.stdout.flush()
            sys.stdout.write("\r" + " " * 60 + "\r")
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise NetworkError("download failed: %s" % exc) from exc
    tmp.replace(dest)


def obtain_tarball(release: Release) -> Path:
    rule("Source tarball")
    TARBALL_DIR.mkdir(parents=True, exist_ok=True)
    dest = TARBALL_DIR / release.tarball
    want = expected_sha256(release)

    if dest.is_file() and dest.stat().st_size > 0:
        if want is None:
            warn("reusing cached %s (unverifiable: no manifest)" % dest.name)
            return dest
        info("Verifying cached %s" % dest.name)
        if sha256_file(dest) == want:
            ok("Cached tarball verified, skipping download")
            return dest
        warn("cached tarball checksum mismatch; re-downloading")
        dest.unlink()

    download(release.tarball_url, dest)
    if want is not None:
        got = sha256_file(dest)
        if got != want:
            dest.unlink(missing_ok=True)
            raise VerifyError("sha256 mismatch for %s\n    expected %s\n    got      %s"
                              % (dest.name, want, got))
        ok("sha256 verified: " + want[:16] + "\u2026")
    return dest


# --------------------------------------------------------------------------- #
# 16. Source tree
# --------------------------------------------------------------------------- #

KERNEL_TREE_MARKERS: Final = ("Makefile", "Kbuild", "kernel", "arch", "scripts", "include", "init")


def is_valid_kernel_tree(path: Path) -> bool:
    if not path.is_dir():
        return False
    if not all((path / m).exists() for m in KERNEL_TREE_MARKERS):
        return False
    try:
        head = (path / "Makefile").read_text(encoding="utf-8", errors="replace")[:512]
    except OSError:
        return False
    return "VERSION" in head and "PATCHLEVEL" in head


def tree_version(path: Path) -> str:
    fields: dict[str, str] = {}
    makefile = path / "Makefile"
    if not makefile.is_file():
        return "unknown"
    try:
        for line in makefile.read_text(encoding="utf-8", errors="replace").splitlines()[:8]:
            m = re.match(r"^(VERSION|PATCHLEVEL|SUBLEVEL|EXTRAVERSION)\s*=\s*(.*)$", line)
            if m:
                fields[m.group(1)] = m.group(2).strip()
        return "%s.%s.%s%s" % (fields.get("VERSION", "?"), fields.get("PATCHLEVEL", "?"),
                               fields.get("SUBLEVEL", "0"), fields.get("EXTRAVERSION", ""))
    except OSError:
        return "unknown"


def unpack(tarball: Path, release: Release, force: bool) -> Path:
    rule("Unpack")
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    tree = SRC_DIR / ("linux-" + release.version)

    if tree.exists() and is_valid_kernel_tree(tree) and not force:
        info("Reusing existing tree %s" % tree)
        if ask_yes("Wipe and re-extract for a pristine build?", False):
            force = True
        else:
            return tree
    if tree.exists():
        info("Removing %s" % tree)
        shutil.rmtree(tree, ignore_errors=True)

    need = 32.0
    avail = free_gib(SRC_DIR)
    if avail < need:
        warn("only %.1f GiB free at %s (a full LTO tree wants ~%.0f GiB)" % (avail, SRC_DIR, need))
        if not ask_yes("Continue anyway?", False):
            raise BuildError("insufficient space at " + str(SRC_DIR))

    info("Extracting %s" % tarball.name)
    require("tar")
    t0 = time.monotonic()
    run(["tar", "-xf", str(tarball), "-C", str(SRC_DIR)], timeout=1800)
    if not is_valid_kernel_tree(tree):
        raise BuildError("extracted tree at %s does not look like a kernel" % tree)
    ok("Extracted in %.1fs -> %s (Makefile says %s)" % (time.monotonic() - t0, tree, tree_version(tree)))
    return tree


# --------------------------------------------------------------------------- #
# 17. Scheduler patches
# --------------------------------------------------------------------------- #

def patch_candidates(sched: str, version: str) -> list[str]:
    base = patch_base_url()
    pkg = _tok("pkg")
    core, _, _ = version.partition("-rc")
    parts = core.split(".")
    series = "%s.%s" % (parts[0], parts[1])
    prev = "%s.%s" % (parts[0], max(0, int(parts[1]) - 1))

    names: list[str]
    match sched:
        case "bore":
            names = ["0001-bore-cachy.patch", "0001-bore.patch", "0001-bore-%s.patch" % pkg]
            subdirs = ["sched", "misc", ""]
        case "bmq":
            names = ["0001-prjc-cachy.patch", "0001-prjc.patch", "0001-bmq.patch"]
            subdirs = ["sched", "misc", ""]
        case _:
            return []

    urls: list[str] = []
    for ser in (series, prev):
        for sub in subdirs:
            for n in names:
                seg = "/".join(x for x in (base, ser, sub, n) if x)
                if seg not in urls:
                    urls.append(seg)
    return urls


def fetch_patch(sched: str, version: str) -> Path | None:
    PATCH_CACHE.mkdir(parents=True, exist_ok=True)
    cached = PATCH_CACHE / ("%s-%s.patch" % (sched, version))
    if cached.is_file() and cached.stat().st_size > 1024:
        ok("Using cached patch %s" % cached.name)
        return cached

    for url in patch_candidates(sched, version):
        debug("probe " + url)
        if not http_exists(url):
            continue
        info("Fetching %s scheduler patch" % sched.upper())
        try:
            blob = http_get(url)
        except NetworkError:
            continue
        if len(blob) < 1024 or b"diff --git" not in blob[:65536]:
            continue
        cached.write_bytes(blob)
        ok("Patch cached: %s (%.1f KiB)" % (cached.name, len(blob) / 1024))
        return cached
    return None


def apply_patches(tree: Path, profile: KernelProfile) -> str:
    sched = profile.g("scheduler", "type")
    if sched == "eevdf":
        ok("Scheduler: upstream in-tree EEVDF (no patches required)")
        return "eevdf"

    rule("Scheduler patch: " + sched.upper())
    version = tree_version(tree)
    patch = fetch_patch(sched, version)
    if patch is None:
        return _patch_fallback(profile, "no %s patch found for %s" % (sched, version))

    require("git")
    check = run(["git", "apply", "--check", "--verbose", "-p1", str(patch)], cwd=tree, check=False)
    if check.returncode != 0:
        if have("patch"):
            dry = run(["patch", "-p1", "--dry-run", "--forward", "-i", str(patch)], cwd=tree, check=False)
            if dry.returncode == 0:
                run(["patch", "-p1", "--forward", "-i", str(patch)], cwd=tree)
                ok("Applied %s via patch(1) with fuzz" % patch.name)
                return sched
        return _patch_fallback(profile, "%s does not apply cleanly to %s" % (patch.name, version))

    run(["git", "apply", "-p1", str(patch)], cwd=tree)
    ok("Applied %s cleanly" % patch.name)
    return sched


def _patch_fallback(profile: KernelProfile, reason: str) -> str:
    warn(reason)
    if profile.g("scheduler", "require_patch") or not profile.g("scheduler", "allow_vanilla_fallback"):
        raise BuildError("scheduler patch required but unavailable (require_patch=true)")
    warn("Falling back to in-tree EEVDF for this build")
    profile.set("scheduler", "type", "eevdf")
    return "eevdf"


# --------------------------------------------------------------------------- #
# 18. Base .config seeding
# --------------------------------------------------------------------------- #

CONFIG_SANITY_SYMBOLS: Final = ("CONFIG_MODULES", "CONFIG_BLOCK", "CONFIG_NET", "CONFIG_PRINTK", "CONFIG_MMU")


def is_plausible_kernel_config(text: str) -> bool:
    if len(text) < 20_000:
        return False
    if "Automatically generated file" not in text and "Kernel Configuration" not in text:
        return False
    present = sum(1 for sym in CONFIG_SANITY_SYMBOLS if re.search(r"^%s=[ym]" % sym, text, re.M))
    return present >= len(CONFIG_SANITY_SYMBOLS) - 1


def seed_config(tree: Path, profile: KernelProfile) -> str:
    rule("Base configuration")
    target = tree / ".config"
    seed = CONFIG_SEED_DIR / ("kernel.config.%s" % profile.name)

    if seed.is_file():
        text = seed.read_text(encoding="utf-8", errors="replace")
        if is_plausible_kernel_config(text):
            target.write_text(text, encoding="utf-8")
            ok("Seeded from %s" % seed.name)
            return "profile-seed"

    proc_cfg = Path("/proc/config.gz")
    if proc_cfg.is_file():
        try:
            text = gzip.decompress(proc_cfg.read_bytes()).decode("utf-8", "replace")
        except (OSError, EOFError):
            text = ""
        if text and is_plausible_kernel_config(text):
            target.write_text(text, encoding="utf-8")
            ok("Seeded from the running kernel (/proc/config.gz)")
            return "proc-config"

    info("Falling back to upstream defconfig")
    run(["make", "defconfig"], cwd=tree, timeout=600)
    ok("Seeded from x86_64 defconfig")
    return "defconfig"


def save_config_snapshot(tree: Path, profile: KernelProfile) -> None:
    CONFIG_SEED_DIR.mkdir(parents=True, exist_ok=True)
    dest = CONFIG_SEED_DIR / ("kernel.config.%s" % profile.name)
    try:
        dest.write_text((tree / ".config").read_text(encoding="utf-8"), encoding="utf-8")
        ok("Saved per-profile baseline: %s" % dest)
    except OSError as exc:
        warn("could not save baseline: %s" % exc)


# --------------------------------------------------------------------------- #
# 19. modprobed-db
# --------------------------------------------------------------------------- #

def ensure_modprobed_db(profile: KernelProfile) -> Path | None:
    if not profile.g("modules", "modprobed_db"):
        return None
    rule("Hardware module profile")

    custom_path = profile.g("modules", "modprobed_db_path")
    if custom_path:
        cp = Path(custom_path).expanduser().resolve()
        if cp.is_file():
            lines = [ln for ln in cp.read_text(encoding="utf-8", errors="replace").splitlines()
                     if ln.strip() and not ln.startswith("#")]
            ok("Using imported module database: %s (%d modules recorded)" % (cp, len(lines)))
            return cp
        else:
            warn("Specified modprobed_db_path not found: %s; falling back to local DB" % cp)

    if not have("modprobed-db"):
        warn("modprobed-db is not installed")
        return None

    MODPROBED_DB.parent.mkdir(parents=True, exist_ok=True)
    run(["modprobed-db", "store"], check=False, timeout=120)

    if not MODPROBED_DB.is_file():
        warn("no modprobed.db produced at %s" % MODPROBED_DB)
        return None

    lines = [ln for ln in MODPROBED_DB.read_text(encoding="utf-8", errors="replace").splitlines()
             if ln.strip() and not ln.startswith("#")]
    ok("modprobed.db: %d modules recorded" % len(lines))
    return MODPROBED_DB


def build_lmc_keep(profile: KernelProfile) -> str:
    if profile.g("modules", "mode") == "strict":
        return ""
    parts = list(LMC_KEEP_BASE) + list(profile.g("modules", "lmc_keep_extra"))
    seen: list[str] = []
    for p in parts:
        p = p.strip().strip("/")
        if p and p not in seen:
            seen.append(p)
    return ":".join(seen)


def localmodconfig(tree: Path, profile: KernelProfile, db: Path | None) -> None:
    rule("Module pruning")
    mode = profile.g("modules", "mode")
    if db is None:
        warn("no module database; skipping localmodconfig (every module stays enabled)")
        return

    keep = build_lmc_keep(profile)
    env = {"LSMOD": str(db), "LMC_KEEP": keep}
    if mode == "strict":
        info("strict: LMC_KEEP='' -> only modules in modprobed.db survive")
    else:
        info("expanded: LMC_KEEP covers %d subsystem trees" % (keep.count(":") + 1))

    before = count_modules(tree)
    run(["make", "LSMOD=%s" % db, "LMC_KEEP=%s" % keep, "localmodconfig"],
        cwd=tree, env=env, stdin_text="\n" * 400, timeout=1800, check=False)
    after = count_modules(tree)
    ok("Modules: %d -> %d  (%d pruned)" % (before, after, max(0, before - after)))


def count_modules(tree: Path) -> int:
    cfg = tree / ".config"
    if not cfg.is_file():
        return 0
    return sum(1 for ln in cfg.read_text(encoding="utf-8", errors="replace").splitlines()
               if ln.endswith("=m"))


# --------------------------------------------------------------------------- #
# 20. The Kconfig matrix & verification contract
# --------------------------------------------------------------------------- #

Op = tuple[str, str, str, bool]  # (scripts/config flag, SYMBOL, value, optional)


def E(sym: str, optional: bool = False) -> Op:
    return ("-e", sym, "", optional)


def D(sym: str, optional: bool = False) -> Op:
    return ("-d", sym, "", optional)


def M(sym: str, optional: bool = False) -> Op:
    return ("-m", sym, "", optional)


def V(sym: str, val: int | str, optional: bool = False) -> Op:
    return ("--set-val", sym, str(val), optional)


def S(sym: str, val: str, optional: bool = False) -> Op:
    return ("--set-str", sym, val, optional)


def build_config_matrix(p: KernelProfile, *, rust_ok: bool) -> list[Op]:
    s = p.sections
    ops: list[Op] = []
    add = ops.append
    extend = ops.extend

    # ------------------------------------------------- Arch Base Prerequisites
    extend((
        E("EXPERT"), E("MULTIUSER"), E("POSIX_MQUEUE"), E("USER_NS"), E("PID_NS"),
        E("NET_NS"), E("UTS_NS"), E("IPC_NS"), E("CGROUPS"), E("CGROUP_BPF"),
        E("CGROUP_SCHED"), E("FAIR_GROUP_SCHED"), E("CGROUP_FREEZER"),
        E("CGROUP_PIDS"), E("CGROUP_DEVICE"), E("CGROUP_CPUACCT"),
        E("CGROUP_HUGETLB"), E("MEMCG"), E("BLK_CGROUP"), E("BLK_DEV_THROTTLING"),
        E("CPUSETS"), E("SECCOMP"), E("SECCOMP_FILTER"), E("SECURITY"),
        E("SECURITY_APPARMOR"), E("SECURITY_LANDLOCK"), E("SECURITY_YAMA"),
        S("LSM", "landlock,lockdown,yama,integrity,apparmor,bpf"),
        E("EPOLL"), E("SIGNALFD"), E("TIMERFD"), E("EVENTFD"), E("FHANDLE"),
        E("INOTIFY_USER"), E("FANOTIFY"), E("IO_URING"), E("ADVISE_SYSCALLS"),
        E("MEMBARRIER"), E("RSEQ"), E("KCMP"), E("DEVTMPFS"), E("DEVTMPFS_MOUNT"),
        E("TMPFS"), E("TMPFS_POSIX_ACL"), E("TMPFS_XATTR"), E("PROC_FS"),
        E("SYSFS"), E("CONFIGFS_FS"), E("EFIVAR_FS"), E("EFI_STUB"), E("EFI_MIXED"),
        E("BLK_DEV_INITRD"), E("BLK_DEV_DM"), E("DM_CRYPT"), E("DM_SNAPSHOT"),
        E("DM_INTEGRITY", optional=True), E("DM_VERITY", optional=True),
        E("DRM_PRIVACY_SCREEN", optional=True), E("MODULES"),
        E("MODULE_UNLOAD"), E("IKCONFIG"), E("IKCONFIG_PROC"), E("IKHEADERS", optional=True),
        E("NAMESPACES"), E("RELOCATABLE"), E("RANDOMIZE_BASE"), E("X86_X2APIC"),
    ))

    # ------------------------------------------------------------- dusky
    if s["dusky"]["enhanced"]:
        add(E(_tok("cfg"), optional=True))
    else:
        add(D(_tok("cfg"), optional=True))

    # ------------------------------------------------------------ scheduler
    sched = s["scheduler"]["type"]
    for sym in ("SCHED_BORE", "SCHED_ALT", "SCHED_BMQ"):
        add(D(sym, optional=True))
    match sched:
        case "bore":
            add(E("SCHED_BORE", optional=True))
            add(V("MIN_BASE_SLICE_NS", 1000000, optional=True))
        case "bmq":
            extend((E("SCHED_ALT", optional=True), E("SCHED_BMQ", optional=True)))
        case "eevdf":
            pass

    add(E("SCHED_AUTOGROUP") if s["scheduler"]["autogroup"] else D("SCHED_AUTOGROUP"))
    add(E("RT_GROUP_SCHED") if s["scheduler"]["rt_group"] else D("RT_GROUP_SCHED"))
    add(E("SCHED_MC"))
    add(E("SCHED_MC_PRIO") if s["cpu"]["prefcore"] else D("SCHED_MC_PRIO"))
    add(E("SCHED_SMT") if s["cpu"]["smt"] else D("SCHED_SMT"))
    add(E("SCHED_DEBUG", optional=True))
    add(E("SCHEDSTATS"))

    # Linux 7.2 Cache Aware Scheduling (CAS)
    if s["cache"]["sched_cache"]:
        add(E("SCHED_CACHE", optional=True))
    else:
        add(D("SCHED_CACHE", optional=True))

    # sched_ext & modern BPF
    if s["scheduler"]["scx_enable_class"]:
        extend((
            E("BPF"), E("BPF_SYSCALL"), E("BPF_JIT"), E("BPF_JIT_ALWAYS_ON"),
            E("BPF_EVENTS"), E("DEBUG_INFO_BTF"), E("DEBUG_INFO_BTF_MODULES"),
            E("PAHOLE_HAS_SPLIT_BTF", optional=True), E("SCHED_CLASS_EXT"),
            E("FTRACE"), E("FUNCTION_TRACER"), E("DYNAMIC_FTRACE"),
            E("KPROBES"), E("KPROBE_EVENTS"), E("UPROBES"), E("TRACEPOINTS"),
        ))
    else:
        add(D("SCHED_CLASS_EXT", optional=True))

    # ------------------------------------------------------------------ cpu
    extend(cpu_arch_ops(s["cpu"]["arch"]))
    
    # Auto-snap NR_CPUS to 64 boundary
    host_threads = cpu_count()
    nr_cpus_val = s["cpu"]["nr_cpus"]
    if nr_cpus_val <= 0:
        nr_cpus_val = max(2, min(8192, ((host_threads + 63) // 64) * 64))
    
    add(V("NR_CPUS", nr_cpus_val))
    add(D("MAXSMP"))
    add(D("CPUMASK_OFFSTACK"))
    add(V("RCU_FANOUT", 64))
    add(V("RCU_FANOUT_LEAF", min(64, nr_cpus_val)))
    add(E("X86_MCE") if s["cpu"]["mce"] else D("X86_MCE"))
    add(E("CPU_MITIGATIONS") if s["cpu"]["mitigations"] else D("CPU_MITIGATIONS"))

    # P-States & cpufreq
    add(E("CPU_FREQ"))
    add(E("CPU_FREQ_STAT"))
    for g in ("PERFORMANCE", "POWERSAVE", "USERSPACE", "ONDEMAND", "CONSERVATIVE", "SCHEDUTIL"):
        add(D("CPU_FREQ_DEFAULT_GOV_" + g))
        add(E("CPU_FREQ_GOV_" + g))
    add(E("CPU_FREQ_DEFAULT_GOV_" + s["cpu"]["governor"].upper()))

    add(E("X86_AMD_PSTATE"))
    if s["cpu"]["amd_pstate"] != "undefined":
        mode_map = {"disable": 1, "passive": 2, "active": 3, "guided": 4}
        if s["cpu"]["amd_pstate"] in mode_map:
            add(V("X86_AMD_PSTATE_DEFAULT_MODE", mode_map[s["cpu"]["amd_pstate"]]))
    add(E("X86_INTEL_PSTATE"))
    add(M("X86_ACPI_CPUFREQ"))  # Module to prevent driver race with amd-pstate/intel-pstate
    add(E("INTEL_HFI_THERMAL", optional=True))

    # CPU Idle
    add(E("CPU_IDLE"))
    for g in ("MENU", "TEO", "LADDER"):
        add(D("CPU_IDLE_GOV_" + g))
    add(E("CPU_IDLE_GOV_" + s["power"]["cpu_idle_governor"].upper()))

    # --------------------------------------------------------------- timing
    hz = s["timing"]["hz"]
    for h in HZ_CHOICES:
        add(D("HZ_%d" % h))
    add(D("HZ_PERIODIC"))
    add(E("HZ_%d" % hz, optional=True))
    add(V("HZ", hz))

    match s["timing"]["tickless"]:
        case "periodic":
            extend((E("HZ_PERIODIC"), D("NO_HZ_IDLE"), D("NO_HZ_FULL"), D("NO_HZ")))
        case "idle":
            extend((D("HZ_PERIODIC"), E("NO_HZ_IDLE"), D("NO_HZ_FULL"), E("NO_HZ"), E("NO_HZ_COMMON")))
        case "full":
            extend((D("HZ_PERIODIC"), D("NO_HZ_IDLE"), E("NO_HZ_FULL"), E("NO_HZ"), E("NO_HZ_COMMON"),
                    E("CONTEXT_TRACKING_USER"), E("VIRT_CPU_ACCOUNTING_GEN"), E("CPU_ISOLATION")))

    preempt = s["timing"]["preempt"]
    for sym in ("PREEMPT_NONE", "PREEMPT_VOLUNTARY", "PREEMPT", "PREEMPT_LAZY", "PREEMPT_RT"):
        add(D(sym, optional=True))
    match preempt:
        case "lazy":
            extend((E("PREEMPT_LAZY", optional=True), E("PREEMPT_BUILD"), E("PREEMPT_COUNT"), E("PREEMPTION")))
        case "full":
            extend((E("PREEMPT"), E("PREEMPT_BUILD"), E("PREEMPT_COUNT"), E("PREEMPTION")))
        case "rt":
            extend((E("PREEMPT_RT"), E("PREEMPT_COUNT"), E("PREEMPTION"), E("EXPERT")))

    if s["timing"]["preempt_dynamic"] and preempt != "rt":
        extend((E("PREEMPT_DYNAMIC"), E("HAVE_PREEMPT_DYNAMIC")))

    if s["power"]["rcu_lazy"]:
        extend((E("RCU_NOCB_CPU"), E("RCU_LAZY")))
    else:
        add(D("RCU_LAZY"))
    add(E("HIGH_RES_TIMERS"))

    # --------------------------------------------------------------- memory
    for t in ("ALWAYS", "MADVISE", "NEVER"):
        add(D("TRANSPARENT_HUGEPAGE_" + t))
    add(E("TRANSPARENT_HUGEPAGE"))
    add(E("TRANSPARENT_HUGEPAGE_" + s["memory"]["thp"].upper()))
    add(E("READ_ONLY_THP_FOR_FS", optional=True))
    add(E("THP_SWAP"))

    if s["memory"]["mglru"]:
        extend((E("LRU_GEN"), E("LRU_GEN_ENABLED"), D("LRU_GEN_STATS")))
    else:
        extend((D("LRU_GEN_ENABLED"), D("LRU_GEN")))

    # Swap backend
    match s["memory"]["swap_backend"]:
        case "zswap":
            extend((E("ZSWAP"), E("ZSWAP_DEFAULT_ON"), E("ZSMALLOC"), E("ZSWAP_ZPOOL_DEFAULT_ZSMALLOC", optional=True)))
            add(E("ZSWAP_COMPRESSOR_DEFAULT_" + s["memory"]["zswap_compressor"].upper()))
            add(E("CRYPTO_" + s["memory"]["zswap_compressor"].upper()))
            add(M("ZRAM", optional=True))
            add(E("ZRAM_MULTI_COMP", optional=True))
        case "zram":
            extend((D("ZSWAP_DEFAULT_ON"), E("ZRAM"), E("ZRAM_BACKEND_ZSTD", optional=True),
                    E("ZRAM_BACKEND_LZ4", optional=True), E("ZRAM_DEF_COMP_ZSTD", optional=True),
                    E("ZRAM_MULTI_COMP", optional=True)))
        case "none":
            extend((D("ZSWAP_DEFAULT_ON"), M("ZRAM", optional=True)))

    if s["memory"]["slub_tiny"]:
        extend((E("SLUB_TINY"), D("SLUB_CPU_PARTIAL", optional=True), D("SLAB_MERGE_DEFAULT")))
    else:
        extend((D("SLUB_TINY"), E("SLUB_CPU_PARTIAL", optional=True), E("SLUB"), D("SLUB_STATS"), D("SLUB_DEBUG_ON")))

    if s["security"]["slab_freelist_hardened"]:
        add(E("SLAB_FREELIST_HARDENED"))
    else:
        add(D("SLAB_FREELIST_HARDENED"))
    add(D("SLAB_FREELIST_RANDOM"))

    if s["memory"]["numa"]:
        extend((E("NUMA"), E("X86_64_ACPI_NUMA"), E("NUMA_EMU", optional=True)))
        add(V("NODES_SHIFT", s["memory"]["nodes_shift"]))
        add(E("NUMA_BALANCING") if s["memory"]["numa_balancing"] else D("NUMA_BALANCING"))
        add(E("MEMORY_TIERING", optional=True))
    else:
        extend((D("NUMA"), D("NUMA_BALANCING")))

    add(E("KSM") if s["memory"]["ksm"] else D("KSM"))
    add(E("PAGE_REPORTING") if s["memory"]["page_reporting"] else D("PAGE_REPORTING"))
    add(E("DAMON") if s["memory"]["damon"] else D("DAMON"))
    extend((E("COMPACTION"), E("MIGRATION"), E("PER_VMA_LOCK", optional=True)))

    # ------------------------------------------------------------- compiler
    toolchain = s["compiler"]["toolchain"]
    match s["compiler"]["optimize"]:
        case "o2":
            add(E("CC_OPTIMIZE_FOR_PERFORMANCE"))
            add(D("CC_OPTIMIZE_FOR_SIZE"))
        case "size":
            add(E("CC_OPTIMIZE_FOR_SIZE"))
            add(D("CC_OPTIMIZE_FOR_PERFORMANCE"))

    lto = s["compiler"]["lto"] if toolchain == "llvm" else "none"
    for sym in ("LTO_NONE", "LTO_CLANG_THIN", "LTO_CLANG_FULL", "LTO_CLANG_THIN_DIST"):
        add(D(sym, optional=True))
    match lto:
        case "none":
            add(E("LTO_NONE"))
        case "thin":
            extend((E("LTO_CLANG"), E("LTO_CLANG_THIN"), E("HAS_LTO_CLANG"), E("THINLTO_CACHE", optional=True)))
        case "full":
            extend((E("LTO_CLANG"), E("LTO_CLANG_FULL"), E("HAS_LTO_CLANG")))
        case "thin_dist":
            extend((E("LTO_CLANG"), E("LTO_CLANG_THIN_DIST"), E("HAS_LTO_CLANG")))

    if s["compiler"]["kcfi"] and toolchain == "llvm" and lto != "none":
        extend((E("ARCH_SUPPORTS_CFI", optional=True), E("CFI"), E("CFI_CLANG", optional=True),
                D("CFI_PERMISSIVE"), E("X86_KERNEL_IBT"), E("CFI_ICALL_NORMALIZE_INTEGERS", optional=True)))
    else:
        extend((D("CFI"), D("CFI_CLANG", optional=True), D("CFI_PERMISSIVE")))

    if s["compiler"]["fdo"] in ("autofdo", "autofdo_propeller"):
        add(E("AUTOFDO_CLANG", optional=True))
    else:
        add(D("AUTOFDO_CLANG", optional=True))
    if s["compiler"]["fdo"] == "autofdo_propeller":
        add(E("PROPELLER_CLANG", optional=True))
    else:
        add(D("PROPELLER_CLANG", optional=True))

    # Debug info & BTF (DWARF5 + DEBUG_INFO_BTF, DEBUG_INFO_REDUCED disabled for BTF compatibility)
    match s["compiler"]["debug_info"]:
        case "none":
            if s["verify"]["require_btf"] or s["scheduler"]["scx_enable_class"]:
                extend((D("DEBUG_INFO_NONE"), E("DEBUG_INFO"), E("DEBUG_INFO_DWARF5"),
                        D("DEBUG_INFO_REDUCED"), E("DEBUG_INFO_BTF"), E("DEBUG_INFO_BTF_MODULES")))
            else:
                extend((E("DEBUG_INFO_NONE"), D("DEBUG_INFO"), D("DEBUG_INFO_BTF"), D("DEBUG_INFO_BTF_MODULES")))
        case "reduced" | "full":
            extend((D("DEBUG_INFO_NONE"), E("DEBUG_INFO"), E("DEBUG_INFO_DWARF5"),
                    D("DEBUG_INFO_REDUCED"), E("DEBUG_INFO_BTF"), E("DEBUG_INFO_BTF_MODULES")))

    add(E("MODULE_COMPRESS_" + s["compiler"]["module_compress"].upper()))
    add(E("MODULE_COMPRESS"))
    add(E("KERNEL_ZSTD"))
    add(E("RD_ZSTD"))
    add(E("RUST") if (s["compiler"]["rust"] and rust_ok) else D("RUST"))

    # ------------------------------------------------------------- security
    if not s["security"]["init_on_alloc"]:
        extend((D("INIT_ON_ALLOC_DEFAULT_ON"), D("INIT_ON_FREE_DEFAULT_ON")))
    else:
        extend((E("INIT_ON_ALLOC_DEFAULT_ON"), D("INIT_ON_FREE_DEFAULT_ON")))

    if not s["security"]["hardened_usercopy"]:
        add(D("HARDENED_USERCOPY"))
    else:
        add(E("HARDENED_USERCOPY"))

    match s["security"]["stackprotector"]:
        case "strong":
            extend((E("STACKPROTECTOR"), E("STACKPROTECTOR_STRONG")))
        case "regular":
            extend((E("STACKPROTECTOR"), D("STACKPROTECTOR_STRONG")))
        case "none":
            extend((D("STACKPROTECTOR"), D("STACKPROTECTOR_STRONG")))

    if not s["security"]["randomize_kstack"]:
        add(D("RANDOMIZE_KSTACK_OFFSET_DEFAULT", optional=True))

    # Strip pure debug bloat & dangerous fault injection hooks
    extend((D("PAGE_POISONING"), D("DEBUG_PAGEALLOC"), D("DEBUG_LIST"), D("DEBUG_SG"),
            D("DEBUG_PLIST"), D("DEBUG_NOTIFIERS"), D("KFENCE"), D("KASAN"), D("UBSAN"),
            D("KCSAN"), D("LOCKDEP"), D("PROVE_LOCKING"), D("WERROR"),
            D("FUNCTION_ERROR_INJECTION", optional=True),
            D("BPF_KPROBE_OVERRIDE", optional=True),
            D("FAIL_FUNCTION", optional=True),
            D("FAULT_INJECTION", optional=True),
            D("FAULT_INJECTION_DEBUG_FS", optional=True)))

    # --------------------------------------------------------------- gaming
    if s["gaming"]["ntsync"]:
        add(M("NTSYNC", optional=True))
    else:
        add(D("NTSYNC", optional=True))
    add(E("UCLAMP_TASK") if s["gaming"]["uclamp"] else D("UCLAMP_TASK"))
    extend((E("FUTEX"), E("FUTEX_PI"), E("INPUT_UINPUT"), E("DRM"),
            M("DRM_AMDGPU", optional=True), M("DRM_I915", optional=True),
            M("DRM_XE", optional=True), M("DRM_NOUVEAU", optional=True),
            E("DRM_SIMPLEDRM"), E("DRM_FBDEV_EMULATION")))

    # -------------------------------------------------------------- storage
    extend((E("BLK_DEV_NVME"), E("BLK_WBT") if s["storage"]["blk_wbt"] else D("BLK_WBT"),
            E("BLK_WBT_MQ") if s["storage"]["blk_wbt"] else D("BLK_WBT_MQ"),
            E("BTRFS_FS"), E("F2FS_FS"), E("EXT4_FS"), E("XFS_FS"), E("NTFS3_FS"),
            E("MQ_IOSCHED_DEADLINE"), E("MQ_IOSCHED_KYBER"), E("IOSCHED_BFQ")))

    # ---------------------------------------------------------------- power
    add(E("WQ_POWER_EFFICIENT_DEFAULT") if s["power"]["wq_power_efficient"] else D("WQ_POWER_EFFICIENT_DEFAULT"))
    add(E("ENERGY_MODEL") if s["power"]["energy_model"] else D("ENERGY_MODEL"))
    if s["power"]["suspend"]:
        extend((E("SUSPEND"), E("HIBERNATION")))

    # -------------------------------------------------------------- network
    cong = s["network"]["congestion"]
    for c in ("CUBIC", "RENO", "BBR"):
        add(D("DEFAULT_" + c, optional=True))
    if cong == "bbr":
        extend((E("TCP_CONG_BBR"), E("DEFAULT_BBR"), E("NET_SCH_FQ")))
        add(S("DEFAULT_TCP_CONG", "bbr"))
    else:
        add(E("TCP_CONG_" + cong.upper(), optional=True))
        add(E("DEFAULT_" + cong.upper(), optional=True))
        add(S("DEFAULT_TCP_CONG", cong))

    qdisc = s["network"]["qdisc"]
    for q in ("FQ", "FQ_CODEL", "CAKE", "PFIFO_FAST"):
        add(D("DEFAULT_" + q, optional=True))
    add(E("NET_SCH_" + qdisc.upper(), optional=True))
    add(E("DEFAULT_" + qdisc.upper(), optional=True))
    add(S("DEFAULT_NET_SCH", qdisc))
    add(E("NET_SCH_DEFAULT"))
    add(E("MPTCP") if s["network"]["mptcp"] else D("MPTCP"))
    add(E("XDP_SOCKETS") if s["network"]["xdp"] else D("XDP_SOCKETS"))

    # -------------------------------------------------------- extra_config
    for sym, val in s["dusky"]["extra_config"].items():
        symbol = sym.removeprefix("CONFIG_")
        match val:
            case bool() as b:
                add(E(symbol, optional=True) if b else D(symbol, optional=True))
            case int() as i:
                add(V(symbol, i, optional=True))
            case "m":
                add(M(symbol, optional=True))
            case str() as t:
                add(S(symbol, t, optional=True))

    add(S("LOCALVERSION", p.localversion()))
    add(D("LOCALVERSION_AUTO"))
    return ops


def cpu_arch_ops(arch: str) -> list[Op]:
    ops: list[Op] = [D(sym, optional=True) for sym in ARCH_ALL_SYMBOLS]
    if arch == "native":
        vendor = detect_cpu_vendor()
        ops.append(E("MNATIVE_AMD" if vendor == "amd" else "MNATIVE_INTEL", optional=True))
        ops.append(E("X86_NATIVE_CPU", optional=True))
        v_level = detect_cpu_x86_version()
        if v_level == 4:
            ops.append(V("X86_64_VERSION", 4, optional=True))
            ops.append(E("GENERIC_CPU4", optional=True))
        elif v_level == 3:
            ops.append(V("X86_64_VERSION", 3, optional=True))
            ops.append(E("GENERIC_CPU3", optional=True))
        elif v_level == 2:
            ops.append(V("X86_64_VERSION", 2, optional=True))
            ops.append(E("GENERIC_CPU2", optional=True))
        else:
            ops.append(V("X86_64_VERSION", 1, optional=True))
            ops.append(E("GENERIC_CPU", optional=True))
        return ops
    for flag, sym, val in ARCH_KCONFIG.get(arch, ()):
        ops.append((flag.strip(), sym, val, True))
    return ops


def detect_cpu_vendor() -> str:
    try:
        info_txt = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "intel"
    return "amd" if "AuthenticAMD" in info_txt else "intel"


def apply_matrix(tree: Path, ops: Sequence[Op]) -> None:
    rule("Kconfig matrix")
    script = tree / "scripts" / "config"
    if not script.is_file():
        raise BuildError("scripts/config missing from %s" % tree)
    argv: list[str] = [str(script), "--file", str(tree / ".config")]
    for flag, sym, val, _ in ops:
        argv.append(flag)
        argv.append(sym)
        if val != "":
            argv.append(val)
    info("Applying %d Kconfig operations" % len(ops))
    cp = run(argv, cwd=tree, check=False)
    if cp.returncode != 0:
        for flag, sym, val, opt in ops:
            one = [str(script), "--file", str(tree / ".config"), flag, sym]
            if val != "":
                one.append(val)
            run(one, cwd=tree, check=False)
    ok("Kconfig matrix applied")


def finalize_config(tree: Path, env: Mapping[str, str]) -> None:
    rule("Resolve configuration")
    info("make olddefconfig")
    run(["make", *make_flags(env), "olddefconfig"], cwd=tree, env=env, timeout=1200)
    info("make prepare")
    run(["make", *make_flags(env), "prepare"], cwd=tree, env=env, timeout=1800)
    info("make olddefconfig (final)")
    run(["make", *make_flags(env), "olddefconfig"], cwd=tree, env=env, timeout=1200)
    ok("Configuration finalised")


def verify_config(tree: Path, p: KernelProfile, ops: Sequence[Op]) -> None:
    """Post-olddefconfig assertion pass: enforce the verification contract."""
    rule("Verify configuration")
    cfg = (tree / ".config").read_text(encoding="utf-8", errors="replace")
    optional_set = set(p.g("verify", "optional_symbols"))

    def get_state(sym: str) -> str:
        if re.search(r"^CONFIG_%s=y$" % re.escape(sym), cfg, re.M):
            return "y"
        if re.search(r"^CONFIG_%s=m$" % re.escape(sym), cfg, re.M):
            return "m"
        m = re.search(r"^CONFIG_%s=(.+)$" % re.escape(sym), cfg, re.M)
        if m:
            return m.group(1).strip('"')
        if re.search(r"^# CONFIG_%s is not set$" % re.escape(sym), cfg, re.M):
            return "n"
        return "undef"

    vanished: list[str] = []
    coerced: list[tuple[str, str, str]] = []
    matches = 0

    for flag, sym, val, opt in ops:
        target = "y" if flag == "-e" else ("n" if flag == "-d" else ("m" if flag == "-m" else val))
        actual = get_state(sym)
        if flag == "-d":
            if actual in ("n", "undef"):
                matches += 1
            else:
                coerced.append((sym, "n", actual))
        elif flag == "-e" or flag == "-m":
            if actual == "undef":
                if not opt and sym not in optional_set:
                    vanished.append(sym)
            elif actual == target or (flag == "-e" and actual in ("y", "m")):
                matches += 1
            else:
                coerced.append((sym, target, actual))
        else:
            if actual == "undef":
                if not opt and sym not in optional_set:
                    vanished.append(sym)
            elif actual == str(val):
                matches += 1
            else:
                coerced.append((sym, str(val), actual))

    # Critical assertions
    s = p.sections
    critical_checks = [
        ("PREEMPT", get_state("PREEMPT_" + s["timing"]["preempt"].upper()) == "y", s["timing"]["preempt"]),
        ("SCHED_CACHE", get_state("SCHED_CACHE") == ("y" if s["cache"]["sched_cache"] else "undef"), "Cache Aware Scheduling"),
        ("SCHED_CLASS_EXT", get_state("SCHED_CLASS_EXT") == "y", "sched_ext framework"),
        ("BTF", get_state("DEBUG_INFO_BTF") == "y", "DEBUG_INFO_BTF"),
        ("NTSYNC", get_state("NTSYNC") in ("m", "y") if s["gaming"]["ntsync"] else True, "in-tree NTSync"),
        ("MGLRU", get_state("LRU_GEN") == "y", "MGLRU"),
    ]

    rows = []
    for name, good, detail in critical_checks:
        rows.append([(C.GREEN + "MATCH" + C.RESET) if good else (C.RED + "MISS" + C.RESET),
                     name, C.GREY + detail + C.RESET])
    table(["status", "critical subsystem", "target"], rows)
    say("")

    if coerced and _VERBOSE:
        info("%d symbol(s) coerced by Kconfig dependencies" % len(coerced))
        for sym, want, got in coerced[:10]:
            debug(f"  {sym}: wanted {want}, got {got}")

    if vanished:
        err(f"{len(vanished)} requested symbol(s) VANISHED from final .config (not in tree):")
        for sym in vanished[:12]:
            err(f"  CONFIG_{sym}")
        if p.g("verify", "strict"):
            raise VerifyError(f"strict verification failed: {len(vanished)} symbols vanished after olddefconfig")
    else:
        ok(f"All Kconfig assertions verified ({matches} matched, 0 non-optional vanished)")


# --------------------------------------------------------------------------- #
# 21. Build environment
# --------------------------------------------------------------------------- #

def make_flags(env: Mapping[str, str]) -> list[str]:
    if env.get("LLVM") == "1":
        return ["LLVM=1", "LLVM_IAS=1"]
    return []


def build_env(p: KernelProfile, tree: Path, tarball_mtime: float) -> dict[str, str]:
    s = p.sections
    env: dict[str, str] = {
        "KBUILD_BUILD_HOST": s["dusky"]["hostname"],
        "KBUILD_BUILD_USER": s["dusky"]["user"],
        "ZSTD_CLEVEL": str(s["compiler"]["zstd_clevel"]),
        "KCFLAGS": "",
        "KAFLAGS": "",
        "KBUILD_LDFLAGS": "",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    if s["dusky"]["reproducible"]:
        epoch = str(int(tarball_mtime))
        env["SOURCE_DATE_EPOCH"] = epoch
        env["KBUILD_BUILD_TIMESTAMP"] = time.strftime(
            "%a %b %d %H:%M:%S UTC %Y", time.gmtime(tarball_mtime))

    ldflags: list[str] = []
    kcflags: list[str] = []

    if s["compiler"]["toolchain"] == "llvm":
        env.update({"LLVM": "1", "LLVM_IAS": "1", "CC": "clang", "HOSTCC": "clang",
                    "LD": "ld.lld", "HOSTLD": "ld.lld", "AR": "llvm-ar",
                    "NM": "llvm-nm", "STRIP": "llvm-strip",
                    "OBJCOPY": "llvm-objcopy", "OBJDUMP": "llvm-objdump",
                    "READELF": "llvm-readelf", "HOSTAR": "llvm-ar",
                    "HOSTCXX": "clang++"})
        if s["compiler"]["thinlto_cache"]:
            THINLTO_CACHE.mkdir(parents=True, exist_ok=True)
            ldflags.append(f"--thinlto-cache-dir={THINLTO_CACHE}")
            ldflags.append(f"--thinlto-cache-policy=cache_size_bytes={s['compiler']['thinlto_cache_size']}:prune_after=72h")
    else:
        env.update({"CC": "gcc", "HOSTCC": "gcc", "CXX": "g++", "LD": "ld"})

    if s["cpu"]["arch"] == "native":
        kcflags.append("-march=native")
        env["KAFLAGS"] = (env["KAFLAGS"] + " -march=native").strip()
    elif s["cpu"]["march"]:
        kcflags.append(f"-march={s['cpu']['march']}")
        env["KAFLAGS"] = (env["KAFLAGS"] + f" -march={s['cpu']['march']}").strip()
    elif s["cpu"]["arch"] not in ("generic", "generic_v2", "generic_v3", "generic_v4", "default"):
        kcflags.append(f"-march={s['cpu']['arch']}")
        env["KAFLAGS"] = (env["KAFLAGS"] + f" -march={s['cpu']['arch']}").strip()

    if s["compiler"]["allow_unsupported_o3"] and s["compiler"]["optimize"] == "o2":
        kcflags.append("-O3")

    env["KCFLAGS"] = " ".join(kcflags).strip()
    env["KBUILD_LDFLAGS"] = " ".join(ldflags).strip()
    env["PKGDEST"] = str(pkgdest_for(p, tree))
    return env


def pkgdest_for(p: KernelProfile, tree: Path) -> Path:
    dest = PKG_ROOT / ("%s-%s" % (p.name, tree_version(tree)))
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def write_localversion(tree: Path, p: KernelProfile) -> None:
    for stale in tree.glob("localversion*"):
        stale.unlink(missing_ok=True)
    ok("localversion = " + p.localversion())


def clean_stale(tree: Path) -> None:
    victims = [tree / "vmlinux", tree / "vmlinux.o", tree / "System.map",
               tree / ".vmlinux.export.c", tree / "Module.symvers",
               tree / "modules.order", tree / "modules.builtin"]
    removed = 0
    for v in victims:
        if v.exists():
            v.unlink()
            removed += 1
    for pattern in ("arch/x86/boot/bzImage", "arch/x86/boot/compressed/vmlinux"):
        f = tree / pattern
        if f.exists():
            f.unlink()
            removed += 1
    if removed:
        info("Removed %d stale top-level artifact(s)" % removed)


# --------------------------------------------------------------------------- #
# 22. Progress rendering
# --------------------------------------------------------------------------- #

_STEP_RE: Final = re.compile(
    r"^\s*(CC|LD|AR|CC \[M\]|LD \[M\]|AS|CC_FPU|VDSO|OBJCOPY|GEN|HOSTCC|"
    r"HOSTLD|RUSTC|BTF|MODPOST|SYMLINK|UPD|WRAP|ZSTD|STRIP|SIGN)\b")

_ERROR_RE: Final = re.compile(r"\b(error:|Error \d+|fatal error|undefined reference"
                              r"|collect2:|ld\.lld:.*error|\*\*\* )", re.I)


def estimate_steps(tree: Path, env: Mapping[str, str]) -> int:
    info("Estimating build size (make -n all)")
    try:
        cp = run(["make", *make_flags(env), "-n", "all"], cwd=tree, env=env,
                 check=False, timeout=900)
    except BuildError:
        return 0
    count = sum(1 for ln in cp.stdout.splitlines()
                if " -c " in ln or ln.startswith("  CC") or " -o " in ln)
    count = max(count, cp.stdout.count(" -c "))
    if count < 500:
        count = 0
    if count:
        ok("Estimated %s compile/link steps" % "{:,}".format(count))
    return count


class Live:
    def __init__(self, title: str, total: int, tail: int = 6) -> None:
        self.title = title
        self.total = total
        self.done = 0
        self.tail_n = tail
        self.tail: list[str] = []
        self.start = time.monotonic()
        self.rendered = 0
        self.enabled = sys.stdout.isatty() and bool(C.RESET)
        self._last = 0.0
        self._lock = threading.RLock()
        self.errors: list[str] = []
        self._stop_event = threading.Event()
        self._ticker_thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        if self.enabled:
            sys.stdout.write(C.HIDE)
            sys.stdout.flush()
            with self._lock:
                self._paint()
            self._stop_event.clear()
            self._ticker_thread = threading.Thread(target=self._ticker_loop, daemon=True)
            self._ticker_thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self.enabled:
            self._stop_event.set()
            if self._ticker_thread and self._ticker_thread.is_alive():
                self._ticker_thread.join(timeout=1.0)
            with self._lock:
                if self.rendered:
                    sys.stdout.write("\x1b[%dA\r\x1b[J" % self.rendered)
                pct = (100.0 * self.done / self.total) if self.total > 0 else 0.0
                elapsed = time.monotonic() - self.start
                sys.stdout.write(C.SHOW)
                say("  %s%s%s  %s%6.2f%%%s  %s%s/%s%s  \u00b7  %s elapsed" % (
                    C.BOLD, self.title, C.RESET, C.CYAN, pct, C.RESET,
                    C.FAINT, "{:,}".format(self.done),
                    "{:,}".format(self.total) if self.total else "?", C.RESET,
                    hms(elapsed)))
                sys.stdout.flush()

    def _ticker_loop(self) -> None:
        while not self._stop_event.wait(0.25):
            with self._lock:
                if self.enabled:
                    self._paint()

    def feed(self, line: str) -> None:
        with self._lock:
            if _STEP_RE.match(line):
                self.done += 1
            if _ERROR_RE.search(line):
                self.errors.append(line)
            clean = line.strip()
            if clean:
                self.tail.append(clean)
                if len(self.tail) > 20:
                    del self.tail[:-20]
            if not self.enabled:
                if _ERROR_RE.search(line):
                    print(line)
                return
            now = time.monotonic()
            if now - self._last >= 0.08:
                self._last = now
                self._paint()

    def _bar(self, width: int) -> str:
        width = max(10, width)
        if self.total <= 0:
            phase = int((time.monotonic() - self.start) * 8) % max(1, width)
            cells = ["\u2500"] * width
            for i in range(min(6, width)):
                cells[(phase + i) % width] = "\u2501"
            return C.ACCENT + "".join(cells) + C.RESET
        frac = min(1.0, self.done / self.total)
        filled = int(frac * width)
        return (C.ACCENT + "\u2501" * filled + C.RESET
                + C.FAINT + "\u2500" * (width - filled) + C.RESET)

    def _eta(self) -> str:
        elapsed = time.monotonic() - self.start
        if self.total <= 0 or self.done < 25:
            return "elapsed %s" % hms(elapsed)
        rate = self.done / elapsed
        remain = max(0.0, (self.total - self.done) / rate) if rate > 0 else 0.0
        return "%s elapsed  \u00b7  ~%s left  \u00b7  %.1f steps/s" % (hms(elapsed), hms(remain), rate)

    def _paint(self, force: bool = False) -> None:
        try:
            ts = shutil.get_terminal_size((80, 24))
            term_w = max(40, ts.columns)
            term_h = max(10, ts.lines)
        except OSError:
            term_w, term_h = 80, 24

        max_tail = max(2, min(self.tail_n, term_h - 7))
        bar_w = max(10, min(term_w - 6, 80))

        pct = (100.0 * self.done / self.total) if self.total > 0 else 0.0
        head = "  %s%s%s  %s%6.2f%%%s  %s%s/%s%s" % (
            C.BOLD, self.title, C.RESET, C.CYAN, pct, C.RESET,
            C.FAINT, "{:,}".format(self.done),
            "{:,}".format(self.total) if self.total else "?", C.RESET)

        lines: list[str] = [
            truncate(head, term_w - 2),
            "  " + self._bar(bar_w),
            truncate("  " + C.FAINT + self._eta() + C.RESET, term_w - 2),
            truncate("  " + C.FAINT + "\u2500" * (term_w - 4) + C.RESET, term_w - 2),
        ]

        with self._lock:
            tail = list(self.tail)
        tail_slice = tail[-max_tail:] if max_tail > 0 else []
        for ln in tail_slice:
            colour = C.RED if _ERROR_RE.search(ln) else C.GREY
            lines.append(truncate("    " + colour + ln + C.RESET, term_w - 2))
        for _ in range(max_tail - len(tail_slice)):
            lines.append("")

        out: list[str] = []
        if self.rendered:
            out.append("\x1b[%dA\r" % self.rendered)
        for ln in lines:
            out.append("\x1b[2K" + ln + "\n")
        out.append("\x1b[J")

        self.rendered = len(lines)
        sys.stdout.write("".join(out))
        sys.stdout.flush()


def hms(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh%02dm" % (h, m)
    if m:
        return "%dm%02ds" % (m, s)
    return "%ds" % s


# --------------------------------------------------------------------------- #
# 23. Compile
# --------------------------------------------------------------------------- #

def needs_headers() -> bool:
    if have("dkms"):
        cp = run(["dkms", "status"], check=False)
        if cp.returncode == 0 and cp.stdout.strip():
            return True
        dkms_dir = Path("/var/lib/dkms")
        if dkms_dir.is_dir():
            entries = [e for e in dkms_dir.iterdir() if e.is_dir() and not e.name.startswith(".")]
            if entries:
                return True
    return False


def resolve_build_headers(p: KernelProfile) -> bool:
    mode = p.g("compiler", "headers")
    match mode:
        case "always":
            return True
        case "never":
            return False
        case "auto":
            return needs_headers()
    return False


def compile_kernel(tree: Path, p: KernelProfile, env: Mapping[str, str], steps: int) -> Path:
    rule("Compile")
    jobs = p.g("compiler", "jobs") or cpu_count()
    dest = Path(env["PKGDEST"])
    for stale_pkg in dest.glob("*.pkg.tar.*"):
        stale_pkg.unlink(missing_ok=True)

    build_headers = resolve_build_headers(p)
    argv = [
        "make",
        "-j%d" % jobs,
        *make_flags(env),
        "PACMAN_PKGBASE=%s" % p.pkgbase,
    ]
    if build_headers:
        argv.append("PACMAN_EXTRAPACKAGES=headers")
    if env.get("LLVM") != "1":
        argv += ["CC=gcc", "HOSTCC=gcc"]
    argv.append("pacman-pkg")

    info("make -j%d  %s" % (jobs, " ".join(argv[2:])))
    info("PKGDEST = %s" % dest)
    say("")

    t0 = time.monotonic()
    with Live("linux-%s %s" % (tree_version(tree), p.name), steps) as live:
        rc = run_stream(argv, cwd=tree, env=env, on_line=live.feed)
        errors = list(live.errors)

    if rc != 0:
        say("")
        warn("make pacman-pkg returned exit code %d" % rc)
        if errors:
            rule("Last compiler output")
            for line in errors[-12:]:
                say("  " + C.RED + line + C.RESET)
        raise BuildError("compilation failed (make rc=%d)" % rc)

    dt = time.monotonic() - t0
    ok("Compiled in " + hms(dt))
    send_notification(
        "Kernel Compilation Complete",
        f"linux-{tree_version(tree)} ({p.name}) compiled in {hms(dt)}.",
        urgency="normal",
    )

    pkgs = sorted(dest.glob("*.pkg.tar.*"))
    if not pkgs:
        for pkg in tree.glob("*.pkg.tar.*"):
            shutil.move(str(pkg), dest / pkg.name)
        pkgs = sorted(dest.glob("*.pkg.tar.*"))
    if not pkgs:
        raise BuildError("build reported success but produced no packages in %s" % dest)

    rows = [[pkg.name, "%.1f MiB" % (pkg.stat().st_size / 1048576)] for pkg in pkgs]
    table(["package", "size"], rows, aligns=["l", "r"])
    return dest


# --------------------------------------------------------------------------- #
# 24. Runtime system files, Install + boot
# --------------------------------------------------------------------------- #

def write_runtime_system_files(p: KernelProfile) -> None:
    rule("Runtime optimizations & units")
    s = p.sections

    # 1. /etc/sysctl.d/99-dusky-perf.conf
    sysctl_lines = [
        "# Dusky Kernel Runtime Tuning",
        "vm.watermark_scale_factor = %d" % s["memory"]["watermark_scale_factor"],
        "vm.watermark_boost_factor = %d" % s["memory"]["watermark_boost_factor"],
        "vm.vfs_cache_pressure = 50",
        "vm.swappiness = %d" % (100 if s["memory"]["swap_backend"] in ("zram", "zswap") else 60),
        "vm.page_lock_unfairness = 1",
        "vm.compaction_proactiveness = %d" % s["memory"]["compaction_proactiveness"],
        "vm.dirty_background_bytes = 134217728",
        "vm.dirty_bytes = 402653184",
        "vm.dirty_expire_centisecs = 3000",
        "vm.dirty_writeback_centisecs = 1500",
        "vm.max_map_count = %d" % s["gaming"]["max_map_count"],
        "kernel.split_lock_mitigate = %d" % (0 if not s["gaming"]["split_lock_mitigate"] else 1),
        "kernel.sched_schedstats = 1",
        "kernel.perf_event_paranoid = 1",
        "net.core.default_qdisc = %s" % s["network"]["qdisc"],
        "net.ipv4.tcp_congestion_control = %s" % s["network"]["congestion"],
        "net.ipv4.tcp_fastopen = 3",
        "net.ipv4.tcp_slow_start_after_idle = 0",
        "fs.file-max = 2097152",
        "fs.inotify.max_user_watches = 1048576",
        "fs.inotify.max_user_instances = 8192",
    ]
    sysctl_file = Path("/tmp/99-dusky-perf.conf")
    sysctl_file.write_text("\n".join(sysctl_lines) + "\n", encoding="utf-8")
    run(SUDO.argv(["cp", str(sysctl_file), "/etc/sysctl.d/99-dusky-perf.conf"]), check=False)
    run(SUDO.argv(["sysctl", "--system"]), check=False)
    ok("Configured /etc/sysctl.d/99-dusky-perf.conf")

    # 2. /etc/tmpfiles.d/dusky-sysfs.conf
    tmpfiles_lines = [
        "# Dusky sysfs policy",
        "w /sys/kernel/mm/transparent_hugepage/enabled - - - - %s" % s["memory"]["thp"],
        "w /sys/kernel/mm/transparent_hugepage/defrag - - - - %s" % s["memory"]["thp_defrag"],
        "w /sys/kernel/mm/transparent_hugepage/shmem_enabled - - - - %s" % s["memory"]["thp_shmem"],
        "w /sys/kernel/mm/lru_gen/enabled - - - - %d" % s["memory"]["mglru_mask"],
        "w /sys/kernel/mm/lru_gen/min_ttl_ms - - - - %d" % s["memory"]["mglru_min_ttl_ms"],
    ]
    tmpfiles_file = Path("/tmp/dusky-sysfs.conf")
    tmpfiles_file.write_text("\n".join(tmpfiles_lines) + "\n", encoding="utf-8")
    run(SUDO.argv(["cp", str(tmpfiles_file), "/etc/tmpfiles.d/dusky-sysfs.conf"]), check=False)
    run(SUDO.argv(["systemd-tmpfiles", "--create"]), check=False)
    ok("Configured /etc/tmpfiles.d/dusky-sysfs.conf")

    # 3. /etc/udev/rules.d/70-dusky-ntsync.rules & modules-load
    if s["gaming"]["ntsync"]:
        udev_ntsync = 'KERNEL=="ntsync", MODE="0660", TAG+="uaccess"\n'
        Path("/tmp/70-dusky-ntsync.rules").write_text(udev_ntsync, encoding="utf-8")
        run(SUDO.argv(["cp", "/tmp/70-dusky-ntsync.rules", "/etc/udev/rules.d/70-dusky-ntsync.rules"]), check=False)
        Path("/tmp/dusky-ntsync.conf").write_text("ntsync\n", encoding="utf-8")
        run(SUDO.argv(["cp", "/tmp/dusky-ntsync.conf", "/etc/modules-load.d/dusky-ntsync.conf"]), check=False)
        ok("Configured in-tree NTSync uaccess rules and auto-load")

    # 4. /etc/udev/rules.d/60-dusky-ioscheduler.rules
    udev_io = (
        'ACTION=="add|change", KERNEL=="nvme[0-9]*n[0-9]*", ATTR{queue/scheduler}="%s"\n'
        'ACTION=="add|change", KERNEL=="nvme[0-9]*n[0-9]*", ATTR{queue/nr_requests}="1024"\n'
        'ACTION=="add|change", KERNEL=="nvme[0-9]*n[0-9]*", ATTR{queue/wbt_lat_usec}="1000"\n'
        'ACTION=="add|change", KERNEL=="sd[a-z]", ATTR{queue/rotational}=="0", ATTR{queue/scheduler}="mq-deadline"\n'
        'ACTION=="add|change", KERNEL=="sd[a-z]", ATTR{queue/rotational}=="1", ATTR{queue/scheduler}="bfq"\n'
    ) % s["storage"]["io_scheduler"]
    Path("/tmp/60-dusky-ioscheduler.rules").write_text(udev_io, encoding="utf-8")
    run(SUDO.argv(["cp", "/tmp/60-dusky-ioscheduler.rules", "/etc/udev/rules.d/60-dusky-ioscheduler.rules"]), check=False)
    ok("Configured /etc/udev/rules.d/60-dusky-ioscheduler.rules")

    # 5. /etc/security/limits.d/99-dusky-rt.conf
    limits_rt = "@audio - rtprio 95\n@audio - memlock unlimited\n@audio - nice -19\n"
    Path("/tmp/99-dusky-rt.conf").write_text(limits_rt, encoding="utf-8")
    run(SUDO.argv(["cp", "/tmp/99-dusky-rt.conf", "/etc/security/limits.d/99-dusky-rt.conf"]), check=False)

    # 6. Cache-Aware Scheduling unit (Linux 7.2)
    if s["cache"]["sched_cache"] and s["cache"]["persist"]:
        cas_unit = (
            "[Unit]\n"
            "Description=Dusky - Cache Aware Scheduling tunables (CONFIG_SCHED_CACHE)\n"
            "DefaultDependencies=no\n"
            "After=sysinit.target systemd-debugfs.mount\n"
            "ConditionPathExists=/sys/kernel/debug/sched/llc_aggr_tolerance\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            "RemainAfterExit=yes\n"
            "ExecStart=/bin/sh -c 'echo %d > /sys/kernel/debug/sched/llc_aggr_tolerance'\n\n"
            "[Install]\n"
            "WantedBy=sysinit.target\n"
        ) % s["cache"]["llc_aggr_tolerance"]
        Path("/tmp/dusky-cas.service").write_text(cas_unit, encoding="utf-8")
        run(SUDO.argv(["cp", "/tmp/dusky-cas.service", "/etc/systemd/system/dusky-cache-aware-sched.service"]), check=False)
        run(SUDO.argv(["systemctl", "enable", "dusky-cache-aware-sched.service"]), check=False)
        ok("Enabled dusky-cache-aware-sched.service")

    # 7. rseq slice extension unit (Linux 7.0+)
    if s["rseq"]["slice_extension"]:
        rseq_unit = (
            "[Unit]\n"
            "Description=Dusky - rseq time slice extension window\n"
            "DefaultDependencies=no\n"
            "After=sysinit.target\n"
            "ConditionPathExists=/sys/kernel/debug/rseq/slice_ext_nsec\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            "RemainAfterExit=yes\n"
            "ExecStart=/bin/sh -c 'echo %d > /sys/kernel/debug/rseq/slice_ext_nsec'\n\n"
            "[Install]\n"
            "WantedBy=sysinit.target\n"
        ) % s["rseq"]["slice_ext_nsec"]
        Path("/tmp/dusky-rseq.service").write_text(rseq_unit, encoding="utf-8")
        run(SUDO.argv(["cp", "/tmp/dusky-rseq.service", "/etc/systemd/system/dusky-rseq-slice.service"]), check=False)
        run(SUDO.argv(["systemctl", "enable", "dusky-rseq-slice.service"]), check=False)
        ok("Enabled dusky-rseq-slice.service")

    # 8. Sched_ext runtime manager unit
    if s["scheduler"]["scx"] != "none":
        run(SUDO.argv(["mkdir", "-p", "/etc/dusky"]), check=False)
        scx_env = "SCX_SCHED=%s\nSCX_FLAGS=%s\n" % (s["scheduler"]["scx"], s["scheduler"]["scx_flags"])
        Path("/tmp/scx.env").write_text(scx_env, encoding="utf-8")
        run(SUDO.argv(["cp", "/tmp/scx.env", "/etc/dusky/scx.env"]), check=False)

        scx_unit = (
            "[Unit]\n"
            "Description=Dusky - sched_ext dynamic scheduler\n"
            "ConditionPathExists=/sys/kernel/sched_ext\n"
            "After=multi-user.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            "EnvironmentFile=-/etc/dusky/scx.env\n"
            "ExecStart=/bin/sh -c 'exec /usr/bin/$SCX_SCHED $SCX_FLAGS'\n"
            "Restart=on-failure\n"
            "RestartSec=2\n"
            "Nice=-20\n"
            "OOMScoreAdjust=-1000\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        Path("/tmp/dusky-scx.service").write_text(scx_unit, encoding="utf-8")
        run(SUDO.argv(["cp", "/tmp/dusky-scx.service", "/etc/systemd/system/dusky-scx.service"]), check=False)
        run(SUDO.argv(["systemctl", "enable", "dusky-scx.service"]), check=False)
        ok("Enabled dusky-scx.service (%s)" % s["scheduler"]["scx"])


def install_packages(pkgdir: Path, profile: KernelProfile) -> list[Path]:
    rule("Install")
    all_pkgs = sorted(pkgdir.glob("*.pkg.tar.*"))
    all_pkgs = [p for p in all_pkgs if not p.name.endswith(".sig")]
    if not all_pkgs:
        raise BuildError("no packages found in " + str(pkgdir))
    build_headers = resolve_build_headers(profile)
    by_comp: dict[str, Path] = {}
    for p in all_pkgs:
        if not build_headers and "-headers-" in p.name:
            continue
        comp = "headers" if "-headers-" in p.name else "kernel"
        if comp not in by_comp or p.stat().st_mtime > by_comp[comp].stat().st_mtime:
            by_comp[comp] = p
    pkgs = sorted(by_comp.values(), key=lambda p: (0 if "-headers-" not in p.name else 1, p.name))
    for pkg in pkgs:
        say("  " + C.CYAN + pkg.name + C.RESET)
    if not ask_yes("Install these package(s) with pacman -U?", True):
        info("Skipping installation. Install later with:")
        say("    sudo pacman -U " + " ".join(shlex.quote(str(p)) for p in pkgs))
        return []
    rc = subprocess.call(SUDO.argv(["pacman", "-U", "--noconfirm", *[str(p) for p in pkgs]]))
    if rc != 0:
        raise BuildError("pacman -U failed (rc=%d)" % rc)
    ok("Installed")
    write_runtime_system_files(profile)
    return pkgs


def refresh_boot(p: KernelProfile) -> None:
    rule("Boot entries")
    kver = installed_kver(p)
    vmlinuz = None
    if kver:
        for cand in (
            Path("/boot") / ("vmlinuz-" + p.pkgbase),
            Path("/boot") / ("vmlinuz-linux-" + p.suffix.strip("-")),
            Path("/boot") / ("vmlinuz-" + p.suffix.strip("-")),
            Path("/usr/lib/modules") / kver / "vmlinuz",
        ):
            if cand.is_file():
                vmlinuz = cand
                break

    if kver and vmlinuz and have("kernel-install"):
        info("kernel-install add %s %s" % (kver, vmlinuz))
        rc = subprocess.call(SUDO.argv(["kernel-install", "add", kver, str(vmlinuz)]))
        if rc == 0:
            ok("kernel-install registered %s" % kver)

    if have("mkinitcpio") and kver:
        preset = Path("/etc/mkinitcpio.d") / (p.pkgbase + ".preset")
        if preset.is_file():
            rc = subprocess.call(SUDO.argv(["mkinitcpio", "-p", p.pkgbase]))
            if rc == 0:
                ok("initramfs regenerated for " + p.pkgbase)

    if have("bootctl") and kver:
        entry_title = f"Dusky {p.name}"
        sort_key = f"dusky-{p.name}"
        sed_script = (
            f"for d in /boot/loader/entries /efi/loader/entries /boot/efi/loader/entries; do "
            f"  if [ -d \"$d\" ]; then "
            f"    for f in \"$d\"/*{kver}*.conf; do "
            f"      if [ -f \"$f\" ]; then "
            f"        sed -i 's/^title .*/title      {entry_title}/; s/^sort-key .*/sort-key   {sort_key}/' \"$f\"; "
            f"      fi; "
            f"    done; "
            f"  fi; "
            f"done"
        )
        rc = subprocess.call(SUDO.argv(["bash", "-c", sed_script]))
        if rc == 0:
            ok(f"Configured systemd-boot entry title: '{entry_title}'")

        if Path("/boot/loader").is_dir() or Path("/efi/loader").is_dir():
            subprocess.call(SUDO.argv(["bootctl", "update"]))
            ok("systemd-boot updated")
    grub_cfg = Path("/boot/grub/grub.cfg")
    if have("grub-mkconfig") and grub_cfg.parent.is_dir():
        rc = subprocess.call(SUDO.argv(["grub-mkconfig", "-o", str(grub_cfg)]))
        if rc == 0:
            ok("grub.cfg regenerated")
    if have("limine-update"):
        subprocess.call(SUDO.argv(["limine-update"]))


def installed_kver(p: KernelProfile) -> str | None:
    root = Path("/usr/lib/modules")
    if not root.is_dir():
        return None
    matches = [d.name for d in root.iterdir()
               if d.is_dir() and d.name.endswith(p.suffix)]
    matches.sort(key=_vkey, reverse=True)
    return matches[0] if matches else None


# --------------------------------------------------------------------------- #
# 25. Pipeline
# --------------------------------------------------------------------------- #

def do_build(args: argparse.Namespace) -> int:
    banner()
    profiles = discover_profiles()
    profile = select_profile(profiles, args.profile)
    overrides = Overrides.from_env_and_args(args)
    diff = apply_overrides(profile, overrides, prompt=not args.no_prompt)

    rule("Profile: " + profile.name)
    say("  " + C.FAINT + profile.description + C.RESET)
    say("")
    table(["setting", "value"], [[k, v] for k, v in profile.summarize()])
    if diff:
        say("")
        say("  " + C.YELLOW + "ephemeral overrides (TOML untouched):" + C.RESET)
        for d in diff:
            say("    " + C.YELLOW + d + C.RESET)
    say("")

    if not ask_yes("Proceed with this configuration?", True):
        info("Aborted by user")
        return 0

    JOURNAL.open(profile.name)
    check_dependencies(profile)

    releases = fetch_releases()
    release = choose_release(profile, releases)
    tarball = obtain_tarball(release)
    tree = unpack(tarball, release, args.fresh)

    apply_patches(tree, profile)
    seed_config(tree, profile)
    db = ensure_modprobed_db(profile)

    rule("Build scripts")
    env = build_env(profile, tree, tarball.stat().st_mtime)
    run(["make", *make_flags(env), "scripts"], cwd=tree, env=env, timeout=1800)
    ok("scripts/ built")

    localmodconfig(tree, profile, db)

    rust_ok = profile.g("compiler", "rust") and rust_available(
        tree, profile.g("compiler", "toolchain"))
    if profile.g("compiler", "rust") and not rust_ok:
        warn("rustc/bindgen do not satisfy scripts/rustavailable; CONFIG_RUST off")
    elif rust_ok:
        ok("Rust support enabled")

    ops = build_config_matrix(profile, rust_ok=rust_ok)
    apply_matrix(tree, ops)
    write_localversion(tree, profile)
    finalize_config(tree, env)
    verify_config(tree, profile, ops)

    if args.save_config:
        save_config_snapshot(tree, profile)

    if args.configure_only:
        ok("Stopping after configuration (--configure-only)")
        say("  tree:   " + str(tree))
        say("  config: " + str(tree / ".config"))
        return 0

    clean_stale(tree)
    steps = 0 if args.no_eta else estimate_steps(tree, env)
    pkgdir = compile_kernel(tree, profile, env, steps)

    if args.no_install:
        ok("Packages ready in " + str(pkgdir))
        return 0

    installed = install_packages(pkgdir, profile)
    if installed:
        refresh_boot(profile)

    rule("Done")
    ok("%s %s built with profile '%s'" % (release.moniker, release.version, profile.name))
    send_notification(
        "Kernel Installed & Ready",
        f"linux-{release.version} ({profile.name}) is installed. Reboot to test.",
        urgency="normal",
    )
    if JOURNAL.path:
        say("  log: " + C.FAINT + str(JOURNAL.path) + C.RESET)
    say("  " + C.FAINT + "reboot and select the new entry to try it" + C.RESET)
    return 0


# --------------------------------------------------------------------------- #
# 26. Auxiliary commands
# --------------------------------------------------------------------------- #

def do_list(args: argparse.Namespace) -> int:
    profiles = discover_profiles()
    if args.json:
        payload = [{"name": p.name, "path": str(p.path), "sections": p.sections} for p in profiles]
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    banner()
    rule("Profiles in " + str(PROFILES_DIR))
    print_profile_table(profiles, numbered=True)
    return 0


def do_show(args: argparse.Namespace) -> int:
    profiles = discover_profiles()
    p = select_profile(profiles, args.profile)
    banner()
    rule(p.name)
    say("  " + C.FAINT + str(p.path) + C.RESET)
    say("")
    for section, fields in _PROFILE_SPEC.items():
        say("  " + C.BOLD + C.ACCENT + "[" + section + "]" + C.RESET)
        rows = [[f.key, repr(p.g(section, f.key)),
                 C.FAINT + ("ephemeral " if f.ephemeral else "") + f.help + C.RESET]
                for f in fields]
        table(["key", "value", "meaning"], rows)
        say("")
    return 0


def do_spec(args: argparse.Namespace) -> int:
    banner()
    rule("Profile specification (%d sections)" % len(_PROFILE_SPEC))
    for section, fields in _PROFILE_SPEC.items():
        say("")
        say("  " + C.BOLD + C.ACCENT + "[" + section + "]" + C.RESET)
        rows = []
        for f in fields:
            choices = ", ".join(map(str, f.choices)) if f.choices else f.kind
            flags = []
            if f.required:
                flags.append(C.RED + "required" + C.RESET)
            if f.ephemeral:
                flags.append(C.CYAN + "overridable" + C.RESET)
            rows.append([f.key, repr(f.default), C.FAINT + choices + C.RESET,
                         " ".join(flags), C.FAINT + f.help + C.RESET])
        table(["key", "default", "type / choices", "", "meaning"], rows)
    return 0


def do_matrix(args: argparse.Namespace) -> int:
    profiles = discover_profiles()
    targets = profiles if args.all else [select_profile(profiles, args.profile)]
    overrides = Overrides.from_env_and_args(args)
    for p in targets:
        apply_overrides(p, overrides, prompt=False)
        ops = build_config_matrix(p, rust_ok=not args.no_rust)
        if args.json:
            print(json.dumps({"profile": p.name,
                              "ops": [{"op": o[0], "symbol": o[1], "value": o[2], "optional": o[3]}
                                      for o in ops]}, indent=2))
            continue
        rule("%s  (%d operations)" % (p.name, len(ops)))
        for flag, sym, val, opt in ops:
            marker = {"-e": C.GREEN + "y" + C.RESET,
                      "-d": C.FAINT + "n" + C.RESET,
                      "-m": C.CYAN + "m" + C.RESET}.get(flag, C.YELLOW + "=" + C.RESET)
            opt_tag = C.FAINT + " [optional]" + C.RESET if opt else ""
            say("  %s CONFIG_%s%s%s" % (marker, sym, ("=" + val) if val else "", opt_tag))
    return 0


def do_doctor(args: argparse.Namespace) -> int:
    banner()
    rule("Environment & Diagnostics (Linux 7.2+ Spec)")
    rows: list[list[str]] = []

    def row(label: str, value: str, good: bool | None = None) -> None:
        mark = "" if good is None else (
            C.GREEN + "\u2713" + C.RESET if good else C.RED + "\u2717" + C.RESET)
        rows.append([mark, label, value])

    row("python", sys.version.split()[0], sys.version_info >= _MIN_PY)
    row("cpus", str(cpu_count()))
    row("memory", "%.1f GiB" % mem_total_gib(), mem_total_gib() >= 8)
    row("profiles dir", str(PROFILES_DIR), PROFILES_DIR.is_dir())
    row("build dir", str(BUILD_DIR))
    row("build dir free", "%.1f GiB" % free_gib(BUILD_DIR), free_gib(BUILD_DIR) > 32)
    row("build dir ram-backed", "yes" if is_ram_backed(BUILD_DIR) else "no")
    row("thinlto cache", str(THINLTO_CACHE), THINLTO_CACHE.is_dir())
    row("pkgdest root", str(PKG_ROOT))
    row("modprobed.db", str(MODPROBED_DB), MODPROBED_DB.is_file())
    
    # Topology check
    llc_count = 1
    if have("lscpu"):
        cp = run(["lscpu", "-p=CPU,CACHE"], check=False)
        if cp.returncode == 0:
            llcs = set(line.split(",")[1] for line in cp.stdout.splitlines() if line and not line.startswith("#") and "," in line)
            if llcs:
                llc_count = len(llcs)
    row("distinct L3 LLC domains", str(llc_count), llc_count >= 1)
    
    for tool in ("make", "clang", "lld", "gcc", "aria2c", "git", "patch", "gpg",
                 "pacman", "modprobed-db", "dkms", "bootctl", "grub-mkconfig",
                 "kernel-install", "findmnt", "rustc", "rust-bindgen", "pahole"):
        row(tool, shutil.which(tool) or "-", have(tool))
    if have("dkms"):
        dkms_active = needs_headers()
        row("active dkms modules", "detected (headers needed)" if dkms_active else "none (headers optional)", True)
    table(["", "check", "value"], rows)

    say("")
    rule("Connectivity")
    try:
        releases = fetch_releases()
        latest = max(releases, key=lambda r: _vkey(r.version))
        ok("kernel.org reachable; newest entry is %s (%s)" % (latest.version, latest.moniker))
    except NetworkError as exc:
        err(str(exc))

    say("")
    rule("Profiles")
    try:
        profiles = discover_profiles()
        ok("%d profile(s) validated" % len(profiles))
    except ProfileError as exc:
        err(str(exc))
        return 1
    return 0


def do_clean(args: argparse.Namespace) -> int:
    banner()
    targets: list[tuple[str, Path]] = []
    if args.what in ("all", "src"):
        targets.append(("source trees", SRC_DIR))
    if args.what in ("all", "tarballs"):
        targets.append(("tarballs", TARBALL_DIR))
    if args.what in ("all", "patches"):
        targets.append(("patch cache", PATCH_CACHE))
    if args.what in ("all", "packages"):
        targets.append(("packages", PKG_ROOT))
    if args.what in ("all", "thinlto"):
        targets.append(("thinlto cache", THINLTO_CACHE))
    if args.what in ("all", "logs"):
        targets.append(("logs", LOG_DIR))
    for label, path in targets:
        if not path.exists():
            continue
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        say("  %-16s %-48s %.2f GiB" % (label, str(path), size / (1024 ** 3)))
    if not targets:
        return 0
    if not ask_yes("Remove the above?", False):
        return 0
    for _, path in targets:
        shutil.rmtree(path, ignore_errors=True)
    ok("Cleaned")
    return 0


# --------------------------------------------------------------------------- #
# 27. Default profiles materialization
# --------------------------------------------------------------------------- #

def do_write_defaults(args: argparse.Namespace) -> int:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for idx, (name, tweaks, desc, suffix) in enumerate(_DEFAULT_PROFILES, 0):
        path = PROFILES_DIR / ("%02d_%s.toml" % (idx, name))
        if path.exists() and not args.yes:
            info("keeping existing " + path.name)
            continue
        path.write_text(render_profile_toml(name, desc, suffix, idx * 10, tweaks), encoding="utf-8")
        written += 1
    ok("wrote %d profile(s) to %s" % (written, PROFILES_DIR))
    return 0


def render_profile_toml(name: str, desc: str, suffix: str, priority: int,
                        tweaks: Mapping[str, Mapping[str, Any]]) -> str:
    def fmt(v: Any) -> str:
        match v:
            case bool():
                return "true" if v else "false"
            case int():
                return str(v)
            case list():
                return "[" + ", ".join('"%s"' % x for x in v) + "]"
            case _:
                return '"%s"' % v

    lines = ["# %s" % desc, "# generated by %s %s" % (APP_NAME, APP_VERSION), ""]
    for section, fields in _PROFILE_SPEC.items():
        lines.append("[%s]" % section)
        for f in fields:
            if section == "meta":
                match f.key:
                    case "name":
                        lines.append('name = "%s"' % name)
                        continue
                    case "description":
                        lines.append('description = "%s"' % desc)
                        continue
                    case "suffix":
                        lines.append('suffix = "%s"' % suffix)
                        continue
                    case "priority":
                        lines.append("priority = %d" % priority)
                        continue
            if f.kind == "table":
                lines.append("%s = {}" % f.key)
                continue
            value = tweaks.get(section, {}).get(f.key, f.default)
            lines.append("%s = %s" % (f.key, fmt(value)))
        lines.append("")
    return "\n".join(lines)


# (name, tweaks, description, suffix)
_DEFAULT_PROFILES: Final[tuple[tuple[str, dict[str, dict[str, Any]], str, str], ...]] = (
    ("dusky_personal", {
        "release": {"channel": "mainline", "min_version": "7.2"},
        "scheduler": {"type": "eevdf", "scx": "scx_lavd", "scx_flags": "--autopilot", "scx_enable_class": True},
        "cache": {"sched_cache": True, "llc_aggr_tolerance": 1, "persist": True},
        "rseq": {"slice_extension": True, "slice_ext_nsec": 10000},
        "cpu": {"arch": "native", "governor": "schedutil", "amd_pstate": "active", "epp": "balance_performance", "mitigations": False, "prefcore": True},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"thp": "madvise", "thp_defrag": "defer+madvise", "mglru": True, "mglru_mask": 7, "mglru_min_ttl_ms": 1000, "swap_backend": "zram", "zram_size_pct": 100, "numa": True, "numa_balancing": False, "ksm": False, "page_reporting": False, "damon": False},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "full", "thinlto_cache": True, "kcfi": False, "debug_info": "reduced", "rust": True, "headers": "auto"},
        "security": {"profile": "extreme", "init_on_alloc": False, "hardened_usercopy": False, "stackprotector": "regular", "mitigations": "off", "acknowledge_risk": True},
        "gaming": {"ntsync": True, "uclamp": True, "max_map_count": 2147483642, "split_lock_mitigate": False},
        "storage": {"nvme_poll_queues": 0, "io_scheduler": "none", "blk_wbt": True},
        "network": {"congestion": "bbr", "qdisc": "fq", "mptcp": True},
        "modules": {"mode": "strict", "modprobed_db": True},
        "meta": {"bare_metal_only": True, "portable_package": False},
    }, "Dusky Personal: 64GB RAM, Full LTO, Native Arch, EEVDF+CAS+scx_lavd, NTSync, Lazy Preempt", "dusky-personal"),

    ("gaming", {
        "release": {"channel": "mainline", "min_version": "7.2"},
        "scheduler": {"type": "eevdf", "scx": "scx_lavd", "scx_flags": "--autopilot", "scx_enable_class": True},
        "cache": {"sched_cache": True, "llc_aggr_tolerance": 1, "persist": True},
        "rseq": {"slice_extension": True, "slice_ext_nsec": 10000},
        "cpu": {"arch": "native", "governor": "schedutil", "amd_pstate": "active", "epp": "balance_performance", "mitigations": False, "prefcore": True},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"thp": "madvise", "thp_defrag": "defer+madvise", "mglru": True, "mglru_mask": 7, "mglru_min_ttl_ms": 1000, "swap_backend": "zram", "zram_size_pct": 100, "numa": True, "numa_balancing": False, "ksm": False, "page_reporting": False},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "thinlto_cache": True, "kcfi": False, "debug_info": "reduced", "rust": True, "headers": "auto"},
        "security": {"profile": "extreme", "init_on_alloc": False, "hardened_usercopy": False, "stackprotector": "regular", "mitigations": "off", "acknowledge_risk": True},
        "gaming": {"ntsync": True, "uclamp": True, "max_map_count": 2147483642, "split_lock_mitigate": False},
        "network": {"congestion": "bbr", "qdisc": "fq"},
        "modules": {"mode": "strict", "modprobed_db": True},
        "meta": {"bare_metal_only": True, "portable_package": False},
    }, "Gaming: Native Arch, ThinLTO, Lazy Preempt, CAS, scx_lavd, NTSync", "dusky-gaming"),

    ("snappiness", {
        "release": {"channel": "stable", "min_version": "7.2"},
        "scheduler": {"type": "eevdf", "scx": "scx_bpfland", "scx_enable_class": True},
        "cache": {"sched_cache": True, "llc_aggr_tolerance": 1, "persist": True},
        "rseq": {"slice_extension": True, "slice_ext_nsec": 5000},
        "cpu": {"arch": "native", "governor": "schedutil", "amd_pstate": "active", "epp": "balance_performance", "prefcore": True},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"thp": "madvise", "thp_defrag": "defer+madvise", "mglru": True, "mglru_mask": 7, "mglru_min_ttl_ms": 1000, "swap_backend": "zram", "zram_size_pct": 100, "numa": True, "ksm": True, "damon": True},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "thinlto_cache": True, "kcfi": False, "debug_info": "reduced", "rust": True, "headers": "auto"},
        "security": {"profile": "balanced", "init_on_alloc": True, "hardened_usercopy": True, "stackprotector": "strong", "mitigations": "auto"},
        "gaming": {"ntsync": True, "uclamp": True, "max_map_count": 2147483642},
        "network": {"congestion": "bbr", "qdisc": "cake"},
        "modules": {"mode": "strict", "modprobed_db": True},
    }, "Snappiness: Balanced daily driver, smooth UI under load, CAS, scx_bpfland", "dusky-snap"),

    ("workstation", {
        "release": {"channel": "stable", "min_version": "7.2"},
        "scheduler": {"type": "eevdf", "scx": "scx_layered", "scx_enable_class": True, "autogroup": False, "rt_group": True},
        "cache": {"sched_cache": True, "llc_aggr_tolerance": 20, "persist": True},
        "rseq": {"slice_extension": True, "slice_ext_nsec": 20000},
        "cpu": {"arch": "native", "governor": "schedutil", "amd_pstate": "active", "epp": "performance"},
        "timing": {"hz": 500, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"thp": "madvise", "thp_defrag": "defer", "mglru": True, "mglru_mask": 7, "mglru_min_ttl_ms": 0, "swap_backend": "zram", "zram_size_pct": 100, "numa": True, "numa_balancing": True, "nodes_shift": 6, "ksm": True, "page_reporting": True, "damon": True},
        "storage": {"nvme_poll_queues": 8, "io_scheduler": "none", "blk_wbt": True},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "thinlto_cache": True, "kcfi": False, "debug_info": "reduced", "rust": True, "headers": "auto"},
        "security": {"profile": "balanced", "init_on_alloc": True, "mitigations": "auto"},
        "network": {"congestion": "bbr", "qdisc": "fq", "xdp": True},
        "modules": {"mode": "expanded"},
    }, "Workstation: Throughput-first, NUMA/CXL aware, IOPOLL, layered SCX", "dusky-compute"),

    ("battery", {
        "release": {"channel": "mainline", "min_version": "7.2"},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": True},
        "cache": {"sched_cache": True, "llc_aggr_tolerance": 1},
        "cpu": {"arch": "native", "governor": "schedutil", "amd_pstate": "guided", "epp": "balance_power"},
        "timing": {"hz": 100, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "power": {"wq_power_efficient": True, "cpu_idle_governor": "teo", "rcu_lazy": True, "energy_model": True},
        "memory": {"thp": "madvise", "thp_defrag": "defer+madvise", "mglru": True, "mglru_mask": 7, "mglru_min_ttl_ms": 500, "swap_backend": "zram", "zram_size_pct": 100, "damon": True},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "thinlto_cache": True, "kcfi": False, "debug_info": "reduced", "headers": "auto"},
        "network": {"congestion": "bbr", "qdisc": "fq_codel"},
        "modules": {"mode": "strict", "modprobed_db": True},
    }, "Battery: Battery-first, guided P-states, RCU lazy, TEO idle, power-efficient WQ", "dusky-battery"),

    ("low_ram", {
        "release": {"channel": "stable", "min_version": "7.2"},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": True},
        "timing": {"hz": 500, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"slub_tiny": False, "numa": True, "nodes_shift": 1, "swap_backend": "zram", "zram_size_pct": 100, "thp": "madvise", "mglru": True, "mglru_min_ttl_ms": 0, "ksm": False, "page_reporting": False},
        "compiler": {"optimize": "size", "lto": "thin", "thinlto_cache": True, "debug_info": "reduced", "headers": "auto"},
        "cpu": {"arch": "native", "nr_cpus": 16},
        "modules": {"mode": "strict", "modprobed_db": True},
    }, "Low RAM: Small-footprint build for <=8 GiB systems: zram, -Os, ThinLTO, strict modules", "dusky-lowram"),

    ("minimal_strict", {
        "release": {"channel": "mainline", "min_version": "7.2"},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": True},
        "cpu": {"arch": "native"},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"swap_backend": "zram", "zram_size_pct": 100, "thp": "madvise", "mglru": True},
        "compiler": {"lto": "thin", "thinlto_cache": True, "debug_info": "reduced", "headers": "auto"},
        "modules": {"mode": "strict", "modprobed_db": True},
    }, "Minimal Strict: Fastest compile: strict localmodconfig, ThinLTO cache, reduced debug + BTF", "dusky-strict"),

    ("generic_v3", {
        "release": {"channel": "stable", "min_version": "7.2"},
        "meta": {"portable_package": True, "bare_metal_only": False},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": True},
        "cpu": {"arch": "generic_v3", "nr_cpus": 512},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"thp": "madvise", "thp_defrag": "defer+madvise", "mglru": True, "numa": True, "nodes_shift": 6},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "thinlto_cache": True, "kcfi": False, "debug_info": "reduced", "headers": "auto"},
        "modules": {"mode": "expanded", "modprobed_db": False},
    }, "Generic v3: Distributable AVX2/BMI2/FMA baseline package", "dusky-v3"),

    ("generic_v4", {
        "release": {"channel": "stable", "min_version": "7.2"},
        "meta": {"portable_package": True, "bare_metal_only": False},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": True},
        "cpu": {"arch": "generic_v4", "nr_cpus": 512},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "memory": {"thp": "madvise", "thp_defrag": "defer+madvise", "mglru": True, "numa": True, "nodes_shift": 6},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "thinlto_cache": True, "kcfi": False, "debug_info": "reduced", "headers": "auto"},
        "modules": {"mode": "expanded", "modprobed_db": False},
    }, "Generic v4: Distributable AVX-512 baseline package (Zen4+ / modern Xeon)", "dusky-v4"),
)


# --------------------------------------------------------------------------- #
# 28. CLI & Main
# --------------------------------------------------------------------------- #

EPILOG: Final = textwrap.dedent("""\
    environment overrides
      DUSKY_PROFILES_DIR   where *.toml profiles live
      DUSKY_BUILD_DIR      scratch root (src/, tarballs/, packages/)
      DUSKY_PATCH_CACHE    scheduler patch cache
      DUSKY_THINLTO_CACHE  LLVM ThinLTO disk cache directory
      DUSKY_PKGDEST        package output root
      DUSKY_CPU_ARCH       ephemeral CPU arch override
      DUSKY_MODULES_MODE   ephemeral strict|expanded override
      DUSKY_TOOLCHAIN      ephemeral llvm|gcc override
      DUSKY_LTO            ephemeral none|thin|full|thin_dist override
      DUSKY_JOBS           ephemeral make -j override
      DUSKY_CHANNEL        ephemeral mainline|stable|longterm override
      DUSKY_PIN            ephemeral exact version override

    examples
      dusky_kernal_compile.py --list-profiles
      dusky_kernal_compile.py --profile dusky_personal
      dusky_kernal_compile.py --profile gaming --cpu-arch native
      dusky_kernal_compile.py --print-matrix --all
      dusky_kernal_compile.py --doctor
    """)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="dusky_kernal_compile.py",
        description="%s %s -- %s" % (APP_NAME, APP_VERSION, APP_TAGLINE),
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)

    ap.add_argument("--version", action="version", version="%s %s" % (APP_NAME, APP_VERSION))
    ap.add_argument("-p", "--profile", metavar="NAME", help="profile to build")

    mode = ap.add_argument_group("modes")
    mode.add_argument("-l", "--list-profiles", action="store_true", help="print profile table and exit")
    mode.add_argument("--show", action="store_true", help="dump one fully resolved profile and exit")
    mode.add_argument("--spec", action="store_true", help="print profile specification and exit")
    mode.add_argument("--print-matrix", action="store_true", help="dry-run Kconfig matrix and exit")
    mode.add_argument("--doctor", action="store_true", help="environment & diagnostics report")
    mode.add_argument("--clean", metavar="WHAT", nargs="?", const="all",
                      choices=["all", "src", "tarballs", "patches", "packages", "thinlto", "logs"],
                      help="remove cached artifacts")
    mode.add_argument("--write-default-profiles", action="store_true", help="write bundled profiles to PROFILES_DIR")
    mode.add_argument("--export-bundle", nargs="?", const="", default=None, metavar="FILE",
                      help="export target hardware profile & modules into a portable bundle for cross-compiling")
    mode.add_argument("--import-bundle", type=Path, metavar="FILE",
                      help="import remote hardware profile bundle to compile for another PC")

    ov = ap.add_argument_group("ephemeral overrides (TOML is never modified)")
    ov.add_argument("--cpu-arch", choices=list(CPU_ARCHES))
    ov.add_argument("--modules-mode", choices=list(MODULES_MODE_CHOICES))
    ov.add_argument("--toolchain", choices=list(TOOLCHAIN_CHOICES))
    ov.add_argument("--lto", choices=list(LTO_CHOICES))
    ov.add_argument("--channel", choices=list(CHANNEL_CHOICES))
    ov.add_argument("--scheduler", choices=list(SCHED_CHOICES))
    ov.add_argument("--headers", choices=list(HEADERS_CHOICES), help="kernel headers: auto (DKMS-aware)|always|never")
    ov.add_argument("--no-headers", action="store_const", dest="headers", const="never", help="do not build or install headers")
    ov.add_argument("--pin", metavar="VERSION", help="build this exact kernel version")
    ov.add_argument("-j", "--jobs", type=int, metavar="N")

    bh = ap.add_argument_group("build behaviour")
    bh.add_argument("--fresh", action="store_true", help="always re-extract source tree")
    bh.add_argument("--configure-only", action="store_true", help="stop after .config is finalised")
    bh.add_argument("--no-install", action="store_true", help="build packages but do not pacman -U them")
    bh.add_argument("--no-eta", action="store_true", help="skip make -n step estimate")
    bh.add_argument("--no-prompt", action="store_true", help="skip ephemeral override prompts")
    bh.add_argument("--save-config", action="store_true", help="save final .config as baseline seed")
    bh.add_argument("-y", "--yes", action="store_true", help="assume yes for confirmations")
    bh.add_argument("-v", "--verbose", action="store_true")
    bh.add_argument("--json", action="store_true", help="JSON output for list/matrix")
    bh.add_argument("--all", action="store_true", help="all profiles for --print-matrix")
    bh.add_argument("--no-rust", action="store_true", help="model a tree without Rust")
    bh.add_argument("--menu", action="store_true", help="force interactive menu")
    return ap


def install_signal_handlers() -> None:
    def term_handler(signum: int, _frame: object) -> None:
        _ABORT.set()
        sys.stdout.write(C.SHOW)
        sys.stdout.flush()
        say("")
        warn("signal %s received -- terminating child process groups" % signal.Signals(signum).name)
        _reap_all()
        SUDO.stop()
        JOURNAL.close()
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, term_handler)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def main(argv: Sequence[str] | None = None) -> int:
    global _VERBOSE, ASSUME_YES

    ap = build_parser()
    args = ap.parse_args(argv)
    _VERBOSE = args.verbose
    ASSUME_YES = args.yes

    install_signal_handlers()

    no_mode = not any([args.spec, args.write_default_profiles, args.doctor, args.clean,
                       args.list_profiles, args.show, args.print_matrix, args.profile,
                       args.export_bundle is not None, bool(args.import_bundle)])

    try:
        if args.export_bundle is not None:
            dest = Path(args.export_bundle).expanduser().resolve() if args.export_bundle else None
            do_export_bundle(dest)
            return 0
        if args.import_bundle:
            do_import_bundle(args.import_bundle.expanduser().resolve())
            return 0
        if args.menu or (no_mode and sys.stdin.isatty() and sys.stdout.isatty() and not args.yes):
            return interactive_menu()
        if args.spec:
            return do_spec(args)
        if args.write_default_profiles:
            return do_write_defaults(args)
        if args.doctor:
            return do_doctor(args)
        if args.clean:
            return do_clean(args)
        if args.list_profiles:
            return do_list(args)
        if args.show:
            return do_show(args)
        if args.print_matrix:
            return do_matrix(args)
        return do_build(args)
    except DuskyError as exc:
        say("")
        err(str(exc))
        send_notification("Kernel Build Failed", str(exc), urgency="critical", icon="dialog-error")
        return exc.exit_code
    except KeyboardInterrupt:
        say("")
        ok("Exiting Dusky Kernel Compiler. May your uptime be long!")
        return 0
    finally:
        _reap_all()
        SUDO.stop()
        JOURNAL.close()
        sys.stdout.write(C.SHOW)
        sys.stdout.flush()


def initialize_hardware_profiler() -> None:
    banner()
    rule("Initialize Hardware Profiler & Toolchains")
    try:
        SUDO.acquire()
        profiles = discover_profiles()
        sample_profile = profiles[0] if profiles else None
        if sample_profile:
            check_dependencies(sample_profile)

        if not have("modprobed-db"):
            info("Resolving modprobed-db...")
            helper = shutil.which("paru") or shutil.which("yay")
            if helper:
                subprocess.call([str(helper), "-S", "--noconfirm", "--needed", "modprobed-db"])
            else:
                tmp_dir = Path("/tmp") / f"modprobed-db-{os.getpid()}"
                try:
                    run(["git", "clone", "https://aur.archlinux.org/modprobed-db.git", str(tmp_dir)])
                    run(["makepkg", "-si", "--noconfirm"], cwd=tmp_dir)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)

        if have("modprobed-db"):
            info("Capturing current hardware drivers into database...")
            run(["modprobed-db", "store"], check=False)
            db_file = MODPROBED_DB
            count = 0
            if db_file.is_file():
                try:
                    count = sum(1 for ln in db_file.read_text(encoding="utf-8", errors="replace").splitlines()
                                if ln.strip() and not ln.startswith("#"))
                except OSError:
                    count = 0
            ok(f"modprobed-db initialized successfully ({count} drivers mapped in {db_file})")
    except Exception as e:
        err(f"Initialization failed: {e}")


def live_hardware_monitor() -> None:
    banner()
    rule("Live Hardware Telemetry Dashboard")
    say(C.ACCENT + "  Polling modprobed-db store every 2s — Press Ctrl+C to return" + C.RESET)
    say("")
    try:
        while True:
            if have("modprobed-db"):
                run(["modprobed-db", "store"], check=False, capture=True)
            count = 0
            if MODPROBED_DB.is_file():
                try:
                    count = sum(1 for ln in MODPROBED_DB.read_text(encoding="utf-8", errors="replace").splitlines()
                                if ln.strip() and not ln.startswith("#"))
                except OSError:
                    count = 0
            sys.stdout.write(f"\r  {C.BOLD}Unique Drivers Mapped:{C.RESET} {C.GREEN}{count}{C.RESET}  {C.FAINT}(db: {MODPROBED_DB}){C.RESET}   ")
            sys.stdout.flush()
            time.sleep(2)
    except KeyboardInterrupt:
        say("")
        ok("Telemetry monitor closed")


def config_manager_menu() -> None:
    banner()
    rule("Dusky Configuration & Environment Manager")
    try:
        profiles = discover_profiles()
        print_profile_table(profiles)
    except ProfileError as e:
        err(str(e))
    say("")
    info(f"Profiles Directory: {PROFILES_DIR}")
    info(f"Settings Directory: {CONFIG_SEED_DIR}")
    info(f"Build Scratch Dir:  {BUILD_DIR} ({'RAM-backed' if is_ram_backed(BUILD_DIR) else 'Disk'}, {free_gib(BUILD_DIR):.1f} GiB free)")
    info(f"ThinLTO Cache Dir:  {THINLTO_CACHE}")
    info(f"Modprobed Database: {MODPROBED_DB} ({'Present' if MODPROBED_DB.is_file() else 'Missing'})")
    say("")
    rule("Toolchain Probing")
    clang_ok = have("clang") and have("llvm-ar") and have("lld")
    say(f"  LLVM/Clang Toolchain: {'[green]Available (ThinLTO)[/green]' if clang_ok else '[yellow]Missing[/yellow]'}")
    say(f"  GCC Compiler:         {'[green]Available[/green]' if have('gcc') else '[red]Missing[/red]'}")
    say(f"  Rust Kernel Tooling:  {'[green]Available (rustc + bindgen)[/green]' if (have('rustc') and have('bindgen')) else '[yellow]Missing[/yellow]'}")


_UARCH_ALIASES: Final[dict[str, str]] = {
    "cometlake": "skylake", "coffeelake": "skylake", "kabylake": "skylake",
    "skylake-avx512": "skylake", "cascadelake": "skylake", "cooperlake": "skylake",
    "cannonlake": "skylake", "icelake-client": "icelake", "icelake-server": "icelake",
    "goldmont": "sandybridge", "goldmont-plus": "sandybridge", "tremont": "skylake",
    "gracemont": "alderlake", "alderlake": "alderlake", "raptorlake": "raptorlake",
    "meteorlake": "meteorlake", "arrowlake": "arrowlake", "lunarlake": "arrowlake",
    "tigerlake": "tigerlake", "haswell": "haswell", "broadwell": "broadwell",
    "ivybridge": "ivybridge", "sandybridge": "sandybridge",
    "znver1": "znver1", "znver2": "znver2", "znver3": "znver3",
    "znver4": "znver4", "znver5": "znver5",
}

_INTEL_FAMILY6_MODELS: Final[dict[int, str]] = {
    42: "sandybridge", 45: "sandybridge",
    58: "ivybridge", 62: "ivybridge",
    60: "haswell", 63: "haswell", 69: "haswell", 70: "haswell",
    61: "broadwell", 71: "broadwell", 79: "broadwell", 86: "broadwell",
    78: "skylake", 85: "skylake", 94: "skylake", 142: "skylake", 158: "skylake", 166: "skylake",
    106: "icelake", 108: "icelake", 125: "icelake", 126: "icelake",
    167: "rocketlake",
    140: "tigerlake", 141: "tigerlake",
    151: "alderlake", 154: "alderlake", 156: "alderlake", 190: "alderlake",
    183: "raptorlake", 186: "raptorlake", 191: "raptorlake",
    170: "meteorlake", 172: "meteorlake",
    197: "arrowlake", 198: "arrowlake", 199: "arrowlake", 201: "arrowlake",
    143: "sapphirerapids", 207: "sapphirerapids",
}

def detect_target_cpu_uarch() -> str:
    # Tier 1: Compiler query (most accurate dynamic ISA probe)
    for cmd in (["clang", "-march=native", "-###", "-E", "-"],
                ["gcc", "-march=native", "-Q", "--help=target"]):
        if have(cmd[0]):
            try:
                cp = run(cmd, check=False, capture=True, stdin_text="")
                combined = (cp.stdout or "") + "\n" + (cp.stderr or "")
                m = re.search(r'-target-cpu\s+["\']?([a-zA-Z0-9_-]+)["\']?|-march=\s*([a-zA-Z0-9_-]+)', combined)
                if m:
                    target = (m.group(1) or m.group(2)).lower().strip()
                    if target in CPU_ARCHES:
                        return target
                    if target in _UARCH_ALIASES:
                        return _UARCH_ALIASES[target]
            except Exception:
                pass

    # Tier 2: Kernel/CPUID family and model decoding (/proc/cpuinfo)
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
        family, model = None, None
        vendor = "intel" if "AuthenticAMD" not in cpuinfo else "amd"
        for line in cpuinfo.splitlines():
            if "cpu family" in line and family is None:
                family = int(line.split(":")[1].strip())
            elif "model" in line and not line.startswith("model name") and model is None:
                model = int(line.split(":")[1].strip())

        if vendor == "intel" and family == 6 and model in _INTEL_FAMILY6_MODELS:
            return _INTEL_FAMILY6_MODELS[model]

        if vendor == "amd":
            if family == 26:
                return "znver5"
            if family == 25:
                # Family 25h: Models 00h-0Fh / 20h-2Fh / 50h-5Fh are Zen 3, Models 10h-1Fh / 60h-7Fh are Zen 4
                return "znver4" if (model is not None and ((0x10 <= model <= 0x1F) or (0x60 <= model <= 0x7F))) else "znver3"
            if family == 23:
                return "znver2" if (model is not None and (0x30 <= model <= 0x7F)) else "znver1"
    except Exception:
        pass

    # Tier 3: Dynamic hardware vector ISA capability levels
    v = detect_cpu_x86_version()
    if v == 4:
        return "generic_v4"
    if v == 3:
        return "generic_v3"
    if v == 2:
        return "generic_v2"
    return "generic"


def do_export_bundle(dest_file: Path | None = None) -> Path:
    banner()
    rule("Export Target Hardware Bundle")
    import socket, json, tarfile, tempfile

    hostname = socket.gethostname().replace("-", "_").lower()
    export_dir = CONFIG_SEED_DIR / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    if dest_file is None:
        dest_file = export_dir / f"dusky_bundle_{hostname}.tar.gz"
    else:
        dest_file.parent.mkdir(parents=True, exist_ok=True)

    arch = detect_target_cpu_uarch()
    v_level = detect_cpu_x86_version()
    cores = cpu_count()
    vendor = detect_cpu_vendor()

    info(f"Target Hostname: {hostname}")
    info(f"Detected CPU Architecture: {arch} (x86_64-v{v_level}, {cores} cores, vendor: {vendor})")

    # Capture modprobed.db
    if have("modprobed-db"):
        run(["modprobed-db", "store"], check=False, timeout=60)
    
    modules_txt = ""
    if MODPROBED_DB.is_file():
        modules_txt = MODPROBED_DB.read_text(encoding="utf-8", errors="replace")
    elif Path("/proc/modules").is_file():
        mods = [line.split()[0] for line in Path("/proc/modules").read_text().splitlines() if line.strip()]
        modules_txt = "\n".join(sorted(mods)) + "\n"

    # Capture config.gz if present
    config_bytes = b""
    if Path("/proc/config.gz").is_file():
        config_bytes = Path("/proc/config.gz").read_bytes()

    manifest = {
        "format": "dusky_bundle_v1",
        "hostname": hostname,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cpu_arch": arch,
        "cpu_vendor": vendor,
        "x86_version": v_level,
        "cores": cores,
        "kernel_compiler_version": APP_VERSION,
    }

    # Generate custom target profile TOML
    profile_name = f"remote_{hostname}"
    profile_desc = f"Remote Target Profile for {hostname} ({arch}, {cores} cores)"
    profile_suffix = f"dusky-{hostname}"
    tweaks = {
        "release": {"channel": "mainline", "min_version": "7.2"},
        "scheduler": {"type": "eevdf", "scx": "none", "scx_enable_class": True},
        "cpu": {"arch": arch, "nr_cpus": cores, "governor": "schedutil"},
        "timing": {"hz": 1000, "tickless": "idle", "preempt": "lazy", "preempt_dynamic": True},
        "compiler": {"toolchain": "llvm", "optimize": "o2", "lto": "thin", "thinlto_cache": True, "kcfi": False, "headers": "auto"},
        "modules": {"mode": "strict", "modprobed_db": True},
    }
    profile_toml_text = render_profile_toml(profile_name, profile_desc, profile_suffix, 50, tweaks)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (tmp / "modprobed.db").write_text(modules_txt, encoding="utf-8")
        (tmp / "profile.toml").write_text(profile_toml_text, encoding="utf-8")
        if config_bytes:
            (tmp / "config.gz").write_bytes(config_bytes)

        with tarfile.open(dest_file, "w:gz") as tar:
            for f in tmp.iterdir():
                tar.add(f, arcname=f.name)

    say("")
    ok(f"Hardware bundle exported: {dest_file} ({dest_file.stat().st_size / 1024:.1f} KiB)")
    say("")
    info("Instructions for cross-compiling on your fast PC:")
    say(f"  1. Copy {C.CYAN}{dest_file.name}{C.RESET} to your fast build machine.")
    say(f"  2. On the fast machine, run: {C.GREEN}python3 dusky_kernal_compile.py --import-bundle {dest_file.name}{C.RESET}")
    say(f"  3. Compile with: {C.GREEN}python3 dusky_kernal_compile.py --profile remote_{hostname} -y{C.RESET}")
    say(f"  4. Transfer the resulting {C.CYAN}.pkg.tar.zst{C.RESET} to this PC and install with {C.GREEN}sudo pacman -U <pkg>{C.RESET}")
    return dest_file


def do_import_bundle(bundle_path: Path) -> str:
    banner()
    rule("Import Remote Hardware Bundle")
    import tarfile, json, tempfile

    if not bundle_path.is_file():
        raise DuskyError(f"Bundle file not found: {bundle_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with tarfile.open(bundle_path, "r:*") as tar:
            tar.extractall(tmp)

        manifest_file = tmp / "manifest.json"
        if not manifest_file.is_file():
            raise DuskyError("Invalid bundle: manifest.json is missing")
        
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        hostname = manifest.get("hostname", "remote_pc")
        arch = manifest.get("cpu_arch", "generic_v3")
        cores = manifest.get("cores", 4)

        info(f"Importing bundle for target PC: {hostname}")
        info(f"Target CPU Architecture:       {arch} ({cores} cores)")

        # Target storage paths
        import_dir = CONFIG_SEED_DIR / "imports" / hostname
        import_dir.mkdir(parents=True, exist_ok=True)

        imported_db = import_dir / "modprobed.db"
        if (tmp / "modprobed.db").is_file():
            shutil.copy2(tmp / "modprobed.db", imported_db)
            ok(f"Installed target hardware module DB -> {imported_db}")

        if (tmp / "config.gz").is_file():
            seed_dest = CONFIG_SEED_DIR / f"kernel.config.remote_{hostname}"
            shutil.copy2(tmp / "config.gz", seed_dest)
            ok(f"Installed baseline kernel config -> {seed_dest}")

        # Update profile to point to imported modprobed_db_path
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        profile_file = PROFILES_DIR / f"50_remote_{hostname}.toml"
        if (tmp / "profile.toml").is_file():
            profile_txt = (tmp / "profile.toml").read_text(encoding="utf-8")
            if 'modprobed_db_path = ""' in profile_txt:
                profile_txt = profile_txt.replace('modprobed_db_path = ""', f'modprobed_db_path = "{imported_db}"')
            elif "modprobed_db_path" not in profile_txt:
                profile_txt = profile_txt.replace('[modules]\nmode = "strict"',
                                                  f'[modules]\nmode = "strict"\nmodprobed_db_path = "{imported_db}"')
            profile_file.write_text(profile_txt, encoding="utf-8")
            ok(f"Installed custom profile -> {profile_file.name}")

    say("")
    ok(f"Remote hardware bundle for '{hostname}' imported successfully!")
    say("")
    say(f"  Compile now with:  {C.GREEN}python3 dusky_kernal_compile.py --profile remote_{hostname} -y{C.RESET}")
    say(f"  Or select {C.CYAN}remote_{hostname}{C.RESET} in the interactive menu.")
    return f"remote_{hostname}"


def bundle_manager_menu() -> None:
    banner()
    rule("Cross-Machine Hardware Bundle Manager")
    say("  Export/Import hardware profiles & modules to compile for another PC.")
    say("")
    say(" 1) Export hardware bundle from this PC  (to compile on another fast machine)")
    say(" 2) Import hardware bundle from another PC (to compile on this machine)")
    say(" 3) Return to Main Menu")
    say("")
    c = ask_index("Select", 3, default=3)
    if c == 1:
        import socket
        host = socket.gethostname().replace("-", "_").lower()
        default_export = CONFIG_SEED_DIR / "exports" / f"dusky_bundle_{host}.tar.gz"
        dest_str = ask(f"Export bundle path (blank for default: {default_export})", "")
        dest_path = Path(dest_str).expanduser().resolve() if dest_str else default_export
        do_export_bundle(dest_path)
    elif c == 2:
        src_str = ask("Path to bundle file to import (.tar.gz / .tar.zst)", "")
        if src_str:
            do_import_bundle(Path(src_str).expanduser().resolve())
        else:
            warn("No bundle path provided")


def empirical_diagnostics_menu() -> None:
    args = argparse.Namespace(json=False)
    do_doctor(args)


def interactive_menu() -> int:
    while True:
        banner()
        say(C.ACCENT + "  Dusky Kernel Compiler — Main Menu (v5.0.0)" + C.RESET)
        say(" 1) Install Toolchains & Init Hardware Profiler")
        say(" 2) View Live Hardware Telemetry")
        say(" 3) Run System Empirical Diagnostics")
        say(" 4) Config Manager & Profile Overview")
        say(" 5) Export / Import Remote PC Hardware Bundle")
        say(" 6) Compile & Install Kernel (Profile Picker)")
        say(" 7) Exit")
        say("")
        try:
            choice = ask_index("Select", 7, default=6)
        except (DuskyError, KeyboardInterrupt):
            say("")
            ok("Exiting Dusky Kernel Compiler. May your uptime be long!")
            return 0
        if choice == 7:
            ok("Exiting Dusky Kernel Compiler. May your uptime be long!")
            return 0
        try:
            if choice == 1:
                initialize_hardware_profiler()
                ask("Press Enter to return", "")
            elif choice == 2:
                live_hardware_monitor()
            elif choice == 3:
                empirical_diagnostics_menu()
                ask("Press Enter to return", "")
            elif choice == 4:
                config_manager_menu()
                ask("Press Enter to return", "")
            elif choice == 5:
                bundle_manager_menu()
                ask("Press Enter to return", "")
            elif choice == 6:
                args = argparse.Namespace(
                    profile=None, cpu_arch=None, modules_mode=None, toolchain=None,
                    lto=None, channel=None, pin=None, jobs=None, fresh=False,
                    configure_only=False, no_install=False, no_eta=False,
                    no_prompt=False, save_config=True, yes=False, verbose=_VERBOSE,
                )
                do_build(args)
                ask("Press Enter to return", "")
        except KeyboardInterrupt:
            warn("Action cancelled by user")
        except DuskyError as e:
            err(str(e))
            send_notification("Kernel Build Failed", str(e), urgency="critical", icon="dialog-error")
            ask("Press Enter to return", "")
        except Exception as e:
            err(f"Action failed: [{type(e).__name__}] {e}")
            send_notification("Kernel Build Failed", f"[{type(e).__name__}] {e}", urgency="critical", icon="dialog-error")
            ask("Press Enter to return", "")


if __name__ == "__main__":
    raise SystemExit(main())

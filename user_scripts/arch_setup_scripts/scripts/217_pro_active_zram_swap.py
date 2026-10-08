#!/usr/bin/env python3
#d: Proactive idle memory reclaimer & ZRAM swapper (MGLRU Slice Skimmer & Boot Flush)
# Target: Arch Linux / Linux Kernel 7.3+ / systemd 262+

from __future__ import annotations

import argparse
import errno
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

# --- Presentation (Zero-Dependency ANSI) ---
class C:
    BOLD = "\033[1m"
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    CYN = "\033[1;36m"
    RST = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for name in ("BOLD", "RED", "GRN", "YLW", "BLU", "CYN", "RST"):
            setattr(cls, name, "")

def info(msg: str) -> None: print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str) -> None: print(f"{C.GRN}[ OK ]{C.RST} {msg}")
def warn(msg: str) -> None: print(f"{C.YLW}[WARN]{C.RST} {msg}")
def err(msg: str) -> None: print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)
def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)

# --- Configuration & Tuning Defaults ---
PAGE_SIZE: int = os.sysconf("SC_PAGESIZE") if hasattr(os, "sysconf") else 4096
CHUNK_SIZE: int = 32 * 1024 * 1024       # 32 MiB write chunks for low-latency MGLRU paging
PSI_SOME_THRESHOLD: float = 0.50         # Abort sweep if some avg10 >= 0.50%
ZRAM_MAX_USAGE_RATIO: float = 0.90       # Abort sweep if zRAM swap is >= 90% full to protect disk swap
RAM_USAGE_THRESHOLD_RATIO: float = 0.70  # Only trigger periodic sweep if total system RAM usage is >= 70%
RECLAIM_RATIO: float = 0.30              # Reclaim up to 30% of slice anonymous pages per periodic sweep
RAM_TIER_MAX_MB: int = 29696             # Target <=29 GB RAM tier by default; skip large RAM machines (>29GB)
ENABLE_ON_LARGE_RAM: bool = False        # Allow override for machines with > 29 GB RAM
TIMER_INTERVAL: str = "6min"             # Dynamic periodic timer recurrence interval written into systemd timer unit
MAX_PER_RUN_MB: int = 256                # Capped at 256 MiB per periodic sweep to prevent background stutter
BOOT_FLUSH_MAX_MB: int = 1024            # Budget for one-time 60s boot flush to minimize baseline idle RAM
BOOT_FLUSH_DELAY: str = "60s"            # Time after boot to fire the one-shot baseline memory flush

def get_total_ram_bytes() -> int:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    return int(parts[1]) * 1024
    except Exception:
        pass
    return 16 * 1024 * 1024 * 1024

def get_ram_usage() -> tuple[int, int, float]:
    """Returns (used_bytes, total_bytes, usage_ratio) using MemTotal and MemAvailable."""
    total = 0
    available = 0
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    available = int(line.split()[1]) * 1024
    except Exception:
        pass

    if total <= 0:
        total = get_total_ram_bytes()
    used = max(0, total - available) if available > 0 else 0
    ratio = used / total if total > 0 else 0.0
    return used, total, ratio

TOTAL_RAM: int = get_total_ram_bytes()
MAX_PER_RUN: int = MAX_PER_RUN_MB * 1024 * 1024
BOOT_FLUSH_MAX: int = BOOT_FLUSH_MAX_MB * 1024 * 1024
CONF_PATH: Path = Path("/etc/dusky/dusky_pro_active_zram_swap.conf")

def load_runtime_config() -> None:
    """Load dynamic overrides from /etc/dusky/dusky_pro_active_zram_swap.conf if present."""
    global MAX_PER_RUN, MAX_PER_RUN_MB, ZRAM_MAX_USAGE_RATIO, PSI_SOME_THRESHOLD
    global CHUNK_SIZE, RAM_USAGE_THRESHOLD_RATIO, TIMER_INTERVAL, ENABLE_ON_LARGE_RAM
    global RAM_TIER_MAX_MB, RECLAIM_RATIO, BOOT_FLUSH_MAX_MB, BOOT_FLUSH_MAX, BOOT_FLUSH_DELAY
    if not CONF_PATH.exists():
        return
    try:
        with open(CONF_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'")
                if k in ("RECLAIM_RATIO", "APP_IDLE_RECLAIM_RATIO"):
                    val = float(v.rstrip("%")) / 100.0 if "%" in v else float(v)
                    RECLAIM_RATIO = max(0.01, min(1.0, val))
                elif k == "MAX_PER_RUN_MB":
                    MAX_PER_RUN_MB = max(32, min(2048, int(float(v))))
                    MAX_PER_RUN = MAX_PER_RUN_MB * 1024 * 1024
                elif k == "BOOT_FLUSH_MAX_MB":
                    BOOT_FLUSH_MAX_MB = max(64, min(4096, int(float(v))))
                    BOOT_FLUSH_MAX = BOOT_FLUSH_MAX_MB * 1024 * 1024
                elif k == "BOOT_FLUSH_DELAY":
                    if v:
                        BOOT_FLUSH_DELAY = v
                elif k == "CHUNK_SIZE_MB":
                    mb = int(float(v))
                    CHUNK_SIZE = max(4, min(256, mb)) * 1024 * 1024
                elif k == "ZRAM_MAX_USAGE_RATIO":
                    val = float(v.rstrip("%")) / 100.0 if "%" in v else float(v)
                    ZRAM_MAX_USAGE_RATIO = max(0.10, min(1.0, val))
                elif k in ("RAM_USAGE_THRESHOLD_RATIO", "RAM_THRESHOLD_RATIO", "RAM_USAGE_THRESHOLD", "RAM_THRESHOLD"):
                    val = float(v.rstrip("%")) / 100.0 if "%" in v else float(v)
                    RAM_USAGE_THRESHOLD_RATIO = max(0.01, min(1.0, val))
                elif k == "PSI_SOME_THRESHOLD":
                    PSI_SOME_THRESHOLD = float(v)
                elif k == "TIMER_INTERVAL":
                    if v:
                        TIMER_INTERVAL = v
                elif k == "ENABLE_ON_LARGE_RAM":
                    ENABLE_ON_LARGE_RAM = v.lower() in ("1", "true", "yes")
                elif k == "RAM_TIER_MAX_MB":
                    RAM_TIER_MAX_MB = int(float(v))
    except Exception:
        pass

# --- Privilege Escalation ---
def escalate_privileges() -> None:
    if os.geteuid() == 0:
        return
    info("Root privileges required. Escalating...")
    if shutil.which("sudo") is None:
        die("sudo is required to run this script as root (not found in PATH).")
    script_path = str(Path(__file__).resolve())
    os.execvp("sudo", ["sudo", sys.executable, script_path] + sys.argv[1:])

def write_file_atomic(path: Path, content: str, mode: int = 0o644) -> None:
    path = Path(path)
    try:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            try:
                if (path.stat().st_mode & 0o777) != mode:
                    os.chmod(path, mode)
            except FileNotFoundError:
                pass
            return
    except OSError:
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    dir_name = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=f".{path.name}.", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
        try:
            dfd = os.open(dir_name, os.O_DIRECTORY | os.O_RDONLY if hasattr(os, "O_DIRECTORY") else os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise

def get_zram_swap_usage() -> tuple[int, int, float] | None:
    """Returns (used_bytes, size_bytes, usage_ratio) across all /dev/zram* devices in /proc/swaps."""
    try:
        with open("/proc/swaps", "r", encoding="utf-8") as fh:
            lines = fh.read().strip().splitlines()
        if len(lines) <= 1:
            return None
        used_total = 0
        size_total = 0
        found = False
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 5 and parts[0].startswith("/dev/zram"):
                size_total += int(parts[2]) * 1024
                used_total += int(parts[3]) * 1024
                found = True
        if found and size_total > 0:
            return used_total, size_total, (used_total / size_total)
    except Exception:
        pass
    return None

def zram_has_priority() -> bool:
    """Anonymous reclaim follows the kernel swap allocator's priority order."""
    try:
        swaps = [line.split() for line in Path("/proc/swaps").read_text().splitlines()[1:]]
        zram = [int(row[4]) for row in swaps if row[0].startswith("/dev/zram")]
        disk = [int(row[4]) for row in swaps if not row[0].startswith("/dev/zram")]
        return bool(zram) and (not disk or min(zram) > max(disk))
    except (OSError, ValueError, IndexError):
        return False

def is_cgroup2_mounted() -> bool:
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "/sys/fs/cgroup" and "cgroup2" in parts[2]:
                    return True
    except OSError:
        pass
    return Path("/sys/fs/cgroup/cgroup.controllers").exists()

def get_system_pressure() -> float:
    try:
        with open("/proc/pressure/memory", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("some "):
                    parts = line.split()
                    for p in parts:
                        if p.startswith("avg10="):
                            return float(p.split("=")[1])
    except Exception:
        pass
    return 0.0

def find_user_slices(include_all: bool = False) -> list[tuple[Path, str]]:
    """
    Finds user-level cgroup targets across all active user sessions.
    If include_all is False (periodic sweeps): targets only app.slice (desktop applications).
    If include_all is True (one-shot boot flush): targets app.slice, session.slice (daemons/portals),
    and active session scopes (e.g. session-*.scope / Hyprland).
    """
    user_slice = Path("/sys/fs/cgroup/user.slice")
    targets: list[tuple[Path, str]] = []
    if not user_slice.exists():
        return targets
    for user_sub in user_slice.glob("user-*.slice"):
        if not (user_sub.is_dir() and user_sub.name.startswith("user-")):
            continue
        uid_str = user_sub.name[5:-6]
        user_service = user_sub / f"user@{uid_str}.service"
        # Always target app.slice (desktop apps)
        app_slice = user_service / "app.slice"
        if app_slice.exists() and (app_slice / "memory.reclaim").exists():
            targets.append((app_slice, f"{user_sub.name}/app.slice"))

        if include_all:
            # Target user background services & portals
            sess_slice = user_service / "session.slice"
            if sess_slice.exists() and (sess_slice / "memory.reclaim").exists():
                targets.append((sess_slice, f"{user_sub.name}/session.slice"))

            # Target login and compositor session scopes (e.g. session-1.scope)
            for scope in user_sub.glob("session-*.scope"):
                if scope.is_dir() and (scope / "memory.reclaim").exists():
                    targets.append((scope, f"{user_sub.name}/{scope.name}"))
    return targets

def get_cgroup_anon_bytes(cgroup_dir: Path) -> int:
    """Reads anonymous memory bytes from a cgroup's memory.stat file."""
    stat_file = cgroup_dir / "memory.stat"
    if not stat_file.exists():
        return 0
    try:
        with stat_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("anon "):
                    return int(line.split()[1])
    except OSError:
        pass
    return 0

def parse_proactive_reclaimed_bytes(stat_path: Path) -> int:
    try:
        with stat_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("pgsteal_proactive "):
                    try:
                        return int(line.split()[1]) * PAGE_SIZE
                    except (IndexError, ValueError):
                        return 0
    except OSError:
        return 0
    return 0

def reclaim_cgroup_chunked(cgroup_dir: Path, target_bytes: int, label: str, *, force: bool = False) -> tuple[int, int]:
    """
    Submit bounded anonymous-memory reclaim requests with swappiness=max.
    The kernel can reclaim recently used pages too; this is not an idle-page filter.
    """
    reclaim_file = cgroup_dir / "memory.reclaim"
    stat_file = cgroup_dir / "memory.stat"
    if not reclaim_file.exists():
        return 0, 0

    before_steal = parse_proactive_reclaimed_bytes(stat_file) if stat_file.exists() else 0
    reclaimed_requested = 0

    while reclaimed_requested < target_bytes:
        # Recheck priority between chunks: swap topology can change at runtime.
        if not zram_has_priority():
            warn("ZRAM does not exclusively precede disk swap; halting anonymous reclaim.")
            break

        # 1. zRAM saturation guard
        zram_stat = get_zram_swap_usage()
        if zram_stat is None:
            warn(f"No active zRAM swap device detected. Halting sweep on {label} to protect disk swap.")
            break
        used_b, size_b, ratio = zram_stat
        if ratio >= ZRAM_MAX_USAGE_RATIO:
            warn(
                f"zRAM swap capacity reached {ratio * 100:.1f}% ({used_b / (1024*1024):.1f} MB / "
                f"{size_b / (1024*1024):.1f} MB >= {ZRAM_MAX_USAGE_RATIO * 100:.0f}%). "
                f"Halting sweep on {label} to protect disk swap."
            )
            break

        # 2. System memory pressure guard
        psi_sys = get_system_pressure()
        if not force and psi_sys >= PSI_SOME_THRESHOLD:
            warn(f"System memory pressure active ({psi_sys:.2f}% >= {PSI_SOME_THRESHOLD}%). Halting sweep.")
            break

        chunk = min(CHUNK_SIZE, target_bytes - reclaimed_requested,
                    max(0, int(size_b * ZRAM_MAX_USAGE_RATIO) - used_b))
        chunk = chunk // PAGE_SIZE * PAGE_SIZE
        if chunk <= 0:
            break
        try:
            with reclaim_file.open("w", encoding="utf-8") as fh:
                fh.write(f"{chunk} swappiness=max\n")
            reclaimed_requested += chunk
            time.sleep(0.005)  # Brief 5ms yield for interactive desktop fluidity
        except OSError as e:
            if e.errno == errno.EAGAIN:
                # Kernel processed all colder pages available in this MGLRU generation
                reclaimed_requested += chunk
                break
            elif e.errno == errno.EINVAL:
                warn(f"swappiness=max unsupported on {label}")
                break
            elif e.errno == errno.ENOENT:
                break
            else:
                warn(f"Reclaim error on {label}: {e}")
                break

    after_steal = parse_proactive_reclaimed_bytes(stat_file) if stat_file.exists() else 0
    actual_stolen = max(0, after_steal - before_steal)
    return reclaimed_requested, actual_stolen

def perform_reclaim(force: bool = False, boot_flush: bool = False) -> None:
    """
    Executes a bounded slice-level memory reclaim sweep via Linux Kernel MGLRU.
    If boot_flush=True: runs one-time baseline sweep at 60s boot, bypassing the 70% threshold
    and 30% cap, requesting anonymous startup memory reclaim.
    """
    load_runtime_config()

    used_b, total_b, ram_ratio = get_ram_usage()
    total_mb = total_b // (1024 * 1024)

    # 1. RAM Tier Guard: Skip on >29 GB workstations by default to save CPU
    if not force and not ENABLE_ON_LARGE_RAM and total_mb > RAM_TIER_MAX_MB:
        info(
            f"RAM tier > 29 GB ({total_mb} MB detected). Skipping memory sweep to conserve CPU "
            "(large RAM systems have ample memory headroom). Override via ENABLE_ON_LARGE_RAM=true."
        )
        try:
            write_file_atomic(Path("/run/dusky/pro_active_zram_swap.state"), f"Idle (Tier > 29GB: {total_mb} MB)\n", mode=0o644)
        except Exception:
            pass
        return

    # 2. RAM Usage Threshold Guard: Applies to periodic runs (bypassed for one-time boot flush)
    if not boot_flush and not force and ram_ratio < RAM_USAGE_THRESHOLD_RATIO:
        info(
            f"RAM usage below threshold: {ram_ratio * 100:.1f}% ({used_b // (1024*1024)} MB / "
            f"{total_mb} MB < {int(RAM_USAGE_THRESHOLD_RATIO * 100)}% threshold). "
            "Skipping proactive sweep to conserve CPU and avoid unnecessary compression."
        )
        try:
            write_file_atomic(Path("/run/dusky/pro_active_zram_swap.state"), f"Idle (RAM: {ram_ratio * 100:.1f}% < {int(RAM_USAGE_THRESHOLD_RATIO * 100)}%)\n", mode=0o644)
        except Exception:
            pass
        return

    budget_limit = BOOT_FLUSH_MAX if boot_flush else MAX_PER_RUN
    effective_ratio = 1.0 if boot_flush else RECLAIM_RATIO

    if boot_flush:
        info(
            f"Initiating one-time MGLRU baseline boot memory flush at {BOOT_FLUSH_DELAY} (RAM usage={ram_ratio*100:.1f}%, "
            f"budget={BOOT_FLUSH_MAX_MB}MB, ratio=100% anon cap, chunk={CHUNK_SIZE // (1024*1024)}MB, zram_limit={int(ZRAM_MAX_USAGE_RATIO*100)}%)..."
        )
    else:
        info(
            f"Initiating MGLRU proactive slice sweep (RAM usage={ram_ratio*100:.1f}% >= {int(RAM_USAGE_THRESHOLD_RATIO*100)}%, "
            f"budget={MAX_PER_RUN_MB}MB, ratio={int(RECLAIM_RATIO*100)}%, chunk={CHUNK_SIZE // (1024*1024)}MB, zram_limit={int(ZRAM_MAX_USAGE_RATIO*100)}%)..."
        )

    if not is_cgroup2_mounted():
        die("cgroup v2 not mounted at /sys/fs/cgroup. Arch uses cgroup2 by default.")

    if not zram_has_priority():
        warn("Active ZRAM must have higher priority than every disk swap; skipping sweep.")
        return

    # 3. zRAM Swap Guard: Never spill cold pages to disk swap
    zram_stat = get_zram_swap_usage()
    if zram_stat is None:
        warn("No active zRAM swap device detected in /proc/swaps. Skipping sweep to avoid spilling pages to disk swap.")
        return
    used_zram_b, size_zram_b, zram_ratio = zram_stat
    if zram_ratio >= ZRAM_MAX_USAGE_RATIO:
        warn(
            f"zRAM swap capacity at {zram_ratio * 100:.1f}% ({used_zram_b / (1024*1024)} MB / "
            f"{size_zram_b / (1024*1024)} MB >= {ZRAM_MAX_USAGE_RATIO * 100:.0f}%). "
            "Skipping sweep to avoid spilling pages to disk swap."
        )
        try:
            write_file_atomic(Path("/run/dusky/pro_active_zram_swap.state"), f"Idle (zRAM full: {zram_ratio * 100:.0f}%)\n", mode=0o644)
        except Exception:
            pass
        return

    # 4. PSI System Pressure Guard
    psi_sys = get_system_pressure()
    if not force and psi_sys >= PSI_SOME_THRESHOLD:
        info(f"System memory pressure active (some avg10={psi_sys:.2f}% >= {PSI_SOME_THRESHOLD}%). Skipping sweep.")
        try:
            write_file_atomic(Path("/run/dusky/pro_active_zram_swap.state"), f"Idle (PSI active: {psi_sys:.1f}%)\n", mode=0o644)
        except Exception:
            pass
        return

    start_time = time.perf_counter()
    total_requested = 0
    total_stolen = 0

    # 5. Reclaim from user application and session cold pools via MGLRU
    user_targets = find_user_slices(include_all=boot_flush)
    for cgroup_target, label in user_targets:
        if total_requested >= budget_limit:
            break
        anon_bytes = get_cgroup_anon_bytes(cgroup_target)
        if anon_bytes <= 0:
            continue
        remaining_budget = budget_limit - total_requested
        target_reclaim = min(int(anon_bytes * effective_ratio), remaining_budget)
        if target_reclaim <= 0:
            continue
        req, stl = reclaim_cgroup_chunked(cgroup_target, target_reclaim, label, force=force)
        total_requested += req
        total_stolen += stl
        if stl > 0:
            anon_mb = anon_bytes / (1024 * 1024)
            cap_str = "100% (boot)" if boot_flush else f"{int(RECLAIM_RATIO*100)}%"
            ok(f"Reclaimed {stl / (1024*1024):.1f} MB from {label} (anon: {anon_mb:.1f} MB, cap: {cap_str})")

    # 6. Reclaim remaining budget from system services cold pool (system.slice) via MGLRU
    if total_requested < budget_limit:
        system_slice = Path("/sys/fs/cgroup/system.slice")
        if system_slice.exists() and (system_slice / "memory.reclaim").exists():
            sys_anon = get_cgroup_anon_bytes(system_slice)
            if sys_anon > 0:
                remaining_budget = budget_limit - total_requested
                sys_cap = remaining_budget if boot_flush else min(128 * 1024 * 1024, remaining_budget)
                target_sys = min(int(sys_anon * effective_ratio), sys_cap)
                if target_sys > 0:
                    req, stl = reclaim_cgroup_chunked(system_slice, target_sys, "system.slice", force=force)
                    total_requested += req
                    total_stolen += stl
                    if stl > 0:
                        sys_mb = sys_anon / (1024 * 1024)
                        cap_str = "100% (boot)" if boot_flush else f"{int(RECLAIM_RATIO*100)}%"
                        ok(f"Reclaimed {stl / (1024*1024):.1f} MB from system.slice (anon: {sys_mb:.1f} MB, cap: {cap_str})")

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    zram_info = ""
    try:
        res = subprocess.run(["zramctl", "--output", "NAME,DATA,COMPR,TOTAL", "--noheadings"],
                             capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            zram_info = f" | zRAM: {res.stdout.strip()}"
    except Exception:
        pass

    sweep_tag = "Boot flush" if boot_flush else "Sweep"
    ok(f"{sweep_tag} finished in {elapsed_ms:.1f}ms. Reclaimed: {total_stolen / (1024*1024):.1f} MB (pgsteal_proactive){zram_info}")
    try:
        state_path = Path("/run/dusky/pro_active_zram_swap.state")
        stolen_mb = total_stolen / (1024 * 1024)
        stolen_str = f"{int(round(stolen_mb))} MB" if stolen_mb >= 10 else f"{stolen_mb:.1f} MB"
        dur_str = f"{elapsed_ms/1000:.1f}s" if elapsed_ms >= 1000 else f"{int(round(elapsed_ms))}ms"
        prefix = "Boot Flush: " if boot_flush else ""
        write_file_atomic(state_path, f"{prefix}{stolen_str} ({dur_str})\n", mode=0o644)
    except Exception:
        pass

def show_status() -> None:
    """Displays current system memory, zRAM swap, PSI pressure, and service status."""
    load_runtime_config()
    used_b, total_b, ram_ratio = get_ram_usage()
    psi = get_system_pressure()
    zram_stat = get_zram_swap_usage()

    print(f"\n{C.BOLD}{C.CYN}=== Dusky MGLRU Proactive ZRAM Swap Status ==={C.RST}")
    print(f"Total System RAM    : {total_b // (1024*1024)} MB ({total_b / (1024*1024*1024):.1f} GB)")
    print(f"Current RAM Used    : {used_b // (1024*1024)} MB ({ram_ratio*100:.1f}%) [Periodic Threshold: {int(RAM_USAGE_THRESHOLD_RATIO*100)}%]")
    print(f"Slice Reclaim Cap   : {int(RECLAIM_RATIO*100)}% anon per periodic sweep [Run Budget: {MAX_PER_RUN_MB} MB max]")
    print(f"One-Shot Boot Flush : {BOOT_FLUSH_DELAY} after boot [Budget: {BOOT_FLUSH_MAX_MB} MB max, 100% anon cap]")
    print(f"RAM Tier Policy     : {'<= 29 GB active' if total_b // (1024*1024) <= RAM_TIER_MAX_MB or ENABLE_ON_LARGE_RAM else f'> 29 GB skipped (ENABLE_ON_LARGE_RAM={ENABLE_ON_LARGE_RAM})'}")
    print(f"Memory Pressure PSI : {psi:.2f}% [Abort Threshold: {PSI_SOME_THRESHOLD:.2f}%]")

    if zram_stat:
        used_z, size_z, ratio_z = zram_stat
        print(f"zRAM Swap Usage     : {used_z // (1024*1024)} MB / {size_z // (1024*1024)} MB ({ratio_z*100:.1f}%) [Limit: {int(ZRAM_MAX_USAGE_RATIO*100)}%]")
    else:
        print(f"zRAM Swap Usage     : {C.RED}No zRAM swap active!{C.RST}")

    state_file = Path("/run/dusky/pro_active_zram_swap.state")
    last_state = state_file.read_text().strip() if state_file.exists() else "No sweep recorded yet"
    print(f"Last Reclaim State  : {last_state}")

    # Check systemd timer & service status
    for u in ("dusky_boot_zram_flush.timer", "dusky_pro_active_zram_swap.timer"):
        try:
            res = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True, check=False)
            active = res.stdout.strip() == "active"
            label = f"Boot Timer ({BOOT_FLUSH_DELAY})   " if "boot" in u else f"Periodic Timer ({TIMER_INTERVAL}) "
            print(f"{label}: {C.GRN if active else C.RED}{res.stdout.strip()}{C.RST}")
        except Exception:
            pass
    print("=" * 45 + "\n")

def deploy_systemd_units(timer_interval_arg: str = "") -> None:
    info("Deploying lean MGLRU proactive slice skimmer & one-shot boot flush units...")

    install_path = Path("/usr/local/bin/dusky_pro_active_zram_swap")
    current_script = Path(__file__).resolve()

    if current_script != install_path:
        install_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(current_script, install_path)
            os.chmod(install_path, 0o755)
            ok(f"Binary securely installed to {install_path}")
        except OSError as e:
            die(f"Failed to install to {install_path}: {e}")

    # Preserve explicit runtime tuning across installer reruns.
    load_runtime_config()
    timer_int = timer_interval_arg.strip() if timer_interval_arg else TIMER_INTERVAL
    conf_content = f"""# Dusky Proactive ZRAM Swap Runtime Configuration
# Dynamically consumed by /usr/local/bin/dusky_pro_active_zram_swap and gatekeeper
RAM_USAGE_THRESHOLD_RATIO={RAM_USAGE_THRESHOLD_RATIO:.2f}
RECLAIM_RATIO={RECLAIM_RATIO:.2f}
MAX_PER_RUN_MB={MAX_PER_RUN_MB}
BOOT_FLUSH_MAX_MB={BOOT_FLUSH_MAX_MB}
BOOT_FLUSH_DELAY={BOOT_FLUSH_DELAY}
CHUNK_SIZE_MB={CHUNK_SIZE // (1024*1024)}
ZRAM_MAX_USAGE_RATIO={ZRAM_MAX_USAGE_RATIO:.2f}
PSI_SOME_THRESHOLD={PSI_SOME_THRESHOLD:.2f}
ENABLE_ON_LARGE_RAM={'true' if ENABLE_ON_LARGE_RAM else 'false'}
RAM_TIER_MAX_MB={RAM_TIER_MAX_MB}
TIMER_INTERVAL={timer_int}
"""
    write_file_atomic(CONF_PATH, conf_content, mode=0o644)
    ok(f"Runtime configuration written to {CONF_PATH}")

    gate_path = Path("/usr/local/bin/dusky_pro_active_zram_gate")
    gate_content = r"""#!/usr/bin/env bash
# Dusky Proactive ZRAM Swap - Ultra-Fast Native Pre-flight Gatekeeper (Kernel 7.3+ / systemd 262+)
# Executed by systemd via ExecCondition= before spawning Python runtime for periodic sweeps.
# Exits 0 if RAM usage >= threshold and tier matches (proceeds to ExecStart= Python)
# Exits 1 if skipped (bypasses ExecStart= completely with zero Python overhead in <2ms)

set -eo pipefail
export LC_ALL=C

for arg in "$@"; do
    if [[ "$arg" == "--force" ]]; then
        exit 0
    fi
done

CONF="/etc/dusky/dusky_pro_active_zram_swap.conf"
thresh_pct=70
enable_large_ram="false"
ram_tier_max_mb=29696

if [[ -f "$CONF" ]]; then
    while read -r line; do
        if [[ "$line" =~ ^(RAM_USAGE_THRESHOLD_RATIO|RAM_THRESHOLD_RATIO)[[:space:]]*=[[:space:]]*(.*) ]]; then
            val="${BASH_REMATCH[2]}"
            val="${val//[[:space:]]/}"
            val="${val//\"/}"
            val="${val//\'/}"
            val="${val//%/}"
            if [[ "$val" =~ ^0\.([0-9]{1,2}) ]]; then
                frac="${BASH_REMATCH[1]}"
                [[ ${#frac} -eq 1 ]] && frac="${frac}0"
                thresh_pct=$(( 10#$frac ))
            elif [[ "$val" == "1" || "$val" == "1.0" || "$val" == "1.00" ]]; then
                thresh_pct=100
            elif [[ "$val" =~ ^[0-9]+$ ]]; then
                thresh_pct=$(( 10#$val ))
            fi
        elif [[ "$line" =~ ^ENABLE_ON_LARGE_RAM[[:space:]]*=[[:space:]]*(.*) ]]; then
            val="${BASH_REMATCH[1],,}"
            val="${val//[[:space:]]/}"
            val="${val//\"/}"
            val="${val//\'/}"
            enable_large_ram="$val"
        elif [[ "$line" =~ ^RAM_TIER_MAX_MB[[:space:]]*=[[:space:]]*(.*) ]]; then
            val="${BASH_REMATCH[1]}"
            val="${val//[[:space:]]/}"
            val="${val//\"/}"
            val="${val//\'/}"
            if [[ "$val" =~ ^[0-9]+$ ]]; then
                ram_tier_max_mb=$(( 10#$val ))
            fi
        fi
    done < "$CONF"
fi

(( thresh_pct < 1 )) && thresh_pct=1
(( thresh_pct > 100 )) && thresh_pct=100

mem_total=0
mem_avail=0
while read -r key val _; do
    case "$key" in
        MemTotal:)     mem_total=$val ;;
        MemAvailable:) mem_avail=$val ;;
    esac
    (( mem_total && mem_avail )) && break
done < /proc/meminfo

if (( mem_total <= 0 )); then
    exit 0
fi

total_mb=$(( mem_total / 1024 ))

# RAM Tier Guard: On systems > 29 GB, skip unless explicitly enabled in conf
if (( total_mb > ram_tier_max_mb )) && [[ "$enable_large_ram" != "true" && "$enable_large_ram" != "1" && "$enable_large_ram" != "yes" ]]; then
    printf '[INFO] RAM tier > %d MB (%d MB detected). Skipping proactive sweep to conserve CPU (large RAM tier has ample headroom).\n' \
        "$ram_tier_max_mb" "$total_mb"
    mkdir -p /run/dusky 2>/dev/null || true
    printf 'Idle (Tier > %dMB: %d MB)\n' "$ram_tier_max_mb" "$total_mb" > /run/dusky/pro_active_zram_swap.state 2>/dev/null || true
    exit 1
fi

(( mem_avail < 0 )) && mem_avail=0
(( mem_avail > mem_total )) && mem_avail=$mem_total

used_kb=$(( mem_total - mem_avail ))
pct=$(( used_kb * 100 / mem_total ))
pct_tenths=$(( (used_kb * 1000 / mem_total) % 10 ))
used_mb=$(( used_kb / 1024 ))

if (( pct < thresh_pct )); then
    printf '[INFO] RAM usage below threshold: %d.%d%% (%d MB / %d MB < %d%% threshold). Skipping proactive sweep to conserve CPU and avoid unnecessary compression.\n' \
        "$pct" "$pct_tenths" "$used_mb" "$total_mb" "$thresh_pct"
    mkdir -p /run/dusky 2>/dev/null || true
    printf 'Idle (RAM: %d.%d%% < %d%%)\n' "$pct" "$pct_tenths" "$thresh_pct" > /run/dusky/pro_active_zram_swap.state 2>/dev/null || true
    exit 1
fi

exit 0
"""
    write_file_atomic(gate_path, gate_content, mode=0o755)
    ok(f"Native Bash gatekeeper installed to {gate_path}")

    python_bin = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable

    # 1. One-Shot Boot Baseline Memory Flush Units
    boot_service_path = Path("/etc/systemd/system/dusky_boot_zram_flush.service")
    boot_service_content = f"""[Unit]
Description=One-Shot MGLRU Baseline Cold Memory Flush at {BOOT_FLUSH_DELAY} (Kernel 7.3+ / systemd 262+)
Documentation=https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
After=multi-user.target local-fs.target
ConditionPathExists=/sys/fs/cgroup
ConditionPathExists=/sys/fs/cgroup/system.slice

[Service]
Type=oneshot
TimeoutStartSec=60s
ExecStart={python_bin} {install_path} --boot-flush
RemainAfterExit=no
Nice=19
CPUSchedulingPolicy=idle
IOSchedulingClass=idle
CPUWeight=1
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=no
ProtectControlGroups=no
RuntimeDirectory=dusky
RuntimeDirectoryPreserve=yes
ReadWritePaths=/sys/fs/cgroup /run/dusky
LockPersonality=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
MemoryDenyWriteExecute=no
"""
    write_file_atomic(boot_service_path, boot_service_content, mode=0o644)
    ok(f"One-shot boot service written to {boot_service_path}")

    boot_timer_path = Path("/etc/systemd/system/dusky_boot_zram_flush.timer")
    boot_timer_content = f"""[Unit]
Description=Trigger One-Shot MGLRU Baseline Cold Memory Flush at {BOOT_FLUSH_DELAY} Boot
Documentation=https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html

[Timer]
OnBootSec={BOOT_FLUSH_DELAY}
AccuracySec=2s
Persistent=false
Unit=dusky_boot_zram_flush.service

[Install]
WantedBy=timers.target
"""
    write_file_atomic(boot_timer_path, boot_timer_content, mode=0o644)
    ok(f"One-shot boot timer written to {boot_timer_path} (Fires once at {BOOT_FLUSH_DELAY})")

    # 2. Periodic Proactive Memory Skimmer Units
    periodic_service_path = Path("/etc/systemd/system/dusky_pro_active_zram_swap.service")
    periodic_service_content = f"""[Unit]
Description=MGLRU Proactive Slice Memory Skimmer & ZRAM Swapper (Kernel 7.3+ / systemd 262+)
Documentation=https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
After=multi-user.target local-fs.target
ConditionPathExists=/sys/fs/cgroup
ConditionPathExists=/sys/fs/cgroup/system.slice

[Service]
Type=oneshot
TimeoutStartSec=30s
ExecCondition={gate_path}
ExecStart={python_bin} {install_path} --run
RemainAfterExit=no
Nice=19
CPUSchedulingPolicy=idle
IOSchedulingClass=idle
CPUWeight=1
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=no
ProtectControlGroups=no
RuntimeDirectory=dusky
RuntimeDirectoryPreserve=yes
ReadWritePaths=/sys/fs/cgroup /run/dusky
LockPersonality=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
MemoryDenyWriteExecute=no
"""
    write_file_atomic(periodic_service_path, periodic_service_content, mode=0o644)
    ok(f"Periodic service unit written to {periodic_service_path}")

    periodic_timer_path = Path("/etc/systemd/system/dusky_pro_active_zram_swap.timer")
    periodic_timer_content = f"""[Unit]
Description=Trigger MGLRU Proactive ZRAM Swap at {timer_int} Periodic
Documentation=https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html

[Timer]
OnBootSec={timer_int}
OnUnitActiveSec={timer_int}
AccuracySec=5s
RandomizedDelaySec=15s
Persistent=false
Unit=dusky_pro_active_zram_swap.service

[Install]
WantedBy=timers.target
"""
    write_file_atomic(periodic_timer_path, periodic_timer_content, mode=0o644)
    ok(f"Periodic timer unit written to {periodic_timer_path} (Interval: {timer_int})")

    info("Reloading systemd daemon...")
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
    except subprocess.CalledProcessError as e:
        die(f"systemctl daemon-reload failed: {e}")

    info("Enabling and starting memory management timers...")
    for timer_unit in ("dusky_boot_zram_flush.timer", "dusky_pro_active_zram_swap.timer"):
        try:
            subprocess.run(["systemctl", "enable", "--now", timer_unit], check=True)
            ok(f"Started timer: {timer_unit}")
        except subprocess.CalledProcessError as e:
            die(f"Failed to enable timer {timer_unit}: {e}")

    ok(f"MGLRU memory management active: one-shot boot flush at {BOOT_FLUSH_DELAY}, periodic skimmer starting at {timer_int} (recurring every {timer_int}).")

def restore_system() -> None:
    info("Stopping and disabling systemd timers and services...")
    all_units = (
        "dusky_boot_zram_flush.timer",
        "dusky_boot_zram_flush.service",
        "dusky_pro_active_zram_swap.timer",
        "dusky_pro_active_zram_swap.service",
    )
    for unit in all_units:
        try:
            subprocess.run(["systemctl", "disable", "--now", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    files_to_remove = [
        Path("/usr/local/bin/dusky_pro_active_zram_swap"),
        Path("/usr/local/bin/dusky_pro_active_zram_gate"),
        Path("/etc/systemd/system/dusky_boot_zram_flush.service"),
        Path("/etc/systemd/system/dusky_boot_zram_flush.timer"),
        Path("/etc/systemd/system/dusky_pro_active_zram_swap.service"),
        Path("/etc/systemd/system/dusky_pro_active_zram_swap.timer"),
        Path("/run/dusky/pro_active_zram_swap.state"),
        Path("/run/dusky/app_idle_tracker.json"),
        CONF_PATH,
    ]
    for f in files_to_remove:
        if f.exists():
            try:
                f.unlink()
                ok(f"Removed {f}")
            except Exception as e:
                warn(f"Failed to remove {f}: {e}")

    info("Reloading systemd daemon...")
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
    except Exception as e:
        warn(f"systemctl daemon-reload failed: {e}")

    ok("Restoration complete. Memory reclaimer and boot flush units uninstalled.")

# --- Entrypoint & Argument Handling ---
def main() -> None:
    if sys.version_info < (3, 14):
        die(f"Python 3.14+ required, running {sys.version.split()[0]}")

    parser = argparse.ArgumentParser(description="Lean Arch Linux MGLRU Proactive Slice Memory Skimmer & Boot Flush")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run", action="store_true", help="Directly trigger the periodic MGLRU slice memory reclaim task")
    group.add_argument("--boot-flush", action="store_true", help="Directly trigger the one-time baseline boot memory flush")
    group.add_argument("--restore", action="store_true", help="Remove reclaimer binaries, systemd units, timers, and state")
    group.add_argument("--status", action="store_true", help="Display memory, zRAM swap, PSI pressure, and reclaimer status")
    parser.add_argument("--force", action="store_true", help="Bypass RAM threshold, tier check, and PSI checks")
    parser.add_argument("--timer-interval", type=str, default="", help="Override periodic timer interval (e.g. 5min, 10min)")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")

    args = parser.parse_args()

    if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        C.strip()

    if args.status:
        show_status()
        return

    escalate_privileges()

    if args.restore:
        restore_system()
    elif args.boot_flush:
        perform_reclaim(force=args.force, boot_flush=True)
    elif args.run:
        perform_reclaim(force=args.force, boot_flush=False)
    else:
        deploy_systemd_units(timer_interval_arg=args.timer_interval)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.BOLD}{C.RED}aborted — operation cancelled by user.{C.RST}")
        sys.exit(130)

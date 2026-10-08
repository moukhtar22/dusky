#!/usr/bin/env python3

"""
Dusky Disk Real-Time System I/O Monitor (Hyper-Sleek Cutting-Edge Edition)
Zero-stutter background polling, solid sleek borders, Matugen theme integration,
strictly aligned dense NVMe/SATA SMART diagnostics, circular keyboard navigation,
and automated sudo keep-alive.
"""

from __future__ import annotations

import atexit
import concurrent.futures
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

# ============================================================================
# 1. AGGRESSIVE DEPENDENCY MANAGEMENT & AUTHENTICATION
# ============================================================================
def ensure_dependencies() -> None:
    """Checks for required Python libraries and system binaries, installing natively via pacman if needed."""
    missing: list[str] = []
    try:
        import textual  # noqa: F401
    except ImportError:
        missing.append("python-textual")
    try:
        import rich  # noqa: F401
    except ImportError:
        missing.append("python-rich")

    if shutil.which("lsblk") is None:
        missing.append("util-linux")
    if shutil.which("nvme") is None:
        missing.append("nvme-cli")
    if shutil.which("smartctl") is None:
        missing.append("smartmontools")

    if missing:
        print(f"\n[!] Missing absolute dependencies: {', '.join(missing)}")
        print("[*] Escalating privileges to install via pacman (requires sudo password)...\n")
        cmd = ["sudo", "pacman", "-S", "--needed", "--noconfirm", *missing]
        try:
            subprocess.run(cmd, check=True)
            print("\n[*] Dependencies installed successfully. Initializing engine...\n")
            os.execv(sys.executable, [sys.executable, *sys.argv])
        except subprocess.CalledProcessError as e:
            print(f"\n[!] Critical Failure: Dependency installation aborted. (Code: {e.returncode})", file=sys.stderr)
            sys.exit(1)


_sudo_keepalive_stop = threading.Event()
atexit.register(_sudo_keepalive_stop.set)

def _sudo_keepalive_worker() -> None:
    """Refreshes the sudo timestamp in the background so telemetry continues uninterrupted."""
    while not _sudo_keepalive_stop.is_set():
        try:
            subprocess.run(["sudo", "-n", "-v"], capture_output=True, timeout=5)
        except Exception:
            pass
        _sudo_keepalive_stop.wait(45.0)


def _run_privileged(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    """Executes a command with root privileges, bypassing sudo overhead when already root."""
    prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
    return subprocess.run([*prefix, *cmd], capture_output=True, text=True, timeout=timeout)


def ensure_smart_access() -> None:
    """Prompts for sudo upfront and spawns a background refresher for non-expiring telemetry."""
    if os.geteuid() != 0:
        if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
            if not sys.stdin.isatty():
                return
            print("\n[!] Advanced NVMe SMART diagnostics require administrative privileges.")
            print("[*] Please authenticate to enable full telemetry (Temp, TBW, Health, etc):\n")
            try:
                subprocess.run(["sudo", "-v"], check=True)
                print("\n[*] Diagnostics unlocked. Engaging monitors...\n")
            except subprocess.CalledProcessError:
                print("\n[!] Warning: Authentication skipped. SMART metrics will show N/A.")
                time.sleep(1.5)
            except KeyboardInterrupt:
                print("\n[!] Authentication cancelled. Exiting.")
                sys.exit(0)

        # Spawn daemon thread to keep sudo credentials alive (only required for non-root users)
        t = threading.Thread(target=_sudo_keepalive_worker, daemon=True, name="SudoKeepAlive")
        t.start()


from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static

# ============================================================================
# 2. DYNAMIC MATUGEN THEME COMPILER
# ============================================================================
def load_theme() -> dict[str, str]:
    """Loads the user's Matugen-generated theme with bulletproof fallback mechanisms."""
    path = Path.home() / ".config" / "matugen" / "generated" / "dusky_tui.json"
    defaults: dict[str, str] = {
        "bg": "#0e1416",
        "fg": "#dee3e5",
        "accent": "#82d3e2",
        "error": "#ffb4ab",
        "warning": "#b1cbd0",
        "success": "#bbc5ea",
        "muted": "#3f484a",
    }
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                user_theme = json.load(f)
                return {k: str(user_theme.get(k, defaults[k])) for k in defaults}
        except Exception:
            return defaults
    return defaults


THEME = load_theme()
BG = THEME["bg"]
FG = THEME["fg"]
ACCENT = THEME["accent"]
ERROR = THEME["error"]
WARNING = THEME["warning"]
SUCCESS = THEME["success"]
MUTED = THEME["muted"]

# High-contrast readable palette for diagnostic labels and sub-grids
LABEL_COL = "#8fa7ab"
DIVIDER_COL = "#486368"
SPARK_BASE_COL = "#223c40"
TEMP_COL = "#fcd34d"


# ============================================================================
# 3. CORE SYSTEM METRICS & FORMATTING ENGINE
# ============================================================================
def format_bytes(bytes_val: float) -> str:
    """Formats bytes into human-readable B, KB, MB, GB, TB, or PB string."""
    if bytes_val < 1024:
        return f"{bytes_val:.0f} B"
    if bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    if bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} MB"
    if bytes_val < 1024 * 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024 * 1024):.1f} GB"
    if bytes_val < 1024 * 1024 * 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024 * 1024 * 1024):.2f} TB"
    return f"{bytes_val / (1024 * 1024 * 1024 * 1024 * 1024):.2f} PB"


def format_rate(rate_bytes_per_sec: float) -> str:
    """Formats transfer rate into human-readable B/s, KB/s, MB/s, or GB/s."""
    if rate_bytes_per_sec <= 0.0:
        return "0.00 MB/s"
    mb_s = rate_bytes_per_sec / (1024 * 1024)
    if mb_s >= 1000.0:
        return f"{mb_s / 1024.0:.2f} GB/s"
    if mb_s >= 100.0:
        return f"{mb_s:.1f} MB/s"
    if mb_s >= 1.0:
        return f"{mb_s:.2f} MB/s"
    if rate_bytes_per_sec >= 1024.0:
        return f"{rate_bytes_per_sec / 1024.0:.1f} KB/s"
    return f"{rate_bytes_per_sec:.0f} B/s"


def format_nvme_units(units: int | float | str) -> str:
    """Formats NVMe data units (1 unit = 1,000 * 512 bytes = 512 KB) into human-readable SI string matching nvme-cli."""
    if isinstance(units, str) and any(u in units for u in ("KB", "MB", "GB", "TB", "PB")):
        return units.strip()
    try:
        bytes_val = float(units) * 512_000.0
        if bytes_val < 1e6:
            return f"{bytes_val / 1e3:.1f} KB"
        if bytes_val < 1e9:
            return f"{bytes_val / 1e6:.1f} MB"
        if bytes_val < 1e12:
            return f"{bytes_val / 1e9:.1f} GB"
        if bytes_val < 1e15:
            return f"{bytes_val / 1e12:.2f} TB"
        return f"{bytes_val / 1e15:.2f} PB"
    except (ValueError, TypeError):
        return "N/A"


@dataclass(slots=True, frozen=True)
class BlockStats:
    timestamp: float
    read_ios: int
    read_sectors: int
    read_ticks: int
    write_ios: int
    write_sectors: int
    write_ticks: int
    in_flight: int
    io_ticks: int
    time_in_queue: int
    discard_ios: int = 0
    discard_sectors: int = 0
    discard_ticks: int = 0
    flush_ios: int = 0
    flush_ticks: int = 0


@dataclass(slots=True, frozen=True)
class SmartInfo:
    temp: str = "N/A"
    tbr: str = "N/A"
    tbw: str = "N/A"
    health: str = "N/A"
    power_cycles: str = "N/A"
    power_on_hours: str = "N/A"
    unsafe_shutdowns: str = "N/A"
    media_errors: str = "N/A"
    critical_warning: str = "N/A"
    therm_t1: str = "N/A"


class SysStatParser:
    @staticmethod
    def get_block_stats(device: str) -> BlockStats | None:
        path = Path(f"/sys/block/{device}/stat")
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                fields = f.read().split()
            if len(fields) < 11:
                return None
            return BlockStats(
                timestamp=time.perf_counter(),
                read_ios=int(fields[0]),
                read_sectors=int(fields[2]),
                read_ticks=int(fields[3]),
                write_ios=int(fields[4]),
                write_sectors=int(fields[6]),
                write_ticks=int(fields[7]),
                in_flight=int(fields[8]),
                io_ticks=int(fields[9]),
                time_in_queue=int(fields[10]),
                discard_ios=int(fields[11]) if len(fields) > 11 else 0,
                discard_sectors=int(fields[13]) if len(fields) > 13 else 0,
                discard_ticks=int(fields[14]) if len(fields) > 14 else 0,
                flush_ios=int(fields[15]) if len(fields) > 15 else 0,
                flush_ticks=int(fields[16]) if len(fields) > 16 else 0,
            )
        except (IndexError, ValueError, OSError):
            return None

    @staticmethod
    def _get_smartctl_data(device: str) -> SmartInfo:
        try:
            res = _run_privileged(["smartctl", "-j", "-a", f"/dev/{device}"], timeout=3.0)
            temp_str = "N/A"
            health_str = "N/A"
            p_cycles = "N/A"
            p_hours = "N/A"
            realloc = "N/A"
            tbr = "N/A"
            tbw = "N/A"
            u_shut = "N/A"
            crit_warn = "N/A"

            if res.stdout:
                try:
                    data = json.loads(res.stdout)

                    # NVMe SMART log block from smartctl (handles NVMe devices seamlessly)
                    nvme_log = data.get("nvme_smart_health_information_log", {})
                    if nvme_log:
                        pct_used = nvme_log.get("percentage_used")
                        if pct_used is not None:
                            try:
                                health_str = f"{max(0, 100 - int(pct_used))}%"
                            except (ValueError, TypeError):
                                pass
                        dur = nvme_log.get("data_units_read")
                        if dur is not None:
                            tbr = format_nvme_units(dur)
                        duw = nvme_log.get("data_units_written")
                        if duw is not None:
                            tbw = format_nvme_units(duw)
                        p_cycles = str(nvme_log.get("power_cycles", "N/A"))
                        p_hours = str(nvme_log.get("power_on_hours", "N/A"))
                        u_shut = str(nvme_log.get("unsafe_shutdowns", "N/A"))
                        realloc = str(nvme_log.get("media_errors", "N/A"))
                        cw = nvme_log.get("critical_warning")
                        if cw is not None:
                            crit_warn = str(cw)

                        # Multi-sensor temperatures support from smartctl NVMe log
                        ts_list = nvme_log.get("temperature_sensors", [])
                        if ts_list and isinstance(ts_list, list):
                            raw_temps = [str(t) for t in ts_list if isinstance(t, (int, float)) and t > 0]
                            if not raw_temps:
                                t_nvme = nvme_log.get("temperature")
                                temp_str = f"{t_nvme}°C" if t_nvme is not None else "N/A"
                            elif len(raw_temps) <= 2:
                                temp_str = " │ ".join(f"{n}°C" for n in raw_temps)
                            else:
                                temp_str = f"{' │ '.join(raw_temps)}°C"
                        else:
                            t_nvme = nvme_log.get("temperature")
                            if t_nvme is not None:
                                temp_str = f"{t_nvme}°C"

                    # Parse ATA attributes for SATA SSDs & HDDs
                    ata_attrs = data.get("ata_smart_attributes", {}).get("table", [])
                    if ata_attrs:
                        bad_sectors = 0
                        found_bad = False
                        for attr in ata_attrs:
                            attr_name = attr.get("name", "")
                            raw_val = attr.get("raw", {}).get("value")
                            if raw_val is not None:
                                # TBW / TBR for SATA SSDs
                                if tbw == "N/A" and attr_name in ("Total_LBAs_Written", "Lifetime_Writes_GiB", "Host_Writes_32MiB"):
                                    if attr_name == "Total_LBAs_Written":
                                        tbw = format_bytes(raw_val * 512)
                                    elif attr_name == "Lifetime_Writes_GiB":
                                        tbw = format_bytes(raw_val * 1024 * 1024 * 1024)
                                    elif attr_name == "Host_Writes_32MiB":
                                        tbw = format_bytes(raw_val * 32 * 1024 * 1024)
                                elif tbr == "N/A" and attr_name in ("Total_LBAs_Read", "Lifetime_Reads_GiB", "Host_Reads_32MiB"):
                                    if attr_name == "Total_LBAs_Read":
                                        tbr = format_bytes(raw_val * 512)
                                    elif attr_name == "Lifetime_Reads_GiB":
                                        tbr = format_bytes(raw_val * 1024 * 1024 * 1024)
                                    elif attr_name == "Host_Reads_32MiB":
                                        tbr = format_bytes(raw_val * 32 * 1024 * 1024)
                                # SATA SSD Wear / Health Percentage
                                elif health_str == "N/A" and attr_name in ("SSD_Life_Left", "Percent_Lifetime_Remain", "Wear_Leveling_Count"):
                                    health_str = f"{raw_val}%"
                                # Unsafe shutdowns for SATA
                                elif u_shut == "N/A" and attr_name in ("Power-Off_Retract_Count", "Unsafe_Shutdown_Count"):
                                    u_shut = str(raw_val)
                                # Media errors & uncorrectable sectors
                                if attr_name in ("Reallocated_Sector_Ct", "Current_Pending_Sector", "Offline_Uncorrectable"):
                                    bad_sectors += int(raw_val)
                                    found_bad = True
                        if realloc == "N/A" and found_bad:
                            realloc = str(bad_sectors)

                    if temp_str == "N/A":
                        t_curr = data.get("temperature", {}).get("current")
                        if t_curr is not None:
                            temp_str = f"{t_curr}°C"
                        else:
                            for attr in ata_attrs:
                                if attr.get("name") in ("Temperature_Celsius", "Airflow_Temperature_Cel", "Temperature"):
                                    raw_v = attr.get("raw", {}).get("value")
                                    if raw_v is not None:
                                        temp_str = f"{raw_v}°C"
                                        break

                    if health_str == "N/A":
                        smart_passed = data.get("smart_status", {}).get("passed")
                        health_str = "PASSED" if smart_passed is True else ("FAILED" if smart_passed is False else "N/A")

                    if p_cycles == "N/A":
                        p_cycles = str(data.get("power_cycle_count", "N/A"))
                    if p_hours == "N/A":
                        p_hours = str(data.get("power_on_time", {}).get("hours", "N/A"))

                except json.JSONDecodeError:
                    pass

            if temp_str == "N/A":
                # Fallback to plain smartctl -A /dev/{device} for legacy USB SAT bridges
                try:
                    res_a = _run_privileged(["smartctl", "-A", f"/dev/{device}"], timeout=2.0)
                    for line in res_a.stdout.splitlines():
                        if "Temperature_Celsius" in line or "Airflow_Temperature" in line:
                            parts = line.split()
                            if len(parts) >= 10 and parts[9].isdigit():
                                temp_str = f"{parts[9]}°C"
                                break
                except Exception:
                    pass

            return SmartInfo(
                temp=temp_str,
                tbr=tbr,
                tbw=tbw,
                health=health_str,
                power_cycles=p_cycles,
                power_on_hours=p_hours,
                unsafe_shutdowns=u_shut,
                media_errors=realloc,
                critical_warning=crit_warn,
            )
        except Exception:
            pass
        return SmartInfo()

    @staticmethod
    def get_smart_data(device: str) -> SmartInfo:
        # Instant return for non-SMART block devices (ZRAM, loopbacks, ramdisks, devmapper)
        if device.startswith(("zram", "loop", "ram", "dm", "sr", "fd", "nbd")):
            return SmartInfo()

        # Parse NVMe controller telemetry via modern nvme-cli 3.0
        match = re.match(r"(nvme\d+)", device)
        if match:
            ctrl = match.group(1)
            dev_target = f"/dev/{ctrl}" if Path(f"/dev/{ctrl}").exists() else f"/dev/{device}"
            try:
                # Cutting-edge nvme-cli 3.0 native architecture:
                # 1. 'nvme log smart': canonical 3.0 subcommand replacing deprecated 'smart-log'
                # 2. '-o json' & '--output-format-version=2': script-friendly standardized JSON schema
                # 3. '--timeout=1500': hardware IOCTL timeout preventing D-state kernel hangs
                # 4. '--no-retries': disables retry loops on transient errors for zero-stutter polling
                cmd = [
                    "nvme", "log", "smart", dev_target,
                    "-o", "json",
                    "--output-format-version=2",
                    "--timeout=1500",
                    "--no-retries",
                ]
                res = _run_privileged(cmd, timeout=2.0)
                if res.returncode == 0 and res.stdout:
                    stdout_str = res.stdout.strip()
                    if "{" in stdout_str and "}" in stdout_str:
                        json_str = stdout_str[stdout_str.find("{"):stdout_str.rfind("}") + 1]
                        data = json.loads(json_str)

                        # Temperature (Kelvin in nvme-cli 3.0 JSON schema, converted to Celsius)
                        raw_temps: list[str] = []
                        temp_raw = data.get("temperature")
                        if temp_raw is not None:
                            t_c = temp_raw - 273 if temp_raw > 200 else temp_raw
                            raw_temps.append(str(t_c))

                        for i in range(1, 9):
                            ts_raw = data.get(f"temperature_sensor_{i}")
                            if ts_raw is not None and ts_raw > 0:
                                ts_c = ts_raw - 273 if ts_raw > 200 else ts_raw
                                s_str = str(ts_c)
                                if s_str not in raw_temps and len(raw_temps) < 3:
                                    raw_temps.append(s_str)

                        if not raw_temps:
                            temp_str = "N/A"
                        elif len(raw_temps) <= 2:
                            temp_str = " │ ".join(f"{n}°C" for n in raw_temps)
                        else:
                            temp_str = f"{' │ '.join(raw_temps)}°C"

                        # Drive Health (Percentage Used)
                        health = "N/A"
                        pct_used = data.get("percent_used", data.get("percentage_used"))
                        if pct_used is not None:
                            try:
                                health = f"{max(0, 100 - int(pct_used))}%"
                            except (ValueError, TypeError):
                                pass

                        # TBR / TBW (Data Units Read/Written scaled to SI standard)
                        dur = data.get("data_units_read")
                        tbr = format_nvme_units(dur) if dur is not None else "N/A"
                        duw = data.get("data_units_written")
                        tbw = format_nvme_units(duw) if duw is not None else "N/A"

                        # Hardware Lifecycle & Media Reliability Counters
                        power_cycles = str(data.get("power_cycles", "N/A"))
                        power_on_hours = str(data.get("power_on_hours", "N/A"))
                        unsafe_shutdowns = str(data.get("unsafe_shutdowns", "N/A"))
                        media_errors = str(data.get("media_errors", "N/A"))

                        # Critical Warning (numeric or structured mask)
                        cw = data.get("critical_warning", "N/A")
                        if isinstance(cw, dict):
                            cw = cw.get("value", "N/A")
                        critical_warning = str(cw) if cw is not None else "N/A"

                        # Thermal Throttling T1 Time
                        t1 = data.get("thm_temp1_total_time")
                        therm_t1 = f"{t1}s" if t1 is not None and str(t1).isdigit() else "N/A"

                        return SmartInfo(
                            temp=temp_str,
                            tbr=tbr,
                            tbw=tbw,
                            health=health,
                            power_cycles=power_cycles,
                            power_on_hours=power_on_hours,
                            unsafe_shutdowns=unsafe_shutdowns,
                            media_errors=media_errors,
                            critical_warning=critical_warning,
                            therm_t1=therm_t1,
                        )
            except Exception:
                pass

        # Fallback for SATA SSD, HDD, USB drives (or when nvme CLI is not authorized)
        return SysStatParser._get_smartctl_data(device)

    @staticmethod
    def get_ram_buffers() -> tuple[float, float]:
        dirty = writeback = 0.0
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("Dirty:"):
                        dirty = float(line.split()[1]) / 1024.0
                    elif line.startswith("Writeback:"):
                        writeback = float(line.split()[1]) / 1024.0
        except (OSError, IndexError, ValueError):
            pass
        return dirty, writeback

    @staticmethod
    def is_zram_active(dev_name: str) -> bool:
        """Verifies that a ZRAM device is actively engaged in swap or mounted to a filesystem."""
        try:
            sz_p = Path(f"/sys/block/{dev_name}/size")
            if not sz_p.exists() or int(sz_p.read_text().strip()) == 0:
                return False
            if Path("/proc/swaps").exists():
                swaps = Path("/proc/swaps").read_text()
                if f"/dev/{dev_name}" in swaps or dev_name in swaps:
                    return True
            if Path("/proc/mounts").exists():
                with open("/proc/mounts", "r", encoding="utf-8") as f:
                    for line in f:
                        src = line.split()[0] if line.split() else ""
                        if src == f"/dev/{dev_name}" or src.endswith(f"/{dev_name}"):
                            return True
        except Exception:
            pass
        return False

    @staticmethod
    def get_basic_metadata() -> dict[str, dict]:
        """Instantly (< 5ms) extracts block device topology from lsblk for instantaneous frame-0 UI rendering."""
        try:
            res = subprocess.run(
                ["lsblk", "-J", "-d", "-o", "NAME,SIZE,TYPE,MODEL,ROTA,TRAN"],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(res.stdout)
            results = {}
            for d in data.get("blockdevices", []):
                name = d.get("name", "")
                if not name or name.startswith(("loop", "sr", "ram", "dm", "fd", "nbd")):
                    continue
                if name.startswith("zram") and not SysStatParser.is_zram_active(name):
                    continue
                model = d.get("model")
                clean_model = str(model).strip() if model else ("Compressed RAM" if name.startswith("zram") else "N/A")
                rota_val = d.get("rota")
                is_hdd = str(rota_val).strip() in ("1", "true", "True") if rota_val is not None else False
                tran = d.get("tran")
                dtype = tran.upper() if tran else ("ZRAM" if name.startswith("zram") else ("NVME" if name.startswith("nvme") else d.get("type", "DISK").upper().strip()))
                results[name] = {
                    "size": d.get("size", "?").strip(),
                    "type": dtype,
                    "model": clean_model,
                    "rota": is_hdd,
                    "smart": SmartInfo(),
                }
            return results
        except Exception:
            return {}

    @staticmethod
    def get_device_metadata() -> dict[str, dict]:
        try:
            res = subprocess.run(
                ["lsblk", "-J", "-d", "-o", "NAME,SIZE,TYPE,MODEL,ROTA,TRAN"],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(res.stdout)
            devices_raw = []
            for d in data.get("blockdevices", []):
                name = d.get("name", "")
                if not name or name.startswith(("loop", "sr", "ram", "dm", "fd", "nbd")):
                    continue
                if name.startswith("zram") and not SysStatParser.is_zram_active(name):
                    continue
                devices_raw.append(d)

            def fetch_single_meta(dev: dict) -> tuple[str, dict]:
                name = dev["name"]
                try:
                    model = dev.get("model")
                    clean_model = str(model).strip() if model else ("Compressed RAM" if name.startswith("zram") else "N/A")
                    rota_val = dev.get("rota")
                    is_hdd = str(rota_val).strip() in ("1", "true", "True") if rota_val is not None else False
                    tran = dev.get("tran")
                    dtype = tran.upper() if tran else ("ZRAM" if name.startswith("zram") else ("NVME" if name.startswith("nvme") else dev.get("type", "DISK").upper().strip()))

                    smart = SysStatParser.get_smart_data(name)
                    return name, {
                        "size": dev.get("size", "?").strip(),
                        "type": dtype,
                        "model": clean_model,
                        "rota": is_hdd,
                        "smart": smart,
                    }
                except Exception:
                    return name, {
                        "size": dev.get("size", "?").strip(),
                        "type": "DISK",
                        "model": "N/A",
                        "rota": False,
                        "smart": SmartInfo(),
                    }

            # Parallel query across all connected block devices
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                results = dict(executor.map(fetch_single_meta, devices_raw))

            return results
        except Exception:
            return {}


# ============================================================================
# 4. TEXTUAL WIDGETS & UI
# ============================================================================

class DriveWidget(Static, can_focus=True):
    DEFAULT_CSS = f"""
    DriveWidget {{
        border: solid {MUTED};
        background: {BG};
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
        transition: border 150ms;
    }}
    DriveWidget:focus {{
        border: solid {ACCENT};
        background: {BG};
    }}
    """

    def __init__(self, dev_name: str, **kwargs):
        super().__init__(**kwargs)
        self.dev_name = dev_name
        self.history_read: deque[float] = deque([0.0] * 16, maxlen=16)
        self.history_write: deque[float] = deque([0.0] * 16, maxlen=16)
        self.peak_read: float = 10.0
        self.peak_write: float = 10.0
        self.prev_stats: BlockStats | None = None

    def generate_sparkline(self, data: deque[float], current_peak: float, width: int = 16, color_hex: str = ACCENT) -> tuple[Text, float]:
        """Returns a hard-cropped rich Text object with stabilized peak scaling and zero ellipsis truncation."""
        ticks = " ▂▃▄▅▆▇█"
        valid_data = list(data)

        if not valid_data:
            line = f"[{SPARK_BASE_COL}]" + " " * width + f"[/{SPARK_BASE_COL}]"
            return Text.from_markup(line, overflow="crop"), 10.0

        max_in_window = max(valid_data)
        # Stabilize peak with smooth exponential decay (avoids sudden jumping when bursts expire)
        new_peak = max(max_in_window, current_peak * 0.90, 10.0)

        line = ""
        for v in valid_data[-width:]:
            if v <= 0.01:
                line += f"[{SPARK_BASE_COL}] [/{SPARK_BASE_COL}]"
            else:
                # Dynamic soft power curve (v/new_peak)**0.6 enables clear distinction across wide MB/s to GB/s bandwidths
                norm = min(max(v / new_peak, 0.0), 1.0)
                idx = int((norm ** 0.6) * (len(ticks) - 1))
                idx = max(0, min(idx, len(ticks) - 1))
                line += f"[{color_hex}]{ticks[idx]}[/{color_hex}]"
        return Text.from_markup(line, overflow="crop"), new_peak

    def tick_update(self, curr: BlockStats, meta_info: dict) -> None:
        size = meta_info.get("size", "?")
        dtype = meta_info.get("type", "DISK")
        model = meta_info.get("model", "N/A")

        is_hdd = meta_info.get("rota", False)
        is_zram = self.dev_name.startswith("zram")
        is_compact = is_hdd or is_zram

        smart: SmartInfo = meta_info.get("smart", SmartInfo())

        self.border_title = (
            f"[bold {FG}]/dev/{self.dev_name}[/]  [{MUTED}]│[/]  "
            f"[{ACCENT}]{size}[/]  [{MUTED}]│[/]  [{SUCCESS}]{dtype}[/]  [{MUTED}]│[/]  [{WARNING}]{model}[/]"
        )

        if not self.prev_stats:
            self.prev_stats = curr
            r_mb_s = w_mb_s = r_iops = w_iops = await_ms = util_pct = 0.0
        else:
            prev = self.prev_stats
            dt = curr.timestamp - prev.timestamp
            if dt > 0:
                r_mb_s = max(0.0, ((curr.read_sectors - prev.read_sectors) * 512) / dt / 1048576)
                w_mb_s = max(0.0, ((curr.write_sectors - prev.write_sectors) * 512) / dt / 1048576)
                r_iops = max(0.0, (curr.read_ios - prev.read_ios) / dt)
                w_iops = max(0.0, (curr.write_ios - prev.write_ios) / dt)

                total_ios_delta = (
                    max(0, curr.read_ios - prev.read_ios)
                    + max(0, curr.write_ios - prev.write_ios)
                    + max(0, curr.discard_ios - prev.discard_ios)
                    + max(0, curr.flush_ios - prev.flush_ios)
                )
                total_ticks_delta = (
                    max(0, curr.read_ticks - prev.read_ticks)
                    + max(0, curr.write_ticks - prev.write_ticks)
                    + max(0, curr.discard_ticks - prev.discard_ticks)
                    + max(0, curr.flush_ticks - prev.flush_ticks)
                )

                util_pct = max(0.0, min(((curr.io_ticks - prev.io_ticks) / 1000.0) / dt * 100.0, 100.0))
                await_ms = (total_ticks_delta / total_ios_delta) if total_ios_delta > 0 else 0.0

                self.history_read.append(r_mb_s)
                self.history_write.append(w_mb_s)
                self.prev_stats = curr
            else:
                r_mb_s = w_mb_s = r_iops = w_iops = await_ms = util_pct = 0.0

        read_total_str = format_bytes(curr.read_sectors * 512)
        write_total_str = format_bytes(curr.write_sectors * 512)

        # ====================================================================
        # ROCK-SOLID JITTER-FREE FLUID GRID (Fixed Column Metric Anchoring)
        # ====================================================================
        table = Table.grid(padding=(0, 1), expand=True)

        table.add_column("C1_L", justify="left", no_wrap=True, width=10)
        table.add_column("C1_V", justify="left", no_wrap=True, width=10)
        table.add_column("F1", ratio=1)
        table.add_column("C2", justify="left", no_wrap=True, width=25)
        table.add_column("F2", ratio=1)
        table.add_column("C3", justify="left", no_wrap=True, width=16)
        table.add_column("F3", ratio=1)
        table.add_column("C4", justify="left", no_wrap=True, width=17)

        r_spark, self.peak_read = self.generate_sparkline(self.history_read, self.peak_read, width=16, color_hex=SUCCESS)
        w_spark, self.peak_write = self.generate_sparkline(self.history_write, self.peak_write, width=16, color_hex=ACCENT)

        # Diagnostics Color Evaluation (Eliminating False Alarms on Healthy Drives & N/A)
        m_str = str(smart.media_errors).strip()
        if m_str in ("N/A", "?", ""):
            err_col = MUTED
        elif m_str in ("0", "0x0", "0x00") or smart.media_errors == 0:
            err_col = SUCCESS
        else:
            err_col = ERROR

        c_str = str(smart.critical_warning).strip()
        if c_str in ("N/A", "?", ""):
            crit_col = MUTED
        elif c_str in ("0", "0x0", "0x00") or smart.critical_warning == 0:
            crit_col = SUCCESS
        else:
            crit_col = ERROR

        h_str = str(smart.health).strip()
        if h_str == "FAILED":
            health_col = ERROR
        elif h_str in ("N/A", "?", ""):
            health_col = MUTED
        elif h_str.endswith("%"):
            try:
                h_val = int(h_str.rstrip("%"))
                health_col = ERROR if h_val < 30 else (WARNING if h_val < 70 else ACCENT)
            except ValueError:
                health_col = ACCENT
        else:
            health_col = SUCCESS

        u_str = str(smart.unsafe_shutdowns).strip()
        if u_str in ("N/A", "?", ""):
            pwr_cut_col = MUTED
        elif u_str in ("0", "0x0") or smart.unsafe_shutdowns == 0:
            pwr_cut_col = SUCCESS
        else:
            pwr_cut_col = WARNING

        t1_str = str(smart.therm_t1).strip()
        if t1_str in ("N/A", "?", ""):
            t1_col = MUTED
        elif t1_str in ("0s", "0"):
            t1_col = SUCCESS
        else:
            t1_col = WARNING

        util_col = ERROR if util_pct >= 85.0 else (WARNING if util_pct >= 50.0 else FG)
        lat_col = ERROR if await_ms >= 50.0 else (WARNING if await_ms >= 15.0 else FG)

        r_spd = format_rate(r_mb_s * 1048576)
        w_spd = format_rate(w_mb_s * 1048576)
        r_iops_str = f"{r_iops:.1f} IOPS"
        w_iops_str = f"{w_iops:.1f} IOPS"

        r_c4 = (
            f"[{SUCCESS}]{r_iops_str}[/] [{TEMP_COL}]{smart.temp}[/]"
            if (is_compact and smart.temp != "N/A")
            else f"[{SUCCESS}]{r_iops_str:>11}[/]"
        )
        w_c4 = (
            f"[{ACCENT}]{w_iops_str}[/] [bold {lat_col}]{await_ms:.2f} ms[/]"
            if is_compact
            else f"[{ACCENT}]{w_iops_str:>11}[/]"
        )

        # ROW 1 (Read Activity)
        table.add_row(
            f"[{WARNING}]Read:[/]",
            f"[bold {SUCCESS}]{read_total_str}[/]",
            "",
            f"[bold {SUCCESS}]READ [/] {r_spark}",
            "",
            f"[bold {FG}]{r_spd:>10}[/]",
            "",
            r_c4,
        )

        # ROW 2 (Write Activity)
        table.add_row(
            f"[{WARNING}]Write:[/]",
            f"[bold {ACCENT}]{write_total_str}[/]",
            "",
            f"[bold {ACCENT}]WRITE[/] {w_spark}",
            "",
            f"[bold {FG}]{w_spd:>10}[/]",
            "",
            w_c4,
        )

        if not is_compact:
            # ROW 3 (Utilization / Critical / Power Cycles)
            table.add_row(
                f"[{WARNING}]Latency:[/]",
                f"[bold {lat_col}]{await_ms:.2f} ms[/]",
                "",
                f"[{LABEL_COL}]UTIL    [{DIVIDER_COL}]│[/][/] [bold {util_col}]{util_pct:>5.1f}%[/]",
                "",
                f"[{LABEL_COL}]CRITICAL [{DIVIDER_COL}]│[/][/] [bold {crit_col}]{smart.critical_warning:>4}[/]",
                "",
                f"[{LABEL_COL}]PWR CYC [{DIVIDER_COL}]│[/][/] [bold {FG}]{smart.power_cycles:>6}[/]",
            )

            # ROW 4 (Health / Errors / Power Hours)
            table.add_row(
                f"[{SUCCESS}]Total Rd:[/]",
                f"[bold {SUCCESS}]{smart.tbr}[/]",
                "",
                f"[{LABEL_COL}]HEALTH  [{DIVIDER_COL}]│[/][/] [bold {health_col}]{smart.health:>5}[/]",
                "",
                f"[{LABEL_COL}]ERRORS   [{DIVIDER_COL}]│[/][/] [bold {err_col}]{smart.media_errors:>4}[/]",
                "",
                f"[{LABEL_COL}]PWR HRS [{DIVIDER_COL}]│[/][/] [bold {FG}]{smart.power_on_hours:>6}[/]",
            )

            # ROW 5 (Temperature / Thermal Throttle / Power Cuts)
            table.add_row(
                f"[{ACCENT}]Total Wr:[/]",
                f"[bold {ACCENT}]{smart.tbw}[/]",
                "",
                f"[{LABEL_COL}]TEMP    [{DIVIDER_COL}]│[/][/] [bold {TEMP_COL}]{smart.temp:>5}[/]",
                "",
                f"[{LABEL_COL}]T1 TIME  [{DIVIDER_COL}]│[/][/] [bold {t1_col}]{smart.therm_t1:>4}[/]",
                "",
                f"[{LABEL_COL}]PWR CUT [{DIVIDER_COL}]│[/][/] [bold {pwr_cut_col}]{smart.unsafe_shutdowns:>6}[/]",
            )

        self.update(table)


# ============================================================================
# 5. SHORTCUTS & HELP MODAL DIALOG
# ============================================================================
class ShortcutsScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "Dismiss", priority=True),
        Binding("f1", "dismiss", "Dismiss", priority=True),
        Binding("question_mark", "dismiss", "Dismiss", priority=True),
        Binding("q", "dismiss", "Dismiss", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="help_dialog"):
            yield Static("󰌌 Dusky Disk Monitor Shortcuts", id="modal-title")

            text = Text()
            text.append("Drive Navigation (Vim & Keys)\n", style=f"bold {ACCENT}")
            text.append("  j / Down       Select next drive\n")
            text.append("  k / Up         Select previous drive\n")
            text.append("  g / Home       Jump to first drive\n")
            text.append("  G / End        Jump to last drive\n\n")

            text.append("Card Reordering\n", style=f"bold {ACCENT}")
            text.append("  J / Shift+Down Move selected drive down\n")
            text.append("  K / Shift+Up   Move selected drive up\n\n")

            text.append("Actions & Controls\n", style=f"bold {ACCENT}")
            text.append("  s / Sync Btn   Flush dirty page cache to disks (sync)\n")
            text.append("  F1 / ?         Open / close this shortcuts modal\n")
            text.append("  q / Ctrl+C     Quit monitor\n")

            yield Static(text, id="modal-text")

            with Horizontal(id="modal_btn_container"):
                yield Button("Close [F1 / Esc]", id="btn_modal_close")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key in ("escape", "f1", "question_mark", "q", "enter", "space", "?") or event.character in ("?", "q"):
            self.dismiss(None)
            event.stop()

    @on(Button.Pressed, "#btn_modal_close")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)


class IOMonitorApp(App):
    """Dusky Disk I/O Monitor"""
    ENABLE_COMMAND_PALETTE = False

    CSS = f"""
    Screen {{
        background: {BG};
        layout: vertical;
    }}

    #ram_bar {{
        height: 1;
        background: {BG};
        color: {FG};
        padding: 0 1;
    }}

    Button#btn_help {{
        height: 1;
        min-width: 0;
        width: 8;
        border: none;
        background: {ACCENT};
        color: {BG};
        text-style: bold;
        padding: 0;
        margin: 0;
    }}

    Button#btn_help:hover, Button#btn_help:focus {{
        background: {SUCCESS};
        color: {BG};
    }}

    #ram_txt {{
        width: 1fr;
        height: 1;
        text-align: center;
    }}

    Button#btn_sync {{
        height: 1;
        min-width: 0;
        width: 8;
        border: none;
        background: {ACCENT};
        color: {BG};
        text-style: bold;
        padding: 0;
        margin: 0;
    }}

    Button#btn_sync:hover {{
        background: {SUCCESS};
        color: {BG};
    }}

    Button#btn_sync:focus {{
        background: {SUCCESS};
        color: {BG};
    }}

    Button#btn_sync.-syncing {{
        background: {WARNING};
        color: {BG};
    }}

    Button#btn_sync.-synced {{
        background: {SUCCESS};
        color: {BG};
    }}

    ShortcutsScreen {{
        align: center middle;
    }}

    #help_dialog {{
        width: 66;
        height: auto;
        max-height: 85%;
        background: {BG};
        border: heavy {ACCENT};
        padding: 1 2;
    }}

    #modal-title {{
        color: {ACCENT};
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }}

    #modal-text {{
        color: {FG};
        margin-bottom: 1;
    }}

    #modal_btn_container {{
        height: 1;
        align-horizontal: center;
    }}

    Button#btn_modal_close {{
        height: 1;
        width: auto;
        min-width: 0;
        border: none;
        background: {ACCENT};
        color: {BG};
        text-style: bold;
        padding: 0 1;
        margin: 0;
    }}

    Button#btn_modal_close:hover, Button#btn_modal_close:focus {{
        background: {SUCCESS};
        color: {BG};
    }}

    #main_scroll {{
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        scrollbar-size: 1 1; 
        scrollbar-background: {BG};
        scrollbar-color: {MUTED};
        scrollbar-color-hover: {ACCENT};
    }}
    """

    BINDINGS = [
        # Essential keyboard shortcuts
        Binding("f1", "help", "Help", priority=True),
        Binding("question_mark", "help", "Help", priority=True),
        Binding("j", "next_drive", "Select"),
        Binding("k", "prev_drive", "Prev Drive"),
        Binding("s", "sync", "Sync"),
        Binding("q", "quit", "Quit"),

        # Vim / Arrow / Navigation bindings
        Binding("down", "next_drive", "Next Drive", priority=True),
        Binding("up", "prev_drive", "Prev Drive", priority=True),
        Binding("J", "move_down", "Move Down"),
        Binding("K", "move_up", "Move Up"),
        Binding("shift+down", "move_down", "Move Down", priority=True),
        Binding("shift+up", "move_up", "Move Up", priority=True),
        Binding("g", "first_drive", "First Drive"),
        Binding("home", "first_drive", "First Drive", priority=True),
        Binding("G", "last_drive", "Last Drive"),
        Binding("end", "last_drive", "Last Drive", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, dict] = SysStatParser.get_basic_metadata()
        self.mounted_drives: set[str] = set()

    def compose(self) -> ComposeResult:
        self.title = "Dusky Disk"
        with Horizontal(id="ram_bar"):
            yield Button("󰌌 F1", id="btn_help")
            yield Static(id="ram_txt")
            yield Button("󰚰 Sync", id="btn_sync")
        yield VerticalScroll(id="main_scroll")

    def on_mount(self) -> None:
        self.refresh_metadata_worker()
        self.tick()
        self.set_interval(1.0, self.tick)
        self.set_interval(5.0, self.refresh_metadata_worker)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_sync":
            self.action_sync()
        elif event.button.id == "btn_help":
            self.action_help()

    def action_sync(self) -> None:
        if getattr(self, "_syncing", False):
            return
        self._syncing = True
        self.do_sync()

    @work(thread=True, exclusive=True)
    def do_sync(self) -> None:
        """Flushes unwritten dirty pages to disk asynchronously."""
        self.call_from_thread(self._set_sync_state, "syncing")
        try:
            os.sync()
        except Exception:
            pass
        self.call_from_thread(self._set_sync_state, "synced")
        time.sleep(1.8)
        self.call_from_thread(self._set_sync_state, "idle")

    def _set_sync_state(self, state: str) -> None:
        try:
            btn = self.query_one("#btn_sync", Button)
            if state == "syncing":
                btn.label = "󱑂 Syncing..."
                btn.disabled = True
                btn.add_class("-syncing")
                btn.remove_class("-synced")
            elif state == "synced":
                btn.label = "󰄬 Synced!"
                btn.disabled = False
                btn.remove_class("-syncing")
                btn.add_class("-synced")
            elif state == "idle":
                self._syncing = False
                btn.label = "󰚰 Sync"
                btn.disabled = False
                btn.remove_class("-syncing")
                btn.remove_class("-synced")
        except Exception:
            self._syncing = False

    @work(thread=True, exclusive=True)
    def refresh_metadata_worker(self) -> None:
        new_meta = SysStatParser.get_device_metadata()
        self.call_from_thread(self._update_meta, new_meta)

    def _update_meta(self, new_meta: dict[str, dict]) -> None:
        if new_meta:
            self.meta.update(new_meta)

    # ========================================================================
    # CIRCULAR NAVIGATION (Loops seamlessly top-to-bottom and bottom-to-top)
    # ========================================================================
    def action_next_drive(self) -> None:
        drives = list(self.query(DriveWidget))
        if not drives:
            return
        focused = self.focused
        if focused in drives:
            idx = drives.index(focused)
            next_idx = (idx + 1) % len(drives)
            target = drives[next_idx]
        else:
            target = drives[0]
        target.focus()
        target.scroll_visible()

    def action_prev_drive(self) -> None:
        drives = list(self.query(DriveWidget))
        if not drives:
            return
        focused = self.focused
        if focused in drives:
            idx = drives.index(focused)
            prev_idx = (idx - 1 + len(drives)) % len(drives)
            target = drives[prev_idx]
        else:
            target = drives[-1]
        target.focus()
        target.scroll_visible()

    def action_first_drive(self) -> None:
        drives = list(self.query(DriveWidget))
        if drives:
            drives[0].focus()
            drives[0].scroll_visible()

    def action_last_drive(self) -> None:
        drives = list(self.query(DriveWidget))
        if drives:
            drives[-1].focus()
            drives[-1].scroll_visible()

    # ========================================================================
    # CARD REORDERING (Move up/down with circular wrapping)
    # ========================================================================
    def action_move_down(self) -> None:
        focused = self.focused
        if isinstance(focused, DriveWidget):
            scroll = self.query_one("#main_scroll", VerticalScroll)
            children = [c for c in scroll.children if isinstance(c, DriveWidget)]
            if len(children) > 1:
                idx = children.index(focused)
                if idx < len(children) - 1:
                    scroll.move_child(focused, after=children[idx + 1])
                else:
                    scroll.move_child(focused, before=children[0])
                focused.scroll_visible()

    def action_move_up(self) -> None:
        focused = self.focused
        if isinstance(focused, DriveWidget):
            scroll = self.query_one("#main_scroll", VerticalScroll)
            children = [c for c in scroll.children if isinstance(c, DriveWidget)]
            if len(children) > 1:
                idx = children.index(focused)
                if idx > 0:
                    scroll.move_child(focused, before=children[idx - 1])
                else:
                    scroll.move_child(focused, after=children[-1])
                focused.scroll_visible()

    def action_help(self) -> None:
        """Toggles the shortcuts and help modal dialog."""
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss(None)
        else:
            self.push_screen(ShortcutsScreen())

    def tick(self) -> None:
        dirty, wb = SysStatParser.get_ram_buffers()
        wb_col = ERROR if wb > 50.0 else (WARNING if wb > 0.0 else SUCCESS)
        dirty_col = WARNING if dirty > 500.0 else ACCENT
        ram_txt = Text.from_markup(
            f"[{LABEL_COL}]Dirty:[/] [bold {dirty_col}]{dirty:.1f} MB[/]    "
            f"[bold {BG} on {SUCCESS}] Dusky Disk [/]    "
            f"[{LABEL_COL}]Writeback:[/] [bold {wb_col}]{wb:.1f} MB[/]"
        )
        try:
            self.query_one("#ram_txt", Static).update(ram_txt)
        except Exception:
            pass

        current_drives: list[str] = []
        try:
            for d in os.listdir("/sys/block"):
                if d.startswith(("loop", "sr", "ram", "dm", "fd", "nbd")):
                    continue
                if d.startswith("zram") and not SysStatParser.is_zram_active(d):
                    continue
                current_drives.append(d)
            current_drives.sort()
        except Exception:
            pass

        scroll_area = self.query_one("#main_scroll", VerticalScroll)
        is_initial = len(self.mounted_drives) == 0

        # Remove disconnected drives
        for dev in list(self.mounted_drives):
            if dev not in current_drives:
                try:
                    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", dev)
                    self.query_one(f"#drive_{clean_id}").remove()
                except Exception:
                    pass
                self.mounted_drives.remove(dev)

        # Mount new drives
        new_drives_added = False
        for dev in current_drives:
            if dev not in self.mounted_drives:
                clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", dev)
                widget = DriveWidget(id=f"drive_{clean_id}", dev_name=dev)
                scroll_area.mount(widget)
                self.mounted_drives.add(dev)
                new_drives_added = True

        if new_drives_added and not is_initial:
            self.refresh_metadata_worker()

        # Initial focus on first drive widget
        if is_initial and current_drives:
            def focus_first() -> None:
                widgets = list(self.query(DriveWidget))
                if widgets:
                    widgets[0].focus()
            self.call_later(focus_first)

        # Update telemetry data across all active drive widgets
        for widget in self.query(DriveWidget):
            curr = SysStatParser.get_block_stats(widget.dev_name)
            if curr:
                meta_info = self.meta.get(widget.dev_name, {})
                widget.tick_update(curr, meta_info)


if __name__ == "__main__":
    ensure_dependencies()
    ensure_smart_access()
    app = IOMonitorApp()
    app.run()

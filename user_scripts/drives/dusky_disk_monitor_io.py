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

    # Spawn daemon thread to keep sudo credentials alive
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
    """Formats bytes into human-readable KB, MB, GB, or TB string."""
    if bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    if bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} MB"
    if bytes_val < 1024 * 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024 * 1024):.1f} GB"
    return f"{bytes_val / (1024 * 1024 * 1024 * 1024):.2f} TB"


def format_rate(rate_bytes_per_sec: float) -> str:
    """Formats transfer rate into human-readable MB/s or GB/s."""
    mb_s = rate_bytes_per_sec / (1024 * 1024)
    if mb_s >= 1000.0:
        return f"{mb_s / 1024.0:.2f} GB/s"
    if mb_s >= 100.0:
        return f"{mb_s:.1f} MB/s"
    if mb_s >= 0.01:
        return f"{mb_s:.2f} MB/s"
    return "0.00 MB/s"


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
            cmd = ["sudo", "-n", "smartctl", "-j", "-a", f"/dev/{device}"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            temp_str = "N/A"
            health_str = "N/A"
            p_cycles = "N/A"
            p_hours = "N/A"
            realloc = "N/A"

            if res.stdout:
                try:
                    data = json.loads(res.stdout)
                    t_curr = data.get("temperature", {}).get("current")
                    if t_curr is not None:
                        temp_str = f"{t_curr}°C"
                    else:
                        for attr in data.get("ata_smart_attributes", {}).get("table", []):
                            if attr.get("name") in ("Temperature_Celsius", "Airflow_Temperature_Cel", "Temperature"):
                                raw_v = attr.get("raw", {}).get("value")
                                if raw_v is not None:
                                    temp_str = f"{raw_v}°C"
                                    break

                    smart_passed = data.get("smart_status", {}).get("passed")
                    health_str = "PASSED" if smart_passed is True else ("FAILED" if smart_passed is False else "N/A")
                    p_cycles = str(data.get("power_cycle_count", "N/A"))
                    p_hours = str(data.get("power_on_time", {}).get("hours", "N/A"))

                    for attr in data.get("ata_smart_attributes", {}).get("table", []):
                        if attr.get("name") in ("Reallocated_Sector_Ct", "Reallocated_Event_Count"):
                            realloc = str(attr.get("raw", {}).get("value", attr.get("raw", {}).get("string", "N/A")))
                            break
                except json.JSONDecodeError:
                    pass

            if temp_str == "N/A":
                # Fallback to plain smartctl -A /dev/{device} for legacy USB SAT bridges
                try:
                    res_a = subprocess.run(
                        ["sudo", "-n", "smartctl", "-A", f"/dev/{device}"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
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
                health=health_str,
                power_cycles=p_cycles,
                power_on_hours=p_hours,
                media_errors=realloc,
            )
        except Exception:
            pass
        return SmartInfo()

    @staticmethod
    def get_smart_data(device: str) -> SmartInfo:
        # Instant return for non-SMART block devices (ZRAM, loopbacks, ramdisks, devmapper)
        if device.startswith(("zram", "loop", "ram", "dm", "sr", "fd", "nbd")):
            return SmartInfo()

        # Parse NVMe controller data
        match = re.match(r"(nvme\d+)", device)
        if match:
            ctrl = match.group(1)
            try:
                cmd = ["sudo", "-n", "nvme", "smart-log", f"/dev/{ctrl}"]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    temp_base = "N/A"
                    t_sensors: list[str] = []
                    health = "N/A"
                    tbr = "N/A"
                    tbw = "N/A"
                    power_cycles = "N/A"
                    power_on_hours = "N/A"
                    unsafe_shutdowns = "N/A"
                    media_errors = "N/A"
                    critical_warning = "N/A"
                    therm_t1 = "N/A"

                    for line in res.stdout.splitlines():
                        line = line.strip()
                        if not line or ":" not in line:
                            continue

                        key, val = (p.strip() for p in line.split(":", 1))

                        if key == "temperature":
                            temp_base = val.split("(")[0].strip().replace(" ", "")
                        elif key.startswith("Temperature Sensor"):
                            t_sensors.append(val.split("(")[0].strip().replace(" ", ""))
                        elif key == "percentage_used":
                            clean_val = val.replace("%", "").strip()
                            try:
                                health = f"{max(0, 100 - int(clean_val))}%"
                            except ValueError:
                                pass
                        elif key == "Data Units Read":
                            tbr = val.split("(")[1].replace(")", "").strip() if "(" in val else val
                        elif key == "Data Units Written":
                            tbw = val.split("(")[1].replace(")", "").strip() if "(" in val else val
                        elif key == "power_cycles":
                            power_cycles = val
                        elif key == "power_on_hours":
                            power_on_hours = val
                        elif key == "unsafe_shutdowns":
                            unsafe_shutdowns = val
                        elif key == "media_errors":
                            media_errors = val
                        elif key == "critical_warning":
                            critical_warning = val
                        elif key == "Thermal Management T1 Total Time":
                            therm_t1 = f"{val}s" if val.isdigit() else val

                    raw_temps: list[str] = []
                    if temp_base != "N/A":
                        raw_temps.append(temp_base)
                    for ts in t_sensors[:3]:
                        if ts != temp_base and ts not in raw_temps:
                            raw_temps.append(ts)

                    if not raw_temps:
                        temp_str = "N/A"
                    else:
                        clean_nums: list[str] = []
                        unit = "°C"
                        for t in raw_temps:
                            num = t.replace("°C", "").replace("C", "").replace("°F", "").replace("F", "").strip()
                            if "F" in t:
                                unit = "°F"
                            if num:
                                clean_nums.append(num)
                        if not clean_nums:
                            temp_str = "N/A"
                        elif len(clean_nums) <= 2:
                            temp_str = " │ ".join(f"{n}{unit}" for n in clean_nums)
                        else:
                            temp_str = f"{' │ '.join(clean_nums)}{unit}"

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

        # Fallback for SATA SSD, HDD, USB drives
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
                dtype = tran.upper() if tran else ("ZRAM" if name.startswith("zram") else d.get("type", "DISK").upper().strip())
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
                model = dev.get("model")
                clean_model = str(model).strip() if model else ("Compressed RAM" if name.startswith("zram") else "N/A")
                rota_val = dev.get("rota")
                is_hdd = str(rota_val).strip() in ("1", "true", "True") if rota_val is not None else False
                tran = dev.get("tran")
                dtype = tran.upper() if tran else ("ZRAM" if name.startswith("zram") else dev.get("type", "DISK").upper().strip())

                smart = SysStatParser.get_smart_data(name)
                return name, {
                    "size": dev.get("size", "?").strip(),
                    "type": dtype,
                    "model": clean_model,
                    "rota": is_hdd,
                    "smart": smart,
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
                r_mb_s = ((curr.read_sectors - prev.read_sectors) * 512) / dt / 1048576
                w_mb_s = ((curr.write_sectors - prev.write_sectors) * 512) / dt / 1048576
                r_iops = (curr.read_ios - prev.read_ios) / dt
                w_iops = (curr.write_ios - prev.write_ios) / dt

                total_ios_delta = (
                    (curr.read_ios - prev.read_ios)
                    + (curr.write_ios - prev.write_ios)
                    + (curr.discard_ios - prev.discard_ios)
                )
                total_ticks_delta = (
                    (curr.read_ticks - prev.read_ticks)
                    + (curr.write_ticks - prev.write_ticks)
                    + (curr.discard_ticks - prev.discard_ticks)
                )

                util_pct = min(((curr.io_ticks - prev.io_ticks) / 1000.0) / dt * 100.0, 100.0)
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
        table.add_column("C2", justify="left", no_wrap=True, width=24)
        table.add_column("F2", ratio=1)
        table.add_column("C3", justify="left", no_wrap=True, width=16)
        table.add_column("F3", ratio=1)
        table.add_column("C4", justify="left", no_wrap=True, width=17)

        r_spark, self.peak_read = self.generate_sparkline(self.history_read, self.peak_read, width=16, color_hex=SUCCESS)
        w_spark, self.peak_write = self.generate_sparkline(self.history_write, self.peak_write, width=16, color_hex=ACCENT)

        err_col = SUCCESS if str(smart.media_errors) == "0" else ERROR
        crit_col = SUCCESS if str(smart.critical_warning) == "0" else ERROR

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
            f"[{ACCENT}]{w_iops_str}[/] [bold {ERROR}]{await_ms:.2f} ms[/]"
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
                f"[bold {ERROR}]{await_ms:.2f} ms[/]",
                "",
                f"[{LABEL_COL}]UTIL    [{DIVIDER_COL}]│[/][/] [bold {ERROR}]{util_pct:>5.1f}%[/]",
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
                f"[{LABEL_COL}]HEALTH  [{DIVIDER_COL}]│[/][/] [bold {ACCENT}]{smart.health:>5}[/]",
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
                f"[{LABEL_COL}]T1 TIME  [{DIVIDER_COL}]│[/][/] [bold {FG}]{smart.therm_t1:>4}[/]",
                "",
                f"[{LABEL_COL}]PWR CUT [{DIVIDER_COL}]│[/][/] [bold {ERROR}]{smart.unsafe_shutdowns:>6}[/]",
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
                btn.label = "󰚰 Sync"
                btn.disabled = False
                btn.remove_class("-syncing")
                btn.remove_class("-synced")
        except Exception:
            pass

    @work(thread=True, exclusive=True)
    def refresh_metadata_worker(self) -> None:
        new_meta = SysStatParser.get_device_metadata()
        self.call_from_thread(self._update_meta, new_meta)

    def _update_meta(self, new_meta: dict[str, dict]) -> None:
        self.meta = new_meta

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
        ram_txt = Text.from_markup(
            f"[{LABEL_COL}]Dirty (Wait):[/] [bold {ACCENT}]{dirty:.1f} MB[/]    "
            f"[bold {BG} on {SUCCESS}] Dusky Disk [/]    "
            f"[{LABEL_COL}]Writeback (Active):[/] [bold {ERROR}]{wb:.1f} MB[/]"
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
        for dev in current_drives:
            if dev not in self.mounted_drives:
                clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", dev)
                widget = DriveWidget(id=f"drive_{clean_id}", dev_name=dev)
                scroll_area.mount(widget)
                self.mounted_drives.add(dev)

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

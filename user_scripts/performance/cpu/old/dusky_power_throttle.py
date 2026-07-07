#!/usr/bin/env python3
"""
Dusky Power Throttle (v7.0 - Apex)
CPU Package Power Limiter via RAPL
Arch Linux | Kernel 7.1+ | Intel/AMD
"""

import os
import sys
import time
import argparse
import json
import fcntl
import shutil
import curses
from pathlib import Path
from typing import Any

# ==========================================
# 1. Dependency Resolution & Rich Help
# ==========================================
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.align import Align
except ImportError:
    if os.geteuid() != 0:
        print("\033[93m[!] Missing 'rich' library. Elevating to install...\033[0m")
        sys.stdout.flush()
        os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
    import subprocess
    try:
        subprocess.run(["pacman", "-S", "--needed", "--noconfirm", "--quiet", "python-rich"], check=True)
    except subprocess.CalledProcessError:
        print("\033[91m[X] Failed to install dependencies. Please run: sudo pacman -S python-rich\033[0m")
        sys.exit(1)
    os.execvp(sys.executable, [sys.executable] + sys.argv)

console = Console()

def display_help() -> None:
    """Renders a comprehensive dashboard manual via Rich."""
    console.print(Panel.fit("[bold cyan]Dusky Power Throttle - Master Manual[/bold cyan]", border_style="cyan"))
    
    console.print("\n[bold yellow]EXECUTION MODES[/bold yellow]")
    cmd_table = Table(show_header=True, header_style="bold magenta", box=None)
    cmd_table.add_column("Command", style="cyan", width=14)
    cmd_table.add_column("Description", style="white")
    cmd_table.add_row("(no args)", "Launches the Live Interactive TUI Dashboard (Vim Tweak Mode).")
    cmd_table.add_row("status", "Print active power boundaries and package telemetry.")
    cmd_table.add_row("info", "Perform a forensic dump of raw Sysfs RAPL structures.")
    cmd_table.add_row("set", "Directly override limits or windows via standard CLI bounds.")
    cmd_table.add_row("reset", "Purge all runtime changes and force sync with BIOS baseline.")
    cmd_table.add_row("monitor", "Launch standard clean-line stdout terminal tracker.")
    cmd_table.add_row("raw", "Emit an atomic JSON dictionary string for scripts/Waybar.")
    console.print(cmd_table)
    
    console.print("\n[bold yellow]INTERACTIVE TUI KEYBINDINGS (Zero-Arg Mode)[/bold yellow]")
    tui_table = Table(show_header=True, header_style="bold magenta", box=None)
    tui_table.add_column("Keybind", style="green", width=16)
    tui_table.add_column("Action Performed", style="white")
    tui_table.add_row("j / k", "Navigate selection down / up through the power constraints.")
    tui_table.add_row("h / l", "Fine Adjustment: Decrement / Increment the selected parameter.")
    tui_table.add_row("H / L", "Coarse Adjustment: Macro decrement / increment the selected parameter.")
    tui_table.add_row("r", "Local Reset: Restore the currently selected parameter to BIOS default.")
    tui_table.add_row("Shift + R", "Global Reset: Restore ALL parameters to their BIOS defaults instantly.")
    tui_table.add_row("q", "Gracefully terminate the interactive TUI mode.")
    console.print(tui_table)

    console.print("\n[bold yellow]CLI ARGUMENTS (for 'set' command)[/bold yellow]")
    param_table = Table(show_header=True, header_style="bold magenta", box=None)
    param_table.add_column("Argument", style="green", width=22)
    param_table.add_column("Description", style="white")
    param_table.add_row("--pl1 <watts>", "Long-term sustained package ceiling limit.")
    param_table.add_row("--pl2 <watts>", "Short-term maximum boost package ceiling limit.")
    param_table.add_row("--pl4 <watts>", "Absolute peak hardware transient limit.")
    param_table.add_row("--pl1-time <sec>", "PL1 averaging time constant envelope window.")
    param_table.add_row("--pl2-time <sec>", "PL2 duration threshold window.")
    param_table.add_row("--save", "Lock structural changes into baseline buffer memory.")
    console.print(param_table)
    sys.exit(0)

if "-h" in sys.argv or "--help" in sys.argv:
    display_help()

# ==========================================
# 2. Privilege Escalation Check
# ==========================================
if os.geteuid() != 0:
    print("\033[93m[!] Elevating to root privileges...\033[0m")
    sys.stdout.flush()
    os.execvp("sudo", ["sudo", sys.executable] + sys.argv)

# ==========================================
# 3. Hardware Telemetry & I/O Engine
# ==========================================
RAPL_BASE = Path("/sys/class/powercap")
STATE_FILE = Path("/dev/shm/dusky_rapl_state.json")

def format_time(us: int) -> str:
    if us >= 1_000_000: return f"{us / 1_000_000:.2f}s"
    if us >= 1_000: return f"{us / 1_000:.1f}ms"
    return f"{us}µs"

class FastEnergyReader:
    """Context Manager providing persistent, zero-overhead file descriptor polling."""
    def __init__(self, path: Path):
        try:
            self.fd = os.open(path, os.O_RDONLY)
        except OSError:
            self.fd = None
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
    
    def read(self) -> int | None:
        if self.fd is None:
            return None
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            return int(os.read(self.fd, 32).decode().strip())
        except (OSError, ValueError):
            return None
            
    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

def find_package_domain() -> Path | None:
    domains = list(RAPL_BASE.glob("*rapl*"))
    domains.sort(key=lambda p: (1 if "mmio" in p.name else 0, p.name))
    for d in domains:
        name_file = d / "name"
        if name_file.exists() and name_file.read_text().strip() == "package-0":
            if (d / "constraint_0_power_limit_uw").exists():
                return d.resolve()
    return None

def safe_read_int(p: Path) -> int | None:
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None

def safe_write(p: Path, val: int) -> bool:
    try:
        p.write_text(str(val))
        return True
    except OSError:
        return False

def get_power_info(domain: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "energy_uj": safe_read_int(domain / "energy_uj"),
        "max_energy_range_uj": safe_read_int(domain / "max_energy_range_uj"),
        "enabled": safe_read_int(domain / "enabled"),
        "name": (domain / "name").read_text().strip() if (domain / "name").exists() else "unknown"
    }
    for f in domain.glob("constraint_*"):
        if f.is_file() and not f.name.endswith("_name"):
            info[f.name] = safe_read_int(f)
    for nf in domain.glob("constraint_*_name"):
        info[nf.name] = nf.read_text().strip()
    return info

# ==========================================
# 4. Core Management State Controller
# ==========================================
class PowerThrottle:
    def __init__(self):
        self.domain = find_package_domain()
        if not self.domain:
            console.print("[bold red][X] No RAPL package domain found. Hardware limitation features unavailable.[/bold red]")
            sys.exit(1)
        self._ensure_state_exists()

    def _ensure_state_exists(self) -> None:
        """Bootstraps the JSON state file and auto-heals any missing keys or domain changes."""
        domain_str = str(self.domain)
        def heal_state(data):
            healed = False
            if data.get("domain") != domain_str:
                data["domain"] = domain_str
                data["boot"] = self._capture_power_limits()
                healed = True
            
            boot = data.setdefault("boot", {})
            current = self._capture_power_limits()
            for k, v in current.items():
                if k not in boot:
                    boot[k] = v
                    healed = True
            if healed:
                return data
            return None
        self._atomic_state_update(heal_state)

    def _atomic_state_update(self, callback) -> None:
        STATE_FILE.touch(mode=0o644, exist_ok=True)
        with open(STATE_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                write_needed = False
                try:
                    f.seek(0)
                    data = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    data = {"domain": str(self.domain), "boot": self._capture_power_limits(), "modified": False}
                    write_needed = True
                
                updated_data = callback(data)
                if updated_data is not None:
                    data = updated_data
                    write_needed = True
                
                if write_needed:
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _capture_power_limits(self) -> dict[str, int]:
        result = {}
        for c in ["constraint_0_power_limit_uw", "constraint_1_power_limit_uw", "constraint_2_power_limit_uw",
                  "constraint_0_time_window_us", "constraint_1_time_window_us"]:
            val = safe_read_int(self.domain / c)
            if val is not None:
                result[c] = val
        return result

    def get_boot_state(self) -> dict[str, int]:
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f).get("boot", {})
        except (OSError, json.JSONDecodeError):
            return self._capture_power_limits()

    def _persist_boot_values(self) -> None:
        self._ensure_state_exists()

    def restore(self) -> None:
        def do_restore(data):
            boot = data.get("boot", {})
            for key, val in boot.items():
                safe_write(self.domain / key, val)
            console.print("[bold green][+] Power limits restored to original BIOS values.[/bold green]")
            data["modified"] = False
            return data
        self._atomic_state_update(do_restore)

    def set_limit(self, pl1: int | None = None, pl2: int | None = None, pl4: int | None = None,
                  pl1_time: int | None = None, pl2_time: int | None = None, 
                  save_as_default: bool = False) -> dict[str, int]:
        self._persist_boot_values()
        result = {}
        
        operations = {
            "pl1": ("constraint_0_power_limit_uw", pl1),
            "pl2": ("constraint_1_power_limit_uw", pl2),
            "pl4": ("constraint_2_power_limit_uw", pl4),
            "pl1_time": ("constraint_0_time_window_us", pl1_time),
            "pl2_time": ("constraint_1_time_window_us", pl2_time),
        }

        for name, (sysfs_file, value) in operations.items():
            if value is not None:
                if safe_write(self.domain / sysfs_file, value):
                    result[f"{name}_set"] = value
                else:
                    result[f"{name}_write_failed"] = True

        time.sleep(0.05) # Sync processing gap
        
        for name, sysfs_file in [("pl1", "constraint_0_power_limit_uw"), 
                                 ("pl2", "constraint_1_power_limit_uw"),
                                 ("pl4", "constraint_2_power_limit_uw"),
                                 ("pl1_time", "constraint_0_time_window_us"),
                                 ("pl2_time", "constraint_1_time_window_us")]:
            actual = safe_read_int(self.domain / sysfs_file)
            if actual is not None:
                result[f"{name}_actual"] = actual

        def flag_modified(data):
            data["modified"] = True
            if save_as_default:
                data["boot"] = self._capture_power_limits()
            return data
            
        self._atomic_state_update(flag_modified)
        return result

    def status(self) -> dict[str, Any]:
        info = get_power_info(self.domain)
        pkg_power = None
        max_energy = info.get("max_energy_range_uj", 0) or 0
        
        energy_file = self.domain / "energy_uj"
        if energy_file.exists():
            with FastEnergyReader(energy_file) as reader:
                e1 = reader.read()
                t1 = time.perf_counter()
                time.sleep(0.2)
                e2 = reader.read()
                t2 = time.perf_counter()
                
                if e1 is not None and e2 is not None and (t2 - t1) > 0:
                    delta_e = e2 - e1
                    if delta_e < 0 and max_energy > 0:
                        delta_e += max_energy 
                    pkg_power = (delta_e / 1_000_000) / (t2 - t1)
            
        info["power_watts"] = pkg_power
        return info

    def monitor(self, interval: float = 1.0, count: int | None = None) -> None:
        energy_file = self.domain / "energy_uj"
        if not energy_file.exists():
            console.print("[bold red][X] Energy telemetry missing. Cannot monitor.[/bold red]")
            return
            
        max_energy = safe_read_int(self.domain / "max_energy_range_uj") or 0
        pl1_base = (safe_read_int(self.domain / "constraint_0_power_limit_uw") or 0) // 1_000_000
        pl2_base = (safe_read_int(self.domain / "constraint_1_power_limit_uw") or 0) // 1_000_000
        pl4_base = (safe_read_int(self.domain / "constraint_2_power_limit_uw") or 0) // 1_000_000
        dynamic_max = max(pl4_base, pl2_base * 1.2, pl1_base * 1.5, 100.0)

        console.print(f"[bold cyan]RAPL Power Monitor[/bold cyan] (Interval: {interval}s | Range: 0-{int(dynamic_max)}W | Ctrl+C to stop)")
        t_start = time.monotonic()
        n = 0
        
        try:
            with FastEnergyReader(energy_file) as reader:
                while count is None or n < count:
                    cols = shutil.get_terminal_size().columns
                    ts = time.monotonic() - t_start
                    
                    e1 = reader.read()
                    t1 = time.perf_counter()
                    time.sleep(interval)
                    e2 = reader.read()
                    t2 = time.perf_counter()
                    
                    p = None
                    if e1 is not None and e2 is not None and (t2 - t1) > 0:
                        delta_e = e2 - e1
                        if delta_e < 0 and max_energy > 0:
                            delta_e += max_energy
                        p = (delta_e / 1_000_000) / (t2 - t1)

                    if p is None:
                        line = f"[{ts:7.1f}s]  Power: N/A"
                    else:
                        bar_w = max(10, cols - 45)
                        filled = max(0, min(bar_w, int((p / dynamic_max) * bar_w)))
                        bar = "█" * filled + "░" * (bar_w - filled)
                        
                        pl1_raw = safe_read_int(self.domain / "constraint_0_power_limit_uw")
                        pl1_current = pl1_raw // 1_000_000 if pl1_raw else 0
                        line = f"[{ts:7.1f}s]  {bar}  {p:6.1f} W  (limit: PL1={pl1_current}W)"
                    
                    sys.stdout.write(f"\r{line[:cols]:<{cols}}")
                    sys.stdout.flush()
                    n += 1
        except KeyboardInterrupt:
            print()

# ==========================================
# 5. Interactive Curses Dashboard (Vim Mode)
# ==========================================
def safe_addstr(stdscr, y: int, x: int, string: str, attr: int = 0) -> None:
    try:
        max_y, max_x = stdscr.getmaxyx()
        if y < 0 or y >= max_y or x < 0 or x >= max_x: return
        stdscr.addstr(y, x, string[:max_x - x], attr)
    except curses.error:
        pass

def interactive_mode(stdscr, throttle: PowerThrottle) -> None:
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    stdscr.timeout(250)
    
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(5, curses.COLOR_YELLOW, -1)
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)

    items = [
        {"id": "pl1", "label": "PL1 Limit", "type": "power", "sysfs": "constraint_0_power_limit_uw", "step": 1, "big": 5},
        {"id": "pl1_time", "label": "PL1 Window", "type": "time", "sysfs": "constraint_0_time_window_us", "step": 500_000, "big": 5_000_000},
        {"id": "pl2", "label": "PL2 Limit", "type": "power", "sysfs": "constraint_1_power_limit_uw", "step": 1, "big": 5},
        {"id": "pl2_time", "label": "PL2 Window", "type": "time", "sysfs": "constraint_1_time_window_us", "step": 250, "big": 2500},
        {"id": "pl4", "label": "PL4 Limit", "type": "power", "sysfs": "constraint_2_power_limit_uw", "step": 5, "big": 25},
    ]
    
    selected_idx = 0
    feedback_msg = "Dashboard initialized. Ready."
    feedback_time = time.time()

    max_energy = safe_read_int(throttle.domain / "max_energy_range_uj") or 0
    energy_file = throttle.domain / "energy_uj"
    
    with FastEnergyReader(energy_file) as reader:
        last_e = reader.read()
        last_t = time.perf_counter()
        pkg_watts = 0.0

        while True:
            stdscr.clear()
            my, mx = stdscr.getmaxyx()
            
            if my < 12 or mx < 68:
                safe_addstr(stdscr, 0, 0, "Terminal window too small for layout visualization.", curses.color_pair(3))
                stdscr.refresh()
                if stdscr.getch() == ord('q'): break
                continue

            curr_e = reader.read()
            curr_t = time.perf_counter()
            if curr_e is not None:
                if last_e is None:
                    last_e = curr_e
                    last_t = curr_t
                elif (curr_t - last_t) >= 0.2:
                    delta_e = curr_e - last_e
                    if delta_e < 0 and max_energy > 0: 
                        delta_e += max_energy
                    pkg_watts = (delta_e / 1_000_000) / (curr_t - last_t)
                    last_e = curr_e  # Update with raw hardware value, preventing bounds corruption
                    last_t = curr_t

            # Fetch fresh boot state each loop in case it self-healed
            boot_state = throttle.get_boot_state()

            title = " Dusky Power Throttle "
            safe_addstr(stdscr, 0, max(0, (mx - len(title)) // 2), title, curses.color_pair(6) | curses.A_REVERSE | curses.A_BOLD)
            
            p_str = f"PKG Telemetry: {pkg_watts:5.1f} W"
            safe_addstr(stdscr, 2, 2, p_str, curses.A_BOLD)
            bar_space = max(10, mx - 30)
            filled = max(0, min(bar_space, int((pkg_watts / 150.0) * bar_space)))
            bar_graph = "█" * filled + "░" * (bar_space - filled)
            safe_addstr(stdscr, 2, 24, f"[{bar_graph}]", curses.color_pair(1))

            if time.time() - feedback_time > 4.0:
                feedback_msg = "System tracking operational."
            
            # Dynamic feedback coloring
            status_color = curses.color_pair(5)
            if "operation" in feedback_msg.lower(): status_color = curses.color_pair(2)
            if "floor" in feedback_msg.lower() or "ceiling" in feedback_msg.lower() or "reject" in feedback_msg.lower(): 
                status_color = curses.color_pair(3) | curses.A_BOLD
            
            safe_addstr(stdscr, 4, 2, f"Status: {feedback_msg}", status_color)

            # Upgraded UI Layout Headers
            safe_addstr(stdscr, 6, 2, f"{'CONSTRAINT PARAMETER':<22} | {'CURRENT VALUE':<15} | {'BIOS DEFAULT':<15}", curses.A_UNDERLINE | curses.A_BOLD)

            for idx, item in enumerate(items):
                current_raw = safe_read_int(throttle.domain / item["sysfs"])
                boot_raw = boot_state.get(item["sysfs"])
                
                # Format Current Value
                if current_raw is None or current_raw == 0:
                    current_str = "N/A"
                else:
                    current_str = f"{current_raw // 1_000_000} W" if item["type"] == "power" else format_time(current_raw)
                    
                # Format Boot Default
                if boot_raw is None or boot_raw == 0:
                    boot_str = "N/A"
                else:
                    boot_str = f"{boot_raw // 1_000_000} W" if item["type"] == "power" else format_time(boot_raw)

                row_y = 7 + idx
                lbl = item["label"]
                
                # Determine drift highlight color
                val_color = curses.A_NORMAL
                if current_raw != boot_raw and current_raw is not None and boot_raw is not None:
                    val_color = curses.color_pair(5) # Yellow if modified from BIOS

                if idx == selected_idx:
                    safe_addstr(stdscr, row_y, 2, f"-> {lbl:<19} | ", curses.color_pair(4))
                    safe_addstr(stdscr, row_y, 27, f"{current_str:<15}", curses.color_pair(4) | curses.A_BOLD)
                    safe_addstr(stdscr, row_y, 42, f" | {boot_str:<15}", curses.color_pair(4))
                else:
                    safe_addstr(stdscr, row_y, 2, f"   {lbl:<19} | ")
                    safe_addstr(stdscr, row_y, 27, f"{current_str:<15}", val_color)
                    safe_addstr(stdscr, row_y, 42, f" | {boot_str:<15}", curses.A_DIM)

            safe_addstr(stdscr, 14, 2, "[j/k] Nav | [h/l] Modify | [H/L] Coarse | [r] Reset Row | [Shift+R] Reset All | [q] Quit", curses.color_pair(1) | curses.A_DIM)
            stdscr.refresh()

            ch = stdscr.getch()
            if ch == curses.ERR: continue
            
            match ch:
                case 107 | curses.KEY_UP:    # 'k'
                    selected_idx = max(0, selected_idx - 1)
                case 106 | curses.KEY_DOWN:  # 'j'
                    selected_idx = min(len(items) - 1, selected_idx + 1)
                case 113:                    # 'q'
                    break
                case 114:                    # 'r' (Local Reset)
                    curr_item = items[selected_idx]
                    boot_val = boot_state.get(curr_item["sysfs"])
                    if boot_val is not None:
                        safe_write(throttle.domain / curr_item["sysfs"], boot_val)
                        feedback_msg = f"Restored {curr_item['label']} to BIOS default."
                    else:
                        feedback_msg = f"No known default for {curr_item['label']}."
                    feedback_time = time.time()
                case 82:                     # 'R' (Shift+R) (Global Reset)
                    def do_restore(data):
                        boot = data.get("boot", {})
                        for key, val in boot.items():
                            safe_write(throttle.domain / key, val)
                        data["modified"] = False
                        return data
                    throttle._atomic_state_update(do_restore)
                    feedback_msg = "Global Reset Executed! Restored all to BIOS defaults."
                    feedback_time = time.time()
                    
                case 104 | 108 | 72 | 76:    # 'h', 'l', 'H', 'L'
                    curr_item = items[selected_idx]
                    raw_curr = safe_read_int(throttle.domain / curr_item["sysfs"]) or 0
                    
                    multiplier = curr_item["big"] if ch in (72, 76) else curr_item["step"]
                    if curr_item["type"] == "power":
                        multiplier *= 1_000_000
                    
                    if ch in (104, 72): 
                        new_raw = max(0, raw_curr - multiplier)
                    else: 
                        new_raw = raw_curr + multiplier
                        
                    safe_write(throttle.domain / curr_item["sysfs"], new_raw)
                    time.sleep(0.01) 
                    
                    verify_raw = safe_read_int(throttle.domain / curr_item["sysfs"])
                    
                    if verify_raw is not None:
                        # Validate whether it was a strict hardware rejection or a soft mathematical clamping
                        if verify_raw == raw_curr and new_raw != raw_curr:
                            if new_raw < raw_curr:
                                feedback_msg = f"Hardware floor hit: {curr_item['label']} cannot go lower."
                            else:
                                feedback_msg = f"Hardware ceiling hit: {curr_item['label']} cannot go higher."
                        elif verify_raw != new_raw:
                            val_str = format_time(verify_raw) if curr_item["type"] == "time" else f"{verify_raw // 1_000_000} W"
                            feedback_msg = f"Hardware quantized {curr_item['label']} to nearest step ({val_str})."
                        else:
                            feedback_msg = f"Updated {curr_item['label']} successfully."
                    else:
                        feedback_msg = f"Rejected! {curr_item['label']} unsupported by hardware."
                    
                    feedback_time = time.time()

# ==========================================
# 6. Command Line Entry Parser & Fallbacks
# ==========================================
def display_status(throttle: PowerThrottle) -> None:
    s = throttle.status()
    
    pl1_val = s.get("constraint_0_power_limit_uw")
    pl1_w_str = f"{pl1_val // 1_000_000} W" if pl1_val is not None else "N/A"
    
    pl2_val = s.get("constraint_1_power_limit_uw")
    pl2_w_str = f"{pl2_val // 1_000_000} W" if pl2_val is not None else "N/A"
    
    pl4_val = s.get("constraint_2_power_limit_uw")
    pl4_w_str = f"{pl4_val // 1_000_000} W" if pl4_val is not None else "N/A"
    
    pl1_time_val = s.get("constraint_0_time_window_us")
    pl1_time_str = format_time(pl1_time_val) if pl1_time_val is not None else "N/A"
    
    pl2_time_val = s.get("constraint_1_time_window_us")
    pl2_time_str = format_time(pl2_time_val) if pl2_time_val is not None else "N/A"
    
    power = s.get("power_watts")
    power_str = f"{power:.1f} W" if power is not None else "N/A"

    table = Table(show_header=False, expand=False, box=None)
    table.add_column("Property", style="bold cyan", justify="right")
    table.add_column("Value", style="bold white", justify="left")
    
    table.add_row("RAPL Domain:", s.get('name', 'unknown'))
    table.add_row("Package Power:", f"[magenta]{power_str}[/magenta]")
    table.add_row("PL1 (Long-Term):", f"[yellow]{pl1_w_str}[/yellow]" + (f" [dim](Window: {pl1_time_str})[/dim]" if pl1_val is not None else ""))
    table.add_row("PL2 (Short-Term):", f"[yellow]{pl2_w_str}[/yellow]" + (f" [dim](Window: {pl2_time_str})[/dim]" if pl2_val is not None else ""))
    if pl4_val is not None:
        table.add_row("PL4 (Peak Limit):", f"[yellow]{pl4_w_str}[/yellow]")
        
    status_panel = Panel(
        table,
        title="[bold magenta] Dusky Power Throttle [/bold magenta]",
        border_style="cyan",
        expand=False
    )
    console.print(Align.center(status_panel))

def main() -> None:
    throttle = PowerThrottle()

    parser = argparse.ArgumentParser(add_help=False)
    sub = parser.add_subparsers(dest="command")
    
    sub.add_parser("status")
    sub.add_parser("info")
    
    p_set = sub.add_parser("set")
    p_set.add_argument("--pl1", type=int, default=None)
    p_set.add_argument("--pl2", type=int, default=None)
    p_set.add_argument("--pl4", type=int, default=None)
    p_set.add_argument("--pl1-time", type=float, default=None)
    p_set.add_argument("--pl2-time", type=float, default=None)
    p_set.add_argument("--save", action="store_true")

    p_reset = sub.add_parser("reset")
    p_reset.add_argument("--force", action="store_true")
    
    p_mon = sub.add_parser("monitor")
    p_mon.add_argument("-i", "--interval", type=float, default=1.0)
    p_mon.add_argument("-n", "--count", type=int, default=None)
    
    p_raw = sub.add_parser("raw")
    p_raw.add_argument("--watch", action="store_true")
    p_raw.add_argument("-i", "--interval", type=float, default=1.0)

    args, unknown = parser.parse_known_args()
    if unknown:
        console.print(f"[bold red][X] Unrecognized arguments: {unknown}[/bold red]")
        console.print("Use -h or --help for usage details.")
        sys.exit(1)

    # Default to Curses interactive GUI environment if no subcommand passed
    if not args.command:
        curses.wrapper(interactive_mode, throttle)
        sys.exit(0)

    match args.command:
        case "status":
            display_status(throttle)

        case "info":
            s = throttle.status()
            table = Table(title="Raw Sysfs RAPL Output", show_header=True, header_style="bold magenta")
            table.add_column("Parameter", style="cyan")
            table.add_column("Value", style="green")
            for k, v in sorted(s.items()):
                if v is None: display_v = "[dim]N/A[/dim]"
                elif k.endswith("_power_limit_uw") and isinstance(v, int): display_v = f"{v} µW ({v // 1_000_000} W)"
                elif k.endswith("_time_window_us") and isinstance(v, int): display_v = f"{v} µs ({format_time(v)})"
                elif k in ("energy_uj", "max_energy_range_uj") and isinstance(v, int): display_v = f"{v} µJ"
                elif k == "power_watts": display_v = f"{v:.1f} W"
                else: display_v = str(v)
                table.add_row(k, display_v)
            console.print(table)

        case "set":
            if all(v is None for v in [args.pl1, args.pl2, args.pl4, args.pl1_time, args.pl2_time]):
                console.print("[bold red][X] Specify at least one bound parameter attribute constraint.[/bold red]")
                sys.exit(1)

            pl1_uw = args.pl1 * 1_000_000 if args.pl1 is not None else None
            pl2_uw = args.pl2 * 1_000_000 if args.pl2 is not None else None
            pl4_uw = args.pl4 * 1_000_000 if args.pl4 is not None else None
            pl1_time_us = int(args.pl1_time * 1_000_000) if args.pl1_time is not None else None
            pl2_time_us = int(args.pl2_time * 1_000_000) if args.pl2_time is not None else None

            result = throttle.set_limit(pl1=pl1_uw, pl2=pl2_uw, pl4=pl4_uw, 
                                        pl1_time=pl1_time_us, pl2_time=pl2_time_us, save_as_default=args.save)
            
            console.print("\n[bold green][+] Power Configuration Sync Pipeline Complete:[/bold green]")
            any_failed = False
            for param, label, is_time in [
                ("pl1", "PL1 (Long-Term)", False),
                ("pl2", "PL2 (Short-Term)", False),
                ("pl4", "PL4 (Peak)", False),
                ("pl1_time", "PL1 Window", True),
                ("pl2_time", "PL2 Window", True)
            ]:
                if f"{param}_write_failed" in result:
                    console.print(f"    {label:<18}: [bold red]Write failed (unsupported or permission denied)[/bold red]")
                    any_failed = True
                elif f"{param}_set" in result:
                    target = result[f"{param}_set"]
                    actual = result.get(f"{param}_actual", 0)
                    if is_time:
                        target_str = format_time(target)
                        actual_str = format_time(actual)
                    else:
                        target_str = f"{target // 1_000_000} W"
                        actual_str = f"{actual // 1_000_000} W"

                    if actual == target:
                        console.print(f"    {label:<18}: [green]{target_str}[/green] [dim](Verified)[/dim]")
                    elif target != 0 and (abs(actual - target) / target) <= 0.05:
                        console.print(f"    {label:<18}: [green]{target_str}[/green] [dim](Verified: quantized to {actual_str})[/dim]")
                    else:
                        console.print(f"    {label:<18}: [yellow]{target_str}[/yellow] [bold red](Rejected by Hardware! Actual Locked: {actual_str})[/bold red]")
                        any_failed = True
            if any_failed:
                sys.exit(1)

        case "reset":
            if not args.force:
                console.print("[bold yellow][!] This will clear local baseline tracking and restore original BIOS values.[/bold yellow]")
                try:
                    if input("Continue? [y/N] ").strip().lower() != "y": sys.exit("Aborted.")
                except (EOFError, KeyboardInterrupt): sys.exit("\nAborted.")
            throttle.restore()

        case "monitor":
            throttle.monitor(args.interval, args.count)

        case "raw":
            s = throttle.status()
            s["constraint_0_power_limit_w"] = s["constraint_0_power_limit_uw"] // 1_000_000 if s.get("constraint_0_power_limit_uw") is not None else None
            s["constraint_1_power_limit_w"] = s["constraint_1_power_limit_uw"] // 1_000_000 if s.get("constraint_1_power_limit_uw") is not None else None
            s["constraint_2_power_limit_w"] = s["constraint_2_power_limit_uw"] // 1_000_000 if s.get("constraint_2_power_limit_uw") is not None else None
            
            if args.watch:
                energy_file = throttle.domain / "energy_uj"
                if not energy_file.exists():
                    console.print("[bold red][X] Energy telemetry missing. Cannot watch.[/bold red]")
                    sys.exit(1)
                max_energy = s.get("max_energy_range_uj", 0) or 0
                try:
                    with FastEnergyReader(energy_file) as reader:
                        while True:
                            e1 = reader.read()
                            t1 = time.perf_counter()
                            time.sleep(args.interval)
                            e2 = reader.read()
                            t2 = time.perf_counter()
                            
                            p = None
                            if e1 is not None and e2 is not None and (t2 - t1) > 0:
                                delta_e = e2 - e1
                                if delta_e < 0 and max_energy > 0: 
                                    delta_e += max_energy
                                p = (delta_e / 1_000_000) / (t2 - t1)
                                
                            out = {
                                "timestamp": time.time(), 
                                "power_w": round(p, 2) if p is not None else None,
                                "pl1_w": (safe_read_int(throttle.domain / "constraint_0_power_limit_uw") or 0) // 1_000_000,
                                "pl2_w": (safe_read_int(throttle.domain / "constraint_1_power_limit_uw") or 0) // 1_000_000,
                                "pl4_w": (safe_read_int(throttle.domain / "constraint_2_power_limit_uw") or 0) // 1_000_000
                            }
                            print(json.dumps(out))
                            sys.stdout.flush()
                except KeyboardInterrupt: pass
            else:
                print(json.dumps(s, indent=2, default=str))

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

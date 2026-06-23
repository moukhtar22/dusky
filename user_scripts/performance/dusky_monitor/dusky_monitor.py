#!/usr/bin/env python3
"""
Dusky Monitor - Advanced Live System Resource Monitor
A rigorously optimized Terminal User Interface (TUI) utilizing Textual, psutil, and RapidFuzz.
Architected for the absolute vanguard of Python 3.14+ syntactical and asynchronous paradigms.
"""

import sys
import os
import json
import time
import subprocess
import asyncio
from pathlib import Path
from collections import deque
from typing import Any, Iterable

# =============================================================================
# DEPENDENCY BOOTSTRAPPER (Auto-Installer / Environment Sanity Check)
# =============================================================================
def _ensure_dependencies() -> None:
    missing: list[str] = []
    
    for module in ("psutil", "rapidfuzz", "textual"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
            
    if missing:
        print(f"\033[94m[Dusky Monitor]\033[0m Missing dependencies detected: \033[93m{', '.join(missing)}\033[0m")
        print("\033[90m[*] Initializing automatic installation...\033[0m")
        try:
            # Mandating --break-system-packages for modern Arch/PEP 668 environments if run outside venv
            subprocess.run(
                [sys.executable, "-m", "pip", "install", *missing, "--break-system-packages"],
                check=True, stdout=sys.stdout, stderr=sys.stderr
            )
            print("\033[92m[*] Installation successful. Rebooting execution context...\033[0m")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except subprocess.CalledProcessError:
            print("\033[91m[!] Auto-installation unequivocally failed.\033[0m")
            print("Execute manual remediation: sudo pacman -S --needed python-psutil python-rapidfuzz python-textual")
            sys.exit(1)

_ensure_dependencies()

# --- Deferred Imports Post-Bootstrapper ---
import psutil
from rapidfuzz import fuzz
from textual import on, events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal, Container
from textual.widgets import Input, DataTable, Label, OptionList
from textual.widgets.option_list import Option
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.theme import Theme
from rich.text import Text

# =============================================================================
# CLI ARGUMENT PARSER (Modernized Pattern Matching)
# =============================================================================
def parse_cli_args() -> dict[str, Any]:
    args = sys.argv[1:]
    config: dict[str, Any] = {
        "sort_metric": "ram",
        "show_full": False,
        "interval": 2.0,
        "count": None,  
        "pids": [],
        "name": ""
    }
    
    interval_explicit = False
    plain_nums: list[str] = []
    i = 0
    
    while i < len(args):
        arg = args[i].lower()
        match arg:
            case "help" | "-h" | "--help":
                print("\n\033[94m::\033[0m \033[1mres_mon\033[0m — Live System Resource Monitor")
                print("\033[38;5;238m" + "-"*77 + "\033[0m")
                print("\033[92mUsage:\033[0m res_mon [sort_metric] [display_mode] [interval] [count] [filters]")
                print("       \033[38;5;242m(Arguments orchestrate autonomously irrespective of sequence)\033[0m\n")
                
                print("\033[1mMetrics:\033[0m (Default: ram)")
                print("  \033[96mram\033[0m, \033[96mmem\033[0m              - Sort by memory footprint")
                print("  \033[96mcpu\033[0m                    - Sort by CPU computational saturation\n")
                
                print("\033[1mDisplay:\033[0m (Default: sanitized binary nomenclature)")
                print("  \033[96mpath\033[0m, \033[96mfull\033[0m, \033[96margs\033[0m     - Expose unabridged command trajectories\n")

                print("\033[1mFilters:\033[0m (Optional)")
                print("  \033[96m-p, --pid <pids>\033[0m       - Target explicit PIDs (comma-separated)")
                print("  \033[96m-n, --name <name>\033[0m      - Fuzzy probabilistic evaluation by nomenclature\n")
                
                print("\033[1mNumbers:\033[0m (Defaults: 2s interval, Unbounded process matrix)")
                print("  \033[96m<number>\033[0m               - Constrain the process matrix magnitude (e.g., 20)")
                print("  \033[96m<number>s\033[0m              - Dictate temporal refresh cadence (e.g., 1.5s)\n")
                sys.exit(0)
            
            case "cpu": config["sort_metric"] = "cpu"
            case "ram" | "mem" | "memory": config["sort_metric"] = "ram"
            case "path" | "full" | "args": config["show_full"] = True
            case "-p" | "--pid":
                if i + 1 < len(args):
                    try: config["pids"] = [int(p) for p in args[i+1].replace(',', ' ').split()]
                    except ValueError: pass
                    i += 1
            case "-n" | "--name":
                if i + 1 < len(args):
                    config["name"] = args[i+1]
                    i += 1
            case _ if arg.endswith('s') and arg[:-1].replace('.', '', 1).isdigit():
                config["interval"] = float(arg[:-1])
                interval_explicit = True
            case _ if arg.replace('.', '', 1).isdigit():
                plain_nums.append(arg)  
        i += 1

    # Deterministic Scalar Routing
    if len(plain_nums) == 1:
        if "." in plain_nums[0]:
            if not interval_explicit:
                config["interval"] = float(plain_nums[0])
        else:
            config["count"] = int(plain_nums[0])
    elif len(plain_nums) >= 2:
        val1, val2 = float(plain_nums[0]), float(plain_nums[1])
        if val1 > val2:
            config["count"] = int(val1)
            if not interval_explicit: config["interval"] = val2
        else:
            if not interval_explicit: config["interval"] = val1
            config["count"] = int(val2)
            
    config["interval"] = max(0.1, config["interval"])
    return config

# =============================================================================
# NOTIFICATION ENGINE (Wayland/X11 Mako Integration)
# =============================================================================
def dispatch_notification(title: str, message: str) -> None:
    try:
        subprocess.Popen([
            "notify-send", 
            "--app-name=dusky-glance-alert",
            "-u", "critical",
            title, message
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass

# =============================================================================
# FORMATTING & UTILITY ARCHITECTURE
# =============================================================================
def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

def format_bytes_to_mb(bytes_val: int | float) -> float:
    return bytes_val / (1024 * 1024)

def format_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"
    elif bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"

# =============================================================================
# BESPOKE UI WIDGETS (The Braille Graph Engine)
# =============================================================================
class BrailleGraph(Label):
    """
    A cutting-edge, visually elegant multi-line Sparkline utilizing Unicode Braille dots.
    Packs 2 data points horizontally per character, and 4 vertical levels per character row.
    """
    data: reactive[list[float]] = reactive(list)

    def __init__(self, *args: Any, anchor_zero: bool = True, min_range: float = 1.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.anchor_zero = anchor_zero
        self.min_range = min_range

    def render(self) -> Text:
        # Use self.size to obtain the rigid container dimensions given by the layout engine,
        # thereby circumventing the catastrophic horizontal collapse of content_size.
        width = self.size.width
        height = self.size.height
        
        # Halt execution gracefully if dimensions haven't been resolved by the hypervisor yet
        if width <= 0 or height <= 0:
            return Text("")
            
        max_points = width * 2
        actual_data = list(self.data)

        # Generate a perfectly empty matrix to sustain the rigid structural integrity when idle
        if not actual_data:
            empty_line = chr(0x2800) * width
            return Text("\n".join([empty_line] * height), no_wrap=True, overflow="crop")

        # CRITICAL FIX: Slicing the data matrix to the precise viewport limit *before* processing bounds.
        # This isolates the Y-axis scaling to only the visually rendered data, preventing off-screen 
        # spikes from corrupting and "squashing" the active topography.
        if len(actual_data) > max_points:
            actual_data = actual_data[-max_points:]

        min_val = min(actual_data)
        max_val = max(actual_data)
        
        # Absolute structural anchoring to suppress infinite-zoom distortion
        if self.anchor_zero:
            min_val = 0.0
            
        if max_val - min_val < self.min_range:
            max_val = min_val + self.min_range

        range_val = max_val - min_val

        # MATHEMATICAL PADDING: Zero-pad the left void to force data to stream from the right edge
        pad_len = max_points - len(actual_data)
        padded_data = [None] * pad_len + actual_data

        total_dots_y = height * 4
        dots_data = []
        
        for v in padded_data:
            if v is None:
                dots_data.append(0)
            else:
                normalized = (v - min_val) / range_val
                dots = int(normalized * total_dots_y)
                dots_data.append(min(total_dots_y, max(0, dots)))

        lines = []
        # Construct graph descending iteratively from the zenith to the baseline
        for row in range(height):
            # Resolve the mathematical baseline dot index for the currently active row
            row_bottom_dot_idx = (height - row - 1) * 4
            line_chars = []
            
            for i in range(0, len(dots_data), 2):
                dl = dots_data[i] - row_bottom_dot_idx
                dr = dots_data[i+1] - row_bottom_dot_idx
                
                # Rigid constraint mapping: restrict local row projections to [0, 4]
                hl = min(4, max(0, dl))
                hr = min(4, max(0, dr))
                
                # Binary Bitmask mapping into standard unicode braille offsets (Left & Right dot structures)
                l_mask = [0, 0x40, 0x44, 0x46, 0x47][hl]
                r_mask = [0, 0x80, 0xA0, 0xB0, 0xB8][hr]
                
                line_chars.append(chr(0x2800 + l_mask + r_mask))
            
            lines.append("".join(line_chars))

        # We return a locked, no-wrap Rich Text object to bypass Textual's inherent sentence-formatting logic
        return Text("\n".join(lines), no_wrap=True, overflow="crop")


# =============================================================================
# MODALS & INTERFACE COMPONENTS
# =============================================================================
class SetThresholdScreen(ModalScreen[float | None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="threshold-dialog"):
            yield Label("Set RAM Threshold Alert (MB)", id="modal-title")
            yield Input(placeholder="e.g., 2048", id="threshold-input", type="number")
            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn", id="cancel-btn")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted, "#threshold-input")
    def on_submit(self, event: Input.Submitted) -> None:
        try:
            val = float(event.value)
            self.dismiss(val)
        except ValueError:
            self.dismiss(None)

    @on(events.Click, "#cancel-btn")
    def on_cancel(self) -> None:
        self.dismiss(None)


class ShortcutsInfoScreen(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="shortcuts-dialog"):
            yield Label("KEYBOARD SCHEMATICS", id="modal-title")
            yield OptionList(id="shortcuts-list")
            with Horizontal(classes="modal-btn-container"):
                yield Label(" Close ", classes="modal-close-btn")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        bindings_info = [
            ("q, ctrl+c", "Terminate the hypervisor"),
            ("Enter", "Track process / Launch Panopticon"),
            ("t", "Set RAM alert threshold for tracked process"),
            ("s", "Toggle Heuristic Sorting (RAM / CPU)"),
            ("c", "Toggle Command Trajectory Visibility"),
            ("/", "Initiate fuzzy heuristic filtration"),
            ("Escape", "Clear filtration / Extinguish modals"),
            ("j, down", "Navigate cursor descending"),
            ("k, up", "Navigate cursor ascending"),
            ("ctrl+d", "Navigate descending (10 indices)"),
            ("ctrl+u", "Navigate ascending (10 indices)"),
            ("g", "Traverse to the absolute zenith"),
            ("G", "Traverse to the absolute nadir"),
        ]

        for keys, desc in bindings_info:
            txt = Text()
            txt.append(f"{keys:<20}", style=self.app.theme_colors.get("accent", "blue") + " bold")
            txt.append(" ➜ ", style=self.app.theme_colors.get("muted", "grey"))
            txt.append(desc, style=self.app.theme_colors.get("fg", "white"))
            ol.add_option(Option(txt, disabled=True))

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)


class Shortcut(Label):
    def __init__(self, key_text: str, label: str, action_name: str | None = None, **kwargs: Any) -> None:
        super().__init__(classes="footer-shortcut", **kwargs)
        self.key_text = key_text
        self.label_text = label
        self.action_name = action_name

    def render(self) -> str:
        txt = Text()
        if self.has_class("-active"):
            txt.append(f"[{self.key_text}] ", style=f"bold {self.app.theme_colors.get('bg', '#000')}")
            txt.append(self.label_text, style=f"bold {self.app.theme_colors.get('bg', '#000')}")
        else:
            txt.append(f"[{self.key_text}] ", style=self.app.theme_colors.get("accent", "blue"))
            txt.append(self.label_text, style=self.app.theme_colors.get("fg", "white"))
        return txt

    async def on_click(self) -> None:
        if self.action_name: await self.app.run_action(self.action_name)


class AppFooter(Horizontal):
    def compose(self) -> ComposeResult:
        yield Shortcut("q", "Quit", "quit")
        yield Shortcut("/", "Search", "focus_search")
        yield Shortcut("Enter", "Track", None)
        yield Shortcut("t", "Threshold", "set_threshold")
        yield Shortcut("c", "Toggle CMD", "toggle_cmd")
        yield Shortcut("s", "Sort Mode", "toggle_sort")
        yield Shortcut("?", "Shortcuts", "show_shortcuts")

# =============================================================================
# TRACKER PANOPTICON (Detail View)
# =============================================================================
class ProcessPanopticon(Container):
    def compose(self) -> ComposeResult:
        with Horizontal(id="panopticon-header"):
            yield Label("TRACKED: None", id="panopticon-title")
            yield Label("Status: N/A", id="panopticon-status")
            yield Label("Uptime: 00:00:00", id="panopticon-uptime")
            yield Label("Threshold: None", id="panopticon-threshold")
        
        with Horizontal(id="panopticon-io-row"):
            yield Label("↓ Read: 0 B/s", id="panopticon-io-read")
            yield Label("↑ Write: 0 B/s", id="panopticon-io-write")

        with Horizontal(id="panopticon-graphs-row"):
            with Vertical(id="cpu-container", classes="graph-container"):
                yield Label("CPU Utilization (%):", classes="graph-title")
                yield BrailleGraph(id="panopticon-cpu-graph", anchor_zero=True, min_range=100.0)
            
            with Vertical(id="ram-container", classes="graph-container"):
                yield Label("Memory Utilization Topography (MB):", classes="graph-title")
                yield BrailleGraph(id="panopticon-ram-graph", anchor_zero=True, min_range=50.0)

# =============================================================================
# MAIN APPLICATION: Dusky Monitor
# =============================================================================
class DuskyMonitorApp(App):
    CSS = """
    Screen { background: $background; layout: vertical; }

    #panopticon {
        height: 14; dock: top; display: none;
        border: solid $primary; border-title-color: $primary; border-title-style: bold;
        background: $background 50%; padding: 0 1; margin: 0 0 1 0;
    }
    #panopticon.-active { display: block; }
    
    #panopticon-header { height: 1; align: left middle; }
    #panopticon-header > Label { width: 1fr; color: $foreground; text-style: bold; }
    #panopticon-title { color: $primary; }
    
    #panopticon-io-row { height: 1; align: left middle; margin-top: 1; }
    #panopticon-io-row > Label { width: 1fr; color: $secondary; }
    
    #panopticon-graphs-row { height: 1fr; margin-top: 1; }
    .graph-container { width: 1fr; height: 1fr; }
    #cpu-container { margin-right: 2; }
    .graph-title { color: $secondary; height: 1; margin-bottom: 0; }
    
    /* Dual Braille Engine Orchestration */
    #panopticon-cpu-graph { width: 1fr; height: 1fr; color: $warning; text-style: none; overflow: hidden; }
    #panopticon-ram-graph { width: 1fr; height: 1fr; color: $success; text-style: none; overflow: hidden; }

    #main-box {
        width: 100%; height: 1fr;
        border: solid $primary 50%; border-title-color: $primary; border-title-style: bold; border-title-align: center;
        border-subtitle-color: $primary; border-subtitle-style: bold; border-subtitle-align: right;
        background: transparent; padding: 0 1;
    }

    DataTable { height: 1fr; border: none; background: transparent; scrollbar-size: 0 0; }
    
    #search_input {
        dock: bottom; border: none; border-top: solid $primary 50%;
        background: $primary 10%; color: $foreground;
        display: none; height: 3; margin: 0;
    }
    #search_input.-active { display: block; }
    #search_input:focus { border-top: solid $primary; }

    #footer { 
        height: auto; min-height: 2; dock: bottom; 
        border-top: solid $secondary; padding: 0 2; 
        background: transparent; align: left middle;
    }
    .footer-shortcut { padding: 0 1; background: transparent; }
    .footer-shortcut:hover { text-style: bold; color: $foreground; background: $primary 25%; }

    ShortcutsInfoScreen, SetThresholdScreen { align: center middle; background: rgba(0, 0, 0, 0.75); }
    #shortcuts-dialog, #threshold-dialog { width: 70; height: auto; max-height: 80%; background: $background; border: solid $primary; padding: 1 2; }
    #modal-title { color: $primary; margin-bottom: 1; text-style: bold; border-bottom: solid $secondary; content-align: center middle; width: 100%; }
    #threshold-input { margin-bottom: 1; }
    
    #shortcuts-list { height: 1fr; scrollbar-size: 0 0; background: transparent; border: none; }
    #shortcuts-list > .option-list--option { padding: 0 1; background: transparent; }
    
    .modal-btn-container { width: 100%; height: auto; align: center middle; margin-top: 1; background: transparent; }
    .modal-close-btn { background: $primary; color: $background; text-style: bold; padding: 0 2; width: auto; height: 1; margin: 0 1;}
    .modal-close-btn:hover { background: $foreground; color: $background; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("s", "toggle_sort", "Toggle Sort", priority=True),
        Binding("c", "toggle_cmd", "Toggle Command Mode", priority=True),
        Binding("t", "set_threshold", "Set Threshold", priority=True),
        Binding("/", "focus_search", "Search", priority=True),
        Binding("escape", "clear_search", "Clear Search", priority=True),
        Binding("?", "show_shortcuts", "Shortcuts", priority=True),
        Binding("f1", "show_shortcuts", "Shortcuts", priority=True),
        Binding("ctrl+d", "jump_down", "Jump Down", show=False),
        Binding("ctrl+u", "jump_up", "Jump Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "scroll_top", "Top", show=False),
        Binding("G", "scroll_bottom", "Bottom", show=False),
    ]

    sort_by: reactive[str] = reactive("ram")
    search_query: reactive[str] = reactive("")
    refresh_interval: reactive[float] = reactive(2.0)
    show_full_cmd: reactive[bool] = reactive(False)
    max_count: reactive[int | None] = reactive(None)

    def __init__(self, cli_config: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        
        self.sort_by = cli_config["sort_metric"]
        self.show_full_cmd = cli_config["show_full"]
        self.refresh_interval = cli_config["interval"]
        self.max_count = cli_config["count"]
        self.target_pids = set(cli_config["pids"])
        self.search_query = cli_config["name"]
        
        self.process_cache: dict[int, psutil.Process] = {}
        self.theme_path = Path("~/.config/matugen/generated/dusky_tui.json").expanduser().resolve()
        self.last_theme_mtime: float = 0.0
        
        self.tracked_pid: int | None = None
        
        # Dual-Array State Injection
        self.tracked_ram_history: deque[float] = deque(maxlen=2000)
        self.tracked_cpu_history: deque[float] = deque(maxlen=2000)
        
        self.tracked_last_io: dict[str, float] | None = None
        self.tracked_last_time: float = 0.0
        self.tracked_threshold_mb: float | None = None
        self.last_alert_time: float = 0.0
        
        self._search_task: asyncio.Task | None = None
        
        self.theme_colors = {
            "bg": "#0d141b", "fg": "#dce3ee", "accent": "#9acbff", 
            "error": "#ffb4ab", "warning": "#b9c6ea", "success": "#c2c1f9", "muted": "#404750"
        }

    def compose(self) -> ComposeResult:
        yield ProcessPanopticon(id="panopticon")
        with Vertical(id="main-box"):
            yield DataTable(id="process_table", cursor_type="row")
            yield Input(id="search_input", placeholder="Execute probabilistic nomenclature filtration... (Enter/Esc to extinguish)")
        yield AppFooter(id="footer")

    def apply_theme_to_engine(self) -> None:
        self._theme_toggle = not getattr(self, "_theme_toggle", False)
        theme_name = "dusky_live_A" if self._theme_toggle else "dusky_live_B"

        custom_theme = Theme(
            name=theme_name,
            primary=self.theme_colors.get("accent", "#9acbff"),
            secondary=self.theme_colors.get("muted", "#404750"),
            background=self.theme_colors.get("bg", "#0d141b"),
            surface=self.theme_colors.get("bg", "#0d141b"),
            warning=self.theme_colors.get("warning", "#b9c6ea"),
            error=self.theme_colors.get("error", "#ffb4ab"),
            success=self.theme_colors.get("success", "#c2c1f9"),
            variables={
                "foreground": self.theme_colors.get("fg", "#dce3ee"),
            },
        )
        self.register_theme(custom_theme)
        self.theme = theme_name
        
        self.update_table()
        for shortcut in self.query(Shortcut):
            shortcut.refresh()

    async def watch_theme_file(self) -> None:
        if not self.theme_path.exists(): return
        try:
            stat_info = await asyncio.to_thread(self.theme_path.stat)
            current_mtime = stat_info.st_mtime
            if current_mtime > self.last_theme_mtime:
                self.last_theme_mtime = current_mtime
                def _load_json() -> dict[str, Any]:
                    with open(self.theme_path, "r", encoding="utf-8") as f: return json.load(f)
                try:
                    new_theme = await asyncio.to_thread(_load_json)
                    self.theme_colors.update(new_theme)
                    self.apply_theme_to_engine()
                except Exception: pass
        except OSError: pass

    def on_mount(self) -> None:
        self.query_one("#main-box").border_title = " Dusky Monitor"
        
        table = self.query_one(DataTable)
        table.add_columns(
            "[primary bold]PID[/]",
            "[warning bold]CPU %[/]",
            "[success bold]MEM %[/]",
            "[text bold]RAM (MB)[/]",
            "[secondary bold]TIME[/]",
            "[text bold]COMMAND TRAJECTORY[/]"
        )
        table.focus()
        
        if self.search_query:
            self.query_one("#search_input", Input).add_class("-active")

        if self.theme_path.exists():
            try:
                with open(self.theme_path, "r", encoding="utf-8") as f:
                    self.theme_colors.update(json.load(f))
                self.last_theme_mtime = self.theme_path.stat().st_mtime
            except Exception: pass
            
        self.apply_theme_to_engine()
        self.set_interval(self.refresh_interval, self.update_table)
        self.set_interval(0.5, self.watch_theme_file)

    def fetch_process_data(self) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Runs purely in the background thread. Transitioned to Atomic Syscalls."""
        processes = []
        current_pids = set()
        tracked_data = None
        
        # Micro-optimization: pre-lower the search query string outside the core loop
        search_lower = self.search_query.lower() if self.search_query else None

        # Leveraging `attrs` dictates psutil to aggregate properties at the C-level (stat buffer) simultaneously.
        required_attrs = ['pid', 'name', 'cmdline', 'memory_info', 'memory_percent', 'cpu_times', 'status', 'create_time', 'io_counters']
        
        for proc in psutil.process_iter(attrs=required_attrs):
            try:
                pinfo = proc.info
                pid = pinfo['pid']
                
                if self.target_pids and pid not in self.target_pids:
                    continue
                    
                current_pids.add(pid)
                
                # We specifically execute cpu_percent() directly on the object to ensure non-blocking differential calculation
                if pid not in self.process_cache:
                    self.process_cache[pid] = proc
                    proc.cpu_percent(interval=None)
                    cpu_perc = 0.0
                else:
                    cpu_perc = self.process_cache[pid].cpu_percent(interval=None)

                cmdline = pinfo.get('cmdline') or []
                name = pinfo.get('name') or ""
                mem_info = pinfo.get('memory_info')
                mem_perc = pinfo.get('memory_percent') or 0.0
                cpu_times = pinfo.get('cpu_times')

                full_cmd_str = " ".join(cmdline) if cmdline else f"[{name}]"
                display_cmd = full_cmd_str if self.show_full_cmd else (Path(cmdline[0]).name if cmdline else f"[{name}]")

                score = 0
                if search_lower:
                    # processor=None bypasses redundant internal .lower() casting resulting in massive performance bumps
                    score = fuzz.partial_ratio(search_lower, full_cmd_str.lower(), processor=None)
                    if score < 40: continue

                ram_mb = round(format_bytes_to_mb(mem_info.rss), 1) if mem_info else 0.0
                
                # Decoupled Deep Analysis: Extracts straight from the safe, pre-cached info dictionary
                if self.tracked_pid == pid:
                    io_counters = pinfo.get('io_counters')
                    read_bytes = io_counters.read_bytes if io_counters else 0
                    write_bytes = io_counters.write_bytes if io_counters else 0
                        
                    tracked_data = {
                        "status": pinfo.get('status', 'unknown'),
                        "create_time": pinfo.get('create_time', time.time()),
                        "read_bytes": read_bytes,
                        "write_bytes": write_bytes,
                        "ram_mb": ram_mb,
                        "cpu_percent": cpu_perc
                    }

                processes.append({
                    'pid': pid,
                    'cpu': round(cpu_perc, 1),
                    'mem_perc': round(mem_perc, 1),
                    'ram_mb': ram_mb,
                    'time': format_time(sum(cpu_times[:2])) if cpu_times else "00:00",
                    'cmd': display_cmd,
                    'score': score
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, TypeError):
                continue

        # Natively map C-level dictionary keys for micro-optimized mathematical set subtraction
        dead_pids = self.process_cache.keys() - current_pids
        for pid in dead_pids:
            del self.process_cache[pid]

        return processes, tracked_data

    def sort_data(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def sort_key(item: dict[str, Any]) -> tuple[float, float]:
            primary = item['score'] if self.search_query else 0
            secondary = item['cpu'] if self.sort_by == "cpu" else item['ram_mb']
            return (primary, secondary)
        
        sorted_matrix = sorted(data, key=sort_key, reverse=True)
        return sorted_matrix[:self.max_count] if self.max_count else sorted_matrix

    def update_table(self) -> None:
        self.execute_heuristic_fetch()
        
    @work(thread=True, exclusive=True)
    def execute_heuristic_fetch(self) -> None:
        """
        EXCLUSIVE worker thread: guarantees zero concurrency overlaps and resolves the deadlock.
        """
        try:
            data, tracked_data = self.fetch_process_data()
            sorted_data = self.sort_data(data)
            
            # Pipe pure python dictionaries safely back to the asyncio event loop boundaries
            if tracked_data:
                self.app.call_from_thread(self._update_panopticon_dom, tracked_data)
            elif self.tracked_pid:
                self.app.call_from_thread(self._handle_tracked_pid_death)
                
            self.app.call_from_thread(self._refresh_dom_matrix, sorted_data)
        except Exception:
            pass 

    def _handle_tracked_pid_death(self) -> None:
        """Safely cleans up the UI state from the main event loop if the process ceases."""
        self.tracked_pid = None
        self.query_one("#panopticon").remove_class("-active")

    def _update_panopticon_dom(self, data: dict[str, Any]) -> None:
        """Executes purely on the main UI thread with completely sanitized python primitives."""
        now = time.time()
        uptime_secs = now - data["create_time"]
        
        r_speed, w_speed = 0.0, 0.0
        if self.tracked_last_io and self.tracked_last_time > 0:
            dt = now - self.tracked_last_time
            if dt > 0:
                r_speed = (data["read_bytes"] - self.tracked_last_io['r']) / dt
                w_speed = (data["write_bytes"] - self.tracked_last_io['w']) / dt
                
        self.tracked_last_io = {'r': data["read_bytes"], 'w': data["write_bytes"]}
        self.tracked_last_time = now
        
        # Synchronized Data Ingestion
        self.tracked_ram_history.append(data["ram_mb"])
        self.tracked_cpu_history.append(data["cpu_percent"])

        if self.tracked_threshold_mb and data["ram_mb"] > self.tracked_threshold_mb:
            if now - self.last_alert_time > 15:
                dispatch_notification("Resource Limit Exceeded", f"PID {self.tracked_pid} consumption achieved {data['ram_mb']:.1f} MB!")
                self.last_alert_time = now

        self.query_one("#panopticon-title", Label).update(f"TRACKED PID: [bold]{self.tracked_pid}[/]")
        self.query_one("#panopticon-status", Label).update(f"Status: [bold {self.theme_colors.get('accent')}]{str(data['status']).upper()}[/]")
        self.query_one("#panopticon-uptime", Label).update(f"Uptime: [bold]{format_time(uptime_secs)}[/]")
        
        thresh_str = f"{self.tracked_threshold_mb} MB" if self.tracked_threshold_mb else "Unconstrained"
        self.query_one("#panopticon-threshold", Label).update(f"Threshold Limit: [bold {self.theme_colors.get('error')}]{thresh_str}[/]")
        self.query_one("#panopticon-io-read", Label).update(f"↓ Vel: [bold]{format_speed(r_speed)}[/]")
        self.query_one("#panopticon-io-write", Label).update(f"↑ Vel: [bold]{format_speed(w_speed)}[/]")
        
        # Route dual history matrices into the Braille rendering engine independently
        self.query_one("#panopticon-ram-graph", BrailleGraph).data = list(self.tracked_ram_history)
        self.query_one("#panopticon-cpu-graph", BrailleGraph).data = list(self.tracked_cpu_history)

    def _refresh_dom_matrix(self, sorted_data: list[dict[str, Any]]) -> None:
        """
        Hyper-Optimized DOM injection utilizing the batch_update context manager 
        to eradicate rendering latency and interface flickering.
        """
        # Batching entirely halts internal layout geometry calculations until context completion
        with self.app.batch_update():
            table = self.query_one(DataTable)
            current_row = table.cursor_row if table.row_count > 0 else 0
            
            # Using parameters=False suppresses column regeneration
            table.clear(columns=False) 
            
            for item in sorted_data:
                pid_style = f"bold {self.theme_colors.get('error')}" if item['pid'] == self.tracked_pid else "primary"
                table.add_row(
                    f"[{pid_style}]{item['pid']}[/]",
                    f"[warning]{item['cpu']}%[/]",
                    f"[success]{item['mem_perc']}%[/]",
                    f"[text]{item['ram_mb']}[/]",
                    f"[secondary]{item['time']}[/]",
                    item['cmd'],
                    key=str(item['pid'])
                )
                
            if table.row_count > 0:
                table.move_cursor(row=min(current_row, table.row_count - 1))
                
            mode = "Fuzzy Probabilistic" if self.search_query else self.sort_by.upper()
            cmd_view = "Unabridged Trajectory" if self.show_full_cmd else "Binary Nomenclature"
            cnt = str(self.max_count) if self.max_count else "Unbounded"
            
            self.query_one("#main-box").border_subtitle = (
                f" Heuristic: {mode} | Context: {cmd_view} | Limit: {cnt} | Refresh: {self.refresh_interval}s "
            )

    # =========================================================================
    # EVENT DISPATCH & KEYBIND ARCHITECTURE
    # =========================================================================
    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key:
            try:
                raw_pid = int(event.row_key.value)
                if self.tracked_pid == raw_pid:
                    self.tracked_pid = None
                    self.query_one("#panopticon").remove_class("-active")
                    self.update_table()
                else:
                    self.tracked_pid = raw_pid
                    self.tracked_ram_history.clear()
                    self.tracked_cpu_history.clear()
                    self.tracked_last_io = None
                    self.query_one("#panopticon").add_class("-active")
                    self.update_table() 
            except ValueError:
                pass

    def action_jump_down(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus and table.row_count > 0:
            table.move_cursor(row=min(table.cursor_row + 10, table.row_count - 1))

    def action_jump_up(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus and table.row_count > 0:
            table.move_cursor(row=max(table.cursor_row - 10, 0))

    def action_cursor_down(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus: table.action_cursor_down()

    def action_cursor_up(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus: table.action_cursor_up()
            
    def action_scroll_top(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus and table.row_count > 0: table.move_cursor(row=0)
            
    def action_scroll_bottom(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus and table.row_count > 0: table.move_cursor(row=table.row_count - 1)

    def action_toggle_sort(self) -> None:
        self.sort_by = "cpu" if self.sort_by == "ram" else "ram"
        self.update_table()
        
    def action_toggle_cmd(self) -> None:
        self.show_full_cmd = not self.show_full_cmd
        self.update_table()

    def action_focus_search(self) -> None:
        search_input = self.query_one("#search_input", Input)
        search_input.add_class("-active")
        search_input.focus()

    def action_clear_search(self) -> None:
        search_input = self.query_one("#search_input", Input)
        if search_input.has_class("-active"):
            search_input.remove_class("-active")
            search_input.value = ""
            self.search_query = ""
            self.query_one(DataTable).focus()
            self.update_table()
        elif isinstance(self.screen, ModalScreen):
            self.screen.dismiss(None)

    @work
    async def action_set_threshold(self) -> None:
        if not self.tracked_pid: return
        threshold_value = await self.push_screen_wait(SetThresholdScreen())
        if threshold_value is not None:
            self.tracked_threshold_mb = threshold_value
            self.update_table()

    def action_show_shortcuts(self) -> None:
        if isinstance(self.screen, ShortcutsInfoScreen):
            self.screen.dismiss(None)
        elif not isinstance(self.screen, ModalScreen):
            self.push_screen(ShortcutsInfoScreen())

    @on(Input.Changed, "#search_input")
    async def on_input_changed(self, event: Input.Changed) -> None:
        if self._search_task:
            self._search_task.cancel()
        self._search_task = asyncio.create_task(self._debounced_search(event.value))
        
    async def _debounced_search(self, value: str) -> None:
        try:
            await asyncio.sleep(0.15)
            self.search_query = value
            self.update_table()
        except asyncio.CancelledError:
            pass

    @on(Input.Submitted, "#search_input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_clear_search()

if __name__ == "__main__":
    cli_config = parse_cli_args()
    app = DuskyMonitorApp(cli_config)
    app.run()

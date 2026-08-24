#!/usr/bin/env python3
"""
===============================================================================
DUSKY SCREENTIME: MATUGEN THEMED RICH & FZF DASHBOARD (Python 3.14 Bleeding-Edge)
===============================================================================
Ultra-fast, lightweight screentime visualization engine featuring:
1. Full-bleed Live Rich Terminal Dashboard with locked bottom footer
2. Dual-mode Live navigation: Dashboard Overview & App Details Breakdown
3. Single-instance Live lifecycle (ZERO TTY termios deadlock or screen flashes)
4. Instant 1..5 period tab switching in < 1ms
5. Strict TTY ECHO suppression (ZERO mouse/key/number leakage to stdout)
6. Robust ANSI sequence buffer parser for Arrow keys, Mouse Wheel & Vim controls
7. Smart Yesterday fallback to most recent recorded date
8. Dynamic Truecolor Matugen color integration (~/.config/matugen/generated/dusky_tui.json)
9. Theme-aware FZF explorer & ANSI-clean preview renderer
"""

from __future__ import annotations

import fcntl
import json
import os
import select
import socket
import subprocess
import sys
import termios
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Ensure local imports work
SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from desktop_resolver import DesktopResolver
except ImportError:
    try:
        from python.desktop_resolver import AppInfo, DesktopResolver
    except ImportError:
        DesktopResolver = None  # type: ignore[misc, assignment]

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

DATA_FILE = Path("~/.local/share/dusky/screentime/screentime_data.json").expanduser()
THEME_FILE = Path("~/.config/matugen/generated/dusky_tui.json").expanduser()
LOG_FILE = Path("~/.local/share/dusky/screentime/screentime_error.log").expanduser()

DEFAULT_COLORS: dict[str, str] = {
    "bg": "#0e1416",
    "fg": "#dee3e5",
    "accent": "#82d3e2",
    "error": "#ffb4ab",
    "warning": "#b1cbd0",
    "success": "#bbc5ea",
    "muted": "#3f484a",
    "cursor_bg": "#1c2528",
}

PERIOD_KEYS: dict[str, str] = {
    "period_today": "today",
    "period_yesterday": "yesterday",
    "period_week": "week",
    "period_month": "month",
    "period_all": "all",
}

PERIOD_LIST: list[str] = ["today", "yesterday", "week", "month", "all"]


def get_cycled_period(current_key: str, step: int = 1) -> str:
    """Cycle forward (+1) or backward (-1) through PERIOD_LIST."""
    try:
        idx = PERIOD_LIST.index(current_key)
    except ValueError:
        idx = 0
    return PERIOD_LIST[(idx + step) % len(PERIOD_LIST)]

# Mouse / cursor control (written only when Live does not own the TTY)
_MOUSE_ON = "\x1b[?1000h\x1b[?1006h"
_MOUSE_OFF = "\x1b[?1000l\x1b[?1006l"
_CURSOR_HIDE = "\x1b[?25l"
_CURSOR_SHOW = "\x1b[?25h"
_CLEAR_HOME = "\x1b[2J\x1b[3J\x1b[H"
_ANSI_RESET = "\x1b[0m"


# =============================================================================
# LOGGING / THEME / DATA HELPERS
# =============================================================================
def log_error(err_msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {err_msg}\n")
    except Exception:
        pass


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    """Never print thread crashes onto the Live TTY."""
    try:
        tb = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        name = getattr(args.thread, "name", "?")
        log_error(f"threading.excepthook in {name}: {args.exc_type} {args.exc_value}\n{tb}")
    except Exception:
        pass


threading.excepthook = _thread_excepthook


def _safe_color(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    v = value.strip()
    if not v or "[" in v or "]" in v or "\n" in v or "\x1b" in v:
        return fallback
    if v.startswith("#"):
        hexpart = v[1:]
        if len(hexpart) in (3, 6, 8) and all(c in "0123456789abcdefABCDEF" for c in hexpart):
            return v
        return fallback
    return v


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    hex_clean = hex_str.strip().lstrip("#")
    if len(hex_clean) == 3:
        hex_clean = "".join(c * 2 for c in hex_clean)
    if len(hex_clean) >= 6:
        try:
            return int(hex_clean[0:2], 16), int(hex_clean[2:4], 16), int(hex_clean[4:6], 16)
        except ValueError:
            pass
    return 222, 227, 229


def ansi_color(hex_str: str, bold: bool = False) -> str:
    r, g, b = hex_to_rgb(hex_str)
    prefix = "\033[1;" if bold else "\033["
    return f"{prefix}38;2;{r};{g};{b}m"


def load_theme_colors() -> dict[str, str]:
    colors = DEFAULT_COLORS.copy()
    if THEME_FILE.exists():
        try:
            with open(THEME_FILE, "r", encoding="utf-8") as f:
                user_colors = json.load(f)
                if isinstance(user_colors, dict):
                    for key, fallback in DEFAULT_COLORS.items():
                        if key in user_colors:
                            colors[key] = _safe_color(user_colors[key], fallback)
                    for key, val in user_colors.items():
                        if key not in colors:
                            colors[key] = _safe_color(val, DEFAULT_COLORS["fg"])
        except Exception as e:
            log_error(f"load_theme_colors error: {e}")
    return colors


def load_screentime_data() -> dict[str, dict[str, Any]]:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            log_error(f"load_screentime_data error: {e}")
    return {}


def get_active_hypr_window() -> tuple[str, str]:
    """Retrieve active Hyprland window class and title via Unix domain socket."""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")

    sock_path: Path | None = None
    if xdg_runtime and sig:
        p = Path(xdg_runtime) / "hypr" / sig / ".socket.sock"
        if p.exists():
            sock_path = p

    if not sock_path:
        base_dirs: list[Path] = []
        if xdg_runtime:
            base_dirs.append(Path(xdg_runtime) / "hypr")
        base_dirs.append(Path("/tmp/hypr"))

        candidates: list[tuple[float, Path]] = []
        for bd in base_dirs:
            if bd.exists() and bd.is_dir():
                try:
                    for sdir in bd.iterdir():
                        if sdir.is_dir():
                            sp = sdir / ".socket.sock"
                            if sp.exists():
                                try:
                                    candidates.append((sp.stat().st_mtime, sp))
                                except OSError:
                                    pass
                except Exception:
                    pass

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            sock_path = candidates[0][1]

    if sock_path:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.05)
                s.connect(str(sock_path))
                s.sendall(b"j/activewindow")
                response = bytearray()
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
                resp_str = response.decode("utf-8", errors="ignore")
                data = json.loads(resp_str)
                if isinstance(data, dict):
                    return str(data.get("class", "")).strip(), str(data.get("title", "")).strip()
        except Exception as e:
            log_error(f"get_active_hypr_window socket error: {e}")

    return "", ""


def simplify_category(cat: str) -> str:
    if not isinstance(cat, str):
        return "System"
    match cat:
        case "Terminal & Shell" | "TerminalEmulator":
            return "Terminal"
        case "Web Browser":
            return "Browser"
        case "Audio & Video" | "AudioVideo" | "Multimedia player":
            return "Media"
        case "Agentic Platform":
            return "AI"
        case "Development":
            return "Dev"
        case "Utilities" | "System" | "System Controls" | "System Settings":
            return "System"
        case "Virtual machine viewer/manager" | "Virtual Machine":
            return "VM"
        case "Office":
            return "Office"
        case "Gaming" | "Game":
            return "Gaming"
        case "Graphics":
            return "Graphics"
        case _:
            if len(cat) > 15:
                return f"{cat[:12]}..."
            return cat


def format_duration(seconds: int | float) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return "0s"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    match (h > 0, m > 0):
        case (True, _):
            return f"{h}h {m:02d}m {s:02d}s"
        case (False, True):
            return f"{m}m {s:02d}s"
        case _:
            return f"{s}s"


def make_bar_text(
    percent: float,
    colors: dict[str, str],
    is_active: bool = False,
    is_cursor: bool = False,
    width: int = 16,
) -> Text:
    percent = max(0.0, min(100.0, float(percent)))
    filled = int(round((percent / 100.0) * width))
    filled = max(0, min(width, filled))
    empty = width - filled

    accent = colors.get("accent", "#82d3e2")
    success = colors.get("success", "#bbc5ea")
    muted = colors.get("muted", "#3f484a")

    txt = Text()
    bar_color = success if is_active else accent
    txt.append("━" * filled, style=f"bold {bar_color}")
    txt.append("─" * empty, style=f"dim {muted}")
    return txt


def aggregate_by_range(
    raw_data: dict[str, dict[str, Any]], range_key: str
) -> tuple[dict[str, dict[str, Any]], int, str]:
    today_date = datetime.now()
    today_str = today_date.strftime("%Y-%m-%d")
    yesterday_str = (today_date - timedelta(days=1)).strftime("%Y-%m-%d")

    target_days: list[str] = []
    display_label = ""

    match range_key:
        case "today":
            target_days = [today_str]
            display_label = "Today"
        case "yesterday":
            if yesterday_str in raw_data:
                target_days = [yesterday_str]
                display_label = "Yesterday"
            else:
                past_dates = sorted([d for d in raw_data.keys() if d < today_str], reverse=True)
                if past_dates:
                    target_days = [past_dates[0]]
                    display_label = f"Yesterday ({past_dates[0]})"
                else:
                    target_days = [yesterday_str]
                    display_label = "Yesterday"
        case "week":
            target_days = [
                (today_date - timedelta(days=d)).strftime("%Y-%m-%d")
                for d in range(7)
            ]
            display_label = "Past 7 Days"
        case "month":
            target_days = [
                (today_date - timedelta(days=d)).strftime("%Y-%m-%d")
                for d in range(30)
            ]
            display_label = "Past 30 Days"
        case _:
            target_days = sorted(raw_data.keys(), reverse=True)
            display_label = "All Time"

    agg: dict[str, dict[str, Any]] = {}
    total_time = 0

    # Process in reverse chronological order so most recent app metadata is retained
    for day in sorted(target_days, reverse=True):
        if day not in raw_data or not isinstance(raw_data[day], dict):
            continue
        for cls, info in raw_data[day].items():
            if not isinstance(info, dict):
                continue
            dur = info.get("duration", 0)
            if not isinstance(dur, (int, float)) or dur <= 0:
                continue
            dur = int(dur)
            if cls not in agg:
                agg[cls] = {
                    "name": str(info.get("name", cls)),
                    "category": str(info.get("category", "Application")),
                    "icon": str(info.get("icon", "")),
                    "duration": 0,
                    "sessions": 0,
                    "titles": {},
                }
            agg[cls]["duration"] += dur
            agg[cls]["sessions"] += int(info.get("sessions", 1))
            total_time += dur

            titles_dict = info.get("titles")
            if isinstance(titles_dict, dict):
                for t_title, t_dur in titles_dict.items():
                    if isinstance(t_dur, (int, float)) and t_dur > 0:
                        agg[cls]["titles"][str(t_title)] = (
                            agg[cls]["titles"].get(str(t_title), 0) + int(t_dur)
                        )

    return agg, total_time, display_label


# =============================================================================
# COLOR-CODED RICH FZF PREVIEW RENDERER (Clean Markup, Zero Raw ANSI Escape Leaks)
# =============================================================================
def render_fzf_preview(app_class: str, range_key: str = "today") -> None:
    console = Console(
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        soft_wrap=False,
    )
    colors = load_theme_colors()
    raw_data = load_screentime_data()
    agg, total_time, r_label = aggregate_by_range(raw_data, range_key)

    app_class_clean = app_class.strip()
    target_info: dict[str, Any] | None = None
    target_class = app_class_clean

    if app_class_clean in agg:
        target_info = agg[app_class_clean]
    else:
        # Case-insensitive and name fallback lookup
        for cls, info in agg.items():
            if cls.lower() == app_class_clean.lower() or info.get("name", "").lower() == app_class_clean.lower():
                target_info = info
                target_class = cls
                break

    accent = colors.get("accent", "#82d3e2")
    success = colors.get("success", "#bbc5ea")
    warning = colors.get("warning", "#b1cbd0")
    fg = colors.get("fg", "#dee3e5")
    muted = colors.get("muted", "#3f484a")
    error = colors.get("error", "#ffb4ab")

    if not target_info:
        console.print(f"\n[bold {error}]✖ No screentime data found for:[/] [bold {fg}]{app_class_clean}[/] [dim]({r_label})[/]\n")
        return

    name = target_info.get("name", target_class)
    cat = simplify_category(target_info.get("category", "Application"))
    icon = target_info.get("icon", "")
    dur = target_info.get("duration", 0)
    sessions = target_info.get("sessions", 1)
    share = (dur / total_time * 100.0) if total_time > 0 else 0.0

    # Header Card
    console.print()
    console.print(f"[bold {accent}]󱎫 {name}[/]  [dim {warning}]({cat})[/]")
    console.print(
        f"[dim {muted}]Class:[/] [{fg}]{target_class}[/] [dim {muted}]│[/] "
        f"[dim {muted}]Icon:[/] [bold {success}]{icon or 'application'}[/] [dim {muted}]│[/] "
        f"[dim {muted}]Period:[/] [bold {warning}]{r_label}[/]"
    )
    console.print(f"[dim {muted}]────────────────────────────────────────────────────────[/]")
    console.print(
        f"[dim {muted}]Time:[/] [bold {success}]{format_duration(dur)}[/]  "
        f"[dim {muted}]│[/]  [dim {muted}]Share:[/] [bold {accent}]{share:.1f}%[/]  "
        f"[dim {muted}]│[/]  [dim {muted}]Sessions:[/] [bold {fg}]{sessions}[/]"
    )
    console.print(f"[dim {muted}]────────────────────────────────────────────────────────[/]")
    console.print(f"\n[bold {accent}]󰏖 Window Title & Document Breakdown:[/] [dim {muted}]({len(target_info.get('titles', {}))} entries)[/]\n")

    table = Table(
        box=None,
        show_header=True,
        header_style=f"bold {accent}",
        expand=True,
        pad_edge=False,
    )
    table.add_column("Duration", style=f"bold {success}", min_width=12, max_width=12, justify="right", no_wrap=True)
    table.add_column("Share", style=f"bold {accent}", min_width=7, max_width=7, justify="right", no_wrap=True)
    table.add_column("Window Title / Document", style=f"{fg}", ratio=1, overflow="ellipsis", no_wrap=True)

    titles_sorted = sorted(
        target_info.get("titles", {}).items(), key=lambda x: x[1], reverse=True
    )

    if titles_sorted:
        for t_title, t_dur in titles_sorted:
            t_share = (t_dur / dur * 100.0) if dur > 0 else 0.0
            table.add_row(format_duration(t_dur), f"{t_share:5.1f}%", str(t_title))
    else:
        table.add_row("-", "0.0%", "No detailed window titles recorded")

    console.print(table)


# =============================================================================
# COLOR-CODED INTERACTIVE FZF EXPLORER MODE
# =============================================================================
def run_fzf_explorer(range_key: str = "today") -> None:
    raw_data = load_screentime_data()
    agg, total_time, r_label = aggregate_by_range(raw_data, range_key)
    colors = load_theme_colors()

    if not agg:
        print(f"[!] No screentime data available for the selected period ({range_key}).")
        return

    accent_c = ansi_color(colors.get("accent", "#82d3e2"), bold=True)
    success_c = ansi_color(colors.get("success", "#bbc5ea"), bold=True)
    warning_c = ansi_color(colors.get("warning", "#b1cbd0"))
    fg_c = ansi_color(colors.get("fg", "#dee3e5"))
    muted_c = ansi_color(colors.get("muted", "#3f484a"))

    sorted_apps = sorted(agg.items(), key=lambda x: x[1]["duration"], reverse=True)

    lines = []
    for cls, info in sorted_apps:
        dur = info["duration"]
        share = (dur / total_time * 100.0) if total_time > 0 else 0.0
        name = info.get("name", cls)
        cat = simplify_category(info.get("category", "Application"))

        disp = (
            f"{accent_c}{name:<26}{_ANSI_RESET} "
            f"{muted_c}│{_ANSI_RESET} {success_c}{format_duration(dur):<10}{_ANSI_RESET} "
            f"{muted_c}│{_ANSI_RESET} {warning_c}{share:5.1f}%{_ANSI_RESET} "
            f"{muted_c}│{_ANSI_RESET} {fg_c}{cat:<12}{_ANSI_RESET} "
            f"{muted_c}│{_ANSI_RESET} {cls}"
        )
        lines.append(disp)

    script_path = str(Path(__file__).resolve())
    preview_cmd = f'python3 "{script_path}" --preview {{5}} {range_key}'

    visual_header = (
        f" {accent_c}{'APPLICATION':<26}{_ANSI_RESET} "
        f"{muted_c}│{_ANSI_RESET} {accent_c}{'TIME':<10}{_ANSI_RESET} "
        f"{muted_c}│{_ANSI_RESET} {accent_c}{'SHARE':<6}{_ANSI_RESET} "
        f"{muted_c}│{_ANSI_RESET} {accent_c}{'CATEGORY':<12}{_ANSI_RESET}"
    )

    fzf_cmd = [
        "fzf",
        "--ansi",
        "--delimiter=│",
        "--with-nth=1,2,3,4",
        "--no-hscroll",
        "--highlight-line",
        "--prompt= 󱎫 Screentime ❯ ",
        "--pointer=❯ ",
        "--marker=✔ ",
        "--layout=reverse",
        "--border=rounded",
        f"--border-label= 󱎫 Dusky Screentime Explorer ({r_label}) [Alt+C: Copy Summary] ",
        "--border-label-pos=3",
        "--info=hidden",
        f"--header={visual_header}",
        "--header-first",
        f"--color=bg+:{colors.get('muted', '#3f484a')},bg:{colors.get('bg', '#0e1416')},spinner:{colors.get('accent', '#82d3e2')}",
        f"--color=fg:{colors.get('fg', '#dee3e5')},fg+:{colors.get('fg', '#dee3e5')},header:{colors.get('accent', '#82d3e2')},info:{colors.get('accent', '#82d3e2')}",
        f"--color=pointer:{colors.get('success', '#bbc5ea')},marker:{colors.get('success', '#bbc5ea')},prompt:{colors.get('accent', '#82d3e2')}",
        f"--color=hl:{colors.get('accent', '#82d3e2')},hl+:{colors.get('accent', '#82d3e2')},border:{colors.get('muted', '#3f484a')},label:{colors.get('accent', '#82d3e2')}",
        f"--preview={preview_cmd}",
        "--preview-window=right,50%,border-left,wrap",
        "--bind=alt-c:execute-silent(echo {1} {2} | wl-copy)+change-prompt( 󱎫 Copied Summary! ❯ )",
    ]

    input_data = "\n".join(lines).encode("utf-8")
    try:
        proc = subprocess.run(fzf_cmd, input=input_data, capture_output=True)
        if proc.returncode == 0 and proc.stdout:
            _ = proc.stdout.decode("utf-8").strip()
    except Exception as e:
        log_error(f"fzf error: {e}")


# =============================================================================
# LIVE RICH TERMINAL DASHBOARD LAYOUTS (Overview & App Details)
# =============================================================================
def render_dashboard_layout(
    range_key: str,
    colors: dict[str, str],
    scroll_offset: int,
    cursor_idx: int,
    console_height: int,
    raw_data: dict[str, dict[str, Any]] | None = None,
    active_window: tuple[str, str] | None = None,
) -> tuple[Panel, int, int, int]:
    try:
        return _render_dashboard_layout_impl(
            range_key, colors, scroll_offset, cursor_idx, console_height, raw_data, active_window
        )
    except Exception as e:
        log_error(f"render_dashboard_layout error: {e}\n{traceback.format_exc()}")
        err = Panel(
            Text(f"Render error (see log). Period={range_key}: {e}", style="bold red"),
            border_style="red",
            expand=True,
            height=max(8, console_height or 24),
        )
        return err, scroll_offset, cursor_idx, 0


def _render_dashboard_layout_impl(
    range_key: str,
    colors: dict[str, str],
    scroll_offset: int,
    cursor_idx: int,
    console_height: int,
    raw_data: dict[str, dict[str, Any]] | None,
    active_window: tuple[str, str] | None,
) -> tuple[Panel, int, int, int]:
    if raw_data is None:
        raw_data = load_screentime_data()
    agg, total_time, r_name = aggregate_by_range(raw_data, range_key)
    if active_window is None:
        active_cls, _active_title = get_active_hypr_window()
    else:
        active_cls, _active_title = active_window

    console_height = max(8, int(console_height or 24))

    accent = colors.get("accent", "#82d3e2")
    success = colors.get("success", "#bbc5ea")
    warning = colors.get("warning", "#b1cbd0")
    fg = colors.get("fg", "#dee3e5")
    muted = colors.get("muted", "#3f484a")
    cursor_bg = colors.get("cursor_bg", "#1c2528")

    # Period tab indicator bar
    period_tabs = Text(overflow="ellipsis", no_wrap=True)
    period_tabs.append("  ", style=f"dim {muted}")
    tab_defs = [
        ("1", "today", "Today"),
        ("2", "yesterday", "Yesterday"),
        ("3", "week", "7 Days"),
        ("4", "month", "30 Days"),
        ("5", "all", "All Time"),
    ]
    for key_num, key_id, label in tab_defs:
        if key_id == range_key:
            period_tabs.append(f" {key_num}:{label} ", style=f"bold {accent} on {cursor_bg}")
        else:
            period_tabs.append(f" {key_num}:{label} ", style=f"dim {fg}")
        period_tabs.append(" ", style=f"dim {muted}")

    header_text = Text(overflow="ellipsis", no_wrap=True)
    header_text.append(" 󱎫 Dusky Screentime ", style=f"bold {accent}")
    header_text.append(f"({r_name})", style=f"bold {warning}")
    header_text.append("  Total: ", style=f"{fg}")
    header_text.append(f"{format_duration(total_time)}", style=f"bold {success}")
    header_text.append("  Apps: ", style=f"{fg}")
    header_text.append(f"{len(agg)}", style=f"bold {fg}")

    if active_cls:
        header_text.append("   ▶ ACTIVE: ", style=f"bold {success}")
        header_text.append(f"{active_cls}", style=f"bold {success}")
    else:
        header_text.append("   ▶ ACTIVE: ", style=f"dim {muted}")
        header_text.append("idle", style=f"dim {muted}")

    table = Table(box=None, expand=True, show_header=True, header_style=f"bold {accent}")
    table.add_column("", width=2, justify="center", no_wrap=True)
    table.add_column("Application & Category", ratio=3, overflow="ellipsis", no_wrap=True)
    table.add_column("Time", min_width=12, max_width=12, justify="right", no_wrap=True)
    table.add_column("Share", min_width=7, max_width=7, justify="right", no_wrap=True)
    table.add_column("Usage Bar", ratio=2, no_wrap=True)
    table.add_column("", width=1, justify="center", no_wrap=True)

    sorted_apps = sorted(agg.items(), key=lambda x: x[1]["duration"], reverse=True)
    total_apps = len(sorted_apps)

    if sorted_apps and sorted_apps[0][1]["duration"] > 0:
        max_dur = max(1, sorted_apps[0][1]["duration"])
    else:
        max_dur = 1

    visible_rows = max(3, console_height - 7)
    max_scroll = max(0, total_apps - visible_rows)

    if total_apps > 0:
        cursor_idx = max(0, min(cursor_idx, total_apps - 1))
        if cursor_idx < scroll_offset:
            scroll_offset = cursor_idx
        elif cursor_idx >= scroll_offset + visible_rows:
            scroll_offset = cursor_idx - visible_rows + 1
    else:
        cursor_idx = 0
        scroll_offset = 0

    scroll_offset = max(0, min(scroll_offset, max_scroll))
    page_apps = sorted_apps[scroll_offset : scroll_offset + visible_rows]

    if total_apps > visible_rows:
        thumb_h = max(1, int(round((visible_rows / total_apps) * visible_rows)))
        max_thumb_top = max(0, visible_rows - thumb_h)
        if max_scroll > 0:
            thumb_top = int(round((scroll_offset / max_scroll) * max_thumb_top))
        else:
            thumb_top = 0
    else:
        thumb_h = visible_rows
        thumb_top = 0

    for idx_in_page, (cls, info) in enumerate(page_apps):
        global_idx = scroll_offset + idx_in_page
        dur = info["duration"]
        share = (dur / total_time * 100.0) if total_time > 0 else 0.0
        is_active = (cls.lower() == active_cls.lower() and active_cls != "")
        is_cursor = (global_idx == cursor_idx)

        if is_active:
            status_cell = Text("●", style=f"bold {success}")
        elif is_cursor:
            status_cell = Text("▸", style=f"bold {accent}")
        else:
            status_cell = Text("·", style=f"dim {muted}")

        app_name = info.get("name", cls)
        cat = simplify_category(info.get("category", "Application"))

        bg_style = f"on {cursor_bg}" if is_cursor else ""

        app_cell = Text()
        if is_active:
            app_cell.append(f"{app_name}", style=f"bold {success}")
            app_cell.append(f"  ({cat})", style=f"dim {success}")
        elif is_cursor:
            app_cell.append(f"{app_name}", style=f"bold {fg}")
            app_cell.append(f"  ({cat})", style=f"dim {warning}")
        else:
            app_cell.append(f"{app_name}", style=f"bold {fg}" if global_idx == 0 else f"{fg}")
            app_cell.append(f"  ({cat})", style=f"dim {warning}")

        dur_cell = Text(format_duration(dur), style=f"bold {success}" if is_active or is_cursor or global_idx == 0 else f"{success}")
        share_cell = Text(f"{share:.1f}%", style=f"bold {success}" if is_active else f"{accent}")
        bar_cell = make_bar_text((dur / max_dur) * 100.0, colors, is_active, is_cursor, width=16)

        if total_apps > visible_rows:
            if thumb_top <= idx_in_page < thumb_top + thumb_h:
                scroll_cell = Text("┃", style=f"bold {accent}")
            else:
                scroll_cell = Text("│", style=f"dim {muted}")
        else:
            scroll_cell = Text("")

        if bg_style:
            status_cell.stylize(bg_style)
            app_cell.stylize(bg_style)
            dur_cell.stylize(bg_style)
            share_cell.stylize(bg_style)
            bar_cell.stylize(bg_style)

        table.add_row(status_cell, app_cell, dur_cell, share_cell, bar_cell, scroll_cell)

    rows_rendered = len(page_apps)
    if rows_rendered < visible_rows:
        for _ in range(visible_rows - rows_rendered):
            table.add_row("", "", "", "", "", "")

    footer_text = Text(overflow="ellipsis", no_wrap=True)
    footer_text.append(" Controls: ", style=f"bold {muted}")
    footer_text.append("[1-5/Tab] Period   [j/k/↑/↓] Move   [Ctrl-D/Ctrl-U] Page   ", style=f"{fg}")
    footer_text.append("[Enter] Details   [F] FZF   [Q] Quit", style=f"bold {accent}")

    layout_group = Table.grid(expand=True)
    layout_group.add_row(period_tabs)
    layout_group.add_row(header_text)
    layout_group.add_row(table)
    layout_group.add_row(footer_text)

    if total_apps > visible_rows:
        subtitle_str = (
            f"[bold {accent}]Item {cursor_idx + 1} of {total_apps}[/] "
            f"[dim]({scroll_offset + 1}–{min(scroll_offset + visible_rows, total_apps)} visible | j/k/Wheel to scroll)[/dim]"
        )
    elif total_apps > 0:
        subtitle_str = f"[bold {accent}]Item {cursor_idx + 1} of {total_apps}[/] [dim](Live Dashboard)[/dim]"
    else:
        safe_name = str(r_name).replace("[", "").replace("]", "")
        subtitle_str = f"[bold {warning}]No screentime data recorded for {safe_name}[/]"

    panel = Panel(
        layout_group,
        border_style=f"{accent}",
        subtitle=subtitle_str,
        expand=True,
        height=console_height,
    )
    return panel, scroll_offset, cursor_idx, max_scroll


def render_details_layout(
    target_app_class: str,
    range_key: str,
    colors: dict[str, str],
    details_scroll: int,
    details_cursor: int,
    console_height: int,
    raw_data: dict[str, dict[str, Any]] | None = None,
    active_window: tuple[str, str] | None = None,
) -> tuple[Panel, int, int, int]:
    """
    Renders the in-TUI Deep Dive Details layout for a specific application.
    Supports full scroll navigation through all recorded window titles and documents.
    """
    if raw_data is None:
        raw_data = load_screentime_data()
    agg, total_time, r_name = aggregate_by_range(raw_data, range_key)
    if active_window is None:
        active_cls, _active_title = get_active_hypr_window()
    else:
        active_cls, _active_title = active_window

    console_height = max(8, int(console_height or 24))

    accent = colors.get("accent", "#82d3e2")
    success = colors.get("success", "#bbc5ea")
    warning = colors.get("warning", "#b1cbd0")
    fg = colors.get("fg", "#dee3e5")
    muted = colors.get("muted", "#3f484a")
    cursor_bg = colors.get("cursor_bg", "#1c2528")

    # Resolve target info
    app_class_clean = target_app_class.strip()
    target_info = agg.get(app_class_clean)
    if not target_info:
        for cls, info in agg.items():
            if cls.lower() == app_class_clean.lower() or info.get("name", "").lower() == app_class_clean.lower():
                target_info = info
                app_class_clean = cls
                break

    # Navigation / Period Bar
    period_tabs = Text(overflow="ellipsis", no_wrap=True)
    period_tabs.append("  ", style=f"dim {muted}")
    tab_defs = [
        ("1", "today", "Today"),
        ("2", "yesterday", "Yesterday"),
        ("3", "week", "7 Days"),
        ("4", "month", "30 Days"),
        ("5", "all", "All Time"),
    ]
    for key_num, key_id, label in tab_defs:
        if key_id == range_key:
            period_tabs.append(f" {key_num}:{label} ", style=f"bold {accent} on {cursor_bg}")
        else:
            period_tabs.append(f" {key_num}:{label} ", style=f"dim {fg}")
        period_tabs.append(" ", style=f"dim {muted}")

    if not target_info:
        empty_grid = Table.grid(expand=True)
        empty_grid.add_row(period_tabs)
        empty_grid.add_row(Text(f"\n  ✖ No screentime data recorded for '{app_class_clean}' in period: {r_name}\n", style=f"bold {warning}"))
        empty_grid.add_row(Text("  [Esc/Enter/q] Back to Dashboard   [1-5] Switch Period", style=f"bold {accent}"))
        panel = Panel(
            empty_grid,
            border_style=f"{warning}",
            subtitle=f"[bold {warning}]Empty Details ({r_name})[/]",
            expand=True,
            height=console_height,
        )
        return panel, 0, 0, 0

    name = target_info.get("name", app_class_clean)
    cat = simplify_category(target_info.get("category", "Application"))
    icon = target_info.get("icon", "")
    dur = target_info.get("duration", 0)
    sessions = target_info.get("sessions", 1)
    share = (dur / total_time * 100.0) if total_time > 0 else 0.0
    is_active = (app_class_clean.lower() == active_cls.lower() and active_cls != "")

    # Top metadata block
    meta_line = Text(overflow="ellipsis", no_wrap=True)
    meta_line.append(" 󱎫 ", style=f"bold {accent}")
    meta_line.append(f"{name}", style=f"bold {success}" if is_active else f"bold {fg}")
    meta_line.append(f" ({cat})", style=f"dim {warning}")
    meta_line.append("   Class: ", style=f"dim {muted}")
    meta_line.append(f"{app_class_clean}", style=f"{fg}")
    meta_line.append("   Icon: ", style=f"dim {muted}")
    meta_line.append(f"{icon or 'default'}", style=f"{success}")

    stats_line = Text(overflow="ellipsis", no_wrap=True)
    stats_line.append("   Total Time: ", style=f"{fg}")
    stats_line.append(f"{format_duration(dur)}", style=f"bold {success}")
    stats_line.append("   Share: ", style=f"{fg}")
    stats_line.append(f"{share:.1f}%", style=f"bold {accent}")
    stats_line.append("   Sessions: ", style=f"{fg}")
    stats_line.append(f"{sessions}", style=f"bold {fg}")
    if is_active:
        stats_line.append("   [ACTIVE NOW]", style=f"bold {success}")

    # Title breakdown table
    titles_sorted = sorted(
        target_info.get("titles", {}).items(), key=lambda x: x[1], reverse=True
    )
    total_titles = len(titles_sorted)

    table = Table(box=None, expand=True, show_header=True, header_style=f"bold {accent}")
    table.add_column("", width=2, justify="center", no_wrap=True)
    table.add_column("Window Title / Document", ratio=4, overflow="ellipsis", no_wrap=True)
    table.add_column("Duration", min_width=12, max_width=12, justify="right", no_wrap=True)
    table.add_column("Share", min_width=7, max_width=7, justify="right", no_wrap=True)
    table.add_column("Usage Bar", ratio=2, no_wrap=True)
    table.add_column("", width=1, justify="center", no_wrap=True)

    # Calculate layout space: console_height minus header lines and footer
    visible_rows = max(3, console_height - 9)
    max_scroll = max(0, total_titles - visible_rows)

    if total_titles > 0:
        details_cursor = max(0, min(details_cursor, total_titles - 1))
        if details_cursor < details_scroll:
            details_scroll = details_cursor
        elif details_cursor >= details_scroll + visible_rows:
            details_scroll = details_cursor - visible_rows + 1
    else:
        details_cursor = 0
        details_scroll = 0

    details_scroll = max(0, min(details_scroll, max_scroll))
    page_titles = titles_sorted[details_scroll : details_scroll + visible_rows]

    if total_titles > visible_rows:
        thumb_h = max(1, int(round((visible_rows / total_titles) * visible_rows)))
        max_thumb_top = max(0, visible_rows - thumb_h)
        if max_scroll > 0:
            thumb_top = int(round((details_scroll / max_scroll) * max_thumb_top))
        else:
            thumb_top = 0
    else:
        thumb_h = visible_rows
        thumb_top = 0

    for idx_in_page, (t_title, t_dur) in enumerate(page_titles):
        global_idx = details_scroll + idx_in_page
        t_share = (t_dur / dur * 100.0) if dur > 0 else 0.0
        is_cursor = (global_idx == details_cursor)

        status_cell = Text("▸" if is_cursor else "·", style=f"bold {accent}" if is_cursor else f"dim {muted}")
        title_cell = Text(str(t_title), style=f"bold {fg}" if is_cursor or global_idx == 0 else f"{fg}")
        dur_cell = Text(format_duration(t_dur), style=f"bold {success}" if is_cursor or global_idx == 0 else f"{success}")
        share_cell = Text(f"{t_share:.1f}%", style=f"bold {accent}" if is_cursor else f"{accent}")
        bar_cell = make_bar_text(t_share, colors, is_active=False, is_cursor=is_cursor, width=16)

        if total_titles > visible_rows:
            if thumb_top <= idx_in_page < thumb_top + thumb_h:
                scroll_cell = Text("┃", style=f"bold {accent}")
            else:
                scroll_cell = Text("│", style=f"dim {muted}")
        else:
            scroll_cell = Text("")

        bg_style = f"on {cursor_bg}" if is_cursor else ""
        if bg_style:
            status_cell.stylize(bg_style)
            title_cell.stylize(bg_style)
            dur_cell.stylize(bg_style)
            share_cell.stylize(bg_style)
            bar_cell.stylize(bg_style)

        table.add_row(status_cell, title_cell, dur_cell, share_cell, bar_cell, scroll_cell)

    rows_rendered = len(page_titles)
    if rows_rendered < visible_rows:
        for _ in range(visible_rows - rows_rendered):
            table.add_row("", "", "", "", "", "")

    divider = Text(" ─" * 40, style=f"dim {muted}", overflow="ellipsis", no_wrap=True)

    footer_text = Text(overflow="ellipsis", no_wrap=True)
    footer_text.append(" Controls: ", style=f"bold {muted}")
    footer_text.append("[Esc/Enter/q] Back   [1-5/Tab] Period   [j/k/↑/↓] Scroll   [Ctrl-D/U] Page   ", style=f"{fg}")
    footer_text.append("[F] FZF Explorer", style=f"bold {accent}")

    layout_group = Table.grid(expand=True)
    layout_group.add_row(period_tabs)
    layout_group.add_row(meta_line)
    layout_group.add_row(stats_line)
    layout_group.add_row(divider)
    layout_group.add_row(table)
    layout_group.add_row(footer_text)

    if total_titles > visible_rows:
        subtitle_str = (
            f"[bold {accent}]Item {details_cursor + 1} of {total_titles}[/] "
            f"[dim]({details_scroll + 1}–{min(details_scroll + visible_rows, total_titles)} visible | j/k/Wheel to scroll)[/dim]"
        )
    elif total_titles > 0:
        subtitle_str = f"[bold {accent}]Item {details_cursor + 1} of {total_titles}[/] [dim](Titles Breakdown)[/dim]"
    else:
        subtitle_str = f"[bold {warning}]No window titles recorded for {name}[/]"

    panel = Panel(
        layout_group,
        border_style=f"{accent}",
        subtitle=subtitle_str,
        expand=True,
        height=console_height,
    )
    return panel, details_scroll, details_cursor, max_scroll


# =============================================================================
# TERMINAL / RAW INPUT / EVENT LOOP
# =============================================================================
def set_terminal_cbreak(fd: int) -> list[Any]:
    """Enter non-canonical, no-echo mode WITHOUT O_NONBLOCK."""
    old_settings = termios.tcgetattr(fd)
    new_settings = termios.tcgetattr(fd)

    new_settings[0] &= ~(termios.IXON | termios.IXOFF | termios.ICRNL | termios.INLCR)
    new_settings[3] &= ~(
        termios.ECHO
        | termios.ECHOE
        | termios.ECHOK
        | termios.ECHONL
        | termios.ICANON
        | termios.IEXTEN
        | termios.ISIG
    )
    new_settings[6][termios.VMIN] = 0
    new_settings[6][termios.VTIME] = 0

    termios.tcsetattr(fd, termios.TCSAFLUSH, new_settings)
    return old_settings


def restore_terminal(fd: int, old_settings: list[Any], old_flags: int | None = None) -> None:
    try:
        sys.stdout.write(_MOUSE_OFF + _CURSOR_SHOW + _CLEAR_HOME)
        sys.stdout.flush()
    except Exception:
        pass
    if old_flags is not None:
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)
        except Exception:
            pass
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except Exception:
        pass


def _write_tty(data: str) -> None:
    try:
        sys.stdout.write(data)
        sys.stdout.flush()
    except Exception:
        pass


def parse_input_sequence(buf: bytes) -> tuple[str | None, int]:
    """Parse one command from the front of *buf*."""
    if not buf:
        return None, 0

    # ----- Escape / CSI / mouse / SS3 -----
    if buf[0] == 0x1B:
        if len(buf) == 1:
            return None, 0

        # SGR mouse: ESC [ < btn ; x ; y M/m
        if buf.startswith(b"\x1b[<"):
            for i in range(3, len(buf)):
                if buf[i] in (ord("M"), ord("m")):
                    try:
                        body = buf[3:i].decode("ascii", errors="ignore")
                        parts = body.split(";")
                        if parts and parts[0].isdigit():
                            b_code = int(parts[0])
                            if b_code == 64:
                                return "scroll_up", i + 1
                            if b_code == 65:
                                return "scroll_down", i + 1
                    except Exception:
                        pass
                    return None, i + 1
            return (None, 0) if len(buf) < 64 else (None, 1)

        # CSI: ESC [
        if buf[1] == ord("["):
            if len(buf) < 3:
                return None, 0
            for i in range(2, len(buf)):
                if 0x40 <= buf[i] <= 0x7E:
                    final = chr(buf[i])
                    inner = buf[2:i].decode("ascii", errors="ignore")
                    num = inner.split(";")[0] if inner else ""
                    if final == "A":
                        return "up", i + 1
                    if final == "B":
                        return "down", i + 1
                    if final == "C":
                        return "right", i + 1
                    if final == "D":
                        return "left", i + 1
                    if final == "H":
                        return "home", i + 1
                    if final == "F":
                        return "end", i + 1
                    if final == "Z":
                        return "prev_tab", i + 1
                    if final == "u":
                        if num == "9":
                            if ";2" in inner or ":2" in inner:
                                return "prev_tab", i + 1
                            return "next_tab", i + 1
                    if final == "~":
                        if num == "5":
                            return "page_up", i + 1
                        if num == "6":
                            return "page_down", i + 1
                        if num in {"1", "7"}:
                            return "home", i + 1
                        if num in {"4", "8"}:
                            return "end", i + 1
                        if num == "27" and (";2;9" in inner or ":2:9" in inner):
                            return "prev_tab", i + 1
                    return None, i + 1
            return (None, 0) if len(buf) < 32 else (None, 1)

        # SS3: ESC O A  (application cursor keys)
        if buf[1] == ord("O"):
            if len(buf) < 3:
                return None, 0
            if buf[2] == ord("A"):
                return "up", 3
            if buf[2] == ord("B"):
                return "down", 3
            if buf[2] == ord("C"):
                return "right", 3
            if buf[2] == ord("D"):
                return "left", 3
            if buf[2] == ord("H"):
                return "home", 3
            if buf[2] == ord("F"):
                return "end", 3
            if buf[2] in (ord("Z"), ord("I"), ord("i")):
                return "prev_tab", 3
            return None, 3

        # ESC + regular key (Alt+key) — consume both, ignore
        return None, 2

    ch = buf[:1]
    match ch:
        case b"\x1b":
            return "escape", 1
        case b"q" | b"Q":
            return "quit", 1
        case b"\x03":
            return "force_quit", 1
        case b"\r" | b"\n" | b" ":
            return "select", 1
        case b"\x7f" | b"\x08":
            return "back", 1
        case b"\t" | b"]":
            return "next_tab", 1
        case b"[":
            return "prev_tab", 1
        case b"j" | b"s":
            return "down", 1
        case b"k" | b"w":
            return "up", 1
        case b"h" | b"a":
            return "left", 1
        case b"l" | b"d":
            return "right", 1
        case b"\x04":
            return "page_down", 1
        case b"\x15":
            return "page_up", 1
        case b"\x06":
            return "half_page_down", 1
        case b"\x02":
            return "half_page_up", 1
        case b"g":
            return "home", 1
        case b"G":
            return "end", 1
        case b"1":
            return "period_today", 1
        case b"2":
            return "period_yesterday", 1
        case b"3":
            return "period_week", 1
        case b"4":
            return "period_month", 1
        case b"5":
            return "period_all", 1
        case b"f" | b"/":
            return "fzf", 1
        case b"r":
            return "refresh", 1
        case _:
            return None, 1


class _DashCache:
    """In-memory snapshot so tab/details switches do zero disk I/O."""

    __slots__ = ("raw_data", "raw_ts", "active", "active_ts", "colors", "colors_ts")

    def __init__(self) -> None:
        self.raw_data: dict[str, dict[str, Any]] = {}
        self.raw_ts: float = 0.0
        self.active: tuple[str, str] = ("", "")
        self.active_ts: float = 0.0
        self.colors: dict[str, str] = DEFAULT_COLORS.copy()
        self.colors_ts: float = 0.0

    def reload(self, force: bool = False, now: float | None = None) -> None:
        import time as _time

        t = now if now is not None else _time.monotonic()
        if force or (t - self.raw_ts) >= 2.0:
            try:
                self.raw_data = load_screentime_data()
            except Exception as e:
                log_error(f"cache raw_data: {e}")
            self.raw_ts = t
        if force or (t - self.active_ts) >= 1.0:
            try:
                self.active = get_active_hypr_window()
            except Exception as e:
                log_error(f"cache active: {e}")
            self.active_ts = t
        if force or (t - self.colors_ts) >= 5.0:
            try:
                self.colors = load_theme_colors()
            except Exception as e:
                log_error(f"cache colors: {e}")
            self.colors_ts = t


def _pause_live(live: Live, fd: int, old_settings: list[Any]) -> None:
    """Leave alt-screen + cbreak so a child process (FZF) can own the TTY."""
    try:
        live.stop()
    except Exception as e:
        log_error(f"live.stop: {e}")
    _write_tty(_MOUSE_OFF + _CURSOR_SHOW + _CLEAR_HOME)
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except Exception as e:
        log_error(f"pause termios restore: {e}")


def _resume_live(live: Live, fd: int) -> None:
    try:
        set_terminal_cbreak(fd)
    except Exception as e:
        log_error(f"resume cbreak: {e}")
    _write_tty(_MOUSE_ON + _CURSOR_HIDE)
    try:
        live.start(refresh=True)
    except Exception as e:
        log_error(f"live.start: {e}")


def run_live_dashboard() -> None:
    console = Console(
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        soft_wrap=False,
    )

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print("[bold red]screentime_tui requires a real TTY.[/bold red]")
        return

    fd = sys.stdin.fileno()
    old_settings = set_terminal_cbreak(fd)

    _write_tty(_CLEAR_HOME + _MOUSE_ON + _CURSOR_HIDE)

    cache = _DashCache()
    cache.reload(force=True)

    current_view: str = "dashboard"  # "dashboard" or "details"
    selected_app_class: str = ""

    range_key = "today"
    scroll_offset = 0
    cursor_idx = 0

    details_scroll = 0
    details_cursor = 0

    def build_panel() -> Panel:
        nonlocal scroll_offset, cursor_idx, details_scroll, details_cursor
        if current_view == "details":
            panel, details_scroll, details_cursor, _ = render_details_layout(
                selected_app_class,
                range_key,
                cache.colors,
                details_scroll,
                details_cursor,
                console.height,
                raw_data=cache.raw_data,
                active_window=cache.active,
            )
        else:
            panel, scroll_offset, cursor_idx, _ = render_dashboard_layout(
                range_key,
                cache.colors,
                scroll_offset,
                cursor_idx,
                console.height,
                raw_data=cache.raw_data,
                active_window=cache.active,
            )
        return panel

    def push_frame(live: Live) -> None:
        try:
            live.update(build_panel(), refresh=True)
        except Exception as e:
            log_error(f"live.update/refresh: {e}\n{traceback.format_exc()}")

    try:
        panel = build_panel()

        with Live(
            panel,
            console=console,
            screen=True,
            auto_refresh=False,
            redirect_stdout=False,
            redirect_stderr=False,
            transient=True,
            vertical_overflow="crop",
        ) as live:
            input_buf = bytearray()
            running = True

            while running:
                try:
                    ready, _, _ = select.select([fd], [], [], 0.25)
                except InterruptedError:
                    ready = []

                got_keys = False
                if ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except BlockingIOError:
                        chunk = b""
                    except OSError as e:
                        log_error(f"os.read: {e}")
                        chunk = b""

                    if chunk:
                        input_buf.extend(chunk)
                        got_keys = True

                # Standalone Escape key detection on select timeout
                if not got_keys and input_buf == b"\x1b":
                    input_buf.clear()
                    if current_view == "details":
                        current_view = "dashboard"
                    else:
                        running = False
                        break

                while input_buf:
                    cmd, consumed = parse_input_sequence(bytes(input_buf))
                    if consumed <= 0:
                        if input_buf[0] == 0x1B and len(input_buf) > 48:
                            del input_buf[0]
                            continue
                        break
                    del input_buf[:consumed]

                    match cmd:
                        case "force_quit":
                            running = False
                            break
                        case "quit":
                            if current_view == "details":
                                current_view = "dashboard"
                            else:
                                running = False
                                break
                        case "escape" | "back":
                            if current_view == "details":
                                current_view = "dashboard"
                            else:
                                running = False
                                break
                        case "up":
                            if current_view == "details":
                                details_cursor = max(0, details_cursor - 1)
                            else:
                                cursor_idx = max(0, cursor_idx - 1)
                        case "down":
                            if current_view == "details":
                                details_cursor += 1
                            else:
                                cursor_idx += 1
                        case "scroll_up":
                            if current_view == "details":
                                details_cursor = max(0, details_cursor - 3)
                            else:
                                cursor_idx = max(0, cursor_idx - 3)
                        case "scroll_down":
                            if current_view == "details":
                                details_cursor += 3
                            else:
                                cursor_idx += 3
                        case "page_up":
                            if current_view == "details":
                                details_cursor = max(0, details_cursor - 10)
                            else:
                                cursor_idx = max(0, cursor_idx - 10)
                        case "page_down":
                            if current_view == "details":
                                details_cursor += 10
                            else:
                                cursor_idx += 10
                        case "half_page_up":
                            if current_view == "details":
                                details_cursor = max(0, details_cursor - 15)
                            else:
                                cursor_idx = max(0, cursor_idx - 15)
                        case "half_page_down":
                            if current_view == "details":
                                details_cursor += 15
                            else:
                                cursor_idx += 15
                        case "home":
                            if current_view == "details":
                                details_cursor = 0
                            else:
                                cursor_idx = 0
                        case "end":
                            if current_view == "details":
                                details_cursor = 10**9
                            else:
                                cursor_idx = 10**9
                        case "left":
                            if current_view == "details":
                                current_view = "dashboard"
                        case "select" | "right":
                            if current_view == "dashboard":
                                agg, _, _ = aggregate_by_range(cache.raw_data, range_key)
                                sorted_apps = sorted(agg.items(), key=lambda x: x[1]["duration"], reverse=True)
                                if sorted_apps and 0 <= cursor_idx < len(sorted_apps):
                                    selected_app_class = sorted_apps[cursor_idx][0]
                                    details_cursor = 0
                                    details_scroll = 0
                                    current_view = "details"
                            else:
                                current_view = "dashboard"
                        case "period_today" | "period_yesterday" | "period_week" | "period_month" | "period_all":
                            range_key = PERIOD_KEYS[cmd]
                            if current_view == "dashboard":
                                cursor_idx = 0
                                scroll_offset = 0
                            else:
                                details_cursor = 0
                                details_scroll = 0
                        case "next_tab":
                            range_key = get_cycled_period(range_key, 1)
                            if current_view == "dashboard":
                                cursor_idx = 0
                                scroll_offset = 0
                            else:
                                details_cursor = 0
                                details_scroll = 0
                        case "prev_tab":
                            range_key = get_cycled_period(range_key, -1)
                            if current_view == "dashboard":
                                cursor_idx = 0
                                scroll_offset = 0
                            else:
                                details_cursor = 0
                                details_scroll = 0
                        case "fzf":
                            _pause_live(live, fd, old_settings)
                            try:
                                run_fzf_explorer(range_key)
                            finally:
                                _resume_live(live, fd)
                                cache.reload(force=True)
                        case "refresh":
                            cache.reload(force=True)
                        case _:
                            pass

                if not running:
                    break

                if not got_keys:
                    cache.reload(force=False)

                push_frame(live)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        log_error(f"run_live_dashboard unhandled exception: {e}\n{traceback.format_exc()}")
    finally:
        restore_terminal(fd, old_settings)
        try:
            console.print("[bold green]✔ Screentime Dashboard closed cleanly.[/bold green]")
        except Exception:
            pass


# =============================================================================
# CLI ENTRY POINT
# =============================================================================
def main() -> None:
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        match cmd:
            case "--preview":
                # Handle multi-word app classes safely: python3 screentime_tui.py --preview <cls...> <key>
                valid_keys = {"today", "yesterday", "week", "month", "all"}
                if len(sys.argv) >= 4 and sys.argv[-1].lower() in valid_keys:
                    r_key = sys.argv[-1].lower()
                    app_cls = " ".join(sys.argv[2:-1]).strip()
                else:
                    app_cls = sys.argv[2].strip() if len(sys.argv) > 2 else ""
                    r_key = sys.argv[3].lower() if len(sys.argv) > 3 else "today"
                render_fzf_preview(app_cls, r_key)
                return
            case "--fzf" | "-i" | "fzf" | "explore":
                r_key = sys.argv[2].lower() if len(sys.argv) > 2 else "today"
                run_fzf_explorer(r_key)
                return
            case "--help" | "-h":
                print("Usage: screentime_tui.py [OPTIONS]")
                print("  (no args)           Launch Python Rich Live Dashboard")
                print("  --fzf, -i [PERIOD]  Launch Interactive FZF Explorer")
                print("  --preview CLS [KEY] Render Matugen ANSI preview window for FZF")
                return
            case _:
                pass

    run_live_dashboard()


if __name__ == "__main__":
    main()

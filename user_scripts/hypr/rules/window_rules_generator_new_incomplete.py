#!/usr/bin/env python3
# =============================================================================
# Dusky Window Rule Generator — Production Bleeding-Edge Edition
# Target: Arch Linux (rolling) | Python 3.14+ | Hyprland 0.56+ (Lua config)
# Framework: Textual 8.2+ | Wayland wl_output transforms 0–7 | Strict RE2 / Lua
#
# Key Features:
#   • Zero hardcoded usernames: strict XDG Base Directory Specification ($XDG_CONFIG_HOME,
#     $XDG_RUNTIME_DIR) with portable fallback to Path.home().
#   • Mathematical precision: true_scale() recovers exact n/120 fractional scale
#     from Hyprland's 2-decimal IPC output (e.g. 1.88 -> 1.875, 1.33 -> 1.333333).
#   • Schema validation: verified against Hyprland 0.56.2 hl.window_rule schema.
#     Strictly separates match properties from effect properties.
#   • Byte-level regex escaping: escape_regex() handles RE2 metacharacters,
#     Lua string escape chains (\\\\ for literal \), quotes, and control chars.
#   • Dual mode: Full interactive Textual 8.2+ TUI or headless CLI (--list, --print, --append).
#   • Robust locking & atomics: flock in $XDG_RUNTIME_DIR, fsync on file + dir,
#     and auto-deduplicating rule names.
# =============================================================================

import os
import sys
import shutil
import subprocess
import importlib.util

# ---------------------------------------------------------------------------
# 0. Arch Linux Bootstrap Gate
# ---------------------------------------------------------------------------
_BOOTSTRAP_SENTINEL = "DUSKY_RULES_BOOTSTRAPPED"

def _bootstrap() -> None:
    if os.environ.get(_BOOTSTRAP_SENTINEL):
        return

    missing: list[str] = []
    if importlib.util.find_spec("textual") is None:
        missing.append("python-textual")
    if importlib.util.find_spec("rich") is None:
        missing.append("python-rich")
    if shutil.which("wl-copy") is None:
        missing.append("wl-clipboard")
    if shutil.which("hyprctl") is None:
        missing.append("hyprland")

    if not missing:
        return

    print(f"\033[1;33m[bootstrap]\033[0m Missing dependencies: {', '.join(missing)}", file=sys.stderr)

    if shutil.which("pacman") is None or not sys.stdin.isatty():
        sys.exit(f"Error: Missing required packages: {' '.join(missing)}\nPlease install them via pacman.")

    try:
        ans = input(f"Install {', '.join(missing)} via pacman now? [Y/n] ").strip().lower()
        if ans not in ("", "y", "yes"):
            sys.exit("Aborted by user.")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)

    cmd = ["pacman", "-S", "--needed", "--noconfirm", *missing]
    if os.geteuid() != 0:
        if shutil.which("sudo") is None:
            sys.exit("sudo not found and not running as root. Install dependencies manually.")
        cmd.insert(0, "sudo")

    print(f"\033[1;34m==>\033[0m Running: {' '.join(cmd)}", file=sys.stderr)
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        sys.exit(f"pacman failed with exit code {exc.returncode}. Install manually.")

    env = dict(os.environ) | {_BOOTSTRAP_SENTINEL: "1"}
    os.execve(sys.executable, [sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]], env)

_bootstrap()

# ---------------------------------------------------------------------------
# 1. Standard & Third-Party Imports
# ---------------------------------------------------------------------------
import argparse
import atexit
import fcntl
import json
import math
import re
import shlex
import tempfile
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Final, NoReturn, override

from rich.markup import escape as rich_escape
from textual import on, work
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button, Footer, Input, Label, ListItem, ListView, Select, TextArea,
)

# ---------------------------------------------------------------------------
# 2. Paths, Constants & Metacharacters
# ---------------------------------------------------------------------------
type RuleText = str

SCALE_Q: Final[int] = 120  # wp_fractional_scale_v1 denominator

XDG_CONFIG_HOME: Final = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
XDG_RUNTIME_DIR: Final = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())

CONFIG_DIR: Final = XDG_CONFIG_HOME / "hypr" / "edit_here" / "source"
DEFAULT_TARGET_FILE: Final = CONFIG_DIR / "window_rules.lua"
LOCK_FILE: Final = XDG_RUNTIME_DIR / f"hypr-window-rules-{os.getuid()}.lock"

APP_TITLE: Final = "Dusky Window Rule Generator"
APP_VERSION: Final = "v9.2  hypr 0.56+  py3.14  textual 8.2+"
MIN_HYPR: Final = (0, 55, 4)

# RE2 metacharacters that must be escaped inside a Hyprland rule regex.
_RE2_META: Final = frozenset(r".[]*^$()+?{}|")


# ---------------------------------------------------------------------------
# 3. Data Models
# ---------------------------------------------------------------------------
@dataclass(slots=True, kw_only=True, frozen=True)
class MonitorData:
    id: int
    name: str
    description: str
    width: int              # Physical pixels; IPC never swaps these on rotation
    height: int
    scale: float            # Exact true scale (snapped to 1/120)
    x: int                  # Logical layout position
    y: int
    transform: int          # Wayland transform (0-7)
    reserved: tuple[int, int, int, int] = (0, 0, 0, 0)   # left, top, right, bottom

    @property
    def rotated(self) -> bool:
        return bool(self.transform & 1)

    @property
    def logical_width(self) -> float:
        w = self.height if self.rotated else self.width
        return w / self.scale if self.scale > 0.001 else float(w)

    @property
    def logical_height(self) -> float:
        h = self.width if self.rotated else self.height
        return h / self.scale if self.scale > 0.001 else float(h)

    @property
    def usable_width(self) -> float:
        return max(1.0, self.logical_width - self.reserved[0] - self.reserved[2])

    @property
    def usable_height(self) -> float:
        return max(1.0, self.logical_height - self.reserved[1] - self.reserved[3])


@dataclass(slots=True, kw_only=True, frozen=True)
class ClientData:
    address: str
    title: str
    app_class: str
    mon_id: int
    w: int                  # Logical dimensions reported by Hyprland
    h: int
    x: int                  # Logical layout position
    y: int
    floating: bool
    mapped: bool
    workspace_name: str
    monitor_name: str = ""


@dataclass(slots=True, kw_only=True, frozen=True)
class GeneratedRule:
    address: str
    title: str
    app_class: str
    client: ClientData
    monitor: MonitorData


@dataclass(slots=True, frozen=True)
class Geometry:
    lx: int
    ly: int
    rel_w: str
    rel_h: str
    rel_x: str
    rel_y: str


# ---------------------------------------------------------------------------
# 4. Math, Escaping & String Helpers
# ---------------------------------------------------------------------------
def clean_scale(n: int, w: int, h: int) -> bool:
    """Returns True if scale = n/120 divides physical dimensions into exact integers."""
    return n > 0 and (SCALE_Q * w) % n == 0 and (SCALE_Q * h) % n == 0


def true_scale(ipc_scale: float, w: int, h: int) -> float:
    """hyprctl monitors -j serializes scale with 2 decimals ({:.2f}).
    Recover the exact n/120 quantum that cleanly divides the monitor mode."""
    centre = ipc_scale * SCALE_Q
    for n in sorted(range(math.floor(centre - 0.61), math.ceil(centre + 0.61) + 1),
                    key=lambda val: (abs(val - centre), val)):
        if clean_scale(n, w, h):
            return n / SCALE_Q
    return max(1, round(centre)) / SCALE_Q


def escape_regex(value: str) -> str:
    r"""
    Escape `value` so that, after the Lua string literal is decoded by the
    Hyprland config loader, RE2 receives a literal match.

        input char   bytes in .lua file   lua string in VM   RE2 sees
        ----------   ------------------   ----------------   --------
        \            \\\\  (4)            \\                 literal "\"
        .            \\.   (3)            \.                 escaped dot
        "            \"    (2)            "                  literal quote
    """
    out: list[str] = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch in _RE2_META:
            out.append("\\\\" + ch)
        elif ch in ("\n", "\r", "\t"):
            out.append(" ")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            continue
        else:
            out.append(ch)
    return "".join(out)


def sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    cleaned = re.sub(r"-{2,}", "-", cleaned).lower()
    return cleaned[:64] or "unnamed"


def fmt_float(value: float, places: int = 4) -> str:
    if not math.isfinite(value):
        return "0"
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def editor_command() -> list[str]:
    for env in ("VISUAL", "EDITOR"):
        raw = os.environ.get(env, "").strip()
        if raw:
            try:
                parts = shlex.split(raw, comments=False, posix=True)
                if parts:
                    return parts
            except ValueError:
                return [raw]
    for cand in ("nvim", "hx", "helix", "vim", "micro", "nano", "vi"):
        p = shutil.which(cand)
        if p:
            return [p]
    return ["vi"]


def wl_copy(text: str) -> bool:
    """Copies text using wl-copy by forking a detached process."""
    exe = shutil.which("wl-copy")
    if not exe:
        return False
    try:
        proc = subprocess.Popen(
            [exe, "--type", "text/plain;charset=utf-8"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        proc.communicate(text.encode("utf-8"), timeout=2)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def lua_balanced(text: str) -> tuple[bool, str]:
    """Scans code ignoring comments and strings, verifying balanced braces."""
    balance = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "-" and i + 1 < n and text[i + 1] == "-":
            i += 2
            if i + 1 < n and text[i:i + 2] == "[[":
                i += 2
                close = text.find("]]", i)
                i = n if close < 0 else close + 2
            else:
                eol = text.find("\n", i)
                i = n if eol < 0 else eol + 1
            continue
        if c in ('"', "'"):
            quote = c
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                elif text[i] == quote or text[i] == "\n":
                    i += 1
                    break
                else:
                    i += 1
            continue
        if c == "{":
            balance += 1
        elif c == "}":
            balance -= 1
            if balance < 0:
                return False, "unexpected closing brace '}'"
        i += 1
    if balance != 0:
        return False, f"unclosed brace (depth {balance})"

    if shutil.which("luac"):
        res = subprocess.run(["luac", "-p", "-"], input=text.encode("utf-8"), capture_output=True)
        if res.returncode != 0:
            err = res.stderr.decode("utf-8", errors="replace").strip()
            return False, f"luac syntax error: {err}"

    return True, ""


# ---------------------------------------------------------------------------
# 5. Rule Presets & Generation
# ---------------------------------------------------------------------------
class Preset(Enum):
    FULL = "FULL"
    MINIMAL = "MINIMAL"
    FLOAT = "FLOAT"
    TRANSIENT = "TRANSIENT"
    STICKY = "STICKY"
    VISUALS = "VISUALS"
    TILING = "TILING"


PRESET_META: Final[dict[Preset, tuple[str, str, str]]] = {
    Preset.FULL:      ("Full Template",    "Complete documented 10-section reference template", "◈"),
    Preset.MINIMAL:   ("Minimal Blank",    "Match rule only; ready for custom properties",      "○"),
    Preset.FLOAT:     ("Basic Float",      "Floating layout with exact pixel bounds & center",  "▭"),
    Preset.TRANSIENT: ("Native Transient", "Spawns on active workspace and does not follow",    "⧉"),
    Preset.STICKY:    ("Sticky Dialog",    "Floating, pinned across all workspaces, centered",  "📌"),
    Preset.VISUALS:   ("Visuals & Style",  "Opacity, rounding, border, shadow & blur controls", "🎨"),
    Preset.TILING:    ("Tiling Exception", "Forced tile mode, window grouping & deny policies", "⊞"),
}


def _compute_geometry(client: ClientData, mon: MonitorData) -> Geometry:
    lx = client.x - mon.x
    ly = client.y - mon.y
    uw = mon.usable_width
    uh = mon.usable_height
    return Geometry(
        lx=lx,
        ly=ly,
        rel_w=fmt_float(client.w / uw if uw > 0 else 0.0),
        rel_h=fmt_float(client.h / uh if uh > 0 else 0.0),
        rel_x=fmt_float(lx / uw if uw > 0 else 0.0),
        rel_y=fmt_float(ly / uh if uh > 0 else 0.0),
    )


def extract_existing_rule_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        content = path.read_text(encoding="utf-8")
        return set(re.findall(r'(?m)^[ \t]*name[ \t]*=[ \t]*"([^"]+)"', content))
    except OSError:
        return set()


def next_available_name(base_name: str, existing_names: set[str]) -> str:
    if base_name not in existing_names:
        return base_name
    i = 2
    while f"{base_name}-{i}" in existing_names:
        i += 1
    return f"{base_name}-{i}"


def generate_rule(client: ClientData, mon: MonitorData, preset: Preset,
                  existing_names: set[str] | None = None) -> RuleText:
    safe_class = escape_regex(client.app_class)
    safe_title = escape_regex(client.title)
    base_name = sanitize_name(client.app_class)
    if existing_names is None:
        existing_names = set()

    geo = _compute_geometry(client, mon)
    header = (
        f"-- {client.title or client.app_class} | {mon.name} {client.w}x{client.h} @ "
        f"{geo.lx},{geo.ly} | ws:{client.workspace_name}"
    )

    match preset:
        case Preset.MINIMAL:
            rname = next_available_name(f"{base_name}-minimal", existing_names)
            return f"""{header}
hl.window_rule({{
    name  = "{rname}",
    match = {{ class = "^({safe_class})$" }},
}})
"""
        case Preset.FLOAT:
            rname = next_available_name(f"{base_name}-float", existing_names)
            return f"""{header}
hl.window_rule({{
    name  = "{rname}",
    match = {{ class = "^({safe_class})$" }},
    float  = true,
    size   = {{ {client.w}, {client.h} }},
    -- size = {{ "monitor_w * {geo.rel_w}", "monitor_h * {geo.rel_h}" }},
    center = true,
}})
"""
        case Preset.TRANSIENT:
            rname = next_available_name(f"{base_name}-transient", existing_names)
            return f"""{header}
-- Native transient: spawns on the active workspace only and does not follow.
-- Requires misc.initial_workspace_tracking = 2 in your Hyprland config.
hl.window_rule({{
    name  = "{rname}",
    match = {{ class = "^({safe_class})$" }},
    float             = true,
    workspace         = "unset",
    focus_on_activate = true,
    stay_focused      = true,
}})
"""
        case Preset.STICKY:
            rname = next_available_name(f"{base_name}-sticky", existing_names)
            return f"""{header}
hl.window_rule({{
    name  = "{rname}",
    match = {{ class = "^({safe_class})$" }},
    float  = true,
    pin    = true,
    center = true,
}})
"""
        case Preset.VISUALS:
            rname = next_available_name(f"{base_name}-visuals", existing_names)
            return f"""{header}
hl.window_rule({{
    name  = "{rname}",
    match = {{ class = "^({safe_class})$" }},
    opacity        = "0.92 override 0.88 override",
    rounding       = 14,
    rounding_power = 2.5,
    border_size    = 2,
    -- border_color = "rgba(7aa2f7ff) rgba(bb9af7ff) 45deg",
    no_blur    = false,
    no_shadow  = false,
    dim_around = false,
}})
"""
        case Preset.TILING:
            rname = next_available_name(f"{base_name}-tiling", existing_names)
            return f"""{header}
hl.window_rule({{
    name  = "{rname}",
    match = {{ class = "^({safe_class})$" }},
    tile  = true,
    group = "set",
    -- group = "barred",   -- keep in group, hide the tab label
    -- group = "deny",     -- never allow grouping
}})
"""
        case Preset.FULL:
            rname = next_available_name(base_name, existing_names)
            return f"""{header}
-- ---------------------------------------------------------------------
-- {client.title or client.app_class}
-- monitor  {mon.name}  {mon.width}x{mon.height} physical  scale {mon.scale}
--   transform {mon.transform}  logical {mon.logical_width:.0f}x{mon.logical_height:.0f}
--   reserved l/t/r/b {mon.reserved[0]}/{mon.reserved[1]}/{mon.reserved[2]}/{mon.reserved[3]}
--   usable  {mon.usable_width:.0f}x{mon.usable_height:.0f}
-- window   {client.w}x{client.h} @ {geo.lx},{geo.ly}  (monitor-relative logical px)
-- ---------------------------------------------------------------------

hl.window_rule({{
    name = "{rname}",
    -- enabled = true,                    -- master toggle for this rule

    -- 1. IDENTITY & MATCHING (every key must match)
    match = {{
        class = "^({safe_class})$",
        -- title         = "^({safe_title})$",
        -- initial_class = "^({safe_class})$",   -- class at first map (static)
        -- initial_title = "^({safe_title})$",
        -- xwayland      = false,
        -- float         = false,
        -- fullscreen    = false,
        -- pin           = true,
        -- focus         = true,
        -- group         = true,
        -- modal         = true,
        -- tag           = "gaming",
        -- content       = "video",           -- none | photo | video | game
        -- xdg_tag       = "dialog",
        -- workspace     = "{client.workspace_name}",
    }},

    -- 2. PLACEMENT & WORKSPACE
    -- workspace = "1",
    -- workspace = "special:magic",
    -- workspace = "name:gaming",
    -- workspace = "unset",                   -- active workspace, do not follow
    -- workspace = "1 silent",
    -- monitor   = "{mon.name}",

    -- 3. LAYOUT STATE
    float = true,
    -- tile = true,
    -- fullscreen = true,
    -- maximize = true,
    -- fullscreen_state = "0 0",              -- "internal client"

    -- 4. GEOMETRY (logical px; monitor_w / monitor_h are logical)
    size = {{ {client.w}, {client.h} }},
    -- size = {{ "monitor_w * {geo.rel_w}", "monitor_h * {geo.rel_h}" }},
    -- min_size = {{ 200, 100 }},
    -- max_size = {{ 1920, 1080 }},
    -- keep_aspect_ratio = true,
    -- persistent_size   = true,
    -- no_max_size       = true,
    -- scrolling_width   = 1.2,
    move = {{ {geo.lx}, {geo.ly} }},
    -- move = {{ "monitor_w * {geo.rel_x}", "monitor_h * {geo.rel_y}" }},
    -- move = {{ "monitor_w - window_w - 20", "monitor_h - window_h - 20" }},
    -- move = {{ "cursor_x - (window_w * 0.5)", "cursor_y - (window_h * 0.5)" }},
    -- center = true,                         -- overrides move
    -- center = 1,                            -- center inside reserved area

    -- 5. FOCUS, GROUPS & LIFECYCLE
    -- no_initial_focus  = true,
    -- focus_on_activate = false,             -- block focus stealing (PiP)
    -- stay_focused      = true,
    -- group = "set",   -- new | lock | barred | deny | invade | override | unset
    -- tag   = "+gaming",                     -- +name adds, -name removes
    -- content = "game",
    -- no_close_for = 500,                    -- ms

    -- 6. ANIMATION (choose exactly one)
    -- animation = "popin",
    -- animation = "popin 87%",
    -- animation = "slide left",
    -- animation = "gnomed",
    -- animation = "fade",
    -- no_anim = true,

    -- 7. VISUALS & DECORATION
    -- opacity = "0.95 override 0.85 override",
    -- opaque  = true,
    -- rounding = 10,
    -- rounding_power = 2,                    -- 2 = circular, higher = squircle
    -- border_size = 2,
    -- border_color = "rgba(33ccffee) rgba(00ff99ee) 45deg",
    -- idle_inhibit = "always",               -- none | always | focus | fullscreen

    -- 8. COMPOSITING & EFFECTS
    -- no_blur    = true,
    -- xray       = true,
    -- no_shadow  = true,
    -- no_dim     = true,
    -- dim_around = true,
    -- no_focus   = true,

    -- 9. PERFORMANCE & SUPPRESSION
    -- immediate = true,                      -- bypass vsync (games)
    -- no_shortcuts_inhibit = true,
    -- suppress_event = "maximize",           -- fullscreen | activate | activatefocus

    -- 10. NOTES
    -- generated from live IPC; re-check with: hyprctl clients -j
}})
"""


# ---------------------------------------------------------------------------
# 6. Hyprland IPC Scanner
# ---------------------------------------------------------------------------
def hyprctl_json(args: list[str], timeout: float = 3.0) -> Any:
    try:
        proc = subprocess.run(
            ["hyprctl", "-j", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("'hyprctl' command not found. Is Hyprland installed?") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"hyprctl {' '.join(args)} timed out after {timeout}s.") from None

    if proc.returncode != 0:
        raise RuntimeError(f"hyprctl {' '.join(args)} exited {proc.returncode}: {proc.stderr.strip()}")

    raw = proc.stdout.strip()
    if not raw:
        raise RuntimeError(f"hyprctl {' '.join(args)} returned empty output.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from hyprctl {' '.join(args)}: {exc}") from exc


def _parse_vec2(value: Any) -> tuple[int, int]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (ValueError, TypeError):
            return 0, 0
    if isinstance(value, dict):
        x = value.get("x", value.get("w", value.get("width", 0)))
        y = value.get("y", value.get("h", value.get("height", 0)))
        try:
            return int(x), int(y)
        except (ValueError, TypeError):
            return 0, 0
    return 0, 0


def _parse_reserved(value: Any) -> tuple[int, int, int, int]:
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            return int(value[0]), int(value[1]), int(value[2]), int(value[3])
        except (ValueError, TypeError):
            pass
    return (0, 0, 0, 0)


def scan_windows() -> list[GeneratedRule]:
    monitors_raw = hyprctl_json(["monitors"])
    if not isinstance(monitors_raw, list) or not monitors_raw:
        raise RuntimeError("hyprctl monitors returned no monitor data.")

    monitors: dict[int, MonitorData] = {}
    for entry in monitors_raw:
        if not isinstance(entry, dict) or entry.get("id") is None:
            continue
        try:
            m_id = int(entry["id"])
            m_w = int(entry.get("width", 1920))
            m_h = int(entry.get("height", 1080))
            raw_scale = float(entry.get("scale", 1.0))
            exact_scale = true_scale(raw_scale, m_w, m_h)
            monitors[m_id] = MonitorData(
                id=m_id,
                name=str(entry.get("name", "unknown")),
                description=str(entry.get("description", "")),
                width=m_w,
                height=m_h,
                scale=exact_scale,
                x=int(entry.get("x", 0)),
                y=int(entry.get("y", 0)),
                transform=int(entry.get("transform", 0)),
                reserved=_parse_reserved(entry.get("reserved")),
            )
        except (TypeError, ValueError):
            continue

    if not monitors:
        raise RuntimeError("No usable monitor data parsed from IPC.")

    clients_raw = hyprctl_json(["clients"])
    if not isinstance(clients_raw, list):
        raise RuntimeError("hyprctl clients returned invalid payload.")

    rules: list[GeneratedRule] = []
    for entry in clients_raw:
        if not isinstance(entry, dict) or not entry.get("mapped", False):
            continue
        app_class = str(entry.get("class") or entry.get("initialClass") or "").strip()
        if not app_class:
            continue
        raw_mon = entry.get("monitor")
        if raw_mon is None:
            continue
        try:
            mon_id = int(raw_mon)
        except (TypeError, ValueError):
            continue
        mon = monitors.get(mon_id)
        if mon is None:
            continue

        at_x, at_y = _parse_vec2(entry.get("at"))
        sz_w, sz_h = _parse_vec2(entry.get("size"))
        ws = entry.get("workspace", {})
        ws_name = str(ws.get("name") if isinstance(ws, dict) else ws or "unknown")
        raw_title = str(entry.get("title") or entry.get("initialTitle") or app_class)
        title = re.sub(r"[\r\n\t\x00-\x1f]+", " ", raw_title).strip()[:160]

        client = ClientData(
            address=str(entry.get("address", "")),
            title=title,
            app_class=app_class,
            mon_id=mon_id,
            w=sz_w,
            h=sz_h,
            x=at_x,
            y=at_y,
            floating=bool(entry.get("floating", False)),
            mapped=True,
            workspace_name=ws_name,
            monitor_name=mon.name,
        )

        # Dataclass replace safely clamps invalid dimensions on unmapped/minimized windows
        if client.w <= 0 or client.h <= 0:
            client = replace(client, w=max(client.w, 320), h=max(client.h, 200))

        rules.append(GeneratedRule(
            address=client.address,
            title=title[:64],
            app_class=app_class,
            client=client,
            monitor=mon,
        ))

    rules.sort(key=lambda r: (r.client.monitor_name, r.client.workspace_name,
                              r.app_class.lower(), r.title.lower()))
    return rules


# ---------------------------------------------------------------------------
# 7. Atomic Config Append
# ---------------------------------------------------------------------------
def append_rule(text: str, target_file: Path | None = None) -> str:
    target = target_file or DEFAULT_TARGET_FILE
    ok, why = lua_balanced(text)
    if not ok:
        raise ValueError(f"Lua syntax error: {why}")
    if "hl.window_rule(" not in text:
        raise ValueError("Refusing to append: no hl.window_rule(...) call found.")

    target.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.touch(exist_ok=True)

    match = re.search(r'(?m)^[ \t]*name[ \t]*=[ \t]*"([^"]+)"', text)
    rule_name = match.group(1) if match else ""

    with open(LOCK_FILE, "r+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

        if not target.exists():
            target.write_text(
                "-- window_rules.lua - managed by Dusky Window Rule Generator\n\n",
                encoding="utf-8",
            )
        existing = target.read_text(encoding="utf-8")

        if rule_name and re.search(
            rf'(?m)^[ \t]*name[ \t]*=[ \t]*"{re.escape(rule_name)}"', existing
        ):
            raise ValueError(
                f'Rule name "{rule_name}" already exists in {target.name}.\n'
                "Press 'e' to edit the name before appending."
            )

        payload = ("" if existing.endswith("\n\n") else
                   "\n" if existing.endswith("\n") else "\n\n")
        payload += text.rstrip() + "\n"

        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC)
        try:
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        dir_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    return rule_name or "(unnamed)"


# ---------------------------------------------------------------------------
# 8. Interactive Textual Application
# ---------------------------------------------------------------------------
class DuskyApp(App[None]):
    AUTO_FOCUS = None
    TITLE = APP_TITLE

    CSS = """
    Screen { background: #070a14; color: #c0caf5; }
    #header { dock: top; height: 3; background: #0f111a; border-bottom: tall #23263a;
              layout: horizontal; padding: 0 2; }
    #title { width: auto; color: #7aa2f7; text-style: bold; padding-top: 1; }
    #ver { width: auto; color: #3b4261; padding: 1 0 0 2; }
    #preset-pill { width: 1fr; content-align: right middle; color: #bb9af7;
                   text-align: right; padding-top: 1; }
    #main { layout: horizontal; height: 1fr; }
    #sidebar { width: 46; min-width: 30; max-width: 62; background: #0b0d16;
               border-right: solid #1e2030; layout: vertical; }
    #sidebar-head { height: 5; background: #13151f; border-bottom: solid #23263a;
                    padding: 1 1 0 1; layout: vertical; }
    #filter { margin-top: 1; }
    #window-list { height: 1fr; background: #0b0d16; scrollbar-size: 1 1; }
    ListItem { padding: 0 1; }
    ListItem.-selected { background: #1e2030; }
    ListItem:hover { background: #151720; }
    #right { width: 1fr; layout: vertical; background: #070a14; }
    #preset-bar { height: 4; background: #10111c; layout: horizontal;
                  padding: 1 1 0 1; border-bottom: solid #1e2030; }
    #preset-select { width: 38; }
    #preset-desc { width: 1fr; color: #565f89; padding: 1 1 0 2; }
    #preview-wrap { height: 1fr; margin: 1 1 0 1; border: tall #2a2e45;
                    background: #11131f; border-title-color: #7aa2f7;
                    border-title-style: bold; border-subtitle-color: #3b4261; }
    #preview-wrap:focus-within { border: tall #7aa2f7; }
    #preview { background: #11131f; color: #c0caf5; }
    #actions { height: 3; layout: horizontal; background: #0f111a; padding: 0 1;
               border-top: solid #23263a; }
    .btn { min-width: 13; margin-right: 1; background: #1a1d2f; color: #c0caf5;
           border: none; }
    .btn:hover { background: #252a40; }
    .primary { background: #2a355a; color: #7aa2f7; text-style: bold; }
    #status { dock: bottom; height: 2; background: #0a0c14; color: #565f89;
              layout: horizontal; padding: 0 2; }
    #status-l { width: 1fr; } #status-r { width: auto; color: #444b6a; }
    Input { background: #1a1d2f; border: tall #2a2e45; color: #c0caf5; }
    Input:focus { border: tall #7aa2f7; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("c", "copy", "Copy"),
        Binding("a,ctrl+s", "append", "Append"),
        Binding("e", "edit", "Edit"),
        Binding("ctrl+e", "external_edit", "$EDITOR"),
        Binding("left_square_bracket,comma", "prev_preset", "Prev preset", key_display="["),
        Binding("right_square_bracket,full_stop", "next_preset", "Next preset", key_display="]"),
        Binding("slash", "focus_filter", "Filter", key_display="/"),
        Binding("escape", "escape", "Back", priority=True, show=False),
        Binding("ctrl+z", "undo", "Undo", show=False),
        Binding("j,down", "cursor_down", "Down", show=False),
        Binding("k,up", "cursor_up", "Up", show=False),
        Binding("g,home", "top", "Top", show=False),
        Binding("G,end", "bottom", "Bottom", show=False),
        Binding("ctrl+d,pagedown", "page_down", "PgDn", show=False),
        Binding("ctrl+u,pageup", "page_up", "PgUp", show=False),
    ]

    current_preset: reactive[Preset] = reactive(Preset.FULL, init=False)
    selected_idx: reactive[int] = reactive(0, init=False)
    is_editing: reactive[bool] = reactive(False, init=False)

    def __init__(self, rules: list[GeneratedRule], target_file: Path | None = None) -> None:
        super().__init__()
        self._target_file = target_file or DEFAULT_TARGET_FILE
        self._base: list[GeneratedRule] = rules
        self._filtered: list[GeneratedRule] = list(rules)
        self._edits: dict[tuple[str, Preset], str] = {}
        self._undo: list[tuple[tuple[str, Preset], str | None]] = []
        self._tmpdir = Path(tempfile.mkdtemp(prefix="dusky-wrg-"))
        self._suppress_select = False
        self._status = f"target: {self._target_file}"
        self._existing_names = extract_existing_rule_names(self._target_file)
        atexit.register(self._cleanup_tmp)

    @override
    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Label(f" {APP_TITLE}", id="title")
            yield Label(APP_VERSION, id="ver")
            yield Label("", id="preset-pill")
        with Horizontal(id="main"):
            with Vertical(id="sidebar"):
                with Vertical(id="sidebar-head"):
                    yield Label(f" Windows ({len(self._base)})   / filter   j/k move", id="sb-title")
                    yield Input(placeholder="filter class / title / monitor / ws", id="filter")
                yield ListView(id="window-list")
            with Vertical(id="right"):
                with Horizontal(id="preset-bar"):
                    options = [(f"{p.name}. {PRESET_META[p][0]}", p) for p in Preset]
                    yield Select(options, value=Preset.FULL, id="preset-select", allow_blank=False)
                    yield Label(PRESET_META[Preset.FULL][1], id="preset-desc")
                with Vertical(id="preview-wrap") as wrap:
                    wrap.border_title = "LUA RULE PREVIEW"
                    wrap.border_subtitle = (
                        "e edit | ctrl+e $EDITOR | c copy | ↵/a append | / filter | [ ] preset"
                    )
                    yield TextArea("", language="lua", theme="monokai",
                                   show_line_numbers=True, soft_wrap=False,
                                   read_only=True, id="preview")
                with Horizontal(id="actions"):
                    yield Button("Copy [c]", id="b-copy", classes="btn")
                    yield Button("Append [↵/a]", id="b-append", classes="btn primary")
                    yield Button("Edit [e]", id="b-edit", classes="btn")
                    yield Button("$EDITOR", id="b-ext", classes="btn")
                    yield Button("Refresh [r]", id="b-refresh", classes="btn")
        with Horizontal(id="status"):
            yield Label(self._status, id="status-l")
            yield Label("hl.window_rule | Hyprland 0.56+ | py3.14", id="status-r")
        yield Footer()

    @override
    async def on_mount(self) -> None:
        self.query_one("#preview", TextArea).can_focus = False
        await self.populate()
        self.update_preset_ui()
        self.update_preview()
        self.query_one("#window-list", ListView).focus()

    def on_unmount(self) -> None:
        self._cleanup_tmp()

    def _cleanup_tmp(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @override
    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        guarded = {
            "quit", "refresh", "copy", "append", "edit", "external_edit",
            "prev_preset", "next_preset", "cursor_down", "cursor_up",
            "top", "bottom", "focus_filter",
        }
        if action in guarded:
            focused = self.focused
            if isinstance(focused, Input):
                return False
            if isinstance(focused, TextArea) and not focused.read_only:
                return False
        return True

    @property
    def current(self) -> GeneratedRule | None:
        if not self._filtered:
            return None
        index = min(max(self.selected_idx, 0), len(self._filtered) - 1)
        return self._filtered[index]

    def _edit_key(self) -> tuple[str, Preset] | None:
        rule = self.current
        return None if rule is None else (rule.address, self.current_preset)

    def current_rule_text(self) -> str:
        rule = self.current
        if rule is None:
            return "-- No window matches the filter. Press / and clear it."
        key = (rule.address, self.current_preset)
        cached = self._edits.get(key)
        if cached is not None:
            return cached
        return generate_rule(rule.client, rule.monitor, self.current_preset, self._existing_names)

    async def populate(self) -> None:
        listview = self.query_one("#window-list", ListView)
        await listview.clear()
        items: list[ListItem] = []
        for rule in self._filtered:
            icon = "~" if rule.client.floating else "#"
            title = rule.title if len(rule.title) <= 34 else rule.title[:33] + "..."
            label = (
                f"{icon} [b]{rich_escape(rule.app_class[:22])}[/b]  "
                f"[dim]{rich_escape(rule.client.monitor_name)}:"
                f"{rich_escape(rule.client.workspace_name)}[/dim]\n"
                f"   [dim]{rich_escape(title)}[/dim]"
            )
            items.append(ListItem(Label(label)))
        if items:
            await listview.mount_all(items)
            listview.index = min(max(self.selected_idx, 0), len(items) - 1)

    def update_preset_ui(self) -> None:
        name, desc, icon = PRESET_META[self.current_preset]
        self.query_one("#preset-pill", Label).update(f"{icon} {name} - {desc}")
        self.query_one("#preset-desc", Label).update(desc)

    def update_preview(self) -> None:
        area = self.query_one("#preview", TextArea)
        was_read_only = area.read_only
        area.read_only = False
        area.text = self.current_rule_text()
        area.read_only = was_read_only
        rule = self.current
        if rule is None:
            self.query_one("#status-l", Label).update(rich_escape(self._status))
            return
        self.query_one("#status-l", Label).update(
            f"[#7aa2f7]{rich_escape(rule.app_class)}[/] :: "
            f"{rich_escape(rule.title)}  |  {rich_escape(rule.monitor.name)} "
            f"{rule.client.w}x{rule.client.h}  |  {rich_escape(self._status)}"
        )

    @on(Input.Changed, "#filter")
    async def _filter_changed(self, event: Input.Changed) -> None:
        needle = event.value.lower().strip()
        if needle:
            self._filtered = [
                r for r in self._base
                if needle in r.app_class.lower()
                or needle in r.title.lower()
                or needle in r.client.monitor_name.lower()
                or needle in r.client.workspace_name.lower()
            ]
        else:
            self._filtered = list(self._base)
        self.selected_idx = 0
        await self.populate()
        self.update_preview()

    @on(ListView.Highlighted, "#window-list")
    def _list_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.index is not None:
            self.selected_idx = event.list_view.index
            self.update_preview()

    @on(ListView.Selected, "#window-list")
    def _list_selected(self) -> None:
        """Pressing Enter on a list item appends the rule."""
        self.action_append()

    @on(Select.Changed, "#preset-select")
    def _preset_changed(self, event: Select.Changed) -> None:
        if self._suppress_select or event.value is Select.BLANK:
            return
        if isinstance(event.value, Preset):
            self.current_preset = event.value
            self.update_preset_ui()
            self.update_preview()

    def _set_preset(self, preset: Preset) -> None:
        self.current_preset = preset
        self._suppress_select = True
        try:
            self.query_one("#preset-select", Select).value = preset
        finally:
            self._suppress_select = False
        self.update_preset_ui()
        self.update_preview()

    def action_next_preset(self) -> None:
        values = list(Preset)
        self._set_preset(values[(values.index(self.current_preset) + 1) % len(values)])

    def action_prev_preset(self) -> None:
        values = list(Preset)
        self._set_preset(values[(values.index(self.current_preset) - 1) % len(values)])

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_edit(self) -> None:
        if self.current is None:
            return
        if self.is_editing:
            self.save_edit()
            return
        key = self._edit_key()
        if key is None:
            return
        self._undo.append((key, self._edits.get(key)))
        del self._undo[:-64]
        self.is_editing = True
        area = self.query_one("#preview", TextArea)
        area.read_only = False
        area.can_focus = True
        area.focus()
        self.query_one("#preview-wrap", Vertical).border_title = (
            "EDIT MODE — Esc saves to buffer | ctrl+z undoes"
        )
        self.notify("Edit mode active. Press Esc to save to buffer.", timeout=2)

    def save_edit(self) -> None:
        area = self.query_one("#preview", TextArea)
        key = self._edit_key()
        if key is not None:
            self._edits[key] = area.text
        self.is_editing = False
        area.read_only = True
        area.can_focus = False
        self.query_one("#preview-wrap", Vertical).border_title = "LUA RULE PREVIEW"
        self.query_one("#window-list", ListView).focus()
        self.notify("Saved to in-memory buffer", timeout=1.5)

    def action_undo(self) -> None:
        if not self._undo:
            self.notify("Nothing to undo", timeout=1.5)
            return
        key, previous = self._undo.pop()
        if previous is None:
            self._edits.pop(key, None)
        else:
            self._edits[key] = previous
        self.update_preview()
        self.notify("Undo applied", timeout=1.5)

    def action_external_edit(self) -> None:
        editor = editor_command()
        path = self._tmpdir / "rule.lua"
        try:
            path.write_text(self.query_one("#preview", TextArea).text, encoding="utf-8")
        except OSError as exc:
            self.notify(f"Temp file failed: {exc}", severity="error", timeout=4)
            return
        self.notify(f"Opening {editor[0]}...", timeout=1.2)
        try:
            with self.suspend():
                subprocess.call([*editor, str(path)])
        except SuspendNotSupported:
            subprocess.call([*editor, str(path)])
        try:
            new_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            self.notify(f"Editor read failed: {exc}", severity="error", timeout=4)
            return
        key = self._edit_key()
        if key is not None:
            self._undo.append((key, self._edits.get(key)))
            del self._undo[:-64]
            self._edits[key] = new_text
        self.update_preview()
        self.notify("External edit applied to buffer", timeout=2)

    def action_escape(self) -> None:
        if self.is_editing:
            self.save_edit()
            return
        filter_input = self.query_one("#filter", Input)
        if filter_input.has_focus:
            if filter_input.value:
                filter_input.value = ""
            self.query_one("#window-list", ListView).focus()
            return
        self.exit()

    def action_copy(self) -> None:
        text = self.query_one("#preview", TextArea).text
        if wl_copy(text):
            self._status = "Copied to clipboard via wl-copy"
            self.notify(self._status, timeout=2)
        else:
            self.copy_to_clipboard(text)
            self._status = "Copied via terminal OSC-52 (wl-copy unavailable)"
            self.notify(self._status, severity="warning", timeout=2.5)
        self.update_preview()

    def action_append(self) -> None:
        text = self.query_one("#preview", TextArea).text
        try:
            name = append_rule(text, self._target_file)
        except ValueError as exc:
            self.notify(str(exc), severity="error", timeout=5)
            return
        except OSError as exc:
            self.notify(f"Append failed: {exc}", severity="error", timeout=5)
            return
        self._existing_names.add(name)
        self._status = f'Appended "{name}" to {self._target_file.name}'
        self.notify(self._status, timeout=2.5)
        self.update_preview()

    def _move(self, delta: int, absolute: int | None = None) -> None:
        if not self._filtered:
            return
        listview = self.query_one("#window-list", ListView)
        current = listview.index or 0
        target = absolute if absolute is not None else current + delta
        listview.index = min(max(target, 0), len(self._filtered) - 1)

    def action_cursor_down(self) -> None: self._move(1)
    def action_cursor_up(self) -> None: self._move(-1)
    def action_page_down(self) -> None: self._move(8)
    def action_page_up(self) -> None: self._move(-8)
    def action_top(self) -> None: self._move(0, absolute=0)
    def action_bottom(self) -> None: self._move(0, absolute=len(self._filtered) - 1)

    def action_refresh(self) -> None:
        self.notify("Rescanning hyprctl...", timeout=1.2)
        self._rescan()

    @work(thread=True, exclusive=True, group="scan")
    def _rescan(self) -> None:
        try:
            fresh = scan_windows()
        except Exception as exc:
            self.call_from_thread(self.notify, f"Refresh failed: {exc}", severity="error", timeout=5)
            return
        self.call_from_thread(self._apply_scan, fresh)

    async def _apply_scan(self, fresh: list[GeneratedRule]) -> None:
        self._base = fresh
        self._filtered = list(fresh)
        self._edits.clear()
        self._undo.clear()
        self.selected_idx = 0
        self.query_one("#filter", Input).value = ""
        self.query_one("#sb-title", Label).update(f" Windows ({len(fresh)})   / filter   j/k move")
        self._existing_names = extract_existing_rule_names(self._target_file)
        await self.populate()
        self.update_preview()
        self.notify(f"Refreshed — {len(fresh)} windows", timeout=2)

    @on(Button.Pressed, "#b-copy")
    def _btn_copy(self) -> None: self.action_copy()

    @on(Button.Pressed, "#b-append")
    def _btn_append(self) -> None: self.action_append()

    @on(Button.Pressed, "#b-edit")
    def _btn_edit(self) -> None: self.action_edit()

    @on(Button.Pressed, "#b-ext")
    def _btn_ext(self) -> None: self.action_external_edit()

    @on(Button.Pressed, "#b-refresh")
    def _btn_refresh(self) -> None: self.action_refresh()


# ---------------------------------------------------------------------------
# 9. Main Entry Point & CLI
# ---------------------------------------------------------------------------
def fail(message: str) -> NoReturn:
    print(f"\033[1;31m[ERROR]\033[0m {message}", file=sys.stderr)
    raise SystemExit(1)


def check_hyprland() -> None:
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        fail("HYPRLAND_INSTANCE_SIGNATURE is unset. Run inside an active Hyprland session.")
    try:
        payload = hyprctl_json(["version"], timeout=3)
    except RuntimeError as exc:
        fail(str(exc))
    if not isinstance(payload, dict):
        return
    raw = str(payload.get("tag") or payload.get("version") or "")
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", raw)
    if match:
        found = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if found < MIN_HYPR:
            print(f"[warn] Hyprland {raw}: this tool targets {'.'.join(map(str, MIN_HYPR))}+", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="window_rules_generator.py",
        description="Generate hl.window_rule Lua snippets from live Hyprland windows.",
    )
    parser.add_argument("--list", action="store_true", help="List mapped windows and exit")
    parser.add_argument("--print", dest="print_pattern", metavar="CLASS_REGEX",
                        help="Non-interactive: print rules for matching classes to stdout")
    parser.add_argument("--preset", choices=[p.name for p in Preset], default="FULL",
                        help="Preset used by --print (default: FULL)")
    parser.add_argument("--append", action="store_true",
                        help="With --print: append generated rules to window_rules.lua")
    parser.add_argument("--file", type=Path, default=DEFAULT_TARGET_FILE,
                        help=f"Target Lua config file (default: {DEFAULT_TARGET_FILE})")

    args = parser.parse_args()
    check_hyprland()

    try:
        rules = scan_windows()
    except RuntimeError as exc:
        fail(str(exc))

    if args.list:
        if not rules:
            print("No mapped windows found.")
            return
        for r in rules:
            print(f"{r.client.monitor_name:<10} ws:{r.client.workspace_name:<10} "
                  f"{r.app_class:<30} {r.title}")
        return

    if args.print_pattern is not None:
        try:
            pat = re.compile(args.print_pattern, re.IGNORECASE)
        except re.error as exc:
            fail(f"Invalid regex: {exc}")

        matches = [r for r in rules if pat.search(r.app_class) or pat.search(r.title)]
        if not matches:
            fail(f"No windows match pattern: {args.print_pattern}")

        preset = Preset[args.preset]
        existing = extract_existing_rule_names(args.file)
        chunks: list[str] = []
        for r in matches:
            chunk = generate_rule(r.client, r.monitor, preset, existing)
            chunks.append(chunk)

        combined = "\n".join(chunks)
        print(combined)

        if args.append:
            for chunk in chunks:
                try:
                    name = append_rule(chunk, args.file)
                    print(f"Appended rule '{name}' to {args.file}", file=sys.stderr)
                except ValueError as exc:
                    fail(str(exc))
        return

    if not rules:
        fail("No mapped windows found in Hyprland session.")

    app = DuskyApp(rules, target_file=args.file)
    app.run()


if __name__ == "__main__":
    main()

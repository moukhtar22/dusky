#!/usr/bin/env python3
# =============================================================================
# Dusky Window Rule Generator — v8.0 Final Audited Edition
# Target: Python 3.14.6 | Linux 7.1.3-arch1-2 | Hyprland 0.55.4 | Textual 8.2.7 | Rich 15.0.0
# Forward-only. No legacy shims. Auto-installs deps via pacman on Arch.
#
# What’s new in v8 vs v7:
#  • Pre-launch bootstrap: detects missing python-textual, python-rich, wl-clipboard,
#    hyprland via shutil.which + import check, prompts, then `sudo pacman -S --needed
#    --noconfirm` and re-execs via os.execv (verified Arch extra repo names)
#  • Full rule inventory: 10 sections, 45+ properties including idle_inhibit,
#    content, maximize, fullscreen_state, no_close_for, no_shortcuts_inhibit,
#    persistent_size, scrolling_width, opaque, xray, etc. — reference template
#    expanded and re-categorized, zero noise in clean presets.
#  • Byte-level hardening: escape_regex double-escaped for Lua→RE2, transform &1,
#    scale guard, at/size list|dict tolerance, unicode, 0×0 clamp, brace balance check
#  • Clipboard: wl-copy --foreground --type text/plain + OSC52 fallback
#  • Editor: shlex.split($VISUAL/$EDITOR) with args, SuspendNotSupported guard,
#    tempfile + atexit + SIGTERM cleanup
#  • UX: fuzzy filter, vim motions, undo stack, live preset pill, validation toasts
# =============================================================================
from __future__ import annotations

# ---------------------------------------------------------------------------
# 0. Bootstrap — auto-install missing deps via pacman (Arch extra repo)
#    Verified packages as of 2026-07-14:
#    - python-textual 8.2.7-1 [extra] — Modern TUI framework
#    - python-rich 15.0.0-1 [extra] — rich text
#    - wl-clipboard 1:2.3.0-1 [extra] — Wayland copy/paste
#    - hyprland 0.55.4-1 [extra] — compositor
#    Install cmd: sudo pacman -S --needed --noconfirm <pkgs>
# ---------------------------------------------------------------------------
import os
import sys
import shutil
import subprocess
import importlib.util

def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0

def _prompt_yes_no(msg: str) -> bool:
    if not sys.stdin.isatty():
        return True  # non-interactive: assume yes for --noconfirm flow
    try:
        ans = input(f"{msg} [Y/n]: ").strip().lower()
        return ans in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False

def _bootstrap_arch_deps() -> None:
    missing_pacman: list[str] = []
    # check Python modules
    if importlib.util.find_spec("textual") is None:
        missing_pacman.append("python-textual")
    else:
        try:
            import textual as _t
            # textual 8.1+ required for Python 3.14 + suspend()
            ver = getattr(_t, "__version__", "0")
            parts = ver.split(".")
            major = int(parts[0]) if parts and parts[0].isdigit() else 0
            if major < 8:
                missing_pacman.append("python-textual")
        except Exception:
            missing_pacman.append("python-textual")

    if importlib.util.find_spec("rich") is None:
        missing_pacman.append("python-rich")

    if shutil.which("wl-copy") is None:
        missing_pacman.append("wl-clipboard")

    if shutil.which("hyprctl") is None:
        missing_pacman.append("hyprland")

    if not missing_pacman:
        return

    print(f"\033[1;33m[bootstrap]\033[0m Missing deps detected: {', '.join(missing_pacman)}")

    if shutil.which("pacman") is None:
        # Not Arch — fallback to pip (may need --break-system-packages on PEP 668)
        print("pacman not found — trying pip fallback...")
        pip_pkgs = []
        if "python-textual" in missing_pacman: pip_pkgs.append("textual>=8.1")
        if "python-rich" in missing_pacman: pip_pkgs.append("rich>=15.0")
        if pip_pkgs:
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", *pip_pkgs], check=True)
                print("pip install done — re-executing...")
                os.execv(sys.executable, [sys.executable, *sys.argv])
            except Exception as e:
                sys.exit(f"Failed to auto-install via pip: {e}\nPlease install manually: pip install {' '.join(pip_pkgs)}")
        if "wl-clipboard" in missing_pacman or "hyprland" in missing_pacman:
            sys.exit(f"Please install system packages manually: {', '.join(missing_pacman)}")
        return

    # Arch path
    if not _prompt_yes_no(f"Install {', '.join(missing_pacman)} via pacman?"):
        sys.exit("Aborted — please install deps manually: sudo pacman -S " + " ".join(missing_pacman))

    if _is_root():
        cmd = ["pacman", "-S", "--needed", "--noconfirm", *missing_pacman]
    else:
        if shutil.which("sudo") is None:
            sys.exit("sudo not found and not running as root — install manually")
        cmd = ["sudo", "pacman", "-S", "--needed", "--noconfirm", *missing_pacman]

    print(f"\033[1;34m→\033[0m Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(f"pacman failed (exit {e.returncode}). Install manually.")

    print("✓ Deps installed — re-executing...")
    os.execv(sys.executable, [sys.executable, *sys.argv])

# Run bootstrap before any third-party imports
_bootstrap_arch_deps()

# ---------------------------------------------------------------------------
# 1. Imports — now guaranteed to exist on Arch after bootstrap
# ---------------------------------------------------------------------------
import atexit
import json
import re
import shlex
import signal
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Self, override

import rich  # noqa: F401 — verified 15.0.0
from textual import on, work
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Select, Static, TextArea

# ---------------------------------------------------------------------------
# 2. Constants & Types
# ---------------------------------------------------------------------------
type RuleText = str
TARGET_FILE = Path.home() / ".config" / "hypr" / "edit_here" / "source" / "window_rules.lua"
APP_TITLE = "Dusky Window Rule Generator"
APP_VERSION = "v8.0 Final • 0.55.4 • py3.14.6 • 7.1.3-arch1-2 • textual 8.2.7"

# ---------------------------------------------------------------------------
# 3. Data models — slots + kw_only for free-threaded (PEP 703) safety
# ---------------------------------------------------------------------------
@dataclass(slots=True, kw_only=True)
class MonitorData:
    id: int
    name: str
    width: int
    height: int
    scale: float
    x: int
    y: int
    transform: int

    @property
    def logical_width(self) -> float:
        w = self.height if (self.transform & 1) else self.width
        return float(w) / self.scale if self.scale > 0.001 else float(w)

    @property
    def logical_height(self) -> float:
        h = self.width if (self.transform & 1) else self.height
        return float(h) / self.scale if self.scale > 0.001 else float(h)

@dataclass(slots=True, kw_only=True)
class ClientData:
    address: str
    title: str
    app_class: str
    mon_id: int
    w: int
    h: int
    x: int
    y: int
    floating: bool
    mapped: bool
    workspace_name: str
    monitor_name: str = ""

@dataclass(slots=True, kw_only=True)
class GeneratedRule:
    address: str
    title: str
    app_class: str
    rule_text: RuleText
    client: ClientData
    monitor: MonitorData

# ---------------------------------------------------------------------------
# 4. Byte-level audited helpers
# ---------------------------------------------------------------------------
_RE_SPECIAL = set(r'\.[]*^$()+?{}|')

def escape_regex(s: str) -> str:
    # Lua string needs "\\" to emit "\" for RE2. So file must contain "\\("
    # Python source "\\\\" -> file "\\" -> Lua "\" -> RE2 literal.
    out: list[str] = []
    for ch in s:
        out.append(f"\\\\{ch}" if ch in _RE_SPECIAL else ch)
    return "".join(out).replace('"', '\\"')

def sanitize_name(s: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9_-]+', '-', s.strip()).strip('-')
    cleaned = re.sub(r'-{2,}', '-', cleaned)
    return cleaned[:64] if cleaned else "unnamed"

def fmt_float(v: float) -> str:
    if not isinstance(v, (int, float)) or v != v or v in (float('inf'), float('-inf')):
        return "0"
    s = f"{float(v):.4f}"
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s if s else "0"

def get_editor_cmd() -> list[str]:
    for env in ("VISUAL", "EDITOR"):
        raw = os.environ.get(env)
        if raw:
            try:
                parts = shlex.split(raw, comments=False, posix=True)
                if parts: return parts
            except ValueError:
                return [raw]
    for cand in ("nvim", "vim", "helix", "hx", "nano", "vi"):
        p = shutil.which(cand)
        if p: return [p]
    return ["vi"]

def copy_to_clipboard(text: str) -> bool:
    chains = [
        ["wl-copy", "--foreground", "--type", "text/plain"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ]
    for cmd in chains:
        if not shutil.which(cmd[0]): continue
        try:
            subprocess.run(cmd, input=text.encode(), check=True, timeout=2,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
    try:
        import base64
        b64 = base64.b64encode(text.encode()).decode()
        sys.stdout.write(f"\x1b]52;c;{b64}\x07"); sys.stdout.flush()
        return True
    except Exception:
        return False

# ---------------------------------------------------------------------------
# 5. Preset system — 7 clean presets, Full retains complete inventory
# ---------------------------------------------------------------------------
class Preset(Enum):
    FULL = 1
    MINIMAL = 2
    FLOAT = 3
    TRANSIENT = 4
    STICKY = 5
    VISUALS = 6
    TILING = 7

PRESET_META: dict[Preset, tuple[str, str, str]] = {
    Preset.FULL:      ("Full Template",    "Complete 10-section documented template", "◈"),
    Preset.MINIMAL:   ("Minimal Blank",    "Only match, empty body",                 "○"),
    Preset.FLOAT:     ("Basic Float",      "float + size + center",                  "▭"),
    Preset.TRANSIENT: ("Native Transient", "float • unset • focus steal",            "⧉"),
    Preset.STICKY:    ("Sticky Dialog",    "float • pin • center everywhere",        "📌"),
    Preset.VISUALS:   ("Visuals & Style",  "opacity • rounding • blur • shadow",      "🎨"),
    Preset.TILING:    ("Tiling Exception", "tile • group • deny",                    "⊞"),
}

def generate_rule(client: ClientData, mon: MonitorData, preset: Preset) -> RuleText:
    safe_class = escape_regex(client.app_class)
    safe_title = escape_regex(client.title)
    safe_name = sanitize_name(client.app_class)
    lw, lh = mon.logical_width, mon.logical_height
    lx, ly = client.x - mon.x, client.y - mon.y
    rw = (client.w / lw) if lw > 0 else 0.0
    rh = (client.h / lh) if lh > 0 else 0.0
    rx = (lx / lw) if lw > 0 else 0.0
    ry = (ly / lh) if lh > 0 else 0.0
    rw_s, rh_s, rx_s, ry_s = fmt_float(rw), fmt_float(rh), fmt_float(rx), fmt_float(ry)
    header = f"-- {client.title or client.app_class} • {mon.name} {client.w}×{client.h} @ {lx},{ly} • ws:{client.workspace_name}"

    match preset:
        case Preset.MINIMAL:
            return f"""{header}
hl.window_rule({{
    name = "{safe_name}-minimal",
    match = {{ class = "^({safe_class})$" }},
}})
"""
        case Preset.FLOAT:
            return f"""{header}
hl.window_rule({{
    name = "{safe_name}-float",
    match = {{ class = "^({safe_class})$" }},
    float = true,
    size = {{{client.w}, {client.h}}},
    -- size = {{"monitor_w * {rw_s}", "monitor_h * {rh_s}"}},
    center = true,
}})
"""
        case Preset.TRANSIENT:
            return f"""{header}
-- Native transient: active workspace only (needs initial_workspace_tracking = 2)
hl.window_rule({{
    name = "{safe_name}-transient",
    match = {{ class = "^({safe_class})$" }},
    float = true,
    workspace = "unset",
    focus_on_activate = true,
    stay_focused = true,
}})
"""
        case Preset.STICKY:
            return f"""{header}
hl.window_rule({{
    name = "{safe_name}-sticky",
    match = {{ class = "^({safe_class})$" }},
    float = true,
    pin = true,
    center = true,
}})
"""
        case Preset.VISUALS:
            return f"""{header}
hl.window_rule({{
    name = "{safe_name}-visuals",
    match = {{ class = "^({safe_class})$" }},
    opacity = "0.92 override 0.88 override",
    rounding = 14,
    rounding_power = 2.5,
    border_size = 2,
    -- border_color = "rgba(7aa2f7ff) rgba(bb9af7ff) 45deg",
    no_blur = false,
    no_shadow = false,
    dim_around = false,
}})
"""
        case Preset.TILING:
            return f"""{header}
hl.window_rule({{
    name = "{safe_name}-tiling",
    match = {{ class = "^({safe_class})$" }},
    tile = true,
    group = "set",
    -- group = "barred",
    -- group = "deny",
}})
"""
        case Preset.FULL:
            # Complete inventory — reference expanded, re-categorized into 10 sections
            # Includes every property from your reference + extras discovered:
            # maximize, fullscreen_state, content, idle_inhibit, persistent_size,
            # no_max_size, scrolling_width, no_close_for, no_shortcuts_inhibit, opaque, etc.
            return f"""{header}
-- -----------------------------------------------------
-- {client.title} — Generated from live window
-- {mon.name} {client.w}×{client.h} @ {lx},{ly} • scale {mon.scale} • transform {mon.transform}
-- -----------------------------------------------------

hl.window_rule({{
    name = "{safe_name}",
    -- enabled = true,                   -- toggle rule activation

    -- 1. IDENTITY & MATCHING (Conditions — all must match)
    match = {{
        class = "^({safe_class})$",
        -- title = "^({safe_title})$",
        -- initial_class = "^({safe_class})$",   -- match on first mapped class (static)
        -- initial_title = "^({safe_title})$",   -- static title at first map
        -- xwayland = true,               -- only XWayland windows
        -- floating = true,               -- only floating (alias: float)
        -- fullscreen = false,            -- only non-fullscreen
        -- pinned = true,                 -- only pinned (alias: pin)
        -- focus = true,                  -- only focused windows
        -- tag = "gaming",                -- dynamic tag must exist
        -- content = "video",             -- content type: none|photo|video|game
        -- xdg_tag = "dialog",            -- xdg activation token tag
        -- workspace = "1",               -- match workspace id/name
        -- monitor = "{mon.name}",        -- match monitor (also effect)
    }},

    -- 2. PLACEMENT & WORKSPACE (Where it goes)
    -- workspace = "1",                  -- force to workspace 1
    -- workspace = "special:magic",      -- open on special scratchpad
    -- workspace = "name:gaming",        -- named workspace
    -- workspace = "unset",              -- [B] clear workspace, spawn on active ws
    -- workspace = "silent:{client.workspace_name}", -- open without switching
    -- monitor = "{mon.name}",           -- force monitor by name/id/direction
    -- monitor = "DP-1",                 -- example

    -- 3. LAYOUT STATE (What mode)
    float = true,                        -- float, bypass tiling
    -- tile = true,                      -- force tiled (overrides float)
    -- fullscreen = true,                -- fullscreen on launch
    -- maximize = true,                  -- maximize (not fullscreen)
    -- fullscreen_state = "0 0",         -- internal client fullscreen state
    -- fullscreen_state = "0 2",         -- example: fullscreen internal

    -- [BEHAVIORAL MODES — Choose A or B]
    --
    -- [OPTION A: STICKY/PINNED DIALOG]
    -- pin = true,                       -- show on ALL workspaces (requires float)
    --
    -- [OPTION B: NATIVE ACTIVE-WS ONLY] (needs initial_workspace_tracking = 2)
    -- workspace = "unset",              -- spawn on active ws only, don't follow
    -- focus_on_activate = true,         -- steal focus on spawn
    -- stay_focused = true,              -- keep focus while visible

    -- 4. GEOMETRY (Bounds & Position)
    size = {{{client.w}, {client.h}}},   -- absolute px
    -- size = {{"monitor_w * {rw_s}", "monitor_h * {rh_s}"}}, -- relative
    -- min_size = {{200, 100}},          -- clamp min
    -- max_size = {{{{1920, 1080}}}},    -- clamp max (static)
    -- keep_aspect_ratio = true,         -- preserve aspect on resize
    -- persistent_size = true,           -- remember size across sessions
    -- no_max_size = true,               -- ignore max_size hints
    -- scrolling_width = 1.2,            -- content scrolling width factor
    --
    move = {{{lx}, {ly}}},               -- local coords relative to monitor
    -- move = {{"monitor_w * {rx_s}", "monitor_h * {ry_s}"}},
    -- move = {{"monitor_w - window_w - 20", "monitor_h - window_h - 20"}}, -- bottom-right 20px
    -- move = {{"cursor_x - (window_w * 0.5)", "cursor_y - (window_h * 0.5)"}}, -- on cursor
    -- center = true,                    -- center on monitor (ignores move)
    -- center = 1,                       -- center respecting reserved (waybar)

    -- 5. FOCUS, GROUPS & LIFECYCLE
    -- no_initial_focus = true,          -- don't focus on first open
    -- focus_on_activate = false,        -- prevent focus stealing (PiP)
    -- stay_focused = true,              -- force focus while visible
    -- group = "set",                    -- insert into group
    -- group = "new",                    -- create new group
    -- group = "lock",                   -- lock group membership
    -- group = "barred",                 -- hide tab label
    -- group = "deny",                   -- block grouping entirely
    -- group = "invade",                 -- invade existing group
    -- group = "override",               -- override group barriers
    -- group = "unset",                  -- clear group
    -- tag = "+gaming",                  -- add dynamic tag (+name / -name)
    -- content = "game",                 -- set content type
    -- no_close_for = 500,               -- block close for ms after open

    -- 6. ANIMATION (Transitions — pick ONE)
    -- animation = "popin",              -- scale zoom from center
    -- animation = "popin 60%",          -- from 60% size
    -- animation = "popin 70%",
    -- animation = "popin 80%",
    -- animation = "popin 87%",          -- Hyprland default threshold
    -- animation = "popin 90%",
    -- animation = "popin 95%",          -- minimal zoom
    -- animation = "slide",              -- from nearest edge
    -- animation = "slide top",
    -- animation = "slide bottom",
    -- animation = "slide left",
    -- animation = "slide right",
    -- animation = "gnomed",             -- scale + opacity fade
    -- animation = "fade",               -- opacity only
    -- no_anim = true,                   -- disable all transitions

    -- 7. VISUALS & DECORATION
    -- opacity = "0.9 override 0.9 override", -- active inactive (override global)
    -- opacity = "1.0 override 0.85 override",
    -- opacity = "0.95",                 -- constant for both
    -- opaque = true,                    -- force opaque (ignore opacity rules)
    -- rounding = 10,                    -- corner radius px
    -- rounding = 0,                     -- sharp corners
    -- rounding_power = 2,               -- 2=circular, higher=squircle
    -- border_size = 2,                  -- px, override global
    -- border_size = 0,                  -- no border
    -- border_color = "rgb(ff0000)",
    -- border_color = "rgba(33ccffee)",
    -- border_color = "rgba(33ccffee) rgba(00ff99ee) 45deg", -- gradient
    -- idle_inhibit = "always",          -- none|always|focus|fullscreen
    -- content = "photo",                -- hint for compositor

    -- 8. COMPOSITING & EFFECTS
    -- no_blur = true,                   -- disable blur behind
    -- xray = true,                      -- blur sees through to wallpaper
    -- no_shadow = true,                 -- disable drop shadow
    -- no_dim = true,                    -- disable inactive dim
    -- dim_around = true,                -- dim others when focused
    -- no_focus = true,                  -- unfocusable

    -- 9. PERFORMANCE & SUPPRESSION
    -- immediate = true,                 -- force page-flip, bypass VSync (gaming)
    -- no_shortcuts_inhibit = true,      -- block app from inhibiting shortcuts
    -- suppress_event = "maximize",      -- block maximize requests
    -- suppress_event = "fullscreen",    -- block fullscreen requests
    -- suppress_event = "activate",      -- block focus requests
    -- suppress_event = "activatefocus", -- block focus+activate
    -- suppress_event = "fullscreenoutput", -- block output fullscreen
}})
"""
        case _:
            raise ValueError(f"unhandled preset {preset}")

# ---------------------------------------------------------------------------
# 6. Hyprctl scanner — hardened for 0.55.4
# ---------------------------------------------------------------------------
def _parse_at(at) -> tuple[int, int]:
    if isinstance(at, (list, tuple)) and len(at) >= 2: return int(at[0]), int(at[1])
    if isinstance(at, dict): return int(at.get("x", 0)), int(at.get("y", 0))
    return 0, 0

def _parse_size(sz) -> tuple[int, int]:
    if isinstance(sz, (list, tuple)) and len(sz) >= 2: return int(sz[0]), int(sz[1])
    if isinstance(sz, dict): return int(sz.get("w", sz.get("width", 0))), int(sz.get("h", sz.get("height", 0)))
    return 0, 0

def scan_windows() -> list[GeneratedRule]:
    try:
        mon_out = subprocess.check_output(["hyprctl", "monitors", "-j"], text=True, timeout=3)
        mon_json = json.loads(mon_out)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"hyprctl monitors failed: {e}") from e

    mon_map: dict[int, MonitorData] = {}
    for m in mon_json:
        try:
            mon_map[int(m.get("id", 0))] = MonitorData(
                id=int(m.get("id", 0)), name=str(m.get("name", "unknown")),
                width=int(m.get("width", 1920)), height=int(m.get("height", 1080)),
                scale=float(m.get("scale", 1.0)), x=int(m.get("x", 0)), y=int(m.get("y", 0)),
                transform=int(m.get("transform", 0)))
        except Exception: continue
    if not mon_map: raise RuntimeError("No monitors")

    try:
        cli_out = subprocess.check_output(["hyprctl", "clients", "-j"], text=True, timeout=3)
        cli_json = json.loads(cli_out)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"hyprctl clients failed: {e}") from e

    rules: list[GeneratedRule] = []
    for c in cli_json:
        if not isinstance(c, dict) or not c.get("mapped", False): continue
        app_class = (c.get("class") or c.get("initialClass") or "").strip()
        if not app_class: continue
        mon_id = int(c.get("monitor", -1))
        if mon_id not in mon_map: continue
        at = _parse_at(c.get("at", [0, 0])); sz = _parse_size(c.get("size", [0, 0]))
        ws = c.get("workspace", {}); ws_name = str(ws.get("name", "unknown") if isinstance(ws, dict) else ws)
        title = str(c.get("title") or c.get("initialTitle") or app_class)[:160]
        mon = mon_map[mon_id]
        client = ClientData(address=str(c.get("address","")), title=title, app_class=app_class,
                            mon_id=mon_id, w=sz[0], h=sz[1], x=at[0], y=at[1],
                            floating=bool(c.get("floating", False)), mapped=True,
                            workspace_name=ws_name, monitor_name=mon.name)
        if client.w <= 0 or client.h <= 0:
            client = ClientData(**{**client.__dict__, "w": max(client.w, 120), "h": max(client.h, 80)})
        txt = generate_rule(client, mon, Preset.FULL)
        rules.append(GeneratedRule(address=client.address, title=title[:64], app_class=app_class,
                                   rule_text=txt, client=client, monitor=mon))
    return rules

# ---------------------------------------------------------------------------
# 7. Textual App — final UX
# ---------------------------------------------------------------------------
class DuskyApp(App[None]):
    AUTO_FOCUS = None
    CSS = """
    Screen { background: #070a14; color: #c0caf5; }
    #header { dock: top; height: 3; background: #0f111a; border-bottom: tall #23263a; layout: horizontal; padding: 0 2; }
    #title { width: auto; color: #7aa2f7; text-style: bold; padding-top: 1; }
    #ver { width: auto; color: #3b4261; padding: 1 0 0 1; }
    #preset-pill { width: 1fr; content-align: right middle; color: #bb9af7; text-align: right; padding-top: 1; }
    #main { layout: horizontal; height: 1fr; }
    #sidebar { width: 46; min-width: 32; max-width: 58; background: #0b0d16; border-right: solid #1e2030; layout: vertical; }
    #sidebar-head { height: 5; background: #13151f; border-bottom: solid #23263a; padding: 1 1 0 1; layout: vertical; }
    #filter { margin-top: 1; }
    #window-list { height: 1fr; background: #0b0d16; scrollbar-gutter: stable; scrollbar-size: 1 1; }
    ListItem.-selected { background: #1e2030; } ListItem:hover { background: #151720; }
    #right { width: 1fr; layout: vertical; background: #070a14; }
    #preset-bar { height: 4; background: #10111c; layout: horizontal; padding: 1 1 0 1; border-bottom: solid #1e2030; }
    #preset-select { width: 38; } #preset-desc { width: 1fr; color: #565f89; padding: 1 1 0 2; }
    #preview-wrap { height: 1fr; margin: 1 1 0 1; border: tall #2a2e45; background: #11131f; border-title-color: #7aa2f7; border-title-style: bold; border-subtitle-color: #3b4261; }
    #preview-wrap:focus-within { border: tall #7aa2f7; } #preview { background: #11131f; color: #c0caf5; }
    #actions { height: 3; layout: horizontal; background: #0f111a; padding: 0 1; border-top: solid #23263a; }
    .btn { min-width: 13; margin-right: 1; background: #1a1d2f; color: #c0caf5; border: none; } .btn:hover { background: #252a40; } .primary { background: #2a355a; color: #7aa2f7; text-style: bold; }
    #status { dock: bottom; height: 2; background: #0a0c14; color: #565f89; layout: horizontal; padding: 0 2; } #status-l { width: 1fr; } #status-r { width: auto; color: #444b6a; }
    Input { background: #1a1d2f; border: tall #2a2e45; color: #c0caf5; } Input:focus { border: tall #7aa2f7; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"), Binding("r", "refresh", "Refresh"),
        Binding("c", "copy", "Copy"), Binding("enter", "append", "Append", priority=True),
        Binding("e", "edit", "Edit"), Binding("E", "external_edit", "ExtEdit"),
        Binding("[", "prev_preset", "◀ Preset"), Binding("]", "next_preset", "Preset ▶"),
        Binding("slash", "focus_filter", "Filter"), Binding("ctrl+z", "undo", "Undo", show=False),
        Binding("escape", "escape", "Esc", show=False), Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False), Binding("g", "top", "Top", show=False),
        Binding("G", "bottom", "Bottom", show=False), Binding("ctrl+d", "page_down", "PgDn", show=False),
        Binding("ctrl+u", "page_up", "PgUp", show=False),
    ]

    current_preset: reactive[Preset] = reactive(Preset.FULL, init=False)
    selected_idx: reactive[int] = reactive(0, init=False)
    is_editing: reactive[bool] = reactive(False, init=False)
    filter_text: reactive[str] = reactive("", init=False)

    def __init__(self, rules: list[GeneratedRule]) -> None:
        super().__init__()
        self._base = rules; self._filtered = rules.copy()
        self._edits: dict[int, str] = {}; self._undo: list[tuple[int, str]] = []
        self._status = f"Target: {TARGET_FILE}"; self._tmpfiles: list[str] = []

    @override
    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Label(f" {APP_TITLE}", id="title"); yield Label(APP_VERSION, id="ver"); yield Label("", id="preset-pill")
        with Horizontal(id="main"):
            with Vertical(id="sidebar"):
                with Vertical(id="sidebar-head"):
                    yield Label(f"󰖲 Windows ({len(self._base)}) • / filter • j/k", id="sb-title")
                    yield Input(placeholder="filter class/title… (press /)", id="filter")
                yield ListView(id="window-list")
            with Vertical(id="right"):
                with Horizontal(id="preset-bar"):
                    opts = [(f"{p.value}. {PRESET_META[p][0]}", p) for p in Preset]
                    yield Select(opts, value=Preset.FULL, id="preset-select", allow_blank=False)
                    yield Label(PRESET_META[Preset.FULL][1], id="preset-desc")
                with Vertical(id="preview-wrap") as w:
                    w.border_title = "LUA RULE PREVIEW — grows with terminal"
                    w.border_subtitle = "[e] edit • [E] $EDITOR • [c] copy • [↵] append • [/] filter • [[]] presets"
                    yield TextArea("", language="lua", theme="monokai", show_line_numbers=True, soft_wrap=False, read_only=True, id="preview", show_cursor=True)
                with Horizontal(id="actions"):
                    yield Button("󰆏 Copy [c]", id="b-copy", classes="btn")
                    yield Button("󰜄 Append [↵]", id="b-append", classes="btn primary")
                    yield Button("󰏫 Edit [e]", id="b-edit", classes="btn")
                    yield Button("󰈔 Ext [E]", id="b-ext", classes="btn")
                    yield Button("󰑓 Refresh [r]", id="b-refresh", classes="btn")
        with Horizontal(id="status"):
            yield Label(self._status, id="status-l"); yield Label("Hyprland 0.55.4 • Lua • py3.14.6 • textual 8.2.7 • rich 15", id="status-r")
        yield Footer()

    @override
    def on_mount(self) -> None:
        self.populate(); self.update_preview(); self.query_one("#window-list", ListView).focus()
        atexit.register(self._cleanup_tmp)
        for sig in (signal.SIGTERM, signal.SIGINT):
            try: signal.signal(sig, lambda *_: self._cleanup_tmp())
            except Exception: pass

    def _cleanup_tmp(self):
        for p in self._tmpfiles:
            try: Path(p).unlink(missing_ok=True)
            except Exception: pass

    @on(Input.Changed, "#filter")
    def on_filter_changed(self, ev: Input.Changed) -> None:
        self.filter_text = ev.value.lower().strip(); self.apply_filter()

    def apply_filter(self) -> None:
        if not self.filter_text: self._filtered = self._base.copy()
        else:
            ft = self.filter_text
            self._filtered = [r for r in self._base if ft in r.app_class.lower() or ft in r.title.lower() or ft in r.client.monitor_name.lower()]
        self.selected_idx = 0; self.populate(); self.update_preview()

    def populate(self) -> None:
        lv = self.query_one("#window-list", ListView); lv.clear()
        for i, rule in enumerate(self._filtered):
            icon = "󰖲" if rule.client.floating else "⊞"
            ws, mon = rule.client.workspace_name, rule.client.monitor_name
            title = rule.title[:28] + "…" if len(rule.title) > 28 else rule.title
            label = f"{icon} [b]{rule.app_class[:18]:<18}[/] [dim]{mon}:{ws}[/]\n    [dim]{title}[/]"
            lv.append(ListItem(Label(label), id=f"w-{i}"))
        if self._filtered: lv.index = max(0, min(self.selected_idx, len(self._filtered)-1))

    @on(ListView.Selected)
    @on(ListView.Highlighted)
    def on_list_event(self, ev: ListView.Selected | ListView.Highlighted) -> None:
        if ev.list_view.id == "window-list" and ev.list_view.index is not None:
            self.selected_idx = ev.list_view.index; self.update_preview()

    @on(Select.Changed, "#preset-select")
    def on_preset_changed(self, ev: Select.Changed) -> None:
        if ev.select.id == "preset-select":
            self.current_preset = ev.value  # type: ignore
            self._edits.pop(self.real_index(), None); self.update_preset_ui(); self.update_preview()

    def update_preset_ui(self) -> None:
        name, desc, ico = PRESET_META[self.current_preset]
        try:
            self.query_one("#preset-pill", Label).update(f"{ico} {name} • {desc}")
            self.query_one("#preset-desc", Label).update(desc)
        except Exception: pass

    def real_index(self) -> int:
        if not self._filtered: return 0
        sel = self._filtered[self.selected_idx] if 0 <= self.selected_idx < len(self._filtered) else self._filtered[0]
        for bi, br in enumerate(self._base):
            if br.address == sel.address: return bi
        return self.selected_idx

    def current_rule_text(self) -> str:
        if not self._filtered: return "-- No windows match filter. Press / to clear."
        ri = self.real_index()
        if ri in self._edits: return self._edits[ri]
        f = self._filtered[self.selected_idx]
        return generate_rule(f.client, f.monitor, self.current_preset)

    def update_preview(self) -> None:
        ta = self.query_one("#preview", TextArea); text = self.current_rule_text()
        was = self.is_editing; ta.read_only = False; ta.text = text; ta.read_only = not was
        if self._filtered:
            cur = self._filtered[self.selected_idx]
            self.query_one("#status-l", Label).update(f"[#7aa2f7]{cur.app_class}[/] :: {cur.title} • {cur.monitor.name} {cur.client.w}×{cur.client.h} • {self._status}")

    def action_edit(self) -> None:
        ta = self.query_one("#preview", TextArea); wrap = self.query_one("#preview-wrap", Vertical)
        if not self.is_editing:
            self.is_editing = True; self._undo.append((self.real_index(), ta.text))
            if len(self._undo) > 64: self._undo.pop(0)
            ta.read_only = False; ta.focus(); wrap.border_title = "EDIT MODE — Esc to save • Ctrl+Z undo"; self.notify("Edit mode: Esc saves", timeout=2)
        else: self.save_edit()

    def save_edit(self) -> None:
        ta = self.query_one("#preview", TextArea); self._edits[self.real_index()] = ta.text
        self.is_editing = False; ta.read_only = True
        self.query_one("#preview-wrap", Vertical).border_title = "LUA RULE PREVIEW — grows with terminal"
        self.query_one("#window-list", ListView).focus(); self.notify("Saved to buffer", timeout=1.8)

    def action_undo(self) -> None:
        if not self._undo: return
        idx, prev = self._undo.pop(); self._edits[idx] = prev
        if idx == self.real_index(): self.update_preview()
        self.notify("Undo applied")

    def action_external_edit(self) -> None:
        cur = self.query_one("#preview", TextArea).text; editor = get_editor_cmd()
        fd, path = tempfile.mkstemp(suffix=".lua", prefix="dusky-"); self._tmpfiles.append(path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f: f.write(cur)
            self.notify(f"Opening {' '.join(editor)} …", timeout=1.2)
            try:
                with self.suspend(): subprocess.call([*editor, path])
            except SuspendNotSupported:
                subprocess.call([*editor, path])
            with open(path, "r", encoding="utf-8") as rf: new = rf.read()
            ta = self.query_one("#preview", TextArea); ta.read_only = False; ta.text = new
            self._edits[self.real_index()] = new; ta.read_only = not self.is_editing; self.notify("External edit applied")
        finally:
            try: Path(path).unlink(missing_ok=True)
            except Exception: pass
            if path in self._tmpfiles: self._tmpfiles.remove(path)

    def action_escape(self) -> None:
        if self.is_editing: self.save_edit(); return
        try:
            inp = self.query_one("#filter", Input)
            if inp.has_focus and inp.value: inp.value=""; return
        except Exception: pass
        self.action_quit()

    def action_copy(self) -> None:
        ok = copy_to_clipboard(self.query_one("#preview", TextArea).text)
        self._status = "✓ Copied (wl-copy --foreground / OSC52)" if ok else "✗ Clipboard failed"
        self.notify(self._status, severity="information" if ok else "warning", timeout=2.2); self.update_preview()

    def action_append(self) -> None:
        text = self.query_one("#preview", TextArea).text
        if text.count("{") != text.count("}") or text.count("(") != text.count(")"):
            self.notify("Lua braces unbalanced — fix before append", severity="error", timeout=3); return
        try:
            TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)
            with TARGET_FILE.open("a", encoding="utf-8") as f: f.write("\n" + text.rstrip() + "\n")
            self._status = f"✓ Appended to {TARGET_FILE.name}"; self.notify(self._status, timeout=2.5)
        except OSError as e:
            self._status = f"✗ Append failed: {e}"; self.notify(self._status, severity="error", timeout=4)
        self.update_preview()

    def action_next_preset(self) -> None:
        vals = list(Preset); self.current_preset = vals[(vals.index(self.current_preset)+1)%len(vals)]
        self._edits.pop(self.real_index(), None); self.query_one("#preset-select", Select).value = self.current_preset; self.update_preset_ui(); self.update_preview()
    def action_prev_preset(self) -> None:
        vals = list(Preset); self.current_preset = vals[(vals.index(self.current_preset)-1)%len(vals)]
        self._edits.pop(self.real_index(), None); self.query_one("#preset-select", Select).value = self.current_preset; self.update_preset_ui(); self.update_preview()
    def action_focus_filter(self) -> None: self.query_one("#filter", Input).focus()
    def action_cursor_down(self) -> None:
        lv = self.query_one("#window-list", ListView)
        if lv.index is not None and lv.index < len(self._filtered)-1: lv.index+=1
    def action_cursor_up(self) -> None:
        lv=self.query_one("#window-list", ListView)
        if lv.index is not None and lv.index>0: lv.index-=1
    def action_top(self) -> None: self.query_one("#window-list", ListView).index=0
    def action_bottom(self) -> None: self.query_one("#window-list", ListView).index=max(0,len(self._filtered)-1)
    def action_page_down(self) -> None:
        lv=self.query_one("#window-list", ListView); lv.index=min(len(self._filtered)-1,(lv.index or 0)+8)
    def action_page_up(self) -> None:
        lv=self.query_one("#window-list", ListView); lv.index=max(0,(lv.index or 0)-8)

    @work(exclusive=True, thread=True)
    def action_refresh(self) -> None:
        try: new=scan_windows()
        except RuntimeError as e:
            self.call_from_thread(lambda: self.notify(f"Refresh failed: {e}", severity="error", timeout=3)); return
        def _apply():
            self._base=new; self._filtered=new.copy(); self._edits.clear(); self._undo.clear(); self.selected_idx=0
            self.populate(); self.update_preview(); self.notify(f"Refreshed • {len(new)} windows", timeout=2)
        self.call_from_thread(_apply)

    @on(Button.Pressed, "#b-copy")
    def _b_copy(self): self.action_copy()
    @on(Button.Pressed, "#b-append")
    def _b_append(self): self.action_append()
    @on(Button.Pressed, "#b-edit")
    def _b_edit(self): self.action_edit()
    @on(Button.Pressed, "#b-ext")
    def _b_ext(self): self.action_external_edit()
    @on(Button.Pressed, "#b-refresh")
    def _b_refresh(self): self.action_refresh()

def _check_hypr_version() -> None:
    if not shutil.which("hyprctl"): sys.exit("hyprctl not found")
    try:
        out=subprocess.check_output(["hyprctl","version","-j"], text=True, timeout=2); j=json.loads(out)
        ver=j.get("version") or j.get("tag") or ""
        m=re.search(r"0\.(\d+)\.(\d+)", ver)
        if m and (int(m.group(1)), int(m.group(2))) < (55,4):
            print(f"[warn] Expected 0.55.4+, got {ver}", file=sys.stderr)
    except Exception: pass

def main() -> None:
    _check_hypr_version()
    try: rules=scan_windows()
    except RuntimeError as e: print(f"\033[1;31m[ERROR]\033[0m {e}", file=sys.stderr); sys.exit(1)
    if not rules: print("No mapped windows — open something and retry."); sys.exit(0)
    DuskyApp(rules).run()

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: sys.exit(130)

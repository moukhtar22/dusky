#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: CURSOR CONFIGURATION SCHEMA
===============================================================================
Target: ~/.config/dusky/settings/cursor.conf
Engine: env (KEY=VALUE store consumed by dusky_cursor.py and cursor_size.py)

Controls cursor theme selection, pointer size, and Matugen-adaptive or custom
RGB color recoloring across all desktop layers (Hyprland compositor, XWayland,
GTK 3/4, Qt 5/6, and systemd session environment).
===============================================================================
"""

import os
import sys
from pathlib import Path

# Inject Dusky TUI root into sys.path
_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

# Inject cursor color script directory for helper imports
_CURSOR_DIR = Path.home() / "user_scripts" / "cursor" / "color"
if str(_CURSOR_DIR) not in sys.path:
    sys.path.insert(0, str(_CURSOR_DIR))

try:
    import dusky_cursor
    _conf_theme, _conf_size = dusky_cursor.load_theme_size()
    _DETECTED_SIZE = _conf_size or dusky_cursor.detect_size() or 24
    _DETECTED_THEME = _conf_theme or "Dusky"
except Exception:
    _DETECTED_SIZE = 24
    _DETECTED_THEME = "Dusky"

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "env"
TARGET_FILE = "~/.config/dusky/settings/cursor.conf"
APP_TITLE = "Cursor Settings"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

# Dynamic theme discovery
def _discover_themes() -> tuple[list[str], list[str]]:
    search_dirs = [
        Path.home() / ".local" / "share" / "icons",
        Path.home() / ".icons",
        Path("/usr/local/share/icons"),
        Path("/usr/share/icons"),
    ]
    found = set()
    for base in search_dirs:
        if base.is_dir():
            try:
                for item in base.iterdir():
                    if item.is_dir() and (item / "cursors").is_dir():
                        found.add(item.name)
            except OSError:
                pass

    ordered = []
    # Primary themes first
    if "Dusky" in found:
        ordered.append("Dusky")
        found.remove("Dusky")
    else:
        ordered.append("Dusky")

    if "Bibata-Modern-Classic" in found:
        ordered.append("Bibata-Modern-Classic")
        found.remove("Bibata-Modern-Classic")

    # Add other discovered themes alphabetically
    ordered.extend(sorted(found))

    hints_map = {
        "Dusky": "Matugen-adaptive theme (rebuilt from wallpaper colors)",
        "Bibata-Modern-Classic": "Upstream Bibata theme (black pointer, white edge)",
        "Adwaita": "GNOME standard system cursor",
    }
    hints = [hints_map.get(t, "Installed cursor theme") for t in ordered]
    return ordered, hints

DISCOVERED_THEMES, THEME_HINTS = _discover_themes()

TAB_NOTICES = {
    0: [
        {
            "level": "info",
            "position": "top",
            "message": "Select theme and pointer size. Select **Apply & Rebuild** to push changes across all desktop layers.",
        },
        {
            "level": "info",
            "position": "bottom",
            "message": "**Wayland Note**: After applying, cursor updates reflect once the pointer switches shapes. Move across window borders, hover text, or switch workspaces to refresh.",
        },
    ],
    1: [
        {
            "level": "info",
            "position": "top",
            "message": "Color customizations apply to the **Dusky** theme. Set to 'matugen' to follow your wallpaper automatically.",
        },
        {
            "level": "info",
            "position": "bottom",
            "message": "**Wayland Note**: After applying, cursor updates reflect once the pointer switches shapes. Move across window borders, hover text, or switch workspaces to refresh.",
        },
    ],
    2: {
        "level": "info",
        "position": "top",
        "message": "Press Enter on any color preset to instantly apply it, then run **Apply & Rebuild** on the Cursor tab.",
    },
}

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Cursor",
    "Colors",
    "Presets",
]

COLOR_PRESETS = [
    "matugen",
    "#ffffff",
    "#000000",
    "#ff5555",
    "#faba72",
    "#f8e369",
    "#5ff08a",
    "#5fd8f0",
    "#5f8ff0",
    "#b48cf2",
    "#f06cb0",
    "#83d5c6",
]

COLOR_HINTS = [
    "Matugen",
    "White (#ffffff)",
    "Black (#000000)",
    "Red (#ff5555)",
    "Orange (#faba72)",
    "Yellow (#f8e369)",
    "Green (#5ff08a)",
    "Cyan (#5fd8f0)",
    "Blue (#5f8ff0)",
    "Purple (#b48cf2)",
    "Pink (#f06cb0)",
    "Mint (#83d5c6)",
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA: dict[int, list[ConfigItem]] = {
    # -------------------------------------------------------------------------
    # TAB 0: CURSOR THEME & SIZE
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Cursor Theme",
            key="THEME",
            scope="DEFAULT",
            type_="picker",
            default=_DETECTED_THEME,
            options=DISCOVERED_THEMES,
            hints=THEME_HINTS,
            group="Theme & Size",
            extended_help=(
                "**Cursor Theme**\n\n"
                "Selects the active pointer theme:\n"
                "• **Dusky**: Dynamically recolored from your wallpaper/Matugen theme or custom colors.\n"
                "• **Bibata-Modern-Classic**: Clean black arrow with white border.\n"
                "• **Adwaita**: Standard GNOME cursor."
            ),
        ),
        ConfigItem(
            label="Cursor Size",
            key="SIZE",
            scope="DEFAULT",
            type_="picker",
            default=str(_DETECTED_SIZE),
            options=["16", "18", "20", "24", "28", "32", "36", "40", "48", "56", "64"],
            hints=[
                "16px (Compact)",
                "18px (Dusky Base)",
                "20px",
                "24px (Standard)",
                "28px",
                "32px (HiDPI / 1440p)",
                "36px",
                "40px",
                "48px (4K UHD)",
                "56px",
                "64px (Large)",
            ],
            group="Theme & Size",
            extended_help=(
                "**Cursor Size**\n\n"
                "Pointer size in pixels (16 to 64px).\n"
                "Synchronized with Hyprland shortcuts (SUPER + SHIFT + +/-)."
            ),
        ),
        ConfigItem(
            label="Apply & Rebuild Cursor",
            key="action_apply_rebuild",
            scope="DEFAULT",
            type_="action",
            default="python3 ~/user_scripts/cursor/color/dusky_cursor.py --rebuild",
            group="Actions",
            extended_help=(
                "**Apply & Rebuild Theme**\n\n"
                "Rebuilds the Dusky cursor theme if needed and applies changes immediately across "
                "the Hyprland compositor, XWayland, GTK 3/4, and session environment.\n\n"
                "**Wayland Refresh Note**:\n"
                "After applying, cursor changes reflect once the pointer switches shapes. Move "
                "across window borders, hover text, or switch workspaces to refresh."
            ),
        ),
        ConfigItem(
            label="Restore Bibata Stock Theme",
            key="action_restore_bibata",
            scope="DEFAULT",
            type_="action",
            default="python3 ~/user_scripts/cursor/color/dusky_cursor.py --restore",
            group="Actions",
            confirm_message="Restore Bibata-Modern-Classic cursor theme across all layers?",
            extended_help=(
                "**Restore Bibata Stock Theme**\n\n"
                "Switches the compositor, GTK, and session configuration back to the stock "
                "Bibata-Modern-Classic theme. The Dusky theme build remains installed."
            ),
        ),
        ConfigItem(
            label="Check Cursor Status",
            key="action_check_status",
            scope="DEFAULT",
            type_="action",
            default="python3 ~/user_scripts/cursor/color/dusky_cursor.py --status",
            group="Actions",
            extended_help=(
                "**Check Cursor Status**\n\n"
                "Verifies current cursor theme, size, and palette configuration."
            ),
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: DUSKY THEME RECOLOR
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Border Outline",
            key="BORDER",
            scope="DEFAULT",
            type_="color",
            default="matugen",
            options=COLOR_PRESETS,
            hints=COLOR_HINTS,
            group="Dusky Theme Recolor",
            extended_help=(
                "**Border Outline**\n\n"
                "The light outer border edge of the pointer.\n"
                "Set to `matugen` to automatically follow your wallpaper accent."
            ),
        ),
        ConfigItem(
            label="Base Fill",
            key="BASE",
            scope="DEFAULT",
            type_="color",
            default="matugen",
            options=COLOR_PRESETS,
            hints=COLOR_HINTS,
            group="Dusky Theme Recolor",
            extended_help=(
                "**Base Fill**\n\n"
                "The dark interior fill of the pointer.\n"
                "Set to `matugen` to automatically derive a deep tone from the accent."
            ),
        ),
        ConfigItem(
            label="Accent Override",
            key="ACCENT",
            scope="DEFAULT",
            type_="color",
            default="matugen",
            options=COLOR_PRESETS,
            hints=COLOR_HINTS,
            group="Dusky Theme Recolor",
            extended_help=(
                "**Accent Override**\n\n"
                "Global accent override for border outline and derived base fill.\n"
                "Set to `matugen` to follow your wallpaper colors."
            ),
        ),
        ConfigItem(
            label="Spinner Background",
            key="WATCH_BG",
            scope="DEFAULT",
            type_="color",
            default="matugen",
            options=COLOR_PRESETS,
            hints=COLOR_HINTS,
            group="Dusky Theme Recolor",
            extended_help=(
                "**Spinner Background**\n\n"
                "Background fill for loading and busy pointers.\n"
                "Set to `matugen` to follow your wallpaper background."
            ),
        ),
        ConfigItem(
            label="Apply & Rebuild Theme",
            key="action_apply_colors",
            scope="DEFAULT",
            type_="action",
            default="python3 ~/user_scripts/cursor/color/dusky_cursor.py --rebuild",
            group="Actions",
            extended_help=(
                "**Apply & Rebuild Theme**\n\n"
                "Rebuilds the Dusky theme bitmaps with custom colors and applies them.\n\n"
                "**Wayland Refresh Note**:\n"
                "After applying, cursor changes reflect once the pointer switches shapes. Move "
                "across window borders, hover text, or switch workspaces to refresh."
            ),
        ),
        ConfigItem(
            label="Reset Colors to Matugen",
            key="action_reset_colors",
            scope="DEFAULT",
            type_="action",
            default="python3 ~/user_scripts/cursor/color/dusky_cursor.py --reset-colors",
            group="Actions",
            confirm_message="Reset all color overrides to follow Matugen?",
            extended_help="Clears color overrides from cursor.conf, restoring automatic wallpaper theming.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: CURATED PRESETS
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Matugen Adaptive",
            key="pre_matugen_adaptive",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Adaptive Presets",
            preset_payload={
                "THEME": "Dusky",
                "BASE": "matugen",
                "BORDER": "matugen",
                "ACCENT": "matugen",
                "WATCH_BG": "matugen",
            },
            confirm_message="Switch to Matugen adaptive colors?",
            extended_help="Automatically adapts cursor colors to your wallpaper whenever you switch themes.",
        ),
        ConfigItem(
            label="Bibata Classic (Black & White)",
            key="pre_bibata_bw",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Curated Themes",
            preset_payload={
                "THEME": "Dusky",
                "BASE": "#000000",
                "BORDER": "#ffffff",
                "ACCENT": "#ffffff",
                "WATCH_BG": "#000000",
            },
            confirm_message="Apply Bibata Classic Black & White colors?",
            extended_help="High-contrast black fill with pure white outline.",
        ),
        ConfigItem(
            label="Pure White Minimal",
            key="pre_white_minimal",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Curated Themes",
            preset_payload={
                "THEME": "Dusky",
                "BASE": "#ffffff",
                "BORDER": "#000000",
                "ACCENT": "#ffffff",
                "WATCH_BG": "#ffffff",
            },
            confirm_message="Apply Pure White Minimal colors?",
            extended_help="Inverted white fill with crisp black outline.",
        ),
        ConfigItem(
            label="Sunset Orange",
            key="pre_sunset_orange",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Curated Themes",
            preset_payload={
                "THEME": "Dusky",
                "BASE": "#2b1704",
                "BORDER": "#faba72",
                "ACCENT": "#faba72",
                "WATCH_BG": "#1a0f02",
            },
            confirm_message="Apply Sunset Orange preset?",
            extended_help="Warm amber border with deep espresso fill.",
        ),
        ConfigItem(
            label="Neon Cyan",
            key="pre_neon_cyan",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Curated Themes",
            preset_payload={
                "THEME": "Dusky",
                "BASE": "#04202b",
                "BORDER": "#5fd8f0",
                "ACCENT": "#5fd8f0",
                "WATCH_BG": "#02141a",
            },
            confirm_message="Apply Neon Cyan preset?",
            extended_help="Vibrant cyan outline with deep navy fill.",
        ),
        ConfigItem(
            label="Emerald Green",
            key="pre_emerald_green",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Curated Themes",
            preset_payload={
                "THEME": "Dusky",
                "BASE": "#042b10",
                "BORDER": "#5ff08a",
                "ACCENT": "#5ff08a",
                "WATCH_BG": "#021a0a",
            },
            confirm_message="Apply Emerald Green preset?",
            extended_help="Lush green outline with deep forest fill.",
        ),
        ConfigItem(
            label="Cyberpunk Pink",
            key="pre_cyberpunk_pink",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Curated Themes",
            preset_payload={
                "THEME": "Dusky",
                "BASE": "#2b041a",
                "BORDER": "#f06cb0",
                "ACCENT": "#f06cb0",
                "WATCH_BG": "#1a0210",
            },
            confirm_message="Apply Cyberpunk Pink preset?",
            extended_help="Hot neon pink outline with deep plum fill.",
        ),
        ConfigItem(
            label="Pastel Purple",
            key="pre_pastel_purple",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Curated Themes",
            preset_payload={
                "THEME": "Dusky",
                "BASE": "#1f1433",
                "BORDER": "#b48cf2",
                "ACCENT": "#b48cf2",
                "WATCH_BG": "#120c1f",
            },
            confirm_message="Apply Pastel Purple preset?",
            extended_help="Soft lavender outline with midnight violet fill.",
        ),
    ],
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(
            subprocess.run(
                [sys.executable, str(main_router), str(script_path)] + sys.argv[1:]
            ).returncode
        )
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)

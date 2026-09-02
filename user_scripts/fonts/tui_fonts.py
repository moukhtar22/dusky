#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: FONTCONFIG SCHEMA
===============================================================================
Target: ~/.config/fontconfig/conf.d/99-dusky-fonts.conf
Engine: Fontconfig XML Serializer
===============================================================================
Note: Picker options are discovered dynamically at import time via `fc-list`
so the schema always reflects the fonts actually installed on the machine
(no hardcoded family lists; a curated pool is only a last-resort fallback
when fontconfig tooling is unavailable).
===============================================================================
"""

import sys
from pathlib import Path

# Inject Dusky TUI root into Python path for standalone execution
_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "fontconfig"
TARGET_FILE = "~/.config/fontconfig/conf.d/99-dusky-fonts.conf"
APP_TITLE = "Dusky Fonts"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================

THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
DEFAULT_MODE = "auto"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Profiles"
REQUIRE_ROOT = False

# -------------------------------------------------------------------------
# 2b. TAB NOTICES (banners shown above each tab)
# -------------------------------------------------------------------------
TAB_NOTICES = {
    0: {"level": "info", "message": "New font files dropped into the archive dir appear here after a TUI restart. Applying any change auto-refreshes the font cache AND syncs GTK/Qt/dconf; no manual step needed."},
    2: {"level": "info", "message": "Applies auto-refresh the font cache and sync GTK + Qt (qt5ct/qt6ct) fonts. The manual actions below are only for re-running after manual edits or outside-TUI changes."},
}

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Fonts",
    "Rendering",
    "Cache & Tools",
    "Profiles"
]

# =============================================================================
# 4. INSTALLED-FAMILY DISCOVERY (runtime, not hardcoded)
# -----------------------------------------------------------------------------
# Options are scanned from the live system with `fc-list` so the pickers
# always reflect exactly what is installed. If fontconfig tooling is missing,
# the curated pools below act as a last-resort fallback (they were verified
# present on the authoring machine).
# =============================================================================
import subprocess

_CURATED_FALLBACK = {
    "sans": ["Atkinson Hyperlegible", "Liberation Sans", "Adwaita Sans", "FreeSans"],
    "serif": ["Liberation Serif", "FreeSerif"],
    "mono": [
        "JetBrainsMono Nerd Font Mono", "AtkynsonMono Nerd Font Mono",
        "JetBrainsMono Nerd Font", "FreeMono", "Liberation Mono",
        "Adwaita Mono", "Symbols Nerd Font Mono",
    ],
    "emoji": ["Noto Color Emoji"],
}

_FAMILY_HINTS = {
    "Atkinson Hyperlegible": "High legibility",
    "Liberation Sans": "Metric-compatible Arial",
    "Adwaita Sans": "GNOME default",
    "FreeSans": "GNU free core",
    "Liberation Serif": "Metric-compatible Times New Roman",
    "FreeSerif": "GNU free core serif",
    "JetBrainsMono Nerd Font Mono": "JetBrains IDE default, icon-patched",
    "AtkynsonMono Nerd Font Mono": "Icon-patched mono variant",
    "JetBrainsMono Nerd Font": "JetBrains default (proportional-icons)",
    "FreeMono": "GNU free core mono",
    "Liberation Mono": "Metric-compatible Courier New",
    "Adwaita Mono": "GNOME default mono",
    "Symbols Nerd Font Mono": "Nerd icon symbols (mono metrics)",
    "Noto Color Emoji": "Google's standard emoji",
    "Symbols Nerd Font": "Nerd Font icon symbols (regular metrics)",
    "Font Awesome 7 Free": "Font Awesome icon set",
    "Font Awesome 7 Brands": "Font Awesome brand icons",
}

# =============================================================================
# # 4b. FONT ARCHIVE DIRECTORIES
# -----------------------------------------------------------------------------
# Fontconfig does NOT auto-scan arbitrary folders: only the well-known ones
# (~/.local/share/fonts, system paths) are indexed unless a <dir> element
# lists them in a config file. The font archive picked here gets:
#   * a <dir> entry written by the engine into 99-dusky-fonts.conf so
#     fontconfig + fc-cache index it natively, and
#   * an fc-scan based scan at schema import time (fc-list only sees dirs the
#     config already knows about; fc-scan works on raw font files) so the
#     Typeface pickers list archive families even on the very first run,
#     before the engine has written any config.
# =============================================================================

FONT_ARCHIVE_DIR = "~/user_scripts/fonts/archive"

_ARCHIVE_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2")


def _scan_families() -> dict[str, str]:
    """Discover installed families and their fontconfig spacing metadata.

    Returns {family: spacing} where spacing is the fontconfig spacing
    property ("100"=monospace, "90"=dual-width, "" otherwise, ASCII to keep
    fc-scan/fc-list calls uniform). Queries fc-list for installed fonts and
    fc-scan for raw files under the archive dir (fc-list only sees dirs the
    config already knows about).
    """
    meta: dict[str, str] = {}

    def ingest(stdout: str) -> None:
        for line in stdout.splitlines():
            family, _, spacing = line.strip().partition("\t")
            if family:
                meta[family] = spacing.strip()

    try:
        proc = subprocess.run(
            ["fc-list", "--format=%{family[0]}\t%{spacing}\n", ":"],
            capture_output=True, text=True, timeout=20,
        )
        ingest(proc.stdout)
    except Exception:
        pass

    root = Path(FONT_ARCHIVE_DIR).expanduser()
    if root.is_dir():
        files = sorted(
            f for f in root.rglob("*") if f.suffix.lower() in _ARCHIVE_EXTENSIONS)
        if files:
            try:
                proc = subprocess.run(
                    ["fc-scan", "--format=%{family[0]}\t%{spacing}\n", *[str(f) for f in files]],
                    capture_output=True, text=True, timeout=30,
                )
                ingest(proc.stdout)
            except Exception:
                pass

    return meta


def _classify_families(meta: dict[str, str]) -> dict[str, list[str]]:
    """Classify families into picker buckets using metadata we can trust.

    Order matters:
      * emoji/symbol-ish names come first: icon fonts must never leak into
        mono even when their spacing says monospace (e.g. "Symbols Nerd
        Font Mono").
      * monospace is decided by the fontconfig spacing property, the same
        signal fontconfig itself uses for the monospace generic family;
        name guessing would mislabel proportional "Propo" variants as mono.
      * serif has no authoritative metadata exposed by this fontconfig
        (no PANOSE, no class property), so only families that state their
        genre in the name are classified as serif; ambiguous names
        (OpenDyslexic, Baskerville, ...) land in sans rather than guess.
      * everything else defaults to sans.
    """
    buckets: dict[str, list[str]] = {"sans": [], "serif": [], "mono": [], "emoji": []}
    for fam in sorted(meta):
        low = fam.lower()
        if any(e in low for e in ("emoji", "symbols", "awesome", "icon")):
            buckets["emoji"].append(fam)
        elif meta[fam] in ("100", "90"):
            buckets["mono"].append(fam)
        elif any(s in low for s in ("serif", "times", "georgia")):
            buckets["serif"].append(fam)
        else:
            buckets["sans"].append(fam)
    return buckets


def _scan_installed_families() -> dict[str, list[str]]:
    """Discover installed families on the live system and bucket them.

    Returns {bucket: [family,...]}; falls back to the curated pools if
    fc-list/fc-scan are unavailable or yield nothing.
    """
    buckets = _classify_families(_scan_families())
    if not any(buckets.values()):
        buckets = {k: list(v) for k, v in _CURATED_FALLBACK.items()}
    return buckets


_INSTALLED = _scan_installed_families()

# Options + positionally-aligned hint lists per picker.
def _picker_options(bucket_key: str, default: str) -> tuple[list[str], list[str]]:
    fams = list(_INSTALLED[bucket_key])
    if default not in fams:
        fams.insert(0, default)
    return fams, [_FAMILY_HINTS.get(f, "Installed family") for f in fams]


_SANS_OPTIONS, _SANS_HINTS = _picker_options("sans", "Atkinson Hyperlegible")
_SERIF_OPTIONS, _SERIF_HINTS = _picker_options("serif", "Liberation Serif")
_MONO_OPTIONS, _MONO_HINTS = _picker_options("mono", "JetBrainsMono Nerd Font Mono")
_EMOJI_OPTIONS, _EMOJI_HINTS = _picker_options("emoji", "Noto Color Emoji")

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: FONTS
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="System Sans-Serif Font",
            key="sans-serif",
            scope="DEFAULT",
            type_="picker",
            default="Atkinson Hyperlegible",
            options=_SANS_OPTIONS,
            hints=_SANS_HINTS,
            extended_help="Sets the primary Sans-Serif font used across the desktop environment (e.g., Waybar, Hyprland, GTK apps). Options are scanned live from fc-list, so only installed families appear."
        ),
        ConfigItem(
            label="System Serif Font",
            key="serif",
            scope="DEFAULT",
            type_="picker",
            default="Liberation Serif",
            options=_SERIF_OPTIONS,
            hints=_SERIF_HINTS,
            extended_help="Sets the primary Serif font. Options are scanned live from fc-list, so only installed families appear."
        ),
        ConfigItem(
            label="System Monospace Font",
            key="monospace",
            scope="DEFAULT",
            type_="picker",
            default="JetBrainsMono Nerd Font Mono",
            options=_MONO_OPTIONS,
            hints=_MONO_HINTS,
            extended_help="Sets the primary fixed-width font. Terminal/editor fonts: options are scanned live from fc-list; patched Nerd Fonts are required for icon rendering in terminals."
        ),
        ConfigItem(
            label="System Emoji/Icons Font",
            key="emoji",
            scope="DEFAULT",
            type_="picker",
            default="Noto Color Emoji",
            options=_EMOJI_OPTIONS,
            hints=_EMOJI_HINTS,
            extended_help="Forces the system-wide fallback for emoji rendering. Options are scanned live from fc-list; only installed families appear."
        ),
        ConfigItem(
            label="Font Archive Directory",
            key="font_dir",
            scope="DEFAULT",
            type_="string",
            default="~/user_scripts/fonts/archive",
            extended_help="Directory containing downloadable fonts (TTF/OTF/TTC/OTF variable weight, WOFF supported; WOFF2 scan-only). Drop new font files here, then use the System & Cache 'Force Verbose Cache Rebuild' action. The engine writes a <dir> entry into 99-dusky-fonts.conf so fontconfig indexes it natively, and Typeface pickers auto-scan it for families."
        ),
    ],
    
    # -------------------------------------------------------------------------
    # TAB 1: RENDERING
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Enable Antialiasing",
            key="antialias",
            scope="DEFAULT",
            type_="bool",
            default=True,
            extended_help="Smooths the jagged edges of fonts. Highly recommended for modern high-DPI and standard displays."
        ),
        ConfigItem(
            label="Enable Font Hinting",
            key="hinting",
            scope="DEFAULT",
            type_="bool",
            default=True,
            extended_help="Master switch for font hinting. When enabled, FreeType aligns font glyphs to pixel boundaries for sharp text rendering."
        ),
        ConfigItem(
            label="Enable Auto-Hinting",
            key="autohint",
            scope="DEFAULT",
            type_="bool",
            default=False,
            extended_help="Forces FreeType's automatic hinting algorithm when a font's native hinting instructions are insufficient. Recommended only for fonts with broken/absent native hints; keep off by default."
        ),
        ConfigItem(
            label="Hinting Style",
            key="hintstyle",
            scope="DEFAULT",
            type_="picker",
            default="hintslight",
            options=["hintnone", "hintslight", "hintmedium", "hintfull"],
            hints=[
                "No pixel alignment", "Light alignment (Recommended)", 
                "Medium alignment", "Strict pixel alignment"
            ],
            extended_help="Controls how font outlines are aligned to the screen's pixel grid. 'hintslight' is heavily recommended for FreeType on modern Arch Linux."
        ),
        ConfigItem(
            label="Subpixel Geometry (RGBA)",
            key="rgba",
            scope="DEFAULT",
            type_="picker",
            default="rgb",
            options=["none", "rgb", "bgr", "vrgb", "vbgr"],
            hints=[
                "Grayscale smoothing", "Standard horizontal (Most common)", 
                "Reversed horizontal", "Standard vertical", "Reversed vertical"
            ],
            extended_help="Configures subpixel rendering for LCD displays. 'rgb' is correct for 99% of modern desktop monitors."
        ),
        ConfigItem(
            label="LCD Filter",
            key="lcdfilter",
            scope="DEFAULT",
            type_="picker",
            default="lcddefault",
            options=["lcdnone", "lcddefault", "lcdlight", "lcdlegacy"],
            hints=[
                "No color fringing filter", "Standard filter (Recommended)", 
                "Light fringing filter", "Legacy FreeType filter"
            ],
            extended_help="Reduces color fringing when subpixel rendering is active. 'lcddefault' ensures optimal text clarity."
        ),
        ConfigItem(
            label="Enable Embedded Bitmaps",
            key="embeddedbitmap",
            scope="DEFAULT",
            type_="bool",
            default=True,
            extended_help="Controls whether fonts with embedded bitmap glyphs display bitmaps at small sizes. Must be enabled for color emoji fonts (e.g. Noto Color Emoji) which are bitmap-only (CBDT/CBLC) and have no scalable outlines to fall back to — disabling this breaks emoji rendering entirely."
        )
    ],

    # -------------------------------------------------------------------------
    # TAB 2: CACHE & TOOLS
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Force Verbose Cache Rebuild",
            key="trigger_refresh",
            scope="DEFAULT",
            type_="action",
            default="fc-cache -fv",
            options=["trigger"],
            force_interactive=True,
            confirm_message="Are you sure you want to manually rebuild the font cache? This may take several seconds.",
            extended_help="Executes `fc-cache -fv` to force an immediate, verbose rebuild of the system font cache, bypassing the background refresh."
        ),
        ConfigItem(
            label="Verify Sans-Serif Resolution (fc-match)",
            key="trigger_verify_sans",
            scope="DEFAULT",
            type_="action",
            default="fc-match sans-serif",
            options=["trigger"],
            popup_message="Check status bar for the test output.",
            extended_help="Executes `fc-match sans-serif` to verify which exact font file fontconfig resolves for sans-serif requests."
        ),
        ConfigItem(
            label="Verify Monospace Font Resolution (fc-match) ",
            key="trigger_verify_mono",
            scope="DEFAULT",
            type_="action",
            default="fc-match monospace",
            options=["trigger"],
            popup_message="Check status bar for the test output.",
            extended_help="Executes `fc-match monospace` to verify which exact font file fontconfig resolves for terminal/code requests."
        ),
        ConfigItem(
            label="Verify Arial Aliasing Fallback",
            key="trigger_verify",
            scope="DEFAULT",
            type_="action",
            default="fc-match 'Arial'",
            options=["trigger"],
            popup_message="Check status bar for the test output.",
            extended_help="Executes a test match to verify which exact font file the system currently falls back to when 'Arial' is requested."
        ),
        ConfigItem(
            label="Sync Xft Rendering to X11 (legacy)",
            key="trigger_sync_xresources",
            scope="DEFAULT",
            type_="action",
            default="mkdir -p ~/.config/dusky && printf 'Xft.antialias: 1\\nXft.hinting: 1\\nXft.hintstyle: hintslight\\nXft.rgba: rgba\\nXft.lcdfilter: lcddefault\\n' > ~/.config/dusky/xresources && xrdb -merge ~/.config/dusky/xresources 2>/dev/null || true",
            options=["trigger"],
            popup_message="Synced Xft rendering properties to ~/.config/dusky/xresources and merged with xrdb.",
            extended_help="Legacy X11 syncing: writes Xft rendering properties into a dedicated ~/.config/dusky/xresources file and merges it with xrdb. Does not truncate any existing ~/.Xresources."
        ),
        ConfigItem(
            label="Sync GTK & Qt Fonts (settings.ini + qt5ct/qt6ct)",
            key="trigger_sync_gtk",
            scope="DEFAULT",
            type_="action",
            default="python3 ~/user_scripts/dusky_tui/python/engines/fontconfig.py",
            options=["trigger"],
            popup_message="Synced GTK font-name and Qt general/fixed to the configured families.",
            extended_help="Writes gtk-font-name (family + current size) into ~/.config/gtk-3.0/settings.ini and gtk-4.0/settings.ini, mirrors org.gnome.desktop.interface font-name/document-font-name via gsettings, and rewrites the [Fonts] general/fixed entries in qt5ct.conf and qt6ct.conf (sans-serif -> general, monospace -> fixed), preserving existing sizes/weights. This runs automatically on every apply; use the action to re-sync after manual edits."
        )
    ],
    
    # -------------------------------------------------------------------------
    # TAB 3: PROFILES (Presets)
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Apply Modern Sharp UI Profile",
            key="preset_modern_sharp",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System Defaults",
            preset_payload={
                "sans-serif": "Atkinson Hyperlegible",
                "serif": "Liberation Serif",
                "monospace": "JetBrainsMono Nerd Font Mono",
                "emoji": "Noto Color Emoji",
                "antialias": True,
                "hinting": True,
                "hintstyle": "hintslight",
                "rgba": "rgb",
                "lcdfilter": "lcddefault",
                "embeddedbitmap": True
            },
            extended_help="**Modern Sharp UI**\n\nApplies highly modern, crisp fonts with standard RGB subpixel rendering and slight hinting. Ideal for high-resolution standard monitors."
        ),
        ConfigItem(
            label="Apply Accessibility & Legibility Profile",
            key="preset_accessibility",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System Defaults",
            preset_payload={
                "sans-serif": "Atkinson Hyperlegible",
                "serif": "Liberation Serif",
                "monospace": "JetBrainsMono Nerd Font Mono",
                "emoji": "Noto Color Emoji",
                "antialias": True,
                "hinting": True,
                "hintstyle": "hintslight",
                "embeddedbitmap": True
            },
            extended_help="**Accessibility Focus**\n\nPrioritizes character distinction using Atkinson Hyperlegible (Braille Institute) to prevent visual confusion between similar characters like '1', 'l', and 'I'."
        ),
        ConfigItem(
            label="Apply 4K / High-DPI Clean Profile",
            key="preset_hidpi_clean",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System Defaults",
            preset_payload={
                "sans-serif": "Liberation Sans",
                "serif": "Liberation Serif",
                "monospace": "JetBrainsMono Nerd Font Mono",
                "emoji": "Noto Color Emoji",
                "antialias": True,
                "hinting": True,
                "hintstyle": "hintnone",
                "rgba": "none",
                "lcdfilter": "lcdnone",
                "embeddedbitmap": True
            },
            extended_help="**High-DPI / 4K Clean Profile**\n\nOptimized for 4K and Retina-class displays. Disables subpixel LCD geometry (`rgba=none`) and pixel grid alignment (`hintstyle=hintnone`) for ultra-clean pure vector outline rendering."
        ),
        ConfigItem(
            label="Apply Legacy Linux Defaults",
            key="preset_legacy_linux",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System Defaults",
            preset_payload={
                "sans-serif": "Liberation Sans",
                "serif": "Liberation Serif",
                "monospace": "FreeMono",
                "emoji": "Noto Color Emoji",
                "antialias": True,
                "hinting": True,
                "hintstyle": "hintfull",
                "embeddedbitmap": True
            },
            extended_help="**Legacy Linux Config**\n\nRestores the classic open-source desktop appearance using metric-compatible Liberation/Free fonts alongside strict/full hinting pixel alignment."
        ),
    ]
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import sys, subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    # Route execution to the main Dusky TUI router
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)

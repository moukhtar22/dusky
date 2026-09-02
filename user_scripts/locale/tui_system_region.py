#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: SYSTEM REGION & LOCALE MANAGER SCHEMA
===============================================================================
Target: /etc/locale.gen
Engine: locale_gen
Comprehensive region, clock, locale (/etc/locale.conf), and console/Wayland
keyboard manager (/etc/vconsole.conf) for modern Linux systems.
"""
import sys
import zoneinfo
from pathlib import Path

# Dynamically locate Dusky TUI root without hardcoded paths or username assumptions
_DUSKY_TUI_ROOT = Path(__file__).resolve().parents[1] / "dusky_tui"
if not _DUSKY_TUI_ROOT.exists():
    _DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "locale_gen"
TARGET_FILE = "/etc/locale.gen"
APP_TITLE = "System Region & Locale Manager"
REQUIRE_ROOT = True

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

# =============================================================================
# 3. DYNAMIC METADATA & PICKER OPTIONS
# =============================================================================
_POPULAR_TZS = [
    "Asia/Kolkata",
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Toronto",
    "America/Vancouver",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Rome",
    "Europe/Madrid",
    "Europe/Amsterdam",
    "Europe/Stockholm",
    "Europe/Warsaw",
    "Europe/Kyiv",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Asia/Hong_Kong",
    "Asia/Singapore",
    "Asia/Seoul",
    "Asia/Dubai",
    "Australia/Sydney",
    "Australia/Melbourne",
    "Pacific/Auckland",
]
try:
    _ALL_TZS = sorted(zoneinfo.available_timezones())
    _TIMEZONE_OPTIONS = _POPULAR_TZS + [tz for tz in _ALL_TZS if tz not in _POPULAR_TZS]
except Exception:
    _TIMEZONE_OPTIONS = _POPULAR_TZS

_POPULAR_LOCALES = [
    "en_US.UTF-8",
    "en_GB.UTF-8",
    "en_CA.UTF-8",
    "en_AU.UTF-8",
    "en_IN",
    "de_DE.UTF-8",
    "fr_FR.UTF-8",
    "es_ES.UTF-8",
    "it_IT.UTF-8",
    "ja_JP.UTF-8",
    "zh_CN.UTF-8",
    "zh_TW.UTF-8",
    "ko_KR.UTF-8",
    "pt_BR.UTF-8",
    "pt_PT.UTF-8",
    "ru_RU.UTF-8",
    "nl_NL.UTF-8",
    "pl_PL.UTF-8",
    "sv_SE.UTF-8",
    "hi_IN",
    "C.UTF-8",
    "C",
]

_POPULAR_KEYMAPS = [
    "us",
    "uk",
    "de",
    "fr",
    "es",
    "it",
    "jp106",
    "dvorak",
    "colemak",
    "pl",
    "ru",
    "se",
    "no",
    "dk",
    "fi",
    "br-abnt2",
    "ca",
]

_POPULAR_FONTS = [
    "default8x16",
    "ter-v16n",
    "ter-v24n",
    "ter-v32n",
    "eurlatgr",
    "Lat2-Terminus16",
    "lat9w-16",
    "iso01-12x22",
]

# =============================================================================
# 4. TABS DEFINITION
# =============================================================================
TABS = [
    "Time & Date",
    "System Locale",
    "Keyboard & Console",
    "Popular Locales",
    "World Locales",
    "System Actions",
]

# =============================================================================
# 5. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: TIME & DATE / SYSTEMD TIMEDATED
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="System Timezone",
            key="timezone",
            scope="DEFAULT",
            type_="picker",
            options=_TIMEZONE_OPTIONS,
            default="Asia/Kolkata",
            group="Timezone & Clock",
            extended_help=(
                "**System Timezone (`timedatectl set-timezone`)**\n\n"
                "Configures the local system timezone symlink (`/etc/localtime`).\n\n"
                "- Select your region and city from the list.\n"
                "- Synchronizes timezone across systemd services, logs, and user desktop."
            ),
        ),
        ConfigItem(
            label="NTP Time Synchronization",
            key="ntp_sync",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Timezone & Clock",
            extended_help=(
                "**Network Time Protocol (NTP) Sync**\n\n"
                "Enables automatic network clock synchronization via `systemd-timesyncd` (`timedatectl set-ntp true`)."
            ),
        ),
        ConfigItem(
            label="Hardware Clock (RTC) in Local Time",
            key="rtc_local",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Timezone & Clock",
            extended_help=(
                "**Real-Time Clock (RTC) Local Time Mode**\n\n"
                "- `False` (Recommended): Hardware clock is maintained in UTC.\n"
                "- `True`: Hardware clock is maintained in local timezone (useful when dual-booting with Windows without UTC registry fix)."
            ),
        ),
        ConfigItem(
            label="Interactive Timezone Search (fzf)",
            key="action_set_timezone",
            scope="DEFAULT",
            type_="action",
            default="tz=$(timedatectl list-timezones | fzf --prompt='Select System Timezone > ') && [ -n \"$tz\" ] && timedatectl set-timezone \"$tz\"",
            group="Quick Actions",
            extended_help="Interactively select and apply system timezone via fuzzy finder (`fzf`).",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: SYSTEM LOCALE & FORMATS (/etc/locale.conf)
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Primary System Locale (LANG)",
            key="LANG",
            scope="DEFAULT",
            type_="picker",
            options=_POPULAR_LOCALES,
            default="en_US.UTF-8",
            group="System Language",
            extended_help=(
                "**Primary System Locale (`LANG` in `/etc/locale.conf`)**\n\n"
                "Controls the default language, encoding, and regional formatting for the entire system.\n\n"
                "Ensure the selected locale is uncommented in `/etc/locale.gen` and compiled with `locale-gen`."
            ),
        ),
        ConfigItem(
            label="Time & Date Format (LC_TIME)",
            key="LC_TIME",
            scope="DEFAULT",
            type_="picker",
            options=["unset"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Overrides date and clock format (e.g. 24-hour vs 12-hour, date ordering).",
        ),
        ConfigItem(
            label="Numeric Format (LC_NUMERIC)",
            key="LC_NUMERIC",
            scope="DEFAULT",
            type_="picker",
            options=["unset"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Overrides numeric format and decimal separator (point vs comma).",
        ),
        ConfigItem(
            label="Monetary Format (LC_MONETARY)",
            key="LC_MONETARY",
            scope="DEFAULT",
            type_="picker",
            options=["unset"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Overrides currency formatting and monetary symbols.",
        ),
        ConfigItem(
            label="Paper Dimensions (LC_PAPER)",
            key="LC_PAPER",
            scope="DEFAULT",
            type_="picker",
            options=["unset"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Overrides default paper size standard (A4 vs Letter).",
        ),
        ConfigItem(
            label="Measurement Units (LC_MEASUREMENT)",
            key="LC_MEASUREMENT",
            scope="DEFAULT",
            type_="picker",
            options=["unset"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Overrides units of measurement (Metric vs Imperial).",
        ),
        ConfigItem(
            label="Collation & Sorting (LC_COLLATE)",
            key="LC_COLLATE",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "C", "C.UTF-8"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Controls alphabetical sorting order of filenames and text (`C` or `C.UTF-8` for byte-order sorting).",
        ),
        ConfigItem(
            label="System Messages & UI (LC_MESSAGES)",
            key="LC_MESSAGES",
            scope="DEFAULT",
            type_="picker",
            options=["unset"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Controls the language of system messages, prompts, and application UI text.",
        ),
        ConfigItem(
            label="Address Format (LC_ADDRESS)",
            key="LC_ADDRESS",
            scope="DEFAULT",
            type_="picker",
            options=["unset"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Overrides conventions used to format postal addresses and country identifiers.",
        ),
        ConfigItem(
            label="Telephone Format (LC_TELEPHONE)",
            key="LC_TELEPHONE",
            scope="DEFAULT",
            type_="picker",
            options=["unset"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Overrides conventions used to format telephone numbers.",
        ),
        ConfigItem(
            label="Name Format (LC_NAME)",
            key="LC_NAME",
            scope="DEFAULT",
            type_="picker",
            options=["unset"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Overrides conventions used to format personal names, honorifics, and salutations.",
        ),
        ConfigItem(
            label="Character Classification (LC_CTYPE)",
            key="LC_CTYPE",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "C.UTF-8", "en_US.UTF-8"] + _POPULAR_LOCALES,
            default="unset",
            group="Regional Overrides",
            extended_help="Controls character classification and case conversion rules (upper/lowercase matching).",
        ),
        ConfigItem(
            label="Interactive LANG Selection (fzf)",
            key="action_set_lang",
            scope="DEFAULT",
            type_="action",
            default="loc=$(grep -v '^#' /etc/locale.gen | awk '{print $1}' | fzf --prompt='Select System LANG Locale > ') && [ -n \"$loc\" ] && localectl set-locale LANG=\"$loc\"",
            group="Quick Actions",
            extended_help="Fuzzy search and apply system `LANG` from currently enabled locales in `/etc/locale.gen`.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: KEYBOARD & CONSOLE (/etc/vconsole.conf)
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="TTY Console Keymap (KEYMAP)",
            key="KEYMAP",
            scope="DEFAULT",
            type_="picker",
            options=_POPULAR_KEYMAPS,
            default="us",
            group="Virtual Console (TTY)",
            extended_help=(
                "**Virtual Console Keyboard Layout (`KEYMAP` in `/etc/vconsole.conf`)**\n\n"
                "Sets the keyboard mapping used in Linux TTY terminals and early boot."
            ),
        ),
        ConfigItem(
            label="Virtual Console Font (FONT)",
            key="FONT",
            scope="DEFAULT",
            type_="picker",
            options=_POPULAR_FONTS,
            default="default8x16",
            group="Virtual Console (TTY)",
            extended_help="Configures the console font loaded during early boot (`/etc/vconsole.conf`).",
        ),
        ConfigItem(
            label="Wayland / XKB Layout (XKBLAYOUT)",
            key="XKBLAYOUT",
            scope="DEFAULT",
            type_="string",
            default="us",
            group="Graphical Keyboard Layout",
            extended_help="Wayland and XKB keyboard layout (e.g. `us`, `gb`, `de`, `fr`, `in`, `es`).",
        ),
        ConfigItem(
            label="Wayland / XKB Model (XKBMODEL)",
            key="XKBMODEL",
            scope="DEFAULT",
            type_="string",
            default="pc105+inet",
            group="Graphical Keyboard Layout",
            extended_help="Keyboard hardware model identifier (standard: `pc105+inet`).",
        ),
        ConfigItem(
            label="Wayland / XKB Variant (XKBVARIANT)",
            key="XKBVARIANT",
            scope="DEFAULT",
            type_="string",
            default="",
            group="Graphical Keyboard Layout",
            extended_help="Keyboard layout variant (e.g. `intl`, `altgr-intl`, `dvorak`, `colemak`, `nodeadkeys`).",
        ),
        ConfigItem(
            label="Wayland / XKB Options (XKBOPTIONS)",
            key="XKBOPTIONS",
            scope="DEFAULT",
            type_="string",
            default="terminate:ctrl_alt_bksp",
            group="Graphical Keyboard Layout",
            extended_help="Keyboard modifier options (e.g. `terminate:ctrl_alt_bksp`, `caps:escape`, `caps:swapescape`, `grp:alt_shift_toggle`).",
        ),
        ConfigItem(
            label="Interactive TTY Keymap Search (fzf)",
            key="action_set_keymap",
            scope="DEFAULT",
            type_="action",
            default="km=$(localectl list-keymaps | fzf --prompt='Select TTY Keymap > ') && [ -n \"$km\" ] && localectl set-keymap \"$km\"",
            group="Quick Actions",
            extended_help="Fuzzy search and apply TTY keymap via `localectl set-keymap`.",
        ),
        ConfigItem(
            label="Interactive Wayland Layout Search (fzf)",
            key="action_set_x11_keymap",
            scope="DEFAULT",
            type_="action",
            default="layout=$(localectl list-x11-keymap-layouts | fzf --prompt='Select Wayland/XKB Layout > ') && [ -n \"$layout\" ] && localectl set-x11-keymap \"$layout\"",
            group="Quick Actions",
            extended_help="Fuzzy search and apply graphical Wayland/XKB keyboard layout via `localectl set-x11-keymap`.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: POPULAR LOCALES (/etc/locale.gen)
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="American English (en_US.UTF-8)",
            key="en_US.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Popular Locales",
            extended_help="Standard American English UTF-8 locale.",
        ),
        ConfigItem(
            label="British English (en_GB.UTF-8)",
            key="en_GB.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="British English UTF-8 locale.",
        ),
        ConfigItem(
            label="German / Deutschland (de_DE.UTF-8)",
            key="de_DE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Standard German UTF-8 locale.",
        ),
        ConfigItem(
            label="French / France (fr_FR.UTF-8)",
            key="fr_FR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Standard French UTF-8 locale.",
        ),
        ConfigItem(
            label="Spanish / España (es_ES.UTF-8)",
            key="es_ES.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Standard Spanish UTF-8 locale.",
        ),
        ConfigItem(
            label="Italian / Italia (it_IT.UTF-8)",
            key="it_IT.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Standard Italian UTF-8 locale.",
        ),
        ConfigItem(
            label="Japanese / 日本語 (ja_JP.UTF-8)",
            key="ja_JP.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Standard Japanese UTF-8 locale.",
        ),
        ConfigItem(
            label="Simplified Chinese / 简体中文 (zh_CN.UTF-8)",
            key="zh_CN.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Simplified Chinese UTF-8 locale.",
        ),
        ConfigItem(
            label="Traditional Chinese / 繁體中文 (zh_TW.UTF-8)",
            key="zh_TW.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Traditional Chinese UTF-8 locale.",
        ),
        ConfigItem(
            label="Korean / 한국어 (ko_KR.UTF-8)",
            key="ko_KR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Standard Korean UTF-8 locale.",
        ),
        ConfigItem(
            label="Brazilian Portuguese (pt_BR.UTF-8)",
            key="pt_BR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Brazilian Portuguese UTF-8 locale.",
        ),
        ConfigItem(
            label="Russian / Россия (ru_RU.UTF-8)",
            key="ru_RU.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Standard Russian UTF-8 locale.",
        ),
        ConfigItem(
            label="Indian English (en_IN)",
            key="en_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular Locales",
            extended_help="Indian English UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: WORLD LOCALES (/etc/locale.gen)
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Australian English (en_AU.UTF-8)",
            key="en_AU.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English Variants",
            extended_help="Australian English UTF-8 locale.",
        ),
        ConfigItem(
            label="Canadian English (en_CA.UTF-8)",
            key="en_CA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English Variants",
            extended_help="Canadian English UTF-8 locale.",
        ),
        ConfigItem(
            label="Irish English (en_IE.UTF-8)",
            key="en_IE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English Variants",
            extended_help="Irish English UTF-8 locale.",
        ),
        ConfigItem(
            label="New Zealand English (en_NZ.UTF-8)",
            key="en_NZ.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English Variants",
            extended_help="New Zealand English UTF-8 locale.",
        ),
        ConfigItem(
            label="Singapore English (en_SG.UTF-8)",
            key="en_SG.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English Variants",
            extended_help="Singapore English UTF-8 locale.",
        ),
        ConfigItem(
            label="Portuguese / Portugal (pt_PT.UTF-8)",
            key="pt_PT.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Portuguese UTF-8 locale.",
        ),
        ConfigItem(
            label="Dutch / Nederland (nl_NL.UTF-8)",
            key="nl_NL.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Dutch UTF-8 locale.",
        ),
        ConfigItem(
            label="Polish / Polska (pl_PL.UTF-8)",
            key="pl_PL.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Polish UTF-8 locale.",
        ),
        ConfigItem(
            label="Swedish / Sverige (sv_SE.UTF-8)",
            key="sv_SE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Swedish UTF-8 locale.",
        ),
        ConfigItem(
            label="Norwegian / Norge (nb_NO.UTF-8)",
            key="nb_NO.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Norwegian Bokmål UTF-8 locale.",
        ),
        ConfigItem(
            label="Danish / Danmark (da_DK.UTF-8)",
            key="da_DK.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Danish UTF-8 locale.",
        ),
        ConfigItem(
            label="Finnish / Suomi (fi_FI.UTF-8)",
            key="fi_FI.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Finnish UTF-8 locale.",
        ),
        ConfigItem(
            label="Greek / Ελλάδα (el_GR.UTF-8)",
            key="el_GR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Greek UTF-8 locale.",
        ),
        ConfigItem(
            label="Czech / Česko (cs_CZ.UTF-8)",
            key="cs_CZ.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Czech UTF-8 locale.",
        ),
        ConfigItem(
            label="Hungarian / Magyarország (hu_HU.UTF-8)",
            key="hu_HU.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Hungarian UTF-8 locale.",
        ),
        ConfigItem(
            label="Ukrainian / Україна (uk_UA.UTF-8)",
            key="uk_UA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European Locales",
            extended_help="Ukrainian UTF-8 locale.",
        ),
        ConfigItem(
            label="Hindi / हिन्दी (hi_IN)",
            key="hi_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Hindi UTF-8 locale.",
        ),
        ConfigItem(
            label="Arabic / Saudi Arabia (ar_SA.UTF-8)",
            key="ar_SA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Arabic UTF-8 locale.",
        ),
        ConfigItem(
            label="Hebrew / Israel (he_IL.UTF-8)",
            key="he_IL.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Hebrew UTF-8 locale.",
        ),
        ConfigItem(
            label="Turkish / Türkiye (tr_TR.UTF-8)",
            key="tr_TR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Turkish UTF-8 locale.",
        ),
        ConfigItem(
            label="Vietnamese / Việt Nam (vi_VN)",
            key="vi_VN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Vietnamese UTF-8 locale.",
        ),
        ConfigItem(
            label="Thai / ประเทศไทย (th_TH.UTF-8)",
            key="th_TH.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Thai UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 5: SYSTEM ACTIONS
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="Compile Locales (locale-gen)",
            key="action_locale_gen",
            scope="DEFAULT",
            type_="action",
            default="locale-gen",
            group="Maintenance Actions",
            extended_help="Executes `locale-gen` as root to compile all enabled locales in `/etc/locale.gen` into `/usr/lib/locale/locale-archive`.",
        ),
        ConfigItem(
            label="List Compiled Locales (locale -a)",
            key="action_list_locales",
            scope="DEFAULT",
            type_="action",
            default="locale -a",
            group="System Diagnostics",
            extended_help="Lists all compiled glibc locale archives currently installed on the system (`locale -a`).",
        ),
        ConfigItem(
            label="View Active Locale Status (localectl)",
            key="action_view_localectl",
            scope="DEFAULT",
            type_="action",
            default="localectl status",
            group="System Diagnostics",
            extended_help="Displays the active system locale, TTY keymap, and X11/Wayland layout state (`localectl status`).",
        ),
        ConfigItem(
            label="View Timedate Status (timedatectl)",
            key="action_view_timedatectl",
            scope="DEFAULT",
            type_="action",
            default="timedatectl status",
            group="System Diagnostics",
            extended_help="Displays the system clock synchronization, NTP status, and RTC mode (`timedatectl status`).",
        ),
        ConfigItem(
            label="View Timesyncd NTP Status (timedatectl)",
            key="action_view_timesync",
            scope="DEFAULT",
            type_="action",
            default="timedatectl timesync-status",
            group="System Diagnostics",
            extended_help="Displays detailed NTP network time synchronization metrics, server addresses, offset, and jitter (`timedatectl timesync-status`).",
        ),
        ConfigItem(
            label="View Shell Environment Locales (locale)",
            key="action_view_shell_locale",
            scope="DEFAULT",
            type_="action",
            default="locale",
            group="System Diagnostics",
            extended_help="Displays all currently active locale environment variables in the active shell environment (`locale`).",
        ),
    ],
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import subprocess

    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)



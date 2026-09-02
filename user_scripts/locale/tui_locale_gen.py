#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: GLIBC LOCALE GENERATOR SCHEMA
===============================================================================
Target: /etc/locale.gen
Engine: locale_gen
Manages glibc locale definitions and compilation across global languages.
"""
import sys
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
APP_TITLE = "Dusky Locale Generator"
REQUIRE_ROOT = True

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Popular Locales",
    "English Variants",
    "Western & Central Europe",
    "Nordic & Baltic",
    "Eastern & Southern Europe",
    "Asian & Middle East",
    "Americas & Actions",
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: POPULAR LOCALES
    # -------------------------------------------------------------------------
    0: [
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
    # TAB 1: ENGLISH VARIANTS
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Australian English (en_AU.UTF-8)",
            key="en_AU.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Global English",
            extended_help="Australian English UTF-8 locale.",
        ),
        ConfigItem(
            label="Canadian English (en_CA.UTF-8)",
            key="en_CA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Global English",
            extended_help="Canadian English UTF-8 locale.",
        ),
        ConfigItem(
            label="Irish English (en_IE.UTF-8)",
            key="en_IE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Global English",
            extended_help="Irish English UTF-8 locale.",
        ),
        ConfigItem(
            label="New Zealand English (en_NZ.UTF-8)",
            key="en_NZ.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Global English",
            extended_help="New Zealand English UTF-8 locale.",
        ),
        ConfigItem(
            label="Singapore English (en_SG.UTF-8)",
            key="en_SG.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Global English",
            extended_help="Singapore English UTF-8 locale.",
        ),
        ConfigItem(
            label="South African English (en_ZA.UTF-8)",
            key="en_ZA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Global English",
            extended_help="South African English UTF-8 locale.",
        ),
        ConfigItem(
            label="Hong Kong English (en_HK.UTF-8)",
            key="en_HK.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Global English",
            extended_help="Hong Kong English UTF-8 locale.",
        ),
        ConfigItem(
            label="Philippines English (en_PH.UTF-8)",
            key="en_PH.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Global English",
            extended_help="Philippines English UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: WESTERN & CENTRAL EUROPE
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Austrian German (de_AT.UTF-8)",
            key="de_AT.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Germanic",
            extended_help="Austrian German UTF-8 locale.",
        ),
        ConfigItem(
            label="Swiss German (de_CH.UTF-8)",
            key="de_CH.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Germanic",
            extended_help="Swiss German UTF-8 locale.",
        ),
        ConfigItem(
            label="Belgian French (fr_BE.UTF-8)",
            key="fr_BE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Romance",
            extended_help="Belgian French UTF-8 locale.",
        ),
        ConfigItem(
            label="Swiss French (fr_CH.UTF-8)",
            key="fr_CH.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Romance",
            extended_help="Swiss French UTF-8 locale.",
        ),
        ConfigItem(
            label="Portuguese / Portugal (pt_PT.UTF-8)",
            key="pt_PT.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Romance",
            extended_help="Portuguese UTF-8 locale.",
        ),
        ConfigItem(
            label="Dutch / Nederland (nl_NL.UTF-8)",
            key="nl_NL.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Low Countries",
            extended_help="Dutch UTF-8 locale.",
        ),
        ConfigItem(
            label="Dutch / Belgium (nl_BE.UTF-8)",
            key="nl_BE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Low Countries",
            extended_help="Belgian Dutch UTF-8 locale.",
        ),
        ConfigItem(
            label="Polish / Polska (pl_PL.UTF-8)",
            key="pl_PL.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Central Europe",
            extended_help="Polish UTF-8 locale.",
        ),
        ConfigItem(
            label="Czech / Česko (cs_CZ.UTF-8)",
            key="cs_CZ.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Central Europe",
            extended_help="Czech UTF-8 locale.",
        ),
        ConfigItem(
            label="Hungarian / Magyarország (hu_HU.UTF-8)",
            key="hu_HU.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Central Europe",
            extended_help="Hungarian UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: NORDIC & BALTIC LOCALES
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Swedish / Sverige (sv_SE.UTF-8)",
            key="sv_SE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Nordic",
            extended_help="Swedish UTF-8 locale.",
        ),
        ConfigItem(
            label="Norwegian Bokmål (nb_NO.UTF-8)",
            key="nb_NO.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Nordic",
            extended_help="Norwegian Bokmål UTF-8 locale.",
        ),
        ConfigItem(
            label="Norwegian Nynorsk (nn_NO.UTF-8)",
            key="nn_NO.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Nordic",
            extended_help="Norwegian Nynorsk UTF-8 locale.",
        ),
        ConfigItem(
            label="Danish / Danmark (da_DK.UTF-8)",
            key="da_DK.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Nordic",
            extended_help="Danish UTF-8 locale.",
        ),
        ConfigItem(
            label="Finnish / Suomi (fi_FI.UTF-8)",
            key="fi_FI.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Nordic",
            extended_help="Finnish UTF-8 locale.",
        ),
        ConfigItem(
            label="Icelandic / Ísland (is_IS.UTF-8)",
            key="is_IS.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Nordic",
            extended_help="Icelandic UTF-8 locale.",
        ),
        ConfigItem(
            label="Estonian / Eesti (et_EE.UTF-8)",
            key="et_EE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Baltic",
            extended_help="Estonian UTF-8 locale.",
        ),
        ConfigItem(
            label="Latvian / Latvija (lv_LV.UTF-8)",
            key="lv_LV.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Baltic",
            extended_help="Latvian UTF-8 locale.",
        ),
        ConfigItem(
            label="Lithuanian / Lietuva (lt_LT.UTF-8)",
            key="lt_LT.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Baltic",
            extended_help="Lithuanian UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: EASTERN & SOUTHERN EUROPE
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Greek / Ελλάδα (el_GR.UTF-8)",
            key="el_GR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Southern Europe",
            extended_help="Greek UTF-8 locale.",
        ),
        ConfigItem(
            label="Romanian / România (ro_RO.UTF-8)",
            key="ro_RO.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Eastern Europe",
            extended_help="Romanian UTF-8 locale.",
        ),
        ConfigItem(
            label="Ukrainian / Україна (uk_UA.UTF-8)",
            key="uk_UA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Eastern Europe",
            extended_help="Ukrainian UTF-8 locale.",
        ),
        ConfigItem(
            label="Bulgarian / България (bg_BG.UTF-8)",
            key="bg_BG.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Eastern Europe",
            extended_help="Bulgarian UTF-8 locale.",
        ),
        ConfigItem(
            label="Croatian / Hrvatska (hr_HR.UTF-8)",
            key="hr_HR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Southern Europe",
            extended_help="Croatian UTF-8 locale.",
        ),
        ConfigItem(
            label="Slovak / Slovensko (sk_SK.UTF-8)",
            key="sk_SK.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Central Europe",
            extended_help="Slovak UTF-8 locale.",
        ),
        ConfigItem(
            label="Slovenian / Slovenija (sl_SI.UTF-8)",
            key="sl_SI.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Central Europe",
            extended_help="Slovenian UTF-8 locale.",
        ),
        ConfigItem(
            label="Serbian / Србија (sr_RS)",
            key="sr_RS UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Southern Europe",
            extended_help="Serbian Cyrillic UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 5: ASIAN & MIDDLE EAST LOCALES
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="Hindi / हिन्दी (hi_IN)",
            key="hi_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="South Asia",
            extended_help="Hindi UTF-8 locale.",
        ),
        ConfigItem(
            label="Bengali / বাংলা (bn_IN)",
            key="bn_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="South Asia",
            extended_help="Bengali (India) UTF-8 locale.",
        ),
        ConfigItem(
            label="Tamil / தமிழ் (ta_IN)",
            key="ta_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="South Asia",
            extended_help="Tamil UTF-8 locale.",
        ),
        ConfigItem(
            label="Telugu / తెలుగు (te_IN)",
            key="te_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="South Asia",
            extended_help="Telugu UTF-8 locale.",
        ),
        ConfigItem(
            label="Marathi / मराठी (mr_IN)",
            key="mr_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="South Asia",
            extended_help="Marathi UTF-8 locale.",
        ),
        ConfigItem(
            label="Gujarati / ગુજરાતી (gu_IN)",
            key="gu_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="South Asia",
            extended_help="Gujarati UTF-8 locale.",
        ),
        ConfigItem(
            label="Kannada / ಕನ್ನಡ (kn_IN)",
            key="kn_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="South Asia",
            extended_help="Kannada UTF-8 locale.",
        ),
        ConfigItem(
            label="Malayalam / മലയാളം (ml_IN)",
            key="ml_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="South Asia",
            extended_help="Malayalam UTF-8 locale.",
        ),
        ConfigItem(
            label="Punjabi / ਪੰਜਾਬੀ (pa_IN)",
            key="pa_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="South Asia",
            extended_help="Punjabi UTF-8 locale.",
        ),
        ConfigItem(
            label="Urdu / اردو (ur_PK)",
            key="ur_PK UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="South Asia",
            extended_help="Urdu (Pakistan) UTF-8 locale.",
        ),
        ConfigItem(
            label="Arabic / Saudi Arabia (ar_SA.UTF-8)",
            key="ar_SA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Middle East",
            extended_help="Arabic (Saudi Arabia) UTF-8 locale.",
        ),
        ConfigItem(
            label="Arabic / UAE (ar_AE.UTF-8)",
            key="ar_AE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Middle East",
            extended_help="Arabic (UAE) UTF-8 locale.",
        ),
        ConfigItem(
            label="Arabic / Egypt (ar_EG.UTF-8)",
            key="ar_EG.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Middle East",
            extended_help="Arabic (Egypt) UTF-8 locale.",
        ),
        ConfigItem(
            label="Hebrew / Israel (he_IL.UTF-8)",
            key="he_IL.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Middle East",
            extended_help="Hebrew UTF-8 locale.",
        ),
        ConfigItem(
            label="Persian / فارسی (fa_IR)",
            key="fa_IR UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Middle East",
            extended_help="Persian / Farsi UTF-8 locale.",
        ),
        ConfigItem(
            label="Turkish / Türkiye (tr_TR.UTF-8)",
            key="tr_TR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Middle East",
            extended_help="Turkish UTF-8 locale.",
        ),
        ConfigItem(
            label="Vietnamese / Việt Nam (vi_VN)",
            key="vi_VN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Southeast Asia",
            extended_help="Vietnamese UTF-8 locale.",
        ),
        ConfigItem(
            label="Thai / ประเทศไทย (th_TH.UTF-8)",
            key="th_TH.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Southeast Asia",
            extended_help="Thai UTF-8 locale.",
        ),
        ConfigItem(
            label="Indonesian / Indonesia (id_ID.UTF-8)",
            key="id_ID.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Southeast Asia",
            extended_help="Indonesian UTF-8 locale.",
        ),
        ConfigItem(
            label="Malay / Malaysia (ms_MY.UTF-8)",
            key="ms_MY.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Southeast Asia",
            extended_help="Malay UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 6: AMERICAS & ACTIONS
    # -------------------------------------------------------------------------
    6: [
        ConfigItem(
            label="Spanish / Mexico (es_MX.UTF-8)",
            key="es_MX.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Latin America",
            extended_help="Mexican Spanish UTF-8 locale.",
        ),
        ConfigItem(
            label="Spanish / Argentina (es_AR.UTF-8)",
            key="es_AR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Latin America",
            extended_help="Argentine Spanish UTF-8 locale.",
        ),
        ConfigItem(
            label="Spanish / Chile (es_CL.UTF-8)",
            key="es_CL.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Latin America",
            extended_help="Chilean Spanish UTF-8 locale.",
        ),
        ConfigItem(
            label="Spanish / Colombia (es_CO.UTF-8)",
            key="es_CO.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Latin America",
            extended_help="Colombian Spanish UTF-8 locale.",
        ),
        ConfigItem(
            label="Spanish / Peru (es_PE.UTF-8)",
            key="es_PE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Latin America",
            extended_help="Peruvian Spanish UTF-8 locale.",
        ),
        ConfigItem(
            label="Afrikaans / South Africa (af_ZA.UTF-8)",
            key="af_ZA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="African Locales",
            extended_help="Afrikaans UTF-8 locale.",
        ),
        ConfigItem(
            label="Swahili / Kenya (sw_KE)",
            key="sw_KE UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="African Locales",
            extended_help="Swahili UTF-8 locale.",
        ),
        ConfigItem(
            label="Zulu / South Africa (zu_ZA.UTF-8)",
            key="zu_ZA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="African Locales",
            extended_help="Zulu UTF-8 locale.",
        ),
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
            extended_help="Lists all compiled glibc locale archives currently installed on the system.",
        ),
        ConfigItem(
            label="View System Locale Status (localectl)",
            key="action_view_localectl",
            scope="DEFAULT",
            type_="action",
            default="localectl status",
            group="System Diagnostics",
            extended_help="Displays active system locale settings (`localectl status`).",
        ),
        ConfigItem(
            label="View Active Shell Locale (locale)",
            key="action_view_shell_locale",
            scope="DEFAULT",
            type_="action",
            default="locale",
            group="System Diagnostics",
            extended_help="Displays all active environment locale variables in the current session (`locale`).",
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



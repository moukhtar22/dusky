#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky Matugen Presets v4.1.1 (Production Release)
# -----------------------------------------------------------------------------
# Target: Arch Linux / Hyprland / Matugen / Wayland
#
# v4.1.1 CHANGELOG:
#   - FIX: Resolved right bounding-box structural collapse in the Tab row.
#   - FIX: Decoupled UI width calculations from mouse-zone tracking logic.
#   - FIX: Corrected a 1-character horizontal shift/jitter on the active selection.
#   - SYNC: Re-introduced '30' deg and '-1.0' contrast to match daemon defaults.
#   - FEAT: Bulletproof state parsing handles unexpected quotes gracefully.
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s extglob

# Force standard C locale to prevent decimal format errors
export LC_NUMERIC=C

# =============================================================================
# ▼ CONFIGURATION ▼
# =============================================================================

declare -r APP_TITLE="Dusky Matugen Presets"
declare -r APP_VERSION="v4.1.1"

# --- State & Favorites Paths ---
declare -r USE_STATE_FILE=true
declare -r STATE_DIR="${HOME}/.config/dusky/settings/dusky_theme"
declare -r STATE_FILE="${STATE_DIR}/state.conf"
declare -r FAVORITES_FILE="${STATE_DIR}/theme_preset_fav"
declare -r THEME_CTL="${HOME}/user_scripts/theme_matugen/theme_ctl.sh"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=16
declare -ri BOX_INNER_WIDTH=80
declare -ri ITEM_PADDING=30
declare -ri ADJUST_THRESHOLD=38

# Minimum terminal dimensions
declare -ri MIN_COLS=82
declare -ri MIN_ROWS=24

# UI Row Calculations
# Structure: 1:Top border, 2:Title, 3:Status 1, 4:Status 2, 5:Tabs, 6:Bottom border
declare -ri HEADER_LINES=6
declare -ri TAB_ROW=5
declare -ri ITEM_START_Y=$(( HEADER_LINES + 1 ))

# Tabs — Favorites is tab 0
declare -ra TABS=("♥ Fav" "Vib" "Neon" "Deep" "Pastel" "Mono" "Cust" "Theme" "Anim")

# Favorite indicator
declare -r FAV_ICON="♥"

# Global Settings (Defaults - theme_ctl.sh Aligned)
declare -A SETTINGS=(
    ["type"]="scheme-tonal-spot"
    ["mode"]="dark"
    ["contrast"]="0"
    ["index"]="0"
    ["base16"]="disable"
    ["t_type"]="random"
    ["t_dur"]="2"
    ["t_fps"]="60"
    ["t_bez"]=".54,0,.34,.99"
    ["t_ang"]="30"
    ["t_pos"]="center"
)

# =============================================================================
# ▼ ANSI CONSTANTS ▼
# =============================================================================

declare -r C_RESET=$'\033[0m'
declare -r C_CYAN=$'\033[1;36m'
declare -r C_GREEN=$'\033[1;32m'
declare -r C_MAGENTA=$'\033[1;35m'
declare -r C_RED=$'\033[1;31m'
declare -r C_YELLOW=$'\033[1;33m'
declare -r C_WHITE=$'\033[1;37m'
declare -r C_GREY=$'\033[1;30m'
declare -r C_INVERSE=$'\033[7m'

declare -r CLR_EOL=$'\033[K'
declare -r CLR_EOS=$'\033[J'
declare -r CLR_SCREEN=$'\033[2J'

declare -r CURSOR_HOME=$'\033[H'
declare -r CURSOR_HIDE=$'\033[?25l'
declare -r CURSOR_SHOW=$'\033[?25h'

declare -r MOUSE_ON=$'\033[?1000h\033[?1002h\033[?1006h'
declare -r MOUSE_OFF=$'\033[?1000l\033[?1002l\033[?1006l'

declare -r ESC_READ_TIMEOUT=0.10

# --- Pre-computed Constants ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
declare -r H_LINE="${_h_line_buf// /─}"
unset _h_line_buf

# =============================================================================
# ▼ STATE MANAGEMENT ▼
# =============================================================================

declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=1
declare -i SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare ORIGINAL_STTY=""
declare _TMPFILE=""

declare LAST_APPLIED_HEX=""
declare LAST_STATUS_MSG=""

# Tab index constants
declare -ri FAVORITES_TAB=0
declare -ri CUSTOM_TAB=6
declare -ri THEME_TAB=7
declare -ri ANIM_TAB=8

# Favorite hex lookup
declare -A FAV_HEX_LOOKUP=()

# =============================================================================
# ▼ DATA STRUCTURES ▼
# =============================================================================

declare -A ITEM_MAP=()

for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    declare -ga "TAB_ITEMS_${_ti}=()"
done
unset _ti

# =============================================================================
# ▼ SYSTEM HELPERS ▼
# =============================================================================

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

cleanup() {
    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi
    if [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" ]]; then
        rm -f "$_TMPFILE" 2>/dev/null || :
    fi
    printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

enter_raw_mode() {
    stty -icanon -echo min 1 time 0 2>/dev/null || :
    printf '%s%s' "${CURSOR_HIDE}" "${MOUSE_ON}"
}

# =============================================================================
# ▼ DATA REGISTRATION ▼
# =============================================================================

register() {
    if (( $# != 3 )); then
        log_err "register() requires 3 args, got $#"
        exit 1
    fi

    local -i tab_idx=$1
    local label="$2" value="$3"

    if (( tab_idx < 0 || tab_idx >= TAB_COUNT )); then
        log_err "register() tab_idx $tab_idx out of range [0-$(( TAB_COUNT - 1 ))]"
        exit 1
    fi

    ITEM_MAP["${tab_idx}::${label}"]="${value}"
    local -n _reg_ref="TAB_ITEMS_${tab_idx}"
    _reg_ref+=("${label}")
}

register_items() {
    # --- TAB 1: VIBRANT ---
    register 1 "Hyper Red"         "#FF0000"
    register 1 "Electric Blue"     "#0000FF"
    register 1 "Toxic Green"       "#00FF00"
    register 1 "Pure Magenta"      "#FF00FF"
    register 1 "Cyan Punch"        "#00FFFF"
    register 1 "Safety Yellow"     "#FFFF00"
    register 1 "Blood Orange"      "#FF4500"
    register 1 "Plasma Purple"     "#6A0DAD"
    register 1 "Deep Pink"         "#FF1493"
    register 1 "Ultramarine"       "#120A8F"
    register 1 "Emerald City"      "#50C878"
    register 1 "Crimson Tide"      "#DC143C"
    register 1 "Chartreuse"        "#7FFF00"
    register 1 "Spring Green"      "#00FF7F"
    register 1 "Azure Sky"         "#007FFF"
    register 1 "Violet Ray"        "#EE82EE"
    register 1 "Aquamarine"        "#7FFFD4"
    register 1 "Solid Gold"        "#FFD700"
    register 1 "Rich Teal"         "#008080"
    register 1 "Olive Drab"        "#808000"

    # --- TAB 2: NEON / CYBER ---
    register 2 "Laser Lemon"       "#FFFF66"
    register 2 "Hot Pink"          "#FF69B4"
    register 2 "Cyber Grape"       "#58427C"
    register 2 "Neon Carrot"       "#FFA343"
    register 2 "Matrix Green"      "#03A062"
    register 2 "Electric Indigo"   "#6F00FF"
    register 2 "Miami Pink"        "#FF5AC4"
    register 2 "Vice Blue"         "#00C6FF"
    register 2 "Radioactive"       "#CCFF00"
    register 2 "Plastic Purple"    "#D400FF"
    register 2 "Arcade Red"        "#FF0055"
    register 2 "Hacker Green"      "#00FF2A"
    register 2 "Synthwave Sun"     "#FF7E00"
    register 2 "Tron Cyan"         "#6EFFFF"
    register 2 "Flux Capacitor"    "#FFAE00"
    register 2 "Highlighter Blue"  "#1F51FF"
    register 2 "Shocking Pink"     "#FC0FC0"
    register 2 "Lime Light"        "#BFFF00"

    # --- TAB 3: DEEP / DARK ---
    register 3 "Midnight Blue"     "#191970"
    register 3 "Dark Slate"        "#2F4F4F"
    register 3 "Saddle Brown"      "#8B4513"
    register 3 "Dark Olive"        "#556B2F"
    register 3 "Indigo Dye"        "#4B0082"
    register 3 "Maroon"            "#800000"
    register 3 "Navy"              "#000080"
    register 3 "Dark Green"        "#006400"
    register 3 "Dark Cyan"         "#008B8B"
    register 3 "Dark Magenta"      "#8B008B"
    register 3 "Tyrian Purple"     "#66023C"
    register 3 "Oxblood"           "#4A0404"
    register 3 "Deep Forest"       "#013220"
    register 3 "Night Sky"         "#0C090A"
    register 3 "Black Cherry"      "#540026"
    register 3 "Deep Coffee"       "#3B2F2F"

    # --- TAB 4: PASTEL ---
    register 4 "Baby Blue"         "#89CFF0"
    register 4 "Mint Cream"        "#F5FFFA"
    register 4 "Lavender"          "#E6E6FA"
    register 4 "Peach Puff"        "#FFDAB9"
    register 4 "Misty Rose"        "#FFE4E1"
    register 4 "Honeydew"          "#F0FFF0"
    register 4 "Alice Blue"        "#F0F8FF"
    register 4 "Lemon Chiffon"     "#FFFACD"
    register 4 "Tea Green"         "#D0F0C0"
    register 4 "Celeste"           "#B2FFFF"
    register 4 "Mauve"             "#E0B0FF"
    register 4 "Salmon"            "#FA8072"
    register 4 "Cornflower"        "#6495ED"
    register 4 "Thistle"           "#D8BFD8"
    register 4 "Wheat"             "#F5DEB3"

    # --- TAB 5: MONOCHROME ---
    register 5 "Pure Black"        "#000000"
    register 5 "Pure White"        "#FFFFFF"
    register 5 "Dim Gray"          "#696969"
    register 5 "Slate Gray"        "#708090"
    register 5 "Light Slate"       "#778899"
    register 5 "Silver"            "#C0C0C0"
    register 5 "Gainsboro"         "#DCDCDC"
    register 5 "Charcoal"          "#36454F"
    register 5 "Onyx"              "#353839"
    register 5 "Gunmetal"          "#2A3439"

    # --- TAB 6: CUSTOM INPUT ---
    register 6 "Input HEX Code"    "ACTION_INPUT_HEX"
    register 6 "Input RGB Values"  "ACTION_INPUT_RGB"
    register 6 "Regenerate Last"   "ACTION_REGEN"

    # --- TAB 7: THEME ---
    register 7 "» Apply Settings «" "ACTION_APPLY_SETTINGS"
    register 7 "Scheme Type"       "type|cycle|scheme-fidelity;scheme-content;scheme-fruit-salad;scheme-vibrant;scheme-rainbow;scheme-neutral;scheme-tonal-spot;scheme-expressive;scheme-monochrome;disable"
    register 7 "Mode"              "mode|cycle|dark;light"
    register 7 "Contrast"          "contrast|cycle|0;-1.0;-0.8;-0.6;-0.4;-0.2;0.2;0.4;0.6;0.8;1.0;disable"
    register 7 "Color Index"       "index|cycle|0;1;2;3;4"
    register 7 "Base16 Backend"    "base16|cycle|disable;wal"

    # --- TAB 8: ANIMATION ---
    register 8 "» Apply Settings «" "ACTION_APPLY_SETTINGS"
    register 8 "Transition Type"   "t_type|cycle|random;simple;fade;left;right;top;bottom;wipe;wave;grow;center;any;outer;none;disable"
    register 8 "Duration (sec)"    "t_dur|cycle|disable;0.5;1;2;3;5;10"
    register 8 "FPS"               "t_fps|cycle|disable;30;60;90;120;144"
    register 8 "Bezier Curve"      "t_bez|cycle|disable;.54,0,.34,.99;0,0,1,1;.85,0,.15,1;.17,.67,.83,.67"
    register 8 "Angle (Deg)"       "t_ang|cycle|disable;0;30;45;90;135;180;225;270;315"
    register 8 "Position"          "t_pos|cycle|disable;center;top;left;right;bottom;top-left;top-right;bottom-left;bottom-right"
}

# =============================================================================
# ▼ FAVORITES SYSTEM ▼
# =============================================================================

rebuild_fav_lookup() {
    FAV_HEX_LOOKUP=()
    local -i fav_count=${#TAB_ITEMS_0[@]}
    if (( fav_count > 0 )); then
        local fav_item fav_val
        for fav_item in "${TAB_ITEMS_0[@]}"; do
            fav_val="${ITEM_MAP["0::${fav_item}"]}"
            if [[ -n "$fav_val" ]]; then
                FAV_HEX_LOOKUP["${fav_val^^}"]=1
            fi
        done
    fi
}

load_favorites() {
    TAB_ITEMS_0=()

    local _old_key
    for _old_key in "${!ITEM_MAP[@]}"; do
        if [[ "$_old_key" == 0::* ]]; then
            unset "ITEM_MAP[$_old_key]"
        fi
    done

    if [[ ! -f "$FAVORITES_FILE" ]]; then
        rebuild_fav_lookup
        return 0
    fi

    local line label hex
    while IFS= read -r line || [[ -n "${line:-}" ]]; do
        [[ -z "$line" || "$line" == \#* ]] && continue

        label="${line%%|*}"
        hex="${line#*|}"

        [[ -z "$label" || -z "$hex" ]] && continue
        [[ ! "$hex" =~ ^#[a-fA-F0-9]{6}$ ]] && continue

        ITEM_MAP["0::${label}"]="${hex}"
        TAB_ITEMS_0+=("${label}")
    done < "$FAVORITES_FILE"

    rebuild_fav_lookup
}

save_favorites() {
    mkdir -p "$STATE_DIR"

    local tmpfile
    tmpfile=$(mktemp "${STATE_DIR}/fav.tmp.XXXXXXXXXX") || {
        log_err "Failed to create temp file for favorites save"
        return 1
    }

    printf '# Dusky Matugen Favorites\n' > "$tmpfile"
    printf '# Format: Label|#HEXCODE\n' >> "$tmpfile"

    local item val
    local -i fav_count=${#TAB_ITEMS_0[@]}
    if (( fav_count > 0 )); then
        for item in "${TAB_ITEMS_0[@]}"; do
            val="${ITEM_MAP["0::${item}"]}"
            [[ -z "$val" ]] && continue
            printf '%s|%s\n' "$item" "$val" >> "$tmpfile"
        done
    fi

    cat "$tmpfile" > "$FAVORITES_FILE"
    rm -f "$tmpfile"

    rebuild_fav_lookup
}

unfavorite_by_hex() {
    local target_hex="${1^^}"
    local -i fav_count=${#TAB_ITEMS_0[@]}
    if (( fav_count == 0 )); then
        return 1
    fi

    local matched_label=""
    local fav_item fav_val
    for fav_item in "${TAB_ITEMS_0[@]}"; do
        fav_val="${ITEM_MAP["0::${fav_item}"]}"
        if [[ "${fav_val^^}" == "$target_hex" ]]; then
            matched_label="$fav_item"
            break
        fi
    done

    [[ -z "$matched_label" ]] && return 1

    unset "ITEM_MAP[0::${matched_label}]"

    local -a new_items=()
    local item
    for item in "${TAB_ITEMS_0[@]}"; do
        [[ "$item" == "$matched_label" ]] && continue
        new_items+=("$item")
    done

    if (( ${#new_items[@]} > 0 )); then
        TAB_ITEMS_0=("${new_items[@]}")
    else
        TAB_ITEMS_0=()
    fi

    save_favorites
    LAST_STATUS_MSG="${C_YELLOW}Unfavorited: ${matched_label} (${target_hex})${C_RESET}"
    return 0
}

toggle_favorite() {
    if (( CURRENT_TAB == FAVORITES_TAB || CURRENT_TAB == CUSTOM_TAB || CURRENT_TAB == THEME_TAB || CURRENT_TAB == ANIM_TAB )); then
        LAST_STATUS_MSG="${C_YELLOW}Navigate to a color tab to manage favorites${C_RESET}"
        return 0
    fi

    local -n _fav_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_fav_items_ref[@]}
    if (( count == 0 )); then return 0; fi

    local label="${_fav_items_ref[SELECTED_ROW]}"
    local val="${ITEM_MAP["${CURRENT_TAB}::${label}"]}"

    if [[ ! "$val" =~ ^#[a-fA-F0-9]{6}$ ]]; then
        LAST_STATUS_MSG="${C_YELLOW}Only color presets can be favorited${C_RESET}"
        return 0
    fi

    if [[ -n "${FAV_HEX_LOOKUP["${val^^}"]:-}" ]]; then
        unfavorite_by_hex "$val"
    else
        local final_label="$label"
        local lookup_key="0::${final_label}"
        if [[ -n "${ITEM_MAP["$lookup_key"]+_}" ]]; then
            final_label="${label} ${val}"
        fi

        ITEM_MAP["0::${final_label}"]="$val"
        TAB_ITEMS_0+=("${final_label}")
        save_favorites
        LAST_STATUS_MSG="${C_GREEN}${FAV_ICON} Added to favorites: ${final_label} (${val})${C_RESET}"
    fi
}

remove_favorite() {
    if (( CURRENT_TAB != FAVORITES_TAB )); then
        return 0
    fi

    local -i count=${#TAB_ITEMS_0[@]}
    if (( count == 0 )); then
        LAST_STATUS_MSG="${C_YELLOW}No favorites to remove${C_RESET}"
        return 0
    fi

    local label="${TAB_ITEMS_0[SELECTED_ROW]}"
    local val="${ITEM_MAP["0::${label}"]}"

    unset "ITEM_MAP[0::${label}]"

    local -a new_items=()
    local item
    for item in "${TAB_ITEMS_0[@]}"; do
        [[ "$item" == "$label" ]] && continue
        new_items+=("$item")
    done

    if (( ${#new_items[@]} > 0 )); then
        TAB_ITEMS_0=("${new_items[@]}")
    else
        TAB_ITEMS_0=()
    fi

    count=${#TAB_ITEMS_0[@]}
    if (( count == 0 )); then
        SELECTED_ROW=0
        SCROLL_OFFSET=0
    elif (( SELECTED_ROW >= count )); then
        SELECTED_ROW=$(( count - 1 ))
    fi

    save_favorites
    LAST_STATUS_MSG="${C_RED}✗ Removed from favorites: ${label} (${val})${C_RESET}"
}

# =============================================================================
# ▼ STATE FILE MANAGEMENT ▼
# =============================================================================

load_state() {
    [[ "${USE_STATE_FILE}" != "true" ]] && return 0
    [[ ! -f "${STATE_FILE}" ]] && return 0

    local key value
    while IFS='=' read -r key value; do
        [[ -z "${key}" || "${key}" == \#* ]] && continue

        value="${value//$'\n'/}"
        value="${value//\"/}"
        value="${value//\'/}"

        case "${key}" in
            THEME_MODE)         [[ -n "${value}" ]] && SETTINGS["mode"]="${value}" ;;
            MATUGEN_TYPE)       [[ -n "${value}" ]] && SETTINGS["type"]="${value}" ;;
            SOURCE_COLOR_INDEX) [[ -n "${value}" ]] && SETTINGS["index"]="${value}" ;;
            BASE16_BACKEND)     [[ -n "${value}" ]] && SETTINGS["base16"]="${value}" ;;
            AWWW_TRANS_TYPE)    [[ -n "${value}" ]] && SETTINGS["t_type"]="${value}" ;;
            AWWW_TRANS_DURATION)[[ -n "${value}" ]] && SETTINGS["t_dur"]="${value}" ;;
            AWWW_TRANS_FPS)     [[ -n "${value}" ]] && SETTINGS["t_fps"]="${value}" ;;
            AWWW_TRANS_BEZIER)  [[ -n "${value}" ]] && SETTINGS["t_bez"]="${value}" ;;
            AWWW_TRANS_ANGLE)   [[ -n "${value}" ]] && SETTINGS["t_ang"]="${value}" ;;
            AWWW_TRANS_POS)     [[ -n "${value}" ]] && SETTINGS["t_pos"]="${value}" ;;
            LAST_APPLIED_HEX)   [[ -n "${value}" ]] && LAST_APPLIED_HEX="${value}" ;;
            MATUGEN_CONTRAST)
                if [[ -n "${value}" ]]; then
                    # Map legacy 0.0 standard natively to 0
                    if [[ "${value}" == "0.0" ]]; then
                        SETTINGS["contrast"]="0"
                    else
                        SETTINGS["contrast"]="${value}"
                    fi
                fi
                ;;
        esac
    done < "${STATE_FILE}"
    return 0
}

save_state() {
    [[ "${USE_STATE_FILE}" != "true" ]] && return 0

    mkdir -p "${STATE_DIR}"

    local tmpfile
    tmpfile=$(mktemp "${STATE_DIR}/state.tmp.XXXXXXXXXX") || {
        log_err "Failed to create temp file for state save"
        return 1
    }

    # Extract preserving state managed purely by theme_ctl
    local current_state=""
    if [[ -f "${STATE_FILE}" ]]; then
        current_state=$(grep -v "^LAST_APPLIED_HEX=" "${STATE_FILE}" || true)
    fi
    
    if [[ -n "${current_state}" ]]; then
        printf '%s\n' "${current_state}" > "${tmpfile}"
    else
        printf '# Dusky Theme State File\n' > "${tmpfile}"
    fi
    
    printf 'LAST_APPLIED_HEX=%s\n' "${LAST_APPLIED_HEX}" >> "${tmpfile}"

    cat "$tmpfile" > "$STATE_FILE"
    rm -f "$tmpfile"
}

# =============================================================================
# ▼ CORE LOGIC ▼
# =============================================================================

apply_matugen() {
    local hex="${1^^}"
    
    # Update local tracking UI
    LAST_APPLIED_HEX="${hex}"
    save_state

    # Execute synchronously mapping strictly to theme_ctl schema
    local -a cmd=("${THEME_CTL}" "set" "--no-wall" "--no-regen" \
        "--mode" "${SETTINGS["mode"]}" \
        "--type" "${SETTINGS["type"]}" \
        "--contrast" "${SETTINGS["contrast"]}" \
        "--index" "${SETTINGS["index"]}" \
        "--base16" "${SETTINGS["base16"]}" \
        "--trans-type" "${SETTINGS["t_type"]}" \
        "--trans-duration" "${SETTINGS["t_dur"]}" \
        "--trans-fps" "${SETTINGS["t_fps"]}" \
        "--trans-bezier" "${SETTINGS["t_bez"]}" \
        "--trans-angle" "${SETTINGS["t_ang"]}" \
        "--trans-pos" "${SETTINGS["t_pos"]}"
    )

    # Fire controller to globally cache settings
    if "${cmd[@]}" >/dev/null 2>&1; then
        # Proceed with enforcing solid background color extraction
        local -a color_cmd=("${THEME_CTL}" "color" "${hex}")
        if "${color_cmd[@]}" >/dev/null 2>&1; then
            LAST_STATUS_MSG="${C_GREEN}✓ Applied via theme_ctl: ${hex}${C_RESET}"
        else
            LAST_STATUS_MSG="${C_RED}✗ theme_ctl failed to generate color: ${hex}${C_RESET}"
        fi
    else
        LAST_STATUS_MSG="${C_RED}✗ theme_ctl failed to update state for: ${hex}${C_RESET}"
    fi
}

prompt_input() {
    local prompt_text="$1"
    local -n _prompt_out=$2

    printf '%s%s' "${MOUSE_OFF}" "${CURSOR_SHOW}"
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "${ORIGINAL_STTY}" 2>/dev/null || stty sane
    else
        stty sane
    fi

    printf '%s%s%s➤ %s%s ' "${C_RESET}" "${CLR_SCREEN}" "${C_CYAN}" "${prompt_text}" "${C_RESET}"

    _prompt_out=""
    read -r _prompt_out || :

    enter_raw_mode
}

validate_hex() {
    [[ $1 =~ ^#?[a-fA-F0-9]{6}$ ]]
}

validate_rgb_component() {
    [[ -n "${1:-}" && $1 =~ ^[0-9]+$ ]] && (( 10#$1 >= 0 && 10#$1 <= 255 ))
}

modify_setting() {
    local label="$1"
    local -i direction=$2
    local config="${ITEM_MAP["${CURRENT_TAB}::${label}"]}"
    local key type rest

    key="${config%%|*}"
    rest="${config#*|}"
    type="${rest%%|*}"
    rest="${rest#*|}"

    local current="${SETTINGS[${key}]}"
    local new_val=""

    case "${type}" in
        cycle)
            local -a opts=()
            IFS=';' read -r -a opts <<< "${rest}"
            local -i count=${#opts[@]} idx=0 i
            if (( count == 0 )); then return 0; fi

            for (( i = 0; i < count; i++ )); do
                if [[ "${opts[i]}" == "${current}" ]]; then
                    idx=$i
                    break
                fi
            done

            idx=$(( (idx + direction + count) % count ))
            new_val="${opts[idx]}"
            ;;
        *)
            return 0
            ;;
    esac

    SETTINGS["${key}"]="${new_val}"
}

trigger_action() {
    local label="$1"
    local val="${ITEM_MAP["${CURRENT_TAB}::${label}"]}"

    if (( CURRENT_TAB == THEME_TAB || CURRENT_TAB == ANIM_TAB )); then
        if [[ "${val}" == ACTION_* ]]; then
            : # Fall through to execution
        else
            modify_setting "${label}" 1
            return 0
        fi
    fi

    case "${val}" in
        ACTION_APPLY_SETTINGS)
            local -a cmd=("${THEME_CTL}" "set" "--no-wall" \
                "--mode" "${SETTINGS["mode"]}" \
                "--type" "${SETTINGS["type"]}" \
                "--contrast" "${SETTINGS["contrast"]}" \
                "--index" "${SETTINGS["index"]}" \
                "--base16" "${SETTINGS["base16"]}" \
                "--trans-type" "${SETTINGS["t_type"]}" \
                "--trans-duration" "${SETTINGS["t_dur"]}" \
                "--trans-fps" "${SETTINGS["t_fps"]}" \
                "--trans-bezier" "${SETTINGS["t_bez"]}" \
                "--trans-angle" "${SETTINGS["t_ang"]}" \
                "--trans-pos" "${SETTINGS["t_pos"]}"
            )
            if "${cmd[@]}" >/dev/null 2>&1; then
                LAST_STATUS_MSG="${C_GREEN}✓ Settings Applied successfully${C_RESET}"
            else
                LAST_STATUS_MSG="${C_RED}✗ Failed to apply settings${C_RESET}"
            fi
            return 0
            ;;
        ACTION_INPUT_HEX)
            local input_hex=""
            prompt_input "Enter HEX (e.g. #FF0000):" input_hex
            if validate_hex "${input_hex}"; then
                [[ "${input_hex}" != \#* ]] && input_hex="#${input_hex}"
                apply_matugen "${input_hex}"
            else
                LAST_STATUS_MSG="${C_RED}Invalid HEX code${C_RESET}"
            fi
            ;;
        ACTION_INPUT_RGB)
            local rgb_str="" r="" g="" b=""
            prompt_input "Enter RGB (e.g. 255 0 0):" rgb_str
            read -r r g b _ <<< "${rgb_str}"

            if validate_rgb_component "${r:-}" \
                && validate_rgb_component "${g:-}" \
                && validate_rgb_component "${b:-}"; then
                local hex
                printf -v hex '#%02X%02X%02X' "$(( 10#${r} ))" "$(( 10#${g} ))" "$(( 10#${b} ))"
                apply_matugen "${hex}"
            else
                LAST_STATUS_MSG="${C_RED}Invalid RGB values${C_RESET}"
            fi
            ;;
        ACTION_REGEN)
            if [[ -z "${LAST_APPLIED_HEX}" ]]; then
                LAST_STATUS_MSG="${C_YELLOW}No color has been applied yet${C_RESET}"
            else
                apply_matugen "${LAST_APPLIED_HEX}"
            fi
            ;;
        '#'*)
            apply_matugen "${val}"
            ;;
    esac
}

# =============================================================================
# ▼ SCROLL & RENDER HELPERS ▼
# =============================================================================

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0; SCROLL_OFFSET=0
        _vis_start=0; _vis_end=0
        return
    fi

    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi

    if (( SELECTED_ROW < SCROLL_OFFSET )); then
        SCROLL_OFFSET=$SELECTED_ROW
    elif (( SELECTED_ROW >= SCROLL_OFFSET + MAX_DISPLAY_ROWS )); then
        SCROLL_OFFSET=$(( SELECTED_ROW - MAX_DISPLAY_ROWS + 1 ))
    fi

    local -i max_scroll=$(( count - MAX_DISPLAY_ROWS ))
    if (( max_scroll < 0 )); then max_scroll=0; fi
    if (( SCROLL_OFFSET > max_scroll )); then SCROLL_OFFSET=$max_scroll; fi

    _vis_start=$SCROLL_OFFSET
    _vis_end=$(( SCROLL_OFFSET + MAX_DISPLAY_ROWS ))
    if (( _vis_end > count )); then _vis_end=$count; fi
}

render_scroll_indicator() {
    local -n _rsi_buf=$1
    local position="$2"
    local -i count=$3 boundary=$4

    if [[ "$position" == "above" ]]; then
        if (( SCROLL_OFFSET > 0 )); then
            _rsi_buf+="${C_GREY}    ▲ (more above)${CLR_EOL}${C_RESET}"$'\n'
        else
            _rsi_buf+="${CLR_EOL}"$'\n'
        fi
    else
        if (( count > MAX_DISPLAY_ROWS )); then
            local position_info="[$(( SELECTED_ROW + 1 ))/${count}]"
            if (( boundary < count )); then
                _rsi_buf+="${C_GREY}    ▼ (more below) ${position_info}${CLR_EOL}${C_RESET}"$'\n'
            else
                _rsi_buf+="${C_GREY}                   ${position_info}${CLR_EOL}${C_RESET}"$'\n'
            fi
        else
            _rsi_buf+="${CLR_EOL}"$'\n'
        fi
    fi
}

# =============================================================================
# ▼ UI RENDERING ▼
# =============================================================================

draw_ui() {
    local buf="" pad_buf="" padded_item="" item val display
    local -i i current_col len count pad_needed
    local -i visible_len left_pad right_pad
    local -i _vis_start _vis_end zone_start

    local dot="" key="" setting_val="" prefix=""
    local -i cr=0 cg=0 cb=0

    buf+="${CURSOR_HOME}"

    # --- Top Border ---
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    # --- Header ---
    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    visible_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - visible_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - visible_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    # --- Status Line 1 (Theme) ---
    local status1_content="Mode: ${SETTINGS[mode]} | Type: ${SETTINGS[type]} | Cont: ${SETTINGS[contrast]} | Idx: ${SETTINGS[index]} | B16: ${SETTINGS[base16]}"
    local -i raw_len1=${#status1_content}

    if (( raw_len1 > BOX_INNER_WIDTH - 2 )); then raw_len1=$(( BOX_INNER_WIDTH - 2 )); fi
    left_pad=$(( (BOX_INNER_WIDTH - raw_len1) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - raw_len1 - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_MAGENTA}Mode: ${C_CYAN}${SETTINGS[mode]} ${C_MAGENTA}| Type: ${C_CYAN}${SETTINGS[type]} ${C_MAGENTA}| Cont: ${C_CYAN}${SETTINGS[contrast]} ${C_MAGENTA}| Idx: ${C_CYAN}${SETTINGS[index]} ${C_MAGENTA}| B16: ${C_CYAN}${SETTINGS[base16]}${C_RESET}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'

    # --- Status Line 2 (Animation) ---
    local d_type="${SETTINGS[t_type]}"; [[ "$d_type" == "disable" ]] && d_type="off"
    local d_dur="${SETTINGS[t_dur]}"; [[ "$d_dur" == "disable" ]] && d_dur="off" || d_dur="${d_dur}s"
    local d_fps="${SETTINGS[t_fps]}"; [[ "$d_fps" == "disable" ]] && d_fps="off"
    local d_pos="${SETTINGS[t_pos]}"; [[ "$d_pos" == "disable" ]] && d_pos="off"
    
    local status2_content="Anim: ${d_type} | Dur: ${d_dur} | FPS: ${d_fps} | Pos: ${d_pos}"
    local -i raw_len2=${#status2_content}

    if (( raw_len2 > BOX_INNER_WIDTH - 2 )); then raw_len2=$(( BOX_INNER_WIDTH - 2 )); fi
    left_pad=$(( (BOX_INNER_WIDTH - raw_len2) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - raw_len2 - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_MAGENTA}Anim: ${C_CYAN}${d_type} ${C_MAGENTA}| Dur: ${C_CYAN}${d_dur} ${C_MAGENTA}| FPS: ${C_CYAN}${d_fps} ${C_MAGENTA}| Pos: ${C_CYAN}${d_pos}${C_RESET}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'

    # --- Tab Bar ---
    local tab_line="${C_MAGENTA}│"
    local -i printed_len=0
    current_col=2
    TAB_ZONES=()

    for (( i = 0; i < TAB_COUNT; i++ )); do
        local name="${TABS[i]}"
        len=${#name}
        zone_start=$current_col

        if (( i == CURRENT_TAB )); then
            tab_line+="${C_CYAN}${C_INVERSE} ${name} ${C_RESET}${C_MAGENTA}│"
        else
            tab_line+="${C_GREY} ${name} ${C_MAGENTA}│"
        fi

        TAB_ZONES+=("${zone_start}:$(( zone_start + len + 2 ))")
        current_col=$(( current_col + len + 3 ))
        printed_len=$(( printed_len + len + 3 ))
    done

    # Decouple visual padding from the mouse-zone tracking index 'current_col' 
    # to guarantee a flawless 80-character internal box width.
    pad_needed=$(( BOX_INNER_WIDTH - printed_len ))
    if (( pad_needed < 0 )); then pad_needed=0; fi

    if (( pad_needed > 0 )); then
        printf -v pad_buf '%*s' "$pad_needed" ''
        tab_line+="${pad_buf}"
    fi
    tab_line+="${C_MAGENTA}│${C_RESET}"

    buf+="${tab_line}${CLR_EOL}"$'\n'

    # --- Bottom Border ---
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    # --- Item List ---
    local -n _draw_ref="TAB_ITEMS_${CURRENT_TAB}"
    count=${#_draw_ref[@]}

    # Show fav icons on color tabs 1-5 only
    local -i show_fav_icon=0
    if (( CURRENT_TAB >= 1 && CURRENT_TAB <= 5 )); then
        show_fav_icon=1
    fi

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"

    # Padding: reserve 2 chars for fav prefix on color tabs
    local -i label_pad
    if (( show_fav_icon )); then
        label_pad=$(( ITEM_PADDING - 2 ))
    else
        label_pad=$ITEM_PADDING
    fi

    for (( i = _vis_start; i < _vis_end; i++ )); do
        item="${_draw_ref[i]}"
        val="${ITEM_MAP["${CURRENT_TAB}::${item}"]}"

        if (( CURRENT_TAB == THEME_TAB || CURRENT_TAB == ANIM_TAB )); then
            if [[ "${val}" == "ACTION_APPLY_SETTINGS" ]]; then
                display="${C_YELLOW}[Enter] to Apply & Save${C_RESET}"
                prefix=""
            else
                key="${val%%|*}"
                setting_val="${SETTINGS[${key}]}"
                display="${C_YELLOW}◀ ${setting_val} ▶${C_RESET}"
                prefix=""
            fi
        elif (( CURRENT_TAB == CUSTOM_TAB )); then
            if [[ "${val}" == "ACTION_REGEN" ]]; then
                display="${C_YELLOW}[Enter] to Run${C_RESET}"
            else
                display="${C_CYAN}[Enter] to Type${C_RESET}"
            fi
            prefix=""
        else
            # TrueColor extraction processing natively
            dot=""
            if [[ "${val}" =~ ^#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$ ]]; then
                cr=$(( 16#${BASH_REMATCH[1]} ))
                cg=$(( 16#${BASH_REMATCH[2]} ))
                cb=$(( 16#${BASH_REMATCH[3]} ))
                printf -v dot '\033[38;2;%d;%d;%dm●\033[0m' "${cr}" "${cg}" "${cb}"
            fi

            if [[ -n "${LAST_APPLIED_HEX}" && "${val^^}" == "${LAST_APPLIED_HEX^^}" ]]; then
                display="${dot} ${C_GREEN}ACTIVE${C_RESET}"
            else
                display="${dot} ${C_GREY}${val}${C_RESET}"
            fi

            # Favorite indicator injection
            if (( show_fav_icon )); then
                if [[ -n "${FAV_HEX_LOOKUP["${val^^}"]:-}" ]]; then
                    prefix="${C_RED}${FAV_ICON}${C_RESET} "
                else
                    prefix="  "
                fi
            else
                prefix=""
            fi
        fi

        printf -v padded_item "%-${label_pad}s" "${item:0:${label_pad}}"

        # Ensure selected/unselected rows are perfectly vertically aligned (both offset by exactly 4 visual chars)
        if (( i == SELECTED_ROW )); then
            buf+="${C_CYAN}  ➤ ${C_RESET}${prefix}${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            buf+="    ${prefix}${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    # Pad empty rows seamlessly
    local -i rows_rendered=$(( _vis_end - _vis_start ))
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do
        buf+="${CLR_EOL}"$'\n'
    done

    render_scroll_indicator buf "below" "$count" "$_vis_end"

    # Feedback Line
    if [[ -n "${LAST_STATUS_MSG}" ]]; then
        buf+=" ${LAST_STATUS_MSG}${CLR_EOL}"$'\n'
    else
        buf+="${CLR_EOL}"$'\n'
    fi

    # Footer
    if (( CURRENT_TAB == FAVORITES_TAB )); then
        if (( count > 0 )); then
            buf+="${C_CYAN} [Enter] Apply  [x] Remove  [Tab] Switch  [↑↓/jk] Nav  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
        else
            buf+="${C_CYAN} No favorites yet! Use [f] on any color tab to add.  [Tab] Switch  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
        fi
    elif (( CURRENT_TAB == THEME_TAB || CURRENT_TAB == ANIM_TAB )); then
        buf+="${C_CYAN} [←/→ h/l] Adjust  [Enter] Apply  [Tab] Switch  [↑↓/jk] Nav  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    elif (( CURRENT_TAB == CUSTOM_TAB )); then
        buf+="${C_CYAN} [Enter] Action  [Tab] Switch  [↑↓/jk] Nav  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    else
        buf+="${C_CYAN} [Enter] Apply  [f] ${FAV_ICON} Toggle Fav  [Tab] Switch  [↑↓/jk] Nav  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    fi

    buf+="${CLR_EOS}"
    printf '%s' "${buf}"
}

# =============================================================================
# ▼ INPUT HANDLING ▼
# =============================================================================

navigate() {
    local -i dir=$1
    local -n _nav_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_nav_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
    return 0
}

navigate_page() {
    local -i dir=$1
    local -n _navp_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_navp_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
    return 0
}

navigate_end() {
    local -i target=$1
    local -n _nave_ref="TAB_ITEMS_${CURRENT_TAB}"
    local -i count=${#_nave_ref[@]}
    if (( count == 0 )); then return 0; fi
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
    return 0
}

switch_tab() {
    local -i dir=${1:-1}
    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
    SELECTED_ROW=0
    SCROLL_OFFSET=0
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        CURRENT_TAB=$idx
        SELECTED_ROW=0
        SCROLL_OFFSET=0
    fi
}

adjust_setting() {
    local -i dir=$1
    if (( CURRENT_TAB != THEME_TAB && CURRENT_TAB != ANIM_TAB )); then return 0; fi
    local -n _adj_ref="TAB_ITEMS_${CURRENT_TAB}"
    if (( ${#_adj_ref[@]} == 0 )); then return 0; fi
    
    local label="${_adj_ref[${SELECTED_ROW}]}"
    local val="${ITEM_MAP["${CURRENT_TAB}::${label}"]}"
    if [[ "${val}" != ACTION_* ]]; then
        modify_setting "${label}" "${dir}"
    fi
}

handle_enter() {
    local -n _act_ref="TAB_ITEMS_${CURRENT_TAB}"
    if (( ${#_act_ref[@]} == 0 )); then return 0; fi
    trigger_action "${_act_ref[${SELECTED_ROW}]}"
}

handle_mouse() {
    local input="$1"
    local -i button x y i start end
    local zone

    local body="${input#'[<'}"
    [[ "$body" == "$input" ]] && return 0
    local terminator="${body: -1}"
    [[ "$terminator" != "M" && "$terminator" != "m" ]] && return 0
    body="${body%[Mm]}"

    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    [[ ! "$field1" =~ ^[0-9]+$ ]] && return 0
    [[ ! "$field2" =~ ^[0-9]+$ ]] && return 0
    [[ ! "$field3" =~ ^[0-9]+$ ]] && return 0

    button=$field1; x=$field2; y=$field3

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi

    [[ "$terminator" != "M" ]] && return 0

    if (( y == TAB_ROW )); then
        for (( i = 0; i < TAB_COUNT; i++ )); do
            zone="${TAB_ZONES[i]}"
            start="${zone%%:*}"
            end="${zone##*:}"
            if (( x >= start && x <= end )); then set_tab "$i"; return 0; fi
        done
        return 0
    fi

    local -i effective_start=$(( ITEM_START_Y + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))

        local -n _mouse_items_ref="TAB_ITEMS_${CURRENT_TAB}"
        local -i count=${#_mouse_items_ref[@]}

        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            local label="${_mouse_items_ref[${clicked_idx}]}"
            local val="${ITEM_MAP["${CURRENT_TAB}::${label}"]}"

            if (( CURRENT_TAB == THEME_TAB || CURRENT_TAB == ANIM_TAB )); then
                if [[ "${val}" == ACTION_* ]]; then
                    if (( button == 0 )); then trigger_action "${label}"; fi
                else
                    if (( x > ADJUST_THRESHOLD )); then
                        if (( button == 0 )); then adjust_setting 1; else adjust_setting -1; fi
                    fi
                fi
            else
                if (( button == 0 && x > ADJUST_THRESHOLD )); then
                    trigger_action "${label}"
                fi
            fi
        fi
    fi
    return 0
}

read_escape_seq() {
    local -n _esc_out=$1
    _esc_out=""
    local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; then
        return 1
    fi
    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
            _esc_out+="$char"
            if [[ "$char" =~ [a-zA-Z~] ]]; then break; fi
        done
    fi
    return 0
}

# =============================================================================
# ▼ INPUT ROUTER ▼
# =============================================================================

handle_key() {
    local key="$1"

    case "$key" in
        '[Z')                switch_tab -1; return ;;
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[C'|'OC')           adjust_setting 1; return ;;
        '[D'|'OD')           adjust_setting -1; return ;;
        '[5~')               navigate_page -1; return ;;
        '[6~')               navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac

    case "$key" in
        k|K)                 navigate -1 ;;
        j|J)                 navigate 1 ;;
        l|L)                 adjust_setting 1 ;;
        h|H)                 adjust_setting -1 ;;
        g)                   navigate_end 0 ;;
        G)                   navigate_end 1 ;;
        $'\t')               switch_tab 1 ;;
        ''|$'\n'|o|O)        handle_enter ;;
        $'\x7f'|$'\x08'|$'\e\n') adjust_setting -1 ;;
        f|F)                 toggle_favorite ;;
        x|X)                 remove_favorite ;;
        q|Q|$'\x03')         exit 0 ;;
    esac
}

handle_input_router() {
    local key="$1"
    local escape_seq=""

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
            if [[ "$key" == "" || "$key" == $'\n' ]]; then
                key=$'\e\n'
            fi
        else
            return 0
        fi
    fi

    handle_key "$key"
}

# =============================================================================
# ▼ MAIN ▼
# =============================================================================

main() {
    if (( BASH_VERSINFO[0] < 5 )); then
        log_err "Bash 5.0+ required (found ${BASH_VERSION})"
        exit 1
    fi

    if [[ ! -t 0 ]]; then
        log_err "TTY required. Cannot run non-interactively."
        exit 1
    fi

    local _dep
    for _dep in matugen; do
        if ! command -v "$_dep" &>/dev/null; then
            log_err "Required dependency not found: ${_dep}"
            exit 1
        fi
    done

    local -i term_cols term_rows
    term_cols=$(tput cols 2>/dev/null) || term_cols=80
    term_rows=$(tput lines 2>/dev/null) || term_rows=24

    if (( term_cols < MIN_COLS || term_rows < MIN_ROWS )); then
        log_err "Terminal too small: ${term_cols}x${term_rows} (need ${MIN_COLS}x${MIN_ROWS})"
        exit 1
    fi

    register_items
    load_state
    load_favorites

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    if ! stty -icanon -echo min 1 time 0 2>/dev/null; then
        log_err "Failed to configure terminal (stty). Cannot run interactively."
        exit 1
    fi

    printf '%s%s%s%s' "${MOUSE_ON}" "${CURSOR_HIDE}" "${CLR_SCREEN}" "${CURSOR_HOME}"

    local key
    while true; do
        draw_ui
        IFS= read -rsn1 key || break
        handle_input_router "$key"
    done
}

main "$@"

#!/usr/bin/env bash
# ==============================================================================
# Script: mouse_button_reverse.sh
# Purpose: Toggles mouse handedness in Hyprland (Lua & Conf Hybrid Support)
# Engine: Dusky TUI Engine (Advanced Hybrid Core)
# Usage:  ./mouse_button_reverse.sh [ --left | --right | --toggle ]
#         No args = interactive TUI toggle
# ==============================================================================

set -Eeuo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION ▼
# =============================================================================

# Define the target configuration file (defaults to your specific Lua path)
declare CONFIG_FILE="${HOME}/.config/hypr/edit_here/source/input.lua"

declare -r APP_TITLE="Mouse Handedness"
declare -r APP_VERSION="v7.0 (Hybrid Edition)"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=4
declare -ri BOX_INNER_WIDTH=56
declare -ri ITEM_PADDING=28
declare -ri HEADER_ROWS=4
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

# =============================================================================
# ▲ END OF USER CONFIGURATION ▲
# =============================================================================

# --- Pre-computed Constants ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" '' || true
declare -r H_LINE="${_h_line_buf// /─}"
unset _h_line_buf

# --- ANSI Constants ---
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

# --- State Management ---
declare -i SELECTED_ROW=0
declare ORIGINAL_STTY=""
declare _TMPFILE=""
declare CURRENT_VALUE="unknown"
declare -a _TEMP_PATHS=()

# --- System Helpers & Safeties ---

log_success() { printf '%s[OK]%s %s\n' "$C_GREEN" "$C_RESET" "$1"; }
log_err() { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2 || true; }

register_temp() {
    local path=$1
    [[ -n $path ]] && _TEMP_PATHS+=("$path")
}

cleanup() {
    local path
    if [[ -t 1 ]]; then
        printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    fi
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" < /dev/tty 2>/dev/null || :
    fi
    for path in "${_TEMP_PATHS[@]:-}"; do
        [[ -n $path && -e $path ]] && rm -f -- "$path" 2>/dev/null || :
    done
    _TEMP_PATHS=()
    _TMPFILE=""
    printf '\n' 2>/dev/null || :
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 131' QUIT
trap 'exit 143' TERM

# --- String Helpers ---

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z[\\\]^_\`a-z\{\|\}~])/}"
    REPLY="$v"
}

# --- Hybrid Depth-Aware Config Parser (Lua & Conf) ---

read_current_value() {
    local val
    val=$(LC_ALL=C awk '
        BEGIN { depth = 0; in_input = 0; input_depth = 0; found_val = "" }
        {
            clean = $0
            sub(/--.*$/, "", clean)  # Strip Lua comments
            sub(/#.*$/, "", clean)   # Strip Conf comments

            # Detect entry into the `input` block
            if (match(clean, /input[[:space:]]*=?[[:space:]]*\{/)) {
                if (!in_input) {
                    in_input = 1
                    input_depth = depth + 1
                }
            }
            
            # Increment depth for all opening braces on this line
            depth += gsub(/\{/, "{", clean)

            # Check for the assignment ONLY when inside the root input depth
            if (in_input && depth == input_depth && match(clean, /^[[:space:]]*left_handed[[:space:]]*=[[:space:]]*/)) {
                val_str = clean
                sub(/^[[:space:]]*left_handed[[:space:]]*=[[:space:]]*/, "", val_str)
                sub(/,.*$/, "", val_str) # Remove trailing commas (Lua)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", val_str)
                found_val = val_str
            }

            # Decrement depth for all closing braces on this line
            close_braces = gsub(/\}/, "}", clean)
            while (close_braces > 0 && depth > 0) {
                if (in_input && depth == input_depth) {
                    in_input = 0
                    input_depth = 0
                }
                depth--
                close_braces--
            }
        }
        END { print found_val }
    ' "$CONFIG_FILE")

    if [[ -z "$val" ]]; then
        CURRENT_VALUE="unknown"
    else
        CURRENT_VALUE="$val"
    fi
}

# --- Atomic File Writer ---

write_value_to_file() {
    local new_val="$1"
    local target_dir target_base

    if [[ "$CURRENT_VALUE" == "$new_val" ]]; then return 0; fi

    target_dir=$(dirname "$CONFIG_FILE")
    target_base=$(basename "$CONFIG_FILE")

    if ! _TMPFILE=$(mktemp --tmpdir="$target_dir" ".${target_base}.tmp.XXXXXXXXXX" 2>/dev/null); then
        log_err "Failed to create atomic temporary file in $target_dir"
        return 1
    fi
    register_temp "$_TMPFILE"

    if ! LC_ALL=C awk -v new_value="$new_val" '
        BEGIN { depth = 0; in_input = 0; input_depth = 0; replaced = 0 }
        {
            line = $0
            clean = line
            sub(/--.*$/, "", clean)
            sub(/#.*$/, "", clean)

            if (match(clean, /input[[:space:]]*=?[[:space:]]*\{/)) {
                if (!in_input) {
                    in_input = 1
                    input_depth = depth + 1
                }
            }
            
            depth += gsub(/\{/, "{", clean)

            do_replace = 0
            if (in_input && depth == input_depth && clean ~ /^[[:space:]]*left_handed[[:space:]]*=[[:space:]]*/) {
                do_replace = 1
            }

            if (do_replace) {
                match(line, /left_handed[[:space:]]*=[[:space:]]*/)
                prefix = substr(line, 1, RSTART + RLENGTH - 1)
                match(line, /,?[[:space:]]*(--.*|#.*)?$/)
                suffix = substr(line, RSTART, RLENGTH)
                print prefix new_value suffix
                replaced = 1
            } else {
                print line
            }

            close_braces = gsub(/\}/, "}", clean)
            while (close_braces > 0 && depth > 0) {
                if (in_input && depth == input_depth) {
                    in_input = 0
                    input_depth = 0
                }
                depth--
                close_braces--
            }
        }
        END { exit (replaced ? 0 : 1) }
    ' "$CONFIG_FILE" > "$_TMPFILE"; then
        rm -f "$_TMPFILE" 2>/dev/null || :
        return 1
    fi

    # Atomic swap: preserves ownership/permissions and prevents partial writes
    chown --reference="$CONFIG_FILE" -- "$_TMPFILE" 2>/dev/null || :
    chmod --reference="$CONFIG_FILE" -- "$_TMPFILE" 2>/dev/null || :
    mv -fT -- "$_TMPFILE" "$CONFIG_FILE" || return 1

    CURRENT_VALUE="$new_val"
    return 0
}

# --- Post-Write Hook ---

post_write_action() {
    if pgrep -x "Hyprland" > /dev/null 2>&1 || pgrep -x "Hyprland" -P 1 > /dev/null 2>&1; then
        hyprctl reload > /dev/null 2>&1 || :
    fi
}

# --- TUI Rendering Engine ---

declare -ra MENU_ITEMS=("Left-Handed Mode" "Apply & Quit")

draw_ui() {
    local buf="" pad_buf=""
    local -i left_pad right_pad vis_len i

    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    strip_ansi "$APP_TITLE"; local -i t_len=${#REPLY}
    strip_ansi "$APP_VERSION"; local -i v_len=${#REPLY}
    vis_len=$(( t_len + v_len + 1 ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_WHITE}${APP_TITLE} ${C_CYAN}${APP_VERSION}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    local subtitle
    if [[ "$CURRENT_VALUE" == "true" ]]; then
        subtitle="Currently: ${C_GREEN}Left-Handed${C_MAGENTA}"
    elif [[ "$CURRENT_VALUE" == "false" ]]; then
        subtitle="Currently: ${C_CYAN}Right-Handed${C_MAGENTA}"
    else
        subtitle="Currently: ${C_RED}Unknown State${C_MAGENTA}"
    fi
    
    strip_ansi "$subtitle"; local -i s_len=${#REPLY}
    left_pad=$(( (BOX_INNER_WIDTH - s_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - s_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${subtitle}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'
    buf+="${CLR_EOL}"$'\n'

    local -i count=${#MENU_ITEMS[@]}
    local item display padded_item

    for (( i = 0; i < count; i++ )); do
        item="${MENU_ITEMS[i]}"

        case "$item" in
            "Left-Handed Mode")
                if [[ "$CURRENT_VALUE" == "true" ]]; then
                    display="${C_GREEN}ON${C_RESET}"
                elif [[ "$CURRENT_VALUE" == "false" ]]; then
                    display="${C_RED}OFF${C_RESET}"
                else
                    display="${C_YELLOW}ERR${C_RESET}"
                fi
                ;;
            "Apply & Quit")
                display="${C_YELLOW}[Enter]${C_RESET}"
                ;;
        esac

        printf -v padded_item "%-${ITEM_PADDING}s" "${item:0:${ITEM_PADDING}}"
        if (( i == SELECTED_ROW )); then
            buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    local -i rows_rendered=$count
    for (( i = rows_rendered; i < MAX_DISPLAY_ROWS; i++ )); do
        buf+="${CLR_EOL}"$'\n'
    done

    buf+="${CLR_EOL}"$'\n'
    buf+=$'\n'"${C_CYAN} [↑/↓ j/k] Navigate  [←/→ h/l Enter] Toggle  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    
    local filename
    filename=$(basename "$CONFIG_FILE")
    buf+="${C_CYAN} Target: ${C_WHITE}${filename}${C_RESET}${CLR_EOL}${CLR_EOS}"
    printf '%s' "$buf"
}

# --- Input Handling ---

navigate() {
    local -i dir=$1
    local -i count=${#MENU_ITEMS[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
}

action_toggle() {
    local item="${MENU_ITEMS[SELECTED_ROW]}"
    case "$item" in
        "Left-Handed Mode")
            local new_val
            if [[ "$CURRENT_VALUE" == "true" ]]; then
                new_val="false"
            else
                new_val="true"
            fi
            if write_value_to_file "$new_val"; then
                post_write_action
            fi
            ;;
        "Apply & Quit")
            exit 0
            ;;
    esac
}

read_escape_seq() {
    local -n _esc_out=$1
    _esc_out=""
    local char
    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char < /dev/tty; then
        return 1
    fi
    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char < /dev/tty; do
            _esc_out+="$char"
            if [[ "$char" =~ [a-zA-Z~] ]]; then break; fi
        done
    fi
    return 0
}

handle_mouse() {
    local input="$1"
    local -i button x y

    local body="${input#'[<'}"
    if [[ "$body" == "$input" ]]; then return 0; fi
    local terminator="${body: -1}"
    if [[ "$terminator" != "M" && "$terminator" != "m" ]]; then return 0; fi
    body="${body%[Mm]}"
    
    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    if [[ ! "$field1" =~ ^[0-9]+$ || ! "$field2" =~ ^[0-9]+$ || ! "$field3" =~ ^[0-9]+$ ]]; then return 0; fi
    button=$field1; x=$field2; y=$field3

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi
    if [[ "$terminator" != "M" ]]; then return 0; fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start ))
        local -i count=${#MENU_ITEMS[@]}
        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            if (( button == 0 )); then
                action_toggle
            fi
        fi
    fi
    return 0
}

handle_input() {
    local key="$1"
    local escape_seq=""

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
        else
            exit 0
        fi
    fi

    case "$key" in
        '[A'|'OA'|k|K)       navigate -1 ;;
        '[B'|'OB'|j|J)       navigate 1 ;;
        '[C'|'OC'|l|L)       action_toggle ;;
        '[D'|'OD'|h|H)       action_toggle ;;
        '[H'|'[1~'|g)        SELECTED_ROW=0 ;;
        '[F'|'[4~'|G)        SELECTED_ROW=$(( ${#MENU_ITEMS[@]} - 1 )) ;;
        '['*'<'*[Mm])        handle_mouse "$key" ;;
        ''|$'\n')            action_toggle ;;
        q|Q|$'\x03')         exit 0 ;;
    esac
}

# --- Flag-based (Non-Interactive) Mode ---

run_flag_mode() {
    local target_val="$1"
    local action_msg="$2"

    read_current_value

    printf '  %s...\n' "$action_msg"

    if write_value_to_file "$target_val"; then
        log_success "Configuration updated."
        post_write_action
    else
        log_err "Failed to write to config file."
        exit 1
    fi
}

# --- Main ---

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5+ required"; exit 1; fi

    if [[ ! -f "$CONFIG_FILE" ]]; then
        log_err "Config file not found: $CONFIG_FILE"
        exit 1
    fi
    if [[ ! -w "$CONFIG_FILE" ]]; then
        log_err "Config not writable: $CONFIG_FILE"
        exit 1
    fi

    # --- CLI Arguments ---
    if [[ $# -gt 0 ]]; then
        case "$1" in
            --left)
                run_flag_mode "true" "Setting Left-Handed Mode (Force)"
                exit 0
                ;;
            --right)
                run_flag_mode "false" "Setting Right-Handed Mode (Force)"
                exit 0
                ;;
            --toggle)
                read_current_value
                if [[ "$CURRENT_VALUE" == "true" ]]; then
                    run_flag_mode "false" "Toggling to Right-Handed Mode"
                else
                    run_flag_mode "true" "Toggling to Left-Handed Mode"
                fi
                exit 0
                ;;
            *)
                log_err "Unknown argument: $1"
                printf 'Usage: %s [ --left | --right | --toggle ]\n' "$0"
                exit 1
                ;;
        esac
    fi

    # --- Interactive TUI Mode ---
    if [[ ! -t 0 ]]; then log_err "TTY required for interactive mode"; exit 1; fi

    read_current_value

    ORIGINAL_STTY=$(stty -g < /dev/tty 2>/dev/null) || ORIGINAL_STTY=""
    stty -icanon -echo -ixon min 1 time 0 < /dev/tty 2>/dev/null

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"

    local key
    while true; do
        draw_ui
        IFS= read -rsn1 key < /dev/tty || break
        handle_input "$key"
    done
}

main "$@"

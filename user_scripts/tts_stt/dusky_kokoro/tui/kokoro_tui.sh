#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Dusky Kokoro TUI Engine - Master v5.2.0 (Full TOML & Voices Matrix)
# -----------------------------------------------------------------------------
# Target: TOML Config Editor & Daemon Manager
# -----------------------------------------------------------------------------

set -euo pipefail
shopt -s extglob

# =============================================================================
# ▼ USER CONFIGURATION & TARGETS ▼
# =============================================================================

declare -r CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/dusky-kokoro"
declare -r CONFIG_FILE="${CONFIG_DIR}/config.toml"
declare -r CONTAINED_DIR="${HOME}/contained_apps/uv/dusky_kokoro"
declare -r TRIGGER_SCRIPT="${HOME}/user_scripts/tts_stt/dusky_kokoro/trigger.sh"

declare -r APP_TITLE="Dusky Kokoro TTS Setup"
declare -r APP_VERSION="v5.2.0"

# Dimensions & Layout
declare -ri MAX_DISPLAY_ROWS=14
declare -ri BOX_INNER_WIDTH=76
declare -ri ADJUST_THRESHOLD=38
declare -ri ITEM_PADDING=32

declare -ri HEADER_ROWS=4
declare -ri TAB_ROW=3
declare -ri ITEM_START_ROW=$(( HEADER_ROWS + 1 ))

declare -ra TABS=("Voices" "Speed & Timing" "Playback" "Engine & GPU")

# Item Registration
register_items() {
    # All 54 official Kokoro voices grouped by quality & language
    local all_voices='"af_heart","af_bella","af_nicole","af_aoede","af_kore","af_sarah","af_alloy","af_nova","af_sky","af_jessica","af_river","am_michael","am_fenrir","am_puck","am_echo","am_eric","am_liam","am_onyx","am_adam","am_santa","bf_emma","bf_isabella","bf_alice","bf_lily","bm_george","bm_fable","bm_daniel","bm_lewis","jf_alpha","jf_gongitsune","jf_nezumi","jf_tebukuro","jm_kumo","zf_xiaobei","zf_xiaoni","zf_xiaoxiao","zf_xiaoyi","zm_yunjian","zm_yunxi","zm_yunxia","zm_yunyang","ef_dora","em_alex","em_santa","ff_siwis","hf_alpha","hf_beta","hm_omega","hm_psi","if_sara","im_nicola","pf_dora","pm_alex","pm_santa"'
    local v3_voices='"none",'${all_voices}

    # Tab 0: Voices & Speech
    register 0 "Blend Voices"            'blend|bool||||' "true"
    register 0 "Primary Voice (V1)"      'voice_1|cycle||'"${all_voices}"'||' '"af_heart"'
    register 0 "Primary Weight (W1)"     'weight_1|float||0.05|1.0|0.05' "0.4"
    register 0 "Secondary Voice (V2)"    'voice_2|cycle||'"${all_voices}"'||' '"af_bella"'
    register 0 "Secondary Weight (W2)"   'weight_2|float||0.0|1.0|0.05' "0.6"
    register 0 "Third Voice (V3)"        'voice_3|cycle||'"${v3_voices}"'||' '"none"'
    register 0 "Third Weight (W3)"       'weight_3|float||0.0|1.0|0.05' "0.0"
    register 0 "Language Code"           'lang|cycle||"auto","en-us","en-gb","ja","cmn","es","fr-fr","hi","it","pt-br"||' '"auto"'

    # Tab 1: Speed & Timing
    register 1 "Speech Gen Speed"        'speed|float||0.5|2.0|0.05' "1.0"
    register 1 "MPV Playback Speed"      'mpv_speed|float||0.5|2.0|0.05' "1.0"
    register 1 "Sentence Pause (ms)"     'sentence_pause_ms|int||0|1000|20' "140"
    register 1 "Paragraph Pause (ms)"    'paragraph_pause_ms|int||0|2000|50' "380"
    register 1 "Trim Silence"            'trim_silence|bool||||' "true"

    # Tab 2: Playback
    register 2 "Audio Volume"            'volume|int||0|150|5' "100"
    register 2 "Show MPV Window"         'window|bool||||' "true"
    register 2 "Window Geometry"         'window_geometry|cycle||"420x96","360x80","500x120","300x60"||' '"420x96"'
    register 2 "Archive to WAV"          'enabled|bool||||' "true"
    register 2 "Archive Bit Depth"       'bit_depth|cycle||16,24||' "16"
    register 2 "Prefetch Segments"       'prefetch_segments|int||1|16|1' "4"

    # Tab 3: Engine & GPU
    register 3 "Hardware Provider"       'provider|cycle||"cuda","cpu","rocm","openvino","auto"||' '"cuda"'
    register 3 "Model Precision"         'precision|cycle||"fp16-gpu","int8","f32","fp16","auto"||' '"auto"'
    register 3 "Model Idle Unload (s)"   'model_idle_timeout_s|float||5.0|300.0|5.0' "30.0"
    register 3 "Process Idle Exit (s)"   'process_idle_timeout_s|float||10.0|600.0|10.0' "30.0"
    register 3 "GPU VRAM Limit (MB)"     'gpu_mem_limit_mb|int||0|8192|256' "2048"
    register 3 "Warmup on Load"          'warmup|bool||||' "true"
}

# Post-Write Hook (Hot-reload running daemon)
post_write_action() {
    if [[ -x "$TRIGGER_SCRIPT" ]]; then
        "$TRIGGER_SCRIPT" --reload >/dev/null 2>&1 &
    fi
}

# =============================================================================
# ▲ END OF USER CONFIGURATION ▲
# =============================================================================

# --- Pre-computed Constants ---
declare _h_line_buf
printf -v _h_line_buf '%*s' "$BOX_INNER_WIDTH" ''
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
declare -r UNSET_MARKER='«unset»'

# --- State Management ---
declare -i SELECTED_ROW=0
declare -i CURRENT_TAB=0
declare -i SCROLL_OFFSET=0
declare -ri TAB_COUNT=${#TABS[@]}
declare -a TAB_ZONES=()
declare -i TAB_SCROLL_START=0
declare ORIGINAL_STTY=""

declare -i CURRENT_VIEW=0
declare CURRENT_MENU_ID=""
declare -i PARENT_ROW=0
declare -i PARENT_SCROLL=0

declare _TMPFILE=""
declare _TMPMODE=""
declare WRITE_TARGET=""

declare -i TERM_ROWS=0
declare -i TERM_COLS=0
declare -ri MIN_TERM_COLS=$(( BOX_INNER_WIDTH + 2 ))
declare -ri MIN_TERM_ROWS=$(( HEADER_ROWS + MAX_DISPLAY_ROWS + 5 ))

declare -gi LAST_WRITE_CHANGED=0
declare -gi NEEDS_RESTART=0
declare STATUS_MESSAGE=""
declare DAEMON_STATUS_UI=""
declare DAEMON_IS_RUNNING="0"

declare LEFT_ARROW_ZONE=""
declare RIGHT_ARROW_ZONE=""

declare -A ITEM_MAP=()
declare -A VALUE_CACHE=()
declare -A CONFIG_CACHE=()
declare -A DEFAULTS=()

for (( _ti = 0; _ti < TAB_COUNT; _ti++ )); do
    declare -ga "TAB_ITEMS_${_ti}=()"
done
unset _ti

# --- System Helpers ---

log_err() {
    printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RESET" "$1" >&2
}

set_status() {
    declare -g STATUS_MESSAGE="$1"
}

clear_status() {
    declare -g STATUS_MESSAGE=""
}

check_daemon_status() {
    local pid_file="/tmp/dusky_kokoro.pid"
    if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file" 2>/dev/null)" 2>/dev/null; then
        DAEMON_STATUS_UI="${C_GREEN}● RUNNING${C_RESET}"
        DAEMON_IS_RUNNING="1"
    else
        DAEMON_STATUS_UI="${C_RED}● STOPPED${C_RESET}"
        DAEMON_IS_RUNNING="0"
    fi
}

toggle_daemon() {
    if [[ -x "$TRIGGER_SCRIPT" ]]; then
        check_daemon_status
        if [[ "$DAEMON_IS_RUNNING" == "1" ]]; then
            set_status "Stopping Daemon..."
            draw_ui
            "$TRIGGER_SCRIPT" --kill >/dev/null 2>&1
        else
            set_status "Starting Daemon..."
            draw_ui
            "$TRIGGER_SCRIPT" --restart >/dev/null 2>&1 &
            disown 2>/dev/null || :
        fi
        sleep 1
        clear_status
    else
        set_status "Trigger script missing at $TRIGGER_SCRIPT"
    fi
}

restore_terminal_mode() {
    if [[ -n "${ORIGINAL_STTY:-}" ]]; then
        stty "$ORIGINAL_STTY" 2>/dev/null || :
    fi
}

enable_raw_mode() {
    stty -icanon -echo min 1 time 0 2>/dev/null || :
}

cleanup() {
    printf '%s%s%s' "$MOUSE_OFF" "$CURSOR_SHOW" "$C_RESET" 2>/dev/null || :
    restore_terminal_mode
    if [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" ]]; then
        rm -f "$_TMPFILE" 2>/dev/null || :
    fi
    _TMPFILE=""
    _TMPMODE=""
    printf '\n' 2>/dev/null || :

    # Execute deferred restart upon clean exit
    if (( NEEDS_RESTART == 1 )); then
        if [[ -x "$TRIGGER_SCRIPT" ]]; then
            printf "%s[Kokoro TUI] Configurations updated. Restarting Daemon...%s\n" "$C_CYAN" "$C_RESET"
            "$TRIGGER_SCRIPT" --restart >/dev/null 2>&1 &
            disown 2>/dev/null || :
        fi
    fi
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ensure_config_file() {
    mkdir -p "$CONFIG_DIR"
    if [[ ! -f "$CONFIG_FILE" ]]; then
        local main_py="${CONTAINED_DIR}/dusky_main.py"
        local venv_py="${CONTAINED_DIR}/.venv/bin/python"
        if [[ -x "$venv_py" && -f "$main_py" ]]; then
            "$venv_py" "$main_py" config --write "$CONFIG_FILE" >/dev/null 2>&1 || true
        fi
        if [[ ! -f "$CONFIG_FILE" ]]; then
            cat > "$CONFIG_FILE" <<'EOF'
# Dusky Kokoro TTS - configuration (TOML)
[voice]
spec = "af_heart:0.4,af_bella:0.6"
blend = true
voice_1 = "af_heart"
weight_1 = 0.4
voice_2 = "af_bella"
weight_2 = 0.6
voice_3 = "none"
weight_3 = 0.0
speed = 1.0
lang = "auto"

[playback]
mpv_binary = "mpv"
mpv_speed = 1.0
volume = 100
window = true
window_geometry = "420x96"
prefetch_segments = 4

[archive]
enabled = true
bit_depth = 16

[engine]
provider = "cuda"
precision = "auto"
model_idle_timeout_s = 30.0
gpu_mem_limit_mb = 2048
warmup = true

[daemon]
process_idle_timeout_s = 30.0
exit_when_idle = true
dedup_window_s = 2.0
max_queue = 8

[text]
sentence_pause_ms = 140
paragraph_pause_ms = 380
trim_silence = true
strip_citations = true
read_code_blocks = false
url_mode = "domain"
emoji_mode = "strip"
EOF
        fi
    fi
    if [[ -d "$CONTAINED_DIR" && ! -e "$CONTAINED_DIR/config.toml" ]]; then
        ln -sfn "$CONFIG_FILE" "$CONTAINED_DIR/config.toml" 2>/dev/null || true
    fi
}

resolve_write_target() {
    ensure_config_file
    WRITE_TARGET=$(realpath -e -- "$CONFIG_FILE")
}

create_tmpfile() {
    local target_dir target_base
    target_dir=$(dirname -- "$WRITE_TARGET")
    target_base=$(basename -- "$WRITE_TARGET")
    if ! _TMPFILE=$(mktemp --tmpdir="$target_dir" ".${target_base}.tmp.XXXXXXXXXX" 2>/dev/null); then
        _TMPFILE=""
        _TMPMODE=""
        return 1
    fi
    _TMPMODE="atomic"
    return 0
}

commit_tmpfile() {
    [[ -n "${_TMPFILE:-}" && -f "$_TMPFILE" && "${_TMPMODE:-}" == "atomic" ]] || return 1
    chmod --reference="$WRITE_TARGET" -- "$_TMPFILE" 2>/dev/null || return 1
    mv -f -- "$_TMPFILE" "$WRITE_TARGET" || return 1
    _TMPFILE=""
    _TMPMODE=""
    return 0
}

update_terminal_size() {
    local size
    if size=$(stty size < /dev/tty 2>/dev/null); then
        TERM_ROWS=${size%% *}
        TERM_COLS=${size##* }
    else
        TERM_ROWS=0
        TERM_COLS=0
    fi
}

terminal_size_ok() {
    (( TERM_COLS >= MIN_TERM_COLS && TERM_ROWS >= MIN_TERM_ROWS ))
}

draw_small_terminal_notice() {
    printf '%s%s' "$CURSOR_HOME" "$CLR_SCREEN"
    printf '%sTerminal too small%s\n' "$C_RED" "$C_RESET"
    printf '%sNeed at least:%s %d cols × %d rows\n' "$C_YELLOW" "$C_RESET" "$MIN_TERM_COLS" "$MIN_TERM_ROWS"
    printf '%sCurrent size:%s %d cols × %d rows\n' "$C_WHITE" "$C_RESET" "$TERM_COLS" "$TERM_ROWS"
    printf '%sResize the terminal, then continue. Press q to quit.%s%s' "$C_CYAN" "$C_RESET" "$CLR_EOS"
}

strip_ansi() {
    local v="$1"
    v="${v//$'\033'\[*([0-9;:?<=>])@([@A-Z\[\\\]^_\`a-z\{|\}~])/}"
    REPLY="$v"
}

# --- Core Logic Engine ---

register() {
    local -i tab_idx=$1
    local label="$2" config="$3" default_val="${4:-}"
    local key type block min max step
    IFS='|' read -r key type block min max step <<< "$config"

    ITEM_MAP["${tab_idx}::${label}"]="$config"
    if [[ -n "$default_val" ]]; then
        DEFAULTS["${tab_idx}::${label}"]="$default_val"
    fi
    local -n _reg_tab_ref="TAB_ITEMS_${tab_idx}"
    _reg_tab_ref+=("$label")

    if [[ "$type" == "menu" ]]; then
        if ! declare -p "SUBMENU_ITEMS_${key}" &>/dev/null; then
            declare -ga "SUBMENU_ITEMS_${key}=()"
        fi
    fi
}

register_child() {
    local parent_id="$1"
    local label="$2" config="$3" default_val="${4:-}"

    if ! declare -p "SUBMENU_ITEMS_${parent_id}" &>/dev/null; then
        declare -ga "SUBMENU_ITEMS_${parent_id}=()"
    fi

    ITEM_MAP["${parent_id}::${label}"]="$config"
    if [[ -n "$default_val" ]]; then
        DEFAULTS["${parent_id}::${label}"]="$default_val"
    fi
    local -n _child_ref="SUBMENU_ITEMS_${parent_id}"
    _child_ref+=("$label")
}

populate_config_cache() {
    CONFIG_CACHE=()
    local key_part value_part

    while IFS='|' read -r key_part value_part || [[ -n "${key_part:-}" ]]; do
        [[ -n "${key_part:-}" ]] || continue
        CONFIG_CACHE["$key_part"]="$value_part"
    done < <(LC_ALL=C awk '
        function trim(s) {
            sub(/^[[:space:]]+/, "", s)
            sub(/[[:space:]]+$/, "", s)
            return s
        }
        BEGIN { in_multiline = 0; current_k = ""; current_v = "" }
        {
            clean = $0
            if (!in_multiline && match(clean, /^[[:space:]]*[A-Za-z0-9_]+[[:space:]]*=/)) {
                eq_pos = index(clean, "=")
                k = trim(substr(clean, 1, eq_pos - 1))
                v = trim(substr(clean, eq_pos + 1))

                if (v ~ /^\[/ || v ~ /^\{/) {
                    in_multiline = 1
                    current_k = k
                    current_v = v
                    if (v ~ /[\}\]]/) {
                        in_multiline = 0
                        print k "|" v
                    }
                    next
                }

                if (match(v, /[[:space:]]*#.*$/)) { v = substr(v, 1, RSTART - 1) }
                v = trim(v)
                if (k != "") { print k "|" v }
            } else if (in_multiline) {
                current_v = current_v " " trim(clean)
                if (clean ~ /[\}\]]/) {
                    in_multiline = 0
                    print current_k "|" current_v
                }
            }
        }
    ' "$WRITE_TARGET")

    # If spec exists but voice_1 was not explicitly defined in config.toml, extract from spec
    if [[ -z "${CONFIG_CACHE["voice_1"]:-}" && -n "${CONFIG_CACHE["spec"]:-}" ]]; then
        local clean_spec="${CONFIG_CACHE["spec"]//\"/}"
        local -a parts
        IFS=',' read -r -a parts <<< "$clean_spec"
        if (( ${#parts[@]} == 1 )); then
            local p0="${parts[0]}"
            local v0="${p0%%:*}"
            CONFIG_CACHE["blend"]="false"
            CONFIG_CACHE["voice_1"]="\"${v0}\""
            CONFIG_CACHE["weight_1"]="1.0"
            CONFIG_CACHE["voice_2"]="\"af_bella\""
            CONFIG_CACHE["weight_2"]="0.0"
            CONFIG_CACHE["voice_3"]="\"none\""
            CONFIG_CACHE["weight_3"]="0.0"
        elif (( ${#parts[@]} == 2 )); then
            local p0="${parts[0]}" p1="${parts[1]}"
            local v0="${p0%%:*}" w0="${p0##*:}"
            local v1="${p1%%:*}" w1="${p1##*:}"
            [[ "$v0" == "$w0" ]] && w0="0.5"
            [[ "$v1" == "$w1" ]] && w1="0.5"
            CONFIG_CACHE["blend"]="true"
            CONFIG_CACHE["voice_1"]="\"${v0}\""
            CONFIG_CACHE["weight_1"]="$w0"
            CONFIG_CACHE["voice_2"]="\"${v1}\""
            CONFIG_CACHE["weight_2"]="$w1"
            CONFIG_CACHE["voice_3"]="\"none\""
            CONFIG_CACHE["weight_3"]="0.0"
        elif (( ${#parts[@]} >= 3 )); then
            local p0="${parts[0]}" p1="${parts[1]}" p2="${parts[2]}"
            local v0="${p0%%:*}" w0="${p0##*:}"
            local v1="${p1%%:*}" w1="${p1##*:}"
            local v2="${p2%%:*}" w2="${p2##*:}"
            CONFIG_CACHE["blend"]="true"
            CONFIG_CACHE["voice_1"]="\"${v0}\""
            CONFIG_CACHE["weight_1"]="$w0"
            CONFIG_CACHE["voice_2"]="\"${v1}\""
            CONFIG_CACHE["weight_2"]="$w1"
            CONFIG_CACHE["voice_3"]="\"${v2}\""
            CONFIG_CACHE["weight_3"]="$w2"
        fi
    fi
}

write_value_to_file() {
    local key="$1" new_val="$2" block="${3:-}"
    local cache_key="${key}"
    local current_val="${CONFIG_CACHE["$cache_key"]:-}"

    LAST_WRITE_CHANGED=0

    if [[ -n "${CONFIG_CACHE["$cache_key"]+_}" && "$current_val" == "$new_val" ]]; then
        return 0
    fi

    create_tmpfile || { set_status "Atomic save unavailable."; return 1; }

    TARGET_KEY="$key" NEW_VALUE="$new_val" \
    LC_ALL=C awk '
    BEGIN { target_nr = 0; skip_dict = 0 }
    { lines[NR] = $0 }
    {
        if (match($0, /^[[:space:]]*[A-Za-z0-9_]+[[:space:]]*=/)) {
            eq_pos = index($0, "=")
            k = substr($0, 1, eq_pos - 1)
            sub(/^[[:space:]]+/, "", k)
            sub(/[[:space:]]+$/, "", k)

            if (k == ENVIRON["TARGET_KEY"]) {
                target_nr = NR
            }
        }
    }
    END {
        if (target_nr > 0) {
            for (i = 1; i <= NR; i++) {
                if (i == target_nr) {
                    line = lines[i]
                    eq_pos = index(line, "=")
                    before_eq = substr(line, 1, eq_pos)

                    comment = ""
                    rest = substr(line, eq_pos + 1)
                    if (match(rest, /#[^\x27\x22]*$/)) {
                        comment = "  " substr(rest, RSTART)
                    }
                    print before_eq " " ENVIRON["NEW_VALUE"] comment
                } else {
                    print lines[i]
                }
            }
        } else {
            for (i = 1; i <= NR; i++) {
                print lines[i]
            }
            print ENVIRON["TARGET_KEY"] " = " ENVIRON["NEW_VALUE"]
        }
        exit 0
    }
    ' "$WRITE_TARGET" > "$_TMPFILE" || {
        rm -f -- "$_TMPFILE" 2>/dev/null || :
        set_status "Save failed: ${key}"
        return 1
    }

    if [[ ! -s "$_TMPFILE" ]]; then
        rm -f -- "$_TMPFILE" 2>/dev/null || :
        set_status "Refusing empty write."
        return 1
    fi

    commit_tmpfile || {
        rm -f -- "$_TMPFILE" 2>/dev/null || :
        set_status "Atomic save failed."
        return 1
    }

    CONFIG_CACHE["$cache_key"]="$new_val"
    LAST_WRITE_CHANGED=1
    return 0
}

sync_voice_spec() {
    local b="${CONFIG_CACHE["blend"]:-true}"
    local v1="${CONFIG_CACHE["voice_1"]:-\"af_heart\"}"
    local w1="${CONFIG_CACHE["weight_1"]:-0.4}"
    local v2="${CONFIG_CACHE["voice_2"]:-\"af_bella\"}"
    local w2="${CONFIG_CACHE["weight_2"]:-0.6}"
    local v3="${CONFIG_CACHE["voice_3"]:-\"none\"}"
    local w3="${CONFIG_CACHE["weight_3"]:-0.0}"

    v1="${v1//\"/}"; v2="${v2//\"/}"; v3="${v3//\"/}"
    local new_spec
    if [[ "${b,,}" == "false" || "$v2" == "none" || "$w2" == "0" || "$w2" == "0.0" || "$w2" == "0.00" ]]; then
        new_spec="\"${v1}\""
    elif [[ "$v3" != "none" && "$w3" != "0" && "$w3" != "0.0" && "$w3" != "0.00" ]]; then
        local -a norm
        mapfile -t norm < <(LC_ALL=C awk -v w1="$w1" -v w2="$w2" -v w3="$w3" 'BEGIN {
            s = w1 + w2 + w3
            if (s <= 0) s = 1.0
            n1 = sprintf("%.2f", w1 / s)
            n2 = sprintf("%.2f", w2 / s)
            n3 = sprintf("%.2f", 1.0 - n1 - n2)
            if (n3 < 0) n3 = "0.00"
            print n1; print n2; print n3
        }')
        new_spec="\"${v1}:${norm[0]},${v2}:${norm[1]},${v3}:${norm[2]}\""
    else
        local -a norm
        mapfile -t norm < <(LC_ALL=C awk -v w1="$w1" -v w2="$w2" 'BEGIN {
            s = w1 + w2
            if (s <= 0) s = 1.0
            n1 = sprintf("%.2f", w1 / s)
            n2 = sprintf("%.2f", 1.0 - n1)
            print n1; print n2
        }')
        new_spec="\"${v1}:${norm[0]},${v2}:${norm[1]}\""
    fi
    write_value_to_file "spec" "$new_spec"
}

get_active_context() {
    if (( CURRENT_VIEW == 0 )); then
        REPLY_CTX="${CURRENT_TAB}"
        REPLY_REF="TAB_ITEMS_${CURRENT_TAB}"
    else
        REPLY_CTX="${CURRENT_MENU_ID}"
        REPLY_REF="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
    fi
}

load_active_values() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _lav_items_ref="$REPLY_REF"
    local item key type cache_key

    for item in "${_lav_items_ref[@]}"; do
        IFS='|' read -r key type _ _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${item}"]}"
        cache_key="${key}"
        if [[ -n "${CONFIG_CACHE["$cache_key"]+_}" ]]; then
            VALUE_CACHE["${REPLY_CTX}::${item}"]="${CONFIG_CACHE["$cache_key"]}"
        elif [[ -n "${RAW_DEFAULTS["${REPLY_CTX}::${item}"]+_}" ]]; then
            VALUE_CACHE["${REPLY_CTX}::${item}"]="${RAW_DEFAULTS["${REPLY_CTX}::${item}"]}"
        else
            VALUE_CACHE["${REPLY_CTX}::${item}"]="$UNSET_MARKER"
        fi
    done
}

set_absolute_value() {
    local label="$1" new_val="$2"
    local REPLY_REF REPLY_CTX
    get_active_context
    local key type
    IFS='|' read -r key type _ _ _ _ <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    if write_value_to_file "$key" "$new_val"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
        return 0
    fi
    return 1
}

reset_defaults() {
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _rd_items_ref="$REPLY_REF"
    local item def_val
    local -i any_failed=0

    for item in "${_rd_items_ref[@]}"; do
        def_val="${DEFAULTS["${REPLY_CTX}::${item}"]:-}"
        if [[ -n "$def_val" && "$def_val" != "$UNSET_MARKER" ]]; then
            if ! set_absolute_value "$item" "$def_val"; then
                any_failed=1
            fi
        fi
    done

    if (( any_failed )); then
        set_status "Some defaults were not written."
    else
        clear_status
    fi
    return 0
}

modify_value() {
    local label="$1"
    local -i direction=$2
    local REPLY_REF REPLY_CTX
    get_active_context

    local key type block min max step current new_val
    IFS='|' read -r key type block min max step <<< "${ITEM_MAP["${REPLY_CTX}::${label}"]}"
    current="${VALUE_CACHE["${REPLY_CTX}::${label}"]:-}"

    if [[ "$current" == "$UNSET_MARKER" || -z "$current" ]]; then
        current="${RAW_DEFAULTS["${REPLY_CTX}::${label}"]:-}"
        [[ -z "$current" ]] && current="${min:-0}"
    fi

    case "$type" in
        string)
            local read_status=0
            printf '%s%s' "$CURSOR_SHOW" "$MOUSE_OFF"
            restore_terminal_mode
            printf '\033[%d;0H\033[K' "$TERM_ROWS"
            printf "${C_CYAN}Edit %s:${C_RESET} " "$label"
            IFS= read -re -i "$current" new_val || read_status=$?
            enable_raw_mode
            printf '%s%s' "$CURSOR_HIDE" "$MOUSE_ON"
            (( read_status == 0 )) || return 0
            if [[ -z "$new_val" ]]; then return 0; fi
            ;;
        int)
            local -i int_val=0

            if [[ "$current" =~ ^-?[0-9]+$ ]]; then
                local _stripped="${current#-}"
                [[ -n "$_stripped" ]] && int_val=$(( 10#$_stripped ))
                [[ "$current" == -* ]] && int_val=$(( -int_val ))
            elif [[ -n "$min" && "$min" =~ ^-?[0-9]+$ ]]; then
                local _min_default="${min#-}"
                int_val=$(( 10#$_min_default ))
                [[ "$min" == -* ]] && int_val=$(( -int_val ))
            fi

            local -i int_step=${step:-1}
            int_val=$(( int_val + direction * int_step ))

            if [[ -n "$min" ]]; then
                local -i min_i=$(( 10#${min#-} ))
                [[ "$min" == -* ]] && min_i=$(( -min_i ))
                (( int_val < min_i )) && int_val=$min_i
            fi
            if [[ -n "$max" ]]; then
                local -i max_i=$(( 10#${max#-} ))
                [[ "$max" == -* ]] && max_i=$(( -max_i ))
                (( int_val > max_i )) && int_val=$max_i
            fi
            new_val=$int_val
            ;;
        float)
            if [[ ! "$current" =~ ^-?[0-9]*\.?[0-9]+$ ]]; then current="${min:-0.0}"; fi
            new_val=$(LC_ALL=C awk -v c="$current" -v dir="$direction" -v s="${step:-0.05}" \
                          -v mn="$min" -v mx="$max" 'BEGIN {
                val = c + (dir * s)
                if (mn != "" && val < mn+0) val = mn+0
                if (mx != "" && val > mx+0) val = mx+0
                if (val == 0) val = 0
                str = sprintf("%.2f", val)
                if (str == "-0.00" || str == "-0.0") str = "0.0"
                print str
            }')
            ;;
        bool)
            if [[ "${current,,}" == "true" ]]; then new_val="false"; else new_val="true"; fi
            ;;
        cycle)
            local -a opts
            IFS=',' read -r -a opts <<< "$min"
            local -i count=${#opts[@]} idx=0 i
            (( count == 0 )) && return 0
            for (( i = 0; i < count; i++ )); do
                if [[ "${opts[i]}" == "$current" ]]; then idx=$i; break; fi
            done
            idx=$(( (idx + direction + count) % count ))
            new_val="${opts[idx]}"
            ;;
        menu) return 0 ;;
        *) return 0 ;;
    esac

    if write_value_to_file "$key" "$new_val"; then
        VALUE_CACHE["${REPLY_CTX}::${label}"]="$new_val"
        case "$key" in
            blend|voice_1|weight_1|voice_2|weight_2|voice_3|weight_3)
                sync_voice_spec
                ;;
        esac
        clear_status
        if (( LAST_WRITE_CHANGED )); then
            post_write_action
        fi
    fi
}

# --- UI Rendering Engine ---

compute_scroll_window() {
    local -i count=$1
    if (( count == 0 )); then
        SELECTED_ROW=0
        SCROLL_OFFSET=0
        _vis_start=0
        _vis_end=0
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

render_item_list() {
    local -n _ril_buf=$1
    local -n _ril_items=$2
    local _ril_ctx="$3"
    local -i _ril_vs=$4 _ril_ve=$5

    local -i ri
    local item val display type config padded_item

    for (( ri = _ril_vs; ri < _ril_ve; ri++ )); do
        item="${_ril_items[ri]}"
        val="${VALUE_CACHE["${_ril_ctx}::${item}"]:-${UNSET_MARKER}}"
        config="${ITEM_MAP["${_ril_ctx}::${item}"]}"
        IFS='|' read -r _ type _ _ _ _ <<< "$config"

        local clean_val="${val//\"/}"

        case "$type" in
            menu)
                display="${C_YELLOW}[+] Open Menu ...${C_RESET}"
                ;;
            string)
                if (( ${#val} > 25 )); then
                    display="${C_WHITE}${val:0:22}...${C_RESET}"
                else
                    display="${C_WHITE}${val}${C_RESET}"
                fi
                ;;
            *)
                case "${val,,}" in
                    true)            display="${C_GREEN}ON${C_RESET}" ;;
                    false)           display="${C_RED}OFF${C_RESET}" ;;
                    "$UNSET_MARKER") display="${C_YELLOW}⚠ UNSET${C_RESET}" ;;
                    *)               display="${C_WHITE}${clean_val}${C_RESET}" ;;
                esac
                ;;
        esac

        local -i max_len=$(( ITEM_PADDING - 1 ))
        if (( ${#item} > ITEM_PADDING )); then
            printf -v padded_item "%-${max_len}s…" "${item:0:max_len}"
        else
            printf -v padded_item "%-${ITEM_PADDING}s" "$item"
        fi

        if (( ri == SELECTED_ROW )); then
            _ril_buf+="${C_CYAN} ➤ ${C_INVERSE}${padded_item}${C_RESET} : ${display}${CLR_EOL}"$'\n'
        else
            _ril_buf+="    ${padded_item} : ${display}${CLR_EOL}"$'\n'
        fi
    done

    local -i rows_rendered=$(( _ril_ve - _ril_vs ))
    for (( ri = rows_rendered; ri < MAX_DISPLAY_ROWS; ri++ )); do
        _ril_buf+="${CLR_EOL}"$'\n'
    done
}

draw_main_view() {
    local buf="" pad_buf=""
    local -i i current_col=3 zone_start count
    local -i left_pad right_pad vis_len _vis_start _vis_end

    check_daemon_status

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

    if (( TAB_SCROLL_START > CURRENT_TAB )); then TAB_SCROLL_START=$CURRENT_TAB; fi
    if (( TAB_SCROLL_START < 0 )); then TAB_SCROLL_START=0; fi

    local tab_line
    local -i max_tab_width=$(( BOX_INNER_WIDTH - 6 ))

    LEFT_ARROW_ZONE=""
    RIGHT_ARROW_ZONE=""

    while true; do
        tab_line="${C_MAGENTA}│ "
        current_col=3
        TAB_ZONES=()
        local -i used_len=0

        if (( TAB_SCROLL_START > 0 )); then
            tab_line+="${C_YELLOW}«${C_RESET} "
            LEFT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        else
            tab_line+="  "
            used_len=$(( used_len + 2 ))
            current_col=$(( current_col + 2 ))
        fi

        for (( i = TAB_SCROLL_START; i < TAB_COUNT; i++ )); do
            local name="${TABS[i]}"
            local display_name="$name"
            local -i tab_name_len=${#name}
            local -i chunk_len=$(( tab_name_len + 4 ))
            local -i reserve=0

            if (( i < TAB_COUNT - 1 )); then reserve=2; fi

            if (( used_len + chunk_len + reserve > max_tab_width )); then
                if (( i < CURRENT_TAB || (i == CURRENT_TAB && TAB_SCROLL_START < CURRENT_TAB) )); then
                    TAB_SCROLL_START=$(( TAB_SCROLL_START + 1 ))
                    continue 2
                fi

                if (( i == CURRENT_TAB )); then
                    local -i avail_label=$(( max_tab_width - used_len - reserve - 4 ))
                    if (( avail_label < 1 )); then avail_label=1; fi
                    if (( tab_name_len > avail_label )); then
                        if (( avail_label == 1 )); then display_name="…"; else display_name="${name:0:avail_label-1}…"; fi
                        tab_name_len=${#display_name}
                        chunk_len=$(( tab_name_len + 4 ))
                    fi

                    zone_start=$current_col
                    tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
                    TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
                    used_len=$(( used_len + chunk_len ))
                    current_col=$(( current_col + chunk_len ))

                    if (( i < TAB_COUNT - 1 )); then
                        tab_line+="${C_YELLOW}» ${C_RESET}"
                        RIGHT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
                        used_len=$(( used_len + 2 ))
                    fi
                    break
                fi

                tab_line+="${C_YELLOW}» ${C_RESET}"
                RIGHT_ARROW_ZONE="$current_col:$(( current_col + 1 ))"
                used_len=$(( used_len + 2 ))
                break
            fi

            zone_start=$current_col
            if (( i == CURRENT_TAB )); then
                tab_line+="${C_CYAN}${C_INVERSE} ${display_name} ${C_RESET}${C_MAGENTA}│ "
            else
                tab_line+="${C_GREY} ${display_name} ${C_MAGENTA}│ "
            fi

            TAB_ZONES+=("${zone_start}:$(( zone_start + tab_name_len + 1 ))")
            used_len=$(( used_len + chunk_len ))
            current_col=$(( current_col + chunk_len ))
        done

        local -i pad=$(( BOX_INNER_WIDTH - used_len - 1 ))
        if (( pad > 0 )); then
            printf -v pad_buf '%*s' "$pad" ''
            tab_line+="$pad_buf"
        fi

        tab_line+="${C_MAGENTA}│${C_RESET}"
        break
    done

    buf+="${tab_line}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    local items_var="TAB_ITEMS_${CURRENT_TAB}"
    local -n _draw_items_ref="$items_var"
    count=${#_draw_items_ref[@]}

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"
    render_item_list buf _draw_items_ref "${CURRENT_TAB}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf "below" "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Tab] Category  [←/→] Adjust  [Enter] Edit/Menu  [r] Reset  [s] Toggle  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n "$STATUS_MESSAGE" ]]; then
        buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"
    else
        buf+="${C_CYAN} Daemon: ${DAEMON_STATUS_UI}  ${C_CYAN}File: ${C_WHITE}${CONFIG_FILE##*/}${C_RESET}${CLR_EOL}${CLR_EOS}"
    fi
    printf '%s' "$buf"
}

draw_detail_view() {
    local buf="" pad_buf=""
    local -i count pad_needed
    local -i left_pad right_pad vis_len _vis_start _vis_end

    check_daemon_status

    buf+="${CURSOR_HOME}"
    buf+="${C_MAGENTA}┌${H_LINE}┐${C_RESET}${CLR_EOL}"$'\n'

    local title=" DETAIL VIEW "
    local sub=" ${CURRENT_MENU_ID} "
    strip_ansi "$title"; local -i t_len=${#REPLY}
    strip_ansi "$sub"; local -i s_len=${#REPLY}
    vis_len=$(( t_len + s_len ))
    left_pad=$(( (BOX_INNER_WIDTH - vis_len) / 2 ))
    right_pad=$(( BOX_INNER_WIDTH - vis_len - left_pad ))

    printf -v pad_buf '%*s' "$left_pad" ''
    buf+="${C_MAGENTA}│${pad_buf}${C_YELLOW}${title}${C_GREY}${sub}${C_MAGENTA}"
    printf -v pad_buf '%*s' "$right_pad" ''
    buf+="${pad_buf}│${C_RESET}${CLR_EOL}"$'\n'

    local breadcrumb=" « Back to ${TABS[CURRENT_TAB]}"
    strip_ansi "$breadcrumb"; local -i b_len=${#REPLY}
    pad_needed=$(( BOX_INNER_WIDTH - b_len ))
    if (( pad_needed < 0 )); then pad_needed=0; fi

    printf -v pad_buf '%*s' "$pad_needed" ''
    buf+="${C_MAGENTA}│${C_CYAN}${breadcrumb}${C_RESET}${pad_buf}${C_MAGENTA}│${C_RESET}${CLR_EOL}"$'\n'
    buf+="${C_MAGENTA}└${H_LINE}┘${C_RESET}${CLR_EOL}"$'\n'

    local items_var="SUBMENU_ITEMS_${CURRENT_MENU_ID}"
    local -n _detail_items_ref="$items_var"
    count=${#_detail_items_ref[@]}

    compute_scroll_window "$count"
    render_scroll_indicator buf "above" "$count" "$_vis_start"
    render_item_list buf _detail_items_ref "${CURRENT_MENU_ID}" "$_vis_start" "$_vis_end"
    render_scroll_indicator buf "below" "$count" "$_vis_end"

    buf+=$'\n'"${C_CYAN} [Esc] Back  [←/→] Adjust  [Enter] Edit Value  [r] Reset  [s] Toggle  [q] Quit${C_RESET}${CLR_EOL}"$'\n'
    if [[ -n "$STATUS_MESSAGE" ]]; then
        buf+="${C_CYAN} Status: ${C_RED}${STATUS_MESSAGE}${C_RESET}${CLR_EOL}${CLR_EOS}"
    else
        buf+="${C_CYAN} Daemon: ${DAEMON_STATUS_UI}  ${C_CYAN}Submenu: ${C_WHITE}${CURRENT_MENU_ID}${C_RESET}${CLR_EOL}${CLR_EOS}"
    fi
    printf '%s' "$buf"
}

draw_ui() {
    update_terminal_size
    if ! terminal_size_ok; then
        draw_small_terminal_notice
        return
    fi
    case $CURRENT_VIEW in
        0) draw_main_view ;;
        1) draw_detail_view ;;
    esac
}

# --- Input Handling ---

navigate() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _nav_items_ref="$REPLY_REF"
    local -i count=${#_nav_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( (SELECTED_ROW + dir + count) % count ))
}

navigate_page() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _navp_items_ref="$REPLY_REF"
    local -i count=${#_navp_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    SELECTED_ROW=$(( SELECTED_ROW + dir * MAX_DISPLAY_ROWS ))
    if (( SELECTED_ROW < 0 )); then SELECTED_ROW=0; fi
    if (( SELECTED_ROW >= count )); then SELECTED_ROW=$(( count - 1 )); fi
}

navigate_end() {
    local -i target=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _nave_items_ref="$REPLY_REF"
    local -i count=${#_nave_items_ref[@]}
    if (( count == 0 )); then return 0; fi
    if (( target == 0 )); then SELECTED_ROW=0; else SELECTED_ROW=$(( count - 1 )); fi
}

adjust() {
    local -i dir=$1
    local REPLY_REF REPLY_CTX
    get_active_context
    local -n _adj_items_ref="$REPLY_REF"
    if (( ${#_adj_items_ref[@]} == 0 )); then return 0; fi
    modify_value "${_adj_items_ref[SELECTED_ROW]}" "$dir"
}

switch_tab() {
    local -i dir=${1:-1}
    CURRENT_TAB=$(( (CURRENT_TAB + dir + TAB_COUNT) % TAB_COUNT ))
    SELECTED_ROW=0
    SCROLL_OFFSET=0
    load_active_values
}

set_tab() {
    local -i idx=$1
    if (( idx != CURRENT_TAB && idx >= 0 && idx < TAB_COUNT )); then
        CURRENT_TAB=$idx
        SELECTED_ROW=0
        SCROLL_OFFSET=0
        load_active_values
    fi
}

check_drilldown() {
    local -n _dd_items_ref="TAB_ITEMS_${CURRENT_TAB}"
    if (( ${#_dd_items_ref[@]} == 0 )); then return 1; fi

    local item="${_dd_items_ref[SELECTED_ROW]}"
    local config="${ITEM_MAP["${CURRENT_TAB}::${item}"]}"
    local key type
    IFS='|' read -r key type _ _ _ _ <<< "$config"

    if [[ "$type" == "menu" ]]; then
        PARENT_ROW=$SELECTED_ROW
        PARENT_SCROLL=$SCROLL_OFFSET
        CURRENT_MENU_ID="$key"
        CURRENT_VIEW=1
        SELECTED_ROW=0
        SCROLL_OFFSET=0
        load_active_values
        return 0
    fi
    return 1
}

go_back() {
    CURRENT_VIEW=0
    SELECTED_ROW=$PARENT_ROW
    SCROLL_OFFSET=$PARENT_SCROLL
    load_active_values
}

handle_mouse() {
    local input="$1"
    local -i button x y i start end
    local zone

    local body="${input#'[<'}"
    if [[ "$body" == "$input" ]]; then return 0; fi

    local terminator="${body: -1}"
    if [[ "$terminator" != "M" && "$terminator" != "m" ]]; then return 0; fi

    body="${body%[Mm]}"
    local field1 field2 field3
    IFS=';' read -r field1 field2 field3 <<< "$body"
    if [[ ! "$field1" =~ ^[0-9]+$ ]] || [[ ! "$field2" =~ ^[0-9]+$ ]] || [[ ! "$field3" =~ ^[0-9]+$ ]]; then return 0; fi

    button=$field1
    x=$field2
    y=$field3

    if (( button == 64 )); then navigate -1; return 0; fi
    if (( button == 65 )); then navigate 1; return 0; fi
    if [[ "$terminator" != "M" ]]; then return 0; fi

    if (( y == TAB_ROW )); then
        if (( CURRENT_VIEW == 0 )); then
            if [[ -n "$LEFT_ARROW_ZONE" ]]; then
                start="${LEFT_ARROW_ZONE%%:*}"
                end="${LEFT_ARROW_ZONE##*:}"
                if (( x >= start && x <= end )); then switch_tab -1; return 0; fi
            fi
            if [[ -n "$RIGHT_ARROW_ZONE" ]]; then
                start="${RIGHT_ARROW_ZONE%%:*}"
                end="${RIGHT_ARROW_ZONE##*:}"
                if (( x >= start && x <= end )); then switch_tab 1; return 0; fi
            fi
            for (( i = 0; i < TAB_COUNT; i++ )); do
                if [[ -z "${TAB_ZONES[i]:-}" ]]; then continue; fi
                zone="${TAB_ZONES[i]}"
                start="${zone%%:*}"
                end="${zone##*:}"
                if (( x >= start && x <= end )); then set_tab "$(( i + TAB_SCROLL_START ))"; return 0; fi
            done
        else
            go_back
            return 0
        fi
    fi

    local -i effective_start=$(( ITEM_START_ROW + 1 ))
    if (( y >= effective_start && y < effective_start + MAX_DISPLAY_ROWS )); then
        local -i clicked_idx=$(( y - effective_start + SCROLL_OFFSET ))

        local _target_var_name
        if (( CURRENT_VIEW == 0 )); then _target_var_name="TAB_ITEMS_${CURRENT_TAB}"; else _target_var_name="SUBMENU_ITEMS_${CURRENT_MENU_ID}"; fi
        local -n _mouse_items_ref="$_target_var_name"
        local -i count=${#_mouse_items_ref[@]}

        if (( clicked_idx >= 0 && clicked_idx < count )); then
            SELECTED_ROW=$clicked_idx
            if (( x > ADJUST_THRESHOLD )); then
                if (( button == 0 )); then
                    if (( CURRENT_VIEW == 0 )); then check_drilldown || adjust 1; else adjust 1; fi
                else
                    adjust -1
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

    if ! IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; then return 1; fi

    _esc_out+="$char"
    if [[ "$char" == '[' || "$char" == 'O' ]]; then
        while IFS= read -rsn1 -t "$ESC_READ_TIMEOUT" char; do
            _esc_out+="$char"
            if [[ "$char" =~ [a-zA-Z~] ]]; then break; fi
        done
    fi
    return 0
}

handle_key_main() {
    local key="$1"

    case "$key" in
        '[Z')                switch_tab -1; return ;;
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[C'|'OC')           adjust 1; return ;;
        '[D'|'OD')           adjust -1; return ;;
        '[5~')               navigate_page -1; return ;;
        '[6~')               navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac

    case "$key" in
        k|K)                         navigate -1 ;;
        j|J)                         navigate 1 ;;
        l|L)                         adjust 1 ;;
        h|H)                         adjust -1 ;;
        g)                           navigate_end 0 ;;
        G)                           navigate_end 1 ;;
        1)                           set_tab 0 ;;
        2)                           set_tab 1 ;;
        3)                           set_tab 2 ;;
        4)                           set_tab 3 ;;
        $'\t')                       switch_tab 1 ;;
        s|S)                         toggle_daemon ;;
        r|R)                         reset_defaults ;;
        ''|$'\n')                    check_drilldown || adjust 1 ;;
        $'\x7f'|$'\x08'|$'\e\n')     adjust -1 ;;
        q|Q|$'\x03')                 exit 0 ;;
    esac
}

handle_key_detail() {
    local key="$1"

    case "$key" in
        '[A'|'OA')           navigate -1; return ;;
        '[B'|'OB')           navigate 1; return ;;
        '[C'|'OC')           adjust 1; return ;;
        '[D'|'OD')           adjust -1; return ;;
        '[5~')               navigate_page -1; return ;;
        '[6~')               navigate_page 1; return ;;
        '[H'|'[1~')          navigate_end 0; return ;;
        '[F'|'[4~')          navigate_end 1; return ;;
        '[Z')                go_back; return ;;
        '['*'<'*[Mm])        handle_mouse "$key"; return ;;
    esac

    case "$key" in
        ESC)                         go_back ;;
        k|K)                         navigate -1 ;;
        j|J)                         navigate 1 ;;
        l|L)                         adjust 1 ;;
        h|H)                         adjust -1 ;;
        g)                           navigate_end 0 ;;
        G)                           navigate_end 1 ;;
        s|S)                         toggle_daemon ;;
        r|R)                         reset_defaults ;;
        ''|$'\n')                    adjust 1 ;;
        $'\x7f'|$'\x08'|$'\e\n')     adjust -1 ;;
        q|Q|$'\x03')                 exit 0 ;;
    esac
}

handle_input_router() {
    local key="$1"
    local escape_seq=""

    if [[ "$key" == $'\x1b' ]]; then
        if read_escape_seq escape_seq; then
            key="$escape_seq"
            if [[ "$key" == "" || "$key" == $'\n' ]]; then key=$'\e\n'; fi
        else
            key="ESC"
        fi
    fi

    if ! terminal_size_ok; then
        case "$key" in q|Q|$'\x03') exit 0 ;; esac
        return 0
    fi

    case $CURRENT_VIEW in
        0) handle_key_main "$key" ;;
        1) handle_key_detail "$key" ;;
    esac
}

main() {
    if (( BASH_VERSINFO[0] < 5 )); then log_err "Bash 5.0+ required"; exit 1; fi
    if [[ ! -t 0 ]]; then log_err "TTY required"; exit 1; fi
    local _dep
    for _dep in awk realpath; do
        if ! command -v "$_dep" &>/dev/null; then
            log_err "Missing dependency: ${_dep}"
            exit 1
        fi
    done

    resolve_write_target

    if [[ ! -w "$WRITE_TARGET" ]]; then log_err "Config not writable: $CONFIG_FILE"; exit 1; fi

    register_items
    populate_config_cache

    ORIGINAL_STTY=$(stty -g 2>/dev/null) || ORIGINAL_STTY=""
    enable_raw_mode

    printf '%s%s%s%s' "$MOUSE_ON" "$CURSOR_HIDE" "$CLR_SCREEN" "$CURSOR_HOME"
    load_active_values

    # Stateful Backup: Record the initial file state for the 'r' key resets
    for k in "${!VALUE_CACHE[@]}"; do
        DEFAULTS["$k"]="${VALUE_CACHE[$k]}"
    done

    trap 'draw_ui' WINCH

    local key
    while true; do
        draw_ui
        if ! IFS= read -rsn1 key; then continue; fi
        handle_input_router "$key"
    done
}

main "$@"

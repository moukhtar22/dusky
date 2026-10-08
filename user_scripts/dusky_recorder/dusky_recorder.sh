#!/usr/bin/env bash
# Rofi capture controls and INI configuration for gpu-screen-recorder on Wayland.
set -euo pipefail

readonly CFG="${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings/dusky_recorder/config.conf"
readonly ROFI_THEME_STR='window { padding: 20px 12px; border: 2px; } mainbox { padding: 0; border: 0; } inputbar { spacing: 1ch; padding: 12px; children: [prompt, entry]; } entry { placeholder: "Search..."; } element { padding: 8px 12px; children: [element-text]; } listview { lines: 8; scrollbar: false; padding: 0; border: 0; }'
readonly RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID}"
readonly RECORDER_STATE_DIR="$RUNTIME_DIR/dusky-recorder"
readonly RECORDER_PID_FILE="$RECORDER_STATE_DIR/recorder.pid"
readonly RECORDER_LOG="$RECORDER_STATE_DIR/recorder.log"
readonly INDICATOR_PID_FILE="$RECORDER_STATE_DIR/indicator.pid"
readonly INDICATOR_ID_FILE="$RECORDER_STATE_DIR/indicator.id"

# This is data, not shell code. Match the TUI's DEFAULT scope and literal values.
load_config() {
    local line key value scope=DEFAULT
    local assignment='^[[:blank:]]*([a-zA-Z_][a-zA-Z0-9_]*)[[:blank:]]*=[[:blank:]]*(.*)$'
    local section='^[[:blank:]]*\[[[:blank:]]*([^]]+)[[:blank:]]*\][[:blank:]]*$'
    [[ -f "$CFG" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=${line%$'\r'}
        if [[ "$line" =~ $section ]]; then
            scope=${BASH_REMATCH[1]}
            scope=${scope%%+([[:blank:]])}
        elif [[ "$scope" == DEFAULT && "$line" =~ $assignment ]]; then
            key=${BASH_REMATCH[1]}
            value=${BASH_REMATCH[2]}
            value=${value%%+([[:blank:]])}
            if [[ "$value" == \"*\" && ${#value} -ge 2 ]]; then
                value=${value:1:-1}
            fi
            case "$key" in
                window|region|fps|cursor|show_indicator|encoder|codec|quality|bitrate_mode|frame_mode|color_range|container|output_dir|audio_codec|audio_bitrate|audio_output|audio_input|replay_buffer|replay_storage|restart_replay|tune|low_power|date_folders)
                    printf -v "$key" '%s' "$value" ;;
            esac
        fi
    done < "$CFG"
}
shopt -s extglob
if [[ ! -f "$CFG" ]]; then
    python3 "$(dirname -- "${BASH_SOURCE[0]}")/recorder_config.py"
fi
load_config

window=${window:-screen}
region=${region:-}
fps=${fps:-60}
cursor=${cursor:-yes}
show_indicator=${show_indicator:-yes}
encoder=${encoder:-gpu}
codec=${codec:-auto}
quality=${quality:-very_high}
bitrate_mode=${bitrate_mode:-auto}
frame_mode=${frame_mode:-vfr}
color_range=${color_range:-limited}
container=${container:-mp4}
output_dir=${output_dir:-$HOME/Videos}
# shellcheck disable=SC2088 # Match literal INI tildes before expanding them.
if [[ "$output_dir" == '~' || "$output_dir" == '~/'* ]]; then
    output_dir="$HOME${output_dir:1}"
fi
audio_codec=${audio_codec:-aac}
audio_bitrate=${audio_bitrate:-128}
audio_output=${audio_output:-default_output}
audio_input=${audio_input:-none}
replay_buffer=${replay_buffer:-0}
replay_storage=${replay_storage:-ram}
restart_replay=${restart_replay:-no}
tune=${tune:-performance}
low_power=${low_power:-no}
date_folders=${date_folders:-no}

menu_theme() {
    local label longest=$(( ${#1} + 10 ))
    shift
    # Include the prompt, one-character gap and "Search..." placeholder.
    for label in "$@"; do
        (( ${#label} <= longest )) || longest=${#label}
    done
    # Horizontal space: window padding (24), row padding (24), border (4).
    printf 'window { width: calc(%dch + 52px); } %s\n' "$longest" "$ROFI_THEME_STR"
}

run_menu() {
    local prompt="$1"
    shift
    local theme
    theme=$(menu_theme "$prompt" "$@")
    printf '%s\n' "$@" | rofi -dmenu -no-custom -i -p "$prompt" -theme-str "$theme" -format s
}

# Use the same atomic INI writer as the TUI, preserving comments and sections.
write_config() {
    python3 - "$CFG" "$@" <<'PYCONFIG'
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / "user_scripts" / "dusky_tui"))
from python.engines.ini import IniConfigEngine
engine = IniConfigEngine(sys.argv[1])
engine.load_state()
changes = [
    (key, "DEFAULT", value, "string")
    for key, value in zip(sys.argv[2::2], sys.argv[3::2], strict=True)
]
ok, message, _ = engine.write_batch(changes)
if not ok:
    print(message, file=sys.stderr)
    sys.exit(1)
PYCONFIG
}

update_config() {
    local key value
    if ! with_lock write_config "$@"; then
        status critical 'Could not save recorder settings'
        return 1
    fi
    while (( $# )); do
        key=$1 value=$2
        printf -v "$key" '%s' "$value"
        shift 2
    done
}

list_audio_devices() {
    timeout -k 0.5 1.5 gpu-screen-recorder --list-audio-devices 2>/dev/null || true
}

get_audio_name() {
    local target_id="$1" devices="${2:-}" dev_id dev_name
    case "$target_id" in
        none) printf '%s\n' 'None'; return ;;
        default_output) printf '%s\n' 'Default Desktop'; return ;;
        default_input) printf '%s\n' 'Default Mic'; return ;;
    esac
    (( $# > 1 )) || devices=$(list_audio_devices)
    while IFS='|' read -r dev_id dev_name; do
        if [[ "$dev_id" == "$target_id" ]]; then
            printf '%s\n' "${dev_name:-$dev_id}"
            return 0
        fi
    done <<< "$devices"
    printf '%s\n' 'Disconnected Device'
}

status() {
    local urgency="$1" message="$2"
    printf '%s\n' "$message" >&2
    timeout -k 0.5 2 notify-send -a dusky-recorder-status \
        -h string:x-canonical-private-synchronous:dusky-recorder-status \
        -u "$urgency" 'Dusky Recorder' "$message" 2>/dev/null || true
}

process_start() {
    local pid="$1" raw
    local -a fields
    [[ "$pid" =~ ^[1-9][0-9]*$ && ${#pid} -le 10 ]] || return 1
    (( pid > 1 )) || return 1
    { IFS= read -r raw < "/proc/$pid/stat"; } 2>/dev/null || return 1
    read -r -a fields <<< "${raw##*) }"
    (( ${#fields[@]} >= 20 )) || return 1
    [[ "${fields[0]}" != Z && "${fields[0]}" != X ]] || return 1
    printf '%s\n' "${fields[19]}"
}

same_process() {
    local actual_start
    [[ "$2" =~ ^[0-9]+$ ]] || return 1
    actual_start=$(process_start "$1") || return 1
    [[ "$actual_start" == "$2" ]]
}

recorder_running() {
    recorder_pid='' recorder_start='' recorder_mode=''
    [[ -f "$RECORDER_PID_FILE" ]] || return 1
    read -r recorder_pid recorder_start recorder_mode < "$RECORDER_PID_FILE" || return 1
    same_process "$recorder_pid" "$recorder_start"
}

# Each operation owns this lock only for its duration; daemons close the descriptor.
cleanup_start() {
    [[ -n "$starting_pid" ]] || return 0
    stop_process "$starting_pid" "$starting_start" || true
    manage_indicator stop
    rm -f "$RECORDER_PID_FILE"
}

with_lock() (
    local starting_pid='' starting_start=''
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 129' HUP
    trap cleanup_start EXIT
    mkdir -p "$RECORDER_STATE_DIR" || return 1
    exec 9> "$RECORDER_STATE_DIR/control.lock" || return 1
    if ! flock -w 20 9; then
        status critical 'Another recorder action is still in progress'
        return 1
    fi
    "$@"
)

dismiss_indicator() {
    local id=''
    if [[ -f "$INDICATOR_ID_FILE" ]]; then
        id=$(<"$INDICATOR_ID_FILE")
        if [[ "$id" =~ ^[1-9][0-9]{0,9}$ ]]; then
            busctl --user --timeout=1 call org.freedesktop.Notifications \
                /org/freedesktop/Notifications org.freedesktop.Notifications \
                CloseNotification u "$id" >/dev/null 2>&1 || true
        fi
        rm -f "$INDICATOR_ID_FILE"
    fi
}

cleanup_indicator() {
    local latest_id="$1" sleeper="$2"
    if [[ "$latest_id" =~ ^[1-9][0-9]{0,9}$ ]]; then
        printf '%s\n' "$latest_id" > "$INDICATOR_ID_FILE"
    fi
    [[ -z "$sleeper" ]] || kill "$sleeper" 2>/dev/null || true
    dismiss_indicator
    rm -f "$INDICATOR_PID_FILE"
}

manage_indicator() {
    local action="$1" pid='' start='' attempt
    if [[ -f "$INDICATOR_PID_FILE" ]]; then
        read -r pid start < "$INDICATOR_PID_FILE" || true
        if same_process "$pid" "$start"; then
            kill -TERM "$pid" 2>/dev/null || true
            # A notification may take 2.5 seconds, followed by a 1-second close.
            for ((attempt=0; attempt<80; ++attempt)); do
                same_process "$pid" "$start" || break
                sleep 0.05
            done
            if same_process "$pid" "$start"; then
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$INDICATOR_PID_FILE"
    fi
    dismiss_indicator
    [[ "$action" == start && "$show_indicator" == yes ]] || return 0

    (
        exec 9>&-
        trap 'exit 0' TERM INT HUP
        local id=0 next_id='' symbol='' sleeper=''
        trap 'cleanup_indicator "$next_id" "$sleeper"' EXIT
        while same_process "$recorder_pid" "$recorder_start"; do
            next_id=$(timeout -k 0.5 2 notify-send -p -r "$id" -a dusky-recorder \
                -t 0 "$symbol" '' 2>/dev/null) || break
            [[ "$next_id" =~ ^[1-9][0-9]{0,9}$ ]] || break
            id=$next_id
            printf '%s\n' "$id" > "$INDICATOR_ID_FILE"
            [[ "$symbol" == '' ]] && symbol=' ' || symbol=''
            sleep 1 &
            sleeper=$!
            wait "$sleeper" || true
            sleeper=''
        done
    ) </dev/null >/dev/null 2>&1 &
    pid=$!
    start=$(process_start "$pid") || return 0
    printf '%s %s\n' "$pid" "$start" > "$INDICATOR_PID_FILE"
    same_process "$pid" "$start" || rm -f "$INDICATOR_PID_FILE"
}

# Return failure when graceful finalization required escalation.
stop_process() {
    local pid="$1" start="$2" attempt
    same_process "$pid" "$start" || return 0
    kill -INT "$pid" 2>/dev/null || true
    for ((attempt=0; attempt<100; ++attempt)); do
        same_process "$pid" "$start" || return 0
        sleep 0.1
    done
    same_process "$pid" "$start" || return 0
    kill -TERM "$pid" 2>/dev/null || true
    for ((attempt=0; attempt<20; ++attempt)); do
        same_process "$pid" "$start" || return 1
        sleep 0.1
    done
    if same_process "$pid" "$start"; then
        kill -KILL "$pid" 2>/dev/null || true
    fi
    return 1
}

stop_recording() {
    local forced=false
    if recorder_running; then
        stop_process "$recorder_pid" "$recorder_start" || forced=true
        if $forced; then
            status critical "Recorder required forced shutdown; check $RECORDER_LOG and the output file"
        else
            status normal 'Recording stopped'
        fi
    fi
    manage_indicator stop
    rm -f "$RECORDER_PID_FILE"
    ! $forced
}

save_replay() {
    if recorder_running && [[ "$recorder_mode" == replay ]]; then
        if kill -USR1 "$recorder_pid" 2>/dev/null; then
            status normal 'Replay save requested'
            return 0
        fi
    fi
    status critical 'No active replay buffer to save'
    return 1
}

# Validate the values the backend cannot interpret safely or unambiguously.
validate_config() {
    local key value
    for key in fps audio_bitrate replay_buffer; do
        value=${!key}
        if [[ ! "$value" =~ ^[0-9]{1,6}$ ]]; then
            status critical "Invalid $key: $value"
            return 1
        fi
        printf -v "$key" '%d' "$((10#$value))"
    done
    if (( fps < 1 || audio_bitrate > 512 || replay_buffer == 1 || replay_buffer > 86400 )); then
        status critical 'FPS must be positive; audio bitrate 0–512; replay duration 0 or 2–86400'
        return 1
    fi
    for key in cursor show_indicator low_power restart_replay date_folders; do
        [[ "${!key}" == yes || "${!key}" == no ]] || { status critical "Invalid $key: ${!key}"; return 1; }
    done
    case "$encoder" in gpu|cpu) ;; *) status critical "Invalid encoder: $encoder"; return 1 ;; esac
    case "$codec" in auto|h264|hevc|av1|vp8|vp9|hevc_hdr|av1_hdr|hevc_10bit|av1_10bit|h264_vulkan|hevc_vulkan|av1_vulkan|hevc_10bit_vulkan|av1_10bit_vulkan|av1_hdr_vulkan) ;; *) status critical "Invalid codec: $codec"; return 1 ;; esac
    case "$bitrate_mode" in auto|qp|vbr|cbr) ;; *) status critical "Invalid bitrate mode: $bitrate_mode"; return 1 ;; esac
    if [[ "$quality" =~ ^[0-9]{1,9}$ ]]; then
        quality=$((10#$quality))
        (( quality > 0 )) || { status critical 'Video bitrate must be positive'; return 1; }
        bitrate_mode=cbr
    else
        case "$quality" in medium|high|very_high|ultra) ;; *) status critical "Invalid quality: $quality"; return 1 ;; esac
        [[ "$bitrate_mode" != cbr ]] || { status critical 'CBR requires a numeric quality in kbps'; return 1; }
    fi
    case "$frame_mode" in vfr|cfr|content) ;; *) status critical "Invalid timing: $frame_mode"; return 1 ;; esac
    case "$color_range" in limited|full) ;; *) status critical "Invalid color range: $color_range"; return 1 ;; esac
    case "$container" in mp4|mkv|flv|webm) ;; *) status critical "Unsupported recording container: $container"; return 1 ;; esac
    case "$audio_codec" in opus|aac) ;; *) status critical "Unsupported audio codec: $audio_codec"; return 1 ;; esac
    case "$replay_storage" in ram|disk) ;; *) status critical "Invalid replay storage: $replay_storage"; return 1 ;; esac
    case "$tune" in performance|quality) ;; *) status critical "Invalid tune: $tune"; return 1 ;; esac
    [[ "$output_dir" == /* ]] || { status critical 'Output directory must be absolute or start with ~/'; return 1; }
    if [[ "$encoder" == cpu && "$codec" != auto && "$codec" != h264 ]]; then
        status critical 'CPU encoding supports only auto or h264'
        return 1
    fi
    if [[ "$container" == webm ]]; then
        if [[ "$encoder" == cpu ]]; then
            status critical 'CPU H.264 encoding cannot produce WebM; use MP4 or MKV'
            return 1
        fi
        case "$codec" in
            auto|vp8|vp9|av1|av1_*) ;;
            *) status critical 'WebM requires VP8, VP9, or AV1 video'; return 1 ;;
        esac
    fi
}

# The backend's WebM auto default is VP8, which many GPUs cannot encode.
webm_codec() {
    local info line section='' candidate
    local -A supported=()
    info=$(timeout -k 0.5 5 gpu-screen-recorder --info 2>/dev/null) || return 1
    while IFS= read -r line; do
        if [[ "$line" == section=* ]]; then
            section=${line#section=}
        elif [[ "$section" == video_codecs ]]; then
            case "$line" in av1|vp9|vp8) supported[$line]=1 ;; esac
        fi
    done <<< "$info"
    for candidate in av1 vp9 vp8; do
        if [[ -n "${supported[$candidate]:-}" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

report_start_failure() {
    local line reason='Recorder exited during startup'
    while IFS= read -r line; do
        if [[ "$line" == *llvmpipe* ]]; then
            reason='GPU Screen Recorder requires GPU-backed OpenGL; this system uses llvmpipe software rendering, which also prevents CPU encoding'
            break
        fi
        if [[ "$reason" == 'Recorder exited during startup' && "$line" == 'gsr error: '* ]]; then
            reason=${line#gsr error: }
        fi
    done < "$RECORDER_LOG"
    status critical "$reason; log: $RECORDER_LOG"
}

start_recording() {
    local target_mode="${1:-$window}" region_coords="$region"
    local capture_source mode=recording out video_codec="$codec"
    # Explicit region actions always draw a fresh selection; --start uses the config.
    [[ "${1:-}" != region ]] || region_coords=''
    if recorder_running; then
        status normal 'Recording is already running'
        return 0
    fi
    validate_config || return 1
    case "$target_mode" in
        screen|portal) ;;
        region)
            if [[ -z "$region_coords" ]]; then
                sleep 0.2
                region_coords=$(slurp -f '%wx%h+%x+%y') || return 1
            fi
            # Slurp emits '+-N' for negative positions; explicit signed offsets work too.
            [[ "$region_coords" =~ ^[0-9]+x[0-9]+(\+-?[0-9]+|-[0-9]+)(\+-?[0-9]+|-[0-9]+)$ ]] || {
                status critical "Invalid region: $region_coords"; return 1;
            }
            ;;
        *) status critical "Invalid capture source: $target_mode"; return 1 ;;
    esac
    capture_source=$target_mode
    [[ "$target_mode" != region ]] || capture_source=$region_coords
    if [[ "$container" == webm && "$codec" == auto ]]; then
        video_codec=$(webm_codec) || {
            status critical 'No available WebM encoder found; select a supported codec or use MP4/MKV'
            return 1
        }
    fi
    manage_indicator stop
    mkdir -p "$output_dir" || return 1
    local -a args=(gpu-screen-recorder -w "$capture_source" -c "$container" -f "$fps"
        -cursor "$cursor" -encoder "$encoder" -q "$quality" -bm "$bitrate_mode"
        -fm "$frame_mode" -cr "$color_range" -tune "$tune" -low-power "$low_power")
    [[ "$video_codec" == auto ]] || args+=(-k "$video_codec")
    local final_audio=''
    [[ "$audio_output" == none ]] || final_audio=$audio_output
    if [[ "$audio_input" != none ]]; then
        final_audio+="${final_audio:+|}$audio_input"
    fi
    [[ -z "$final_audio" ]] || args+=(-a "$final_audio" -ac "$audio_codec" -ab "$audio_bitrate")
    if (( replay_buffer > 0 )); then
        mode=replay
        args+=(-r "$replay_buffer" -replay-storage "$replay_storage"
            -restart-replay-on-save "$restart_replay" -df "$date_folders")
        out=$output_dir
    else
        out="$output_dir/Video_$(date +%Y-%m-%d_%H-%M-%S_%N).$container"
    fi
    args+=(-o "$out")
    (
        exec 9>&-
        exec env --default-signal=INT,TERM,HUP "${args[@]}"
    ) </dev/null >"$RECORDER_LOG" 2>&1 &
    starting_pid=$!
    starting_start=$(process_start "$starting_pid") || starting_start=''
    # Publish ownership before the startup delay, including interrupted launches.
    printf '%s %s %s\n' "$starting_pid" "$starting_start" "$mode" > "$RECORDER_PID_FILE" || return 1
    sleep 0.5
    if ! same_process "$starting_pid" "$starting_start"; then
        wait "$starting_pid" 2>/dev/null || true
        report_start_failure
        rm -f "$RECORDER_PID_FILE"
        return 1
    fi
    recorder_pid=$starting_pid recorder_start=$starting_start recorder_mode=$mode
    status normal "Recorder process started ($mode)"
    manage_indicator start
    starting_pid='' starting_start=''
}

toggle_recording() {
    if recorder_running; then stop_recording; else start_recording; fi
}

# --- SUBMENU: VIDEO & ENCODING ---
video_menu() {
    while true; do
        # Numeric quality values select CBR at launch, regardless of the saved mode.
        local q_disp="$quality"
        [[ "$quality" =~ ^[0-9]+$ ]] && q_disp="${quality} kbps (CBR)"

        local -a opts=(
            "  Back"
            "󰘚  Encoder     [${encoder}]"
            "󰈰  Codec       [${codec}]"
            "󰄬  Quality     [${q_disp}]"
            "󰹑  Frame Mode  [${frame_mode}]"
            "󰃐  Container   [${container}]"
            "󰸱  Color Range [${color_range}]"
        )
        local choice
        choice=$(run_menu "󰕧  Video Settings" "${opts[@]}") || return 0

        case "$choice" in
            "  Back") return 0 ;;
            "󰘚  Encoder"*)
                local new_enc
                new_enc=$(run_menu "Select Encoder" "gpu" "cpu") || continue
                [[ -z "$new_enc" ]] || update_config encoder "$new_enc" || continue
                ;;
            "󰈰  Codec"*)
                local -a codecs=("auto" "h264" "hevc" "av1" "vp8" "vp9" "hevc_10bit" "av1_10bit" "hevc_vulkan" "av1_vulkan")
                local new_codec
                new_codec=$(run_menu "Select Codec" "${codecs[@]}") || continue
                [[ -z "$new_codec" ]] || update_config codec "$new_codec" || continue
                ;;
            "󰄬  Quality"*)
                local -a q_opts=("󰄬  Ultra (Auto)" "󰄬  Very High (Auto)" "󰄬  High (Auto)" "󰄬  Medium (Auto)" "  Custom Bitrate (CBR)...")
                local q_choice
                q_choice=$(run_menu "󰄬  Quality Mode" "${q_opts[@]}") || continue
                case "$q_choice" in
                    "󰄬  Ultra"*) update_config quality ultra bitrate_mode auto || continue ;;
                    "󰄬  Very High"*) update_config quality very_high bitrate_mode auto || continue ;;
                    "󰄬  High"*) update_config quality high bitrate_mode auto || continue ;;
                    "󰄬  Medium"*) update_config quality medium bitrate_mode auto || continue ;;
                    "  Custom"*)
                        local custom_q
                        local bitrate_prompt='Bitrate (kbps, e.g. 40000)' bitrate_theme
                        bitrate_theme=$(menu_theme "$bitrate_prompt")
                        custom_q=$(rofi -dmenu -p "$bitrate_prompt" -theme-str "$bitrate_theme listview { enabled: false; }" < /dev/null) || continue
                        if [[ "$custom_q" =~ ^[1-9][0-9]{0,8}$ ]]; then
                            update_config quality "$custom_q" bitrate_mode cbr || continue
                        else
                            status critical 'Enter a positive video bitrate in kbps'
                        fi
                        ;;
                esac
                ;;
            "󰹑  Frame Mode"*)
                local new_fm
                new_fm=$(run_menu "Select Frame Mode" "vfr" "cfr" "content") || continue
                [[ -z "$new_fm" ]] || update_config frame_mode "$new_fm" || continue
                ;;
            "󰃐  Container"*)
                local new_cont
                new_cont=$(run_menu "Select Container" "mp4" "mkv" "flv" "webm") || continue
                [[ -z "$new_cont" ]] || update_config container "$new_cont" || continue
                ;;
            "󰸱  Color Range"*)
                local new_cr
                new_cr=$(run_menu "Select Color Range" "limited" "full") || continue
                [[ -z "$new_cr" ]] || update_config color_range "$new_cr" || continue
                ;;
        esac
    done
}

# Desktop audio uses monitor sources; all other sources are inputs.
pick_audio_device() {
    local direction="$1" key="audio_$1" default="default_$1"
    local prompt symbol label dev_id dev_name entry choice count
    if [[ "$direction" == output ]]; then
        prompt='Desktop Audio (Output)' symbol='' label='Default Desktop Audio'
    else
        prompt='Microphone (Input)' symbol='' label='Default Microphone'
    fi
    local -a options=("  None" "$symbol  $label")
    local -A devices=(["  None"]=none ["$symbol  $label"]="$default")
    local -A seen=([none]=1 [default_output]=1 [default_input]=1)
    while IFS='|' read -r dev_id dev_name; do
        [[ -n "$dev_id" && -z "${seen[$dev_id]:-}" ]] || continue
        seen[$dev_id]=1
        if [[ "$direction" == output ]]; then
            [[ "$dev_id" == *.monitor ]] || continue
        else
            [[ "$dev_id" != *.monitor ]] || continue
        fi
        dev_name=${dev_name:-$dev_id}
        entry="$symbol  $dev_name"
        count=2
        while [[ -n "${devices[$entry]:-}" ]]; do
            entry="$symbol  $dev_name ($count)"
            ((++count))
        done
        options+=("$entry")
        devices[$entry]=$dev_id
    done < <(list_audio_devices)
    choice=$(run_menu "$prompt" "${options[@]}") || return 0
    [[ -n "$choice" ]] || return 0
    [[ -n "${devices[$choice]:-}" ]] || return 1
    update_config "$key" "${devices[$choice]}"
}

# --- SUBMENU: AUDIO & ROUTING ---
audio_menu() {
    while true; do
        local devices='' disp_out disp_in
        if [[ "$audio_output" != none && "$audio_output" != default_output ]] ||
           [[ "$audio_input" != none && "$audio_input" != default_input ]]; then
            devices=$(list_audio_devices)
        fi
        disp_out=$(get_audio_name "$audio_output" "$devices")
        [[ ${#disp_out} -gt 18 ]] && disp_out="${disp_out:0:15}..."

        disp_in=$(get_audio_name "$audio_input" "$devices")
        [[ ${#disp_in} -gt 18 ]] && disp_in="${disp_in:0:15}..."

        local -a opts=(
            "  Back"
            "󰓃  Output      [${disp_out}]"
            "  Input       [${disp_in}]"
            "󰎆  Codec       [${audio_codec}]"
            "󰡰  Bitrate     [${audio_bitrate}k]"
        )
        local choice
        choice=$(run_menu "󰎆  Audio Settings" "${opts[@]}") || return 0

        case "$choice" in
            "  Back") return 0 ;;
            "󰓃  Output"*) pick_audio_device output || continue ;;
            "  Input"*) pick_audio_device input || continue ;;
            "󰎆  Codec"*)
                local new_ac
                new_ac=$(run_menu "Audio Codec" "opus" "aac") || continue
                [[ -z "$new_ac" ]] || update_config audio_codec "$new_ac" || continue
                ;;
            "󰡰  Bitrate"*)
                local new_ab
                new_ab=$(run_menu "Audio Bitrate (kbps)" "0 (Auto)" "128" "192" "256" "320") || continue
                new_ab="${new_ab%% *}" # Strip the (Auto) text if present
                [[ -z "$new_ab" ]] || update_config audio_bitrate "$new_ab" || continue
                ;;
        esac
    done
}

# --- SUBMENU: CAPTURE & INTERFACE ---
capture_menu() {
    while true; do
        local -a opts=(
            "  Back"
            "󰣖  FPS         [${fps}]"
            "󰇀  Cursor      [${cursor}]"
            "󰂚  Indicator   [${show_indicator}]"
        )
        local choice
        choice=$(run_menu "󰆋  Capture Settings" "${opts[@]}") || return 0

        case "$choice" in
            "  Back") return 0 ;;
            "󰣖  FPS"*)
                local new_fps
                new_fps=$(run_menu "Select FPS" "5" "10" "15" "23" "30" "60" "120" "144") || continue
                [[ -z "$new_fps" ]] || update_config fps "$new_fps" || continue
                ;;
            "󰇀  Cursor"*)
                local new_cursor
                new_cursor=$(run_menu "Record Cursor?" "yes" "no") || continue
                [[ -z "$new_cursor" ]] || update_config cursor "$new_cursor" || continue
                ;;
            "󰂚  Indicator"*)
                local new_ind
                new_ind=$(run_menu "Show Red Dot Indicator?" "yes" "no") || continue
                [[ -z "$new_ind" ]] || update_config show_indicator "$new_ind" || continue
                ;;
        esac
    done
}

# --- SUBMENU: REPLAY BUFFER ---
replay_menu() {
    while true; do
        local -a opts=(
            "  Back"
            "  Duration    [${replay_buffer}s]"
            "󰋊  Storage     [${replay_storage}]"
            "  Restart     [${restart_replay}]"
        )
        local choice
        choice=$(run_menu "  Replay Settings" "${opts[@]}") || return 0

        case "$choice" in
            "  Back") return 0 ;;
            "  Duration"*)
                local new_buf
                new_buf=$(run_menu "Replay Buffer (0 to disable)" "0" "30" "60" "120" "300") || continue
                [[ -z "$new_buf" ]] || update_config replay_buffer "$new_buf" || continue
                ;;
            "󰋊  Storage"*)
                local new_store
                new_store=$(run_menu "Buffer Medium" "ram" "disk") || continue
                [[ -z "$new_store" ]] || update_config replay_storage "$new_store" || continue
                ;;
            "  Restart"*)
                local new_rest
                new_rest=$(run_menu "Restart after save?" "yes" "no") || continue
                [[ -z "$new_rest" ]] || update_config restart_replay "$new_rest" || continue
                ;;
        esac
    done
}

# --- QUICK SETTINGS (ROUTER) ---
quick_settings() {
    while true; do
        local -a opts=(
            "  Back"
            "󰕧  Video & Encoding"
            "󰎆  Audio & Routing"
            "󰆋  Capture & Interface"
            "  Replay Buffer"
        )
        local choice
        choice=$(run_menu "  Quick Settings" "${opts[@]}") || return 0
        case "$choice" in
            "  Back") return 0 ;;
            "󰕧  Video"*) video_menu ;;
            "󰎆  Audio"*) audio_menu ;;
            "󰆋  Capture"*) capture_menu ;;
            "  Replay"*) replay_menu ;;
        esac
    done
}

# --- MAIN LOOP ---
main() {
    local choice replay_label
    local -a main_opts
    while true; do
        main_opts=()
        if recorder_running; then
            [[ "$recorder_mode" != replay ]] || main_opts+=("  Save Replay Buffer")
            main_opts+=("  Stop Recording" "  Open TUI" "  Cancel")
        else
            with_lock clean_stale_state
            replay_label=''
            if [[ "$replay_buffer" =~ ^[0-9]+$ && ! "$replay_buffer" =~ ^0+$ ]]; then
                replay_label=" [Replay ${replay_buffer}s]"
            fi
            main_opts+=("  Record Full Screen$replay_label" "  Record Region$replay_label" "  Record (TUI configured)$replay_label" "  Open TUI" "  Quick Settings" "  Cancel")
        fi
        choice=$(run_menu 'Dusky Recorder' "${main_opts[@]}") || return 0
        case "$choice" in
            "  Stop"*) with_lock stop_recording; return ;;
            "  Save"*) with_lock save_replay; return ;;
            "  Record Full"*) with_lock start_recording screen; return ;;
            "  Record"*) with_lock start_recording region; return ;;
            "  Record (TUI configured)"*) with_lock start_recording; return ;;
            "  Open TUI") exec foot --app-id=dusky_tui python3 "$HOME/user_scripts/dusky_recorder/tui_dusky_recorder.py" ;;
            "  Quick Settings") quick_settings ;;
            *) return 0 ;;
        esac
    done
}

clean_stale_state() {
    if ! recorder_running; then
        manage_indicator stop
        rm -f "$RECORDER_PID_FILE"
    fi
}

usage() {
    printf 'Usage: %s [--start|--start-screen|--start-region|--stop|--save-replay|--toggle]\n' "$0"
    printf '%s\n' '  --start / --toggle use the configured source; --start-region opens a fresh selector.'
}

# Sourcing exposes helpers without executing an action.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    if (( $# > 1 )); then
        usage >&2
        exit 2
    fi
    case "${1:-}" in
        --help|-h) usage ;;
        --stop|stop) with_lock stop_recording ;;
        --start|start) with_lock start_recording ;;
        --start-screen|start_screen) with_lock start_recording screen ;;
        --start-region|start_region) with_lock start_recording region ;;
        --save-replay|save_replay) with_lock save_replay ;;
        --toggle|toggle) with_lock toggle_recording ;;
        '') main ;;
        *) usage >&2; exit 2 ;;
    esac
fi

#!/usr/bin/env bash
# =====================================================================
# mpris_playback_selection - Rofi Audio Source Controller
# Architecture: Bash 5.0+ | pactl + playerctl + D-Bus Pipeline
# Discovery: pactl sink-inputs + client resolution (catches ALL audio sources)
# Control: MPRIS (playerctl) / CLI Plugins / pactl fallback (with wise suspension)
# Dependencies: pactl, rofi, notify-send, busctl
# Optional: playerctl (MPRIS control)
# =====================================================================

set -euo pipefail

# --- Configuration ---
readonly APP_NAME="mpris_playback"
readonly NOTIFY_ICON="multimedia-audio-player-symbolic"

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly CONTROLLERS_DIR="$SCRIPT_DIR/controllers"

readonly ROFI_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/rofi/config.rasi"
readonly ROFI_THEME='window {width: 40%;} listview {lines: 10;}'

# --- Nerd Font Icons ---
readonly ICON_PLAYING="󰐊"
readonly ICON_PAUSED="󰏤"
readonly ICON_STOPPED="󰓛"
readonly ICON_UNKNOWN="󰝚"

readonly ICON_TOGGLE="󰐎"
readonly ICON_NEXT="󰒭"
readonly ICON_PREV="󰒮"
readonly ICON_MUTE="󰖁"
readonly ICON_BACK="󰁍"
readonly ICON_GROUP="󰉋"



# --- Dependency Check ---
declare -a REQ_CMDS=("pactl" "rofi" "notify-send" "busctl")
for cmd in "${REQ_CMDS[@]}"; do
    command -v "$cmd" >/dev/null || {
        printf '%s: missing dependency: %s\n' "$APP_NAME" "$cmd" >&2
        exit 1
    }
done

readonly HAS_PLAYERCTL=$(command -v playerctl >/dev/null 2>&1 && echo 1 || echo 0)

# --- Global Arrays for MPRIS mapping ---
declare -A pid_to_player
declare -A name_to_players_list

_update_mpris_maps() {
    unset pid_to_player name_to_players_list used_mpris_players used_cli_players player_to_pid
    declare -g -A pid_to_player
    declare -g -A player_to_pid
    declare -g -A name_to_players_list
    declare -g -A used_mpris_players
    declare -g -A used_cli_players
    
    local bus_list
    bus_list=$(busctl --user list 2>/dev/null || true)
    if [[ -n "$bus_list" ]]; then
        while IFS='|' read -r player p_pid; do
            [[ -z "$player" ]] && continue
            if [[ -n "$p_pid" && "$p_pid" != "-" ]]; then
                pid_to_player["$p_pid"]="$player"
                player_to_pid["$player"]="$p_pid"
            fi
            local base_name="${player%%.*}"
            local bl="${base_name,,}"
            if [[ -z "${name_to_players_list[$bl]:-}" ]]; then
                name_to_players_list["$bl"]="$player"
            else
                name_to_players_list["$bl"]="${name_to_players_list[$bl]} $player"
            fi
        done <<< "$(printf '%s\n' "$bus_list" | awk '/org.mpris.MediaPlayer2\./ { split($1, a, "org.mpris.MediaPlayer2."); print a[2] "|" $2 }')"
    fi
}

# --- Helper Functions ---

_notify() {
    local summary="$1"
    local body="${2:-}"
    notify-send -a "$APP_NAME" -i "$NOTIFY_ICON" \
        -h "string:x-canonical-private-synchronous:mpris_ctl" \
        "$summary" "$body" || true
}

_rofi_menu() {
    local prompt="$1"
    local output=""
    local status=0
    local -a rofi_args=(-dmenu -i -no-custom -p "$prompt" -theme-str "$ROFI_THEME")
    shift

    [[ -f "$ROFI_CONFIG" ]] && rofi_args+=(-config "$ROFI_CONFIG")

    set +e
    output=$(
        printf '%s\n' "$@" | rofi "${rofi_args[@]}"
    )
    status=$?
    set -e

    case $status in
        0) printf '%s\n' "$output" ;;
        1) return 1 ;;
        *)
            _notify "Execution Failed" "Rofi encountered an error."
            exit 1
            ;;
    esac
}

_status_icon() {
    local status="$1"
    case "${status,,}" in
        playing)             printf '%s' "$ICON_PLAYING" ;;
        paused|corked|muted) printf '%s' "$ICON_PAUSED"  ;;
        stopped|suspended)   printf '%s' "$ICON_STOPPED" ;;
        *)                   printf '%s' "$ICON_UNKNOWN" ;;
    esac
}

_truncate() {
    local text="$1"
    local max="${2:-40}"
    if ((${#text} > max)); then
        printf '%s…' "${text:0:$((max - 1))}"
    else
        printf '%s' "$text"
    fi
}

_format_seconds() {
    local total="${1%%.*}"
    ((total > 0)) 2>/dev/null || { printf '0:00'; return; }
    local m=$((total / 60))
    local s=$((total % 60))
    printf '%d:%02d' "$m" "$s"
}



_get_title_from_cmdline() {
    local pid="$1"
    local fallback="$2"

    if [[ -z "$pid" || ! -f "/proc/$pid/cmdline" ]]; then
        printf '%s' "$fallback"
        return
    fi

    local cmdline
    cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    [[ -z "$cmdline" ]] && { printf '%s' "$fallback"; return; }

    local title=""
    local count=0
    for arg in $cmdline; do
        ((count++))
        ((count == 1)) && continue
        [[ "$arg" == -* ]] && continue
        if [[ "$arg" == *"/"* || "$arg" == *.* ]]; then
            title=$(basename "$arg")
            break
        fi
    done

    if [[ -n "$title" ]]; then
        printf '%s' "$title"
    else
        local comm=""
        if [[ -f "/proc/$pid/comm" ]]; then
            comm=$(cat "/proc/$pid/comm" 2>/dev/null || true)
        fi
        [[ -n "$comm" ]] && printf '%s' "$comm" || printf '%s' "$fallback"
    fi
}

# =====================================================================
# PLUGIN SYSTEM SETUP
# =====================================================================

_ensure_controllers() {
    # Ensure controllers directory exists and scripts are executable
    if [[ -d "$CONTROLLERS_DIR" ]]; then
        find "$CONTROLLERS_DIR" -type f -exec chmod +x {} + 2>/dev/null || true
    fi
}

# =====================================================================
# DISCOVERY: pactl sink-inputs + client matching
# =====================================================================

_discover_all_sources() {
    local client_pids
    client_pids=$(pactl list clients 2>/dev/null | awk '/^Client #/ { cid = $2; gsub(/#/, "", cid) } /application\.process\.id = / { s = $0; gsub(/.*= "/, "", s); gsub(/"$/, "", s); if (cid != "") print cid ":" s }' || true)

    pactl list sink-inputs 2>/dev/null | awk -v client_pids_str="$client_pids" '
        BEGIN {
            n = split(client_pids_str, temp, "\n")
            for (i = 1; i <= n; i++) {
                split(temp[i], pair, ":")
                client_to_pid[pair[1]] = pair[2]
            }
        }
        /^Sink Input #/ {
            if (id != "") {
                if (pid == "" && client_id != "") pid = client_to_pid[client_id]
                print id "|" app "|" media "|" corked "|" mute "|" pid "|" binary
            }
            id = $3; gsub(/#/, "", id)
            app = ""; media = ""; corked = ""; mute = ""; pid = ""; binary = ""; client_id = ""
        }
        /^[ \t]*Client:/ { client_id = $2 }
        /^[ \t]*Corked:/ { corked = $2 }
        /^[ \t]*Mute:/   { mute   = $2 }
        /application\.name = /        { s = $0; gsub(/.*= "/, "", s); gsub(/"$/, "", s); app    = s }
        /media\.name = /              { s = $0; gsub(/.*= "/, "", s); gsub(/"$/, "", s); media  = s }
        /application\.process\.id = / { s = $0; gsub(/.*= "/, "", s); gsub(/"$/, "", s); pid    = s }
        /application\.process\.binary = / { s = $0; gsub(/.*= "/, "", s); gsub(/"$/, "", s); binary = s }
        END {
            if (id != "") {
                if (pid == "" && client_id != "") pid = client_to_pid[client_id]
                print id "|" app "|" media "|" corked "|" mute "|" pid "|" binary
            }
        }
    ' | sort -t"|" -k4,4
}

# =====================================================================
# CONTROL BACKEND DETECTION
# =====================================================================

_detect_control_backend() {
    local app_name="$1"
    local binary="$2"
    local pid="$3"
    local out_var="$4"

    # 1. Custom CLI controllers in controllers dir
    if [[ -d "$CONTROLLERS_DIR" ]]; then
        local proc_name=""
        if [[ -n "$pid" && -f "/proc/$pid/comm" ]]; then
            proc_name=$(cat "/proc/$pid/comm" 2>/dev/null || true)
        fi
        
        for name in "$proc_name" "$binary" "$app_name"; do
            [[ -z "$name" ]] && continue
            local controller_path="$CONTROLLERS_DIR/${name,,}"
            if [[ -x "$controller_path" ]]; then
                local cli_id="cli:${name,,}"
                if [[ -z "${used_cli_players[$cli_id]:-}" ]]; then
                    used_cli_players["$cli_id"]=1
                    printf -v "$out_var" '%s' "$cli_id"
                    return
                fi
            fi
        done
    fi

    # 2. MPRIS players via playerctl
    if ((HAS_PLAYERCTL)); then
        # Direct PID match
        if [[ -n "$pid" && -n "${pid_to_player[$pid]:-}" ]]; then
            local player="${pid_to_player[$pid]}"
            if [[ -z "${used_mpris_players[$player]:-}" ]]; then
                used_mpris_players["$player"]=1
                printf -v "$out_var" 'mpris:%s' "$player"
                return
            fi
        fi

        # Name-based match
        _find_player() {
            local search="$1"
            local list="${name_to_players_list[$search]:-}"
            if [[ -n "$list" ]]; then
                local remaining=""
                local found=""
                for p in $list; do
                    local p_pid="${player_to_pid[$p]:-}"
                    if [[ -n "$p_pid" && "$p_pid" != "$pid" ]]; then
                        remaining="${remaining}${remaining:+ }$p"
                        continue
                    fi
                    
                    if [[ -z "$found" && -z "${used_mpris_players[$p]:-}" ]]; then
                        found="$p"
                        used_mpris_players["$p"]=1
                    else
                        remaining="${remaining}${remaining:+ }$p"
                    fi
                done
                name_to_players_list["$search"]="$remaining"
                if [[ -n "$found" ]]; then
                    printf -v "$out_var" 'mpris:%s' "$found"
                    return 0
                fi
            fi
            return 1
        }

        if [[ -n "$binary" ]]; then
            if _find_player "${binary,,}"; then return; fi
        fi
        if [[ -n "$app_name" ]]; then
            if _find_player "${app_name,,}"; then return; fi
        fi
        
        # Fuzzy match
        local k bin_lower app_lower
        [[ -n "$binary" ]] && bin_lower="${binary,,}" || bin_lower=""
        [[ -n "$app_name" ]] && app_lower="${app_name,,}" || app_lower=""
        for k in "${!name_to_players_list[@]}"; do
            if [[ -n "$bin_lower" && ( "$bin_lower" == *"$k"* || "$k" == *"$bin_lower"* ) ]]; then
                if _find_player "$k"; then return; fi
            fi
            if [[ -n "$app_lower" && ( "$app_lower" == *"$k"* || "$k" == *"$app_lower"* ) ]]; then
                if _find_player "$k"; then return; fi
            fi
        done
    fi

    printf -v "$out_var" 'pactl'
}

# =====================================================================
# METADATA ENRICHMENT
# =====================================================================

_get_mpris_metadata() {
    local player="$1"
    local fallback_title="$2"
    local fallback_app="$3"
    local title artist position duration status

    local meta_str=""
    set +e
    meta_str=$(playerctl -p "$player" metadata --format '{{status}}|{{default(title,"")}}|{{default(artist,"")}}|{{duration(position)}}|{{duration(mpris:length)}}' 2>/dev/null)
    set -e

    if [[ -n "$meta_str" ]]; then
        IFS='|' read -r status title artist position duration <<< "$meta_str"
    fi

    [[ -z "$title" ]] && title="$fallback_title"
    [[ -z "$title" ]] && title="$fallback_app"
    [[ -z "$title" ]] && title="Audio Stream"

    printf '%s|%s|%s|%s|%s\n' "$title" "$artist" "$position" "$duration" "${status:-Unknown}"
}

_get_cli_metadata() {
    local type="$1"
    local pid="$2"
    local fallback_title="$3"
    local fallback_app="$4"

    local controller_path="$CONTROLLERS_DIR/$type"
    local title="" artist="" position="" duration="" status=""

    if [[ -x "$controller_path" ]]; then
        local meta
        set +e
        meta=$("$controller_path" now 2>/dev/null)
        set -e
        if [[ -n "$meta" ]]; then
            IFS='|' read -r title artist position duration status <<< "$meta"
        fi
    fi

    [[ -z "$title" ]] && title="$fallback_title"
    [[ -z "$title" ]] && title="$fallback_app"
    [[ -z "$title" ]] && title="Audio Stream"

    if [[ -z "$status" ]]; then
        status="Playing"
        if [[ -n "$pid" && -f "/proc/$pid/stat" ]]; then
            local proc_state
            proc_state=$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)
            [[ "$proc_state" == "T" ]] && status="Suspended"
        fi
    fi

    printf '%s|%s|%s|%s|%s\n' "$title" "$artist" "$position" "$duration" "$status"
}

_get_pactl_metadata() {
    local sink_id="$1"
    local media_name="$2"
    local app_name="$3"
    local corked="$4"
    local mute="$5"
    local pid="$6"

    local title artist position duration status
    
    title=$(_clean_media_name "$media_name")
    
    if [[ -z "$title" || "$title" == "Audio Stream" || "$title" == "webm" ]]; then
        title=$(_get_title_from_cmdline "$pid" "$app_name")
    fi
    
    [[ -z "$title" ]] && title="$app_name"
    [[ -z "$title" ]] && title="Audio Stream"
    
    artist=""
    position=""
    duration=""

    local proc_state=""
    if [[ -n "$pid" && -f "/proc/$pid/stat" ]]; then
        proc_state=$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)
    fi

    if [[ "$proc_state" == "T" ]]; then
        status="Suspended"
    elif [[ "$corked" == "yes" ]]; then
        status="Paused"
    elif [[ "$mute" == "yes" ]]; then
        status="Muted"
    else
        status="Playing"
    fi

    printf '%s|%s|%s|%s|%s\n' "$title" "$artist" "$position" "$duration" "$status"
}

# =====================================================================
# DISPLAY BUILDER
# =====================================================================

_clean_media_name() {
    local raw="$1"
    # URL-like garbage (contains &key=value patterns)
    if [[ "$raw" == *"&"* && "$raw" == *"="* ]]; then
        # Some URL media names end with " - appname", extract it
        if [[ "$raw" == *" - "* ]]; then
            local suffix="${raw##* - }"
            # If the suffix is just an app name like "mpv", return empty
            if [[ "$suffix" == "mpv" || "$suffix" == "vlc" || "$suffix" == "firefox" ]]; then
                printf ''
            else
                printf '%s' "$suffix"
            fi
        else
            printf ''
        fi
        return
    fi
    local cleaned="$raw"
    # Strip common trailing suffixes
    cleaned="${cleaned% - YouTube}"
    cleaned="${cleaned% - Twitch}"
    cleaned="${cleaned% - mpv}"
    cleaned="${cleaned% - VLC media player}"
    cleaned="${cleaned% - Firefox}"
    printf '%s' "$cleaned"
}

_build_source_entry() {
    local app_name="$1" media_name="$2" corked="$3" mute="$4" backend="$5" sink_id="$6" pid="$7"
    local title="" artist="" position="" duration="" status=""

    # Get supplementary data (artist, position, duration, status) from the backend
    if [[ "$backend" == mpris:* ]]; then
        local player="${backend#mpris:}"
        local mpris_meta
        mpris_meta=$(_get_mpris_metadata "$player" "$media_name" "$app_name")
        IFS='|' read -r title artist position duration status <<< "$mpris_meta"
    elif [[ "$backend" == cli:* ]]; then
        local type="${backend#cli:}"
        local cli_meta
        cli_meta=$(_get_cli_metadata "$type" "$pid" "$media_name" "$app_name")
        IFS='|' read -r title artist position duration status <<< "$cli_meta"
    else
        local pactl_meta
        pactl_meta=$(_get_pactl_metadata "$sink_id" "$media_name" "$app_name" "$corked" "$mute" "$pid")
        IFS='|' read -r title artist position duration status <<< "$pactl_meta"
    fi

    # CRITICAL: Always prefer pactl's media_name as the display title.
    # MPRIS/CLI titles are global (e.g. Firefox MPRIS only reports the active tab,
    # CLI controllers like ytm report whichever instance they consider "active").
    # pactl's media.name is per-stream unique and always accurate.
    local pactl_title
    pactl_title=$(_clean_media_name "$media_name")
    if [[ -n "$pactl_title" && "$pactl_title" != "Audio Stream" && "$pactl_title" != "webm" && "$pactl_title" != "$app_name" ]]; then
        title="$pactl_title"
    fi

    local icon
    icon=$(_status_icon "$status")

    title=$(_truncate "$title" 45)

    local track_info
    if [[ -n "$artist" ]]; then
        artist=$(_truncate "$artist" 25)
        track_info="${artist} · ${title}"
    else
        track_info="${title}"
    fi

    local time_info=""
    if [[ -n "$position" && -n "$duration" && "$duration" != "0:00" && "$duration" != "" ]]; then
        time_info="[${position}/${duration}]"
    elif [[ -n "$position" && "$position" != "0:00" && "$position" != "" ]]; then
        time_info="[${position}]"
    fi

    local suffix=""

    if [[ -n "$time_info" ]]; then
        printf '%s  %s  %s  %s%s' "$icon" "$app_name" "$track_info" "$time_info" "$suffix"
    else
        printf '%s  %s  %s%s' "$icon" "$app_name" "$track_info" "$suffix"
    fi
}

# =====================================================================
# PLAYBACK CONTROL
# =====================================================================

_control_source() {
    local action="$1" backend="$2" sink_input_id="$3" pid="$4"

    case "$backend" in
        mpris:*)
            local player="${backend#mpris:}"
            case "$action" in
                toggle) playerctl -p "$player" play-pause 2>/dev/null || true ;;
                next)   playerctl -p "$player" next 2>/dev/null || true ;;
                prev)   playerctl -p "$player" previous 2>/dev/null || true ;;
            esac
            ;;
        cli:*)
            local type="${backend#cli:}"
            local controller_path="$CONTROLLERS_DIR/$type"
            if [[ -x "$controller_path" ]]; then
                "$controller_path" "$action" 2>/dev/null || true
            fi
            ;;
        pactl)
            # No controls for pactl fallback since user requested removal of mute/suspend
            ;;
    esac
}

_get_source_status_line() {
    local app_name="$1" backend="$2" sink_id="$3" pid="$4"

    local title artist position duration status
    if [[ "$backend" == mpris:* ]]; then
        local player="${backend#mpris:}"
        local meta
        meta=$(_get_mpris_metadata "$player" "" "$app_name")
        IFS='|' read -r title artist position duration status <<< "$meta"
    elif [[ "$backend" == cli:* ]]; then
        local type="${backend#cli:}"
        local meta
        meta=$(_get_cli_metadata "$type" "$pid" "" "$app_name")
        IFS='|' read -r title artist position duration status <<< "$meta"
    else
        local raw_info
        raw_info=$(_discover_all_sources | grep "^${sink_id}|" || true)
        if [[ -n "$raw_info" ]]; then
            local s_id a_name m_name corked mute p_id bin
            IFS='|' read -r s_id a_name m_name corked mute p_id bin <<< "$raw_info"
            local meta
            meta=$(_get_pactl_metadata "$s_id" "$m_name" "$a_name" "$corked" "$mute" "$p_id")
            IFS='|' read -r title artist position duration status <<< "$meta"
        else
            title="Audio Stream"
            status="Unknown"
        fi
    fi

    local icon
    icon=$(_status_icon "$status")
    if [[ "$backend" == mpris:* || "$backend" == cli:* ]]; then
        if [[ -n "$artist" ]]; then
            printf '%s %s: %s · %s (%s)' "$icon" "$app_name" "$artist" "$title" "$status"
        else
            printf '%s %s: %s (%s)' "$icon" "$app_name" "$title" "$status"
        fi
    else
        printf '%s %s (pactl): %s (%s)' "$icon" "$app_name" "$title" "$status"
    fi
}

# =====================================================================
# ROFI FLOWS
# =====================================================================

show_control_menu() {
    local app_name="$1" backend="$2" sink_input_id="$3" pid="$4"

    while :; do
        local -a controls=()
        if [[ "$backend" == "pactl" ]]; then
            controls=("${ICON_BACK}  Back to Sources")
        else
            local current_status="unknown"
            if [[ "$backend" == mpris:* ]]; then
                local player="${backend#mpris:}"
                current_status=$(playerctl -p "$player" status 2>/dev/null || echo "unknown")
            elif [[ "$backend" == cli:* ]]; then
                local type="${backend#cli:}"
                local meta
                meta=$(_get_cli_metadata "$type" "$pid" "" "")
                IFS='|' read -r _ _ _ _ current_status <<< "$meta"
            fi
            current_status="${current_status,,}"

            local toggle_label
            case "$current_status" in
                playing) toggle_label="${ICON_TOGGLE}  Pause" ;;
                paused)  toggle_label="${ICON_TOGGLE}  Play"  ;;
                *)       toggle_label="${ICON_TOGGLE}  Play/Pause" ;;
            esac

            controls=(
                "$toggle_label"
                "${ICON_NEXT}  Next Track"
                "${ICON_PREV}  Previous Track"
                "${ICON_BACK}  Back to Sources"
            )
        fi

        local choice
        set +e
        choice=$(_rofi_menu "$app_name" "${controls[@]}")
        local rofi_status=$?
        set -e

        ((rofi_status != 0)) && return 0

        case "$choice" in
            *"Pause"*|*"Play/Pause"*|*"Play"*)
                _control_source toggle "$backend" "$sink_input_id" "$pid"
                sleep 0.2
                local info
                info=$(_get_source_status_line "$app_name" "$backend" "$sink_input_id" "$pid")
                _notify "$app_name" "$info"
                ;;
            *"Next"*)
                _control_source next "$backend" "$sink_input_id" "$pid"
                sleep 0.4
                local info
                info=$(_get_source_status_line "$app_name" "$backend" "$sink_input_id" "$pid")
                _notify "Next Track" "$info"
                ;;
            *"Previous"*)
                _control_source prev "$backend" "$sink_input_id" "$pid"
                sleep 0.4
                local info
                info=$(_get_source_status_line "$app_name" "$backend" "$sink_input_id" "$pid")
                _notify "Previous Track" "$info"
                ;;
            *"Back"*)
                return 0
                ;;
            *)
                return 0
                ;;
        esac
    done
}

show_source_menu() {
    local open_group=""

    while :; do
        _update_mpris_maps

        local raw_data
        raw_data=$(_discover_all_sources)

        if [[ -z "$raw_data" ]]; then
            _notify "No Audio Sources" "No applications are currently producing audio."
            exit 0
        fi

        local -a all_sink_ids=() all_app_names=() all_backends=() all_display_entries=() all_pids=()
        local -a unique_apps=()
        declare -A app_counts=()

        while IFS='|' read -r sink_id app_name media_name corked mute pid binary; do
            [[ -z "$sink_id" ]] && continue
            if [[ -z "$binary" && -n "$pid" ]]; then
                if [[ -f "/proc/$pid/comm" ]]; then
                    binary=$(cat "/proc/$pid/comm" 2>/dev/null || true)
                fi
            fi

            [[ -z "$app_name" ]] && app_name="${binary:-Unknown}"

            local backend=""
            _detect_control_backend "$app_name" "$binary" "$pid" "backend"

            local entry
            entry=$(_build_source_entry "$app_name" "$media_name" "$corked" "$mute" "$backend" "$sink_id" "$pid")

            all_sink_ids+=("$sink_id")
            all_app_names+=("$app_name")
            all_backends+=("$backend")
            all_display_entries+=("$entry")
            all_pids+=("$pid")

            local current_count=${app_counts["$app_name"]:-0}
            if (( current_count == 0 )); then
                unique_apps+=("$app_name")
            fi
            app_counts["$app_name"]=$(( current_count + 1 ))
        done <<< "$raw_data"

        if ((${#all_display_entries[@]} == 0)); then
            _notify "No Audio Sources" "No applications are currently producing audio."
            exit 0
        fi

        if [[ -n "$open_group" ]]; then
            # Verify the group still exists
            if [[ -z "${app_counts[$open_group]:-}" ]]; then
                open_group="" # Group closed
                continue
            fi

            # Submenu
            local u_app="$open_group"
            local -a sub_entries=()
            local -a sub_all_idx=()
            for i in "${!all_app_names[@]}"; do
                if [[ "${all_app_names[i]}" == "$u_app" ]]; then
                    sub_entries+=("${all_display_entries[i]}")
                    sub_all_idx+=("$i")
                fi
            done
            sub_entries+=("${ICON_BACK}  Back to Sources")
            
            local sub_choice
            set +e
            sub_choice=$(_rofi_menu "$u_app Sources" "${sub_entries[@]}")
            local sub_status=$?
            set -e
            
            if ((sub_status != 0)); then
                open_group=""
                continue
            fi

            if [[ "$sub_choice" == *Back* ]]; then
                open_group=""
                continue
            fi

            local chosen_sub_idx=""
            for i in "${!sub_entries[@]}"; do
                if [[ "${sub_entries[i]}" == "$sub_choice" ]]; then
                    chosen_sub_idx="$i"
                    break
                fi
            done
            
            if [[ -n "$chosen_sub_idx" ]]; then
                local actual_idx="${sub_all_idx[$chosen_sub_idx]}"
                show_control_menu "${all_app_names[$actual_idx]}" "${all_backends[$actual_idx]}" "${all_sink_ids[$actual_idx]}" "${all_pids[$actual_idx]}"
            else
                open_group=""
            fi
            continue
        fi

        # Build main menu entries
        local -a main_menu_entries=()
        local -a main_menu_app_map=()
        local -a main_menu_source_idx=()

        for u_app in "${unique_apps[@]}"; do
            local count=${app_counts["$u_app"]}
            if (( count > 1 )); then
                main_menu_entries+=("${ICON_GROUP}  $u_app  ($count sources)")
                main_menu_app_map+=("$u_app")
                main_menu_source_idx+=("-1")
            else
                local idx=-1
                for i in "${!all_app_names[@]}"; do
                    if [[ "${all_app_names[i]}" == "$u_app" ]]; then
                        idx=$i
                        break
                    fi
                done
                main_menu_entries+=("${all_display_entries[idx]}")
                main_menu_app_map+=("$u_app")
                main_menu_source_idx+=("$idx")
            fi
        done

        local choice
        set +e
        choice=$(_rofi_menu "Audio Sources" "${main_menu_entries[@]}")
        local rofi_status=$?
        set -e

        ((rofi_status != 0)) && exit 0

        local selected_idx=""
        for i in "${!main_menu_entries[@]}"; do
            if [[ "${main_menu_entries[i]}" == "$choice" ]]; then
                selected_idx="$i"
                break
            fi
        done

        [[ -z "$selected_idx" ]] && exit 0

        local s_idx="${main_menu_source_idx[$selected_idx]}"
        if [[ "$s_idx" == "-1" ]]; then
            open_group="${main_menu_app_map[$selected_idx]}"
        else
            show_control_menu "${all_app_names[$s_idx]}" "${all_backends[$s_idx]}" "${all_sink_ids[$s_idx]}" "${all_pids[$s_idx]}"
        fi
    done
}

# =====================================================================
# CLI MODE (Direct Keybind Actions)
# =====================================================================

_resolve_active_source() {
    _update_mpris_maps

    local raw_data
    raw_data=$(_discover_all_sources)
    if [[ -z "$raw_data" ]]; then
        _notify "No Audio Sources" "No applications are currently producing audio."
        exit 0
    fi

    local best_backend="" best_sink_id="" best_app_name="" best_pid=""
    local fallback_backend="" fallback_sink_id="" fallback_app_name="" fallback_pid=""

    while IFS='|' read -r sink_id app_name media_name corked mute pid binary; do
        [[ -z "$sink_id" ]] && continue
        if [[ -z "$binary" && -n "$pid" ]]; then
            if [[ -f "/proc/$pid/comm" ]]; then
                binary=$(cat "/proc/$pid/comm" 2>/dev/null || true)
            fi
        fi
        [[ -z "$app_name" ]] && app_name="${binary:-Unknown}"

        local backend=""
        _detect_control_backend "$app_name" "$binary" "$pid" "backend"

        if [[ -z "$fallback_backend" ]]; then
            fallback_backend="$backend"
            fallback_sink_id="$sink_id"
            fallback_app_name="$app_name"
            fallback_pid="$pid"
        fi

        local is_playing=0
        if [[ "$backend" == mpris:* ]]; then
            local player="${backend#mpris:}"
            local status
            status=$(playerctl -p "$player" status 2>/dev/null || echo "Unknown")
            if [[ "${status,,}" == "playing" ]]; then
                is_playing=1
            fi
        elif [[ "$backend" == cli:* ]]; then
            local type="${backend#cli:}"
            local meta
            meta=$(_get_cli_metadata "$type" "$pid" "" "")
            local status
            IFS='|' read -r _ _ _ _ status <<< "$meta"
            if [[ "${status,,}" == "playing" ]]; then
                is_playing=1
            fi
        else
            if [[ "$corked" == "no" && "$mute" == "no" ]]; then
                is_playing=1
            fi
        fi

        if ((is_playing)); then
            best_backend="$backend"
            best_sink_id="$sink_id"
            best_app_name="$app_name"
            best_pid="$pid"
            break
        fi
    done <<< "$raw_data"

    if [[ -n "$best_backend" ]]; then
        printf '%s|%s|%s|%s' "$best_backend" "$best_sink_id" "$best_app_name" "$best_pid"
    else
        printf '%s|%s|%s|%s' "$fallback_backend" "$fallback_sink_id" "$fallback_app_name" "$fallback_pid"
    fi
}

cli_toggle() {
    local backend sink_id app_name pid
    IFS='|' read -r backend sink_id app_name pid <<< "$(_resolve_active_source)"
    _control_source toggle "$backend" "$sink_id" "$pid"
    sleep 0.2
    local info
    info=$(_get_source_status_line "$app_name" "$backend" "$sink_id" "$pid")
    _notify "Toggle" "$info"
}

cli_next() {
    local backend sink_id app_name pid
    IFS='|' read -r backend sink_id app_name pid <<< "$(_resolve_active_source)"
    _control_source next "$backend" "$sink_id" "$pid"
    sleep 0.4
    local info
    info=$(_get_source_status_line "$app_name" "$backend" "$sink_id" "$pid")
    _notify "Next" "$info"
}

cli_prev() {
    local backend sink_id app_name pid
    IFS='|' read -r backend sink_id app_name pid <<< "$(_resolve_active_source)"
    _control_source prev "$backend" "$sink_id" "$pid"
    sleep 0.4
    local info
    info=$(_get_source_status_line "$app_name" "$backend" "$sink_id" "$pid")
    _notify "Previous" "$info"
}


cli_status() {
    _update_mpris_maps
    local raw_data
    raw_data=$(_discover_all_sources)

    if [[ -z "$raw_data" ]]; then
        _notify "No Audio Sources" "No applications are currently producing audio."
        exit 0
    fi

    local body=""
    while IFS='|' read -r sink_id app_name media_name corked mute pid binary; do
        [[ -z "$sink_id" ]] && continue
        if [[ -z "$binary" && -n "$pid" ]]; then
            if [[ -f "/proc/$pid/comm" ]]; then
                binary=$(cat "/proc/$pid/comm" 2>/dev/null || true)
            fi
        fi
        [[ -z "$app_name" ]] && app_name="${binary:-Unknown}"
        local backend=""
        _detect_control_backend "$app_name" "$binary" "$pid" "backend"
        local line
        line=$(_get_source_status_line "$app_name" "$backend" "$sink_id" "$pid")
        body+="${line}\n"
    done <<< "$raw_data"

    _notify "Active Audio Sources" "$(printf '%b' "$body")"
}

# --- Usage ---
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTION]

Audio source controller with rofi integration.
Discovers ALL audio sources via PipeWire/PulseAudio sink inputs.
Dynamically pairs streams to MPRIS control interfaces or fallback plugins/pactl backends.

Options:
  (no args)     Open the interactive rofi source menu
  --toggle      Play/Pause the most recently active source
  --next        Skip to the next track
  --prev        Skip to the previous track
  --status      Show all active sources via notification
  -h, --help    Show this help message
EOF
}

# --- Main Entry Point ---
main() {
    _ensure_controllers
    case "${1:-}" in
        --toggle)  cli_toggle  ;;
        --next)    cli_next    ;;
        --prev)    cli_prev    ;;
        --status)  cli_status  ;;
        -h|--help) usage       ;;
        "")        show_source_menu ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage >&2
            exit 1
            ;;
    esac
}

main "$@"

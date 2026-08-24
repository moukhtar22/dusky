#!/usr/bin/env bash
# ==============================================================================
# DUSKY AUDIO :: MODERN WAYLAND PIPEWIRE AUDIO MANAGER & SWITCHER
# Engine: WirePlumber (wpctl) / Native PipeWire
# Baseline: Linux 7.1+ / Pure Wayland / PipeWire 1.0+
# ==============================================================================
set -euo pipefail

readonly ROFI_THEME_STR="window { width: 460px; } listview { lines: 6; }"
readonly SYNC_ID="sys-osd"

# 1. Dependency Validation
for cmd in wpctl rofi notify-send awk; do
    if ! command -v "$cmd" &>/dev/null; then
        notify-send -u critical "Dusky Audio" "Missing required dependency: $cmd"
        exit 1
    fi
done

# 2. Helper: Clean & Succinct Device Name Resolver
get_device_desc() {
    local node_id="$1"
    local raw_name="$2"

    # Known virtual nodes - keep them clean and succinct
    case "$raw_name" in
        "ghelper-audio-sink"|"ghelper-audio-sink-out"|"dusky-audio-sink"|"dusky-audio-sink-out")
            echo "Dusky Audio"
            return
            ;;
        "ghelper-audio"|"ghelper-audio-capture"|"dusky-audio"|"dusky-audio-capture")
            echo "Dusky Mic"
            return
            ;;
    esac

    # Query PipeWire for friendly description or nickname
    if command -v pw-cli &>/dev/null; then
        local desc
        desc=$(pw-cli info "$node_id" 2>/dev/null | awk -F" = " "/node\.description/{gsub(/\"/, \"\", \$2); print \$2; exit}")
        if [[ -n "$desc" && "$desc" != "$raw_name" ]]; then
            # Clean up overly verbose hardware/DSP suffixes
            desc="${desc/ (Two-Way RT DSP)/}"
            desc="${desc/ (Noise Suppressed)/}"
            echo "$desc"
            return
        fi
        local nick
        nick=$(pw-cli info "$node_id" 2>/dev/null | awk -F" = " "/node\.nick/{gsub(/\"/, \"\", \$2); print \$2; exit}")
        if [[ -n "$nick" && "$nick" != "$raw_name" ]]; then
            echo "$nick"
            return
        fi
    fi
    echo "$raw_name"
}

# 3. Output Switcher Menu
menu_output() {
    declare -a rofi_options=()
    declare -A device_map=()

    while IFS= read -r line; do
        [[ "$line" =~ ([0-9]+)\. ]] || continue
        local id="${BASH_REMATCH[1]}"
        local is_active=false
        [[ "$line" == *"*"* ]] && is_active=true

        local is_muted=false
        [[ "$line" == *"MUTED"* ]] && is_muted=true

        # Strip ID prefix, volume, and filter tags
        local name="${line#*${id}. }"
        name="${name% \[vol:*}"
        name="${name% \[Audio/Sink\]*}"
        name="${name#"${name%%[![:space:]]*}"}"
        name="${name%"${name##*[![:space:]]}"}"
        [[ -z "$name" ]] && continue

        # Resolve succinct name
        name=$(get_device_desc "$id" "$name")

        local icon=" "
        $is_muted && icon=" "

        local display_str="$icon  $name"
        $is_active && display_str+="  [Active]"

        # Handle identical hardware names gracefully
        local unique_str="$display_str"
        local count=2
        while [[ -n "${device_map[$unique_str]:-}" ]]; do
            unique_str="$display_str ($count)"
            ((count++))
        done

        rofi_options+=("$unique_str")
        device_map["$unique_str"]="$id"
    done < <(wpctl status | awk "
      /Audio/{audio=1}
      /Video/{audio=0}
      audio && /Sinks:/{in_sinks=1; in_filters=0; next}
      audio && /Filters:/{in_filters=1; in_sinks=0; next}
      audio && /Sources:|Streams:|Settings:/{in_sinks=0; in_filters=0}
      in_sinks { print \$0 }
      in_filters && /\[Audio\/Sink\]/ { print \$0 }
    ")

    if [[ ${#rofi_options[@]} -eq 0 ]]; then
        notify-send -u critical "Dusky Audio" "No output devices found."
        return 1
    fi

    if [[ "${DUSKY_AUDIO_TEST:-0}" == "1" ]]; then
        printf '%s\n' "${rofi_options[@]}"
        return 0
    fi

    local choice
    choice=$(printf "%s\n" "${rofi_options[@]}" | rofi -dmenu -i -p "󰓃  Select Output" -theme-str "$ROFI_THEME_STR" -format s)

    if [[ -n "$choice" && -n "${device_map[$choice]:-}" ]]; then
        local target_id="${device_map[$choice]}"
        wpctl set-default "$target_id"

        local clean_name="${choice/\[Active\]/}"
        clean_name="${clean_name/  /}"
        clean_name="${clean_name/  /}"
        clean_name=$(echo "$clean_name" | xargs)

        local vol_info
        vol_info=$(wpctl get-volume "$target_id" 2>/dev/null || echo "Volume: 1.00")
        local vol_val
        vol_val=$(echo "$vol_info" | awk "{print \$2}")
        local vol_pct
        vol_pct=$(awk -v v="$vol_val" "BEGIN { printf \"%.0f\", (v > 0 ? v : 0) * 100 }")

        local osd_icon="audio-volume-high-symbolic"
        if [[ "$vol_info" == *"MUTED"* ]] || (( vol_pct == 0 )); then
            osd_icon="audio-volume-muted-symbolic"
        elif (( vol_pct <= 33 )); then
            osd_icon="audio-volume-low-symbolic"
        elif (( vol_pct <= 66 )); then
            osd_icon="audio-volume-medium-symbolic"
        fi

        notify-send -a "OSD" -h string:x-canonical-private-synchronous:"$SYNC_ID" -h int:value:"$vol_pct" -i "$osd_icon" "$clean_name"
    fi
}

# 4. Input Switcher Menu
menu_input() {
    declare -a rofi_options=()
    declare -A device_map=()

    while IFS= read -r line; do
        [[ "$line" =~ ([0-9]+)\. ]] || continue
        local id="${BASH_REMATCH[1]}"
        local is_active=false
        [[ "$line" == *"*"* ]] && is_active=true

        local is_muted=false
        [[ "$line" == *"MUTED"* ]] && is_muted=true

        # Strip ID prefix, volume, and filter tags
        local name="${line#*${id}. }"
        name="${name% \[vol:*}"
        name="${name% \[Audio/Source\]*}"
        name="${name#"${name%%[![:space:]]*}"}"
        name="${name%"${name##*[![:space:]]}"}"
        [[ -z "$name" ]] && continue

        # Resolve succinct name
        name=$(get_device_desc "$id" "$name")

        local icon=" "
        $is_muted && icon=" "

        local display_str="$icon  $name"
        $is_active && display_str+="  [Active]"

        local unique_str="$display_str"
        local count=2
        while [[ -n "${device_map[$unique_str]:-}" ]]; do
            unique_str="$display_str ($count)"
            ((count++))
        done

        rofi_options+=("$unique_str")
        device_map["$unique_str"]="$id"
    done < <(wpctl status | awk "
      /Audio/{audio=1}
      /Video/{audio=0}
      audio && /Sources:/{in_sources=1; in_filters=0; next}
      audio && /Filters:/{in_filters=1; in_sources=0; next}
      audio && /Sinks:|Streams:|Settings:/{in_sources=0; in_filters=0}
      in_sources { print \$0 }
      in_filters && /\[Audio\/Source\]/ { print \$0 }
    ")

    if [[ ${#rofi_options[@]} -eq 0 ]]; then
        notify-send -u critical "Dusky Audio" "No input devices found."
        return 1
    fi

    if [[ "${DUSKY_AUDIO_TEST:-0}" == "1" ]]; then
        printf "%s\n" "${rofi_options[@]}"
        return 0
    fi

    local choice
    choice=$(printf "%s\n" "${rofi_options[@]}" | rofi -dmenu -i -p "  Select Input" -theme-str "$ROFI_THEME_STR" -format s)

    if [[ -n "$choice" && -n "${device_map[$choice]:-}" ]]; then
        local target_id="${device_map[$choice]}"
        wpctl set-default "$target_id"

        local clean_name="${choice/\[Active\]/}"
        clean_name="${clean_name/  /}"
        clean_name="${clean_name/  /}"
        clean_name=$(echo "$clean_name" | xargs)

        local vol_info
        vol_info=$(wpctl get-volume "$target_id" 2>/dev/null || echo "Volume: 1.00")
        local vol_val
        vol_val=$(echo "$vol_info" | awk "{print \$2}")
        local vol_pct
        vol_pct=$(awk -v v="$vol_val" "BEGIN { printf \"%.0f\", (v > 0 ? v : 0) * 100 }")

        local osd_icon="microphone-sensitivity-high-symbolic"
        if [[ "$vol_info" == *"MUTED"* ]] || (( vol_pct == 0 )); then
            osd_icon="microphone-sensitivity-muted-symbolic"
        elif (( vol_pct <= 33 )); then
            osd_icon="microphone-sensitivity-low-symbolic"
        elif (( vol_pct <= 66 )); then
            osd_icon="microphone-sensitivity-medium-symbolic"
        fi

        notify-send -a "OSD" -h string:x-canonical-private-synchronous:"$SYNC_ID" -h int:value:"$vol_pct" -i "$osd_icon" "$clean_name"
    fi
}

# 5. Master Hub Menu (Submenu Router)
menu_main() {
    local studio_bin="${HOME}/user_scripts/audio/dusky_audio_studio/dusky_audio_studio.py"

    local is_dsp_on=false
    if pgrep -x "dusky_audio_dsp" &>/dev/null; then
        is_dsp_on=true
    fi

    local dsp_status="Off"
    $is_dsp_on && dsp_status="On"

    local -a main_options=(
        "󰓃  Playback Output Devices"
        "  Microphone Input Devices"
        "󰔏  Toggle Dusky Audio DSP  [$dsp_status]"
        "  Open Dusky Audio Studio"
    )

    local choice
    choice=$(printf "%s\n" "${main_options[@]}" | rofi -dmenu -i -p "󰕾  Dusky Audio" -theme-str "$ROFI_THEME_STR" -format s)

    case "$choice" in
        "󰓃  Playback Output Devices"*)
            menu_output
            ;;
        "  Microphone Input Devices"*)
            menu_input
            ;;
        "󰔏  Toggle Dusky Audio DSP"*)
            if [[ -f "$studio_bin" ]]; then
                python3 "$studio_bin" --toggle
            fi
            ;;
        "  Open Dusky Audio Studio"*)
            if [[ -f "$studio_bin" ]]; then
                python3 "$studio_bin" --gui &
            fi
            ;;
    esac
}

# 6. CLI Argument Dispatch
case "${1:-}" in
    -o|--output|output|out)
        menu_output
        ;;
    -i|--input|input|in)
        menu_input
        ;;
    -t|--toggle|toggle)
        "${HOME}/user_scripts/audio/dusky_audio_studio/dusky_audio_studio.py" --toggle
        ;;
    -s|--studio|studio|gui)
        "${HOME}/user_scripts/audio/dusky_audio_studio/dusky_audio_studio.py" --gui &
        ;;
    -h|--help|help)
        cat << "HELP_EOF"
Usage: dusky_in_out_source.sh [FLAG]

Flags:
  -o, --output      Directly open Playback Output Devices menu
  -i, --input       Directly open Microphone Input Devices menu
  -t, --toggle      Toggle Dusky Audio DSP on/off
  -s, --studio      Launch Dusky Audio Studio GUI
  -h, --help        Show this help message

Default (no arguments):
  Opens the Dusky Audio Master Hub menu with all submenus.
HELP_EOF
        ;;
    *)
        menu_main
        ;;
esac

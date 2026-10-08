#!/usr/bin/env bash
#d: Configure battery notification thresholds

set -euo pipefail

#===============================================================================
# CONFIGURATION
#===============================================================================
readonly NOTIFY_SCRIPT="${HOME}/user_scripts/battery/notify/battery_notify.sh"
readonly SERVICE_NAME="dusky_battery.service"
readonly SCRIPT_NAME="${0##*/}"

# Sensible defaults for most users
readonly PRESET_FULL=100
readonly PRESET_LOW=20
readonly PRESET_CRITICAL=10

# Gum colors
readonly C_TEXT="212"
readonly C_ACCENT="99"
readonly C_WARN="208"
readonly C_ERR="196"
readonly C_OK="35"

#===============================================================================
# UTILITY FUNCTIONS
#===============================================================================
declare -i HAS_GUM=0

_check_gum() {
    command -v gum &>/dev/null && HAS_GUM=1 || HAS_GUM=0
}
_check_gum

die() {
    if ((HAS_GUM)); then
        gum style --foreground "$C_ERR" "✗ Error: $1" >&2
    else
        printf '\033[1;31m✗ Error: %s\033[0m\n' "$1" >&2
    fi
    exit 1
}

info() {
    if ((HAS_GUM)); then
        gum style --foreground "$C_ACCENT" "$1"
    else
        printf '\033[1;34m%s\033[0m\n' "$1"
    fi
}

success() {
    if ((HAS_GUM)); then
        gum style --foreground "$C_OK" "✓ $1"
    else
        printf '\033[1;32m✓ %s\033[0m\n' "$1"
    fi
}

warn() {
    if ((HAS_GUM)); then
        gum style --foreground "$C_WARN" "⚠ $1"
    else
        printf '\033[1;33m⚠ %s\033[0m\n' "$1"
    fi
}

is_valid_percent() {
    [[ ${1:-} =~ ^[0-9]{1,4}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 100 ))
}

#===============================================================================
# BATTERY DETECTION
#===============================================================================
check_battery() {
    local info type_file type_val scope present
    # DisplayDevice excludes peripheral batteries and includes system UPS devices.
    if command -v upower >/dev/null &&
        info=$(LC_ALL=C upower --show-info /org/freedesktop/UPower/devices/DisplayDevice 2>/dev/null); then
        [[ $info =~ present:[[:space:]]*yes([[:space:]]|$) &&
           $info =~ power\ supply:[[:space:]]*yes([[:space:]]|$) &&
           $info =~ $'\n'[[:space:]]*(battery|ups)([[:space:]]|$) ]]
        return
    fi
    # Allow configuration while the daemon is unavailable, using kernel metadata.
    for type_file in /sys/class/power_supply/*/type; do
        [[ -r $type_file ]] || continue
        IFS= read -r type_val < "$type_file" || continue
        [[ $type_val == Battery || $type_val == UPS ]] || continue
        scope=System present=1
        if [[ -r ${type_file%type}scope ]]; then
            IFS= read -r scope < "${type_file%type}scope" || continue
        fi
        if [[ -r ${type_file%type}present ]]; then
            IFS= read -r present < "${type_file%type}present" || continue
        fi
        [[ $scope == System && $present == 1 ]] && return 0
    done
    return 1
}

#===============================================================================
# CONFIG FUNCTIONS
#===============================================================================
get_current_value() {
    local var_name="$1"
    local default="$2"

    if [[ ! -f "$NOTIFY_SCRIPT" ]]; then
        printf '%s' "$default"
        return 0
    fi

    local line value
    local pattern='^[[:space:]]*readonly[[:space:]]+'"$var_name"'="\$\{'"$var_name"':-([0-9]{1,4})\}"([[:space:]]*(#.*)?)$'
    while IFS= read -r line; do
        if [[ $line =~ $pattern ]]; then
            value=${BASH_REMATCH[1]}
            if is_valid_percent "$value"; then
                printf '%s' "$((10#$value))"
                return 0
            fi
        fi
    done < "$NOTIFY_SCRIPT"
    printf '%s' "$default"
}

update_config() {
    local full=$1 low=$2 critical=$3
    if ! is_valid_percent "$full" || ! is_valid_percent "$low" || ! is_valid_percent "$critical"; then
        die "Thresholds must be decimal percentages in 1..100"
    fi
    full=$((10#$full)) low=$((10#$low)) critical=$((10#$critical))
    (( critical < low && low < full )) || die "Thresholds must satisfy CRITICAL < LOW < FULL"

    # Reuse the TUI writer's atomic replacement and permission preservation.
    python3 - "$NOTIFY_SCRIPT" "$HOME/user_scripts/dusky_tui" "$full" "$low" "$critical" <<'PYTHON'
import sys
sys.path.insert(0, sys.argv[2])
from python.engines.shell_fallback import ShellFallbackEngine

engine = ShellFallbackEngine(sys.argv[1])
engine.load_state()
keys = ("BATTERY_FULL_THRESHOLD", "BATTERY_LOW_THRESHOLD", "BATTERY_CRITICAL_THRESHOLD")
ok, message, _ = engine.write_batch([
    (key, "DEFAULT", value, "int") for key, value in zip(keys, sys.argv[3:], strict=True)
])
if not ok:
    print(message, file=sys.stderr)
    raise SystemExit(1)
PYTHON
}

restart_service() {
    # Check if service unit exists
    if ! systemctl --user cat "$SERVICE_NAME" &>/dev/null; then
        info "Service '$SERVICE_NAME' not found - skipping restart"
        return 0
    fi

    if systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null ||
       systemctl --user is-failed --quiet "$SERVICE_NAME" 2>/dev/null; then
        if systemctl --user restart "$SERVICE_NAME" 2>/dev/null; then
            success "Service restarted"
        else
            warn "Failed to restart service"
        fi
    else
        info "Service not running (start with: systemctl --user start $SERVICE_NAME)"
    fi
}

#===============================================================================
# USAGE
#===============================================================================
show_usage() {
    cat << EOF
Usage: ${SCRIPT_NAME} [OPTIONS]

Configure battery notification thresholds.

OPTIONS:
    --default    Apply sensible defaults without TUI:
                   • Full Battery Reminder:    ${PRESET_FULL}%
                   • Low Battery Warning:      ${PRESET_LOW}%
                   • Critical (Auto-Suspend):  ${PRESET_CRITICAL}%
    -h, --help   Show this help message

EXAMPLES:
    ${SCRIPT_NAME}           # Interactive TUI mode
    ${SCRIPT_NAME} --default # Apply defaults non-interactively

NOTE: This script requires a system battery or UPS.
      Config path: ${NOTIFY_SCRIPT}
EOF
}

#===============================================================================
# DEFAULT MODE (Non-Interactive)
#===============================================================================
apply_defaults() {
    printf '\n'
    info "Applying default battery thresholds..."
    printf '\n  Full Battery Reminder:     %s%%\n' "$PRESET_FULL"
    printf '  Low Battery Warning:       %s%%\n' "$PRESET_LOW"
    printf '  Critical (Auto-Suspend):   %s%%\n\n' "$PRESET_CRITICAL"

    update_config "$PRESET_FULL" "$PRESET_LOW" "$PRESET_CRITICAL"
    success "Configuration updated"
    printf '\n'
    restart_service
}

#===============================================================================
# TUI MODE
#===============================================================================
ensure_gum() {
    ((HAS_GUM)) || die "gum is required for TUI mode. Use --default or install gum first."
}

show_header() {
    gum style --border normal --margin "1" --padding "1 2" --border-foreground "$C_TEXT" \
        "$(gum style --foreground "$C_TEXT" --bold "BATTERY") $(gum style --foreground "$C_ACCENT" "NOTIFICATIONS")"
}

prompt_value() {
    local current="$1"
    local header="$2"
    local result

    while true; do
        result=$(gum input \
            --placeholder "$current" \
            --value "$current" \
            --header "$header" \
            --header.foreground "$C_ACCENT") || {
            printf '%s' "$current"
            return 0
        }

        if is_valid_percent "$result"; then
            printf '%s' "$((10#$result))"
            return 0
        elif [[ -z "$result" ]]; then
            printf '%s' "$current"
            return 0
        else
            warn "Enter a value between 1 and 100" >&2
        fi
    done
}

run_tui() {
    # Must be interactive terminal
    [[ ! -t 0 || ! -t 1 ]] && die "TUI requires interactive terminal. Use --default flag."
    ensure_gum

    # Load current values
    local CUR_FULL CUR_LOW CUR_CRITICAL
    CUR_FULL=$(get_current_value "BATTERY_FULL_THRESHOLD" "$PRESET_FULL")
    CUR_LOW=$(get_current_value "BATTERY_LOW_THRESHOLD" "$PRESET_LOW")
    CUR_CRITICAL=$(get_current_value "BATTERY_CRITICAL_THRESHOLD" "$PRESET_CRITICAL")

    local NEW_FULL="$CUR_FULL"
    local NEW_LOW="$CUR_LOW"
    local NEW_CRITICAL="$CUR_CRITICAL"
    local choice

    while true; do
        clear || true
        show_header

        choice=$(gum choose \
            --cursor.foreground="$C_TEXT" \
            --selected.foreground="$C_TEXT" \
            --header "Select a threshold to configure:" \
            "1. Full Battery Reminder     [${NEW_FULL}%]   (notify when charging reaches this)" \
            "2. Low Battery Warning       [${NEW_LOW}%]   (show warning notification)" \
            "3. Critical / Auto-Suspend   [${NEW_CRITICAL}%]   (suspend system to save data)" \
            "────────────────────────────────────────────────" \
            "↺ Reset to Defaults" \
            "▶ Apply Changes & Restart Service" \
            "✗ Exit Without Saving") || {
            info "Cancelled."
            exit 0
        }

        case "$choice" in
            *"Full Battery"*)
                NEW_FULL=$(prompt_value "$NEW_FULL" "Notify when charging reaches (%):")
                ;;
            *"Low Battery"*)
                NEW_LOW=$(prompt_value "$NEW_LOW" "Show low battery warning at (%):")
                ;;
            *"Critical"*)
                NEW_CRITICAL=$(prompt_value "$NEW_CRITICAL" "Auto-suspend when battery reaches (%):")
                ;;
            *"Reset"*)
                NEW_FULL="$PRESET_FULL"
                NEW_LOW="$PRESET_LOW"
                NEW_CRITICAL="$PRESET_CRITICAL"

                printf '\n'
                info "Applying defaults..."
                update_config "$NEW_FULL" "$NEW_LOW" "$NEW_CRITICAL"
                success "Defaults applied"
                printf '\n'
                restart_service

                CUR_FULL="$NEW_FULL"
                CUR_LOW="$NEW_LOW"
                CUR_CRITICAL="$NEW_CRITICAL"

                sleep 1.5
                ;;
            *"Apply"*)
                # Validate threshold order: Critical < Low < Full
                local errors=""

                if ((NEW_CRITICAL >= NEW_LOW)); then
                    errors+="  • Critical (${NEW_CRITICAL}%) must be lower than Low (${NEW_LOW}%)\n"
                fi
                if ((NEW_LOW >= NEW_FULL)); then
                    errors+="  • Low (${NEW_LOW}%) must be lower than Full (${NEW_FULL}%)\n"
                fi

                if [[ -n "$errors" ]]; then
                    printf '\n'
                    gum style --border double --border-foreground "$C_WARN" --padding "1" --margin "0 1" \
                        "$(gum style --foreground "$C_WARN" --bold "⚠ INVALID THRESHOLD ORDER")" \
                        "" \
                        "$(printf '%b' "$errors")" \
                        "" \
                        "Expected order: Critical < Low < Full"

                    printf '\n'
                    if ! gum confirm --affirmative="Go Back" --negative="Exit" "Return to the menu?"; then
                        return 0
                    fi
                    continue
                fi

                # Check for actual changes
                if [[ "$NEW_FULL" == "$CUR_FULL" && \
                      "$NEW_LOW" == "$CUR_LOW" && \
                      "$NEW_CRITICAL" == "$CUR_CRITICAL" ]]; then
                    printf '\n'
                    info "No changes detected."
                    sleep 1
                    continue
                fi

                printf '\n'
                info "Updating configuration..."
                update_config "$NEW_FULL" "$NEW_LOW" "$NEW_CRITICAL"
                success "Configuration saved"
                printf '\n'
                restart_service

                CUR_FULL="$NEW_FULL"
                CUR_LOW="$NEW_LOW"
                CUR_CRITICAL="$NEW_CRITICAL"

                printf '\n'
                info "Returning to menu..."
                sleep 1.5
                ;;
            *"Exit"* | *"────"*)
                info "No changes made."
                exit 0
                ;;
        esac
    done
}

#===============================================================================
# MAIN ENTRY POINT
#===============================================================================
main() {
    (( $# <= 1 )) || die "Expected at most one option: --default or --help"
    case "${1:-}" in
        -h|--help) show_usage; return 0 ;;
        ""|--default) ;;
        *) die "Unknown option: $1 (use --help for usage)" ;;
    esac
    if ! check_battery; then
        info "No system battery or UPS available. Skipping configuration."
        return 0
    fi
    [[ -f "$NOTIFY_SCRIPT" ]] || die "Battery notify script not found: $NOTIFY_SCRIPT"
    if [[ ${1:-} == --default ]]; then apply_defaults; else run_tui; fi
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi

#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: warp-toggle.sh
# Description: Robust toggle for Cloudflare WARP with UWSM/Hyprland notifications.
#              Supports --connect and --disconnect flags.
# Author: Elite DevOps
# Environment: Arch Linux / Hyprland / UWSM
# Dependencies: warp-cli, libnotify (notify-send) [optional]
# -----------------------------------------------------------------------------

# --- Strict Mode ---
set -euo pipefail

# --- Configuration ---
readonly APP_NAME="Cloudflare WARP"
readonly TIMEOUT_SEC=10
readonly ICON_CONN="network-vpn"
readonly ICON_DISC="network-offline"
readonly ICON_WAIT="network-transmit-receive"
readonly ICON_ERR="dialog-error"

# --- Runtime Checks ---
# Cache notify-send availability once to avoid repetitive syscalls
HAS_NOTIFY=0
command -v notify-send &>/dev/null && HAS_NOTIFY=1
readonly HAS_NOTIFY

# --- Styling (ANSI Colors with TTY detection) ---
if [[ -t 1 ]]; then
    readonly C_RESET=$'\033[0m' C_BOLD=$'\033[1m'
    readonly C_GREEN=$'\033[1;32m' C_BLUE=$'\033[1;34m'
    readonly C_RED=$'\033[1;31m' C_YELLOW=$'\033[1;33m'
else
    readonly C_RESET='' C_BOLD='' C_GREEN='' C_BLUE='' C_RED='' C_YELLOW=''
fi

# --- Logging Functions ---
log_info()    { printf "%s[INFO]%s %s\n" "$C_BLUE" "$C_RESET" "${1:-}"; }
log_success() { printf "%s[OK]%s   %s\n" "$C_GREEN" "$C_RESET" "${1:-}"; }
log_warn()    { printf "%s[WARN]%s %s\n" "$C_YELLOW" "$C_RESET" "${1:-}" >&2; }
log_error()   { printf "%s[ERR]%s  %s\n" "$C_RED" "$C_RESET" "${1:-}" >&2; }

# --- Notification Helper ---
notify_user() {
    (( HAS_NOTIFY )) || return 0
    
    local title="${1:-Notification}"
    local message="${2:-}"
    local urgency="${3:-low}"
    local icon="${4:-$ICON_WAIT}"
    
    # 1. '--' guards against title being parsed as a flag
    # 2. '|| true' prevents crash if notification daemon is dead/restarting
    notify-send -u "$urgency" -a "$APP_NAME" -i "$icon" -- "$title" "$message" 2>/dev/null || true
}

# --- Core Logic ---

get_warp_status() {
    local output status
    output=$(warp-cli status 2>/dev/null) || return 1
    
    # Robust awk: uses [[:space:]] to catch tabs, spaces, and potential \r
    status=$(awk -F': ' '/Status update/ {
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)
        print $2
        exit
    }' <<< "$output")

    # Fail if status is empty to prevent logic errors
    if [[ -n "$status" ]]; then
        printf '%s' "$status"
        return 0
    fi
    return 1
}

wait_for_connection() {
    local timer=0 
    local current_state
    
    log_info "Initiating connection sequence..."
    notify_user "Connecting..." "Establishing secure tunnel." "normal" "$ICON_WAIT"

    if ! warp-cli connect &>/dev/null; then
        log_error "Failed to send connect command."
        notify_user "Error" "Failed to send connect command." "critical" "$ICON_ERR"
        return 1
    fi

    while (( timer < TIMEOUT_SEC )); do
        current_state=$(get_warp_status) || current_state="Unknown"

        if [[ "$current_state" == "Connected" ]]; then
            log_success "WARP is now Connected."
            notify_user "Connected" "Secure tunnel active." "normal" "$ICON_CONN"
            return 0
        fi

        sleep 1
        # Pre-increment (++timer) avoids 'set -e' exit trigger on 0
        (( ++timer ))
    done

    log_error "Connection timed out after ${TIMEOUT_SEC}s."
    notify_user "Timeout" "Failed to connect within ${TIMEOUT_SEC} seconds." "critical" "$ICON_ERR"
    return 1
}

disconnect_warp() {
    log_info "Disconnecting..."
    
    if warp-cli disconnect &>/dev/null; then
        log_success "Disconnected successfully."
        notify_user "Disconnected" "Secure tunnel closed." "low" "$ICON_DISC"
        return 0
    else
        log_error "Failed to disconnect."
        notify_user "Error" "Failed to disconnect WARP." "critical" "$ICON_ERR"
        return 1
    fi
}

show_help() {
    # ${0##*/} is a faster, pure-bash alternative to $(basename "$0")
    cat <<EOF
Usage: ${0##*/} [OPTIONS]

Options:
  (no args)      Toggle connection state
  --connect      Force connection (idempotent)
  --disconnect   Force disconnection (idempotent)
  -h, --help     Show this message
EOF
}

main() {
    # Dependency Check
    if ! command -v warp-cli &>/dev/null; then
        log_error "warp-cli not found. Please install 'cloudflare-warp-bin'."
        exit 1
    fi

    # Argument Parsing
    local action="toggle"
    
    while (( $# > 0 )); do
        case "$1" in
            --connect)
                action="connect"
                ;;
            --disconnect)
                action="disconnect"
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
        shift
    done

    # Get Status
    local status
    status=$(get_warp_status) || status="Unknown"

    # Execution Logic
    case "$action" in
        "connect")
            if [[ "$status" == "Connected" ]]; then
                log_success "Already Connected. No action taken."
            else
                wait_for_connection
            fi
            ;;
            
        "disconnect")
            if [[ "$status" == "Disconnected" ]]; then
                log_success "Already Disconnected. No action taken."
            else
                disconnect_warp
            fi
            ;;
            
        "toggle")
            log_info "Current Status: ${C_BOLD}${status}${C_RESET}"
            case "$status" in
                "Connected"|"Connecting")
                    disconnect_warp
                    ;;
                "Disconnected")
                    wait_for_connection
                    ;;
                *)
                    log_warn "Unknown status detected: '$status'. Attempting to connect."
                    wait_for_connection
                    ;;
            esac
            ;;
    esac
}

main "$@"

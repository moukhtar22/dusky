#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Description: Deterministic state manager for Waybar on Hyprland/UWSM.
#              Supports targeted states (on/off/toggle) and pass-through args.
# -----------------------------------------------------------------------------

set -euo pipefail

# --- Configuration & Constants ---
readonly APP_NAME="waybar"
readonly TIMEOUT_SEC=5
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/${APP_NAME}_state.lock"

# --- Terminal-Aware Colors ---
if [[ -t 2 ]]; then
    readonly C_RED=$'\033[0;31m'
    readonly C_GREEN=$'\033[0;32m'
    readonly C_BLUE=$'\033[0;34m'
    readonly C_YELLOW=$'\033[0;33m'
    readonly C_RESET=$'\033[0m'
else
    readonly C_RED=''
    readonly C_GREEN=''
    readonly C_BLUE=''
    readonly C_YELLOW=''
    readonly C_RESET=''
fi

# --- Logging ---
log_info()    { printf '%s[INFO]%s %s\n' "${C_BLUE}" "${C_RESET}" "$*" >&2; }
log_success() { printf '%s[OK]%s %s\n' "${C_GREEN}" "${C_RESET}" "$*" >&2; }
log_warn()    { printf '%s[WARN]%s %s\n' "${C_YELLOW}" "${C_RESET}" "$*" >&2; }
log_err()     { printf '%s[ERROR]%s %s\n' "${C_RED}" "${C_RESET}" "$*" >&2; }

# --- Help Menu ---
print_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS] [-- [WAYBAR_ARGS]]

A highly deterministic state manager for Waybar.

Options:
  -t, --toggle    Toggle Waybar on/off (Default behavior)
  --on            Explicitly start Waybar (No-op if already running)
  --off           Explicitly stop Waybar (No-op if not running)
  -h, --help      Show this help message

Any arguments not recognized by this script will be passed directly to Waybar.
Example: $(basename "$0") --on -c ~/.config/waybar/alt_config.json
EOF
}

# --- Argument Parsing ---
ACTION="toggle" # Default action
WAYBAR_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            print_help
            exit 0
            ;;
        -t|--toggle)
            ACTION="toggle"
            shift
            ;;
        --on)
            ACTION="on"
            shift
            ;;
        --off)
            ACTION="off"
            shift
            ;;
        --)
            shift
            WAYBAR_ARGS+=("$@")
            break
            ;;
        -*)
            # Assume unknown flags (e.g. -c, -s) belong to Waybar
            WAYBAR_ARGS+=("$1")
            shift
            ;;
        *)
            WAYBAR_ARGS+=("$1")
            shift
            ;;
    esac
done

# --- Core Functions ---
is_running() {
    # -u ensures we only see our user's processes.
    # -x ensures exact binary name match (prevents matching this script).
    pgrep -u "$UID" -x "${APP_NAME}" >/dev/null 2>&1
}

start_waybar() {
    if is_running; then
        log_warn "${APP_NAME} is already running. Ignoring --on request."
        return 0
    fi

    log_info "Starting ${APP_NAME}..."

    # systemd-run strategy for a clean environment
    if command -v systemd-run >/dev/null 2>&1; then
        local unit_name="${APP_NAME}-mgr-${EPOCHSECONDS}-$$"
        
        # Fixed array expansion bug: "${WAYBAR_ARGS[@]}" correctly evaluates to 0 args if empty
        # Added --collect to prevent systemd transient unit memory leaks over multiple toggles
        if systemd-run --user --quiet --collect --unit="${unit_name}" -- "${APP_NAME}" "${WAYBAR_ARGS[@]}" >/dev/null 2>&1; then
            log_success "${APP_NAME} launched (systemd: ${unit_name})"
            return 0
        else
            log_err "systemd-run failed; falling back to setsid."
        fi
    fi

    # Fallback strategy
    log_info "Attempting fallback launch (setsid)..."
    (
        unset XDG_ACTIVATION_TOKEN DESKTOP_STARTUP_ID
        setsid "${APP_NAME}" "${WAYBAR_ARGS[@]}" </dev/null >/dev/null 2>&1 &
    )
    log_success "${APP_NAME} launched (fallback mode)."
}

stop_waybar() {
    if ! is_running; then
        log_warn "${APP_NAME} is not running. Ignoring --off request."
        return 0
    fi

    log_info "Shutting down ${APP_NAME}..."
    pkill -u "$UID" -x "${APP_NAME}" >/dev/null 2>&1 || true

    # High-efficiency polling (Checks every 0.1s for fast exit)
    for (( i = 0; i < TIMEOUT_SEC * 10; i++ )); do
        if ! is_running; then
            log_success "${APP_NAME} successfully closed."
            return 0
        fi
        sleep 0.1
    done

    # Force kill if process is hung
    if is_running; then
        log_err "Process hung. Sending SIGKILL..."
        pkill -9 -u "$UID" -x "${APP_NAME}" >/dev/null 2>&1 || true
        log_success "${APP_NAME} forcefully closed."
    fi
}

# --- Preflight & Concurrency Checks ---
(( EUID != 0 )) || { log_err "Do not run as root."; exit 1; }
command -v "${APP_NAME}" >/dev/null 2>&1 || { log_err "${APP_NAME} binary not found."; exit 1; }

# Atomic Concurrency Lock
exec 9>"${LOCK_FILE}"
flock -n 9 || { log_err "Another instance is actively managing state. Dropping request."; exit 0; }

# --- Execution State Machine ---
case "$ACTION" in
    on)
        start_waybar
        ;;
    off)
        stop_waybar
        ;;
    toggle)
        if is_running; then
            stop_waybar
        else
            start_waybar
        fi
        ;;
esac

exit 0

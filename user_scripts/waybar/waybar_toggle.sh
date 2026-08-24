#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Description: State-of-the-Art Waybar State Manager for Arch Linux (2026).
#              Supports Dusky Run, systemd transient scopes/units,
#              SIGUSR1 fast visibility toggle, and deterministic process lifecycle.
# -----------------------------------------------------------------------------

set -euo pipefail

# --- Configuration & Constants ---
readonly APP_NAME="waybar"
readonly TIMEOUT_SEC=3
readonly LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/${APP_NAME}_${UID}_state.lock"

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

State-of-the-Art Waybar State Manager for Arch Linux.

Options:
  -t, --toggle      Toggle Waybar process on/off (Default behavior)
  -s, --signal      Fast-toggle Waybar visibility via SIGUSR1 (Zero process overhead)
  --on              Explicitly start Waybar (No-op if already running)
  --off             Explicitly stop Waybar (No-op if not running)
  -h, --help        Show this help message

Any additional arguments will be passed directly to Waybar when starting.
Example: $(basename "$0") --on -- -c ~/.config/waybar/config.json
EOF
}

# --- Argument Parsing ---
ACTION="toggle"
SIGNAL_MODE=false
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
        -s|--signal)
            SIGNAL_MODE=true
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
    pgrep -u "$UID" -x "${APP_NAME}" >/dev/null 2>&1
}

start_waybar() {
    if is_running; then
        log_warn "${APP_NAME} is already running. Ignoring --on request."
        return 0
    fi

    log_info "Starting ${APP_NAME}..."

    # Method 1: Dusky Run (Custom System Launcher with OOM Score Elevation & Transient Scopes)
    # 9>&- prevents the background process from inheriting the lock file descriptor
    if command -v dusky-run >/dev/null 2>&1; then
        if dusky-run "${APP_NAME}" "${WAYBAR_ARGS[@]}" 9>&- >/dev/null 2>&1 & then
            log_success "${APP_NAME} launched (dusky-run)."
            return 0
        fi
    fi

    # Method 2: systemd-run transient scope/unit (Zero-fork nanosecond naming)
    if command -v systemd-run >/dev/null 2>&1; then
        local ts="${EPOCHREALTIME//./}"
        local unit_name="${APP_NAME}-mgr-${ts:-$$}"
        if systemd-run --user --quiet --collect --unit="${unit_name}" -- "${APP_NAME}" "${WAYBAR_ARGS[@]}" 9>&- >/dev/null 2>&1; then
            log_success "${APP_NAME} launched (systemd: ${unit_name})"
            return 0
        fi
    fi

    # Method 3: Fallback detached session via setsid
    (
        exec 9>&-
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
    pkill -SIGTERM -u "$UID" -x "${APP_NAME}" >/dev/null 2>&1 || true

    # High-precision 50ms polling loop
    local iterations=$(( TIMEOUT_SEC * 20 ))
    for (( i = 0; i < iterations; i++ )); do
        if ! is_running; then
            log_success "${APP_NAME} successfully closed."
            return 0
        fi
        sleep 0.05
    done

    # Force kill if hung after grace period
    if is_running; then
        log_err "Process hung after ${TIMEOUT_SEC}s. Sending SIGKILL..."
        pkill -SIGKILL -u "$UID" -x "${APP_NAME}" >/dev/null 2>&1 || true
        log_success "${APP_NAME} forcefully closed."
    fi
}

toggle_signal() {
    if is_running; then
        pkill -SIGUSR1 -u "$UID" -x "${APP_NAME}" >/dev/null 2>&1
        log_success "Sent SIGUSR1 visibility toggle to ${APP_NAME}."
    else
        start_waybar
    fi
}

# --- Preflight & Concurrency Checks ---
(( EUID != 0 )) || { log_err "Do not run as root."; exit 1; }
command -v "${APP_NAME}" >/dev/null 2>&1 || { log_err "${APP_NAME} binary not found."; exit 1; }

# Atomic Concurrency Lock
exec 9>"${LOCK_FILE}"
flock -n 9 || { log_err "Another instance is actively managing state. Dropping request."; exit 0; }

# --- Execution ---
if [[ "${SIGNAL_MODE}" == true ]]; then
    toggle_signal
    exit 0
fi

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

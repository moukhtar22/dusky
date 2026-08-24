#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: reload_llm_side_panal.sh
# Purpose: Gracefully manages & restarts the Dusky LLM Side Panel lifecycle.
# -----------------------------------------------------------------------------

set -euo pipefail

readonly APP_NAME="Dusky LLM Side Panel"
readonly SERVICE_NAME="dusky_llm.service"
readonly PROCESS_PATTERN='dusky_llm\.py'
readonly GUI_SCRIPT_PATH="${HOME}/user_scripts/llm/llm_side_panal/dusky_llm.py"

log_info() { printf '\e[34m[INFO]\e[0m %s\n' "$*"; }
log_ok()   { printf '\e[32m[OK]\e[0m %s\n' "$*"; }
log_warn() { printf '\e[33m[WARN]\e[0m %s\n' "$*" >&2; }
log_err()  { printf '\e[31m[ERR]\e[0m %s\n' "$*" >&2; }

terminate_existing() {
    local pids
    mapfile -t pids < <(pgrep -f "$PROCESS_PATTERN" || true)
    if ((${#pids[@]} > 0)); then
        log_info "Terminating running instances (PIDs: ${pids[*]})..."
        kill -TERM "${pids[@]}" 2>/dev/null || true
        sleep 0.2
        kill -KILL "${pids[@]}" 2>/dev/null || true
    fi
}

start_service() {
    log_info "Starting systemd service: ${SERVICE_NAME}"
    systemctl --user reset-failed "$SERVICE_NAME" 2>/dev/null || true
    if systemctl --user start "$SERVICE_NAME"; then
        log_ok "Service ${SERVICE_NAME} started successfully."
    else
        log_err "Failed to start service ${SERVICE_NAME}."
        return 1
    fi
}

activate_ui() {
    log_info "Activating UI window via D-Bus..."
    if command -v gdbus &>/dev/null; then
        gdbus call --session --dest org.dusky.llm \
                   --object-path /org/dusky/llm \
                   --method org.freedesktop.Application.Activate "{}" &>/dev/null || true
    fi
}

main() {
    log_info "Reloading ${APP_NAME}..."
    terminate_existing
    start_service || true
    sleep 0.3
    activate_ui
    log_ok "Reload complete."
}

main "$@"

#!/usr/bin/env bash
set -euo pipefail

SERVICE="dusky_visualizer.service"

: "${XDG_CONFIG_HOME:=$HOME/.config}"
CTL_FILE="${XDG_CONFIG_HOME}/dusky/settings/way_layers/visualizer/visualizer.ctl"

fifo_ready() {
    [[ -p "$CTL_FILE" ]]
}

wait_for_fifo() {
    local i
    for ((i = 0; i < 50; ++i)); do
        if fifo_ready; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

send_cmd() {
    local cmd="$1"

    if ! wait_for_fifo; then
        echo "Control FIFO not available: $CTL_FILE" >&2
        return 1
    fi

    printf '%s\n' "$cmd" 1<> "$CTL_FILE"
}

main() {
    if ! systemctl --user is-active --quiet "$SERVICE"; then
        rm -f -- "$CTL_FILE"
        systemctl --user reset-failed "$SERVICE" 2>/dev/null || true
        systemctl --user start "$SERVICE"
    fi

    send_cmd overlay
}

main "$@"

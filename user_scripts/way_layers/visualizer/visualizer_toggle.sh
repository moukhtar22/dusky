#!/usr/bin/env bash
set -euo pipefail

SERVICE="dusky_visualizer.service"

: "${XDG_CONFIG_HOME:=$HOME/.config}"
CONFIG_FILE="${XDG_CONFIG_HOME}/dusky/settings/way_layers/visualizer/visualizer.json"
CTL_FILE="${XDG_CONFIG_HOME}/dusky/settings/way_layers/visualizer/visualizer.ctl"

PYTHON="$(command -v python3 || true)"
[[ -n "$PYTHON" ]] || PYTHON="/usr/bin/python3"

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

    # Exact line-based IPC command.
    printf '%s\n' "$cmd" 1<> "$CTL_FILE"
}

config_enabled() {
    # Return 0 if enabled or unknown, 1 if explicitly disabled.
    [[ -f "$CONFIG_FILE" ]] || return 0
    [[ -x "$PYTHON" ]] || return 0

    "$PYTHON" - "$CONFIG_FILE" <<'PY'
import json
import sys
from pathlib import Path

try:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        sys.exit(0)

    for key, value in data.items():
        if str(key).strip().lower() != "enabled":
            continue

        if isinstance(value, bool):
            sys.exit(0 if value else 1)

        if isinstance(value, str):
            sys.exit(0 if value.strip().lower() in {"1", "true", "yes", "on"} else 1)

        sys.exit(0 if value else 1)

except Exception:
    pass

sys.exit(0)
PY
}

main() {
    if systemctl --user is-active --quiet "$SERVICE"; then
        send_cmd toggle
        return
    fi

    # Service is not active. Remove a possible stale FIFO so we can wait
    # for the daemon to create a fresh one.
    rm -f -- "$CTL_FILE"

    # Clear a failed state if systemd put it there.
    systemctl --user reset-failed "$SERVICE" 2>/dev/null || true

    systemctl --user start "$SERVICE"

    if ! wait_for_fifo; then
        echo "Service started, but FIFO did not appear: $CTL_FILE" >&2
        exit 1
    fi

    # If the daemon started but the saved config says disabled, enable it.
    if ! config_enabled; then
        send_cmd toggle
    fi
}

main "$@"

#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: toggle_llm_side_panal.sh
# Purpose: Toggles the Dusky LLM Side Panel via D-Bus activation or direct run.
# -----------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUI_SCRIPT="${SCRIPT_DIR}/dusky_llm.py"

# Try D-Bus activation first
if command -v gdbus &>/dev/null; then
    if gdbus call --session --dest org.dusky.llm \
                 --object-path /org/dusky/llm \
                 --method org.freedesktop.Application.Activate "{}" &>/dev/null; then
        exit 0
    fi
fi

# Fallback to direct execution
if [[ -x "$GUI_SCRIPT" ]]; then
    exec "$GUI_SCRIPT"
else
    exec python3 "$GUI_SCRIPT"
fi

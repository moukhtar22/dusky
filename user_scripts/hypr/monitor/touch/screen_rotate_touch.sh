#!/usr/bin/env bash

# ==============================================================================
#  HYPRLAND V0.55+ UNIVERSAL DISPLAY & TOUCH ROTATION UTILITY
#  Description: Context-aware rotation utilizing Hyprland's native Lua evaluator.
#               Automatically maps global touch inputs to the focused monitor.
# ==============================================================================

# 1. Strict Mode & Environment Setup
# ------------------------------------------------------------------------------
set -euo pipefail

# Ensure XDG_RUNTIME_DIR is set for safe, user-specific lockfiles
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/hypr_rotate_$(id -u)}"
mkdir -p "$RUNTIME_DIR"
LOCKFILE="$RUNTIME_DIR/hypr_rotate.lock"

# 2. Dependency Checks
# ------------------------------------------------------------------------------
command -v jq >/dev/null 2>&1 || { echo "Error: 'jq' is required but not installed."; exit 1; }
command -v hyprctl >/dev/null 2>&1 || { echo "Error: 'hyprctl' is required but not installed."; exit 1; }

# 3. Usage & Argument Parsing
# ------------------------------------------------------------------------------
print_usage() {
    echo "Usage: ${0##*/} [+90|-90]"
    echo "  +90 : Rotate 90 degrees clockwise"
    echo "  -90 : Rotate 90 degrees counter-clockwise"
    exit 1
}

if [[ $# -ne 1 ]]; then
    print_usage
fi

DIRECTION=0
case "$1" in
    "+90") DIRECTION=1 ;;
    "-90") DIRECTION=-1 ;;
    *) print_usage ;;
esac

# 4. Debounce (Prevent 180-degree jumps on rapid/held keybinds)
# ------------------------------------------------------------------------------
NOW=$(date +%s%3N)
LAST_RUN=$(cat "$LOCKFILE" 2>/dev/null || echo "0")

if (( NOW - LAST_RUN < 500 )); then
    exit 0 # Exit silently if triggered within 500ms
fi
echo "$NOW" > "$LOCKFILE"

# 5. IPC State Extraction (Source of Truth)
# ------------------------------------------------------------------------------
MON_STATE=$(hyprctl monitors -j 2>/dev/null) || { echo "Error: Failed to query Hyprland IPC."; exit 1; }

# Grab focused monitor, fallback to the first monitor if none are focused
MONITOR_JSON=$(echo "$MON_STATE" | jq -c '.[] | select(.focused==true)')
if [[ -z "$MONITOR_JSON" ]]; then
    MONITOR_JSON=$(echo "$MON_STATE" | jq -c '.[0]')
fi

[[ -z "$MONITOR_JSON" || "$MONITOR_JSON" == "null" ]] && { echo "Error: No monitors detected."; exit 1; }

# Extract core hardware values
MONITOR=$(echo "$MONITOR_JSON" | jq -r '.name')
SCALE=$(echo "$MONITOR_JSON" | jq -r '.scale')
X=$(echo "$MONITOR_JSON" | jq -r '.x')
Y=$(echo "$MONITOR_JSON" | jq -r '.y')
CURRENT_TRANSFORM=$(echo "$MONITOR_JSON" | jq -r '.transform')

# Calculate new transform safely (0-3 range)
NEW_TRANSFORM=$(( (CURRENT_TRANSFORM + DIRECTION + 4) % 4 ))

# 6. Execution via Lua Evaluator (v0.55+ Native)
# ------------------------------------------------------------------------------
# We use 'preferred' resolution to prevent swapped-axis bugs on non-standard aspect ratios
hyprctl eval "hl.monitor({ output = \"$MONITOR\", mode = \"preferred\", position = \"${X}x${Y}\", scale = $SCALE, transform = $NEW_TRANSFORM })" >/dev/null

# Apply transform globally to all touch devices and map them to the active monitor
hyprctl eval "hl.config({ input = { touchdevice = { transform = $NEW_TRANSFORM, output = \"$MONITOR\" } } })" >/dev/null

# 7. Notification (Optional, fails gracefully if notify-send is missing)
# ------------------------------------------------------------------------------
if command -v notify-send >/dev/null 2>&1; then
    notify-send -a "Hyprland" -t 1500 "Display Rotated" "Monitor: $MONITOR\nTransform: $NEW_TRANSFORM" -h string:x-canonical-private-synchronous:display-rotate
fi

exit 0

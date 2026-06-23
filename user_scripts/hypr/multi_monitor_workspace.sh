#!/usr/bin/env bash
# ==============================================================================
# multi_monitor_workspace.sh  —  Context-Aware / Banked Workspace Dispatcher
# Compatible with Hyprland 0.55+ (Lua config)
#
# Concept: monitors are sorted left-to-right by X position and assigned a
# "bank" of 10 workspaces each.
#   Monitor 0 (leftmost)  → workspaces  1–10
#   Monitor 1             → workspaces 11–20
#   Monitor 2             → workspaces 21–30
#   …and so on.
#
# Pressing SUPER+1 always means "workspace 1 for this monitor" regardless of
# which physical screen is focused.
#
# Usage:
#   multi_monitor_workspace.sh workspace            <1-10>
#   multi_monitor_workspace.sh movetoworkspace      <1-10>
#   multi_monitor_workspace.sh movetoworkspacesilent <1-10>
#
# REQUIRES: hyprctl, jq
# ==============================================================================

set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────
ACTION="${1:-}"
REQUESTED_WS="${2:-}"

if [[ -z "$ACTION" || -z "$REQUESTED_WS" ]]; then
    printf 'Usage: %s <workspace|movetoworkspace|movetoworkspacesilent> <1-10>\n' \
        "${0##*/}" >&2
    exit 1
fi

if ! [[ "$REQUESTED_WS" =~ ^([1-9]|10)$ ]]; then
    printf 'Error: workspace number must be 1–10, got: %s\n' "$REQUESTED_WS" >&2
    exit 1
fi

# ── Dependency check ──────────────────────────────────────────────────────────
if ! command -v jq >/dev/null 2>&1; then
    printf 'Error: jq is required but not installed.\n' >&2
    exit 1
fi

# ── Determine active monitor and its bank offset ──────────────────────────────
monitor_data=$(hyprctl -j monitors 2>/dev/null) || {
    printf 'Error: hyprctl failed — is Hyprland running?\n' >&2
    exit 1
}

# Name of the currently-focused monitor
focused_name=$(printf '%s' "$monitor_data" \
    | jq -r '.[] | select(.focused == true) | .name')

if [[ -z "$focused_name" ]]; then
    printf 'Error: could not determine focused monitor.\n' >&2
    exit 1
fi

# Sort monitors by their X offset, then find the index of the focused one.
# Monitors with the same X are sorted by Y (top-to-bottom as tiebreak).
monitor_index=$(printf '%s' "$monitor_data" \
    | jq -r --arg name "$focused_name" '
        sort_by(.x, .y)
        | to_entries[]
        | select(.value.name == $name)
        | .key
    ')

if ! [[ "$monitor_index" =~ ^[0-9]+$ ]]; then
    printf 'Error: could not determine monitor index (got: "%s").\n' \
        "$monitor_index" >&2
    exit 1
fi

# Bank offset: index 0 → offset 0, index 1 → offset 10, etc.
bank_offset=$(( monitor_index * 10 ))
target_ws=$(( bank_offset + REQUESTED_WS ))

# ── Dispatch ──────────────────────────────────────────────────────────────────
# hyprctl dispatch in Hyprland 0.55+ (Lua config) requires a Lua expression.
# The old "hyprctl dispatch workspace N" form no longer works.
#
# Native Lua dispatcher forms used below:
#   focus({ workspace = "N" })                        → switch to workspace
#   window.move({ workspace = "N" })                  → move window, follow focus
#   window.move({ workspace = "N", follow = false })  → move window, stay here

case "$ACTION" in
    workspace)
        hyprctl dispatch "hl.dsp.focus({ workspace = \"${target_ws}\" })"
        ;;
    movetoworkspace)
        hyprctl dispatch "hl.dsp.window.move({ workspace = \"${target_ws}\" })"
        ;;
    movetoworkspacesilent)
        hyprctl dispatch \
            "hl.dsp.window.move({ workspace = \"${target_ws}\", follow = false })"
        ;;
    *)
        printf 'Error: unknown action "%s".\n' "$ACTION" >&2
        printf 'Valid actions: workspace, movetoworkspace, movetoworkspacesilent\n' >&2
        exit 1
        ;;
esac

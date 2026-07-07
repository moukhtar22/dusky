#!/usr/bin/env bash
# waybar-net: Minimal JSON output for Waybar (Zero-Fork Edition)

# 1. OPTIMIZATION: Use ${UID} (Bash variable) instead of $(id -u) (Process fork)
STATE_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID}}/waybar-net"
STATE_FILE="$STATE_DIR/state"
HEARTBEAT_FILE="$STATE_DIR/heartbeat"
PID_FILE="$STATE_DIR/daemon.pid"

# Defaults
UNIT="-" UP="-" DOWN="-" CLASS="network-disconnected"

# Read state (fast: tmpfs)
# Added "|| true" to prevent exit on read failure if file is being rotated
[[ -r "$STATE_FILE" ]] && read -r UNIT UP DOWN CLASS < "$STATE_FILE" || true

# Signal daemon via heartbeat
[[ -d "$STATE_DIR" ]] || mkdir -p "$STATE_DIR"
: > "$HEARTBEAT_FILE"

# OPTIMIZATION: Only kill if PID file exists and process is actually running
if [[ -r "$PID_FILE" ]]; then
    read -r DAEMON_PID < "$PID_FILE"
    # 0 signal checks if process exists without killing it
    if [[ -n "$DAEMON_PID" ]] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        kill -USR1 "$DAEMON_PID" 2>/dev/null || true
    fi
fi

# Formatter for Horizontal Mode (Original Unpadded Behavior)
fmt_h() {
    local -n _out=$1
    local s="${2:--}"
    local len="${#s}"
    
    if (( len == 1 )); then _out=" $s "
    elif (( len == 2 )); then _out=" $s"
    else _out="${s:0:3}"
    fi
}

# Formatter for Vertical Mode (Strict alignment matching update_counter.sh)
fmt_v() {
    local -n _out=$1
    local s="${2:--}"
    local len="${#s}" 
    
    if (( len >= 3 )); then
        _out="${s:0:3}"
    elif (( len == 2 )); then
        # Natively pass literal JSON unicode escape so Waybar parses it perfectly
        _out="\\u2005${s}\\u2005"
    elif (( len == 1 )); then
        _out=" ${s} "
    else
        _out="   "
    fi
}

# Tooltip
if [[ "$CLASS" == "network-disconnected" ]]; then
    TT="Disconnected"
else
    TT="Upload: ${UP} ${UNIT}/s\\nDownload: ${DOWN} ${UNIT}/s"
fi

# Output Selection
case "${1:-}" in
    --vertical|vertical)
        fmt_v up_fmt "$UP"
        fmt_v unit_fmt "$UNIT"
        fmt_v down_fmt "$DOWN"
        TEXT="${up_fmt}\\n${unit_fmt}\\n${down_fmt}" 
        ;;
    --horizontal|horizontal)
        fmt_h up_fmt "$UP"
        fmt_h unit_fmt "$UNIT"
        fmt_h down_fmt "$DOWN"
        TEXT="${up_fmt} ${unit_fmt} ${down_fmt}" 
        ;;
    unit)
        fmt_h unit_fmt "$UNIT"
        TEXT="$unit_fmt"
        ;;
    up|upload)
        fmt_h up_fmt "$UP"
        TEXT="$up_fmt"
        ;;
    down|download)
        fmt_h down_fmt "$DOWN"
        TEXT="$down_fmt"
        ;;
    *)
        printf '{%s}\n' ""
        exit 0
        ;;
esac

printf '{"text":"%s","class":"%s","tooltip":"%s"}\n' "$TEXT" "$CLASS" "$TT"

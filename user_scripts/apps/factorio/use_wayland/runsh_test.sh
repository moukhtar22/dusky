#!/usr/bin/env bash
# Test: launching Factorio via the user's run.sh launcher (with the shim fix).
GAME=/mnt/zram1/Factorio_2.1.14
RUNSH=/home/dusk/user_scripts/apps/factorio/run.sh
rm -f /tmp/eglfix.log /tmp/runsh_test.log

echo "=== launching via run.sh (game dir: $GAME) ==="
"$RUNSH" "$GAME" > /tmp/runsh_test.log 2>&1 &
RUNSH_PID=$!
sleep 55

echo "=== game alive? ==="
pgrep -x factorio | head -1 || echo "NOT RUNNING"

echo "=== shim loaded in this launch? (LD_PRELOAD through run.sh) ==="
grep -E 'shim loaded|CreateContext|ensure_current|bind recorded' /tmp/eglfix.log 2>/dev/null | head -6

echo "=== boot progress from run.sh output ==="
grep -vE 'INVALID_OPERATION encountered in frame' /tmp/runsh_test.log | grep -E 'Video driver|Initialised OpenGL|Factorio initialised|Failed to create shader|Error|Steam Storage' | head -8

echo "=== screenshot ==="
timeout 10 grim -o eDP-1 /tmp/runsh_shot.png 2>/dev/null
convert /tmp/runsh_shot.png -crop 1280x720+0+0 -format 'mean=%[fx:mean] stddev=%[fx:standard_deviation] colors=%k\n' info: 2>/dev/null

echo "=== GL error count (0 = clean) ==="
grep -c 'INVALID_OPERATION encountered in frame' /tmp/runsh_test.log 2>/dev/null || true

echo "=== cleanup ==="
pkill -9 -x factorio 2>/dev/null
sleep 2
pgrep -x factorio >/dev/null && echo "WARN still running" || echo "game stopped"
echo "done"

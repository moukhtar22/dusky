#!/usr/bin/env bash
# Deep A-to-Z test: full game session on native Wayland with the shim fix.
GAME=/mnt/zram1/Factorio_2.1.14
cd "$GAME" || exit 1
rm -f /tmp/eglfix.log /tmp/deep_test.log

echo "=== launch via start.n.sh (sandboxed) ==="
./start.n.sh > /tmp/deep_test.log 2>&1 &
LAUNCH_PID=$!
sleep 45

echo "=== boot evidence ==="
grep -vE 'INVALID_OPERATION encountered in frame' /tmp/deep_test.log | grep -E 'Video driver|Initialised OpenGL|Factorio initialised|Steam Storage|Failed to create shader' | head -6

echo "=== shim events ==="
grep -E 'CreateContext|bind recorded|ensure_current' /tmp/eglfix.log 2>/dev/null | head -4

echo "=== drive the menu: Down + Enter to load the save ==="
timeout 10 hyprctl dispatch focuswindow "class:com.factorio.Factorio" >/dev/null 2>&1
sleep 1
timeout 10 wtype -k Down -k Return 2>/dev/null
sleep 30

echo "=== world load evidence ==="
grep -vE 'INVALID_OPERATION encountered in frame' /tmp/deep_test.log | grep -iE 'level.dat|Map version|Scenario|mipmaps uploaded|Loading blueprint|Error' | tail -6

echo "=== in-game screenshot analysis ==="
timeout 10 grim -o eDP-1 /tmp/deep_shot.png 2>/dev/null
convert /tmp/deep_shot.png -crop 1280x720+0+0 -format 'mean=%[fx:mean] stddev=%[fx:standard_deviation] colors=%k\n' info: 2>/dev/null

echo "=== GL errors in session (0 = clean) ==="
grep -c 'INVALID_OPERATION encountered in frame' /tmp/deep_test.log 2>/dev/null || true

echo "=== more input: navigate around (WASD+zoom) and screenshot again ==="
timeout 10 wtype -k w -d 100 -k a -d 100 -k d -d 100 -k w 2>/dev/null
timeout 10 wtype -k minus -d 100 -k equal 2>/dev/null
sleep 5
timeout 10 grim -o eDP-1 /tmp/deep_shot2.png 2>/dev/null
convert /tmp/deep_shot2.png -crop 1280x720+0+0 -format 'shot2: mean=%[fx:mean] stddev=%[fx:standard_deviation] colors=%k\n' info: 2>/dev/null

echo "=== cleanup ==="
kill "$LAUNCH_PID" 2>/dev/null
sleep 2
pkill -9 -x factorio 2>/dev/null
sleep 2
pgrep -x factorio >/dev/null && echo "WARN still running" || echo "game stopped"
echo done

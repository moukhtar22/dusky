#!/usr/bin/env bash
# Firefox diagnostics probe (no escaping headaches - plain script file)
# v2: fixed 10-bit detection (vainfo reports Main10/422_10/Profile2-3, not
#     "10bit"), and adapter detection now uses the empirically-verified
#     method (DRM render-node fds + nvidia-smi + mapped EGL libs) instead of
#     MOZ_LOG=gfx which Firefox 153 does not emit adapter lines for.
set -u

echo "=== AV1/VP9/HEVC support (YouTube needs these) ==="
timeout 15 vainfo 2>&1 | grep -iE 'VP9|AV1|HEVC'
echo
echo "=== 10-bit / 12-bit profiles count ==="
# Correct patterns: HEVCMain10, HEVCMain12, 422_10, 444_10/12, VP9 Profile1-3
timeout 15 vainfo 2>&1 | grep -icE 'Main10|Main12|422_10|444_10|444_12|Profile[1-3]'
echo
echo "=== launching with PDM logging (30s, fresh profile) ==="
rm -rf /tmp/ff_probe2
mkdir -p /tmp/ff_probe2
# MOZ_LOG_FILE makes each child process (RDD/GPU/content) write its own log,
# so the RDD 'Support ... for hw decoding' lines are actually captured.
timeout 30 env MOZ_LOG=PlatformDecoderModule:4 MOZ_LOG_FILE=/tmp/ff_probe2/moz \
  firefox -no-remote -profile /tmp/ff_probe2 -new-instance about:blank > /tmp/ff_probe2/stdout.log 2>&1 &
PROBE_PID=$!
sleep 10

echo "--- main firefox process DRM render nodes (empirical GPU check) ---"
# renderD128 = Intel iGPU, renderD129 = NVIDIA dGPU (matches /dev/dri/by-path)
for p in $(pgrep -x firefox); do
  echo "pid=$p comm=$(cat /proc/$p/comm 2>/dev/null) -> $(ls -l /proc/$p/fd 2>/dev/null | grep -oE 'renderD[0-9]+' | sort -u | tr '\n' ' ')"
done

echo
echo "--- EGL/GL libraries mapped into main process (hw vs sw proof) ---"
for p in $(pgrep -x firefox | head -1); do
  grep -oE '/usr/lib/(libEGL[^ ]*|libGLX[^ ]*|libgallium[^ ]*|libnvidia[^ ]*)[^ ]*' /proc/$p/maps 2>/dev/null | sort -u | head -15
  if grep -qE 'llvmpipe|swrast' /proc/$p/maps 2>/dev/null; then
    echo "WARNING: software rasterizer (llvmpipe/swrast) mapped -> software rendering"
  else
    echo "(no llvmpipe/swrast mapped -> hardware rendering)"
  fi
done

echo
echo "--- dGPU activity (nvidia-smi) ---"
timeout 5 nvidia-smi --query-gpu=name,utilization.gpu,power.draw --format=csv 2>/dev/null || echo "nvidia-smi unavailable (no NVIDIA driver?)"

wait $PROBE_PID 2>/dev/null
echo
echo "=== RDD hardware decode support (empirical) ==="
grep -h 'Support.*hw decoding' /tmp/ff_probe2/moz*.moz_log 2>/dev/null | sort -u | head -8
grep -h 'Broadcast support' /tmp/ff_probe2/stdout.log 2>/dev/null | head -2
echo
echo "=== errors / warnings ==="
grep -iE 'error|warn|fail|crash' /tmp/ff_probe2/stdout.log | grep -viE 'gtk-monospace|wr_glyph|remote settings|nimbus' | head -20
echo "(probe timeout at 30s is expected)"

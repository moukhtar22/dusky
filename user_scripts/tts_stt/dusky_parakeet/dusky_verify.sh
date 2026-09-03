#!/usr/bin/env bash
# Dusky STT verification: static / live / d3 / all. Hardware-aware.
set -uo pipefail

APP_DIR="${HOME}/.local/lib/dusky-stt"
SERVICE="dusky_stt.service"
TRIGGER="${HOME}/.local/bin/dusky_trigger"
CONFIG="${APP_DIR}/config.json"
PASSED=0
FAILED=0

c_ok=$(printf '\033[32m'); c_bad=$(printf '\033[31m'); c_off=$(printf '\033[0m')
pass() { PASSED=$((PASSED+1)); printf '%s  PASS%s %s\n' "$c_ok" "$c_off" "$1"; }
fail() { FAILED=$((FAILED+1)); printf '%s  FAIL%s %s\n' "$c_bad" "$c_off" "$1"; }

hardware() { "$APP_DIR/.venv-main/bin/python" -c 'import json;print(json.load(open("'"$CONFIG"'")).get("hardware","cpu"))' 2>/dev/null || echo cpu; }

static_checks() {
  printf '\n== Static ==\n'
  [[ -f "$CONFIG" ]] || { fail "Missing config.json"; return; }
  [[ -x "$TRIGGER" ]] || { fail "Trigger not executable"; return; }
  local py; py=$("$APP_DIR/.venv-main/bin/python" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')
  "$APP_DIR/.venv-main/bin/python" -c 'import sys;raise SystemExit(0 if sys.version_info>=(3,14,6) and sys._is_gil_enabled() else 1)' \
    && pass "Main CPython $py GIL" || fail "Main python $py"
  local mo wo
  mo=$("$APP_DIR/.venv-main/bin/python" -c 'import importlib.metadata as m;print(sorted(set(m.packages_distributions().get("onnxruntime",[]))))')
  wo=$("$APP_DIR/.venv-worker/bin/python" -c 'import importlib.metadata as m;print(sorted(set(m.packages_distributions().get("onnxruntime",[]))))')
  [[ "$mo" == "['onnxruntime']" ]] && pass "Main ORT exclusive" || fail "Main ORT owners: $mo"
  local hw; hw=$(hardware)
  if [[ "$hw" == "nvidia" ]]; then
    [[ "$wo" == "['onnxruntime-gpu']" ]] && pass "Worker ORT-GPU exclusive" || fail "Worker ORT owners: $wo"
  else
    [[ "$wo" == "['onnxruntime']" ]] && pass "Worker ORT exclusive ($hw)" || fail "Worker ORT owners: $wo"
  fi
  local pid; pid=$(systemctl --user show -p MainPID --value "$SERVICE")
  if [[ "$pid" =~ ^[0-9]+$ ]] && [[ "$pid" -gt 0 ]]; then
    pass "Service active PID $pid"
    if grep -Eiq 'libcuda\.so|libcudart\.so|libcublas|libcudnn|onnxruntime_providers_cuda' "/proc/$pid/maps"; then
      fail "CUDA leaked into daemon"; else pass "Daemon CUDA-clean"; fi
  else fail "Service not active"; fi
  local sock="$XDG_RUNTIME_DIR/dusky-stt/control.sock"
  [[ -S "$sock" ]] && pass "Control socket exists" || fail "Control socket missing"
  [[ "$(stat -c '%a' "$XDG_RUNTIME_DIR/dusky-stt" 2>/dev/null)" == "700" ]] && pass "Dir 0700" || fail "Dir mode"
  [[ "$(stat -c '%a' "$sock" 2>/dev/null)" == "600" ]] && pass "Socket 0600" || fail "Socket mode"
  systemd-analyze --user verify "$HOME/.config/systemd/user/$SERVICE" >/dev/null 2>&1 \
    && pass "Unit verifies" || fail "Unit verify failed"
}

live_checks() {
  printf '\n== Live ==\n'
  wtype "" 2>/dev/null || fail "wtype rejected (compositor virtual-keyboard?)"
  "$TRIGGER" --start --realtime >/dev/null || { fail "Start failed"; return; }
  pass "Capture started"
  local hw; hw=$(hardware)
  if [[ "$hw" == "nvidia" ]]; then
    local seen=0
    for _ in {1..12}; do sleep 0.5
      if nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null | grep -q .; then seen=1; break; fi
    done
    [[ "$seen" -eq 1 ]] && pass "GPU compute context observed" || fail "No GPU context"
  else
    sleep 3; pass "CPU ($hw) capture running (no GPU context expected)"
  fi
  "$TRIGGER" --stop >/dev/null && pass "Stopped/finalized" || fail "Stop failed"
}

d3_checks() {
  local hw; hw=$(hardware)
  if [[ "$hw" != "nvidia" ]]; then printf '\n== D3 (skipped, hardware=%s) ==\n' "$hw"; pass "D3 N/A on $hw"; return; fi
  printf '\n== D3cold ==\n'
  local busid; busid=$(nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader | head -n 1 | tr -d '[:space:]')
  local pci_dev="/sys/bus/pci/devices/$(echo "$busid" | awk '{print tolower(substr($0,5))}')"
  [[ -d "$pci_dev" ]] || { fail "PCI path $pci_dev"; return; }
  local idle; idle=$(python3 -c 'import json;print(int(json.load(open("'"$CONFIG"'")).get("idle_timeout_seconds",90)))')
  local wait_for=$((idle + 6)); printf 'Waiting %s s for idle exit...\n' "$wait_for"; sleep "$wait_for"
  if pgrep -u "$USER" -f 'dusky_worker.py' >/dev/null 2>&1; then fail "Worker survived idle"; else pass "Worker exited"; fi
  # Passive reads only: nvidia-smi wakes the GPU.
  local rs ps; rs=$(cat "$pci_dev/power/runtime_status" 2>/dev/null || echo unknown)
  ps=$(cat "$pci_dev/power_state" 2>/dev/null || echo unknown)
  [[ "$rs" == "suspended" ]] && pass "runtime_status=suspended" || fail "runtime_status=$rs"
  [[ "$ps" == "D3cold" ]] && pass "power_state=D3cold" || fail "power_state=$ps (needs NVreg_DynamicPowerManagement=0x02 + no other clients)"
}

summary() { printf '\n== summary: %d passed, %d failed ==\n' "$PASSED" "$FAILED"; [[ "$FAILED" -eq 0 ]]; }
case "${1:-all}" in
  static) static_checks; summary ;;
  live) live_checks; summary ;;
  d3) d3_checks; summary ;;
  all) static_checks; live_checks; d3_checks; summary ;;
  *) echo "Usage: $0 [static|live|d3|all]"; exit 2 ;;
esac

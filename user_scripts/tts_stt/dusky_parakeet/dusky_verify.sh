#!/usr/bin/env bash
# Verify the installed runtime; live checks record from the configured microphone.
set -uo pipefail
APP_DIR="${DUSKY_APP_DIR:-$HOME/.local/lib/dusky-stt}"
CONFIG="${DUSKY_CONFIG:-$APP_DIR/config.json}"
TRIGGER="${DUSKY_TRIGGER:-$HOME/.local/bin/dusky_trigger}"
SERVICE=dusky_stt.service
PY="$APP_DIR/.venv-main/bin/python"
PASSED=0 FAILED=0
pass() { PASSED=$((PASSED+1)); printf '  ✓ %s\n' "$1"; }
fail() { FAILED=$((FAILED+1)); printf '  ✗ %s\n' "$1"; }
check() { local message=$1; shift; if "$@"; then pass "$message"; else fail "$message"; fi; }
config_value() { "$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],sys.argv[3]))' "$CONFIG" "$1" "$2"; }
static_checks() {
  printf '\nRuntime\n'
  if [[ ! -f $CONFIG || ! -x $PY ]]; then fail 'Install missing'; return; fi
  check 'Trigger executable' test -x "$TRIGGER"
  check 'CPython 3.14.7+ with GIL' "$PY" -c 'import sys; assert sys.version_info >= (3,14,7) and sys._is_gil_enabled()'
  check 'CPU daemon isolation' "$PY" "$APP_DIR/dusky_main.py" --config "$CONFIG" --check-cpu-isolation
  local hw expected pid
  hw=$(config_value hardware cpu)
  expected=onnxruntime; [[ $hw != nvidia ]] || expected=onnxruntime-gpu
  check 'Worker runtime ownership' "$APP_DIR/.venv-worker/bin/python" -c 'import importlib.metadata as m,sys; assert set(m.packages_distributions().get("onnxruntime",[])) == {sys.argv[1]}' "$expected"
  check 'Unit configuration' systemd-analyze --user verify "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$SERVICE"
  pid=$(systemctl --user show -p MainPID --value "$SERVICE")
  if [[ $pid =~ ^[0-9]+$ && $pid -gt 0 ]]; then
    check 'Daemon has no CUDA libraries' "$PY" -c 'import pathlib,re,sys; assert not re.search(r"libcuda\.so|libcudart\.so|libcublas|libcudnn|onnxruntime_providers_cuda",pathlib.Path(f"/proc/{sys.argv[1]}/maps").read_text())' "$pid"
    check 'Control endpoint responding' "$TRIGGER" --status --json
  else
    pass 'Service idle; the trigger starts it on demand'
  fi
}
live_checks() {
  printf '\nMicrophone\n'
  if ! "$TRIGGER" --start --push; then fail 'Capture start'; return; fi
  pass 'Capture started'; sleep 3
  check 'Capture stop requested' "$TRIGGER" --stop
  local state
  for _ in {1..120}; do
    state=$("$TRIGGER" --status --json 2>/dev/null) || break
    if [[ $state == *'"state": "idle"'* ]]; then pass 'Capture finalized'; return; fi
    sleep 0.5
  done
  if systemctl --user is-active --quiet "$SERVICE"; then fail 'Finalization did not reach idle'; else pass 'On-demand service stopped'; fi
}
d3_checks() {
  printf '\nGPU power\n'
  local hw device busid pci_dev rs ps
  hw=$(config_value hardware cpu)
  if [[ $hw != nvidia ]]; then printf '  Not applicable (%s)\n' "$hw"; return; fi
  device=$(config_value gpu_device 0)
  busid=$(nvidia-smi -i "$device" --query-gpu=pci.bus_id --format=csv,noheader) || { fail 'GPU lookup'; return; }
  busid=${busid//[[:space:]]/}; busid=${busid,,}
  pci_dev="/sys/bus/pci/devices/${busid:4}"
  if ! "$TRIGGER" --unload; then fail 'Worker unload (finish active jobs first)'; return; fi
  pass 'Worker unloaded'
  # Passive reads: querying nvidia-smi again would wake the device.
  for _ in {1..30}; do
    rs=$(cat "$pci_dev/power/runtime_status" 2>/dev/null || printf unknown)
    [[ $rs != suspended ]] || break
    sleep 1
  done
  ps=$(cat "$pci_dev/power_state" 2>/dev/null || printf unknown)
  printf '  Device %s: %s, %s\n' "$device" "$rs" "$ps"
  # D3cold depends on other GPU clients and firmware; it is not an ASR health check.
  if [[ $rs == suspended ]]; then pass 'GPU suspended'; else printf '  GPU remains active; check other clients and platform power management.\n'; fi
}
summary() { printf '\n%d passed · %d failed\n' "$PASSED" "$FAILED"; [[ $FAILED -eq 0 ]]; }
case "${1:-static}" in
  static) static_checks; summary ;;
  live) live_checks; summary ;;
  d3) d3_checks; summary ;;
  all) static_checks; live_checks; d3_checks; summary ;;
  -h|--help) printf 'Usage: dusky_verify [static|live|d3|all]\n\nstatic  Installed runtime (default)\nlive    Record three seconds and finalize\nd3      Unload worker; inspect GPU power\nall     Run all checks\n' ;;
  *) printf 'Usage: dusky_verify [static|live|d3|all]\n' >&2; exit 2 ;;
esac

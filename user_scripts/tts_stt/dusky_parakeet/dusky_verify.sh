#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

readonly SERVICE="dusky_stt.service"
readonly APP_DIR="${HOME}/.local/lib/dusky-stt"
readonly MAIN_PYTHON="${APP_DIR}/.venv-main/bin/python"
readonly WORKER_PYTHON="${APP_DIR}/.venv-worker/bin/python"
readonly TRIGGER="${HOME}/.local/bin/dusky_trigger"
readonly SOCKET="${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is required}/dusky-stt/control.sock"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

pass() {
    printf 'PASS: %s\n' "$*"
}

require_file() {
    [[ -f "$1" ]] || fail "missing file: $1"
}

static_checks() {
    require_file "${APP_DIR}/config.json"
    require_file "${APP_DIR}/dusky_main.py"
    require_file "${APP_DIR}/dusky_worker.py"
    [[ -x "$MAIN_PYTHON" ]] || fail "main interpreter is not executable"
    [[ -x "$WORKER_PYTHON" ]] || fail "worker interpreter is not executable"
    [[ -x "$TRIGGER" ]] || fail "trigger is not executable"

    systemd-analyze --user verify "${HOME}/.config/systemd/user/${SERVICE}"
    systemctl --user is-active --quiet pipewire.service || fail "PipeWire is inactive"
    systemctl --user is-active --quiet wireplumber.service || fail "WirePlumber is inactive"
    systemctl --user is-active --quiet "$SERVICE" || fail "Dusky service is inactive"
    pass "systemd units are valid and active"

    [[ -S "$SOCKET" ]] || fail "control path is not a Unix socket"
    [[ "$(stat -Lc '%U:%a:%F' "$SOCKET")" == "$(id -un):600:socket" ]] \
        || fail "control socket owner, mode, or type is wrong: $(stat -Lc '%U:%a:%F' "$SOCKET")"
    [[ "$(stat -Lc '%U:%a:%F' "$(dirname "$SOCKET")")" == "$(id -un):700:directory" ]] \
        || fail "runtime directory owner or mode is wrong"
    pass "private SOCK_SEQPACKET control endpoint has mode 0600 in a 0700 directory"

    local main_pid
    main_pid="$(systemctl --user show "$SERVICE" -p MainPID --value)"
    [[ "$main_pid" =~ ^[1-9][0-9]*$ ]] || fail "invalid MainPID: $main_pid"
    [[ -r "/proc/${main_pid}/maps" ]] || fail "cannot inspect daemon mappings"
    if grep -Eqi 'libcuda.so|libcudart.so|libcublas(Lt)?.so|libcudnn.so|onnxruntime_providers_cuda' "/proc/${main_pid}/maps"; then
        grep -Ei 'libcuda.so|libcudart.so|libcublas(Lt)?.so|libcudnn.so|onnxruntime_providers_cuda' "/proc/${main_pid}/maps" >&2
        fail "CPU daemon mapped CUDA libraries"
    fi
    pass "CPU daemon has no CUDA, cuBLAS, cuDNN, or CUDA EP mappings"

    CUDA_VISIBLE_DEVICES=-1 "$MAIN_PYTHON" -c \
        'import importlib.metadata as m; o={x.lower().replace("_", "-") for x in m.packages_distributions().get("onnxruntime", [])}; assert o=={"onnxruntime"}, o; print(o)'
    CUDA_VISIBLE_DEVICES=0 "$WORKER_PYTHON" -c \
        'import importlib.metadata as m; o={x.lower().replace("_", "-") for x in m.packages_distributions().get("onnxruntime", [])}; assert o=={"onnxruntime-gpu"}, o; print(o)'
    pass "CPU and GPU ORT namespaces have one owner each"

    "$TRIGGER" --status --json
    systemctl --user show "$SERVICE" \
        -p Type -p NotifyAccess -p WatchdogUSec -p MemoryCurrent -p MemoryPeak \
        -p MemoryHigh -p MemoryMax -p NRestarts -p MainPID
    ps -o pid,ppid,rss,etimes,cmd -p "$main_pid"
    ss -xlpn | grep -F "$SOCKET" || fail "socket is absent from ss output"
    pass "daemon status and control socket are observable"
}

live_checks() {
    static_checks
    printf '\nFocus a writable Wayland text field, then press Enter.\n'
    read -r
    wtype '' || fail "compositor rejected the virtual-keyboard protocol"
    "$TRIGGER" --start --realtime
    printf 'Speak a sentence for at least three seconds, then press Enter.\n'
    read -r

    local main_pid worker_pid
    main_pid="$(systemctl --user show "$SERVICE" -p MainPID --value)"
    worker_pid="$(pgrep -P "$main_pid" -f 'dusky_worker.py' | head -n 1 || true)"
    if [[ -n "$worker_pid" ]]; then
        ps -o pid,ppid,rss,etimes,cmd -p "$worker_pid"
        nvidia-smi --query-compute-apps=pid,process_name,used_memory \
            --format=csv,noheader | grep -F "$worker_pid" \
            || fail "GPU worker is not visible as an NVIDIA compute client"
        pass "on-demand worker owns a live NVIDIA compute context"
    else
        printf 'Worker was not sampled before stop; final inference will be checked next.\n'
    fi

    "$TRIGGER" --stop
    if [[ -z "$worker_pid" ]]; then
        local sample_deadline=$((SECONDS + 15))
        while (( SECONDS < sample_deadline )); do
            worker_pid="$(pgrep -P "$main_pid" -f 'dusky_worker.py' | head -n 1 || true)"
            [[ -n "$worker_pid" ]] && break
            sleep 0.2
        done
        [[ -n "$worker_pid" ]] || fail "finalization never spawned the GPU worker"
        ps -o pid,ppid,rss,etimes,cmd -p "$worker_pid"
        nvidia-smi --query-compute-apps=pid,process_name,used_memory \
            --format=csv,noheader | grep -F "$worker_pid" \
            || fail "final GPU worker is not visible as an NVIDIA compute client"
        pass "final inference owns a live NVIDIA compute context"
    fi
    local deadline=$((SECONDS + 130))
    while (( SECONDS < deadline )); do
        if "$TRIGGER" --status --json | grep -q '"state": "idle"'; then
            break
        fi
        sleep 1
    done
    (( SECONDS < deadline )) || fail "recording did not finalize within 120 seconds"
    pass "recording finalized and returned to idle"

    journalctl --user -u "$SERVICE" -n 30 --no-pager -o short-precise
    find "${HOME}/.local/state/dusky-stt/transcripts" -maxdepth 1 -type f \
        -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' | sort | tail -n 3
}

d3_checks() {
    local pci_address="${1:-0000:01:00.0}"
    local idle_timeout main_pid
    idle_timeout="$("$MAIN_PYTHON" -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text())["idle_timeout_seconds"])' "${APP_DIR}/config.json")"
    main_pid="$(systemctl --user show "$SERVICE" -p MainPID --value)"
    printf 'Waiting %s seconds for worker idle teardown...\n' "$("$MAIN_PYTHON" -c "print(int(float('$idle_timeout')) + 5)")"
    sleep "$("$MAIN_PYTHON" -c "print(int(float('$idle_timeout')) + 5)")"

    if pgrep -u "$(id -u)" -f "${APP_DIR}/dusky_worker.py" >/dev/null; then
        pgrep -a -u "$(id -u)" -f "${APP_DIR}/dusky_worker.py" >&2
        fail "GPU worker survived its idle deadline"
    fi
    pass "GPU worker exited after the configured idle deadline"

    local compute_clients
    compute_clients="$(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader || true)"
    if grep -F 'dusky_worker.py' <<<"$compute_clients"; then
        fail "Dusky remains listed as an NVIDIA compute client"
    fi
    pass "no Dusky NVIDIA compute context remains"

    local device="/sys/bus/pci/devices/${pci_address}"
    [[ -d "$device" ]] || fail "PCI device path does not exist: $device"
    printf 'runtime_status: %s\n' "$(<"${device}/power/runtime_status")"
    [[ "$(<"${device}/power/runtime_status")" == "suspended" ]] \
        || fail "GPU runtime PM is not suspended; another client or platform policy is keeping it awake"
    if [[ -r "${device}/power_state" ]]; then
        printf 'power_state: %s\n' "$(<"${device}/power_state")"
        [[ "$(<"${device}/power_state")" == "D3cold" ]] \
            || fail "GPU is suspended but not in D3cold"
    fi
    pass "kernel runtime PM reports the discrete GPU suspended in D3cold"
}

usage() {
    cat <<'EOF'
Usage:
  dusky_verify static
  dusky_verify live
  dusky_verify d3 [PCI_ADDRESS]

Run 'live' while nvtop is open in a second terminal for visual utilization and
VRAM confirmation. Run 'd3' only after live inference and after closing every
other NVIDIA client. D3cold is a platform result, not something an STT process
can force while another client holds the GPU.
EOF
}

case "${1:-}" in
    static)
        static_checks
        ;;
    live)
        live_checks
        ;;
    d3)
        d3_checks "${2:-0000:01:00.0}"
        ;;
    *)
        usage
        exit 2
        ;;
esac

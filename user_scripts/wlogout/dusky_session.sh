#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# dusky-session - Graceful session teardown for Hyprland 0.55.4+ (Lua) / systemd 261
# Pure Hyprland native teardown (no session manager helper)
# Reference patterns from system_menu.sh and wlogout_scale.sh
# -----------------------------------------------------------------------------

set -euo pipefail

# 1. Dependency & Environment Validation (from wlogout_scale.sh pattern)
if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
  echo "WARNING: HYPRLAND_INSTANCE_SIGNATURE not set, not inside Hyprland? Proceeding anyway." >&2
fi

for cmd in hyprctl jq systemctl ps; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: Required command '$cmd' not found in PATH." >&2
    exit 1
  fi
done

# 2. Action parsing & validation
ACTION="${1:-poweroff}"
case "$ACTION" in
  poweroff|reboot|soft-reboot|logout) ;;
  *)
    echo "Error: Invalid action '$ACTION'." >&2
    echo "Usage: ${0##*/} [poweroff|reboot|soft-reboot|logout]" >&2
    exit 1
    ;;
esac

# 3. State management clean-up (non-fatal)
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/dusky"
if [[ -d "$STATE_DIR" ]]; then
  rm -f -- "$STATE_DIR"/re*-required 2>/dev/null || :
fi

# 4. Reset workspace (visually cleaner for next boot, non-fatal)
# Best-effort, must not abort script if IPC fails
hyprctl dispatch "hl.dsp.focus({ workspace = \"1\" })" >/dev/null 2>&1 || :

# 5. Smart teardown - Avoid killing our own ancestry
# Build skip list from current ancestry to not close our own terminal
declare -A skip_pids=()
pid=$$
while (( pid > 1 )); do
  skip_pids["$pid"]=1
  # Modern, fast, width-limited ancestry tracking
  ppid=$(ps -o ppid:1= -p "$pid" 2>/dev/null) || break
  [[ "$ppid" =~ ^[0-9]+$ ]] || break
  (( ppid == pid )) && break
  pid="$ppid"
done

build_batch() {
  local mode="$1" out="" c_pid addr
  local json
  json=$(hyprctl clients -j 2>/dev/null) || return 1
  while IFS=$'\t' read -r c_pid addr; do
    [[ "$c_pid" =~ ^[0-9]+$ ]] || continue
    [[ "$addr" =~ ^0x[0-9a-fA-F]+$ ]] || continue
    [[ -n "${skip_pids[$c_pid]:-}" ]] && continue
    if [[ "$mode" == "close" ]]; then
      out+="dispatch hl.dsp.window.close({ window = 'address:${addr}' }) ; "
    else
      out+="dispatch hl.dsp.window.kill({ window = 'address:${addr}' }) ; "
    fi
  done < <(jq -r '.[] | "\(.pid)\t\(.address)"' <<<"$json" 2>/dev/null || :)
  printf '%s' "$out"
}

# First pass: Graceful window closure
if batch=$(build_batch close); [[ -n "$batch" ]]; then
  hyprctl --batch "$batch" >/dev/null 2>&1 || :
  
  # Wait up to 2.5s for target graceful close, checking every 0.5s
  for _ in {1..5}; do
    sleep 0.5
    still_open=""
    if clients_json=$(hyprctl clients -j 2>/dev/null); then
      while IFS=$'\t' read -r c_pid; do
        [[ "$c_pid" =~ ^[0-9]+$ ]] || continue
        [[ -z "${skip_pids[$c_pid]:-}" ]] && still_open="yes" && break
      done < <(jq -r '.[] | .pid' <<<"$clients_json" 2>/dev/null || :)
    fi
    [[ -z "$still_open" ]] && break
  done

  # Second pass fallback: force kill any remaining target windows
  if kbatch=$(build_batch kill); [[ -n "$kbatch" ]]; then
    hyprctl --batch "$kbatch" >/dev/null 2>&1 || :
  fi
fi

# 6. Execute final action - Native Hyprland / systemd only
if [[ "$ACTION" == "logout" ]]; then
  exec hyprctl dispatch "hl.dsp.exit()"
else
  # Canonical option order: systemctl [OPTIONS...] COMMAND
  exec systemctl --no-wall "$ACTION"
fi

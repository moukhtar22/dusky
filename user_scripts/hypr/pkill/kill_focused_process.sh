#!/usr/bin/env bash

# 1. Fetch PID securely via JSON.
# We strictly use jq to prevent "Title Spoofing" vulnerabilities where a malicious 
# or accidental window title (e.g., "pid: 1") tricks standard text parsing.
pid=$(hyprctl activewindow -j 2>/dev/null | jq -r '.pid // 0')

# Guard: Ensure PID is valid, greater than 1 (not init), and actively exists in the kernel.
if (( pid <= 1 )) || [[ ! -d "/proc/$pid" ]]; then
    exit 0
fi

# 2. Extract the Process Group ID (PGID) natively (0 spawned binaries).
# We read directly from the kernel Virtual File System to bypass `ps`, `awk`, and subshells.
stat_str=$(<"/proc/$pid/stat")
stat_str="${stat_str##*) }"
read -r _ _ pgid _ <<< "$stat_str"

# 3. Extract the current user's Hyprland PGID instantly.
# Scoped to $USER to prevent grabbing another session's compositor in multi-seat setups.
hypr_pid=$(pgrep -x -u "$USER" Hyprland | head -n 1)
if [[ -n "$hypr_pid" && -d "/proc/$hypr_pid" ]]; then
    hypr_stat=$(<"/proc/$hypr_pid/stat")
    hypr_stat="${hypr_stat##*) }"
    read -r _ _ hypr_pgid _ <<< "$hypr_stat"
else
    hypr_pgid=0
fi

# 4. Blast-Radius & Core System Safeguard
# Fallback to single PID if PGID is invalid or matches compositor.
if (( pgid <= 1 || pgid == hypr_pgid )); then
    target="$pid"
else
    target="-$pgid"
fi

# 5. The Graceful Request
# Built-in bash command, executes in microseconds to the entire process group.
kill -15 "$target" 2>/dev/null

# 6. Event-Driven Kernel Block
# `tail --pid` leverages Linux `pidfd_open` (Kernel 5.3+).
# It sleeps with 0% CPU and wakes the microsecond the parent PID dies.
timeout 1 tail --pid="$pid" -f /dev/null 2>/dev/null

# 7. The Group Escalation (The Ultimate Failsafe)
# By running kill -0 against $target (the group), the kernel checks if ANY process 
# in the app's tree survived the SIGTERM. If a zombie child is detected, we SIGKILL the group.
if kill -0 "$target" 2>/dev/null; then
    kill -9 "$target" 2>/dev/null
fi

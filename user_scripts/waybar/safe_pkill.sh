#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# safe_pkill.sh — Race-safe SIGRTMIN+8 delivery for Waybar
#
# Zero forks. Pure bash builtins + /proc reads. kill is a bash builtin.
# Invoked per-notification by Mako on-notify. Not a daemon.
#
# Bash 5.3+ · Linux 7.0+ · x86_64 (SIGRTMIN=34, +8=42, bit 41)
# ------------------------------------------------------------------------------

set -euo pipefail
shopt -s nullglob
exec 2>/dev/null

readonly SIG=42
readonly BIT_POS=41

for cf in /proc/[0-9]*/comm; do
    read -r name < "$cf" 2>/dev/null || continue
    [[ $name == waybar ]] || continue

    pid=${cf%/comm}
    pid=${pid##*/}

    status=$(<"/proc/$pid/status") 2>/dev/null || continue
    [[ $status =~ SigCgt:[[:space:]]+([0-9a-fA-F]+) ]] || continue

    (( 16#${BASH_REMATCH[1]} & (1 << BIT_POS) )) && kill -"$SIG" "$pid"
done

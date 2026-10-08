#!/usr/bin/env bash
# Select one of the 20 largest resident processes and send SIGKILL.

process_start_time() {
    local stat
    local -a stat_fields
    # Read the complete record: Linux process names can also contain newlines.
    { stat=$(<"/proc/$1/stat"); } 2>/dev/null || return 1
    # comm may contain spaces and parentheses. Fields after its final ')' start
    # with field 3 (state); starttime is field 22.
    read -r -a stat_fields <<< "${stat##*) }"
    start_time=${stat_fields[19]}
    [[ $start_time =~ ^[0-9]+$ ]]
}

kill_selected() {
    # Hold the process identity while checking the saved starttime and killing.
    # This avoids the check/kill PID-reuse race of the shell's kill builtin.
    python - "$1" "$2" <<'PY'
import os
import signal
import sys

pid = int(sys.argv[1])
try:
    fd = os.pidfd_open(pid)
    try:
        with open(f"/proc/{pid}/stat", "rb") as stat:
            start = stat.read().rsplit(b") ", 1)[1].split()[19]
        if start != sys.argv[2].encode("ascii"):
            raise ProcessLookupError("Selected process exited; PID was reused")
        signal.pidfd_send_signal(fd, signal.SIGKILL)
    finally:
        os.close(fd)
except (OSError, IndexError) as error:
    print(f"Cannot kill PID {pid}: {error}", file=sys.stderr)
    sys.exit(1)
PY
}

main() {
    local lock_file=${XDG_RUNTIME_DIR:-/run/user/$UID}/rofi_killer.lock result
    exec 9> "$lock_file" || return 1
    flock -n 9 || {
        result=$?
        (( result == 1 )) || return "$result"
        printf '%s\n' 'Rofi killer is already running.'
        return 0
    }

    local pid pmem rss comm start_time selection index process_list
    local -a pids starts rows
    while true; do
        pids=() starts=() rows=()
        process_list=$(LC_ALL=C ps --no-headers -eo pid,pmem,rss,comm --sort=-rss 9>&-) || return $?
        while read -r pid pmem rss comm; do
            process_start_time "$pid" || continue
            pids+=("$pid") starts+=("$start_time")
            printf -v rows[${#rows[@]}] 'RAM: %-4s%% (%4s MB) | %-25s | PID: %s' \
                "$pmem" "$((rss / 1024))" "$comm" "$pid"
            (( ${#rows[@]} == 20 )) && break
        done <<< "$process_list"
        (( ${#rows[@]} )) || return 0

        selection=$(printf '%s\n' "${rows[@]}" | rofi -dmenu \
            -p 'CRITICAL MEMORY! Select to KILL' -i -no-custom -no-sort \
            -format i -theme-str 'window { width: 680px; }' 9>&-) || {
            result=$?
            # Cancellation and custom keybindings do not request SIGKILL.
            (( result == 1 || (result >= 10 && result <= 28) )) && return 0
            return "$result"
        }
        [[ $selection =~ ^[0-9]+$ ]] || return 1
        index=$((10#$selection))
        (( index < ${#pids[@]} )) || return 1
        pid=${pids[index]}

        if kill_selected "$pid" "${starts[index]}" 9>&-; then
            notify-send -a dusky-process-killer -u normal -i dialog-information \
                'Process Killed' "Sent SIGKILL to PID ${pid}." 9>&-
        else
            notify-send -a dusky-process-killer -u normal -i dialog-error \
                'Kill Failed' "PID ${pid} exited, changed identity, or cannot be killed. See the log for details." 9>&-
        fi
    done
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main
fi

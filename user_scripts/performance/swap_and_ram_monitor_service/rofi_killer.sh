#!/usr/bin/env bash
# Rofi Low-Memory Process Killer
# Forensic Optimization: Atomic locking, zero-pipe bloat.

# Secure user-bound lockfile to prevent symlink attacks and race conditions
LOCK_FILE="${XDG_RUNTIME_DIR:-/tmp}/rofi_killer.lock"

# Atomic mutex lock
exec 9> "$LOCK_FILE"
flock -n 9 || { echo "Rofi killer is already running."; exit 0; }

list_processes() {
    # Eliminated 'tail' binary via --no-headers
    ps --no-headers -eo pid,pmem,rss,comm --sort=-pmem | head -n 20 | while read -r pid pmem rss comm; do
        rss_mb=$(( rss / 1024 ))
        # Fixed-width formatting strictly preserved
        printf "RAM: %-4s%% (%4s MB) | %-25s | PID: %s\n" "$pmem" "$rss_mb" "$comm" "$pid"
    done
}

while true; do
    selection=$(list_processes | rofi -dmenu -p "CRITICAL MEMORY! Select to KILL" -i -theme-str 'window { width: 680px; }')
    
    [[ -z "$selection" ]] && break
    
    # Native Bash Regex extraction
    if [[ "$selection" =~ PID:[[:space:]]*([0-9]+) ]]; then
        pid="${BASH_REMATCH[1]}"
        
        # Unconditional SIGKILL per specification
        if kill -9 "$pid" 2>/dev/null; then
            /usr/bin/notify-send -r 3308 -u normal -i dialog-information \
                "Process Killed" \
                "Successfully terminated PID ${pid}."
        else
            /usr/bin/notify-send -r 3308 -u normal -i dialog-error \
                "Kill Failed" \
                "Could not terminate PID ${pid}. It may be a kernel thread or already dead."
        fi
    else
        break
    fi
done

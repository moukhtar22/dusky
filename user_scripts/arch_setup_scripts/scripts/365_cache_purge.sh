#!/usr/bin/env bash
# ==============================================================================
#  ARCH LINUX CACHE PURGE & OPTIMIZER
# ==============================================================================

# --- 1. Safety & Environment ---
set -o errexit
set -o nounset
set -o pipefail

# --- 2. Visuals (with terminal detection) ---
if [[ -t 1 ]]; then
    readonly R=$'\e[31m'
    readonly G=$'\e[32m'
    readonly Y=$'\e[33m'
    readonly B=$'\e[34m'
    readonly RESET=$'\e[0m'
    readonly BOLD=$'\e[1m'
else
    readonly R=''
    readonly G=''
    readonly Y=''
    readonly B=''
    readonly RESET=''
    readonly BOLD=''
fi

log() { printf "%s::%s %s\n" "$B" "$RESET" "$1"; }

# --- 3. Dynamic Configuration ---
# Collect ALL pacman cache directories
PACMAN_CACHES=()
if command -v pacman-conf &>/dev/null; then
    while IFS= read -r line; do
        line="${line%/}"
        [[ -n "$line" ]] && PACMAN_CACHES+=("$line")
    done < <(pacman-conf CacheDir 2>/dev/null)
fi
# Fallback if pacman-conf returned nothing or wasn't available
if [[ ${#PACMAN_CACHES[@]} -eq 0 ]]; then
    PACMAN_CACHES=("/var/cache/pacman/pkg")
fi
readonly PACMAN_CACHES

# Determine sync database path from pacman config
_sync_db=""
if command -v pacman-conf &>/dev/null; then
    _sync_db="$(pacman-conf DBPath 2>/dev/null || true)"
    _sync_db="${_sync_db%/}"
    [[ -n "$_sync_db" ]] && _sync_db="${_sync_db}/sync"
fi
readonly PACMAN_SYNC_DB="${_sync_db:-/var/lib/pacman/sync}"
unset _sync_db

# Respect XDG_CACHE_HOME for AUR helper cache paths
readonly XDG_CACHE="${XDG_CACHE_HOME:-${HOME}/.cache}"
readonly PARU_CACHE="${XDG_CACHE}/paru"
readonly YAY_CACHE="${XDG_CACHE}/yay"

# --- 4. Cleanup Tracking ---
SUDO_KEEPALIVE_PID=""

cleanup() {
    if [[ -n "$SUDO_KEEPALIVE_PID" ]] && kill -0 "$SUDO_KEEPALIVE_PID" 2>/dev/null; then
        kill "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
        wait "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# --- 5. Helper Functions ---

get_dir_size_mb() {
    local target="$1"
    local size

    if [[ ! -d "$target" ]]; then
        echo "0"
        return
    fi

    # Use -r (readable) not -w (writable): du only needs read+execute access.
    # Use '--' to guard against paths starting with a dash.
    if [[ -r "$target" && -x "$target" ]]; then
        size=$(du -sm -- "$target" 2>/dev/null | cut -f1 || true)
    else
        size=$(sudo du -sm -- "$target" 2>/dev/null | cut -f1 || true)
    fi

    if [[ "$size" =~ ^[0-9]+$ ]]; then
        echo "$size"
    else
        echo "0"
    fi
}

# Sum sizes of multiple directories
get_dirs_size_mb() {
    local total=0
    local s
    local dir
    for dir in "$@"; do
        s=$(get_dir_size_mb "$dir")
        total=$((total + s))
    done
    echo "$total"
}

# --- 6. Main Execution ---

main() {
    printf "%sStarting Aggressive Cache Cleanup...%s\n" "$BOLD" "$RESET"

    # Pre-Flight: Validate sudo
    if ! sudo -v; then
        printf "%sError: Sudo authentication failed.%s\n" "$R" "$RESET"
        exit 1
    fi

    # Keep sudo alive in background; disable errexit so a transient
    # sudo -n failure doesn't silently kill the keepalive loop.
    (
        set +o errexit
        while true; do
            sudo -n true 2>/dev/null
            sleep 50
            kill -0 "$$" 2>/dev/null || exit 0
        done
    ) &
    SUDO_KEEPALIVE_PID=$!

    local has_paru=false
    local has_yay=false
    command -v paru &>/dev/null && has_paru=true
    command -v yay &>/dev/null && has_yay=true

    # --- Measure Initial Sizes ---
    log "Measuring current cache usage..."

    local pacman_start
    pacman_start=$(get_dirs_size_mb "${PACMAN_CACHES[@]}")
    printf "   %sPacman Cache:%s   %s MB\n" "$BOLD" "$RESET" "$pacman_start"

    local sync_start
    sync_start=$(get_dir_size_mb "$PACMAN_SYNC_DB")
    printf "   %sSync Database:%s  %s MB\n" "$BOLD" "$RESET" "$sync_start"

    local paru_start=0
    if [[ "$has_paru" == "true" ]]; then
        paru_start=$(get_dir_size_mb "$PARU_CACHE")
        printf "   %sParu Cache:%s    %s MB\n" "$BOLD" "$RESET" "$paru_start"
    fi

    local yay_start=0
    if [[ "$has_yay" == "true" ]]; then
        yay_start=$(get_dir_size_mb "$YAY_CACHE")
        printf "   %sYay Cache:%s     %s MB\n" "$BOLD" "$RESET" "$yay_start"
    fi

    local total_start=$((pacman_start + sync_start + paru_start + yay_start))

    # --- Clean Stuck Partial Downloads (across all pacman cache dirs) ---
    local cache_dir
    for cache_dir in "${PACMAN_CACHES[@]}"; do
        # Strict safety checks: must be a directory, must be an absolute path, must not be the root directory
        if [[ -d "$cache_dir" && "$cache_dir" == /* && "$cache_dir" != "/" ]]; then
            sudo find "$cache_dir" -maxdepth 1 -type f -name "*.part" -delete 2>/dev/null || true
            sudo find "$cache_dir" -maxdepth 1 -type d -name "download-*" -exec rm -rf -- {} + 2>/dev/null || true
        fi
    done

    # --- Clean Caches ---
    # If an AUR helper is present, its -Scc also cleans the pacman cache,
    # so we avoid running pacman -Scc redundantly.
    local pacman_cleaned_by_helper=false

    if [[ "$has_paru" == "true" ]]; then
        log "Purging Paru cache (includes Pacman cache)..."
        yes | paru -Scc 2>/dev/null || true
        printf "   %s✔ Paru cache cleared.%s\n" "$G" "$RESET"
        pacman_cleaned_by_helper=true
    fi

    if [[ "$has_yay" == "true" ]]; then
        if [[ "$pacman_cleaned_by_helper" == "true" ]]; then
            log "Purging Yay cache..."
        else
            log "Purging Yay cache (includes Pacman cache)..."
        fi
        yes | yay -Scc 2>/dev/null || true
        printf "   %s✔ Yay cache cleared.%s\n" "$G" "$RESET"
        pacman_cleaned_by_helper=true
    fi

    if [[ "$pacman_cleaned_by_helper" == "false" ]]; then
        log "Purging Pacman cache (System)..."
        yes | sudo pacman -Scc 2>/dev/null || true
        printf "   %s✔ Pacman cache cleared.%s\n" "$G" "$RESET"
    fi

    # --- Final Report ---
    log "Calculating reclaimed space..."

    local pacman_end
    pacman_end=$(get_dirs_size_mb "${PACMAN_CACHES[@]}")

    local sync_end
    sync_end=$(get_dir_size_mb "$PACMAN_SYNC_DB")

    local paru_end=0
    if [[ "$has_paru" == "true" ]]; then
        paru_end=$(get_dir_size_mb "$PARU_CACHE")
    fi

    local yay_end=0
    if [[ "$has_yay" == "true" ]]; then
        yay_end=$(get_dir_size_mb "$YAY_CACHE")
    fi

    local total_end=$((pacman_end + sync_end + paru_end + yay_end))
    local saved=$((total_start - total_end))

    # Clamp to 0 if somehow negative (cache grew between measurements)
    if [[ $saved -lt 0 ]]; then
        saved=0
    fi

    echo ""
    printf "%s========================================%s\n" "$BOLD" "$RESET"
    printf "%s       DISK SPACE RECLAIMED REPORT      %s\n" "$BOLD" "$RESET"
    printf "%s========================================%s\n" "$BOLD" "$RESET"
    printf "%sInitial Usage:%s  %s MB\n" "$BOLD" "$RESET" "$total_start"
    printf "%sFinal Usage:%s    %s MB\n" "$BOLD" "$RESET" "$total_end"
    printf "%s----------------------------------------%s\n" "$BOLD" "$RESET"

    if [[ $saved -gt 0 ]]; then
        printf "%s%sTOTAL CLEARED:%s %s%s MB%s\n" "$G" "$BOLD" "$RESET" "$G" "$saved" "$RESET"
    else
        printf "%s%sTOTAL CLEARED:%s %s0 MB (Already Clean)%s\n" "$Y" "$BOLD" "$RESET" "$Y" "$RESET"
    fi
    printf "%s========================================%s\n" "$BOLD" "$RESET"
}

main

#!/usr/bin/env bash
#d: Purge package caches to free up space

set -euo pipefail
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -t 1 ]]; then
    readonly R=$'\e[31m' G=$'\e[32m' B=$'\e[34m'
    readonly RESET=$'\e[0m' BOLD=$'\e[1m'
else
    readonly R='' G='' B='' RESET='' BOLD=''
fi

log() { printf '%s::%s %s\n' "$B" "$RESET" "$1"; }
error() { printf '%sError: %s%s\n' "$R" "$*" "$RESET" >&2; }

# One du traversal per snapshot avoids rounding each directory separately and
# counts shared hard links once. Helpers may use custom paths, so their usage
# is deliberately excluded from this report.
get_usage_bytes() {
    local output total
    # Test existence with the same privileges as du; an unreadable parent must
    # not cause an existing cache to be silently omitted.
    output=$(sudo bash -s -- "$@" <<'ROOT_SCRIPT'
set -euo pipefail
dirs=()
for dir in "$@"; do
    [[ ! -d "$dir" ]] || dirs+=("$dir")
done
if (( ${#dirs[@]} == 0 )); then
    printf '0\ttotal\n'
else
    exec du --summarize --total --block-size=1 --dereference-args -- "${dirs[@]}"
fi
ROOT_SCRIPT
    ) || return 1
    total=${output##*$'\n'}
    total=${total%%$'\t'*}
    [[ "$total" =~ ^[0-9]+$ ]] || return 1
    printf '%s\n' "$total"
}

# Full-cache prompts default to No in pacman and paru. --confirm overrides a
# helper's NoConfirm setting; only the consumer's status matters, since yes
# normally exits with SIGPIPE after the consumer closes its input.
run_cleanup() {
    local label=$1 status
    shift
    log "$label"
    if yes | "$@"; then
        status=0
    else
        status=${PIPESTATUS[1]}
    fi
    if (( status != 0 )); then
        error "$label failed (exit $status)."
        case $status in
            129|130|143) exit "$status" ;;
        esac
        return "$status"
    fi
    printf '   %sCleanup command completed.%s\n' "$G" "$RESET"
}

# Pacman -Scc unlinks files but cannot remove leftover download directories.
# Use its database lock while removing those directories to avoid deleting
# files from an active pacman download. The EXIT trap releases only our lock.
clean_download_dirs() {
    sudo bash -s -- "$@" <<'ROOT_SCRIPT'
set -euo pipefail
lock=$1
shift
if ! (set -o noclobber; : > "$lock"); then
    printf 'Error: Cannot acquire pacman lock: %s\n' "$lock" >&2
    exit 1
fi
trap 'rm -f -- "$lock"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
for cache in "$@"; do
    [[ -d "$cache" ]] || continue
    find -H "$cache" -mindepth 1 -maxdepth 1 -type d -name 'download-*' -exec rm -rf -- {} +
done
ROOT_SCRIPT
}

main() {
    local cmd output dbpath cache canonical start='' end='' saved failures=0
    local -a caches helpers=()
    for cmd in sudo pacman pacman-conf du realpath yes bash find rm; do
        command -v "$cmd" >/dev/null || { error "Required command missing: $cmd"; return 1; }
    done

    # Do not silently fall back to defaults after a configuration error.
    output=$(pacman-conf CacheDir) || { error 'Cannot read pacman CacheDir.'; return 1; }
    [[ -n "$output" ]] || { error 'Pacman returned no cache directories.'; return 1; }
    mapfile -t caches <<< "$output"
    dbpath=$(pacman-conf DBPath) || { error 'Cannot read pacman DBPath.'; return 1; }
    for cache in "${caches[@]}" "$dbpath"; do
        [[ "$cache" == /* ]] || { error "Expected an absolute pacman path: $cache"; return 1; }
        canonical=$(realpath --canonicalize-missing -- "$cache") || return 1
        [[ "$canonical" != / ]] || {
            error "Refusing to purge with a pacman path resolving to /: $cache"
            return 1
        }
    done
    dbpath=${dbpath%/}
    for cmd in paru yay; do
        if command -v "$cmd" >/dev/null; then
            helpers+=("$cmd")
        fi
    done

    printf '%sStarting Aggressive Cache Cleanup...%s\n' "$BOLD" "$RESET"
    sudo -v || { error 'Sudo authentication failed.'; return 1; }
    log 'Measuring configured pacman caches and sync databases...'
    start=$(get_usage_bytes "${caches[@]}" "$dbpath/sync") || {
        error 'Initial usage measurement failed; reclaimed space will be unavailable.'
        failures=1
    }

    # Stop on lock/precleanup failure before invoking any other cleanup.
    clean_download_dirs "$dbpath/db.lck" "${caches[@]}" || return "$?"
    run_cleanup 'Purging pacman caches and unused sync databases...' sudo pacman -Scc --confirm || failures=1
    for cmd in "${helpers[@]}"; do
        run_cleanup "Purging $cmd AUR cache using its configuration..." "$cmd" -Scc --aur --confirm || failures=1
    done

    log 'Calculating reclaimed space (pacman caches and sync databases only)...'
    end=$(get_usage_bytes "${caches[@]}" "$dbpath/sync") || {
        error 'Final usage measurement failed; reclaimed space will be unavailable.'
        failures=1
    }
    if [[ -n "$start" && -n "$end" ]]; then
        saved=$((start - end))
        printf '\n%sPacman disk usage report%s (allocated bytes; excludes AUR caches)\n' "$BOLD" "$RESET"
        printf 'Initial usage: %s bytes\nFinal usage:   %s bytes\n' "$start" "$end"
        if (( saved >= 0 )); then
            printf 'Net reclaimed: %s bytes (%s whole MiB)\n' "$saved" "$((saved / 1048576))"
        else
            printf 'Usage increased by %s bytes during cleanup.\n' "$((-saved))"
        fi
    else
        printf '\nReclaimed space: unavailable.\n'
    fi
    if (( failures != 0 )); then
        error 'Cleanup finished with errors; review the output above.'
    fi
    return "$failures"
}

main "$@"

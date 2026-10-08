#!/usr/bin/env bash
#d: Select Terminal or Rofi for Hyprland's clipboard keybinding
# Both frontends share history, pins and the persistence switcher. This script
# changes only the frontend preference consumed by source/keybinds.lua.
set -euo pipefail
umask 077

readonly SETTINGS_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/dusky/settings"
readonly STATE_FILE="$SETTINGS_DIR/clipboard_state"
readonly LOCK_FILE="$SETTINGS_DIR/.clipboard_state.lock"
CURRENT_MODE=terminal
TEMP_FILE=''

cleanup() {
    [[ -z $TEMP_FILE ]] || rm -f -- "$TEMP_FILE"
    return 0
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() { printf 'Error: %s\n' "$1" >&2; exit 1; }

read_mode() {
    CURRENT_MODE=terminal
    local line
    if [[ -f $STATE_FILE ]]; then
        while IFS= read -r line || [[ -n $line ]]; do
            [[ $line =~ ^[[:space:]]*(True|False)[[:space:]]*$ ]] || continue
            if [[ ${BASH_REMATCH[1]} == False ]]; then CURRENT_MODE=rofi
            else CURRENT_MODE=terminal
            fi
        done <"$STATE_FILE"
    fi
    return 0
}

write_mode() {
    local mode="$1" line value=True
    [[ $mode != rofi ]] || value=False
    TEMP_FILE=$(mktemp -- "$SETTINGS_DIR/.clipboard-mode.XXXXXXXX") || fail 'Cannot create state temporary file'
    {
        printf '%s\n' "$value"
        if [[ -f $STATE_FILE ]]; then
            while IFS= read -r line || [[ -n $line ]]; do
                # Replace old frontend markers, preserving every setting,
                # comment and unknown key used by other clipboard components.
                [[ ! $line =~ ^[[:space:]]*(True|False)[[:space:]]*$ ]] || continue
                printf '%s\n' "$line"
            done <"$STATE_FILE"
        fi
    } >"$TEMP_FILE"
    mv -f -- "$TEMP_FILE" "$STATE_FILE"
    TEMP_FILE=''
}

reload_bindings() {
    if [[ -z ${HYPRLAND_INSTANCE_SIGNATURE:-} ]]; then
        printf 'Preference saved; applies when Hyprland starts.\n' >&2
        return 0
    fi
    command -v hyprctl &>/dev/null || fail 'Preference saved, but hyprctl is unavailable; reload Hyprland manually'
    # The state lock remains held until reload finishes, serializing switches
    # with each other and with the Terminal menu's settings updates.
    timeout --kill-after=1 5 hyprctl reload config-only >/dev/null || fail 'Preference saved, but Hyprland reload failed; run hyprctl reload config-only'
}

usage() {
    cat <<EOF
Usage: ${0##*/} [--terminal | --rofi | --status] [--force]

Select the clipboard frontend for Hyprland's Super+V binding.
History, pins and RAM/disk persistence are shared by both frontends.

  --terminal   Select Terminal
  --rofi       Select Rofi
  --status     Print the saved frontend without changing any files
  --force      Reapply the preference and reload even if already selected
  -h, --help   Show this help

Without a mode option, display an interactive selection menu.
EOF
}

main() {
    local mode='' force=0 choice lock_fd
    while (( $# )); do
        case $1 in
            --terminal|--rofi|--status)
                [[ -z $mode ]] || fail 'Choose only one of --terminal, --rofi and --status'
                mode=${1#--}
                ;;
            --force) force=1 ;;
            -h|--help) usage; return 0 ;;
            *) fail "Unknown argument: $1" ;;
        esac
        shift
    done
    [[ $mode != status || $force == 0 ]] || fail '--force cannot be used with --status'
    read_mode
    if [[ $mode == status ]]; then
        printf '%s\n' "$CURRENT_MODE"
        return 0
    fi
    if [[ -z $mode ]]; then
        [[ -t 0 ]] || fail 'Use --terminal or --rofi when stdin is not a terminal'
        printf '\nClipboard frontend (current: %s)\n  1) Terminal\n  2) Rofi (with image thumbnails)\nChoice [1/2]: ' "$CURRENT_MODE" >&2
        read -r choice || fail 'No selection received'
        case $choice in
            1) mode=terminal ;;
            2) mode=rofi ;;
            *) fail "Invalid selection: $choice" ;;
        esac
    fi

    mkdir -p -- "$SETTINGS_DIR"
    exec {lock_fd}<>"$LOCK_FILE"
    flock --exclusive --timeout 3 "$lock_fd" || fail 'Timed out waiting for clipboard settings'
    # Re-read after acquiring the menu's lock; never act on stale settings.
    read_mode
    if [[ $mode == "$CURRENT_MODE" && $force == 0 ]]; then
        printf 'Clipboard frontend already %s.\n' "$mode" >&2
        return 0
    fi
    write_mode "$mode"
    reload_bindings
    printf 'Clipboard frontend: %s\n' "$mode" >&2
    # Keep the lock file: removing a locked inode lets later writers bypass it.
}

main "$@"

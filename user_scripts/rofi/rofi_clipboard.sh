#!/usr/bin/env bash
# Rofi frontend for the shared Dusky Wayland clipboard operations.
set -o nounset -o pipefail
shopt -s nullglob extglob
umask 077
export LC_ALL=C.UTF-8

SELF=$(realpath -e -- "${BASH_SOURCE[0]}") || exit 1
readonly MENU="${SELF%/*}/../clipboard/terminal_clipboard.sh"
readonly THUMB_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/rofi-cliphist/thumbs"
readonly SEP=$'\x1f'
[[ -x $MENU ]] || { printf 'Clipboard backend missing: %s\n' "$MENU" >&2; exit 1; }
mkdir -p -- "$THUMB_DIR" || exit 1
# Bound orphaned thumbnails without touching active menu scratch directories.
find "$THUMB_DIR" -maxdepth 1 -type f -name '*.png' -mmin +1440 -delete 2>/dev/null || :
SESSION=$(mktemp -d -- "$THUMB_DIR/.menu.XXXXXXXX") || exit 1
trap 'rm -rf -- "$SESSION"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
THUMB_KEY=''

ensure_thumbnail() {
    local id="$1" db="$2" generation="$3" digest path decoded="$SESSION/image" temp="$SESSION/thumb.png"
    REPLY=''
    command -v magick &>/dev/null || return 1
    if [[ -z $THUMB_KEY ]]; then
        # IDs remain unique within a database generation; ordinary stores
        # must not invalidate every existing thumbnail.
        digest=$(printf '%s\0%s\0' "$db" "$generation" | b2sum --length=256) || return 1
        THUMB_KEY="${digest%% *}"
    fi
    path="$THUMB_DIR/$THUMB_KEY-$id.png"
    if [[ -s $path ]]; then REPLY="$path"; return 0; fi
    # Decode the database represented by this list, even if storage switches
    # while the optional thumbnails are being rendered.
    "$MENU" --decode "$id" "$db" "$generation" >"$decoded" 2>/dev/null || return 1
    magick "$decoded" -background none -resize 256x256 "PNG:$temp" 2>/dev/null || return 1
    mv -f -- "$temp" "$path" || return 1
    REPLY="$path"
}

display_menu() {
    local display type id db='' generation='' listed_db='' listed_generation='' thumb
    "$MENU" --list >"$SESSION/items" || return 1
    # History rows carry their originating database; pins are shared by both
    # storage modes. This token is returned by Rofi on its next invocation.
    while IFS="$SEP" read -r display type id db generation; do
        if [[ -n $db ]]; then listed_db="$db"; listed_generation="$generation"; break; fi
    done <"$SESSION/items"
    [[ -n $listed_db ]] || listed_db=$("$MENU" --backend) || return 1
    # Base64 keeps whitespace and punctuation in XDG paths out of Rofi's
    # header grammar. The generation remains plain numeric/timestamp text.
    printf '\000data\x1f'
    printf '%s' "$listed_db" | base64 --wrap=0
    printf ':%s\n' "$listed_generation"
    printf '\000message\x1f<b>Alt+T</b>: Wipe | <b>Alt+U</b>: Pin | <b>Alt+Y</b>: UnPin/Delete\n'
    printf '\000no-custom\x1ftrue\n'
    printf '\000use-hot-keys\x1ftrue\n'
    printf '\000keep-selection\x1ftrue\n'

    while IFS="$SEP" read -r display type id db generation; do
        # The shared list already bounds and sanitizes content. Strip only the
        # ANSI decoration added by its image formatter, then shorten for Rofi.
        display="${display//$'\e[36m'/}"
        display="${display//$'\e[0m'/}"
        (( ${#display} <= 80 )) || display="${display:0:80}…"
        thumb=''
        if [[ $type == img ]] && ensure_thumbnail "$id" "$db" "$generation"; then thumb="$REPLY"; fi
        case $type in
            pin|txt|img|bin)
                printf '%s\000info\x1f%s:%s' "$display" "$type" "$id"
                [[ -z $thumb ]] || printf '\x1ficon\x1f%s' "$thumb"
                printf '\n'
                ;;
            *) printf '%s\000nonselectable\x1ftrue\n' "$display" ;;
        esac
    done <"$SESSION/items"
}

handle_selection() {
    local action="${ROFI_RETV:-0}" info="${ROFI_INFO:-}"
    local type="${info%%:*}" id="${info#*:}" context="${ROFI_DATA:-}" db='' generation=''
    if [[ -n $context ]]; then
        db=$(base64 --decode <<<"${context%%:*}") || return 1
        generation="${context#*:}"
    fi
    if [[ $action == 12 ]]; then
        if "$MENU" --wipe "$db" "$generation"; then rm -f -- "$THUMB_DIR"/*.png; fi
        display_menu
        return
    fi
    case $type in pin|txt|img|bin) ;; *) display_menu; return ;; esac
    case $action in
        1) "$MENU" --copy "$type" "$id" "$db" "$generation" ;;
        10|11)
            printf '%s%s%s%s%s%s%s%s\n' "$SEP" "$type" "$SEP" "$id" "$SEP" "$db" "$SEP" "$generation" >"$SESSION/selection" || return 1
            if [[ $type == pin || ( $action == 10 && $type == txt ) ]]; then
                "$MENU" --batch-pin "$SESSION/selection"
            elif [[ $action == 11 ]]; then
                "$MENU" --batch-delete "$SESSION/selection"
            fi
            display_menu
            ;;
        *) display_menu ;;
    esac
}

if (( $# == 0 )); then display_menu; else handle_selection; fi

#!/usr/bin/env bash
# Small synchronous fzf callbacks: no Python/Rich startup on repeated keys.
set -u

if (( $# < 2 || $# > 3 )); then
    printf 'Usage: %s --resize-preview|--move-preview DIRECTION [SETTINGS_DIR]\n' "$0" >&2
    exit 2
fi
action=$1 direction=$2
case "$action:$direction" in
    --resize-preview:left|--resize-preview:right|--resize-preview:up|--resize-preview:down|\
    --move-preview:left|--move-preview:right|--move-preview:up|--move-preview:down|--move-preview:hidden) ;;
    *) printf 'Invalid layout action/direction: %s %s\n' "$action" "$direction" >&2; exit 2 ;;
esac

settings=${3:-$HOME/.config/dusky/settings}
layout_file=$settings/git_preview_layout
last_file=$settings/git_preview_last
default='right,70%,border-left,wrap'
layout=$default
if [[ -r $layout_file ]]; then
    IFS= read -r layout < "$layout_file" || :
fi
layout_re='^(left|right|up|down),([0-9]{1,2})%(,.*)?$'
if [[ $layout != hidden && ! $layout =~ $layout_re ]]; then
    layout=$default
fi

if [[ $action == --resize-preview ]]; then
    [[ $layout != hidden ]] || exit 0
    [[ $layout =~ $layout_re ]]
    edge=${BASH_REMATCH[1]} pct=$((10#${BASH_REMATCH[2]})) rest=${BASH_REMATCH[3]}
    next_pct=$pct
    case "$edge:$direction" in
        right:left|left:right|up:down|down:up) (( next_pct += 5 )) ;;
        right:right|left:left|up:up|down:down) (( next_pct -= 5 )) ;;
        *) exit 0 ;;
    esac
    (( next_pct < 10 )) && next_pct=10
    (( next_pct > 90 )) && next_pct=90
    (( next_pct != pct )) || exit 0
    next="$edge,$next_pct%$rest"
elif [[ $direction == hidden ]]; then
    if [[ $layout == hidden ]]; then
        next=$default
        [[ ! -r $last_file ]] || { IFS= read -r next < "$last_file" || :; }
        [[ $next =~ $layout_re ]] || next=$default
    else
        next=hidden
    fi
else
    case $direction in
        left)  border=border-right;  pct=70 ;;
        right) border=border-left;   pct=70 ;;
        up)    border=border-bottom; pct=50 ;;
        down)  border=border-top;    pct=50 ;;
    esac
    next="$direction,$pct%,$border,wrap"
fi

# The picker invokes transforms synchronously, so repeated keys retain order.
if [[ ! -d $settings ]] && ! mkdir -p -- "$settings"; then
    printf bell
    exit 1
fi
if [[ $next != hidden ]]; then
    printf '%s\n' "$next" > "$last_file" || { printf bell; exit 1; }
else
    printf '%s\n' "$layout" > "$last_file" || { printf bell; exit 1; }
fi
printf '%s\n' "$next" > "$layout_file" || { printf bell; exit 1; }
printf 'change-preview-window(%s)+refresh-preview\n' "$next"

#!/usr/bin/env bash
set -euo pipefail

readonly WALL_DIR="$HOME/Pictures/wallpapers/active_theme"
readonly STATE_DIR="$HOME/.config/dusky/settings/dusky_theme"
readonly FAV_FILE="$STATE_DIR/favorites.list"
readonly CACHE_FILE="$STATE_DIR/current_wallpaper.cache"
readonly INDEX_FILE="$STATE_DIR/favorites.index"
readonly CURRENT_IMAGE_FILE="$STATE_DIR/current_image"
readonly THEME_CTL="$HOME/user_scripts/theme_matugen/theme_ctl.sh"

_TEMP_FILE=""
trap '[[ -z "$_TEMP_FILE" ]] || rm -f -- "$_TEMP_FILE"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

notify() {
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "Favorites" "$1" || :
  else
    printf '%s\n' "$1" >&2
  fi
}

save_file() {
  local destination="$1"
  shift
  _TEMP_FILE=$(mktemp "$STATE_DIR/favorites.tmp.XXXXXX")
  if (( $# )); then
    printf '%s\n' "$@" >"$_TEMP_FILE"
  fi
  mv -fT -- "$_TEMP_FILE" "$destination"
  _TEMP_FILE=""
}

get_current_wallpaper() {
  local output line current=""
  local -a record=()
  # Match the controller's first-image policy on multiple monitors.
  if output=$(timeout -k 1s 2s awww query 2>/dev/null); then
    while IFS= read -r line; do
      if [[ "$line" == *"currently displaying: image: "* ]]; then
        current="${line#*"currently displaying: image: "}"
        break
      fi
    done <<<"$output"
    if [[ -z "$current" ]]; then
      notify "No wallpaper image detected"
      return 1
    fi
    # A mode-directory swap can move the source while awww keeps its old path.
    if [[ -f "$CURRENT_IMAGE_FILE" ]]; then
      mapfile -d '' -t record <"$CURRENT_IMAGE_FILE"
      if (( ${#record[@]} == 2 )) && [[ "${record[0]}" == "$current" ]]; then
        current="${record[1]}"
      fi
    fi
  elif [[ -s "$CACHE_FILE" ]]; then
    current=$(<"$CACHE_FILE")
  fi
  if [[ "$current" != /* || ! -f "$current" || "$current" == *$'\n'* ]]; then
    notify "No valid wallpaper detected"
    return 1
  fi
  save_file "$CACHE_FILE" "$current"
  printf '%s\n' "$current"
}

wallpaper_id() {
  # Preserve whitespace and nested paths; external images keep absolute paths.
  if [[ "$1" == "$WALL_DIR/"* ]]; then
    printf '%s\n' "${1#"$WALL_DIR"/}"
  else
    printf '%s\n' "$1"
  fi
}

load_favorites() {
  favs=()
  [[ ! -f "$FAV_FILE" ]] || mapfile -t favs <"$FAV_FILE"
}

add_favorite() {
  local name="$1" item
  local -a favs=()
  load_favorites
  for item in "${favs[@]}"; do
    if [[ "$item" == "$name" ]]; then
      notify "Already favorite: $name"
      return 0
    fi
  done
  favs+=("$name")
  save_file "$FAV_FILE" "${favs[@]}"
  notify "Added favorite: $name"
}

remove_favorite() {
  local name="$1" item
  local -a favs=() kept=()
  load_favorites
  for item in "${favs[@]}"; do
    [[ "$item" == "$name" ]] || kept+=("$item")
  done
  if (( ${#kept[@]} == ${#favs[@]} )); then
    notify "Not in favorites: $name"
    return 0
  fi
  save_file "$FAV_FILE" "${kept[@]}"
  notify "Removed favorite: $name"
}

cycle_favorite() {
  local index="" next full
  local -i start=0 offset selected total
  local -a favs=()
  load_favorites
  total=${#favs[@]}
  if (( total == 0 )); then
    notify "No favorites saved"
    return 0
  fi
  if [[ -s "$INDEX_FILE" ]]; then
    index=$(<"$INDEX_FILE")
    index="${index#"${index%%[!0]*}"}"
    index="${index:-0}"
    if [[ "$index" =~ ^[0-9]{1,9}$ ]]; then
      start=$(( (10#$index + 1) % total ))
    fi
  fi
  for (( offset=0; offset<total; offset++ )); do
    selected=$(( (start + offset) % total ))
    next="${favs[selected]}"
    [[ -n "$next" ]] || continue
    if [[ "$next" == /* ]]; then full="$next"; else full="$WALL_DIR/$next"; fi
    [[ -f "$full" ]] || continue
    # Never mask a palette/controller failure with a wallpaper-only success.
    if [[ -x "$THEME_CTL" ]]; then
      "$THEME_CTL" set "$full"
    else
      awww img "$full"
    fi
    save_file "$CACHE_FILE" "$full"
    save_file "$INDEX_FILE" "$selected"
    notify "Favorite: $next"$'\n'"Position: $((selected + 1)) / $total"
    return 0
  done
  notify "No saved favorite files are available"
  return 1
}

case "${1:-}" in
  toggle|remove|cycle|list) ;;
  *)
    printf 'Usage: %s {toggle|remove|cycle|list}\n' "${0##*/}"
    printf '  toggle adds the current image; remove deletes its favorite entry.\n'
    exit 1
    ;;
esac
(( $# == 1 )) || { printf 'Unexpected arguments\n' >&2; exit 1; }
mkdir -p -- "$STATE_DIR"

case "$1" in
  toggle|remove)
    current=$(get_current_wallpaper)
    name=$(wallpaper_id "$current")
    if [[ "$1" == toggle ]]; then add_favorite "$name"; else remove_favorite "$name"; fi
    ;;
  cycle) cycle_favorite ;;
  list) [[ ! -f "$FAV_FILE" ]] || cat -- "$FAV_FILE" ;;
esac

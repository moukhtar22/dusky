#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# 🖼️ Rofi Wallpaper Selector (Hardened, Optimized)
# Target: Arch Linux / Hyprland / Dusky / UWSM
#
# Features:
# • Fast thumbnail cache
# • Favorites mode support
# • Parallel thumbnail generation
# • Matugen integration
# • Safe locking (prevents multiple instances)
# • Fully hardened bash (set -euo pipefail)
# -----------------------------------------------------------------------------

set -euo pipefail

# -----------------------------------------------------------------------------
# LOCKING (Prevent multiple instances)
# -----------------------------------------------------------------------------

readonly LOCK_FILE="/tmp/rofi-wallpaper-selector.lock"

exec 200>"$LOCK_FILE"

if ! flock -n 200; then
  notify-send -a "Wallpaper Menu" \
    "Wallpaper selector already running." \
    -u low -t 1000
  exit 0
fi

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------

readonly WALLPAPER_DIR="$HOME/Pictures/wallpapers"
readonly CACHE_DIR="$HOME/.cache/rofi-wallpaper-thumbs"

readonly CACHE_FILE="$CACHE_DIR/rofi_input.cache"
readonly PATH_MAP="$CACHE_DIR/path_map.cache"
readonly FAVORITES_FILE="$HOME/.config/dusky/settings/dusky_theme/favorites.list"

readonly PLACEHOLDER="$CACHE_DIR/_placeholder.png"
readonly ROFI_THEME="$HOME/.config/rofi/wallpaper.rasi"
readonly RANDOM_THEME_SCRIPT="$HOME/user_scripts/random_theme.sh"

readonly THUMB_SIZE=300
readonly MAX_JOBS="$(nproc)"

mkdir -p "$CACHE_DIR"

# -----------------------------------------------------------------------------
# DEPENDENCY CHECK
# -----------------------------------------------------------------------------

check_dependencies() {
  local deps=(rofi swww magick notify-send awk grep)

  for cmd in "${deps[@]}"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      notify-send -a "Wallpaper Menu" \
        "Missing dependency: $cmd" \
        -u critical
      exit 1
    fi
  done
}

check_dependencies

# -----------------------------------------------------------------------------
# PLACEHOLDER THUMBNAIL
# -----------------------------------------------------------------------------

ensure_placeholder() {
  if [[ ! -f "$PLACEHOLDER" ]]; then
    magick \
      -size "${THUMB_SIZE}x${THUMB_SIZE}" \
      xc:"#333333" \
      "$PLACEHOLDER"
  fi
}

ensure_placeholder

# -----------------------------------------------------------------------------
# THUMBNAIL GENERATION
# -----------------------------------------------------------------------------

generate_thumb() {

  local file="$1"
  local rel safe thumb

  rel="${file#"$WALLPAPER_DIR/"}"
  safe="${rel//\//%2F}"
  thumb="$CACHE_DIR/$safe.png"

  if [[ -f "$thumb" && "$thumb" -nt "$file" ]]; then
    return
  fi

  nice -n 19 magick "$file" \
    -strip \
    -thumbnail "${THUMB_SIZE}x${THUMB_SIZE}^" \
    -gravity center \
    -extent "${THUMB_SIZE}x${THUMB_SIZE}" \
    "$thumb" 2>/dev/null || true
}

export WALLPAPER_DIR CACHE_DIR THUMB_SIZE
export -f generate_thumb

# -----------------------------------------------------------------------------
# BUILD MAIN CACHE
# -----------------------------------------------------------------------------

build_cache() {

  notify-send -a "Wallpaper Menu" \
    "Building wallpaper cache..." \
    -u low -t 1000

  mapfile -d '' files < <(
    find "$WALLPAPER_DIR" -type f \
      \( -iname "*.jpg" \
      -o -iname "*.jpeg" \
      -o -iname "*.png" \
      -o -iname "*.webp" \
      -o -iname "*.gif" \) \
      -print0
  )

  # Parallel thumbnail generation
  printf '%s\0' "${files[@]}" |
    xargs -0 -P "$MAX_JOBS" -I{} \
      bash -c 'generate_thumb "$@"' _ {}

  : >"$CACHE_FILE"
  : >"$PATH_MAP"

  local file rel safe thumb

  for file in "${files[@]}"; do

    rel="${file#"$WALLPAPER_DIR/"}"
    safe="${rel//\//%2F}"
    thumb="$CACHE_DIR/$safe.png"

    [[ -f "$thumb" ]] || thumb="$PLACEHOLDER"

    printf '%s\0icon\x1f%s\n' \
      "$rel" "$thumb" >>"$CACHE_FILE"

    printf '%s\t%s\n' \
      "$rel" "$file" >>"$PATH_MAP"

  done
}

# -----------------------------------------------------------------------------
# BUILD FAVORITES CACHE
# -----------------------------------------------------------------------------

build_favorites_cache() {

  if [[ ! -f "$FAVORITES_FILE" ]]; then
    notify-send -a "Wallpaper Menu" \
      "No favorites found." \
      -u low -t 1500
    return 1
  fi

  local fav_cache="$CACHE_DIR/rofi_input_fav.cache"
  : >"$fav_cache"

  local fav thumb

  while read -r fav; do

    [[ -z "$fav" ]] && continue

    thumb="$CACHE_DIR/${fav//\//%2F}.png"

    [[ -f "$thumb" ]] || thumb="$PLACEHOLDER"

    printf '%s\0icon\x1f%s\n' \
      "$fav" "$thumb" >>"$fav_cache"

  done <"$FAVORITES_FILE"

  echo "$fav_cache"
}

# -----------------------------------------------------------------------------
# RESOLVE WALLPAPER PATH
# -----------------------------------------------------------------------------

resolve_path() {

  local name="$1"
  local path

  path=$(awk -F'\t' -v t="$name" '$1 == t {print $2; exit}' "$PATH_MAP")

  if [[ -n "$path" ]]; then
    echo "$path"
    return
  fi

  path="$WALLPAPER_DIR/$name"

  [[ -f "$path" ]] && echo "$path"
}

# -----------------------------------------------------------------------------
# MATUGEN FLAGS EXTRACTION
# -----------------------------------------------------------------------------

get_matugen_flags() {

  if [[ -f "$RANDOM_THEME_SCRIPT" ]]; then

    grep -oP 'matugen \K.*(?= image)' \
      "$RANDOM_THEME_SCRIPT" |
      head -n 1 || echo "--mode dark"

  else

    echo "--mode dark"

  fi
}

# -----------------------------------------------------------------------------
# MAIN EXECUTION
# -----------------------------------------------------------------------------

main() {

  if [[ ! -f "$CACHE_FILE" ]]; then
    build_cache
  fi

  local input="$CACHE_FILE"

  if [[ "${1:-}" == "fav" ]]; then
    input="$(build_favorites_cache)" || exit 0
  fi

  local selection

  selection=$(
    rofi \
      -dmenu \
      -i \
      -show-icons \
      -theme "$ROFI_THEME" \
      -p "Wallpaper" \
      <"$input"
  ) || exit 0

  [[ -z "$selection" ]] && exit 0

  local full_path
  full_path="$(resolve_path "$selection")"

  if [[ -z "$full_path" ]]; then
    notify-send -a "Wallpaper Menu" \
      "Failed to resolve wallpaper path." \
      -u critical
    exit 1
  fi

  local flags
  flags="$(get_matugen_flags)"

  # Set wallpaper
  swww img "$full_path" \
    --transition-type grow \
    --transition-duration 2 \
    --transition-fps 60 \
    >/dev/null 2>&1 &
  disown

  # Apply theme
  setsid uwsm-app -- matugen $flags image "$full_path" \
    >/dev/null 2>&1 &
  disown
}

main "$@"

exit 0

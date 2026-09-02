#!/usr/bin/env bash
# Universal Start — auto-detects native ELF vs Wine EXE, intelligent single template (Aug 2026)
# Replaces start.native.sh + start.wine.sh — fully dynamic, no hardcoded user/cpu
# Usage: cp 02_templates/start.sh /path/to/Game/start.sh && edit CMD auto-detected
cd "$(dirname "$(readlink -f "$0")")" || exit 1
cat << 'EOF'
Support can be provided on our Matrix channel.
Pain heals, chicks dig scars; Glory lasts forever!
EOF
source "$PWD/actions.sh"
# Dynamic checks (no hardcoded HOME/USER)
[ -z "${EXTRACT:-}" ] && echo "Delete ~/.jc141rc and re-run" && exit 1
[ ! -d "${JC_DIRECTORY:-$HOME/Games/jc141}/native-docs" ] && mkdir -p "${JC_DIRECTORY:-$HOME/Games/jc141}/native-docs"
[ "${TERMINAL_OUTPUT:-1}" = 0 ] && exec &> /dev/null
[ "${EXTRACT:-0}" = 0 ] && dwarfs-mount || { dwarfs-extract && UNMOUNT=0; }
# Trap respects GAMESCOPE vs plain
if command -v gamescope &>/dev/null && [ "${GAMESCOPE:-0}" = 1 ]; then
  [ "${UNMOUNT:-1}" = 1 ] && trap jc141-cleanup-gamescope EXIT INT SIGINT SIGTERM HUP
else
  [ "${UNMOUNT:-1}" = 1 ] && trap jc141-cleanup EXIT INT SIGINT SIGTERM HUP
fi
GAMEROOT="$PWD/files/game-root"

# --- Auto-detect intelligent CMD ---
# Priority: 1) ColdClientLoader/steamclient 2) native ELF 3) first .exe
# Override via local.config: CUSTOM_CMD="./MyGame.x86_64 --flag"
if [ -n "${CUSTOM_CMD:-}" ]; then
  # shellcheck disable=SC2206
  CMD=($CUSTOM_CMD)
elif [ -f "$GAMEROOT/steamclient_loader_x64.exe" ] || [ -f "$GAMEDIR/files/game-root/steamclient_loader_x64.exe" ]; then
  # Wine + ColdClientLoader (CRUEL, Crushed, etc.)
  [ -z "${SYSWINE:-}" ] && echo "wine not found — edit ~/.jc141rc SYSWINE" >&2
  export WINE="$SYSWINE"; export WINESERVER="${SYSWINE}server"; export WINEPREFIX="${JC_DIRECTORY:-$HOME/Games/jc141}/wine-prefix-ew"
  export WINEDLLOVERRIDES="winemenubuilder.exe=d;mshtml=d;d3d9,d3d10core,d3d11,dxgi=n;d3d12,d3d12core=n"
  export WINE_LARGE_ADDRESS_AWARE=1; export WINEDEBUG=fixme-all
  [ ! -d "$WINEPREFIX" ] && wine-initiate_prefix
  CMD=("$SYSWINE" "steamclient_loader_x64.exe" "$@")
elif exe=$(find "$GAMEROOT" -maxdepth 3 -type f \( -name "*.x86_64" -o -name "*.bin.x86_64" \) -print -quit 2>/dev/null); [ -n "$exe" ]; then
  # Native ELF (Hollow Knight, Darkwood, etc.) — ultra-fast -print -quit halts traversal instantly
  CMD=("$exe" "$@")
elif exe=$(find "$GAMEROOT" -maxdepth 3 -type f -name "*.exe" -print -quit 2>/dev/null); [ -n "$exe" ]; then
  # Direct Wine EXE (universe.exe, Game.exe)
  [ -z "${SYSWINE:-}" ] && echo "wine not found" >&2
  export WINE="$SYSWINE"; export WINEPREFIX="${JC_DIRECTORY:-$HOME/Games/jc141}/wine-prefix-ew"
  [ ! -d "$WINEPREFIX" ] && wine-initiate_prefix
  CMD=("$SYSWINE" "$(basename "$exe")" "$@")
else
  echo "No executable detected in $GAMEROOT — set CUSTOM_CMD in local.config" >&2; exit 1
fi

# --- Build RUN pipeline (dynamic) ---
declare -a RUN
if command -v gamescope &>/dev/null && [ "${GAMESCOPE:-0}" = 1 ]; then RUN+=( gamescope-run_embedded ); fi
if command -v bwrap &>/dev/null && [ "${ISOLATE:-0}" = 1 ]; then
  # Auto-detect isolation type
  if [[ " ${CMD[*]} " == *"wine"* ]] || [[ " ${CMD[*]} " == *".exe"* ]]; then export ISOLATION_TYPE='wine'; else export ISOLATION_TYPE='native'; fi
  RUN+=( bash 'actions.sh' bwrap-run_in_sandbox --chdir "$GAMEROOT" )
else
  cd "$GAMEROOT" || exit 1
fi
# Env override (ENV="...")
if [ -n "${ENV:-}" ]; then RUN+=( bash -c "$ENV" ); fi
RUN+=( "${CMD[@]}" )

# Gamescope wineserver quirk (Aug 2026)
if command -v gamescope &>/dev/null && [ "${GAMESCOPE:-0}" = 1 ] && [[ "${ISOLATION_TYPE:-}" == "wine" ]]; then
  "$SYSWINE"server -p -f & wineserver_pid=$!
  "${RUN[@]}"
else
  "${RUN[@]}"
fi

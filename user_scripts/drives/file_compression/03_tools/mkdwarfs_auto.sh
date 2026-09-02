#!/usr/bin/env bash
# mkdwarfs_auto.sh — intelligent upstream-aware wrapper (borrowed categorize, sparse, reproducibility)
# Usage: mkdwarfs_auto.sh <game_dir> [-l 7] [--reproducible] [--par2]
set -euo pipefail
GAMEDIR="${1:-}"; shift || true
[ -z "$GAMEDIR" ] && { echo "Usage: $0 <path/to/Game-jc141> [-l level]"; exit 1; }
# Parse -l level correctly (supports: -l 6 or -l6 or --level 6)
LEVEL=7
while [[ $# -gt 0 ]]; do
  case "$1" in
    -l) LEVEL="$2"; shift 2;;
    -l*) LEVEL="${1#-l}"; shift;;
    --level) LEVEL="$2"; shift 2;;
    --) shift; break;;
    *) break;;
  esac
done
REPRO="${DWARFS_REPRODUCIBLE:-0}"
# Profile support: --profile <name> loads 03_tools/profiles/<name>.toml (TOML parsed crudely, only level used)
PROFILE=""
while [[ "$1" == --profile ]]; do PROFILE="$2"; shift 2; done
# Re-parse after profile handling (already done above, but keep LEVEL)
if [ -n "$PROFILE" ]; then
  PROFILE_FILE="$HOME/user_scripts/drives/file_compression/03_tools/profiles/${PROFILE}.toml"
  if [ -f "$PROFILE_FILE" ]; then
    # Crude TOML: extract level = N
    LEVEL=$(grep -E "^level\s*=" "$PROFILE_FILE" | cut -d= -f2 | tr -d ' "')
    echo "[profile] $PROFILE -> level $LEVEL from $PROFILE_FILE"
  fi
fi

GAMEDIR="$(realpath "$GAMEDIR")"
GAME_ROOT="$GAMEDIR/files/game-root"
IMAGE="$GAMEDIR/files/game-root.dwarfs"

MK=($(command -v mkdwarfs 2>/dev/null || echo "$GAMEDIR/files/dwarfs-binary"))
[ -x "${MK[0]}" ] || { echo "mkdwarfs not found"; exit 1; }
# Detect universal
if "${MK[0]}" --tool=mkdwarfs --help &>/dev/null; then MK=("${MK[0]}" --tool=mkdwarfs); fi

OPTS=(-l "$LEVEL" -B26 -S26 --order=nilsimsa --set-owner=1000 --set-group=1000 --set-time=now --chmod=Fa+rw,Da+rwx --no-history)
[ "$REPRO" = 1 ] && OPTS+=(--no-create-timestamp --no-history-timestamps --set-time=0 --num-workers=1) && echo "[auto] reproducible mode"

# Auto-categorize (upstream doc/mkdwarfs.md#categorizers)
if [ -d "$GAME_ROOT" ]; then
    WAV=$(find "$GAME_ROOT" -name "*.wav" 2>/dev/null | wc -l)
    PAK_MB=$(find "$GAME_ROOT" -name "*.pak" -o -name "*.bin" 2>/dev/null | xargs -r du -cm 2>/dev/null | tail -n1 | cut -f1)
    PAK_MB=${PAK_MB:-0}
    if [ "$WAV" -gt 5 ]; then OPTS+=(--categorize=pcmaudio); echo "[auto] pcmaudio ($WAV wavs)"; fi
    if [ "$PAK_MB" -gt 500 ]; then OPTS+=(--categorize=incompressible -C incompressible::null); echo "[auto] incompressible (${PAK_MB}M pak)"; fi
fi

echo "mkdwarfs ${OPTS[*]} -i $GAME_ROOT -o $IMAGE"
"${MK[@]}" "${OPTS[@]}" -i "$GAME_ROOT" -o "$IMAGE"
tree -a -s "$GAMEDIR/files/game-root" > "$GAMEDIR/files/dwarfs-tree" 2>/dev/null || find "$GAME_ROOT" | head -n 20 > "$GAMEDIR/files/dwarfs-tree"
echo "Done: $(du -h "$IMAGE" | cut -f1) $(dwarfsck -i "$IMAGE" --detail=2 2>&1 | grep "original filesystem")"

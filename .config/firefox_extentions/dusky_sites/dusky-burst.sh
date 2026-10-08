#!/usr/bin/env bash
# dusky-burst.sh — 20 distinct palette rewrites in 2 s (rename-into-place like matugen), then restore.
# usage: ./dusky-burst.sh [palette.css] [count] [interval-seconds]
set -euo pipefail
f="${1:-$HOME/.config/matugen/generated/dusky_sites.css}"
n="${2:-20}"; dt="${3:-0.1}"
[[ -f "$f" ]] || { echo "no palette at $f" >&2; exit 1; }
cp -a "$f" "$f.bak"
trap 'mv -f "$f.bak" "$f"' EXIT
t0=$EPOCHREALTIME
for ((i = 1; i <= n; i++)); do
  h=$(printf '%02x' $(( (i * 12) % 256 )))
  sed -E "s/(--primary:\s*#)[0-9a-fA-F]{6}/\1${h}b4aa/; s/(--background:\s*#)[0-9a-fA-F]{6}/\11a11${h}/; s/(--surface:\s*#)[0-9a-fA-F]{6}/\1${h}1110/" \
      "$f.bak" > "$f.tmp"
  mv -f "$f.tmp" "$f"          # IN_MOVED_TO on the directory, exactly what the host watches
  sleep "$dt"
done
awk -v a="$t0" -v b="$EPOCHREALTIME" -v n="$n" 'BEGIN { printf "wrote %d revisions in %.2f s; restoring original\n", n, b - a }'

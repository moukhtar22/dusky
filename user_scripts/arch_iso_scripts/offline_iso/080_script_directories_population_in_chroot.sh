#!/usr/bin/env bash
# Copy the flattened ISO skeleton into the target before useradd populates homes.
# The wrapper transfers the installer itself across the chroot boundary.
set -euo pipefail

readonly MNT_POINT="/mnt"
readonly PAYLOAD_BASE="/etc/skel"

log() { printf '[INFO] %s\n' "$*"; }
die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

(( EUID == 0 )) || die "Run this script as root."
mountpoint -q "$MNT_POINT" || die "'$MNT_POINT' is not mounted."
[[ -f "$PAYLOAD_BASE/.zshrc" ]] || die "ISO skeleton is missing .zshrc; regenerate the ISO payload."
[[ -f "$PAYLOAD_BASE/dusky/HEAD" ]] || die "ISO skeleton is missing the Dusky bare repository; regenerate the ISO payload."

log "Copying ISO dotfiles and bare repository into $MNT_POINT/etc/skel..."
mkdir -p -- "$MNT_POINT/etc/skel"
cp -aT -- "$PAYLOAD_BASE/" "$MNT_POINT/etc/skel/"
log "Skeleton copied successfully; useradd will deploy it into the user's home."

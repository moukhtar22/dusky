#!/usr/bin/env bash
set -eo pipefail

# ── Config ────────────────────────────────────────────────
# Set GAME_DIR to a specific path to override, or leave blank ("") to auto-detect.
GAME_DIR=""
NVIDIA_WRAPPER="$HOME/user_scripts/gaming/nvidia-glx-workaround/use_intel_not_nvidia.sh"
# ──────────────────────────────────────────────────────────

# Auto-detect default GAME_DIR if not explicitly set
find_default_game_dir() {
    local candidates=(
        "/mnt/zram1/Factorio_2.1.14"
        "/mnt/zram1/factorio"
        "$HOME/Downloads/Factorio-jc141"
    )
    for c in "${candidates[@]}"; do
        if [ -d "$c" ] && [ -f "$c/files/game-root.dwarfs" ]; then
            echo "$c"
            return 0
        fi
    done
    for d in /mnt/zram1/Factorio* "$HOME/Downloads/Factorio*" "$HOME/Games/Factorio*"; do
        if [ -d "$d" ] && [ -f "$d/files/game-root.dwarfs" ]; then
            echo "$d"
            return 0
        fi
    done
    echo "/mnt/zram1/Factorio_2.1.14"
}

if [ -z "$GAME_DIR" ]; then
    GAME_DIR="$(find_default_game_dir)"
fi

# Resolve to absolute path if relative (and not a default path)
resolve_path() {
    local p="$1"
    # Already absolute
    if [[ "$p" == /* ]]; then
        echo "$p"
    # ~/... prefix
    elif [[ "$p" == \~/* ]]; then
        echo "$HOME/${p:2}"
    else
        readlink -f "$p"
    fi
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [game-dir] [--help|-h] [--unmount|-u] [--clean|-c]

  game-dir       Path to game directory (default: $GAME_DIR)
  No flags       Mount (if needed) and launch game
  --unmount -u   Unmount DwarFS/overlay, keep files
  --clean   -c   Unmount and delete the entire game dir

Edit GAME_DIR at the top of the script to change the default.
EOF
    exit 0
}

unmount_game() {
    fuser -k "$DWARFS_MNT" 2>/dev/null || true
    for d in "$OVERLAY_DIR" "$DWARFS_MNT"; do
        fusermount3 -u -z "$d" 2>/dev/null || true
    done
    echo "Unmounted"
}

clean_game() {
    unmount_game
    rm -rf "$DWARFS_MNT" "$OVERLAY_WORK"
    [ -d "$OVERLAY_DIR" ] && [ -z "$(ls -A "$OVERLAY_DIR" 2>/dev/null)" ] && rmdir "$OVERLAY_DIR" 2>/dev/null || true
    rm -rf "$GAME_DIR"
    echo "Deleted $GAME_DIR"
}

# Parse path argument (first non-flag positional arg)
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --help|-h|--unmount|-u|--clean|-c)
            args+=("$1"); shift ;;
        --*|-*)
            echo "Unknown option: $1" >&2; exit 1 ;;
        *)
            GAME_DIR=$(resolve_path "$1")
            shift ;;
    esac
done
set -- "${args[@]}"

# Also resolve the wrapper path (needed when script is invoked from elsewhere)
NVIDIA_WRAPPER=$(resolve_path "$NVIDIA_WRAPPER")

DWARFS_IMG="$GAME_DIR/files/game-root.dwarfs"
DWARFS_MNT="$GAME_DIR/files/.game-root-mnt"
OVERLAY_DIR="$GAME_DIR/files/game-root"
OVERLAY_STORAGE="$GAME_DIR/files/overlay-storage"
OVERLAY_WORK="$GAME_DIR/files/.game-root-work"
DWARFS_BIN="$GAME_DIR/files/dwarfs-binary"

case "${1:-}" in
    --help|-h) usage ;;
    --unmount|-u) unmount_game; exit 0 ;;
    --clean|-c) clean_game; exit 0 ;;
esac

mount_game() {
    local total cache
    total=$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 16777216)
    total=${total:-16777216}
    cache=$((total * 25 / 100))

    # Tear down any stale FUSE mounts left from a previous run/crash
    fusermount3 -u -z "$OVERLAY_DIR" 2>/dev/null || true
    fusermount3 -u -z "$DWARFS_MNT"  2>/dev/null || true

    # workdir must be empty for fuse-overlayfs; recreate it
    rm -rf "$OVERLAY_WORK"

    mkdir -p "$DWARFS_MNT" "$OVERLAY_STORAGE" "$OVERLAY_WORK" "$OVERLAY_DIR"

    # DwarFS binary resolution (bundled repack binary vs system dwarfs)
    if [ -f "$DWARFS_BIN" ]; then
        chmod +x "$DWARFS_BIN" 2>/dev/null || true
        "$DWARFS_BIN" --tool=dwarfs "$DWARFS_IMG" "$DWARFS_MNT" \
            -o tidy_strategy=time -o tidy_interval=15m -o tidy_max_age=30m \
            -o cachesize="${cache}k" -o clone_fd
    elif command -v dwarfs >/dev/null 2>&1; then
        dwarfs "$DWARFS_IMG" "$DWARFS_MNT" \
            -o tidy_strategy=time -o tidy_interval=15m -o tidy_max_age=30m \
            -o cachesize="${cache}k" -o clone_fd
    else
        echo "Error: DwarFS binary not found (neither $DWARFS_BIN nor system 'dwarfs')." >&2
        exit 1
    fi

    fuse-overlayfs \
        -o squash_to_uid="$(id -u)" \
        -o squash_to_gid="$(id -g)" \
        -o lowerdir="$DWARFS_MNT",upperdir="$OVERLAY_STORAGE",workdir="$OVERLAY_WORK" \
        "$OVERLAY_DIR"

    echo "Mounted $GAME_DIR"
}

is_mounted() {
    mountpoint -q "$DWARFS_MNT"  2>/dev/null \
 && mountpoint -q "$OVERLAY_DIR" 2>/dev/null
}

if ! is_mounted; then
    echo "Mounting game..."
    mount_game
else
    echo "Already mounted"
fi

GAME_BIN="$OVERLAY_DIR/bin/x64/factorio"

if [ ! -x "$GAME_BIN" ]; then
    echo "Error: $GAME_BIN not found" >&2
    exit 1
fi

# Native-Wayland EGL shim fix (applied by use_wayland/fix_run_on_wayland.py).
# Launched directly here (bypassing start.n.sh), the game needs the shim set
# explicitly; gracefully no-ops if the fix hasn't been installed yet.
# Persistent location: ~/.factorio/wayland_fix (survives game re-downloads).
# run.sh runs unsandboxed, so the primary copy is the right one.
SHIM="$HOME/.factorio/wayland_fix/libEGL.so.1"
if [ -f "$SHIM" ]; then
    export LD_PRELOAD="$SHIM${LD_PRELOAD:+:$LD_PRELOAD}"
fi

if [ -x "$NVIDIA_WRAPPER" ]; then
    exec "$NVIDIA_WRAPPER" "$GAME_BIN" "$@"
else
    exec "$GAME_BIN" "$@"
fi

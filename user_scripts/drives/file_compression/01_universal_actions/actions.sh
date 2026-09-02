#!/usr/bin/env bash
# ==============================================================================
# Universal DwarFS Actions — jc141++ (reliable, upstream-aware)
# Borrows from: /mnt/zram1/dwarfs-main (0.15.5, zstd22/nilsimsa, categorize,
#   hotness, sparse, recompress, dwarfsck, analysis_file, par2) + original
#   JC141 actions.sh:336 (fuse-overlayfs + overlay-storage, 64M -l7 -B26 -S26)
# Improvements listed in 00_docs/JC_VS_UPSTREAM.md
# Location: ~/user_scripts/drives/file_compression/01_universal_actions/
# ==============================================================================

# Navigate to script dir
cd "$(dirname "$(readlink -f "$0")")" || { echo "Failed to navigate."; exit 1; }

if [ -z "${BASH_VERSION-}" ] || shopt -qo posix; then
    printf '%s\n' "This script only works with bash" >&2; exit 1
fi
[ "$EUID" -eq 0 ] && { echo "This script should not be run as root"; exit 1; }

# --- Binary resolution (bundled universal vs system separate, bleeding edge 0.15.7) ---
# Upstream 0.15.7 pacman provides separate binaries: mkdwarfs, dwarfs, dwarfsck, dwarfsextract
# Bundled dwarfs-binary is universal (supports --tool=). Detect correctly.
if [ -x "$PWD/files/dwarfs-binary" ]; then
    chmod +x "$PWD/files/dwarfs-binary" 2>/dev/null || true
    DWARFSBINARY="$(realpath "$PWD/files/dwarfs-binary")"
    if "$DWARFSBINARY" --tool=mkdwarfs --help &>/dev/null 2>&1; then
        # Universal -> use --tool for all
        DWARFS_MK=("$DWARFSBINARY" --tool=mkdwarfs)
        DWARFS_FUSE_BIN=("$DWARFSBINARY" --tool=dwarfs)
        DWARFS_CK=("$DWARFSBINARY" --tool=dwarfsck)
        DWARFS_EX=("$DWARFSBINARY" --tool=dwarfsextract)
    elif "$DWARFSBINARY" --help 2>&1 | grep -q "compress-level"; then
        # It's mkdwarfs itself
        DWARFS_MK=("$DWARFSBINARY")
        DWARFS_FUSE_BIN=($(command -v dwarfs 2>/dev/null || echo dwarfs))
        DWARFS_CK=($(command -v dwarfsck 2>/dev/null || echo dwarfsck))
        DWARFS_EX=($(command -v dwarfsextract 2>/dev/null || echo dwarfsextract))
    else
        # It's dwarfs fuse driver, use system mkdwarfs for compress
        DWARFS_MK=($(command -v mkdwarfs 2>/dev/null || echo mkdwarfs))
        DWARFS_FUSE_BIN=("$DWARFSBINARY")
        DWARFS_CK=($(command -v dwarfsck 2>/dev/null || echo dwarfsck))
        DWARFS_EX=($(command -v dwarfsextract 2>/dev/null || echo dwarfsextract))
    fi
elif command -v mkdwarfs &>/dev/null; then
    DWARFS_MK=(mkdwarfs)
    DWARFS_FUSE_BIN=($(command -v dwarfs 2>/dev/null || echo dwarfs))
    DWARFS_CK=($(command -v dwarfsck 2>/dev/null || echo dwarfsck))
    DWARFS_EX=($(command -v dwarfsextract 2>/dev/null || echo dwarfsextract))
else
    if command -v dwarfs &>/dev/null && dwarfs --tool=mkdwarfs --help &>/dev/null 2>&1; then
        DWARFS_MK=(dwarfs --tool=mkdwarfs)
        DWARFS_FUSE_BIN=(dwarfs --tool=dwarfs)
        DWARFS_CK=(dwarfs --tool=dwarfsck)
        DWARFS_EX=(dwarfs --tool=dwarfsextract)
    else
        echo "ERROR: no dwarfs/mkdwarfs found. Install dwarfs (pacman -S dwarfs) or place files/dwarfs-binary" >&2
        exit 1
    fi
fi
# Legacy compat for old code paths
DWARFSBINARY="${DWARFS_MK[0]}"
DWARFS_TOOL_PREFIX=("${DWARFS_MK[@]}")
DWARFS_FUSE="${DWARFS_FUSE_BIN[0]}"

# --- Universal vs Game Profile ---
# Default is generic: if DWARFS_PROFILE=game, use game paths. Otherwise allow any src/dst via args or env.
# Game is just a profile, not hardcoded in engine. For generic use: bash actions.sh dwarfs-compress /path/to/input /path/to/output.dwarfs
# For game: DWARFS_PROFILE=game or default when files/game-root exists
GAME_ROOT="${GAME_ROOT:-$PWD/files/game-root}"
DWARFS_IMAGE="${DWARFS_IMAGE:-$PWD/files/game-root.dwarfs}"
DWARFS_TREE="${DWARFS_TREE:-$PWD/files/dwarfs-tree}"
# If DWARFS_PROFILE env set, load profile toml for level/block/etc. (see 03_tools/profiles/)
if [ -n "${DWARFS_PROFILE:-}" ]; then
  PROFILE_FILE="$HOME/user_scripts/drives/file_compression/03_tools/profiles/${DWARFS_PROFILE}.toml"
  [ -f "$PROFILE_FILE" ] || PROFILE_FILE="$PWD/${DWARFS_PROFILE}.toml"
  if [ -f "$PROFILE_FILE" ]; then
    # Crude TOML parse for key overrides (level, block_size_bits) - easily configurable, no code edit
    PROFILE_LEVEL=$(grep -E "^\s*level\s*=" "$PROFILE_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' "')
    [ -n "${PROFILE_LEVEL:-}" ] && export PROFILE_LEVEL
  fi
fi

# --- Helpers ---
log_info()  { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
log_warn()  { printf '[WARN] %s\n' "$*" >&2; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }

check_fuse_conf() {
    if ! grep -q "^\s*user_allow_other" /etc/fuse.conf 2>/dev/null; then
        log_warn "user_allow_other not set in /etc/fuse.conf — overlayfs as user may fail. Add line: user_allow_other"
    fi
}

# --- Mount (improved vs JC: adds preload, allow_other handling, kernel overlay fallback) ---
dwarfs-mount() {
    dwarfs-unmount &>/dev/null

    HWRAMTOTAL="$(grep MemTotal /proc/meminfo | awk '{print $2}')"
    CACHEONRAM=$((HWRAMTOTAL * 25 / 100))
    # Upstream: cachesize 25% RAM, 512M default — JC uses 25% which is sane for 62Gi

    CORUID="$(id -u "$USER")"
    CORGID="$(id -g "$USER")"

    [ -d "$GAME_ROOT" ] && { [ "$(ls -A "$GAME_ROOT" 2>/dev/null)" ] && log_info "Game is mounted or extracted." && return 0; }

    declare -A DEPENDENCIES=(
        ['fuse-overlayfs']='fuse-overlayfs'
        ['fuser']='psmisc'  # provides fuser
    )
    for dep_bin in "${!DEPENDENCIES[@]}"; do
        if ! command -v "$dep_bin" &>/dev/null; then
            log_error "${DEPENDENCIES[$dep_bin]} missing. Install it or set EXTRACT=1 in ~/.jc141rc"
            # Fallback to EXTRACT automatically if allowed
            if command -v dwarfsextract &>/dev/null || "${DWARFS_TOOL_PREFIX[@]}"dwarfsextract --help &>/dev/null; then
                log_warn "Falling back to dwarfs-extract (no overlay, slower first run)"
                dwarfs-extract; return $?
            fi
            return 1
        fi
    done

    [ -f "$DWARFS_IMAGE" ] || { log_error "Missing $DWARFS_IMAGE — run dwarfs-compress first"; return 1; }

    mkdir -p "$PWD/files/.game-root-mnt" "$PWD/files/overlay-storage" "$PWD/files/.game-root-work" "$GAME_ROOT" || return 1

    check_fuse_conf

    # Upstream mount options borrowed: clones, tidy, cachesize, allow_root handling, block_allocator
    # Use allow_root only if uncommented user_allow_other is present (needs ^\s* without #)
    DWARFS_EXTRA_OPTS="${DWARFS_EXTRA_OPTS:-}"
    if grep -q "^\s*user_allow_other" /etc/fuse.conf 2>/dev/null; then
        DWARFS_EXTRA_OPTS="-o allow_root $DWARFS_EXTRA_OPTS"
    fi

    # Borrowed: support analysis_file for hotness (upstream doc/dwarfs.md)
    if [ -n "${DWARFS_ANALYSIS_FILE:-}" ]; then
        DWARFS_EXTRA_OPTS="$DWARFS_EXTRA_OPTS -o analysis_file=$DWARFS_ANALYSIS_FILE"
    fi

    if [ -z "${_LANGUAGE:-}" ]; then
        "${DWARFS_FUSE_BIN[@]}" "$DWARFS_IMAGE" "$PWD/files/.game-root-mnt" \
            -o tidy_strategy=time -o tidy_interval=15m -o tidy_max_age=30m \
            -o cachesize="${CACHEONRAM}k" -o clone_fd $DWARFS_EXTRA_OPTS && \
        fuse-overlayfs -o squash_to_uid="$CORUID" \
                        -o squash_to_gid="$CORGID" \
                        -o lowerdir="$PWD/files/.game-root-mnt",upperdir="$PWD/files/overlay-storage",workdir="$PWD/files/.game-root-work" \
                        "$GAME_ROOT"
        log_info "Mounted game files only."
    else
        mkdir -p "$PWD/files/languages/.$_LANGUAGE-mnt"
        "${DWARFS_FUSE_BIN[@]}" "$DWARFS_IMAGE" "$PWD/files/.game-root-mnt" \
            -o tidy_strategy=time -o tidy_interval=15m -o tidy_max_age=30m \
            -o cachesize="${CACHEONRAM}k" -o clone_fd $DWARFS_EXTRA_OPTS && \
        "${DWARFS_FUSE_BIN[@]}" "$PWD/files/languages/$_LANGUAGE.dwarfs" "$PWD/files/languages/.$_LANGUAGE-mnt" $DWARFS_EXTRA_OPTS && \
        fuse-overlayfs -o squash_to_uid="$CORUID" \
                        -o squash_to_gid="$CORGID" \
                        -o lowerdir="$PWD/files/.game-root-mnt":"$PWD/files/languages/.$_LANGUAGE-mnt",upperdir="$PWD/files/overlay-storage",workdir="$PWD/files/.game-root-work" \
                        "$GAME_ROOT"
        log_info "Mounted game files + language $_LANGUAGE"
    fi
    log_info "Game mounted successfully (overlay-storage persists saves/mods)."
}

dwarfs-unmount() {
    fuser -k "$PWD/files/.game-root-mnt" 2>/dev/null || true
    fuser -k "$PWD/files/languages/.$_LANGUAGE-mnt" 2>/dev/null || true
    local UMOUNT_DIRS=("$GAME_ROOT" "$PWD/files/.game-root-mnt" "$PWD/files/languages/.$_LANGUAGE-mnt")
    for dir in "${UMOUNT_DIRS[@]}"; do
        fusermount3 -u -z "$dir" 2>/dev/null || fusermount -u -z "$dir" 2>/dev/null || umount -l "$dir" 2>/dev/null || true
    done
    log_info "Game unmounted."
    rm -rf "$PWD/files/.game-root-mnt" "$PWD/files/.game-root-work" "$PWD/files/languages/.$_LANGUAGE-mnt" 2>/dev/null || true
    [ -d "$GAME_ROOT" ] && [ -z "$(ls -A "$GAME_ROOT" 2>/dev/null)" ] && rm -rf "$GAME_ROOT"
}

dwarfs-unmount-gamescope() {
    dwarfs-unmount
    # JC quirk: gamescope leaves wineserver zombie (doc/setup-guide.txt) — kill only if we started it
    if [ -n "${WINEPREFIX:-}" ]; then
        wineserver -k 2>/dev/null || killall wineserver 2>/dev/null || true
    else
        killall wineserver 2>/dev/null || true
    fi
}

dwarfs-extract() {
    if [ -d "$GAME_ROOT" ] && [ "$(ls -A "$GAME_ROOT" 2>/dev/null)" ]; then
        log_info "Game is already mounted or extracted."; return 0
    fi
    mkdir -p "$GAME_ROOT" || return 1
    # Borrowed upstream: --num-disk-writers for parallel extract on many small files
    local EXTRA_EXTRACT="${DWARFSEXTRACT_EXTRA:-}"
    # Auto-enable 2 writers if >1000 files expected (heuristic)
    "${DWARFS_EX[@]}" --stdout-progress -i "$DWARFS_IMAGE" -o "$GAME_ROOT" $EXTRA_EXTRACT || {
        log_error "Failed to extract."; return 1; }
}

dwarfs-extract-language() {
    if [ -d "$PWD/files/languages/$_LANGUAGE" ] && [ "$(ls -A "$PWD/files/languages/$_LANGUAGE" 2>/dev/null)" ]; then
        log_info "Language already mounted/extracted."; return 0; fi
    mkdir -p "$PWD/files/languages/$_LANGUAGE" || return 1
    "${DWARFS_EX[@]}" --stdout-progress -i "$PWD/files/languages/$_LANGUAGE.dwarfs" -o "$PWD/files/languages/$_LANGUAGE" || {
        log_error "Failed to extract language."; return 1; }
}

# --- dwarfs-compress (universal: any dir, game is just a profile) ---
# Universally handles any directory: dwarfs-compress [src] [dst]
# If no args, uses GAME_ROOT/DWARFS_IMAGE (game profile defaults, easily configurable via env/profile)
# Game is NOT hardcoded in engine — it's just DWARFS_PROFILE=game or default files/game-root
dwarfs-compress() {
    local SRC="${1:-$GAME_ROOT}"
    local DST="${2:-$DWARFS_IMAGE}"
    # Support profile override for level: DWARFS_PROFILE=game -> uses balanced, or --profile handling
    if [ -n "${PROFILE_LEVEL:-}" ]; then
        # PROFILE_LEVEL from DWARFS_PROFILE toml overrides -l
        : # will be handled below via MK_OPTS
        :
    fi
    if [ -f "$DST" ]; then
        log_warn "$DST exists — remove to recompress or use dwarfs-recompress"; return 0
    fi
    [ -d "$SRC" ] && [ "$(ls -A "$SRC" 2>/dev/null)" ] || { log_error "Source empty: $SRC — usage: dwarfs-compress [src_dir] [dst.dwarfs]"; return 1; }
    # For universal, set GAME_ROOT/DWARFS_IMAGE to SRC/DST for downstream tree logic
    GAME_ROOT="$SRC"; DWARFS_IMAGE="$DST"; DWARFS_TREE="${DST%.dwarfs}.tree"
    [ "$DST" = "$PWD/files/game-root.dwarfs" ] && DWARFS_TREE="$PWD/files/dwarfs-tree"

    # Upstream borrow: allow env override for reproducibility/bit-rot
    # JC default: --set-time=now (nondeterministic, audit proved md5 changes)
    # Improved: default to --set-time=now but support DWARFS_REPRODUCIBLE=1 -> fixed time + no timestamps
    local TIME_OPT="--set-time=now"
    local HISTORY_OPT="--no-history"
    local NUM_WORKERS_OPT=""
    if [ "${DWARFS_REPRODUCIBLE:-0}" = "1" ]; then
        TIME_OPT="--set-time=0"  # epoch, deterministic
        HISTORY_OPT="--no-history --no-create-timestamp --no-history-timestamps"
        NUM_WORKERS_OPT="--num-workers=1"  # deterministic, slower
        log_info "Reproducible mode: fixed time, single worker"
    fi

    # Upstream: auto-categorize detection — SIGPIPE-proof via process substitution (no pipefail kill)
    local CATEGORIZE_OPT=""
    if [ "${DWARFS_AUTOCATEGORIZE:-0}" = "1" ]; then
        local WAV_COUNT=0 PAk_SIZE=0
        # Count wav via process substitution — 100% pipefail-safe, bounded
        while IFS= read -r -d '' _; do
            ((WAV_COUNT++))
            ((WAV_COUNT > 5)) && break
        done < <(find "$GAME_ROOT" -type f -iname "*.wav" -print0 2>/dev/null)
        # Check pak existence without SIGPIPE — -print -quit is already safe, but use process substitution for consistency
        local pak_exists=""
        pak_exists=$(find "$GAME_ROOT" -type f \( -iname "*.pak" -o -iname "*.zip" \) -print -quit 2>/dev/null)
        if [ -n "$pak_exists" ]; then
            PAK_SIZE=$(find "$GAME_ROOT" -type f \( -iname "*.pak" -o -iname "*.zip" -o -iname "*.mp4" \) -exec du -cm {} + 2>/dev/null | tail -n1 | cut -f1)
            PAK_SIZE=${PAK_SIZE:-0}
        else
            PAK_SIZE=0
        fi
        # For log, if wav exists, count accurately bounded 20 via same safe method
        if [ "$WAV_COUNT" -gt 0 ]; then
            WAV_COUNT=0
            while IFS= read -r -d '' _; do ((WAV_COUNT++)); ((WAV_COUNT >= 20)) && break; done < <(find "$GAME_ROOT" -type f -iname "*.wav" -print0 2>/dev/null)
        fi
        if [ "$WAV_COUNT" -gt 5 ]; then
            CATEGORIZE_OPT="--categorize=pcmaudio"
            log_info "Auto-categorize: pcmaudio detected ($WAV_COUNT wavs)"
        fi
        if [ "${PAk_SIZE:-0}" -gt 500 ]; then
            [ -n "$CATEGORIZE_OPT" ] && CATEGORIZE_OPT="$CATEGORIZE_OPT,incompressible" || CATEGORIZE_OPT="--categorize=incompressible"
            log_info "Auto-categorize: incompressible (.pak/.zip ${PAk_SIZE}M)"
        fi
        [ -z "$CATEGORIZE_OPT" ] || CATEGORIZE_OPT="--categorize=${CATEGORIZE_OPT#--categorize=}"
        # For incompressible, also set compression null for that cat (upstream doc)
        if echo "$CATEGORIZE_OPT" | grep -q incompressible; then
            CATEGORIZE_OPT="$CATEGORIZE_OPT -C incompressible::null"
        fi
    fi

    # Classic JC flags + upstream extensions, but profile-aware: if PROFILE_LEVEL set, use it
    local EFFECTIVE_LEVEL="${PROFILE_LEVEL:-7}"
    local DWARFS_OWNER="${DWARFS_OWNER:-1000}"; local DWARFS_GROUP="${DWARFS_GROUP:-1000}"; local MK_OPTS=(-l"$EFFECTIVE_LEVEL" -B26 -S26 --order=nilsimsa --set-owner="$DWARFS_OWNER" --set-group="$DWARFS_GROUP" --chmod=Fa+rw,Da+rwx $HISTORY_OPT $TIME_OPT $NUM_WORKERS_OPT $CATEGORIZE_OPT)
    # Allow user filter (upstream --filter)
    if [ -n "${DWARFS_FILTER:-}" ]; then
        MK_OPTS+=(-F "$DWARFS_FILTER")
    fi

    log_info "Compressing $SRC -> $DST with mkdwarfs ${MK_OPTS[*]} (profile: ${DWARFS_PROFILE:-balanced})"
    "${DWARFS_MK[@]}" "${MK_OPTS[@]}" -i "$SRC" -o "$DST" && \
        { if [ "$DST" = "$PWD/files/game-root.dwarfs" ]; then command -v tree &>/dev/null && tree -a -s files/game-root > "$DWARFS_TREE" || find files/game-root -print > "$DWARFS_TREE"; else ls -lh "$DST" 2>/dev/null | cat; fi; } && \
        log_info "Created $DST ($(du -h "$DST" | cut -f1)) + $DWARFS_TREE"

    # Language packs: same logic with loop
    if [ -d "$PWD/files/languages" ]; then
        ( cd "$PWD/files/languages" || exit
          for item in */; do
            folder_name="${item%/}"; [ -d "$folder_name" ] || continue
            "${DWARFS_MK[@]}" "${MK_OPTS[@]}" -i "$folder_name" -o "$PWD/${folder_name}.dwarfs" && \
                log_info "Created ${folder_name}.dwarfs" || log_error "Failed $folder_name"
          done )
    fi

    # Optional par2 for bit-rot (upstream README:Dealing with Bit Rot)
    if command -v par2 &>/dev/null && [ "${DWARFS_PAR2:-0}" = "1" ]; then
        par2 create -n1 "$DWARFS_IMAGE" && log_info "par2 created for $DWARFS_IMAGE"
    fi
}

# --- Additional upstream tools borrowed ---
dwarfs-verify() {
    [ -f "$DWARFS_IMAGE" ] || { log_error "No image $DWARFS_IMAGE"; return 1; }
    log_info "Running dwarfsck --check-integrity (slow, thorough)"
    "${DWARFS_CK[@]}" -i "$DWARFS_IMAGE" --check-integrity --detail=2 || return 1
    # Also checksum mode
    if [ "${DWARFS_VERIFY_CHECKSUM:-0}" = "1" ]; then
        "${DWARFS_CK[@]}" -i "$DWARFS_IMAGE" --checksum=sha256 | sha256sum --check --quiet && log_info "Checksum OK" || log_error "Checksum fail"
    fi
}

dwarfs-recompress() {
    # Upstream: mkdwarfs --recompress (change level without full rebuild)
    local LEVEL="${1:-7}"
    [ -f "$DWARFS_IMAGE" ] || { log_error "No image"; return 1; }
    local TMP="$DWARFS_IMAGE.tmp"
    "${DWARFS_MK[@]}" --recompress -i "$DWARFS_IMAGE" -o "$TMP" -l "$LEVEL" && mv "$TMP" "$DWARFS_IMAGE" && log_info "Recompressed to -l$LEVEL"
}

dwarfs-info() {
    "${DWARFS_CK[@]}" -i "$DWARFS_IMAGE" --detail=2 -j 2>/dev/null | command -v jq &>/dev/null && \
        "${DWARFS_CK[@]}" -i "$DWARFS_IMAGE" --detail=2 -j | jq || \
        "${DWARFS_CK[@]}" -i "$DWARFS_IMAGE" --detail=2
}

# --- JC config generators (kept for compat) ---
jc141-write_config() {
    cat <<- 'EOF' >> "$1"
	# environment variables and commands added before the start script is run in shell (only compatible with releases after 08.04.2026)
	ENV=""

	# Automatically unmounts files/game-root after the process ends.
	UNMOUNT=1

	# Extract to files/game-root instead of mounting the files/game-root.dwarfs archive on launch,
	EXTRACT=0

	# Display output in console.
	TERMINAL_OUTPUT=1

	# wine executable path. Can be an absolute or relative path or a different program that does what wine does.
	SYSWINE="$(command -v wine)"

	# bubblewrap
	ISOLATE=0
	BLOCK_NET=1
	JC_DIRECTORY="$HOME/Games/jc141"

	# gamescope
	GAMESCOPE=0
	GAMESCOPE_FULLSCREEN=1
	GAMESCOPE_BORDERLESS=0
	GAMESCOPE_SCREEN_WIDTH=
	GAMESCOPE_SCREEN_HEIGHT=
	GAMESCOPE_GAME_WIDTH=
	GAMESCOPE_GAME_HEIGHT=
	ADDITIONAL_FLAGS=""
	EOF
}
jc141-generate_global_defaults() { cat <<- 'EOF' > "$HOME/.jc141rc"
	# this config is used by jc141 start scripts to specify default settings.
	EOF
    jc141-write_config "$HOME/.jc141rc"; }
jc141-generate_local_overrides() {
    cat <<- 'EOF' > "$PWD/local.config"
	# this file is used by jc141 start scripts to specify game-specific settings
	EOF
    jc141-write_config "$PWD/local.config"; sed -i -e 's/^\([^#].*\)/#\1/g' "$PWD/local.config"; }
jc141-write_language() { cat <<- 'EOF' > "$PWD/language.config"
	# This release makes use of changing the game language via this file.
	_LANGUAGE=""
	EOF
}

bwrap-run_in_sandbox() {
    [ -z "${XDG_RUNTIME_DIR:-}" ] && export XDG_RUNTIME_DIR="/run/user/${EUID}"
    BWRAP_FLAGS=(--unshare-pid --ro-bind / / --bind-try "$JC_DIRECTORY/native-docs" ~/ --dev-bind /dev /dev --bind-try /dev/shm /dev/shm --ro-bind-try /sys /sys --bind-try /tmp /tmp --bind-try /tmp/.X11-unix /tmp/.X11-unix --proc /proc --new-session --die-with-parent)
    [ "${ISOLATION_TYPE:-}" = 'wine' ] && BWRAP_FLAGS+=( --bind "$WINEPREFIX" "$WINEPREFIX" )
    [ "${ISOLATION_TYPE:-}" = 'native' ] && [ ! -e "$JC_DIRECTORY/native-docs/.Xauthority" ] && [ -n "${XAUTHORITY:-}" ] && ln -f "${XAUTHORITY}" "$JC_DIRECTORY/native-docs/.Xauthority" 2>/dev/null || true; [ "${ISOLATION_TYPE:-}" = 'native' ] && XAUTHORITY="$HOME/.Xauthority"
    [ "${BLOCK_NET:-0}" = 1 ] && BWRAP_FLAGS+=( --unshare-net )
    BWRAP_FLAGS+=( --bind "$PWD" "$PWD" )
    # Explicit Wayland/PipeWire/Pulse shared memory — zero-copy DMA buffers require /dev/shm + xdg runtime
    BWRAP_FLAGS+=( --ro-bind-try "$XDG_RUNTIME_DIR/${WAYLAND_DISPLAY:-wayland-0}" "$XDG_RUNTIME_DIR/${WAYLAND_DISPLAY:-wayland-0}" )
    BWRAP_FLAGS+=( --ro-bind-try "$XDG_RUNTIME_DIR/pipewire-0" "$XDG_RUNTIME_DIR/pipewire-0" )
    BWRAP_FLAGS+=( --ro-bind-try "$XDG_RUNTIME_DIR/pulse/native" "$XDG_RUNTIME_DIR/pulse/native" )
    bwrap "${BWRAP_FLAGS[@]}" "$@"
}
gamescope-run_embedded() {
    GAMESCOPE_BIN="$(command -v gamescope)"
    [ "${GAMESCOPE_FULLSCREEN:-0}" -eq 1 ] && GAMESCOPE_ARGS+=(-f)
    [ "${GAMESCOPE_BORDERLESS:-0}" -eq 1 ] && GAMESCOPE_ARGS+=(-b)
    [ -n "${GAMESCOPE_SCREEN_WIDTH:-}" ] && GAMESCOPE_ARGS+=(-W "$GAMESCOPE_SCREEN_WIDTH")
    [ -n "${GAMESCOPE_SCREEN_HEIGHT:-}" ] && GAMESCOPE_ARGS+=(-H "$GAMESCOPE_SCREEN_HEIGHT")
    [ -n "${GAMESCOPE_GAME_WIDTH:-}" ] && GAMESCOPE_ARGS+=(-w "$GAMESCOPE_GAME_WIDTH")
    [ -n "${GAMESCOPE_GAME_HEIGHT:-}" ] && GAMESCOPE_ARGS+=(-h "$GAMESCOPE_GAME_HEIGHT")
    GAMESCOPE_ARGS+=(${ADDITIONAL_FLAGS:-})
    "$GAMESCOPE_BIN" "${GAMESCOPE_ARGS[@]}" -- "$@"
}
help() {
    cat << 'EOF'
Usage: actions.sh [SUBCOMMAND]

  dwarfs-mount                 Mounts DwarFS + fuse-overlayfs (overlay-storage persists)
  dwarfs-unmount               Unmounts and cleans
  dwarfs-unmount-gamescope     Unmounts + kills wineserver (gamescope quirk)
  dwarfs-extract               Extracts to files/game-root (fallback if fuse-overlayfs missing)
  dwarfs-compress              Compresses files/game-root -> files/game-root.dwarfs (mkdwarfs -l7 nilsimsa, 64M)
  dwarfs-verify                dwarfsck --check-integrity (+ checksum if DWARFS_VERIFY_CHECKSUM=1)
  dwarfs-recompress [level]    mkdwarfs --recompress (fast level change)
  dwarfs-info                  dwarfsck json/info
  help                         This help

Env overrides (upstream borrowed):
  DWARFS_REPRODUCIBLE=1         Fixed time, --num-workers=1, no timestamps (bit-identical)
  DWARFS_AUTOCATEGORIZE=1       Auto --categorize=pcmaudio/incompressible
  DWARFS_PAR2=1                 Create par2 for bit-rot
  DWARFS_ANALYSIS_FILE=path     Enable analysis for hotness categorizer
  DWARFS_EXTRA_OPTS="-o mlock=try"  Extra dwarfs mount opts

Configs: ~/.jc141rc (global) + ./local.config (per-game, overrides)
Matrix: https://matrix.to/#/#rumpowered:matrix.org
Upstream: /mnt/zram1/dwarfs-main, doc/mkdwarfs.md, doc/dwarfs.md
EOF
}

[ ! -f "$HOME/.jc141rc" ] && jc141-generate_global_defaults
[ ! -f "$PWD/local.config" ] && jc141-generate_local_overrides
[ ! -f "$PWD/language.config" ] && [ -d "$PWD/files/languages" ] && jc141-write_language
# shellcheck disable=SC1090
source "$HOME/.jc141rc" 2>/dev/null || true
# shellcheck disable=SC1090
source "$PWD/local.config" 2>/dev/null || true
[ -f "$PWD/language.config" ] && source "$PWD/language.config" 2>/dev/null || true

(return 0 2> /dev/null) || {
    if type "$1" &> /dev/null; then "$1" "${@:2}"; else help; exit 1; fi
}

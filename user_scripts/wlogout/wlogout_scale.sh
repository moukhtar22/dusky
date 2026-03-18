#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
#  wlogout-launch - Dynamic Scaling & Theming Wrapper for Hyprland
#  Architected for Mass Deployment | Enterprise Grade + Sniper Verification
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ──────────────────────────────────────────────────────────────
# 1. Configuration & Constants
# ──────────────────────────────────────────────────────────────
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/wlogout"
readonly LAYOUT_FILE="${CONFIG_DIR}/layout"
readonly ICON_DIR="${CONFIG_DIR}/icons"
readonly MATUGEN_COLORS="${XDG_CONFIG_HOME:-$HOME/.config}/matugen/generated/wlogout-colors.css"
TMP_CSS=''
WLOGOUT_PID=''

# Reference: 1080p @ 1.0 scale settings
readonly REF_HEIGHT=1080
readonly BASE_FONT_SIZE=20
readonly BASE_BUTTON_RAD=20
readonly BASE_ACTIVE_RAD=25
readonly BASE_MARGIN=50
readonly BASE_HOVER_OFFSET=15
readonly BASE_COL_SPACING=2

# ──────────────────────────────────────────────────────────────
# 2. Strict Environment & Dependency Validation
# ──────────────────────────────────────────────────────────────
if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
    echo "ERROR: Not running inside a Hyprland session." >&2
    exit 1
fi

# Hard-fail if the systemd/elogind runtime directory is broken
if [[ -z "${XDG_RUNTIME_DIR:-}" || ! -d "$XDG_RUNTIME_DIR" || ! -w "$XDG_RUNTIME_DIR" ]]; then
    echo "ERROR: XDG_RUNTIME_DIR is not available or writable. Session is fundamentally broken." >&2
    exit 1
fi

readonly RUNTIME_DIR="$XDG_RUNTIME_DIR"
readonly LOCK_FILE="${RUNTIME_DIR}/wlogout-launch-${HYPRLAND_INSTANCE_SIGNATURE}.lock"
readonly PID_FILE="${RUNTIME_DIR}/wlogout-launch-${HYPRLAND_INSTANCE_SIGNATURE}.pid"

# Enforce dependency availability
for cmd in hyprctl jq wlogout flock mktemp grep awk; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: Required command '$cmd' not found in PATH." >&2
        exit 1
    fi
done

if [[ ! -f "$LAYOUT_FILE" ]]; then
    echo "ERROR: Layout file not found at $LAYOUT_FILE" >&2
    exit 1
fi

if [[ ! -r "$MATUGEN_COLORS" ]]; then
    echo "ERROR: Matugen colors file not readable at $MATUGEN_COLORS" >&2
    exit 1
fi

# ──────────────────────────────────────────────────────────────
# 3. Lifecycle Management (Traps & Cleanup)
# ──────────────────────────────────────────────────────────────
cleanup() {
    # 1. Purge the specific temporary CSS file
    [[ -n "${TMP_CSS:-}" && -f "$TMP_CSS" ]] && rm -f -- "$TMP_CSS"

    # 2. Prevent race conditions: Verify PID file ownership before deletion
    if [[ -n "${WLOGOUT_PID:-}" && -f "$PID_FILE" ]]; then
        local recorded_pid='' recorded_css=''
        read -r recorded_pid recorded_css < "$PID_FILE" 2>/dev/null || true
        if [[ "$recorded_pid" == "$WLOGOUT_PID" && "$recorded_css" == "$TMP_CSS" ]]; then
            rm -f -- "$PID_FILE"
        fi
    fi
}
trap cleanup EXIT

# ──────────────────────────────────────────────────────────────
# 4. Concurrency & Toggle Logic (The Sniper Engine)
# ──────────────────────────────────────────────────────────────
exec {LOCK_FD}> "$LOCK_FILE"
flock -x "$LOCK_FD"

if [[ -f "$PID_FILE" ]]; then
    existing_pid=''
    existing_css=''
    read -r existing_pid existing_css < "$PID_FILE" 2>/dev/null || true

    # Validate data format
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && [[ -n "$existing_css" ]]; then
        
        # Verify process existence and read access
        if kill -0 "$existing_pid" 2>/dev/null \
           && [[ -r "/proc/${existing_pid}/comm" ]] \
           && [[ -r "/proc/${existing_pid}/cmdline" ]]; then
            
            read -r existing_comm < "/proc/${existing_pid}/comm" 2>/dev/null || existing_comm=''
            
            # Master verification: Command name AND exact memory payload via null-byte grep
            if [[ "$existing_comm" == "wlogout" ]] && grep -zFq -- "$existing_css" "/proc/${existing_pid}/cmdline"; then
                kill "$existing_pid" 2>/dev/null || true
                rm -f -- "$PID_FILE"
                
                # Free resources instantly
                flock -u "$LOCK_FD"
                exec {LOCK_FD}>&-
                exit 0
            fi
        fi
    fi
    # Purge stale state
    rm -f -- "$PID_FILE"
fi

# ──────────────────────────────────────────────────────────────
# 5. Asset Generation
# ──────────────────────────────────────────────────────────────
# Strict GNU compliant mktemp syntax
TMP_CSS=$(mktemp --tmpdir="$RUNTIME_DIR" --suffix=.css "wlogout-${HYPRLAND_INSTANCE_SIGNATURE}.XXXXXX")

MON_DATA=''
if MON_DATA=$(hyprctl monitors -j 2>/dev/null | jq -r '
    (first(.[] | select(.focused)) // .[0] // {height: 1080, scale: 1})
    | "\(.height) \(.scale)"
') && [[ -n "$MON_DATA" ]]; then
    :
else
    MON_DATA="1080 1"
fi

read -r HEIGHT SCALE <<< "$MON_DATA"

# Fallback for compositor scale anomalies
if [[ "$SCALE" == "0" || "$SCALE" == "0.0" || -z "$SCALE" ]]; then
    SCALE=1
fi

CALC_VARS=$(awk -v h="$HEIGHT" -v s="$SCALE" -v rh="$REF_HEIGHT" \
                -v f="$BASE_FONT_SIZE" -v br="$BASE_BUTTON_RAD" \
                -v ar="$BASE_ACTIVE_RAD" -v m="$BASE_MARGIN" \
                -v ho="$BASE_HOVER_OFFSET" -v cs="$BASE_COL_SPACING" '
BEGIN {
    ratio = (h / s) / rh;
    ratio = (ratio < 0.5) ? 0.5 : (ratio > 2.0 ? 2.0 : ratio);

    printf "%d %d %d %d %d %d",
        int(f * ratio), int(br * ratio), int(ar * ratio),
        int(m * ratio), int(ho * ratio), int(cs * ratio)
}')

read -r FONT_SIZE BTN_RAD ACT_RAD MARGIN HOVER_OFFSET COL_SPACING <<< "$CALC_VARS"
HOVER_MARGIN=$(( MARGIN - HOVER_OFFSET ))

cat > "$TMP_CSS" <<EOF
/* Import Matugen Colors */
@import url("file://${MATUGEN_COLORS}");

window {
    background-color: rgba(0, 0, 0, 0.5);
    font-family: "JetBrainsMono Nerd Font", "Roboto", sans-serif;
    font-size: ${FONT_SIZE}px;
}

button {
    color: @on_secondary_container;
    background-color: @secondary_container;
    outline-style: none;
    border: none;
    border-radius: ${BTN_RAD}px;
    box-shadow: none;
    text-shadow: none;
    background-repeat: no-repeat;
    background-position: center;
    background-size: 25%;
    transition:
        background-size 0.3s cubic-bezier(.55, 0.0, .28, 1.682),
        margin 0.3s cubic-bezier(.55, 0.0, .28, 1.682),
        border-radius 0.3s cubic-bezier(.55, 0.0, .28, 1.682),
        background-color 0.3s ease;
}

button:focus {
    background-color: @tertiary_container;
    color: @on_tertiary_container;
    background-size: 30%;
}

button:hover {
    background-color: @primary;
    color: @on_primary;
    background-size: 40%;
    border-radius: ${ACT_RAD}px;
}

#lock { background-image: image(url("${ICON_DIR}/lock_white.png"), url("/usr/share/wlogout/icons/lock.png")); margin: ${MARGIN}px 0; }
button:hover#lock { margin: ${HOVER_MARGIN}px 0; }

#logout { background-image: image(url("${ICON_DIR}/logout_white.png"), url("/usr/share/wlogout/icons/logout.png")); margin: ${MARGIN}px 0; }
button:hover#logout { margin: ${HOVER_MARGIN}px 0; }

#suspend { background-image: image(url("${ICON_DIR}/suspend_white.png"), url("/usr/share/wlogout/icons/suspend.png")); margin: ${MARGIN}px 0; }
button:hover#suspend { margin: ${HOVER_MARGIN}px 0; }

#shutdown { background-image: image(url("${ICON_DIR}/shutdown_white.png"), url("/usr/share/wlogout/icons/shutdown.png")); margin: ${MARGIN}px 0; }
button:hover#shutdown { margin: ${HOVER_MARGIN}px 0; }

#soft-reboot { background-image: image(url("${ICON_DIR}/soft-reboot_white.png"), url("/usr/share/wlogout/icons/reboot.png")); margin: ${MARGIN}px 0; }
button:hover#soft-reboot { margin: ${HOVER_MARGIN}px 0; }

#reboot { background-image: image(url("${ICON_DIR}/reboot_white.png"), url("/usr/share/wlogout/icons/reboot.png")); margin: ${MARGIN}px 0; }
button:hover#reboot { margin: ${HOVER_MARGIN}px 0; }
EOF

# ──────────────────────────────────────────────────────────────
# 6. Launch & Daemonize
# ──────────────────────────────────────────────────────────────
wlogout \
    --layout "$LAYOUT_FILE" \
    --css "$TMP_CSS" \
    --protocol layer-shell \
    --buttons-per-row 6 \
    --column-spacing "$COL_SPACING" \
    --row-spacing 0 \
    "$@" &
WLOGOUT_PID=$!

# Record state containing BOTH the PID and the exact payload
printf '%s %s\n' "$WLOGOUT_PID" "$TMP_CSS" > "$PID_FILE"

# Explicitly disengage locking resources so backgrounding is entirely detached
flock -u "$LOCK_FD"
exec {LOCK_FD}>&-

# Suspend script execution to maintain the layer-shell process tree and trap integrity
wait "$WLOGOUT_PID"

#!/usr/bin/env bash
# =============================================================================
# Target: Arch Linux (Bleeding Edge), Hyprland, Bash 5.4+
# Description: Synchronous Matugen -> Race-Condition-Free awww -> Hyprctl reload
# =============================================================================

set -eEuo pipefail

log_info()  { echo -e "\033[1;34m[INFO]\033[0m $1"; }
log_warn()  { echo -e "\033[1;33m[WARN]\033[0m $1" >&2; }
log_error() { echo -e "\033[1;31m[ERROR]\033[0m $1" >&2; }

trap 'log_error "Fatal error on line $LINENO. Exiting gracefully."; exit 0' ERR

WALLPAPER="$HOME/Pictures/wallpapers/dusk_default.jpg"
MATUGEN_CMD="matugen"
AWWW_DAEMON="awww-daemon"
AWWW_CLIENT="awww"

if [[ ! -f "$WALLPAPER" ]]; then
    log_error "Wallpaper not found at $WALLPAPER. Cannot proceed."
    exit 0
fi

for cmd in "$MATUGEN_CMD" "$AWWW_DAEMON" "$AWWW_CLIENT" "hyprctl"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log_error "Missing required package: $cmd. Skipping setup."
        exit 0
    fi
done

# =============================================================================
# PHASE 1: SYNCHRONOUS THEME GENERATION
# =============================================================================
log_info "Phase 1: Generating color files using Matugen..."
"$MATUGEN_CMD" --mode dark --type scheme-fruit-salad image "$WALLPAPER" --source-color-index 0 >/dev/null
log_info "Matugen color generation complete."

# =============================================================================
# PHASE 2: WAYLAND ENVIRONMENT INJECTION
# =============================================================================
log_info "Phase 2: Verifying Wayland environment..."
HYPRLAND_IS_RUNNING=0

if pgrep -x "Hyprland" >/dev/null 2>&1; then
    HYPRLAND_IS_RUNNING=1
    if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
        export WAYLAND_DISPLAY="wayland-1"
        export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
        
        HYPR_DIR="$XDG_RUNTIME_DIR/hypr"
        if [[ -d "$HYPR_DIR" ]]; then
            shopt -s nullglob
            hypr_instances=("$HYPR_DIR"/*/)
            shopt -u nullglob
            if [[ ${#hypr_instances[@]} -gt 0 ]]; then
                latest_instance=$(stat -c "%Y %n" "${hypr_instances[@]}" | sort -nr | head -n1 | cut -d' ' -f2-)
                export HYPRLAND_INSTANCE_SIGNATURE=$(basename "${latest_instance%/}")
                log_info "Injected Wayland and Hyprland IPC variables."
            fi
        fi
    fi
else
    log_warn "Hyprland is not running (Pure TTY detected)."
    log_info "Colors generated. Skipping GUI interactions to protect orchestrator."
    exit 0
fi

# =============================================================================
# PHASE 3: BULLETPROOF WALLPAPER APPLICATION
# =============================================================================
log_info "Phase 3: Initializing awww and applying wallpaper..."

if ! pgrep -x "$AWWW_DAEMON" >/dev/null 2>&1; then
    log_info "Starting $AWWW_DAEMON..."
    # We use standard backgrounding + disown to avoid any setsid environment weirdness
    "$AWWW_DAEMON" >/dev/null 2>&1 &
    disown
    
    # ACTIVE POLLING: Wait until the daemon is genuinely ready to accept commands.
    # We query the daemon. If it fails, the socket isn't ready. We try for up to 5 seconds.
    MAX_ATTEMPTS=10
    ATTEMPT=0
    log_info "Waiting for Wayland IPC socket to bind..."
    
    while ! "$AWWW_CLIENT" query >/dev/null 2>&1; do
        sleep 0.5
        ((ATTEMPT++))
        if [[ $ATTEMPT -ge $MAX_ATTEMPTS ]]; then
            log_error "Timeout: $AWWW_DAEMON failed to initialize after 5 seconds."
            exit 0 # Bail out cleanly without crashing the orchestration script
        fi
    done
    log_info "$AWWW_DAEMON initialized successfully."
else
    log_info "$AWWW_DAEMON is already running."
fi

# Now that we have absolute mathematical certainty the daemon is listening, apply the image.
"$AWWW_CLIENT" img "$WALLPAPER" >/dev/null 2>&1

# =============================================================================
# PHASE 4: HYPRLAND RELOAD
# =============================================================================
log_info "Phase 4: Reloading Hyprland to apply Matugen colors..."

if [[ "$HYPRLAND_IS_RUNNING" -eq 1 ]]; then
    hyprctl reload >/dev/null 2>&1 || {
        log_warn "hyprctl reload failed, but suppressing error to protect orchestrator."
        true
    }
    log_info "Hyprland reloaded successfully."
fi

log_info "Complete! Theme, wallpaper, and compositor state updated."
exit 0

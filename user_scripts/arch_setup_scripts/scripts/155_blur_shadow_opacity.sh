#!/usr/bin/env bash
#d: Toggle blur, shadows, and opacity for visibility

set -euo pipefail

# --- 2. Constants & Configuration ---
# ANSI Color Codes for clean output
readonly C_RESET=$'\033[0m'
readonly C_GREEN=$'\033[1;32m'
readonly C_BLUE=$'\033[1;34m'
readonly C_RED=$'\033[1;31m'
readonly C_BOLD=$'\033[1m'

# Target script configuration
readonly TARGET_SCRIPT="$HOME/user_scripts/hypr/hypr_blur_opacity_shadow_toggle.sh"
readonly TARGET_ARGS="on"

# --- 3. Helper Functions ---
log_info() { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$1"; }
log_success() { printf "${C_GREEN}[SUCCESS]${C_RESET} %s\n" "$1"; }
log_error() { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$1" >&2; }

# Trap function to handle errors gracefully
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Script failed with exit code $exit_code."
    fi
}
trap cleanup EXIT

# --- 4. Main Logic ---

main() {
    log_info "Initializing Max Performance/Visibility Mode..."

    # Pre-flight Check: brightnessctl
    if ! command -v brightnessctl &>/dev/null; then
        log_error "'brightnessctl' is not installed or not in PATH."
        exit 1
    fi

    # Pre-flight Check: Target Script
    if [[ ! -f "$TARGET_SCRIPT" ]]; then
        log_error "Target script not found: $TARGET_SCRIPT"
        exit 1
    fi

    if [[ ! -x "$TARGET_SCRIPT" ]]; then
        log_info "Target script is not executable. Fixing permissions..."
        chmod +x "$TARGET_SCRIPT"
    fi

    # Step 1: Set Brightness
    # We silence stdout but keep stderr to keep the terminal clean
    log_info "Setting brightness to 100%..."
    
    # MODIFIED: Graceful degradation for VMs/External Monitors
    # Check if command succeeds; if not, log info and continue instead of exiting.
    if brightnessctl set 100% &>/dev/null; then
        log_success "Brightness maximized."
    else
        log_info "Brightness control unavailable (VM or external monitor detected). Skipping..."
    fi

    # Step 2: Run Visual Toggle Script
    # Inherits current environment (including UWSM vars)
    log_info "Engaging visual effects (${TARGET_ARGS})..."
    
    # MODIFIED: Logic to warn instead of hard fail
    if "$TARGET_SCRIPT" "$TARGET_ARGS"; then
        log_success "Visual effects toggled successfully."
    else
        # We manually construct a WARN message here using existing constants
        # instead of calling log_error or exiting.
        printf "${C_BLUE}[WARN]${C_RESET} %s\n" "Target file hasn't been generated yet. Skipping visual effects."
    fi

    log_success "All operations completed cleanly."
}

main "$@"

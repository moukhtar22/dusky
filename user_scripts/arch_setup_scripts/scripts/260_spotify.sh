#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Script: 044_spotify.sh
# Description: Installs Spotify via Paru/Yay and runs SpotX for ad-blocking.
#              Optimized for Arch Linux (Bash 5.3+).
# -----------------------------------------------------------------------------

# --- Strict Error Handling ---
set -euo pipefail
IFS=$'\n\t'

# --- Styling & Colors ---
readonly C_RESET=$'\033[0m'
readonly C_BOLD=$'\033[1m'
readonly C_INFO=$'\033[34m'    # Blue
readonly C_SUCCESS=$'\033[32m' # Green
readonly C_ERR=$'\033[31m'     # Red
readonly C_WARN=$'\033[33m'    # Yellow

# --- Configuration ---
readonly SPOTX_URL="https://spotx-official.github.io/run.sh"
SPOTX_TMP=""

# --- Logging Helpers ---
log_info()    { printf "${C_BOLD}${C_INFO}[INFO]${C_RESET} %s\n" "$1"; }
log_success() { printf "${C_BOLD}${C_SUCCESS}[OK]${C_RESET} %s\n" "$1"; }
log_error()   { printf "${C_BOLD}${C_ERR}[ERROR]${C_RESET} %s\n" "$1" >&2; }

# --- Cleanup Trap ---
cleanup() {
    local exit_code=$?
    if [[ -n "$SPOTX_TMP" && -f "$SPOTX_TMP" ]]; then
        rm -f "$SPOTX_TMP"
    fi
    if [[ $exit_code -ne 0 ]]; then
        log_error "Script failed with exit code $exit_code."
    fi
}
trap cleanup EXIT

# --- Global Variables ---
AUR_HELPER=""

# --- Functions ---

detect_aur_helper() {
    if [[ $EUID -eq 0 ]]; then
        log_error "Do not run as root. AUR helpers require a non-root user."
        exit 1
    fi

    if command -v paru &>/dev/null; then
        AUR_HELPER="paru"
    elif command -v yay &>/dev/null; then
        AUR_HELPER="yay"
    else
        log_error "Required AUR helper not found. Install 'paru' or 'yay' first."
        exit 1
    fi
}

install_packages() {
    local helper="$1"
    shift
    local packages=("$@")

    # Local IFS override to ensure log formatting is clean (space-separated)
    log_info "Installing/Verifying packages: $(IFS=' '; echo "${packages[*]}")"
    
    # --needed: Idempotency (skip if installed)
    # --noconfirm: Automated install
    if "$helper" -S --needed --noconfirm "${packages[@]}"; then
        log_success "Packages installed/verified."
    else
        log_error "Failed to install packages via $helper."
        exit 1
    fi
}

run_spotx() {
    log_info "Preparing SpotX installation..."
    SPOTX_TMP=$(mktemp)
    
    log_info "Downloading SpotX from official source..."
    # -s: Silent, -S: Show errors, -L: Follow redirects, -f: Fail on HTTP error
    if curl -sSLf "$SPOTX_URL" -o "$SPOTX_TMP"; then
        log_success "Download complete."
    else
        log_error "Failed to download SpotX script. Check internet connection."
        exit 1
    fi

    log_info "Executing SpotX..."
    
    # CRITICAL FIX 1: Use <(cat ...) to force "Pipe Mode" behavior.
    # This prevents SpotX from detecting it's a file and failing 'Client Detection'.
    #
    # CRITICAL FIX 2: Add '-f' (Force) flag.
    # Because we use '--needed' above, Spotify is NOT reinstalled if up-to-date.
    # Without '-f', SpotX sees the existing patch, exits with Code 1 (Warning),
    # and crashes our script. '-f' forces a re-run/overwrite and exits Code 0.
    bash <(cat "$SPOTX_TMP") -f
}

# --- Main Logic ---

# 1. User Confirmation
printf "${C_BOLD}${C_WARN}[?]${C_RESET} Do you want to install/update Spotify? [y/N] "
read -r response

if [[ "${response,,}" != "y" && "${response,,}" != "yes" ]]; then
    log_info "Operation cancelled by user."
    exit 0
fi

# 2. Environment Setup
detect_aur_helper

# 3. Install Spotify + Dependencies
# Explicitly including 'unzip' and 'perl' as SpotX hard dependencies
install_packages "$AUR_HELPER" spotify unzip perl

# 4. Run SpotX
run_spotx

log_success "Process finished. Spotify is ready."

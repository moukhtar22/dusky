#!/usr/bin/env bash
# ==============================================================================
#  NEOVIM PLUGIN SYNCHRONIZER (HEADLESS)
# ==============================================================================
#  Context: Arch Linux / Hyprland
#  Purpose: Bootstraps and syncs Lazy.nvim plugins efficiently
# ==============================================================================
set -euo pipefail

declare -r GREEN=$'\033[0;32m'
declare -r BLUE=$'\033[0;34m'
declare -r RED=$'\033[0;31m'
declare -r YELLOW=$'\033[0;33m'
declare -r BOLD=$'\033[1m'
declare -r RESET=$'\033[0m'

log_info() { printf '%s[INFO]%s %s\n' "$BLUE" "$RESET" "$*"; }
log_success() { printf '%s[SUCCESS]%s %s\n' "$GREEN" "$RESET" "$*"; }
log_warn() { printf '%s[WARN]%s %s\n' "$YELLOW" "$RESET" "$*"; }
log_error() { printf '%s[ERROR]%s %s\n' "$RED" "$RESET" "$*"; }

main() {
    log_info "Initializing Neovim Plugin Synchronization..."

    if ! command -v nvim &>/dev/null; then
        log_error "Neovim (nvim) is not installed or not in PATH."
        exit 1
    fi
    if ! command -v git &>/dev/null; then
        log_error "Git is not installed. Lazy.nvim requires git."
        exit 1
    fi
    if [[ ! -d "${HOME}/.config/nvim" ]]; then
        log_error "Neovim configuration directory (${HOME}/.config/nvim) not found."
        log_warn "Please ensure dotfiles are symlinked/copied before running this script."
        exit 1
    fi

    log_info "Verifying connectivity..."
    local has_net=false
    if command -v timeout &>/dev/null && timeout 5 bash -c 'exec 3<>/dev/tcp/github.com/443' &>/dev/null; then
        has_net=true
    elif command -v curl &>/dev/null && curl -Is --connect-timeout 5 https://github.com &>/dev/null; then
        has_net=true
    elif command -v getent &>/dev/null && getent hosts github.com &>/dev/null; then
        has_net=true
    fi
    if ! $has_net; then
        log_error "Cannot reach GitHub (checked tcp/443, curl, getent). Network is unreachable."
        exit 1
    fi

    log_info "Starting Headless Sync. This may take a moment..."
    log_warn "Output from Neovim will be shown below:"
    echo "--------------------------------------------------------------------------------"
    if nvim --headless "+Lazy! sync" +qa; then
        echo "--------------------------------------------------------------------------------"
        log_success "Neovim plugins synced successfully."
    else
        echo "--------------------------------------------------------------------------------"
        log_error "Neovim exited with an error code."
        exit 1
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi

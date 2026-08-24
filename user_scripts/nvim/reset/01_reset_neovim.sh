#!/usr/bin/env bash
# Resets all cache for Neovim — XDG-aware, safe-rm guarded
set -euo pipefail

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

log_info() { printf '%s[INFO]%s %s\n' "$BLUE" "$NC" "$*"; }
log_success() { printf '%s[SUCCESS]%s %s\n' "$GREEN" "$NC" "$*"; }
log_error() { printf '%s[ERROR]%s %s\n' "$RED" "$NC" "$*"; }

if [[ $EUID -eq 0 ]]; then
    printf '%s[ERROR] This script must be run as User, not Root.%s\n' "$RED" "$NC"
    exit 1
fi

is_safe_nvim_path() {
    local p="$1"
    [[ -n "$p" ]] || return 1
    [[ "$p" != "/" ]] || return 1
    [[ "$p" != "$HOME" ]] || return 1
    [[ "$p" == "$HOME"/* || "$p" == "${XDG_CONFIG_HOME:-$HOME/.config}"/* || "$p" == "${XDG_DATA_HOME:-$HOME/.local/share}"/* || "$p" == "${XDG_STATE_HOME:-$HOME/.local/state}"/* || "$p" == "${XDG_CACHE_HOME:-$HOME/.cache}"/* ]] || return 1
    [[ "$p" == *nvim* ]] || return 1
    return 0
}

declare -A TARGETS=(
    ["Lazy Lockfile"]="${HOME}/.config/nvim/lazy-lock.json"
    ["Data Directory"]="${HOME}/.local/share/nvim"
    ["State Directory"]="${HOME}/.local/state/nvim"
    ["Cache Directory"]="${HOME}/.cache/nvim"
)

main() {
    log_info "Starting Neovim state cleanup..."
    local name path
    for name in "${!TARGETS[@]}"; do
        path="${TARGETS[$name]}"
        if [[ -e "$path" ]]; then
            if ! is_safe_nvim_path "$path"; then
                log_error "Refusing to remove unsafe path: $path"
                continue
            fi
            rm -rf -- "$path"
            log_success "Removed $name: $path"
        else
            log_info "$name not found (Clean): $path"
        fi
    done
    log_success "Neovim state reset complete."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi

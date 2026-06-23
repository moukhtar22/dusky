#!/usr/bin/env bash
# ==============================================================================
# Script Name: 160_zram_config.sh
# Description: Configures base zram-generator for an Arch Linux installation.
#              Perfectly aligned with user-space script 205. Primes the system 
#              with Platinum Grade multi-tier ZSTD swap on first boot.
# Context:     Arch Linux Install (Chrooted Environment)
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# Constants & Configuration
# ------------------------------------------------------------------------------
readonly CONFIG_DIR="/etc/systemd/zram-generator.conf.d"
readonly CONFIG_FILE="${CONFIG_DIR}/99-elite-zram.conf"

# Aligned perfectly with user-space script 205
readonly ZRAM_SIZE_EXPR="ram"
readonly ZRAM_RESIDENT_LIMIT_EXPR="ram * 3 / 4"
readonly COMPRESSION_ALGORITHM="zstd(level=1) zstd(level=8) (type=idle)"

readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly BLUE=$'\033[0;34m'
readonly YELLOW=$'\033[0;33m'
readonly NC=$'\033[0m'

# ------------------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------------------
log_info()    { printf '%b %s\n' "${BLUE}[INFO]${NC}" "$*"; }
log_success() { printf '%b %s\n' "${GREEN}[SUCCESS]${NC}" "$*"; }
log_warn()    { printf '%b %s\n' "${YELLOW}[WARN]${NC}" "$*"; }
log_error()   { printf '%b %s\n' "${RED}[ERROR]${NC}" "$*" >&2; }

die() {
    log_error "$@"
    exit 1
}

check_chroot_root() {
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root inside the chroot environment."
    fi
}

# ------------------------------------------------------------------------------
# Main Logic
# ------------------------------------------------------------------------------
main() {
    check_chroot_root

    if [[ ! -f "/usr/lib/systemd/system-generators/zram-generator" ]]; then
        log_warn "zram-generator binary not found. Ensure it is installed before first boot."
    fi

    log_info "Preparing configuration directory..."
    install -d -m 0755 -- "$CONFIG_DIR"

    log_info "Drafting initial ZRAM swap configuration atomically..."
    
    local tmp_config
    tmp_config="$(mktemp "${CONFIG_DIR}/.99-elite-zram.conf.tmp.XXXXXX")"
    trap 'rm -f -- "$tmp_config"' EXIT

    # Note: [zram1] is intentionally omitted. It is delegated to the user-space 
    # configuration pipeline (script 206) for interactive hybrid backend resolution.
    cat >"$tmp_config" <<EOF
# Managed by Elite Arch Linux Installer.
# Base topology primed for first-boot.

[zram0]
zram-size = ${ZRAM_SIZE_EXPR}
zram-resident-limit = ${ZRAM_RESIDENT_LIMIT_EXPR}
compression-algorithm = ${COMPRESSION_ALGORITHM}
swap-priority = 100
options = discard
EOF

    chmod 0644 -- "$tmp_config"
    mv -f -- "$tmp_config" "$CONFIG_FILE"
    trap - EXIT

    log_success "Base ZRAM swap architecture generated successfully at ${CONFIG_FILE}"
}

main

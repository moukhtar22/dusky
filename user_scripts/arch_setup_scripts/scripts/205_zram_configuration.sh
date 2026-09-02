#!/usr/bin/env bash
#d: Configure ZRAM swap for maximum memory efficiency

set -euo pipefail

readonly SCRIPT_NAME="${0##*/}"
readonly SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"

# --- Formatting ---
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RESET=$'\033[0m'
    C_GREEN=$'\033[1;32m'
    C_BLUE=$'\033[1;34m'
    C_RED=$'\033[1;31m'
    C_YELLOW=$'\033[1;33m'
    C_BOLD=$'\033[1m'
else
    C_RESET='' C_GREEN='' C_BLUE='' C_RED='' C_YELLOW='' C_BOLD=''
fi

log_info()    { printf '%s[INFO]%s %s\n'  "$C_BLUE"   "$C_RESET" "$1"; }
log_success() { printf '%s[OK]%s %s\n'    "$C_GREEN"  "$C_RESET" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n'  "$C_YELLOW" "$C_RESET" "$1"; }
log_error()   { printf '%s[ERROR]%s %s\n' "$C_RED"    "$C_RESET" "$1" >&2; }
die()         { log_error "$1"; exit "${2:-1}"; }

log_critical_action() {
    printf '\n'
    printf '%s======================================================================%s\n' "${C_RED}${C_BOLD}" "${C_RESET}"
    printf '%s [!] ACTION REQUIRED: BOOTLOADER MODIFIED [!]%s\n' "${C_RED}${C_BOLD}" "${C_RESET}"
    printf '%s======================================================================%s\n' "${C_RED}${C_BOLD}" "${C_RESET}"
    printf '%s You MUST regenerate your initramfs/UKI before your next reboot.%s\n' "${C_YELLOW}" "${C_RESET}"
    printf '%s Failure to do so will result in ZSWAP remaining active on boot.%s\n' "${C_YELLOW}" "${C_RESET}"
    printf '\n'
    printf '%s Run this command at the very end of your setup:%s\n' "${C_GREEN}" "${C_RESET}"
    printf '   %smkinitcpio -P%s\n' "${C_BOLD}" "${C_RESET}"
    printf '%s======================================================================%s\n' "${C_RED}${C_BOLD}" "${C_RESET}"
    printf '\n'
}

print_help() {
    cat <<EOF
${C_BOLD}Usage:${C_RESET} ${SCRIPT_NAME} [OPTIONS]

  --size, -s <expr>           ZRAM size expression (auto-detected by RAM tier if omitted)
                              • <= 8GB RAM  -> "ram * 0.8" (80%)
                              • 8GB - 32GB  -> "ram * 0.5" (50%)
                              • >= 32GB RAM -> "ram * 0.2" (20%)
  --resident-limit, -r <expr> ZRAM resident limit expression (default: auto-detected)
  --priority, -p <prio>       Swap priority (default: 32767 - Maximum priority over disk swap)
  --algorithm, -a <algo>      Compression algorithm (default: "zstd(level=2)")
  --help, -h                  Show this help menu
EOF
}

usage_error() { log_error "$1"; print_help >&2; exit 2; }

# --- Dynamic RAM Tier Sizing Detection ---
declare -i RAM_KB=0
if [[ $(< /proc/meminfo) =~ MemTotal:[[:space:]]+([0-9]+) ]]; then
    RAM_KB=$(( BASH_REMATCH[1] ))
else
    RAM_KB=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
fi

declare -i RAM_MB=$(( RAM_KB / 1024 ))
declare -i RAM_GB=$(( (RAM_MB + 512) / 1024 ))

AUTO_SIZE_EXPR="ram * 0.5"
AUTO_LIMIT_EXPR="ram * 0.5"
TIER_DESC=""

# Leeway thresholds for kernel-reserved RAM accounting:
# 8GB raw hardware = ~7.5 - 7.8 GB in MemTotal (~8704 MB ceiling)
# 32GB raw hardware = ~30.5 - 31.8 GB in MemTotal (~31744 MB ceiling)
if (( RAM_MB <= 8704 )); then
    AUTO_SIZE_EXPR="ram * 0.8"
    AUTO_LIMIT_EXPR="ram * 0.5"
    TIER_DESC="<= 8GB RAM (${RAM_GB}GB detected) -> Tier: 80% RAM (0.8x)"
elif (( RAM_MB < 31744 )); then
    AUTO_SIZE_EXPR="ram * 0.5"
    AUTO_LIMIT_EXPR="ram * 0.5"
    TIER_DESC="8GB - 32GB RAM (${RAM_GB}GB detected) -> Tier: 50% RAM (0.5x)"
else
    AUTO_SIZE_EXPR="ram * 0.2"
    AUTO_LIMIT_EXPR="ram * 0.2"
    TIER_DESC=">= 32GB RAM (${RAM_GB}GB detected) -> Tier: 20% RAM (0.2x)"
fi

# --- CLI Parsing ---
ZRAM_SIZE_EXPR=""
ZRAM_RESIDENT_LIMIT_EXPR=""
SWAP_PRIORITY="32767"
COMPRESSION_ALGORITHM="zstd(level=2)"

ORIG_ARGS=("$@")

while [[ $# -gt 0 ]]; do
    case "$1" in
        --size|-s)
            [[ $# -ge 2 ]] || usage_error "Missing value for $1"
            ZRAM_SIZE_EXPR="$2"
            shift 2
            ;;
        --resident-limit|-r)
            [[ $# -ge 2 ]] || usage_error "Missing value for $1"
            ZRAM_RESIDENT_LIMIT_EXPR="$2"
            shift 2
            ;;
        --priority|-p)
            [[ $# -ge 2 ]] || usage_error "Missing value for $1"
            SWAP_PRIORITY="$2"
            shift 2
            ;;
        --algorithm|-a)
            [[ $# -ge 2 ]] || usage_error "Missing value for $1"
            COMPRESSION_ALGORITHM="$2"
            shift 2
            ;;
        --help|-h) print_help; exit 0 ;;
        *) usage_error "Unknown argument: $1" ;;
    esac
done

# Fallback to auto-detected dynamic tiers if not explicitly overridden
if [[ -z "$ZRAM_SIZE_EXPR" ]]; then
    ZRAM_SIZE_EXPR="$AUTO_SIZE_EXPR"
    log_info "Auto-detected Memory Tier: ${C_BOLD}${TIER_DESC}${C_RESET}"
else
    log_info "Manual ZRAM Size Override: ${C_BOLD}${ZRAM_SIZE_EXPR}${C_RESET}"
fi

if [[ -z "$ZRAM_RESIDENT_LIMIT_EXPR" ]]; then
    ZRAM_RESIDENT_LIMIT_EXPR="$AUTO_LIMIT_EXPR"
fi

# --- Privilege Escalation ---
if [[ $EUID -ne 0 ]]; then
    log_info "Root privileges required. Escalating..."
    command -v sudo >/dev/null 2>&1 || die "sudo is required to run this script as root."
    exec sudo -- bash -- "$SELF_PATH" "${ORIG_ARGS[@]}"
fi

# --- Dependency Checks ---
for cmd in systemctl grep sed; do
    command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' is required but missing."
done

readonly CMDLINE_FILE="/etc/kernel/cmdline"
readonly CONFIG_DIR="/etc/systemd/zram-generator.conf.d"
readonly CONFIG_FILE="${CONFIG_DIR}/99-elite-zram.conf"

readonly ZRAM_SWAP_DEV="/dev/zram0"
readonly ZRAM_SIZE_EXPR
readonly ZRAM_RESIDENT_LIMIT_EXPR
readonly SWAP_PRIORITY
readonly COMPRESSION_ALGORITHM

readonly GENERATOR_BIN="/usr/lib/systemd/system-generators/zram-generator"
readonly SWAP_SETUP_UNIT="systemd-zram-setup@zram0.service"
readonly SWAP_UNIT="dev-zram0.swap"

tmp_config="$(umask 077 && mktemp)"
trap 'rm -f "$tmp_config"' EXIT

unit_is_loaded() {
    [[ "$(systemctl show -p LoadState --value "$1" 2>/dev/null || true)" == "loaded" ]]
}

assert_unit_loaded() {
    local unit=$1
    unit_is_loaded "$unit" || die "Expected generated unit is not loaded after daemon-reload: $unit"
}

if systemd-detect-virt --quiet --container; then
    log_warn "Container detected. zram-generator does nothing inside containers; skipping."
    exit 0
fi

# =============================================================================
# --- 1. ZSWAP ANNIHILATION ---
# =============================================================================

log_info "Verifying ZSWAP status..."

readonly ZSWAP_PARAM="/sys/module/zswap/parameters/enabled"
if [[ -w "$ZSWAP_PARAM" ]]; then
    current_zswap=$(<"$ZSWAP_PARAM")
    if [[ "$current_zswap" == "Y" || "$current_zswap" == "1" ]]; then
        log_info "Live patching: Disabling zswap in the running kernel..."
        echo 0 > "$ZSWAP_PARAM" || log_warn "Failed to live-disable zswap."
    else
        log_success "Live memory: ZSWAP is cleanly disabled."
    fi
else
    log_warn "Zswap parameter not found. Kernel might not have zswap compiled in."
fi

if [[ -f "$CMDLINE_FILE" ]]; then
    declare -i needs_cmdline_update=0
    
    if grep -q -E '(^|[[:space:]])zswap\.enabled=0([[:space:]]|$)' "$CMDLINE_FILE"; then
        log_success "Bootloader: zswap.enabled=0 is perfectly configured."
    else
        log_info "Bootloader: Patching $CMDLINE_FILE to enforce zswap.enabled=0..."
        sed -i -E 's/[[:space:]]*zswap\.enabled=[^[:space:]]*//g' "$CMDLINE_FILE"
        sed -i -E 's/[[:space:]]+$//' "$CMDLINE_FILE"
        sed -i -E 's/$/ zswap.enabled=0/' "$CMDLINE_FILE"
        needs_cmdline_update=1
    fi

    if (( needs_cmdline_update == 1 )); then
        log_success "Bootloader cmdline successfully patched."
        log_critical_action
    fi
else
    log_warn "$CMDLINE_FILE not found. If using GRUB, manually add 'zswap.enabled=0'."
fi

# =============================================================================
# --- 2. ZRAM SWAP CONFIGURATION ---
# =============================================================================

if [[ ! -x "$GENERATOR_BIN" ]]; then
    log_warn "zram-generator is missing. Auto-healing..."
    while [[ -f /var/lib/pacman/db.lck ]]; do
        log_warn "Pacman is currently locked. Waiting 3 seconds..."
        sleep 3
    done
    pacman -Sy --needed --noconfirm zram-generator || die "Auto-healing failed."
    log_success "zram-generator successfully bootstrapped."
fi

if grep -Eq '(^|[[:space:]])systemd\.zram=0([[:space:]]|$)' /proc/cmdline; then
    die "FATAL: Kernel cmdline explicitly disables zram device creation."
fi

install -d -m 0755 -- "$CONFIG_DIR"

cat > "$tmp_config" <<EOF
# Managed by Elite Arch Linux ZRAM Configurator.
[zram0]
zram-size = ${ZRAM_SIZE_EXPR}
zram-resident-limit = ${ZRAM_RESIDENT_LIMIT_EXPR}
compression-algorithm = ${COMPRESSION_ALGORITHM}
swap-priority = ${SWAP_PRIORITY}
options = discard
EOF

install -Dm0644 "$tmp_config" "$CONFIG_FILE"
log_success "ZRAM pool configuration written to ${CONFIG_FILE}"

# --- Mount & tmpfiles permissions ---
mkdir -p /mnt /etc/tmpfiles.d
chmod 0755 /mnt 2>/dev/null || true
if command -v setfacl >/dev/null 2>&1; then
    setfacl -b /mnt 2>/dev/null || true
fi

cat > /etc/tmpfiles.d/zram-mounts.conf <<'EOF'
# Managed by Dusky Memory & Swap Subsystem
d /mnt 0755 root root -
d /mnt/zram1 1777 root root -
z /mnt 0755 root root -
z /mnt/zram1 1777 root root -
EOF
if command -v systemd-tmpfiles >/dev/null 2>&1; then
    systemd-tmpfiles --create /etc/tmpfiles.d/zram-mounts.conf 2>/dev/null || true
fi

log_info "Reloading systemd daemon to ingest new architecture..."
systemctl daemon-reload

assert_unit_loaded "$SWAP_SETUP_UNIT"
assert_unit_loaded "$SWAP_UNIT"

systemctl restart "$SWAP_SETUP_UNIT" 2>/dev/null || true
systemctl restart "$SWAP_UNIT" 2>/dev/null || true

log_success "Platinum ZRAM (Pure Multi-Algorithm ZSTD @ Priority ${SWAP_PRIORITY}) swap architecture installed safely."
exit 0

#!/usr/bin/env bash
# Description: Optimize systemd-journald limits for minimal RAM footprint and high I/O throughput.
# Target: Arch Linux / systemd 262+

set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true

readonly SCRIPT_NAME="${0##*/}"
SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"
readonly SELF_PATH
readonly ORIG_ARGS=("$@")

readonly CONF_DIR="/etc/systemd/journald.conf.d"
readonly CONF_FILE="${CONF_DIR}/99-ram-optimization.conf"
readonly LEGACY_SVC="/etc/systemd/system/systemd-journald.service.d/99-cgroup-memory-limit.conf"

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RESET=$'\033[0m'
    C_GREEN=$'\033[1;32m'
    C_BLUE=$'\033[1;34m'
    C_YELLOW=$'\033[1;33m'
    C_RED=$'\033[1;31m'
    C_BOLD=$'\033[1m'
else
    C_RESET='' C_GREEN='' C_BLUE='' C_YELLOW='' C_RED='' C_BOLD=''
fi

log_info()    { printf '%s[INFO]%s %s\n'  "$C_BLUE"   "$C_RESET" "$1"; }
log_success() { printf '%s[OK]%s %s\n'    "$C_GREEN"  "$C_RESET" "$1"; }
log_warn()    { printf '%s[WARN]%s %s\n'  "$C_YELLOW" "$C_RESET" "$1"; }
log_error()   { printf '%s[ERROR]%s %s\n' "$C_RED"    "$C_RESET" "$1" >&2; }
die()         { log_error "$1"; exit "${2:-1}"; }

print_help() {
    cat <<EOF
${C_BOLD}Usage:${C_RESET} ${SCRIPT_NAME} [OPTIONS]

Optimize systemd-journald volatile RAM consumption, disk retention, and flush policies.
• <= 16GB class: RuntimeMaxUse=8M, RuntimeMaxFileSize=2M, Audit=no (ultra-lean RAM)
• > 16GB class:  RuntimeMaxUse=32M, RuntimeMaxFileSize=4M, Audit=keep

Options:
  -f, --force-rotate   Force journal rotation and vacuuming even if config is unchanged
  -n, --dry-run        Preview generated configuration and exit
  -h, --help           Show this help menu
EOF
}

declare -i DRY_RUN=0
declare -i FORCE_ROTATE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force-rotate|-f) FORCE_ROTATE=1; shift ;;
        --dry-run|-n)      DRY_RUN=1; shift ;;
        --help|-h)         print_help; exit 0 ;;
        *)                 log_warn "Ignoring unknown argument: $1"; shift ;;
    esac
done

if [[ $EUID -ne 0 && $DRY_RUN -eq 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die "'sudo' is required to run this script as root."
    log_info "Root privileges required. Escalating..."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "${ORIG_ARGS[@]}"
fi

log_info "Initializing systemd-journald memory and throughput optimizer..."

# --- Dynamic Hardware & Memory Tier Detection ---
declare -i RAM_KB=0
if [[ $(< /proc/meminfo) =~ MemTotal:[[:space:]]+([0-9]+) ]]; then
    RAM_KB=$(( BASH_REMATCH[1] ))
else
    RAM_KB=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
fi

declare -i RAM_MB=$(( RAM_KB / 1024 ))
declare -i RAM_GB=$(( (RAM_MB + 512) / 1024 ))

# 16GB tier cutoff: 17,825,792 KiB (17 GiB accounts for 16GB DIMMs with iGPU/UMA carve-outs)
# <= 16GB class: RuntimeMaxUse=8M, RuntimeMaxFileSize=2M, Audit=no (ultra-low boot RAM)
# > 16GB class:  RuntimeMaxUse=32M, RuntimeMaxFileSize=4M, Audit=keep (high headroom)
RUNTIME_MAX_USE="32M"
RUNTIME_MAX_FILE_SIZE="4M"
AUDIT_POLICY="keep"
TIER_DESC=""

if (( RAM_KB <= 17825792 )); then
    RUNTIME_MAX_USE="8M"
    RUNTIME_MAX_FILE_SIZE="2M"
    AUDIT_POLICY="no"
    TIER_DESC="Efficiency (<=16GB class, ${RAM_GB}GB detected) -> RuntimeMaxUse=8M, Audit=no"
else
    RUNTIME_MAX_USE="32M"
    RUNTIME_MAX_FILE_SIZE="4M"
    AUDIT_POLICY="keep"
    TIER_DESC="Performance (>16GB class, ${RAM_GB}GB detected) -> RuntimeMaxUse=32M, Audit=keep"
fi

log_info "Detected Memory Tier: ${C_BOLD}${TIER_DESC}${C_RESET}"

tmp_conf="$(umask 077 && mktemp)"
trap 'rm -f "$tmp_conf"' EXIT

cat > "$tmp_conf" <<EOF
# Managed by 213_systemd_journaling_optimizer.sh
# Scope: Cap volatile RAM consumption in /run/log/journal and persistent storage in /var/log/journal
# Tier: ${TIER_DESC}

[Journal]
Storage=persistent
Compress=yes

# Persistent disk caps (/var/log/journal): max 100M total, rotated at 16M
SystemMaxUse=100M
SystemMaxFileSize=16M
SystemMaxFiles=7
SystemKeepFree=500M

# Volatile tmpfs caps (/run/log/journal - RAM use): max ${RUNTIME_MAX_USE} total, rotated at ${RUNTIME_MAX_FILE_SIZE}
RuntimeMaxUse=${RUNTIME_MAX_USE}
RuntimeMaxFileSize=${RUNTIME_MAX_FILE_SIZE}
RuntimeMaxFiles=4
RuntimeKeepFree=16M

# Retention policy: 1 week max retention, 1 week max unrotated file lifetime
MaxRetentionSec=1week
MaxFileSec=1week

# Sync interval: 5m optimizes NVMe/SSD wear and battery while batching writes
SyncIntervalSec=5m

# Rate limiting: protects RAM and CPU from runaway service error loops
RateLimitIntervalSec=30s
RateLimitBurst=1000

# Log storage verbosity: suppress debug-level spam (retains emerg..info) to preserve RAM and disk endurance
# Note: To enable debug log storage temporarily, create a drop-in with MaxLevelStore=debug.
MaxLevelStore=info

# Audit subsystem ingestion: eliminate duplicate buffers on <=16GB, keep external policy on >16GB
Audit=${AUDIT_POLICY}

# Duplicate log forwarding suppression (saves IPC, CPU cycles, and memory buffers)
ForwardToSyslog=no
ForwardToKMsg=no
ForwardToConsole=no
ForwardToWall=no
EOF

if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED. Generated ${CONF_FILE}:"
    echo -e "\n${C_BOLD}[ ${CONF_FILE} ]${C_RESET}"
    cat "$tmp_conf"
    exit 0
fi

if [[ ! -d /var/log/journal ]]; then
    install -d -m 2755 -g systemd-journal /var/log/journal
    if command -v systemd-tmpfiles >/dev/null 2>&1; then
        systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true
    fi
fi

declare -i CHANGED=0

install -d -m 0755 "$CONF_DIR"
if [[ -f "$CONF_FILE" ]] && cmp -s "$tmp_conf" "$CONF_FILE"; then
    log_info "${CONF_FILE} is already up to date."
else
    if [[ -f "$CONF_FILE" ]]; then
        cp -p "$CONF_FILE" "${CONF_FILE}.bak"
        log_info "Created backup: ${CONF_FILE}.bak"
    fi
    install -Dm0644 "$tmp_conf" "$CONF_FILE"
    log_success "Updated ${CONF_FILE}"
    CHANGED=1
fi

if [[ -f "$LEGACY_SVC" ]]; then
    log_warn "Removing legacy service-level cgroup limit ${LEGACY_SVC}..."
    rm -f -- "$LEGACY_SVC"
    rmdir --ignore-fail-on-non-empty /etc/systemd/system/systemd-journald.service.d 2>/dev/null || true
    systemctl daemon-reload
    CHANGED=1
fi

if (( CHANGED == 1 || FORCE_ROTATE == 1 )); then
    log_info "Flushing in-memory volatile logs to disk..."
    journalctl --flush >/dev/null 2>&1 || true

    log_info "Reloading journald configuration dynamically via SIGHUP..."
    if ! systemctl kill --signal=HUP systemd-journald 2>/dev/null; then
        systemctl restart systemd-journald.service
    fi
    log_success "journald reloaded successfully."

    log_info "Enforcing retention bounds (vacuuming)..."
    journalctl --rotate --vacuum-size=100M --vacuum-time=1week --vacuum-files=7 >/dev/null 2>&1 || true
    log_success "Journals rotated and vacuumed to configured limits."
else
    log_success "systemd-journald is already optimized. (Use --force-rotate to trigger manual vacuum)."
fi

printf '\n%sCurrent Journal Footprint:%s\n' "$C_BOLD" "$C_RESET"
journalctl --disk-usage 2>/dev/null || true

exit 0

#!/usr/bin/env bash
#d: Cap systemd journal size and limits

set -euo pipefail
shopt -s inherit_errexit 2>/dev/null || true

readonly SCRIPT_NAME="${0##*/}"
readonly SELF_PATH="$(realpath -e -- "${BASH_SOURCE[0]}")"
orig_args=("$@")

# --- Target Configurations ---
readonly CONF_DIR="/etc/systemd/journald.conf.d"
readonly CONF_FILE="${CONF_DIR}/99-ram-optimization.conf"
readonly LEGACY_SVC="/etc/systemd/system/systemd-journald.service.d/99-cgroup-memory-limit.conf"

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

print_help() {
    cat <<EOF
${C_BOLD}Usage:${C_RESET} ${SCRIPT_NAME} [OPTIONS]

  --dry-run, -n        Print the generated configuration and exit
  --help, -h           Show this help menu
EOF
}

# --- 1. CLI Parsing ---
declare -i DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|-n)        DRY_RUN=1; shift ;;
        --help|-h)           print_help; exit 0 ;;
        *)                   log_warn "Ignoring unknown argument: $1"; shift ;;
    esac
done

# --- 2. Privilege Escalation ---
if [[ $EUID -ne 0 && $DRY_RUN -eq 0 ]]; then
    log_info "Root privileges required. Escalating..."
    command -v sudo >/dev/null 2>&1 || die "'sudo' is not available."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "${orig_args[@]}"
fi

log_info "Initializing systemd-journald optimizer..."

# --- 3. Temp File Generation ---
tmp_conf="$(umask 077 && mktemp)"
trap 'rm -f "$tmp_conf"' EXIT

# -----------------------------------------------------------------------------
# Layer 1: journald.conf - the correct way to cap RAM and disk
# -----------------------------------------------------------------------------
cat > "$tmp_conf" <<EOF
# Managed by ${SCRIPT_NAME}
# Scope: Cap volatile RAM journal and persistent disk journal sizes.

[Journal]
Storage=persistent
Compress=yes

# Persistent disk caps: max 100M total disk space, rotated at 16M
SystemMaxUse=100M
SystemMaxFileSize=16M
SystemMaxFiles=7
SystemKeepFree=500M

# Volatile tmpfs caps (/run/log/journal - real RAM use): max 32M total, rotated at 8M
RuntimeMaxUse=32M
RuntimeMaxFileSize=8M
RuntimeMaxFiles=4
RuntimeKeepFree=64M

# Rotation and retention: rotate at 1 week, expire after 1 week
MaxRetentionSec=1week
MaxFileSec=1week

# Flush configuration - default 5m is optimal for SSD / I-O performance
SyncIntervalSec=5m

# Rate limiting - prevents runaway log loops from wasting CPU/disk/RAM
RateLimitIntervalSec=30s
RateLimitBurst=1000

# Verbosity: ignore debug logs to reduce active log storage
MaxLevelStore=info

# Audit - disable reading audit messages from the kernel
Audit=no

# Forwarding - disable duplicate log forwarding to save IPC and CPU overhead.
# Disabling ForwardToWall also mitigates CVE-2026-40228 (terminal escape sequences).
ForwardToSyslog=no
ForwardToKMsg=no
ForwardToConsole=no
ForwardToWall=no
EOF

# --- 4. Dry Run Check ---
if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED. Would generate the following configuration:"
    echo -e "\n${C_BOLD}[ ${CONF_FILE} ]${C_RESET}"
    cat "$tmp_conf"
    exit 0
fi

# --- 5. Installation ---
declare -i CHANGED=0

# Ensure /var/log/journal exists with correct permissions
if [[ ! -d /var/log/journal ]]; then
    install -d -m 2755 -g systemd-journal /var/log/journal
    systemd-tmpfiles --create --prefix /var/log/journal >/dev/null 2>&1 || true
fi

# Install drop-in configuration
install -d -m 0755 "$CONF_DIR"
if [[ -f "$CONF_FILE" ]] && cmp -s "$tmp_conf" "$CONF_FILE"; then
    log_info "${CONF_FILE} is already up to date."
else
    install -Dm0644 "$tmp_conf" "$CONF_FILE"
    log_success "Updated ${CONF_FILE}"
    CHANGED=1
fi

# Clean up legacy dangerous cgroup limit if it exists from previous versions
if [[ -f "$LEGACY_SVC" ]]; then
    log_warn "Removing legacy dangerous cgroup limit ${LEGACY_SVC}"
    rm -f -- "$LEGACY_SVC"
    rmdir --ignore-fail-on-non-empty /etc/systemd/system/systemd-journald.service.d 2>/dev/null || true
    # Force systemd daemon-reload to clear the cgroup limit
    systemctl daemon-reload
    CHANGED=1
fi

# Reload configuration
if (( CHANGED == 1 )); then
    log_info "Reloading journald configuration..."
    # systemd >=258 supports SIGHUP reload, fallback to restart
    if ! systemctl kill --signal=HUP systemd-journald 2>/dev/null; then
        systemctl restart systemd-journald.service
    fi
    log_success "journald reloaded"
else
    log_success "No changes required. systemd-journald is already optimized."
fi

# --- 6. Live Vacuuming ---
log_info "Rotating and vacuuming journals to enforce limits immediately..."
# --rotate is required - vacuum only operates on archived files
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-size=100M --vacuum-time=1week >/dev/null 2>&1 || true

# Show status
journalctl --disk-usage 2>/dev/null || true

log_success "Logging topology is fully optimized for performance and RAM efficiency (safe)."
exit 0

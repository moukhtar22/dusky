#!/usr/bin/env bash
#d: Tune swappiness and VM policy for ZRAM

set -euo pipefail

readonly CONFIG_FILE="/etc/sysctl.d/99-vm-zram-parameters.conf"
readonly MGLRU_CONFIG="/etc/tmpfiles.d/99-mglru-optimize.conf"
readonly SCRIPT_NAME="${0##*/}"

# --- Save original args before shift destroys them (fix #1) ---
ORIG_ARGS=("$@")

# --- Strict Path Resolution ---
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

print_help() {
    cat <<EOF
${C_BOLD}Usage:${C_RESET} ${SCRIPT_NAME} [OPTIONS]

  --auto, -a           Auto-detect RAM size and set dynamic profile (default)
  --aggressive, -A     Force 32GB+ "Absolute Max" RAM usage profile
  --standard, -S       Force <32GB "Dynamic Efficiency" RAM savings profile
  --dry-run, -n        Print the generated config and exit without applying
  --help, -h           Show this help menu
EOF
}

usage_error() { log_error "$1"; print_help >&2; exit 2; }

# --- 1. CLI Parsing ---
MODE="AUTO"
declare -i DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto|-a)           MODE="AUTO"; shift ;;
        --aggressive|-A)     MODE="AGGRESSIVE"; shift ;;
        --standard|-S)       MODE="STANDARD"; shift ;;
        --dry-run|-n)        DRY_RUN=1; shift ;;
        --help|-h)           print_help; exit 0 ;;
        *)                   usage_error "Unknown argument: $1" ;;
    esac
done

# --- 2. Privilege Escalation (fixed: use ORIG_ARGS) ---
if [[ $EUID -ne 0 && $DRY_RUN -eq 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die "'sudo' is not available."
    log_info "Root privileges required. Escalating..."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "${ORIG_ARGS[@]}"
fi

# --- 3. System State Detection ---
declare -i SYSTEM_RAM_KB=0
declare -i SYSTEM_RAM_GB=0
declare -i ACTIVE_ZRAM_COUNT=0
declare -i ACTIVE_OTHER_COUNT=0
ZRAM_MAX_PRIO=""
OTHER_MAX_PRIO=""

if [[ $(< /proc/meminfo) =~ MemTotal:[[:space:]]+([0-9]+) ]]; then
    SYSTEM_RAM_KB=$(( BASH_REMATCH[1] ))
    SYSTEM_RAM_GB=$(( SYSTEM_RAM_KB / 1048576 ))
else
    die "FATAL: Could not parse /proc/meminfo natively."
fi

while read -r path _ _ _ prio; do
    [[ "$path" == "Filename" ]] && continue
    if [[ "$path" == /dev/zram* ]]; then
        ACTIVE_ZRAM_COUNT+=1
        if [[ -z "$ZRAM_MAX_PRIO" || "$prio" -gt "$ZRAM_MAX_PRIO" ]]; then ZRAM_MAX_PRIO="$prio"; fi
    elif [[ -n "$path" ]]; then
        ACTIVE_OTHER_COUNT+=1
        if [[ -z "$OTHER_MAX_PRIO" || "$prio" -gt "$OTHER_MAX_PRIO" ]]; then OTHER_MAX_PRIO="$prio"; fi
    fi
done < /proc/swaps

SWAP_LAYOUT="NONE"
if (( ACTIVE_ZRAM_COUNT > 0 && ACTIVE_OTHER_COUNT > 0 )); then
    SWAP_LAYOUT="HYBRID"
elif (( ACTIVE_ZRAM_COUNT > 0 )); then
    SWAP_LAYOUT="ZRAM_ONLY"
elif (( ACTIVE_OTHER_COUNT > 0 )); then
    SWAP_LAYOUT="DISK_ONLY"
fi

# --- 4. Tuning Profile Resolution ---
declare -i EXPECTED_SWAPPINESS
declare -i EXPECTED_VFS_PRESSURE
declare -i EXPECTED_SCALE_FACTOR
declare -i EXPECTED_DIRTY_BYTES
declare -i EXPECTED_DIRTY_BG_BYTES
declare -i EXPECTED_DIRTY_WRITEBACK
declare -i EXPECTED_DIRTY_EXPIRE
declare -i EXPECTED_MGLRU_TTL

# 30 GiB demarcation (note: GiB, not GB)
declare -i THRESHOLD_KB=$((30 * 1048576))
if [[ "$MODE" == "AGGRESSIVE" ]] || { [[ "$MODE" == "AUTO" ]] && (( SYSTEM_RAM_KB >= THRESHOLD_KB )); }; then
    EXPECTED_MODE="PERFORMANCE_LEAN (32GB+)"
    EXPECTED_SWAPPINESS=150
    EXPECTED_VFS_PRESSURE=100
    EXPECTED_SCALE_FACTOR=100          # 1.0% watermark boost
    EXPECTED_DIRTY_BYTES=1073741824    # 1GiB
    EXPECTED_DIRTY_BG_BYTES=268435456  # 256MiB
    EXPECTED_DIRTY_WRITEBACK=500       # 5s  (must be < expire)
    EXPECTED_DIRTY_EXPIRE=3000         # 30s (kernel default)
    EXPECTED_MGLRU_TTL=1000
else
    EXPECTED_MODE="STRICT_RAM_SAVINGS (<32GB)"
    EXPECTED_SWAPPINESS=190            # 0-200 valid since 5.8, 200=max
    EXPECTED_VFS_PRESSURE=200          # >100 valid, 1000 = 10x reclaim
    EXPECTED_SCALE_FACTOR=15           # 0.15% (15/10000)
    EXPECTED_DIRTY_BYTES=134217728     # 128MiB
    EXPECTED_DIRTY_BG_BYTES=33554432   # 32MiB
    # FIX #2: writeback must be < expire. Original 1000/500 was inverted.
    EXPECTED_DIRTY_WRITEBACK=100       # 1s  (was 1000, inverted)
    EXPECTED_DIRTY_EXPIRE=500          # 5s  (now > writeback, per docs)
    EXPECTED_MGLRU_TTL=100
fi

# Static Constants (verified still present in 7.1 docs)
readonly EXPECTED_PAGE_CLUSTER=0        # disables swap readahead, good for ZRAM
readonly EXPECTED_BOOST_FACTOR=0        # disables watermark boosting (default 15000, 0=off)
readonly EXPECTED_COMPACTION=0          # 0-100 valid, 0 disables proactive compaction
readonly EXPECTED_MAX_MAP_COUNT=1048576 # Arch default since 2024-04-07, was 65530. SteamOS uses 2147483642

# --- 5. Generation & Verification ---
log_info "Initializing Platinum ZRAM & VM Policy Optimizer (fixed)..."
log_info "Detected System RAM: ${C_BOLD}${SYSTEM_RAM_GB} GiB${C_RESET}"
log_info "Detected Swap Layout: ${C_BOLD}${SWAP_LAYOUT}${C_RESET} (${ACTIVE_ZRAM_COUNT} ZRAM / ${ACTIVE_OTHER_COUNT} Disk)"

if [[ "$SWAP_LAYOUT" == "DISK_ONLY" || "$SWAP_LAYOUT" == "NONE" ]]; then
    die "Active ZRAM swap is required to utilize this tuning profile."
fi

# Priority Inversion Safety Guard
if [[ "$SWAP_LAYOUT" == "HYBRID" && -n "$ZRAM_MAX_PRIO" && -n "$OTHER_MAX_PRIO" ]]; then
    if (( ZRAM_MAX_PRIO <= OTHER_MAX_PRIO )); then
        log_warn "PRIORITY INVERSION: Disk prio ${OTHER_MAX_PRIO} >= ZRAM prio ${ZRAM_MAX_PRIO}."
        log_warn "With swappiness=${EXPECTED_SWAPPINESS}, disk will be hit before ZRAM."
        log_warn "Fix /etc/systemd/zram-generator.conf priority (ZRAM should be highest, e.g. 100)."
    else
        log_info "Safety Check Passed: ZRAM (${ZRAM_MAX_PRIO}) overrides Disk (${OTHER_MAX_PRIO})."
    fi
fi

if [[ "$MODE" != "AUTO" ]]; then
    log_warn "Manual Override Engaged: Mode forced to ${C_BOLD}${EXPECTED_MODE}${C_RESET}"
fi

# Secure temp files — single trap handling all (fix #11)
tmpfile="$(umask 077 && mktemp)"
tmpfile_mglru="$(umask 077 && mktemp)"
tmpfile_limits="$(umask 077 && mktemp)"
tmpfile_sysd="$(umask 077 && mktemp)"
trap 'rm -f "$tmpfile" "$tmpfile_mglru" "$tmpfile_limits" "$tmpfile_sysd"' EXIT

# --- SYSCTL Payload (fixed for kernel 7.1 + systemd 261) ---
cat > "$tmpfile" <<EOF
# Managed by ${SCRIPT_NAME}
# Scope: Comprehensive ZRAM, Desktop Performance, & Network Matrix
# Detected State: Layout=${SWAP_LAYOUT}, Mode=${EXPECTED_MODE}, RAM=${SYSTEM_RAM_GB}GiB
# Kernel: 7.1.x (2026-06-14), systemd 261.1 (2026-06-26)

# --- SWAP CONFIGURATION ---
# vm.swappiness: locked high for ZRAM-optimized systems to force immediate 
# memory compression of inactive processes, freeing up physical memory.
vm.swappiness = ${EXPECTED_SWAPPINESS}
# vm.page-cluster: 0 disables swap readahead. While readahead helps slow HDDs,
# it causes latency spikes on random access structures like compressed ZRAM.
vm.page-cluster = ${EXPECTED_PAGE_CLUSTER}

# --- DESKTOP SNAPPINESS (VFS & CACHE) ---
# vm.vfs_cache_pressure: 100/200 reclaim inode/dentry caches to free memory.
vm.vfs_cache_pressure = ${EXPECTED_VFS_PRESSURE}
# vm.dirty_bytes: Maximum amount of memory (in bytes) that can be dirty before
# background processes start blocking and writing directly to disk.
# Caps memory footprint from large file transfers/downloads to prevent lag.
vm.dirty_bytes = ${EXPECTED_DIRTY_BYTES}
# vm.dirty_background_bytes: The threshold at which the kernel's background
# flusher threads are woken up to begin writing out dirty memory blocks to disk.
vm.dirty_background_bytes = ${EXPECTED_DIRTY_BG_BYTES}
# vm.dirty_writeback_centisecs: Defines how often (in hundredths of a second) the
# kernel flusher thread wakes up. 100 centiseconds = 1 second. Fast wakeups ensure
# that expired pages are cleaned rapidly, preventing write spikes on SSDs/NVMe.
vm.dirty_writeback_centisecs = ${EXPECTED_DIRTY_WRITEBACK}
# vm.dirty_expire_centisecs: Defines the maximum age (in hundredths of a second)
# of a dirty page before it is eligible to be written out. 500 = 5 seconds.
vm.dirty_expire_centisecs = ${EXPECTED_DIRTY_EXPIRE}

# --- MEMORY ALLOCATION & COMPACTION ---
# vm.watermark_scale_factor: Control scale factor of the watermark (high limit).
# Higher values keep a larger safety buffer of free pages before direct reclaim.
vm.watermark_scale_factor = ${EXPECTED_SCALE_FACTOR}
# vm.watermark_boost_factor: Disables watermark boosting (default 15000, 0=off).
# Watermark boosting creates large, sudden free page requirements that can 
# lead to direct reclaim stutters/UI lag during high memory usage spikes.
vm.watermark_boost_factor = ${EXPECTED_BOOST_FACTOR}
# vm.compaction_proactiveness: 0 disables proactive background memory compaction.
# Prevents random, silent CPU spikes from running memory compaction in the background.
vm.compaction_proactiveness = ${EXPECTED_COMPACTION}

# --- APPLICATION COMPATIBILITY ---
# vm.max_map_count: Caps the maximum number of memory maps a process can make.
# Arch Linux standard since April 2024 is 1048576 (was 65530). High maps are 
# required for Steam/Proton/Wine gaming compatibility (SteamOS uses 2147483642).
vm.max_map_count = ${EXPECTED_MAX_MAP_COUNT}

# --- MODERN NETWORK STACK (BBR + CAKE) ---
# net.ipv4.tcp_congestion_control: BBR handles congestion detection by measuring
# bottleneck bandwidth and round-trip times, offering far better throughput.
net.ipv4.tcp_congestion_control = bbr
# net.core.default_qdisc: CAKE (Common Applications Kept Enhanced) performs active
# queue management and fair queueing, preventing network bufferbloat on local 
# client interfaces. Modern kernels (>=4.20) pace BBR internally, making CAKE compatible.
net.core.default_qdisc = cake
# net.ipv4.tcp_rmem / tcp_wmem: Optimize min, default, and max TCP buffer sizes
# to allow high-throughput TCP window scaling.
net.ipv4.tcp_rmem = 4096 65536 4194304
net.ipv4.tcp_wmem = 4096 65536 4194304
# net.core.netdev_max_backlog: Default is 1000. Left at default to avoid overriding
# higher values set by system tools or VM network bridges.
# net.core.netdev_max_backlog = 16384

# --- eBPF PERFORMANCE (user requested max performance) ---
# net.core.bpf_jit_enable: 1 compiles eBPF programs on run instead of interpreting.
net.core.bpf_jit_enable = 1
# net.core.bpf_jit_harden: 0 disables JIT hardening (constant blinding xor operations).
# Disabling hardening yields maximum performance and lowers CPU usage when running
# eBPF structures (CachyOS/Performance-focused custom systems default to 0).
net.core.bpf_jit_harden = 0
EOF

# --- MGLRU Payload (path verified in 7.1 docs) ---
cat > "$tmpfile_mglru" <<EOF
# Managed by ${SCRIPT_NAME}
# Scope: MGLRU ZRAM Thrash Protection
# Writes N ms to /sys/kernel/mm/lru_gen/min_ttl_ms to prevent working set eviction
w /sys/kernel/mm/lru_gen/min_ttl_ms - - - - ${EXPECTED_MGLRU_TTL}
EOF

# Dry Run
if (( DRY_RUN == 1 )); then
    log_info "DRY RUN — generated configurations:"
    echo -e "\n${C_BOLD}[ ${CONFIG_FILE} ]${C_RESET}"
    cat "$tmpfile"
    echo -e "\n${C_BOLD}[ ${MGLRU_CONFIG} ]${C_RESET}"
    cat "$tmpfile_mglru"
    exit 0
fi

# --- Apply Sysctl (use -e to ignore unknown keys if watermark_boost_factor is removed in future) ---
if [[ -f "$CONFIG_FILE" ]] && cmp -s "$tmpfile" "$CONFIG_FILE"; then
    log_info "Sysctl configuration already matches desired state."
else
    install -Dm0644 "$tmpfile" "$CONFIG_FILE"
    log_success "Configuration written to ${CONFIG_FILE}"
fi

log_info "Applying sysctl parameters to live kernel..."
# Ensure BBR module is loaded if available
if ! sysctl -n net.ipv4.tcp_available_congestion_control 2>/dev/null | grep -qw bbr; then
    modprobe tcp_bbr 2>/dev/null || log_warn "tcp_bbr module not available, BBR may fail."
fi
modprobe sch_cake 2>/dev/null || true

sysctl -e --load "$CONFIG_FILE" >/dev/null || log_warn "Some sysctl keys were ignored (likely removed in this kernel)."

# --- Apply MGLRU Tmpfiles ---
if [[ -d "/sys/kernel/mm/lru_gen" ]]; then
    if [[ -f "$MGLRU_CONFIG" ]] && cmp -s "$tmpfile_mglru" "$MGLRU_CONFIG"; then
        log_info "MGLRU configuration already matches desired state."
    else
        install -Dm0644 "$tmpfile_mglru" "$MGLRU_CONFIG"
        log_success "MGLRU Protection written to ${MGLRU_CONFIG}"
    fi
    log_info "Applying MGLRU parameters..."
    systemd-tmpfiles --create "$MGLRU_CONFIG" || log_warn "Failed to apply tmpfiles for MGLRU (check /sys/kernel/mm/lru_gen/min_ttl_ms exists)."
else
    log_warn "MGLRU not present at /sys/kernel/mm/lru_gen — skipping min_ttl_ms protection."
fi

# --- NOFILE limits (systemd 261 still supports DefaultLimitNOFILE) ---
log_info "Optimizing open file limits..."

cat > "$tmpfile_limits" <<EOF
# Managed by ${SCRIPT_NAME}
* soft nofile 65536
* hard nofile 524288
EOF

if [[ -f "/etc/security/limits.d/99-nofile-limits.conf" ]] && cmp -s "$tmpfile_limits" "/etc/security/limits.d/99-nofile-limits.conf"; then
    log_info "PAM limits already match."
else
    install -Dm0644 "$tmpfile_limits" "/etc/security/limits.d/99-nofile-limits.conf"
    log_success "PAM limits written."
fi

cat > "$tmpfile_sysd" <<EOF
# Managed by ${SCRIPT_NAME}
[Manager]
DefaultLimitNOFILE=65536:524288
EOF

needs_reexec=0
if [[ -f "/etc/systemd/system.conf.d/99-nofile-limits.conf" ]] && cmp -s "$tmpfile_sysd" "/etc/systemd/system.conf.d/99-nofile-limits.conf"; then
    log_info "Systemd system limits already match."
else
    install -Dm0644 "$tmpfile_sysd" "/etc/systemd/system.conf.d/99-nofile-limits.conf"
    log_success "Systemd system limits written."
    needs_reexec=1
fi

if [[ -f "/etc/systemd/user.conf.d/99-nofile-limits.conf" ]] && cmp -s "$tmpfile_sysd" "/etc/systemd/user.conf.d/99-nofile-limits.conf"; then
    log_info "Systemd user limits already match."
else
    install -Dm0644 "$tmpfile_sysd" "/etc/systemd/user.conf.d/99-nofile-limits.conf"
    log_success "Systemd user limits written."
    needs_reexec=1
fi

if (( needs_reexec )); then
    systemctl daemon-reexec || true
fi

# --- Re-exec user manager instance for active sessions (fixed for systemd 261) ---
if command -v loginctl >/dev/null 2>&1; then
    while read -r uid username _; do
        [[ "$uid" =~ ^[0-9]+$ ]] || continue
        systemctl --user -M "${username}@.host" daemon-reexec >/dev/null 2>&1 || true
    done < <(loginctl --no-legend list-users 2>/dev/null || true)
fi

# --- Hardened Live Verification ---
actual_swappiness="$(< /proc/sys/vm/swappiness)"
actual_vfs="$(< /proc/sys/vm/vfs_cache_pressure)"
actual_scale="$(< /proc/sys/vm/watermark_scale_factor)"
actual_compaction="$(< /proc/sys/vm/compaction_proactiveness)"
actual_bpf_harden="$(< /proc/sys/net/core/bpf_jit_harden)"

[[ "$actual_swappiness" == "$EXPECTED_SWAPPINESS" ]] || die "Verification failed: vm.swappiness is '${actual_swappiness}', expected '${EXPECTED_SWAPPINESS}'."
[[ "$actual_vfs" == "$EXPECTED_VFS_PRESSURE" ]] || die "Verification failed: vm.vfs_cache_pressure is '${actual_vfs}', expected '${EXPECTED_VFS_PRESSURE}'."
[[ "$actual_scale" == "$EXPECTED_SCALE_FACTOR" ]] || die "Verification failed: vm.watermark_scale_factor is '${actual_scale}', expected '${EXPECTED_SCALE_FACTOR}'."
[[ "$actual_compaction" == "$EXPECTED_COMPACTION" ]] || die "Verification failed: vm.compaction_proactiveness is '${actual_compaction}', expected '${EXPECTED_COMPACTION}'."
[[ "$actual_bpf_harden" == "0" ]] || die "Verification failed: net.core.bpf_jit_harden is '${actual_bpf_harden}', expected '0' (performance mode)."

log_success "Verified live kernel values:"
log_success "  vm.swappiness = ${actual_swappiness}"
log_success "  vm.vfs_cache_pressure = ${actual_vfs}"
log_success "  vm.watermark_scale_factor = ${actual_scale}"
log_success "  vm.compaction_proactiveness = ${actual_compaction}"
log_success "  net.core.bpf_jit_harden = ${actual_bpf_harden} (hardening disabled / max perf)"
log_success "  net.ipv4.tcp_congestion_control = $(< /proc/sys/net/ipv4/tcp_congestion_control)"
log_success "  net.core.default_qdisc = $(< /proc/sys/net/core/default_qdisc)"

if [[ -f "/sys/kernel/mm/lru_gen/min_ttl_ms" ]]; then
    actual_ttl="$(< /sys/kernel/mm/lru_gen/min_ttl_ms)"
    if [[ "$actual_ttl" == "$EXPECTED_MGLRU_TTL" ]]; then
        log_success "  MGLRU min_ttl_ms = ${actual_ttl} (thrash protection active)"
    else
        log_warn "  MGLRU min_ttl_ms = ${actual_ttl}, expected ${EXPECTED_MGLRU_TTL}"
    fi
fi

log_success "  Active Tuning Profile: [${C_BOLD}${EXPECTED_MODE}${C_RESET}]"
exit 0

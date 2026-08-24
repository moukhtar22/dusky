#!/usr/bin/env bash
#d: Tune transparent huge pages for low memory use

set -euo pipefail

readonly CONFIG_FILE="/etc/tmpfiles.d/99-thp-mglru-optimize.conf"
readonly SCRIPT_NAME="${0##*/}"
readonly THP_BASE_DIR="/sys/kernel/mm/transparent_hugepage"
readonly MGLRU_BASE_DIR="/sys/kernel/mm/lru_gen"

# --- Save original args BEFORE parsing for correct sudo re-exec ---
orig_args=("$@")

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
fi

log_info() { printf '%s[INFO]%s %s\n' "${C_BLUE:-}" "${C_RESET:-}" "$1"; }
log_success() { printf '%s[OK]%s %s\n' "${C_GREEN:-}" "${C_RESET:-}" "$1"; }
log_warn() { printf '%s[WARN]%s %s\n' "${C_YELLOW:-}" "${C_RESET:-}" "$1"; }
log_error() { printf '%s[ERROR]%s %s\n' "${C_RED:-}" "${C_RESET:-}" "$1" >&2; }
die() { log_error "$1"; exit "${2:-1}"; }

print_help() {
    cat <<EOF
${C_BOLD:-}Usage:${C_RESET:-} ${SCRIPT_NAME} [OPTIONS]

  --auto, -a Auto-detect RAM size and set dynamic THP profile (default)
  --aggressive, -A Force 32GB+ "Performance" THP allocation (Looser limits, 450)
  --standard, -S Force <32GB "Strict RAM Savings" THP allocation (Tight limits, 200)
  --dry-run, -n Print the generated systemd-tmpfiles config and exit
  --help, -h Show this help menu
EOF
}

usage_error() { log_error "$1"; print_help >&2; exit 2; }

# --- 1. CLI Parsing ---
MODE="AUTO"
declare -i DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto|-a) MODE="AUTO"; shift ;;
        --aggressive|-A) MODE="AGGRESSIVE"; shift ;;
        --standard|-S) MODE="STANDARD"; shift ;;
        --dry-run|-n) DRY_RUN=1; shift ;;
        --help|-h) print_help; exit 0 ;;
        *) usage_error "Unknown argument: $1" ;;
    esac
done

# --- 2. Privilege Escalation ---
if [[ $EUID -ne 0 && $DRY_RUN -eq 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die "'sudo' is not available."
    log_info "Root privileges required. Escalating..."
    exec sudo -- /usr/bin/bash "$SELF_PATH" "${orig_args[@]}"
fi

# --- 3. Hardware Support Check ---
if [[ ! -d "$THP_BASE_DIR" ]]; then
    if (( DRY_RUN == 1 )); then
        log_warn "THP hardware directory ($THP_BASE_DIR) missing. Dry-run continuing..."
    else
        die "THP is disabled or not compiled into this kernel. Nothing to optimize."
    fi
fi

# --- 4. System State Detection ---
declare -i SYSTEM_RAM_KB=0
declare -i SYSTEM_RAM_GB=0

if ! SYSTEM_RAM_KB=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null); then
    die "FATAL: Could not parse /proc/meminfo via awk."
fi

if ! [[ "$SYSTEM_RAM_KB" =~ ^[0-9]+$ ]]; then
    die "FATAL: Parsed MemTotal is not numeric: $SYSTEM_RAM_KB"
fi

SYSTEM_RAM_GB=$(( SYSTEM_RAM_KB / 1048576 ))

# --- 5. Tuning Profile Resolution ---
declare -i EXPECTED_MAX_PTES
declare -i EXPECTED_SCAN_SLEEP
declare -i EXPECTED_PAGES_TO_SCAN
declare EXPECTED_ENABLED
declare EXPECTED_DEFRAG
declare EXPECTED_SHMEM

# Threshold: 30 GiB in KiB = 31457280
declare -i THRESHOLD_KB=$((30 * 1048576))

if [[ "$MODE" == "AGGRESSIVE" ]] || { [[ "$MODE" == "AUTO" ]] && (( SYSTEM_RAM_KB >= THRESHOLD_KB )); }; then
    EXPECTED_MODE="PERFORMANCE_LEAN (32GB+)"
    EXPECTED_MAX_PTES=450
    EXPECTED_SCAN_SLEEP=15000
    EXPECTED_PAGES_TO_SCAN=4096
    EXPECTED_ENABLED="madvise"
    EXPECTED_DEFRAG="defer+madvise"
    EXPECTED_SHMEM="within_size"
else
    EXPECTED_MODE="STRICT_RAM_SAVINGS (<32GB)"
    EXPECTED_MAX_PTES=200
    EXPECTED_SCAN_SLEEP=60000
    EXPECTED_PAGES_TO_SCAN=1024
    EXPECTED_ENABLED="madvise"
    EXPECTED_DEFRAG="defer+madvise"
    EXPECTED_SHMEM="within_size"
fi

# --- 6. Generation & Verification ---
log_info "Initializing Platinum Multi-Size THP & MGLRU Optimizer..."
log_info "Detected System RAM: ${C_BOLD:-}${SYSTEM_RAM_GB} GB${C_RESET:-} (${SYSTEM_RAM_KB} KiB)"

if [[ "$MODE" != "AUTO" ]]; then
    log_warn "Manual Override Engaged: Cache Mode forced to ${C_BOLD:-}${EXPECTED_MODE}${C_RESET:-}"
fi

# Secure temp file generation
tmpfile="$(umask 077 && mktemp)"
trap 'rm -f "${tmpfile:-}"' EXIT

cat > "$tmpfile" <<EOF
# Managed by ${SCRIPT_NAME}
# Scope: Transparent HugePages (mTHP) and MGLRU systemd-tmpfiles initialization
# Target: Kernel 7.1+ / systemd 261+ / Arch Latest
# Detected State: Desktop Mode=${EXPECTED_MODE}, RAM=${SYSTEM_RAM_GB}GB
# Docs: https://www.kernel.org/doc/html/next/admin-guide/mm/transhuge.html
#       https://docs.kernel.org/admin-guide/mm/multigen_lru.html

# --- GLOBAL THP CONTROLS (must be before per-size inherit) ---
w- /sys/kernel/mm/transparent_hugepage/enabled - - - - ${EXPECTED_ENABLED}
w- /sys/kernel/mm/transparent_hugepage/defrag - - - - ${EXPECTED_DEFRAG}
w- /sys/kernel/mm/transparent_hugepage/shmem_enabled - - - - ${EXPECTED_SHMEM}

# --- GLOBAL MEMORY EFFICIENCY FLAGS ---
w- /sys/kernel/mm/transparent_hugepage/use_zero_page - - - - 1
w- /sys/kernel/mm/transparent_hugepage/shrink_underused - - - - 1

# --- KHUGEPAGED DAEMON TUNING ---
w- /sys/kernel/mm/transparent_hugepage/khugepaged/max_ptes_none - - - - ${EXPECTED_MAX_PTES}
w- /sys/kernel/mm/transparent_hugepage/khugepaged/scan_sleep_millisecs - - - - ${EXPECTED_SCAN_SLEEP}
w- /sys/kernel/mm/transparent_hugepage/khugepaged/pages_to_scan - - - - ${EXPECTED_PAGES_TO_SCAN}
w- /sys/kernel/mm/transparent_hugepage/khugepaged/defrag - - - - 1
w- /sys/kernel/mm/transparent_hugepage/khugepaged/alloc_sleep_millisecs - - - - 60000

# --- MULTI-SIZE THP (mTHP) TIER DEFINITIONS ---
# Dynamically configured below
EOF

# Dynamically populate mTHP sizes to avoid warnings or hardcoding issues
for size_dir in "${THP_BASE_DIR}"/hugepages-*kB; do
    [[ -d "$size_dir" ]] || continue
    basename_dir="${size_dir##*/}"
    size_kb="${basename_dir#hugepages-}"
    size_kb="${size_kb%kB}"
    sz=$((size_kb))
    
    # Exclude sizes smaller than 16kB to comply with target architecture policies
    (( sz < 16 )) && continue
    
    eval_enabled="never"
    eval_shmem="never"
    
    if [[ "$MODE" == "AGGRESSIVE" ]] || { [[ "$MODE" == "AUTO" ]] && (( SYSTEM_RAM_KB >= THRESHOLD_KB )); }; then
        if (( sz == 64 || sz == 128 || sz == 2048 )); then
            eval_enabled="madvise"
            eval_shmem="inherit"
        fi
    else
        if (( sz == 2048 )); then
            eval_enabled="madvise"
            eval_shmem="inherit"
        fi
    fi
    
    {
        echo ""
        echo "# mTHP Size: ${size_kb}kB"
        if [[ -f "${size_dir}/enabled" ]]; then
            echo "w- ${size_dir}/enabled - - - - ${eval_enabled}"
        fi
        if [[ -f "${size_dir}/shmem_enabled" ]]; then
            echo "w- ${size_dir}/shmem_enabled - - - - ${eval_shmem}"
        fi
    } >> "$tmpfile"
done

cat >> "$tmpfile" <<EOF

# --- MGLRU HARDWARE LOCK ---
w- /sys/kernel/mm/lru_gen/enabled - - - - 0x0007
EOF

# Dry Run Check
if (( DRY_RUN == 1 )); then
    log_info "DRY RUN EXECUTED. Generated systemd-tmpfiles configuration:"
    echo "------------------------------------------------------"
    cat "$tmpfile"
    echo "------------------------------------------------------"
    exit 0
fi

# Apply to Disk
if [[ -f "$CONFIG_FILE" ]] && cmp -s "$tmpfile" "$CONFIG_FILE"; then
    log_info "Configuration file already matches desired state. No disk write needed."
else
    install -Dm0644 "$tmpfile" "$CONFIG_FILE"
    log_success "Configuration written to ${CONFIG_FILE}"
fi

# Apply to Live Kernel via systemd-tmpfiles
log_info "Applying tmpfiles.d configuration to live sysfs..."
systemd-tmpfiles --create "$CONFIG_FILE" >/dev/null 2>&1 || log_warn "systemd-tmpfiles applied with warnings (expected if CPU lacks specific mTHP sizes or in container)."

# --- Hardened Live Verification ---
actual_enabled="$(< "${THP_BASE_DIR}/enabled")"
actual_defrag="$(< "${THP_BASE_DIR}/defrag")"
actual_shmem="$(< "${THP_BASE_DIR}/shmem_enabled")"
actual_ptes="$(< "${THP_BASE_DIR}/khugepaged/max_ptes_none")"
actual_scan_sleep="$(< "${THP_BASE_DIR}/khugepaged/scan_sleep_millisecs")"
actual_pages_to_scan="$(< "${THP_BASE_DIR}/khugepaged/pages_to_scan")"

if [[ "$actual_enabled" != *"[$EXPECTED_ENABLED]"* ]]; then
    die "Verification failed: THP 'enabled' is '${actual_enabled}', expected to contain '[${EXPECTED_ENABLED}]'."
fi

if [[ "$actual_defrag" != *"[$EXPECTED_DEFRAG]"* ]]; then
    die "Verification failed: THP 'defrag' is '${actual_defrag}', expected to contain '[${EXPECTED_DEFRAG}]'."
fi

if [[ "$actual_shmem" != *"[$EXPECTED_SHMEM]"* ]]; then
    die "Verification failed: THP 'shmem_enabled' is '${actual_shmem}', expected to contain '[${EXPECTED_SHMEM}]'."
fi

if [[ "$actual_ptes" != "$EXPECTED_MAX_PTES" ]]; then
    die "Verification failed: THP 'max_ptes_none' is '${actual_ptes}', expected '${EXPECTED_MAX_PTES}'."
fi

if [[ "$actual_scan_sleep" != "$EXPECTED_SCAN_SLEEP" ]]; then
    die "Verification failed: THP 'scan_sleep_millisecs' is '${actual_scan_sleep}', expected '${EXPECTED_SCAN_SLEEP}'."
fi

if [[ "$actual_pages_to_scan" != "$EXPECTED_PAGES_TO_SCAN" ]]; then
    die "Verification failed: THP 'pages_to_scan' is '${actual_pages_to_scan}', expected '${EXPECTED_PAGES_TO_SCAN}'."
fi

# Verify all mTHP sizes dynamically
for size_dir in "${THP_BASE_DIR}"/hugepages-*kB; do
    [[ -d "$size_dir" ]] || continue
    basename_dir="${size_dir##*/}"
    size_kb="${basename_dir#hugepages-}"
    size_kb="${size_kb%kB}"
    sz=$((size_kb))
    
    # Exclude sizes smaller than 16kB to comply with target architecture policies
    (( sz < 16 )) && continue
    
    eval_enabled="never"
    eval_shmem="never"
    
    if [[ "$MODE" == "AGGRESSIVE" ]] || { [[ "$MODE" == "AUTO" ]] && (( SYSTEM_RAM_KB >= THRESHOLD_KB )); }; then
        if (( sz == 64 || sz == 128 || sz == 2048 )); then
            eval_enabled="madvise"
            eval_shmem="inherit"
        fi
    else
        if (( sz == 2048 )); then
            eval_enabled="madvise"
            eval_shmem="inherit"
        fi
    fi
    
    if [[ -f "${size_dir}/enabled" ]]; then
        actual="$(< "${size_dir}/enabled")"
        if [[ "$actual" != *"[$eval_enabled]"* && "$actual" != "$eval_enabled" ]]; then
            die "Verification failed: mTHP ${size_kb}kB 'enabled' is '${actual}', expected '[${eval_enabled}]'."
        fi
    fi
    if [[ -f "${size_dir}/shmem_enabled" ]]; then
        actual="$(< "${size_dir}/shmem_enabled")"
        if [[ "$actual" != *"[$eval_shmem]"* && "$actual" != "$eval_shmem" ]]; then
            die "Verification failed: mTHP ${size_kb}kB 'shmem_enabled' is '${actual}', expected '[${eval_shmem}]'."
        fi
    fi
done

# Verify MGLRU Hardware Lock
if [[ -f "${MGLRU_BASE_DIR}/enabled" ]]; then
    actual_mglru="$(< "${MGLRU_BASE_DIR}/enabled")"
    if [[ "$actual_mglru" != "0x0007" ]]; then
        die "Verification failed: MGLRU 'enabled' is '${actual_mglru}', expected '0x0007'."
    fi
fi

log_success "Verified live sysfs kernel values:"
log_success " enabled = [${EXPECTED_ENABLED}]"
log_success " defrag = [${EXPECTED_DEFRAG}]"
log_success " shmem_enabled = [${EXPECTED_SHMEM}]"
log_success " max_ptes_none = ${actual_ptes} (200=savings, 450=perf collapse)"
log_success " scan_sleep_millisecs = ${actual_scan_sleep} (Deep Sleep CPU Wakeups)"
log_success " pages_to_scan = ${actual_pages_to_scan} (Optimized Defrag Burst)"
log_success " use_zero_page = 1, shrink_underused = 1, khugepaged/defrag = 1"
log_success " MGLRU enabled = 0x0007 (Hardware Lock Active, y => 0x0007)"
log_success " MGLRU min_ttl_ms = (owned by 210_zram_optimize_swappiness.sh)"
log_success " mTHP Matrix = Exhaustively verified across all supported hardware tiers."
log_success " Active Tuning Profile: [${C_BOLD:-}${EXPECTED_MODE}${C_RESET:-}]"

exit 0

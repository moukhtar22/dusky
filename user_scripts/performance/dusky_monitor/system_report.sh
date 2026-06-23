#!/usr/bin/env bash

# ==============================================================================
# Advanced System & THP Tuning Report Script
# EXCLUSIVELY for modern Linux environments (Kernel 7.0+, Bash 5.3+)
# Utilizes pure bash built-ins (globstar, regex, redirects) for zero-fork performance.
# ==============================================================================

# Ensure script is run with root privileges to read all sysctl values
if (( EUID != 0 )); then
    echo -e "\e[1;34m[*] Elevating privileges via sudo...\e[0m"
    SCRIPT_PATH=$(realpath "$0")
    exec sudo bash "$SCRIPT_PATH" "$@"
    exit 1
fi

# Enable modern Bash features
# globstar: Recursive '**' matching without invoking 'find'
# nullglob: Avoid literal strings if no files match
shopt -s globstar nullglob

# Resolve real user and target output directory (bypassing sudo environment masking)
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo "$USER")}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
OUT_DIR="$REAL_HOME/Documents/logs/thp_system_settings"

# Ensure output directory exists and belongs to the real user
mkdir -p "$OUT_DIR"
chown "$REAL_USER":"$REAL_USER" "$OUT_DIR" 2>/dev/null || true

# Read-only Constants
readonly THP_ROOT="/sys/kernel/mm/transparent_hugepage"
readonly MGLRU_ROOT="/sys/kernel/mm/lru_gen"
readonly DATE_STR=$(date +'%Y-%m-%d %H:%M:%S %Z')
readonly REPORT_FILE="$OUT_DIR/report_$(date +%Y%m%d_%H%M%S).md"
readonly TERM_WIDTH=$(tput cols 2>/dev/null || echo 120)

# Generate a separator strictly using bash string manipulation (no 'tr' subprocess)
printf -v SEPARATOR '%*s' "$TERM_WIDTH" ''
SEPARATOR=${SEPARATOR// /─}

# Categorical Arrays for Modern Kernel Tuning Parameters
declare -a SYSCTL_VM=(
    "vm.swappiness"
    "vm.watermark_boost_factor"
    "vm.watermark_scale_factor"
    "vm.compaction_proactiveness"
    "vm.extfrag_threshold"
    "vm.vfs_cache_pressure"
    "vm.dirty_ratio"
    "vm.dirty_background_ratio"
    "vm.dirty_bytes"
    "vm.dirty_background_bytes"
    "vm.min_free_kbytes"
    "vm.max_map_count"
    "vm.mmap_rnd_bits"
    "vm.overcommit_memory"
    "vm.overcommit_ratio"
    "vm.page-cluster"
    "vm.stat_interval"
)

declare -a SYSCTL_FS=(
    "fs.file-max"
    "fs.nr_open"
    "fs.aio-max-nr"
    "fs.inotify.max_user_watches"
    "fs.inotify.max_user_instances"
)

declare -a SYSCTL_KERNEL=(
    "kernel.io_uring_disabled"
    "kernel.unprivileged_bpf_disabled"
    "kernel.split_lock_mitigate"
    "kernel.sched_autogroup_enabled"
    "kernel.perf_event_paranoid"
    "kernel.kptr_restrict"
    "kernel.nmi_watchdog"
    "kernel.numa_balancing"
    "kernel.pid_max"
    "kernel.sysrq"
    "kernel.panic"
)

declare -a SYSCTL_NET=(
    "net.core.default_qdisc"
    "net.core.bpf_jit_enable"
    "net.core.bpf_jit_harden"
    "net.ipv4.tcp_congestion_control"
    "net.ipv4.tcp_fastopen"
    "net.ipv4.tcp_syncookies"
    "net.ipv4.tcp_tw_reuse"
    "net.ipv4.tcp_mtu_probing"
    "net.ipv4.tcp_rmem"
    "net.ipv4.tcp_wmem"
    "net.mptcp.enabled"
    "net.core.rmem_max"
    "net.core.wmem_max"
    "net.core.somaxconn"
    "net.core.netdev_max_backlog"
)

# ------------------------------------------------------------------------------
# Functions
# ------------------------------------------------------------------------------

print_header() {
    echo "$1"
    echo "$SEPARATOR"
}

get_sysctl_val() {
    local key="$1"
    local val
    val=$(sysctl -n "$key" 2>/dev/null)
    if [[ -n "$val" ]]; then
        printf "  %-35s = %s\n" "$key" "$val"
    else
        printf "  %-35s = [Not Available]\n" "$key"
    fi
}

parse_sysfs_file() {
    local file="$1"
    # Clean up path prefixes for prettier nested output
    local display_name="${file#$THP_ROOT/}"
    display_name="${display_name#*/sys/kernel/mm/}"
    local val

    # Safely extract avoiding subprocess crash logic via pure cat
    [[ -r "$file" ]] && val=$(cat "$file" 2>/dev/null) || val="[Unreadable]"

    echo "  ▸ $display_name"

    # Fast native Bash Regex to extract active [value] avoiding 'grep'
    if [[ "$val" =~ \[([^]]+)\] ]]; then
        echo "    $val"
        echo "    active: ${BASH_REMATCH[1]}"
    else
        echo "    $val"
    fi
    echo ""
}

generate_report() {
    print_header "System Identity & State"
    echo "Host:     $(hostname)"
    echo "Kernel:   $(uname -r)"
    echo "Bash:     ${BASH_VERSION}"
    echo "Time:     $DATE_STR"
    echo "User:     ${SUDO_USER:-$USER}"
    echo ""

    # Modern Linux Memory Subsystems
    print_header "Multi-Gen LRU (MGLRU) Status"
    if [[ -d "$MGLRU_ROOT" ]]; then
        for file in "$MGLRU_ROOT"/*; do
            [[ -f "$file" ]] && parse_sysfs_file "$file"
        done
    else
        echo "  [Not Available / Disabled in Kernel]"
        echo ""
    fi

    if [[ -d "$THP_ROOT" ]]; then
        # Count files using bash arrays to avoid 'wc -l' pipe
        local thp_files=("$THP_ROOT"/**)
        
        print_header "Transparent Huge Pages (THP)"
        echo "Root:  $THP_ROOT"
        echo "Files: ${#thp_files[@]}"
        echo ""
        
        # Parse root files first
        echo "root"
        echo "${SEPARATOR//─/-}"
        for file in "$THP_ROOT"/*; do
            [[ -f "$file" ]] && parse_sysfs_file "$file"
        done

        # Parse directories recursively using globstar
        for dir in "$THP_ROOT"/**/; do
            local dir_name="${dir#$THP_ROOT/}"
            dir_name="${dir_name%/}" # Strip trailing slash
            
            # Avoid re-printing root
            [[ -z "$dir_name" ]] && continue
            
            echo "$dir_name"
            echo "${SEPARATOR//─/-}"
            for file in "$dir"*; do
                [[ -f "$file" ]] && parse_sysfs_file "$file"
            done
        done
    fi

    print_header "Virtual Memory (VM) & ZRAM Tuning"
    for key in "${SYSCTL_VM[@]}"; do get_sysctl_val "$key"; done
    echo ""

    print_header "File System (FS) & Async IO Limits"
    for key in "${SYSCTL_FS[@]}"; do get_sysctl_val "$key"; done
    echo ""

    print_header "Kernel Core & eBPF Parameters"
    for key in "${SYSCTL_KERNEL[@]}"; do get_sysctl_val "$key"; done
    echo ""

    print_header "Modern Network Stack (BBR/MPTCP/Qdisc)"
    for key in "${SYSCTL_NET[@]}"; do get_sysctl_val "$key"; done
    echo ""

    print_header "systemd Cgroup & OOMD Policies"
    for svc in systemd-oomd.service systemd-journald.service; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            echo "  ▸ $svc"
            echo "    MemoryHigh = $(systemctl show "$svc" -p MemoryHigh --value 2>/dev/null)"
            echo "    MemoryMax  = $(systemctl show "$svc" -p MemoryMax --value 2>/dev/null)"
            echo "    OOMPolicy  = $(systemctl show "$svc" -p OOMPolicy --value 2>/dev/null)"
            echo ""
        fi
    done

    print_header "ZRAM Block Devices & Writeback Status"
    if command -v zramctl &>/dev/null; then
        zramctl --output-all 2>/dev/null | sed 's/^/  /' || echo "  [No ZRAM devices configured]"
        echo ""
        for zram_dev in /sys/block/zram*; do
            [[ -d "$zram_dev" ]] || continue
            local dev_name="${zram_dev##*/}"
            local backing_dev
            backing_dev=$(cat "$zram_dev/backing_dev" 2>/dev/null || echo "none")
            if [[ "$backing_dev" != "none" && -n "$backing_dev" ]]; then
                echo "  ▸ $dev_name Direct Writeback (Kernel 7.0+)"
                echo "    backing_dev            = $backing_dev"
                echo "    writeback_limit_enable = $(cat "$zram_dev/writeback_limit_enable" 2>/dev/null || echo 'N/A')"
                echo "    writeback_limit        = $(cat "$zram_dev/writeback_limit" 2>/dev/null || echo 'N/A')"
                echo "    bd_stat                = $(cat "$zram_dev/bd_stat" 2>/dev/null || echo 'N/A')"
            fi
        done
    else
        echo "  [zramctl command not found]"
    fi
    echo ""

    print_header "Quick Focus / Triage"
    [[ -r "$THP_ROOT/enabled" ]] && echo "THP enabled             = $(cat "$THP_ROOT/enabled" 2>/dev/null)"
    [[ -r "$THP_ROOT/defrag" ]]  && echo "THP defrag              = $(cat "$THP_ROOT/defrag" 2>/dev/null)"
    [[ -r "$MGLRU_ROOT/enabled" ]] && echo "MGLRU enabled           = $(cat "$MGLRU_ROOT/enabled" 2>/dev/null)"
    echo "---"
    get_sysctl_val "vm.swappiness"
    get_sysctl_val "vm.compaction_proactiveness"
    get_sysctl_val "net.ipv4.tcp_congestion_control"
    get_sysctl_val "net.core.default_qdisc"
    get_sysctl_val "kernel.io_uring_disabled"
    echo ""

    print_header "Saved report"
    echo "$REPORT_FILE"
}

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------

# Execute generator and tee to report file
generate_report | tee "$REPORT_FILE"

# Gracefully restore file permissions to the triggering user, leaving root scope
chown "$REAL_USER":"$REAL_USER" "$REPORT_FILE"

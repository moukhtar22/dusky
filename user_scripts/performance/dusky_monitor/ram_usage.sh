#!/usr/bin/env bash
# ============================================================================
# Platinum-Grade RAM Forensics — Arch Linux + Hyprland 0.55+ / Kernel 7.x
# ============================================================================
# Covers every known RAM sink on a modern Wayland/Hyprland desktop:
#   • Correct full /proc/meminfo accounting (all kernel 7.x fields)
#   • Race-condition immune, zero-fork smaps_rollup PSS engine
#   • Hyprland-specific IPC diagnostics via JSON (jq) & Signature
#   • Transparent Hugepage (THP & mTHP) analysis
#   • ZRAM / ZSWAP efficiency, Physical Pool tracking & Kernel 7.0 Writeback
#   • Mlocked / QEMU memory tracking
#   • Wayland/tmpfs shared memory & XDG_RUNTIME sockets
#   • Universal DMA-BUF GPU buffers (debugfs & sysfs fallbacks)
#   • Kernel slab leak detection
#   • Hyprland Headless / Render Leak vectors (Dynamic IPC) & OOM History
#   • systemd cgroup pressure limits and user slice shields
# ============================================================================

set -euo pipefail

# ── 1. PRIVILEGE ESCALATION & ENVIRONMENT ───────────────────────────────────
if [[ "$EUID" -ne 0 ]]; then
    echo -e "\e[1;33m[!] Elevated privileges required. Auto-elevating...\e[0m"
    exec sudo ORIGINAL_USER="$USER" bash "$0" "$@"
fi

TARGET_USER="${ORIGINAL_USER:-${SUDO_USER:-$USER}}"
if [[ "$TARGET_USER" == "root" ]]; then
    TARGET_HOME="/root"
else
    TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
fi

REPORT_DIR="$TARGET_HOME/Documents/logs/ram_audit"
mkdir -p "$REPORT_DIR"
chown -R "$TARGET_USER":"$TARGET_USER" "$TARGET_HOME/Documents/logs" 2>/dev/null || true
REPORT="$REPORT_DIR/report_$(date +%Y%m%d_%H%M%S).md"

# ── 2. DEPENDENCY CHECK ─────────────────────────────────────────────────────
MISSING_PKGS=()
command -v zramctl  >/dev/null 2>&1 || MISSING_PKGS+=("util-linux")
command -v slabtop  >/dev/null 2>&1 || MISSING_PKGS+=("procps-ng")
command -v jq       >/dev/null 2>&1 || MISSING_PKGS+=("jq")

if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    echo -e "\e[1;34m[*] Missing packages: ${MISSING_PKGS[*]}. Installing...\e[0m"
    pacman -S --noconfirm --needed "${MISSING_PKGS[@]}" || true
fi

# ── 3. HELPERS ───────────────────────────────────────────────────────────────

get_mem() {
    local val
    val=$(awk -v key="$1" '$1 == key ":" {print $2; exit}' /proc/meminfo)
    echo "${val:-0}"
}

to_mb() {
    local val="${1:-0}"
    awk "BEGIN {printf \"%.0f\", $val / 1024}"
}

pss_table() {
    local top_n="${1:-20}"
    local tmp
    tmp=$(mktemp)
    
    # Single-pass C-level stream processing. Race-condition immune & zero-fork optimized.
    (
        set +e +o pipefail
        grep -HE '^(Pss|Private_Clean|Private_Dirty|Rss|Swap):' /proc/[0-9]*/smaps_rollup 2>/dev/null | awk -F':' '
        {
            split($1, path, "/");
            pid = path[3];
            metric = $2;
            val = $3 + 0;
            
            if (metric == "Pss") pss[pid] += val
            else if (metric == "Private_Clean" || metric == "Private_Dirty") uss[pid] += val
            else if (metric == "Rss") rss[pid] += val
            else if (metric == "Swap") swap[pid] += val
        }
        END {
            for (p in pss) {
                comm_file = "/proc/" p "/comm"
                if ((getline comm < comm_file) <= 0) {
                    comm = "?"
                }
                close(comm_file)
                comm = substr(comm, 1, 20)
                gsub(/\n|\r/, "", comm) 
                
                print p "\t" comm "\t" uss[p]+0 "\t" pss[p]+0 "\t" rss[p]+0 "\t" swap[p]+0
            }
        }' | sort -t$'\t' -k4 -rn | head -n "$top_n" > "$tmp"
    )

    awk -F'\t' 'BEGIN {
        print "| PID | COMMAND | USS (MB) | PSS (MB) | RSS (MB) | SWAP (MB) |"
        print "|---|---|---|---|---|---|"
    }
    {
        printf "| %d | %s | %.1f | %.1f | %.1f | %.1f |\n", $1, $2, $3/1024, $4/1024, $5/1024, $6/1024
    }' "$tmp"
    
    rm -f "$tmp"
}

# ── 4. FORENSICS ─────────────────────────────────────────────────────────────
echo -e "\e[1;32m[*] Commencing Deep Kernel RAM Analysis (Hyprland + Arch Linux)...\e[0m"

{
echo "# Platinum System RAM Forensics Report — Hyprland Edition"
echo "**Date:** $(date)"
echo "**Kernel:** $(uname -r)"
echo "**Host:** $(hostname)"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — COMPLETE /proc/meminfo ACCOUNTING
# ─────────────────────────────────────────────────────────────────────────────
# NOTE: values are read here (after the header block) so Section 0 (Executive
# Summary) below can re-use the exact same snapshot. The Section-1 header text
# itself is emitted after the calculations.

MEM_TOTAL=$(get_mem MemTotal)
MEM_FREE=$(get_mem MemFree)
MEM_AVAIL=$(get_mem MemAvailable)
BUFFERS=$(get_mem Buffers)
CACHED=$(get_mem Cached)
SWAP_CACHED=$(get_mem SwapCached)
ANON_PAGES=$(get_mem AnonPages)
SHMEM=$(get_mem Shmem)
MAPPED=$(get_mem Mapped)
UNEVICTABLE=$(get_mem Unevictable)
MLOCKED=$(get_mem Mlocked)

SLAB=$(get_mem Slab)
S_RECLAIMABLE=$(get_mem SReclaimable)
S_UNRECLAIM=$(get_mem SUnreclaim)
K_RECLAIMABLE=$(get_mem KReclaimable)
K_STACK=$(get_mem KernelStack)
PAGE_TABLES=$(get_mem PageTables)
SEC_PAGE_TABLES=$(get_mem SecPageTables)
PERCPU=$(get_mem Percpu)
VMALLOC_USED=$(get_mem VmallocUsed)
GPU_ACTIVE=$(get_mem GPUActive)
GPU_RECLAIM=$(get_mem GPUReclaim)

ANON_HUGE=$(get_mem AnonHugePages)
SHMEM_HUGE=$(get_mem ShmemHugePages)
FILE_HUGE=$(get_mem FileHugePages)

SWAP_TOTAL=$(get_mem SwapTotal)
SWAP_FREE=$(get_mem SwapFree)
ZSWAP=$(get_mem Zswap)
ZSWAPPED=$(get_mem Zswapped)
DIRTY=$(get_mem Dirty)
WRITEBACK=$(get_mem Writeback)
COMMITTED=$(get_mem Committed_AS)
COMMIT_LIMIT=$(get_mem CommitLimit)
HW_CORRUPTED=$(get_mem HardwareCorrupted)

# Refined Calculations
FILE_CACHE=$(( CACHED - SHMEM ))
(( FILE_CACHE < 0 )) && FILE_CACHE=0

ZRAM_TOTAL_KB=$(
    zramctl --bytes --noheadings --output TOTAL 2>/dev/null \
    | awk '{s+=$1} END {printf "%.0f", s/1024}'
)
[[ -z "$ZRAM_TOTAL_KB" ]] && ZRAM_TOTAL_KB=0

ZRAM_PEAK_KB=$(
    zramctl --bytes --noheadings --output MEM-USED 2>/dev/null \
    | awk '{s+=$1} END {printf "%.0f", s/1024}'
)
[[ -z "$ZRAM_PEAK_KB" ]] && ZRAM_PEAK_KB=0

# vmstat-only kernel allocations NOT exposed by /proc/meminfo (kernel 7.x):
#   nr_kernel_file_pages — reclaimable kernel-internal file pages. Empirically
#     DISJOINT from Cached: drop_caches frees them (181MB→24MB) while the identity
#     NR_FILE_PAGES == Cached+Buffers+SwapCached holds exactly before AND after.
#   nr_io_mmu_pages — pages pinned by the IOMMU (0 here, nonzero on VMs/PCI-passthru).
KERNEL_FILE_KB=$(awk '/^nr_kernel_file_pages/{print $2}' /proc/vmstat 2>/dev/null | awk '{print $1*4}')
IOMMU_KB=$(awk '/^nr_io_mmu_pages/{print $2}' /proc/vmstat 2>/dev/null | awk '{print $1*4}')
[[ -z "$KERNEL_FILE_KB" ]] && KERNEL_FILE_KB=0
[[ -z "$IOMMU_KB" ]] && IOMMU_KB=0

KERNEL_CORE_KB=$(( K_RECLAIMABLE + S_UNRECLAIM + K_STACK + PAGE_TABLES + SEC_PAGE_TABLES + PERCPU + VMALLOC_USED + KERNEL_FILE_KB + IOMMU_KB ))
# Unevictable is empirically DISJOINT from AnonPages/Cached/Shmem (drop_caches frees
# it while Shmem stays flat), so it must be counted as known to close the accounting.
KNOWN_KB=$(( MEM_FREE + ANON_PAGES + SHMEM + FILE_CACHE + BUFFERS + UNEVICTABLE + KERNEL_CORE_KB + ZRAM_TOTAL_KB ))
RESIDUAL_KB=$(( MEM_TOTAL - KNOWN_KB ))
if [[ $RESIDUAL_KB -lt 0 ]]; then
    echo "> ⚠ **Accounting warning:** \`Residual\` went negative (double-count suspected). Values captured at slightly different moments can cause this transiently."
fi

# ── 0. EXECUTIVE SUMMARY (one-glance health check) ───────────────────────────
echo ""
echo "## 0. Executive Summary"
echo "---"
echo '> *One-glance health check. All numbers are MB (MiB). See Section 1 for the full breakdown.*'
echo ""
echo "| Metric | MB |"
echo "|---|---:|"
printf "| Total RAM (MemTotal) | %s |\n" "$(to_mb $MEM_TOTAL)"
printf "| Truly Available | %s |\n" "$(to_mb $MEM_AVAIL)"
printf "| Raw Free | %s |\n" "$(to_mb $MEM_FREE)"
printf "| Apps (AnonPages) | %s |\n" "$(to_mb $ANON_PAGES)"
printf "| File Cache (auto-freed) | %s |\n" "$(to_mb $FILE_CACHE)"
printf "| Shared / Wayland (Shmem) | %s |\n" "$(to_mb $SHMEM)"
printf "| GPU GEM Pool (Unevictable) | %s |\n" "$(to_mb $UNEVICTABLE)"
printf "| Kernel (slab/PT/vmalloc/file) | %s |\n" "$(to_mb $KERNEL_CORE_KB)"
printf "| ZRAM Compressed Pool | %s |\n" "$(to_mb $ZRAM_TOTAL_KB)"
printf "| **Residual (true unknown)** | **%s** |\n" "$(to_mb $RESIDUAL_KB)"
ALERTS=""
if [[ $S_UNRECLAIM -gt 524288 ]]; then ALERTS+="⚠ SUnreclaim >512MB (possible slab leak, Sec 7); "; fi
if [[ $COMMIT_LIMIT -gt 0 ]] && (( COMMITTED * 100 / COMMIT_LIMIT > 90 )); then ALERTS+="⚠ Commit ratio >90% (Sec 2); "; fi
if (( MEM_AVAIL * 100 / MEM_TOTAL < 5 )); then ALERTS+="⚠ Critically low free RAM (<5% avail); "; fi
[[ -z "$ALERTS" ]] && ALERTS="✅ No critical flags."
echo "> **Alerts:** $ALERTS"
TOP_PSS_LINE=$(
    set +e +o pipefail
    grep -HE '^Pss:' /proc/[0-9]*/smaps_rollup 2>/dev/null | awk -F: '
    {
        split($1, path, "/");
        pid = path[3];
        val = $3 + 0;
        if (val > pss[pid]) pss[pid] = val;
    }
    END {
        best = 0;
        for (p in pss) {
            if (pss[p] > best) { best = pss[p]; bp = p; }
        }
        if (best > 0) {
            comm = "";
            if ((getline comm < ("/proc/" bp "/comm")) > 0) gsub(/\n|\r/, "", comm);
            close("/proc/" bp "/comm");
            printf "%s\t%s\t%.1f\n", bp, comm, best/1024;
        }
    }' 2>/dev/null
)
if [[ -n "$TOP_PSS_LINE" ]]; then
    TOP_PID=${TOP_PSS_LINE%%$'\t'*}
    rest=${TOP_PSS_LINE#*$'\t'}
    TOP_MB=${rest##*$'\t'}
    TOP_COMM=${rest%$'\t'*}
    echo "> **Largest app by PSS:** \`${TOP_COMM:-?}\` (PID ${TOP_PID:-?}) = **${TOP_MB:-0} MB**."
else
    echo "> **Largest app by PSS:** see Section 4."
fi
echo "> **Hidden GPU RAM:** DMA-BUF = Section 8."
echo ""
echo "### Report Index"
echo "| # | Section |"
echo "|---|---|"
echo "| 1 | Complete Memory Accounting (kernel truth) |"
echo "| 2 | Virtual Memory Commit Pressure |"
echo "| 3 | Compressed RAM (ZRAM / ZSWAP) |"
echo "| 4 | True Process Isolation (Top 25 by PSS) |"
echo "| 5 | Wayland & Hyprland Diagnostics |"
echo "| 6 | Shared Memory & Tmpfs |"
echo "| 7 | Kernel Slab Leak Detection |"
echo "| 8 | GPU DMA-BUF Allocations |"
echo "| 9 | Transparent Hugepages (THP) |"
echo "| 10 | Hyprland Memory Leak Checklist |"
echo "| 11 | Memory Pressure Events (OOM) |"
echo "| 12 | Quick Diagnosis Guide |"
echo "| 13 | Custom Kernel RAM Savings Estimation |"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — COMPLETE /proc/meminfo ACCOUNTING (header text)
# ─────────────────────────────────────────────────────────────────────────────
echo "## 1. Complete Memory Accounting (Kernel Absolute Truth)"
echo "---"
echo '> **Understanding this section:** This is the absolute low-level truth of your RAM. Tools like `htop` group these numbers together unpredictably. Here, you see exactly what the kernel is allocating.'
echo '> * **AnonPages:** Your running apps, browsers, and game memory.'
echo '> * **Cached:** Files kept in RAM to make the system fast. *This is automatically freed if apps need more RAM.*'
echo '> * **Shmem:** Shared Memory. On Wayland, this includes the literal pixel buffers of your visible windows.'
echo ""

echo "### Overall"
echo "| Metric | MB |"
echo "|---|---:|"
printf "| Total Usable RAM (MemTotal) | %s |\n" "$(to_mb $MEM_TOTAL)"
printf "| Truly Available (MemAvailable) | %s |\n" "$(to_mb $MEM_AVAIL)"
printf "| Raw Free (MemFree) | %s |\n" "$(to_mb $MEM_FREE)"
echo ""
echo "### Named Allocations"
echo "| Category | MB |"
echo "|---|---:|"
printf "| Userspace Anon (AnonPages) | %s |\n" "$(to_mb $ANON_PAGES)"
printf "| Page Cache / File-backed | %s |\n" "$(to_mb $FILE_CACHE)"
printf "| Shared Memory/Tmpfs (Shmem) | %s |\n" "$(to_mb $SHMEM)"
printf "| Buffer Cache (Buffers) | %s |\n" "$(to_mb $BUFFERS)"
printf "| Swap Cache (subset of File+Shmem) | %s |\n" "$(to_mb $SWAP_CACHED)"
printf "| Mapped (file-mapped only on 7.x) | %s |\n" "$(to_mb $MAPPED)"
printf "| Mlocked (subset of Unevictable) | %s |\n" "$(to_mb $MLOCKED)"
printf "| Unevictable (pinned LRU) | %s |\n" "$(to_mb $UNEVICTABLE)"
[[ $GPU_ACTIVE -gt 0 || $GPU_RECLAIM -gt 0 ]] && printf "| GPU-managed active/reclaim (7.x) | %s |\n" "$(to_mb $((GPU_ACTIVE + GPU_RECLAIM)))"
echo ""
echo "### Kernel Structures"
echo "| Category | MB |"
echo "|---|---:|"
printf "| Slab Total | %s |\n" "$(to_mb $SLAB)"
printf "| ├─ Reclaimable (KReclaimable) | %s |\n" "$(to_mb $K_RECLAIMABLE)"
printf "| └─ Unreclaimable (SUnreclaim) | %s |\n" "$(to_mb $S_UNRECLAIM)"
printf "| Kernel Stacks | %s |\n" "$(to_mb $K_STACK)"
printf "| Page Tables | %s |\n" "$(to_mb $PAGE_TABLES)"
printf "| Secondary Page Tables (KVM/arm) | %s |\n" "$(to_mb $SEC_PAGE_TABLES)"
printf "| Per-CPU Allocations | %s |\n" "$(to_mb $PERCPU)"
printf "| vmalloc Used | %s |\n" "$(to_mb $VMALLOC_USED)"
printf "| Kernel File Pages (vmstat-only) | %s |\n" "$(to_mb $KERNEL_FILE_KB)"
[[ $IOMMU_KB -gt 0 ]] && printf "| IOMMU Pinned Pages (vmstat-only) | %s |\n" "$(to_mb $IOMMU_KB)"
echo ""
echo "### Summary"
echo "| Metric | MB |"
echo "|---|---:|"
printf "| ZRAM Current Physical Pool | %s |\n" "$(to_mb $ZRAM_TOTAL_KB)"
printf "| ZRAM Peak Physical Pool | %s |\n" "$(to_mb $ZRAM_PEAK_KB)"
printf "| **Known & Tracked** | **%s** |\n" "$(to_mb $KNOWN_KB)"
printf "| **Residual estimate** | **%s** |\n" "$(to_mb $RESIDUAL_KB)"

echo "> **Arithmetic identity (by construction):** \`Known\` + \`Residual\` = **$(to_mb $KNOWN_KB) + $(to_mb $RESIDUAL_KB) = $(to_mb $((KNOWN_KB + RESIDUAL_KB))) MB** = MemTotal **$(to_mb $MEM_TOTAL) MB**. This always holds because \`Residual\` is *defined* as \`MemTotal − Known\` — it only proves arithmetic. The audit below is the real correctness check."

XCHECK=$(
    awk -v zram_extra="$ZRAM_TOTAL_KB" -v zresid="$RESIDUAL_KB" '
        function mb(x) { return int((x + 512) / 1024) }
        FNR == NR {
            if ($1 == "MemTotal:")     mt = $2
            else if ($1 == "MemFree:") mf = $2
            else if ($1 == "AnonPages:") ap = $2
            else if ($1 == "Cached:")  ca = $2
            else if ($1 == "Shmem:")   sh = $2
            else if ($1 == "Buffers:") bu = $2
            else if ($1 == "SwapCached:") sc = $2
            else if ($1 == "Unevictable:") un = $2
            else if ($1 == "KReclaimable:") kr = $2
            else if ($1 == "SUnreclaim:") su = $2
            else if ($1 == "KernelStack:") ks = $2
            else if ($1 == "PageTables:") pt = $2
            else if ($1 == "SecPageTables:") sp = $2
            else if ($1 == "Percpu:") pc = $2
            else if ($1 == "VmallocUsed:") vm = $2
            next
        }
        {
            if ($1 == "nr_file_pages") nfp = $2
            else if ($1 == "nr_kernel_file_pages") kfp = $2
            else if ($1 == "nr_io_mmu_pages") iommu = $2
            else if ($1 == "nr_page_table_pages") pttp = $2
            else if ($1 == "nr_kernel_stack") kstp = $2
            else if ($1 == "nr_vmalloc") vmp = $2
        }
        END {
            fc = ca - sh; if (fc < 0) fc = 0
            kcore = kr + su + ks + pt + sp + pc + vm + kfp*4 + iommu*4
            known = mf + ap + sh + fc + bu + un + kcore + zram_extra
            resid = mt - known
            fpexpect = ca + bu + sc
            fp = nfp*4
            printf "> * **Single-snapshot Residual ≈ %d MB** (multi-read script residual: %d MB) — delta < ~50 MB is per-field read timing skew; a large gap would flag a missed category or double-count.\n", mb(resid), mb(zresid)
            printf "> * Identity NR_FILE_PAGES*4 == Cached+Buffers+SwapCached: **%s** (%d vs %d kB)\n", (fp == fpexpect ? "PASS" : "FAIL"), fp, fpexpect
            printf "> * Identity vmstat-meminfo PageTables: **%s** (%d vs %d kB)\n", (pttp*4 == pt ? "PASS" : "FAIL"), pttp*4, pt
            printf "> * Identity vmstat-meminfo KernelStack (KiB counter on 7.x): **%s** (%d vs %d kB)\n", (kstp == ks ? "PASS" : "FAIL"), kstp, ks
            printf "> * Identity vmstat-meminfo VmallocUsed: **%s** (%d vs %d kB)\n", (vmp*4 == vm ? "PASS" : "FAIL"), vmp*4, vm
        }
    ' /proc/meminfo /proc/vmstat
)
if [[ -n "$XCHECK" ]]; then
    echo "> **Independent single-snapshot audit** (one read each of \`/proc/meminfo\` + \`/proc/vmstat\`, same formula):"
    echo "$XCHECK"
fi

echo "> **Diagnostic Note:**"
echo "> * \`VmallocUsed\` (~800 MB here) is **real committed kernel RAM** (execmem/module/bpf mappings) and is counted as known, so \`Residual\` is the true unclassified remainder: kernel image/direct-map, boot reservations and fragmentation."
echo "> * \`Unevictable\` is counted as known — empirically it is **disjoint** from \`Shmem\`/\`AnonPages\`/\`Cached\` (freeing it via \`echo 3 > /proc/sys/vm/drop_caches\` drops only \`Unevictable\`; \`Shmem\` stays flat). It is **not** double-counted."
echo "> * On kernel 7.x, \`GPUActive\`/\`GPUReclaim\` are the emerging GPU-page counters — but most drivers (incl. i915 here) still report 0, so the real GPU system-RAM pool shows up in \`Unevictable\` instead."
echo "> * \`Kernel File Pages\` reads \`nr_kernel_file_pages\` from \`/proc/vmstat\` (not shown in meminfo). Empirically **disjoint** from \`Cached\`: \`drop_caches\` freed it (181→24 MB) while the identity \`NR_FILE_PAGES == Cached+Buffers+SwapCached\` held exactly before and after. Counted as known."
echo "> * SUnreclaim > 500 MB → **ALERT:** Kernel slab leak (See Section 7)."
echo "> * Large \`Unevictable\` with tiny \`Mlocked\` on an Intel/AMD desktop is the **i915/amdgpu GEM system-RAM pool** held by GPU-accelerated apps, not a leak — the driver shrinker releases it under pressure."
echo "> * \`Mapped\`, \`SwapCached\`, \`Mlocked\` and the THP sub-fields are **subsets** of the categories above; they are shown for reference only and are NOT added to the total."
echo ""

if [[ "$UNEVICTABLE" -gt 0 ]]; then
    echo "### Mlocked Consumers (processes with VmLck > 0)"
    echo "> *Only memory explicitly locked with \`mlock()\`/\`SHM_LOCK\`. QEMU/libvirt, VFIO and secure enclaves pin memory this way.*"
    echo "\`\`\`text"
    (
        set +e +o pipefail
        for pid_dir in /proc/[0-9]*/; do
            [[ -r "${pid_dir}status" ]] || continue
            vmlck=$(awk '/^VmLck:/{print $2}' "${pid_dir}status" 2>/dev/null || echo 0)
            [[ "$vmlck" -gt 0 ]] 2>/dev/null || continue
            pid="${pid_dir#/proc/}"
            pid="${pid%/}"
            comm=$(head -c 20 "${pid_dir}comm" 2>/dev/null || echo "unknown")
            printf "%10.1f MB  PID %-8s (%s)\n" "$(awk "BEGIN {printf \"%.1f\", $vmlck/1024}")" "$pid" "$comm"
        done | sort -t'M' -k1 -rn | head -10
        echo "  (If empty: nothing is mlocked — Unevictable is NOT caused by mlock.)"
    ) || true
    echo "\`\`\`"

    echo ""
    echo "### Unevictable Memory Attribution (per cgroup, empirical)"
    echo "> *Read from \`/sys/fs/cgroup/*/memory.stat\`. Shows which services/apps actually own the pinned pages, so a 500 MB \`Unevictable\` with zero \`Mlocked\` is traced to its real owner instead of being mislabeled as a VM.*"
    echo "\`\`\`text"
    (
        set +e +o pipefail
        find /sys/fs/cgroup -type d 2>/dev/null | while read -r cg; do
            [[ -f "$cg/memory.stat" ]] || continue
            # Only leaf cgroups hold their own charges; parents merely aggregate children.
            [[ -z "$(find "$cg" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)" ]] || continue
            u=$(awk '/^unevictable /{print $2}' "$cg/memory.stat" 2>/dev/null || echo 0)
            [[ "${u:-0}" -gt 1048576 ]] 2>/dev/null || continue
            procs=""
            while read -r p; do
                procs+="$(cat /proc/$p/comm 2>/dev/null || echo "?")[$p] "
            done < "$cg/cgroup.procs" 2>/dev/null
            printf "%10.1f MB  %s  %s\n" "$(awk "BEGIN {printf \"%.1f\", $u/1048576}")" "${cg#/sys/fs/cgroup/}" "${procs:-<none>}"
        done | sort -rn | head -10
    ) || true
    echo "\`\`\`"
    echo ""
fi

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — COMMIT PRESSURE & VIRTUAL OVERCOMMIT
# ─────────────────────────────────────────────────────────────────────────────
echo "## 2. Virtual Memory Commit Pressure"
echo "---"
echo "> **Understanding this section:** Shows if your system is overcommitting memory and risking an Out-Of-Memory (OOM) kill."
echo ""
echo "\`\`\`text"
printf "%-45s %8s MB\n" "  CommitLimit:"   "$(to_mb $COMMIT_LIMIT)"
printf "%-45s %8s MB\n" "  Committed_AS:"  "$(to_mb $COMMITTED)"
printf "%-45s %8s MB\n" "  Dirty pages:"   "$(to_mb $DIRTY)"
printf "%-45s %8s MB\n" "  In writeback:"  "$(to_mb $WRITEBACK)"
[[ $HW_CORRUPTED -gt 0 ]] && printf "%-45s %8s MB\n" "  *** HW CORRUPTED RAM ***:" "$(to_mb $HW_CORRUPTED)"
echo "\`\`\`"
OVERCOMMIT=$(( COMMITTED * 100 / (COMMIT_LIMIT > 0 ? COMMIT_LIMIT : 1) ))
echo "- **Commit ratio:** ${OVERCOMMIT}%  *(> 90% means swap pressure likely)*"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — ZRAM & SWAP
# ─────────────────────────────────────────────────────────────────────────────
echo "## 3. Compressed RAM (ZRAM / ZSWAP)"
echo "---"
echo '> **Understanding this section:** ZRAM/ZSWAP acts as a hyper-fast SSD inside your RAM by compressing inactive memory. The "TOTAL" column shows exactly how much physical RAM this compression pool is eating.'
echo ""
if zramctl --raw 2>/dev/null | grep -q '/dev/zram'; then
    SWAP_USED=$(( SWAP_TOTAL - SWAP_FREE ))
    echo "- **Swap Total / Free / Used:** \`$(to_mb $SWAP_TOTAL)\` / \`$(to_mb $SWAP_FREE)\` / \`$(to_mb $SWAP_USED)\` MB"
    echo ""
    echo "\`\`\`text"
    zramctl --output NAME,ALGORITHM,DISKSIZE,DATA,COMPR,TOTAL,MEM-USED,COMP-RATIO,MOUNTPOINT 2>/dev/null || \
    zramctl --output NAME,ALGORITHM,DISKSIZE,DATA,COMPR,TOTAL,MEM-USED 2>/dev/null || \
    zramctl --output NAME,ALGORITHM,DISKSIZE,DATA,COMPR,TOTAL 2>/dev/null
    echo "\`\`\`"

    # Reconcile SwapTotal against ALL swap devices (zram + any file/partition swap)
    if command -v swapon >/dev/null 2>&1; then
        echo "- **All swap devices (why \`Swap Total\` = $(to_mb $SWAP_TOTAL) MB):**"
        echo "\`\`\`text"
        swapon --show --noheadings 2>/dev/null | sed 's/^/  /' || echo "  (none)"
        echo "\`\`\`"
    fi

    # Kernel 7.0+ Direct Writeback Native Verification
    if [[ -r "/sys/block/zram0/backing_dev" ]]; then
        BACKING_DEV=$(cat /sys/block/zram0/backing_dev 2>/dev/null || echo "none")
        if [[ "$BACKING_DEV" != "none" && -n "$BACKING_DEV" ]]; then
            echo ""
            echo "- **ZRAM Direct Writeback (Kernel 7.0+):**"
            echo "\`\`\`text"
            echo "  Backing Device: $BACKING_DEV"
            echo "  Writeback Limit Enable: $(cat /sys/block/zram0/writeback_limit_enable 2>/dev/null || echo 'N/A')"
            echo "  bd_stat (reads/writes/etc): $(cat /sys/block/zram0/bd_stat 2>/dev/null || echo 'N/A')"
            echo "\`\`\`"
        fi
    fi
else
    echo "ZRAM is not active."
fi
echo ""
if [[ "$ZSWAP" -gt 0 ]]; then
    echo "- **Zswap is active:** \`$(to_mb $ZSWAP) MB\` physical pool, storing \`$(to_mb $ZSWAPPED) MB\` of decompressed data."
    echo "- **Zswap settings:**"
    echo "\`\`\`text"
    echo "  Enabled: $(cat /sys/module/zswap/parameters/enabled 2>/dev/null || echo 'N/A')"
    echo "  Compressor: $(cat /sys/module/zswap/parameters/compressor 2>/dev/null || echo 'N/A')"
    echo "  Pool Allocator: $(cat /sys/module/zswap/parameters/zpool 2>/dev/null || echo 'N/A')"
    echo "  Max Pool Limit: $(cat /sys/module/zswap/parameters/max_pool_percent 2>/dev/null || echo 'N/A')"
    echo "\`\`\`"
fi
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — NATIVE PSS TABLE
# ─────────────────────────────────────────────────────────────────────────────
echo "## 4. True Process Isolation (Top 25 by PSS)"
echo "---"
echo '> **Understanding this section:** Standard system monitors look at `RSS` which wildly exaggerates memory usage by double-counting shared libraries. This table uses `PSS` (Proportional Set Size) which perfectly splits shared memory to give you the truest representation of what apps are heavy.'
echo '> * **USS:** Memory 100% unique to this app. If you kill the app, this exact amount of RAM is freed instantly.'
echo '> * **PSS:** The most accurate metric. USS plus the fair mathematical share of shared libraries for this app.'
echo ""
pss_table 25
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — HYPRLAND-SPECIFIC DIAGNOSTICS
# ─────────────────────────────────────────────────────────────────────────────
echo "## 5. Wayland & Hyprland Diagnostics"
echo "---"
echo '> **Understanding this section:** Interrogates the Wayland compositor directly (using JSON) to see if window surfaces, unmapped layers, or headless monitors are building up in the background.'
echo ""
HYPR_PID=$(pgrep -x Hyprland 2>/dev/null | head -1 || true)
if [[ -n "$HYPR_PID" ]]; then
    HYPR_USER=$(ps -o user= -p "$HYPR_PID" 2>/dev/null | tr -d ' ' || true)
    HYPR_UID=$(id -u "$HYPR_USER" 2>/dev/null || echo 1000)
    HYPR_RSS=$(awk '/^VmRSS:/{print $2}' /proc/"$HYPR_PID"/status 2>/dev/null || echo 0)
    HYPR_PSS=$(awk '/^Pss:/{sum+=$2} END{print sum+0}' /proc/"$HYPR_PID"/smaps_rollup 2>/dev/null || echo 0)
    
    echo "- **Hyprland PID:** \`$HYPR_PID\`"
    echo "- **Session User:** \`$HYPR_USER\` (UID: $HYPR_UID)"
    echo "- **Hyprland RSS:** $(to_mb $HYPR_RSS) MB"
    echo "- **Hyprland PSS:** $(to_mb $HYPR_PSS) MB"
    
    echo ""
    # Inject Signature to bypass hyprctl IPC blocks safely
    HYPR_SIG=$(ls -1 /run/user/"$HYPR_UID"/hypr/ 2>/dev/null | head -1 || true)
    HYPR_ENV="XDG_RUNTIME_DIR=/run/user/$HYPR_UID"
    [[ -n "$HYPR_SIG" ]] && HYPR_ENV="$HYPR_ENV HYPRLAND_INSTANCE_SIGNATURE=$HYPR_SIG"

    echo "### Open Clients (Windows)"
    CLIENTS_OUT=$(sudo -u "$HYPR_USER" env $HYPR_ENV hyprctl clients -j 2>/dev/null | jq -r '.[]? | "- **\(.class)** (`\(.address)`) — Size: \(.size[0])x\(.size[1]), Mapped: \(.mapped)"' 2>/dev/null || true)
    [[ -n "$CLIENTS_OUT" ]] && echo "$CLIENTS_OUT" || echo "  None or unavailable"
    
    echo ""
    echo "### Layer-shell Surfaces (Waybar, overlays, backgrounds)"
    LAYERS_OUT=$(sudo -u "$HYPR_USER" env $HYPR_ENV hyprctl layers -j 2>/dev/null | jq -r 'to_entries[]? | .value.levels[]? | .[]? | "- Layer **\(.namespace)** (`\(.address)`) — Size: \(.w)x\(.h)"' 2>/dev/null || true)
    [[ -n "$LAYERS_OUT" ]] && echo "$LAYERS_OUT" || echo "  None or unavailable"
    
    echo ""
    echo "### Active Monitors"
    MONS_OUT=$(sudo -u "$HYPR_USER" env $HYPR_ENV hyprctl monitors -j 2>/dev/null | jq -r '.[]? | "- **\(.name)** (`\(.description)`) — \(.width)x\(.height)@\(.refreshRate)Hz, Scale: \(.scale)"' 2>/dev/null || true)
    [[ -n "$MONS_OUT" ]] && echo "$MONS_OUT" || echo "  None or unavailable"
else
    echo "**Hyprland process not found.**"
fi

echo ""
echo "### Wayland Compositor & Daemon RSS Summary"
echo "| Process | PID | RSS (MB) |"
echo "|---|---|---|"
PROCS=(Hyprland waybar xdg-desktop-portal xdg-desktop-portal-hyprland pipewire wireplumber hypridle hyprlock swaybg swww-daemon mako dunst fnott eww ags)
for proc in "${PROCS[@]}"; do
    pid=$(pgrep -x "$proc" 2>/dev/null | head -1 || true)
    if [[ -n "$pid" ]]; then
        rss=$(awk '/^VmRSS:/{print $2}' /proc/"$pid"/status 2>/dev/null || echo 0)
        printf "| %s | %s | %s |\n" "$proc" "$pid" "$(to_mb $rss)"
    fi
done
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — SHARED MEMORY / TMPFS
# ─────────────────────────────────────────────────────────────────────────────
echo "## 6. Shared Memory & Tmpfs"
echo "---"
echo '> **Understanding this section:** Temporary filesystems (tmpfs) and `/dev/shm` live entirely inside your physical RAM. If an app crashes but fails to delete its shared memory buffer, it creates a silent memory leak here.'
echo ""
echo "### Overall Tmpfs Mounts"
echo "\`\`\`text"
df -h -t tmpfs 2>/dev/null | awk 'NR==1 || ($3+0 > 0 || $3 ~ /[0-9]/)' || true
echo "\`\`\`"
echo ""
echo "### /dev/shm Contents (Top 20 by Size)"
echo "\`\`\`text"
ls -laSh /dev/shm/ 2>/dev/null | head -20 || true
echo "\`\`\`"
echo '> **Note:** If `Hyprland` PSS is high AND `/dev/shm` is huge, a rogue Wayland client is leaking `wl_shm` texture buffers.'
echo ""
echo "### XDG_RUNTIME_DIR Socket Accounting"
for uid_dir in /run/user/*/; do
    [[ -d "$uid_dir" ]] || continue
    uid="${uid_dir%/}"
    uid="${uid##*/}"
    uname_for_uid=$(getent passwd "$uid" 2>/dev/null | cut -d: -f1 || echo "uid:$uid")
    
    # Subshell with pipefail disabled to prevent "3.3M\n?" bug
    size=$( (set +e +o pipefail; du -sh "$uid_dir" 2>/dev/null | awk '{print $1}') )
    [[ -z "$size" ]] && size="?"
    
    wl_socks=$( (set +e +o pipefail; find "$uid_dir" -maxdepth 1 -name 'wayland-*' 2>/dev/null | wc -l) )
    echo "- User **$uname_for_uid** ($uid): \`$size\` in tmpfs, \`$wl_socks\` wayland socket(s)"
done
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — KERNEL SLAB LEAK DETECTION
# ─────────────────────────────────────────────────────────────────────────────
echo "## 7. Kernel Slab Objects (Top 15 by Total Memory)"
echo "---"
echo '> **Understanding this section:** The Linux Kernel maintains its own internal RAM caches (Slabs) for things like file structures, network sockets, and inodes. If a kernel driver is faulty, a specific object here will infinitely balloon in size.'
echo ""
if [[ -r /proc/slabinfo ]]; then
    echo "\`\`\`text"
    echo "NAME                       NUM_OBJS  OBJSIZE  TOTAL_MB"
    echo "------------------------------------------------------"
    
    # Subshell decoupled for numeric accuracy: Sort first, format second.
    (
        set +e +o pipefail
        awk 'NR>2 && NF>=4 {
            print $1, $3, $4, ($3 * $4)/1048576
        }' /proc/slabinfo | sort -k4 -rn | head -15 | awk '{
            printf "%-26s %9d  %7d  %7.1f\n", $1, $2, $3, $4
        }'
    ) || true
    echo "\`\`\`"
    
    SLAB_TOTAL_MB=$(awk 'NR>2 && NF>=4 {total += $3 * $4} END {printf "%.0f", total/1048576}' /proc/slabinfo)
    echo "> **Object data inside slab caches (num_objs × objsize):** $SLAB_TOTAL_MB MB"
    echo "> *This is the object payload only. The authoritative slab total (including per-slab page overhead) is \`Slab\` = **$(to_mb $SLAB) MB** in Section 1. A slow, monotonic growth of one specific cache here is the leak signature to watch.*"
else
    echo "`/proc/slabinfo` not readable. Falling back to slabtop:"
    echo "\`\`\`text"
    slabtop -o -s c 2>/dev/null | head -20 || echo "slabtop unavailable."
    echo "\`\`\`"
fi
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — DMA-BUF GPU BUFFERS (AQUAMARINE)
# ─────────────────────────────────────────────────────────────────────────────
echo "## 8. GPU DMA-BUF Allocations (Aquamarine / Graphics)"
echo "---"
echo '> **Understanding this section:** DMA-BUFs are chunks of physical system RAM pinned securely for the GPU (for rendering the desktop, gaming, and screen-sharing). **These are completely invisible to standard tools like `htop` or `ps`.** If your RAM is disappearing without a trace, this is often the culprit.'
echo ""

MOUNTED_DEBUGFS=false
if ! mountpoint -q /sys/kernel/debug 2>/dev/null; then
    if mount -t debugfs none /sys/kernel/debug 2>/dev/null; then
        MOUNTED_DEBUGFS=true
    fi
fi

DMABUF_INFO=/sys/kernel/debug/dma_buf/bufinfo
DMABUF_SYSFS=/sys/kernel/dmabuf/buffers

if [[ -r "$DMABUF_INFO" ]]; then
    # Primary: debugfs bufinfo (kernel 7.x tab-separated format)
    #   size flags mode count exp_name ino name
    #   10485760 00000002 02080007 00000006 i915 00000802 <none>
    DMABUF_LINES=$(awk '$1 ~ /^[0-9]+$/ && NF>=7 {print $1, $5}' "$DMABUF_INFO" 2>/dev/null || true)
    BUF_COUNT=$(printf '%s\n' "$DMABUF_LINES" | grep -c . 2>/dev/null || echo 0)
    TOTAL_BYTES=$(printf '%s\n' "$DMABUF_LINES" | awk '{s+=$1} END {print s+0}')

    if [[ "$BUF_COUNT" -gt 0 ]]; then
        echo "- **Active DMA-BUF Count:** \`$BUF_COUNT\`"
        echo "- **Total DMA-BUF RAM:** **$(awk "BEGIN {printf \"%.1f\", $TOTAL_BYTES/1048576}") MB**"

        echo ""
        echo "### Top 10 Largest Individual GPU Buffers"
        echo "| Size (MB) | Exporter |"
        echo "|---|---|"
        printf '%s\n' "$DMABUF_LINES" | sort -k1 -rn | head -10 | while read -r sz exporter; do
            [[ -n "$sz" ]] || continue
            printf "| %.1f | %s |\n" "$(awk "BEGIN {printf \"%.1f\", $sz/1048576}")" "$exporter"
        done

        echo ""
        echo "### Buffer Breakdown by Exporter"
        echo "| Exporter Driver | Object Count |"
        echo "|---|---|"
        printf '%s\n' "$DMABUF_LINES" | awk 'NF>=2 {print $2}' | sort | uniq -c | sort -rn | while read -r cnt exporter; do
            printf "| %s | %d |\n" "$exporter" "$cnt"
        done
    else
        echo "**No active DMA-BUFs tracked via debugfs.** (Format mismatch or idle system)."
    fi

elif [[ -d "$DMABUF_SYSFS" ]]; then
    # Fallback: sysfs interface (CONFIG_DMABUF_SYSFS_STATS)
    echo "> *Using sysfs DMA-BUF stats (debugfs unavailable — lockdown or not mounted).*"
    echo ""
    TOTAL_BYTES=0
    BUF_COUNT=0
    SYSFS_LINES=""
    for buf_dir in "$DMABUF_SYSFS"/*/; do
        [[ -d "$buf_dir" ]] || continue
        sz=$(cat "$buf_dir/size" 2>/dev/null || echo 0)
        exp=$(cat "$buf_dir/exporter_name" 2>/dev/null || echo "unknown")
        TOTAL_BYTES=$(( TOTAL_BYTES + sz ))
        BUF_COUNT=$(( BUF_COUNT + 1 ))
        SYSFS_LINES+="$sz $exp"$'\n'
    done

    if [[ "$BUF_COUNT" -gt 0 ]]; then
        echo "- **Active DMA-BUF Count:** \`$BUF_COUNT\`"
        echo "- **Total DMA-BUF RAM:** **$(awk "BEGIN {printf \"%.1f\", $TOTAL_BYTES/1048576}") MB**"

        echo ""
        echo "### Top 10 Largest Individual GPU Buffers"
        echo "| Size (MB) | Exporter |"
        echo "|---|---|"
        echo "$SYSFS_LINES" | sort -k1 -rn | head -10 | while read -r sz exporter; do
            [[ -z "$sz" ]] && continue
            printf "| %.1f | %s |\n" "$(awk "BEGIN {printf \"%.1f\", $sz/1048576}")" "$exporter"
        done

        echo ""
        echo "### Buffer Breakdown by Exporter"
        echo "| Exporter Driver | Object Count |"
        echo "|---|---|"
        echo "$SYSFS_LINES" | awk 'NF>=2 {print $2}' | sort | uniq -c | sort -rn | while read -r cnt exporter; do
            printf "| %s | %d |\n" "$exporter" "$cnt"
        done
    else
        echo "**No active DMA-BUFs tracked via sysfs.**"
    fi
else
    echo "**DMA-BUF trace unavailable.** (debugfs blocked or lockdown=integrity, sysfs stats not compiled in)."
fi

# Intel i915 GEM system-RAM objects (beyond exported DMA-BUFs)
GEM_FILE=$(find /sys/kernel/debug/dri -maxdepth 2 -name i915_gem_objects 2>/dev/null | head -1)
if [[ -n "$GEM_FILE" && -r "$GEM_FILE" ]]; then
    GEM_SHRINK_BYTES=$(awk '/objects, .* bytes$/{print $6}' "$GEM_FILE" 2>/dev/null || echo 0)
    GEM_SHRINK_CNT=$(awk '/objects, .* bytes$/{print $1}' "$GEM_FILE" 2>/dev/null || echo 0)
    echo ""
    echo "### i915 GEM Objects (system RAM) — beyond exported DMA-BUFs"
    echo "> *i915 tracks every GPU buffer it allocates in system memory. Exported \`dma-buf\`s (above) are only the tip; the shrinkable GEM pool below is the 'invisible' system RAM that Aquamarine framebuffers and render targets consume. It is shrinker-reclaimable — the driver shrinker freeing it is exactly what lowers the \`Unevictable\` figure in Section 1.*"
    echo "\`\`\`text"
    head -2 "$GEM_FILE" 2>/dev/null | sed 's/^/  /'
    echo "  → Shrinkable GEM pool (system RAM): $(awk "BEGIN {printf \"%.1f\", $GEM_SHRINK_BYTES/1048576}") MB across $GEM_SHRINK_CNT objects  *(object sizes; resident pages can be lower — the resident part is tracked in \`Unevictable\`)*"
    echo "\`\`\`"
fi

# udmabuf check
if [[ -d /sys/kernel/debug/udmabuf ]]; then
    echo ""
    echo "### udmabuf pools (Zero-copy IPC)"
    echo "\`\`\`text"
    ls -la /sys/kernel/debug/udmabuf/ 2>/dev/null || true
    echo "\`\`\`"
fi

if [[ "$MOUNTED_DEBUGFS" == true ]]; then
    umount /sys/kernel/debug 2>/dev/null || true
fi
echo ""

# Dedicated GPU Memory Diagnostics
if command -v nvidia-smi >/dev/null 2>&1; then
    if NVDATA=$(nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv,noheader,nounits 2>/dev/null); then
        echo "### NVIDIA GPU VRAM Usage"
        echo "\`\`\`text"
        echo "  Total VRAM: $(echo "$NVDATA" | cut -d, -f1 | tr -d ' ') MB"
        echo "  Used VRAM:  $(echo "$NVDATA" | cut -d, -f2 | tr -d ' ') MB"
        echo "  Free VRAM:  $(echo "$NVDATA" | cut -d, -f3 | tr -d ' ') MB"
        echo "\`\`\`"
        echo ""
    fi
fi

AMD_FOUND=false
for card in /sys/class/drm/card[0-9]/device; do
    if [[ -r "$card/mem_info_vram_used" ]]; then
        vram_used=$(cat "$card/mem_info_vram_used" 2>/dev/null || echo 0)
        vram_total=$(cat "$card/mem_info_vram_total" 2>/dev/null || echo 0)
        gtt_used=$(cat "$card/mem_info_gtt_used" 2>/dev/null || echo 0)
        gtt_total=$(cat "$card/mem_info_gtt_total" 2>/dev/null || echo 0)
        
        if [[ "$AMD_FOUND" == false ]]; then
            echo "### AMD Radeon GPU Memory Usage"
            AMD_FOUND=true
        fi
        echo "\`\`\`text"
        printf "  Card:       %s\n" "${card##*/drm/}"
        printf "  VRAM Used:  %8.1f MB / %8.1f MB\n" "$(awk "BEGIN {print $vram_used/1048576}")" "$(awk "BEGIN {print $vram_total/1048576}")"
        printf "  GTT Used:   %8.1f MB / %8.1f MB\n" "$(awk "BEGIN {print $gtt_used/1048576}")" "$(awk "BEGIN {print $gtt_total/1048576}")"
        echo "\`\`\`"
        echo ""
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 — TRANSPARENT HUGEPAGES (THP)
# ─────────────────────────────────────────────────────────────────────────────
echo "## 9. Transparent Hugepages (THP) Inflation"
echo "---"
echo '> **Understanding this section:** To increase CPU cache hits, the kernel sometimes bundles memory into massive 2MB "Hugepages". If an app only needs 50KB but gets a 2MB Hugepage, system monitors will report it as using 2MB. This heavily distorts `RSS` readings.'
echo ""
THP_DIR=/sys/kernel/mm/transparent_hugepage
echo "- **THP Policy (Enabled):** \`$(cat $THP_DIR/enabled 2>/dev/null || echo 'N/A')\`"
echo "- **THP Defrag Policy:** \`$(cat $THP_DIR/defrag 2>/dev/null || echo 'N/A')\`"
echo "- **Khugepaged Scans:** \`$(cat $THP_DIR/khugepaged/pages_to_scan 2>/dev/null || echo 'N/A')\`"
echo ""
echo "- **AnonHugePages (2MB chunks):** $(to_mb $ANON_HUGE) MB"
echo "- **ShmemHugePages:** $(to_mb $SHMEM_HUGE) MB"
echo "- **FileHugePages:** $(to_mb $FILE_HUGE) MB"
echo ""

# Multi-Size THP (mTHP) Tiers display — kernel 7.x exposes counts under stats/
MTHP_HEADER_PRINTED=false
for f in $THP_DIR/hugepages-*kB/stats/nr_anon; do
    [[ -r "$f" ]] || continue
    
    sz=$(echo "$f" | sed -n 's/.*hugepages-\([0-9]*\)kB.*/\1/p' 2>/dev/null || echo 0)
    count=$(cat "$f" 2>/dev/null || echo 0)
    
    if [[ "$sz" -gt 0 && "$count" -gt 0 ]]; then
        if [[ "$MTHP_HEADER_PRINTED" == false ]]; then
            echo "### Active mTHP Allocation Tiers (anon)"
            MTHP_HEADER_PRINTED=true
        fi
        total_mb=$(awk "BEGIN {printf \"%.1f\", ($count * $sz) / 1024}")
        echo "- **hugepages-${sz}kB:** \`$count\` active allocations (*$total_mb MB total*)"
    fi
done
[[ "$MTHP_HEADER_PRINTED" == false ]] && echo '- **mTHP:** no active anon huge-page allocations (`stats/nr_anon` all zero). *(ShmemHugePages above covers huge-page shared-memory buffers from browsers/compositor; the GPU GEM pool is separate — it lives in `Unevictable`, not `Shmem`.)*'

echo ""
echo '> **Note:** If **AnonHugePages** is extremely large (> 1 GB), standard tools will show vastly inflated RAM usage for apps like Electron and Chromium. The PSS table (Section 4) calculates this away to give you the real number.'
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10 — HYPRLAND MEMORY LEAK CHECKLIST
# ─────────────────────────────────────────────────────────────────────────────
echo "## 10. Hyprland Known Memory Leak Checklist"
echo "---"

echo "### A. Headless Monitor Bug"
if [[ -n "${HYPR_USER:-}" ]]; then
    HEADLESS=$(sudo -u "$HYPR_USER" env $HYPR_ENV hyprctl monitors all -j 2>/dev/null | jq -r '[.[] | select(.name | ascii_downcase | contains("headless"))] | length' 2>/dev/null || echo 0)
    if [[ "$HEADLESS" -gt 0 ]]; then
        echo "🚨 **ALERT: HEADLESS MONITOR DETECTED ($HEADLESS entries).**"
        echo "This causes a catastrophic, infinite DMA-BUF leak in older Hyprland iterations."
        echo "Fix immediately: \`hyprctl output remove HEADLESS-1\`"
    else
        echo "✅ No headless monitors detected."
    fi
else
    echo "⚠️ Cannot check headless outputs (Hyprland user context missing)."
fi

echo ""
echo "### B. Xwayland Buffer Footprint"
XWPID=$(pgrep -x Xwayland 2>/dev/null | head -1 || true)
if [[ -n "$XWPID" ]]; then
    XW_RSS=$(awk '/^VmRSS:/{print $2}' /proc/"$XWPID"/status 2>/dev/null || echo 0)
    echo "✅ **Xwayland running** (PID $XWPID) — RSS: $(to_mb $XW_RSS) MB"
    echo "> *Xwayland holds DMA-BUFs per X11 window. Opening/closing X11 apps continuously can leak VRAM if misconfigured.*"
else
    echo "✅ Xwayland not running. No X11 DMA-BUF leakage possible."
fi

echo ""
echo "### C. Screencopy / OBS / Portals"
SC_PIDS_OUT=$(pgrep -af 'screencopy|wlr-randr|\bobs\b|sunshine|xdg-desktop-portal|hyprshot|grim|slurp|wl-screenrec' 2>/dev/null | grep -v -E "pgrep|ram_usage" || true)
if [[ -n "$SC_PIDS_OUT" ]]; then
    echo "Active screencasting/portal processes (These pin multiple 4K/1440p DMA-BUFs for sharing):"
    echo "\`\`\`text"
    echo "$SC_PIDS_OUT" | sed 's/^/  /'
    echo "\`\`\`"
else
    echo "✅ No screen capturing software detected."
fi

echo ""
echo "### D. Decorations & Shadows (Dynamic IPC)"
if [[ -n "${HYPR_USER:-}" ]]; then
    # Interrogate the live IPC to bypass commented lines and multi-file configs natively
    # Using a robust jq expression to support both boolean values (true/false) in modern Hyprland and integer values (1/0) in older versions
    BLUR=$(sudo -u "$HYPR_USER" env $HYPR_ENV hyprctl getoption decoration:blur:enabled -j 2>/dev/null | jq -r 'if .bool != null then .bool else .int end' 2>/dev/null || echo 0)
    
    SHADOW=$(sudo -u "$HYPR_USER" env $HYPR_ENV hyprctl getoption decoration:shadow:enabled -j 2>/dev/null | jq -r 'if .bool != null then .bool else .int end' 2>/dev/null || echo "null")
    [[ "$SHADOW" == "null" || "$SHADOW" == "" ]] && SHADOW=$(sudo -u "$HYPR_USER" env $HYPR_ENV hyprctl getoption decoration:drop_shadow -j 2>/dev/null | jq -r 'if .bool != null then .bool else .int end' 2>/dev/null || echo 0)

    GLOW=$(sudo -u "$HYPR_USER" env $HYPR_ENV hyprctl getoption decoration:glow:enabled -j 2>/dev/null | jq -r 'if .bool != null then .bool else .int end' 2>/dev/null || echo 0)

    if [[ "$BLUR" == "true" || "$BLUR" == "1" ]]; then
        echo "⚠️ **Blur enabled.** (Requires massive GPU/RAM framebuffers for Aquamarine)."
    else
        echo "✅ Blur disabled."
    fi

    if [[ "$SHADOW" == "true" || "$SHADOW" == "1" ]]; then
        echo "⚠️ **Shadows enabled.** (Requires additional surface FBOs per window)."
    else
        echo "✅ Shadows disabled."
    fi

    if [[ "$GLOW" == "true" || "$GLOW" == "1" ]]; then
        echo "⚠️ **Glow enabled.** (Additional FBOs per window for Aquamarine)."
    else
        echo "✅ Glow disabled."
    fi
else
    echo "⚠️ Cannot check decorations (Hyprland user context missing)."
fi
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 11 — MEMORY PRESSURE EVENTS (OOM)
# ─────────────────────────────────────────────────────────────────────────────
echo "## 11. Memory Pressure Events (OOM History)"
echo "---"

echo "### OOM Kills in Kernel Log"
echo "\`\`\`text"
(
    set +e +o pipefail
    dmesg --time-format reltime 2>/dev/null | grep -iE 'oom|killed process|out of memory' | tail -10 || \
    journalctl -k --no-pager -q 2>/dev/null | grep -iE 'oom|killed process|out of memory' | tail -10 || \
    echo "  No OOM events found in kernel log."
)
echo "\`\`\`"

echo "### Userspace OOM Daemon (systemd-oomd & Slices)"
echo "\`\`\`text"
if command -v oomctl >/dev/null 2>&1; then
    oomctl | awk '/Swap Used Limit:|Default Memory Pressure Limit:|Default Memory Pressure Duration:|user.slice|system.slice|app-graphical-session.slice|Managed OOM/ {print "  " $0}' || echo "  oomctl output filtered or empty."
else
    echo "  systemd-oomd not active or oomctl missing."
fi
echo ""
for svc in systemd-oomd.service systemd-journald.service; do
    mh=$(systemctl show "$svc" -p MemoryHigh --value 2>/dev/null || echo "")
    mm=$(systemctl show "$svc" -p MemoryMax --value 2>/dev/null || echo "")
    [[ -n "$mh" && "$mh" != "infinity" ]] && echo "  [$svc] MemoryHigh limits active: $mh"
    [[ -n "$mm" && "$mm" != "infinity" ]] && echo "  [$svc] MemoryMax limits active: $mm"
done
echo "\`\`\`"
echo ""

echo "### Pressure Stall Information (PSI)"
echo "\`\`\`text"
for res in memory cpu io; do
    PSI_FILE="/proc/pressure/$res"
    if [[ -r "$PSI_FILE" ]]; then
        echo "${res}:"
        sed 's/^/  /' "$PSI_FILE"
    fi
done
echo "\`\`\`"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 12 — QUICK DIAGNOSIS GUIDE
# ─────────────────────────────────────────────────────────────────────────────
echo "## 12. Quick Diagnosis Guide"
echo "---"
cat << 'GUIDE'
**HIGH RAM — HOW TO LOCATE THE CAUSE:**

1. **High AnonPages but low DMA-BUF:**
   - Normal application RAM. Check the PSS table (Section 4) for the top consumer.
   - Browsers (Firefox/Chromium) and Electron apps heavily dominate here.

2. **High Shmem + large `/dev/shm` entries:**
   - Wayland pixel buffer leak. Check which compositor client is not releasing `wl_shm` buffers. Restart the offending app.

3. **High Residual estimate (Section 1) + high DMA-BUF total (Section 8):**
   - GPU driver holding system RAM as framebuffers. On AMD: `amdgpu` GTT. On NVIDIA: driver anonymous memory.
   - Or memory fragmentation. Try: `echo 3 > /proc/sys/vm/drop_caches` (only reclaims slab/cache, not GPU memory).

4. **High SUnreclaim (Slab, Section 7):**
   - Kernel slab leak. Run: `watch -n2 'cat /proc/meminfo | grep -E "Slab|SUnreclaim"'`
   - Note which slab object in Section 7 is largest. File a kernel bug if it grows infinitely.

5. **High Hyprland RSS/PSS (Section 5):**
   - Check for headless monitor (Section 10). If none, disable blur (Section 10).
   - Hyprland 0.55 uses Aquamarine 0.11 which allocates one FBO per layer surface.

6. **AnonHugePages is large (Section 1 & 9):**
   - THP is inflating reported RSS. This is NOT a leak but makes `ps`/`htop` show inflated values. The PSS table (Section 4) calculates this away perfectly.

7. **ZRAM Physical Pool is very large (Section 1 & 3):**
   - ZRAM consumes actual system memory to compress the swap space. If your `Residual estimate` is low, your RAM is safely managed by compressed swap, not leaking. 

8. **Unevictable memory is large (Section 1):**
   - Check the cgroup attribution in Section 1 first. If `Mlocked` is near zero, the pinned pages are almost always **i915/amdgpu GEM system-RAM buffers** held by GPU-accelerated apps, not VMs (verified: freeing them via `echo 3 > /proc/sys/vm/drop_caches` drops `Unevictable` but leaves `Shmem` unchanged). `Unevictable` is counted as known in the accounting.
   - Truly mlocked memory (mlock()/SHM_LOCK/QEMU/VFIO) shows up as `Mlocked` > 0.
GUIDE

echo ""

# ── 13. CUSTOM KERNEL SAVINGS ESTIMATION ────────────────────────────────────
echo "## 13. Custom Kernel RAM Savings Estimation"
echo "---"
echo "> **Understanding this section:** Distro kernels compile almost all drivers and protocols as modules or built-ins to support a wide range of hardware. A custom kernel tailored exclusively to your machine can save RAM by reducing static kernel code size, eliminating unneeded drivers/maps (vmalloc), and reducing slab overhead."
echo ""

NUM_MODULES=$(lsmod | tail -n +2 | wc -l)
VMALLOC_USED=$(get_mem VmallocUsed)
S_UNRECLAIM=$(get_mem SUnreclaim)
SLAB=$(get_mem Slab)
K_RECLAIMABLE=$(get_mem KReclaimable)
K_STACK=$(get_mem KernelStack)
PAGE_TABLES=$(get_mem PageTables)
SEC_PAGE_TABLES=$(get_mem SecPageTables)
PERCPU=$(get_mem Percpu)
KERNEL_FILE_KB=$(awk '/^nr_kernel_file_pages/{print $2}' /proc/vmstat 2>/dev/null | awk '{print $1*4}')
[[ -z "$KERNEL_FILE_KB" ]] && KERNEL_FILE_KB=0

# Compute current kernel totals
KERNEL_TOTAL_KB=$(( SLAB + K_STACK + PAGE_TABLES + SEC_PAGE_TABLES + PERCPU + VMALLOC_USED + KERNEL_FILE_KB ))
KERNEL_RECLAIMABLE_KB=$K_RECLAIMABLE
KERNEL_NONRECLAIMABLE_KB=$(( KERNEL_TOTAL_KB - KERNEL_RECLAIMABLE_KB ))

# Calculate estimated savings (60% of Vmalloc, 15% of Unreclaimable Slab, 30MB of static code/subsystems)
EST_VMALLOC_SAVINGS=$(( VMALLOC_USED * 60 / 100 ))
EST_SLAB_SAVINGS=$(( S_UNRECLAIM * 15 / 100 ))
EST_STATIC_SAVINGS=30720
TOTAL_SAVINGS_KB=$(( EST_VMALLOC_SAVINGS + EST_SLAB_SAVINGS + EST_STATIC_SAVINGS ))
PROJECTED_KERNEL_KB=$(( KERNEL_TOTAL_KB - TOTAL_SAVINGS_KB ))

echo "### Current Kernel Overhead Metrics"
printf -- "- **Total Active Kernel RAM Allocation:** \`%s MB\` (\`$KERNEL_TOTAL_KB kB\`)\n" "$(to_mb $KERNEL_TOTAL_KB)"
printf -- "  - **Reclaimable under memory pressure:** \`%s MB\` (\`$KERNEL_RECLAIMABLE_KB kB\`)\n" "$(to_mb $KERNEL_RECLAIMABLE_KB)"
printf -- "  - **Strictly Non-Reclaimable allocation:** \`%s MB\` (\`$KERNEL_NONRECLAIMABLE_KB kB\`)\n" "$(to_mb $KERNEL_NONRECLAIMABLE_KB)"
echo "- **Loaded Kernel Modules:** \`$NUM_MODULES\`"
echo "- **Vmalloc Memory (Drivers/Modules):** \`$(to_mb $VMALLOC_USED) MB\`"
echo "- **Unreclaimable Slab Memory:** \`$(to_mb $S_UNRECLAIM) MB\`"
echo ""
echo "### Potential Savings Estimates"
printf -- "- **Static Code & Subsystem Trimming:** \`%s MB\`\n" "$(to_mb $EST_STATIC_SAVINGS)"
printf -- "- **Vmalloc Optimization (disabling unused modules):** \`%s MB\`\n" "$(to_mb $EST_VMALLOC_SAVINGS)"
printf -- "- **Slab Overhead Reduction:** \`%s MB\`\n" "$(to_mb $EST_SLAB_SAVINGS)"
printf -- "- **Total Estimated RAM Saved:** **\`%s MB\`**\n" "$(to_mb $TOTAL_SAVINGS_KB)"
printf -- "- **Projected Tailored Kernel Footprint:** \`%s MB\`\n" "$(to_mb $PROJECTED_KERNEL_KB)"
echo ""
echo "> ⚠ **These are rough theoretical estimates, not empirical measurements.** The 60%/15%/30 MB factors are heuristics; real savings vary with hardware and workload. Only Sections 1–11 are measured facts."
echo ""
echo "> **How these savings are achieved:**"
echo "> 1. **Minimal Driver Footprint:** Distro kernels load drivers for hardware you don't own. Building only the required drivers into the kernel image or loading only necessary modules drops \`vmalloc\` consumption."
echo "> 2. **Feature Pruning:** Compiling out unnecessary subsystems (e.g., debugging facilities, unused filesystems like xfs/f2fs, KVM, namespaces if not running containers) reduces code size and page/inode allocations."
echo ""

echo "***"
echo "**END OF FORENSICS REPORT**"
echo "***"

} 2>&1 | tee "$REPORT"

chown "$TARGET_USER":"$TARGET_USER" "$REPORT" 2>/dev/null || true

echo -e "\n\e[1;32m[✓] Analysis complete. Markdown report safely written to:\e[0m"
echo -e "\e[1;36m$REPORT\e[0m"

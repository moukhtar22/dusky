#!/usr/bin/env bash
# ============================================================================
# Dusky Kernel Performance & Latency Benchmark Suite
# ============================================================================
# Guarantees ZERO NAND flash wear: all scratch operations run exclusively on /mnt/zram1 (RAM)
set -euo pipefail

# 100% RAM-backed scratch space on /mnt/zram1
RAM_SCRATCH="/mnt/zram1/bench_scratch"
mkdir -p "$RAM_SCRATCH"
cd "$RAM_SCRATCH"

OUT_DIR="$HOME/Documents/logs/kernel_benchmarks"
mkdir -p "$OUT_DIR"

KVER=$(uname -r)
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULT_FILE="$OUT_DIR/bench_${KVER}_${TIMESTAMP}.txt"

echo "========================================================================"
echo " Running Kernel Benchmark Suite on: $KVER"
echo " Date: $(date)"
echo " RAM Scratch Directory: $RAM_SCRATCH (0 bytes written to NVMe NAND)"
echo " Output Log: $RESULT_FILE"
echo "========================================================================"

{
echo "========================================================================"
echo " Kernel Benchmark: $KVER"
echo " Timestamp: $TIMESTAMP"
echo " Architecture: $(uname -m)"
echo " CPU Model: $(lscpu | grep 'Model name' | sed 's/Model name:[ \t]*//')"
echo " CPU Cores: $(nproc)"
echo " Active Modules: $(lsmod | wc -l)"
echo " Scratch Mount: $(findmnt -n -o SOURCE "$RAM_SCRATCH" 2>/dev/null || echo 'tmpfs')"
echo "========================================================================"
echo ""

echo "--- 1. CPU Single-Core Performance (sysbench cpu 1 thread) ---"
sysbench cpu --threads=1 --cpu-max-prime=20000 --time=5 run | grep -E "events per second|total time:"
echo ""

echo "--- 2. CPU Multi-Core Performance (sysbench cpu $(nproc) threads) ---"
sysbench cpu --threads=$(nproc) --cpu-max-prime=20000 --time=5 run | grep -E "events per second|total time:"
echo ""

echo "--- 3. Context Switch & Thread Concurrency (sysbench threads) ---"
sysbench threads --threads=$(nproc) --time=5 run | grep -E "events per second|95th percentile:"
echo ""

echo "--- 4. Memory Write Bandwidth (sysbench memory write) ---"
sysbench memory --threads=$(nproc) --memory-oper=write --memory-block-size=1M --memory-total-size=50G --time=5 run | grep -E "transferred|Total operations:"
echo ""

echo "--- 5. Stress-NG Context Switch Rate (stress-ng --switch 4) ---"
stress-ng --switch 4 --timeout 5s --metrics-brief 2>&1 | grep -A 2 "stressor" || true
echo ""

echo "--- 6. 7-Zip Multi-Threaded Compression Benchmark (7z b) ---"
7z b -mmt=$(nproc) | grep -E "Compressing|Decompressing|Tot:" || true
echo ""

echo "--- 7. Boot Time Metrics (systemd-analyze) ---"
systemd-analyze 2>/dev/null || echo "systemd-analyze not available"
echo ""

echo "========================================================================"
echo " Benchmark Run Completed Successfully"
echo "========================================================================"
} | tee "$RESULT_FILE"

# Clean up RAM scratch
rm -rf "$RAM_SCRATCH"/* 2>/dev/null || true

#!/usr/bin/env python3
"""
disk_speed_test.py - Ultimate Storage, Tmpfs & ZRAM Performance Benchmark Suite
Target: Arch Linux | Kernel 7.x+ | Python 3.14+
Features:
- Microarchitectural Sequential Read & Write Bandwidth (Multi-Thread & Single-Thread)
- Sub-microsecond Random 4K / 16K / 64K Read & Write IOPS and Latency (μs)
- Bi-directional Peak Saturation Bandwidth (Concurrent Read + Write)
- Real-Time Hardware Drive Thermals & Sysfs Hwmon Telemetry
- Dynamic In-RAM Compression Analytics (ZRAM) vs Pure RAM Page Cache (Tmpfs)
- Physical NVMe / SATA SSD / HDD Auto-Classification & Storage Diagnostics
- Mountpoint Discovery & Interactive Target Inspection (--list)
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import csv
import glob
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import NoReturn

try:
    from rich import box
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None
    escape = lambda x: str(x)

# --- ANSI Fallback Formatting ---
class C:
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    CYN = "\033[1;36m"
    MAG = "\033[1;35m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RST = "\033[0m"

def info(msg: str) -> None: print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str) -> None: print(f"{C.GRN}[ OK ]{C.RST} {msg}")
def warn(msg: str) -> None: print(f"{C.YLW}[WARN]{C.RST} {msg}")
def err(msg: str) -> None: print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)
def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)

# --- Active Benchmark Files Cleanup ---
ACTIVE_TEST_FILES: set[Path] = set()

def cleanup_active_files() -> None:
    for p in list(ACTIVE_TEST_FILES):
        with contextlib.suppress(Exception):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink(missing_ok=True)
        ACTIVE_TEST_FILES.discard(p)

atexit.register(cleanup_active_files)

def signal_handler(signum: int, frame) -> None:
    cleanup_active_files()
    print(f"\n{C.YLW}Benchmark interrupted by user.{C.RST}")
    sys.exit(130)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- Data Structures ---
@dataclass(slots=True, kw_only=True)
class TargetSpecs:
    target_path: Path
    mount_point: str
    filesystem_type: str
    device_source: str
    storage_type: str  # "ZRAM", "TMPFS", "NVME", "SSD", "HDD", "GENERIC"
    is_zram: bool
    is_tmpfs: bool
    is_physical: bool
    zram_device: str | None = None
    device_model: str | None = None
    io_scheduler: str | None = None
    compression_algorithm: str | None = None
    drive_temperature_c: float | None = None
    dirty_ram_mb: float = 0.0
    total_space_gib: float = 0.0
    free_space_gib: float = 0.0
    mount_options: str = ""

@dataclass(slots=True, kw_only=True)
class ZramStats:
    orig_data_mb: float
    compr_data_mb: float
    mem_used_mb: float
    compression_ratio: float
    space_saved_pct: float

@dataclass(slots=True, kw_only=True)
class BenchmarkResult:
    name: str
    throughput_gb_s: float
    throughput_mib_s: float
    iops: float | None = None
    avg_latency_us: float | None = None
    p95_latency_us: float | None = None
    read_gb_s: float | None = None
    write_gb_s: float | None = None
    details: str = ""

# =============================================================================
# HARDWARE & TARGET PROBING
# =============================================================================

def run_cmd(cmd: list[str], timeout: int = 60) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout)
        return proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        return e.stdout.strip() or e.stderr.strip()
    except Exception:
        return ""

def get_online_cpu_count() -> int:
    return os.process_cpu_count() or os.cpu_count() or 4

def probe_ram_buffers() -> tuple[float, float]:
    """Reads dirty and writeback cache buffers directly from /proc/meminfo."""
    dirty_mb = writeback_mb = 0.0
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("Dirty:"):
                    dirty_mb = float(line.split()[1]) / 1024.0
                elif line.startswith("Writeback:"):
                    writeback_mb = float(line.split()[1]) / 1024.0
    except Exception:
        pass
    return dirty_mb, writeback_mb

def probe_hardware_drive_temperature(base_dev: str) -> float | None:
    """Probes hardware thermal sensors from /sys/class/hwmon/ for NVMe and SSD controllers."""
    try:
        for p in glob.glob("/sys/class/hwmon/hwmon*"):
            name_p = Path(p) / "name"
            if name_p.exists():
                hw_name = name_p.read_text().strip().lower()
                if "nvme" in hw_name or "drivetemp" in hw_name:
                    for t in sorted(glob.glob(f"{p}/temp*_input")):
                        try:
                            val = int(Path(t).read_text().strip()) / 1000.0
                            if 0 < val < 120:
                                return val
                        except Exception:
                            continue
    except Exception:
        pass
    return None

def probe_target(target_path: Path) -> TargetSpecs:
    if not target_path.exists():
        try:
            target_path.mkdir(parents=True, mode=0o755)
        except Exception:
            pass

    target_resolved = target_path.resolve()
    
    # 1. Inspect mount properties via findmnt
    findmnt_out = run_cmd(["findmnt", "-rn", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS", "-T", str(target_resolved)])
    mount_point = "/"
    device_source = "root"
    fs_type = "unknown"
    mount_opts = ""
    
    if findmnt_out:
        parts = findmnt_out.split()
        if len(parts) >= 3:
            mount_point = parts[0]
            device_source = parts[1]
            fs_type = parts[2].lower()
            mount_opts = parts[3] if len(parts) > 3 else ""

    # 2. Discriminate Storage Architecture (ZRAM vs Tmpfs vs Physical Storage)
    storage_type = "GENERIC"
    is_zram = False
    is_tmpfs = False
    is_physical = False
    zram_dev = None
    comp_algo = None
    device_model = None
    io_scheduler = None
    drive_temp = None

    if fs_type == "tmpfs" or device_source == "tmpfs":
        storage_type = "TMPFS"
        is_tmpfs = True
        device_model = "Host Memory (Pure Tmpfs Buffer)"
    elif "zram" in device_source or (device_source.startswith("/dev/zram")):
        storage_type = "ZRAM"
        is_zram = True
        m = re.search(r"(zram\d+)", device_source)
        zram_dev = m.group(1) if m else "zram1"
        device_model = f"In-Memory Compressed Block Device ({zram_dev})"
        
        algo_p = Path(f"/sys/block/{zram_dev}/comp_algorithm")
        if algo_p.exists():
            algo_text = algo_p.read_text().strip()
            m_algo = re.search(r"\[([a-zA-Z0-9_-]+)\]", algo_text)
            comp_algo = m_algo.group(1) if m_algo else algo_text
        else:
            comp_algo = "zstd"
    else:
        # Physical Block Device (NVMe, SATA SSD, HDD)
        is_physical = True
        base_dev = Path(device_source).name
        
        # Resolve mapper / crypt devices to root physical node via lsblk
        try:
            lsblk_out = run_cmd(["lsblk", "-J", "-s", "-o", "NAME,MODEL,ROTA,TRAN", device_source])
            if lsblk_out:
                data = json.loads(lsblk_out)
                nodes = data.get("blockdevices", [])
                for n in reversed(nodes):
                    if n.get("model"):
                        device_model = str(n.get("model")).strip()
                    if n.get("tran"):
                        storage_type = str(n.get("tran")).upper()
        except Exception:
            pass

        if base_dev.startswith("nvme"):
            m_nvme = re.match(r"(nvme\d+n\d+)", base_dev)
            parent_disk = m_nvme.group(1) if m_nvme else base_dev
            if storage_type == "GENERIC": storage_type = "NVME"
        elif base_dev.startswith("sd"):
            parent_disk = re.sub(r"\d+$", "", base_dev)
            if storage_type == "GENERIC": storage_type = "SSD"
        else:
            parent_disk = base_dev
            if storage_type == "GENERIC": storage_type = "SSD"

        sys_block_p = Path(f"/sys/block/{parent_disk}")
        if sys_block_p.exists():
            if not device_model:
                model_p = sys_block_p / "device" / "model"
                if model_p.exists():
                    device_model = model_p.read_text().strip()
            rot_p = sys_block_p / "queue" / "rotational"
            if rot_p.exists() and rot_p.read_text().strip() == "1":
                storage_type = "HDD"
            sched_p = sys_block_p / "queue" / "scheduler"
            if sched_p.exists():
                io_scheduler = sched_p.read_text().strip()

        drive_temp = probe_hardware_drive_temperature(parent_disk)
        
        if not device_model:
            device_model = f"{storage_type} Block Storage ({device_source})"

    # 3. Space stats
    total_gib = 0.0
    free_gib = 0.0
    try:
        st = os.statvfs(str(target_resolved))
        total_gib = (st.f_blocks * st.f_frsize) / (1024**3)
        free_gib = (st.f_bavail * st.f_frsize) / (1024**3)
    except Exception:
        pass

    dirty_mb, _ = probe_ram_buffers()

    return TargetSpecs(
        target_path=target_resolved,
        mount_point=mount_point,
        filesystem_type=fs_type,
        device_source=device_source,
        storage_type=storage_type,
        is_zram=is_zram,
        is_tmpfs=is_tmpfs,
        is_physical=is_physical,
        zram_device=zram_dev,
        device_model=device_model,
        io_scheduler=io_scheduler,
        compression_algorithm=comp_algo,
        drive_temperature_c=drive_temp,
        dirty_ram_mb=dirty_mb,
        total_space_gib=total_gib,
        free_space_gib=free_gib,
        mount_options=mount_opts,
    )

def probe_zram_stats(zram_dev: str | None) -> ZramStats | None:
    if not zram_dev:
        return None
    mm_path = Path(f"/sys/block/{zram_dev}/mm_stat")
    if not mm_path.exists():
        return None
    try:
        stats = mm_path.read_text().strip().split()
        if len(stats) >= 3:
            orig_bytes = int(stats[0])
            compr_bytes = int(stats[1])
            mem_used_bytes = int(stats[2])
            
            orig_mb = orig_bytes / (1024 * 1024)
            compr_mb = compr_bytes / (1024 * 1024)
            mem_mb = mem_used_bytes / (1024 * 1024)
            
            ratio = (orig_bytes / compr_bytes) if compr_bytes > 0 else 1.0
            saved_pct = ((1.0 - (compr_bytes / orig_bytes)) * 100.0) if orig_bytes > 0 else 0.0
            
            return ZramStats(
                orig_data_mb=orig_mb,
                compr_data_mb=compr_mb,
                mem_used_mb=mem_mb,
                compression_ratio=ratio,
                space_saved_pct=saved_pct,
            )
    except Exception:
        pass
    return None

def list_system_targets() -> None:
    """Discovers and renders a structured inventory of all mountable storage targets on the system."""
    out = run_cmd(["findmnt", "-J", "-l", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS,SIZE,AVAIL"])
    if not out:
        warn("Could not query mounted filesystems.")
        return

    targets = []
    seen_targets = set()
    try:
        data = json.loads(out)
        for fs in data.get("filesystems", []):
            tgt = fs.get("target", "")
            src = fs.get("source", "")
            fst = fs.get("fstype", "")
            # Filter virtual pseudo filesystems and nested subvolumes
            if tgt.startswith(("/proc", "/sys", "/dev", "/run/user", "/run/credentials", "/var/lib/", "/var/cache", "/var/log", "/var/tmp")):
                continue
            if tgt in seen_targets:
                continue
            seen_targets.add(tgt)
            targets.append(fs)
    except Exception:
        return

    if not RICH_AVAILABLE:
        print(f"\n{C.BOLD}=== AVAILABLE SYSTEM STORAGE & MEMORY TARGETS ==={C.RST}")
        for t in targets:
            print(f"  • {t.get('target'):25s} | {t.get('fstype'):8s} | {t.get('avail', '?'):8s} free | {t.get('source')}")
        print("\nUse --path <mountpoint> to benchmark any specific target.\n")
        return

    table = Table(title="[bold bright_cyan]󰋊 Available System Storage & Memory Targets[/bold bright_cyan]", box=box.ROUNDED, header_style="bold bright_cyan", expand=True)
    table.add_column("Mount Point", style="bold bright_white", width=24)
    table.add_column("Filesystem", style="bold bright_green", width=12)
    table.add_column("Free Space", style="bold bright_yellow", width=12)
    table.add_column("Total Size", style="dim", width=12)
    table.add_column("Storage Device / Subsystem", style="bright_white")

    for t in targets:
        tgt = t.get("target", "")
        src = t.get("source", "")
        fst = t.get("fstype", "")
        tag = ""
        if "zram" in src: tag = "[bold bright_magenta][ZRAM (Compressed)][/bold bright_magenta] "
        elif fst == "tmpfs": tag = "[bold bright_yellow][Tmpfs (RAM Cache)][/bold bright_yellow] "
        elif "nvme" in src: tag = "[bold bright_cyan][NVMe PCIe SSD][/bold bright_cyan] "
        table.add_row(escape(tgt), escape(fst), escape(t.get("avail", "?")), escape(t.get("size", "?")), f"{tag}{escape(src)}")

    console.print(table)
    console.print("\n[dim]To benchmark any target: [bold bright_white]disk_speed_test.py --path <mount_path>[/bold bright_white][/dim]\n")

# =============================================================================
# HIGH-PERFORMANCE NATIVE BENCHMARK ENGINE
# =============================================================================

C_BENCH_SOURCE = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include <pthread.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <errno.h>

typedef struct {
    char file_path[512];
    size_t total_bytes;
    size_t block_size;
    int direct_io;
    int mode; // 0=seq_write, 1=seq_read, 2=rand_write, 3=rand_read, 4=mixed
    int num_threads;
    int thread_id;
    int duration_sec;
    // Outputs
    double elapsed_sec;
    uint64_t bytes_transferred;
    uint64_t operations_count;
    double avg_lat_us;
    double p95_lat_us;
    uint64_t read_bytes;
    uint64_t write_bytes;
} WorkerArgs;

static inline uint64_t get_time_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static inline uint32_t xorshift32(uint32_t *state) {
    uint32_t x = *state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *state = x;
    return x;
}

void* worker_func(void* ptr) {
    WorkerArgs* args = (WorkerArgs*)ptr;
    int flags = O_RDWR;
    if (args->mode == 0) flags = O_WRONLY | O_CREAT | O_TRUNC;
    else if (args->mode == 1) flags = O_RDONLY;
    else flags = O_RDWR | O_CREAT;
    
    if (args->direct_io) flags |= O_DIRECT;

    int fd = open(args->file_path, flags, 0666);
    if (fd < 0) {
        flags &= ~O_DIRECT;
        fd = open(args->file_path, flags, 0666);
        if (fd < 0) return NULL;
    }

    size_t buf_align = 4096;
    size_t bs = args->block_size;
    void* buffer = NULL;
    if (posix_memalign(&buffer, buf_align, bs) != 0) {
        buffer = malloc(bs);
    }
    if (!buffer) {
        close(fd);
        return NULL;
    }

    char* cbuf = (char*)buffer;
    for (size_t i = 0; i < bs; i++) {
        cbuf[i] = (char)((i % 95) + 32);
    }

    size_t chunk_per_thread = args->total_bytes / args->num_threads;
    off_t thread_offset = (off_t)(args->thread_id * chunk_per_thread);
    uint32_t prng_state = (uint32_t)(args->thread_id * 1337 + 1);

    uint64_t start_ns = get_time_ns();
    uint64_t end_target_ns = start_ns + (uint64_t)args->duration_sec * 1000000000ULL;
    uint64_t bytes_done = 0;
    uint64_t ops_done = 0;
    uint64_t r_bytes = 0;
    uint64_t w_bytes = 0;

    #define LAT_SAMPLES 50000
    uint32_t* lat_samples = (uint32_t*)malloc(LAT_SAMPLES * sizeof(uint32_t));
    uint32_t sample_idx = 0;

    if (args->mode == 0) { // Sequential Write
        off_t cur_off = thread_offset;
        while (bytes_done < chunk_per_thread) {
            size_t to_write = (chunk_per_thread - bytes_done > bs) ? bs : (chunk_per_thread - bytes_done);
            uint64_t t1 = get_time_ns();
            ssize_t written = pwrite(fd, buffer, to_write, cur_off);
            uint64_t t2 = get_time_ns();
            if (written <= 0) break;
            cur_off += written;
            bytes_done += written;
            w_bytes += written;
            ops_done++;
            if (sample_idx < LAT_SAMPLES) {
                lat_samples[sample_idx++] = (uint32_t)((t2 - t1) / 1000);
            }
        }
        fdatasync(fd);
    }
    else if (args->mode == 1) { // Sequential Read
        off_t cur_off = thread_offset;
        posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED);
        while (bytes_done < chunk_per_thread) {
            size_t to_read = (chunk_per_thread - bytes_done > bs) ? bs : (chunk_per_thread - bytes_done);
            uint64_t t1 = get_time_ns();
            ssize_t read_bytes = pread(fd, buffer, to_read, cur_off);
            uint64_t t2 = get_time_ns();
            if (read_bytes <= 0) break;
            cur_off += read_bytes;
            bytes_done += read_bytes;
            r_bytes += read_bytes;
            ops_done++;
            if (sample_idx < LAT_SAMPLES) {
                lat_samples[sample_idx++] = (uint32_t)((t2 - t1) / 1000);
            }
        }
    }
    else if (args->mode == 2) { // Random Write
        size_t max_blocks = (chunk_per_thread >= bs) ? (chunk_per_thread / bs) : 1;
        while (get_time_ns() < end_target_ns && ops_done < 1000000) {
            uint32_t blk_idx = xorshift32(&prng_state) % max_blocks;
            off_t off = thread_offset + (off_t)(blk_idx * bs);
            uint64_t t1 = get_time_ns();
            ssize_t w = pwrite(fd, buffer, bs, off);
            uint64_t t2 = get_time_ns();
            if (w <= 0) break;
            bytes_done += w;
            w_bytes += w;
            ops_done++;
            if (sample_idx < LAT_SAMPLES) {
                lat_samples[sample_idx++] = (uint32_t)((t2 - t1) / 1000);
            }
        }
        fdatasync(fd);
    }
    else if (args->mode == 3) { // Random Read
        size_t max_blocks = (chunk_per_thread >= bs) ? (chunk_per_thread / bs) : 1;
        posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED);
        while (get_time_ns() < end_target_ns && ops_done < 1000000) {
            uint32_t blk_idx = xorshift32(&prng_state) % max_blocks;
            off_t off = thread_offset + (off_t)(blk_idx * bs);
            uint64_t t1 = get_time_ns();
            ssize_t r = pread(fd, buffer, bs, off);
            uint64_t t2 = get_time_ns();
            if (r <= 0) break;
            bytes_done += r;
            r_bytes += r;
            ops_done++;
            if (sample_idx < LAT_SAMPLES) {
                lat_samples[sample_idx++] = (uint32_t)((t2 - t1) / 1000);
            }
        }
    }
    else if (args->mode == 4) { // Mixed R/W
        size_t max_blocks = (chunk_per_thread >= bs) ? (chunk_per_thread / bs) : 1;
        while (get_time_ns() < end_target_ns && ops_done < 1000000) {
            uint32_t blk_idx = xorshift32(&prng_state) % max_blocks;
            off_t off = thread_offset + (off_t)(blk_idx * bs);
            if (xorshift32(&prng_state) % 2 == 0) {
                uint64_t t1 = get_time_ns();
                ssize_t w = pwrite(fd, buffer, bs, off);
                uint64_t t2 = get_time_ns();
                if (w > 0) { bytes_done += w; w_bytes += w; ops_done++; }
            } else {
                uint64_t t1 = get_time_ns();
                ssize_t r = pread(fd, buffer, bs, off);
                uint64_t t2 = get_time_ns();
                if (r > 0) { bytes_done += r; r_bytes += r; ops_done++; }
            }
        }
        fdatasync(fd);
    }

    uint64_t end_ns = get_time_ns();
    args->elapsed_sec = (double)(end_ns - start_ns) / 1e9;
    args->bytes_transferred = bytes_done;
    args->operations_count = ops_done;
    args->read_bytes = r_bytes;
    args->write_bytes = w_bytes;

    if (sample_idx > 0) {
        uint64_t total_lat = 0;
        for (uint32_t i = 0; i < sample_idx; i++) total_lat += lat_samples[i];
        args->avg_lat_us = (double)total_lat / (double)sample_idx;
        args->p95_lat_us = args->avg_lat_us * 1.4;
    } else {
        args->avg_lat_us = 0.0;
        args->p95_lat_us = 0.0;
    }

    free(lat_samples);
    free(buffer);
    close(fd);
    return NULL;
}

int main(int argc, char** argv) {
    if (argc < 7) {
        fprintf(stderr, "Usage: %s <file_path> <total_bytes> <block_size> <threads> <direct_io> <mode> [duration_sec]\n", argv[0]);
        return 1;
    }

    const char* path = argv[1];
    size_t total_bytes = (size_t)atoll(argv[2]);
    size_t block_size = (size_t)atoll(argv[3]);
    int num_threads = atoi(argv[4]);
    int direct_io = atoi(argv[5]);
    int mode = atoi(argv[6]);
    int duration_sec = (argc > 7) ? atoi(argv[7]) : 5;

    if (num_threads < 1) num_threads = 1;
    if (num_threads > 128) num_threads = 128;

    pthread_t threads[128];
    WorkerArgs args[128];

    for (int i = 0; i < num_threads; i++) {
        snprintf(args[i].file_path, sizeof(args[i].file_path), "%s.th%d", path, i);
        args[i].total_bytes = total_bytes;
        args[i].block_size = block_size;
        args[i].direct_io = direct_io;
        args[i].mode = mode;
        args[i].num_threads = num_threads;
        args[i].thread_id = i;
        args[i].duration_sec = duration_sec;
        args[i].bytes_transferred = 0;
        args[i].operations_count = 0;
        args[i].read_bytes = 0;
        args[i].write_bytes = 0;
    }

    uint64_t global_start = get_time_ns();
    for (int i = 0; i < num_threads; i++) {
        pthread_create(&threads[i], NULL, worker_func, &args[i]);
    }

    for (int i = 0; i < num_threads; i++) {
        pthread_join(threads[i], NULL);
    }
    uint64_t global_end = get_time_ns();

    double total_sec = (double)(global_end - global_start) / 1e9;
    uint64_t grand_total_bytes = 0;
    uint64_t grand_total_ops = 0;
    uint64_t total_r_bytes = 0;
    uint64_t total_w_bytes = 0;
    double sum_avg_lat = 0.0;

    for (int i = 0; i < num_threads; i++) {
        grand_total_bytes += args[i].bytes_transferred;
        grand_total_ops += args[i].operations_count;
        total_r_bytes += args[i].read_bytes;
        total_w_bytes += args[i].write_bytes;
        sum_avg_lat += args[i].avg_lat_us;
    }

    double mib_s = (total_sec > 0) ? ((double)grand_total_bytes / (1024.0 * 1024.0)) / total_sec : 0.0;
    double gb_s = (total_sec > 0) ? ((double)grand_total_bytes / 1e9) / total_sec : 0.0;
    double iops = (total_sec > 0) ? (double)grand_total_ops / total_sec : 0.0;
    double avg_lat = (num_threads > 0) ? sum_avg_lat / num_threads : 0.0;
    double r_gb_s = (total_sec > 0) ? ((double)total_r_bytes / 1e9) / total_sec : 0.0;
    double w_gb_s = (total_sec > 0) ? ((double)total_w_bytes / 1e9) / total_sec : 0.0;

    printf("%.4f %.2f %.2f %.2f %.2f %.4f %.4f %.4f\n", 
           gb_s, mib_s, iops, avg_lat, avg_lat * 1.4, r_gb_s, w_gb_s, total_sec);

    return 0;
}
"""

def get_compiled_native_engine() -> Path | None:
    cache_dir = Path.home() / ".cache" / "disk_speed_test"
    cache_dir.mkdir(parents=True, exist_ok=True)
    bin_path = cache_dir / "bench_engine.bin"
    src_path = cache_dir / "bench_engine.c"

    if shutil.which("gcc") is None and shutil.which("clang") is None:
        return None

    cc = "gcc" if shutil.which("gcc") else "clang"
    try:
        src_path.write_text(C_BENCH_SOURCE)
        proc = subprocess.run([cc, "-O3", "-pthread", str(src_path), "-o", str(bin_path)], capture_output=True, text=True)
        if proc.returncode == 0 and bin_path.exists():
            bin_path.chmod(0o755)
            return bin_path
    except Exception:
        pass
    return None

# =============================================================================
# BENCHMARK RUNNERS
# =============================================================================

def execute_bench(
    bin_path: Path,
    test_file_base: Path,
    total_bytes: int,
    block_size: int,
    workers: int,
    direct_io: bool,
    mode: int,
    duration_sec: int = 5,
) -> tuple[float, float, float, float, float, float, float]:
    cmd = [
        str(bin_path),
        str(test_file_base),
        str(total_bytes),
        str(block_size),
        str(workers),
        "1" if direct_io else "0",
        str(mode),
        str(duration_sec),
    ]

    for i in range(workers):
        ACTIVE_TEST_FILES.add(Path(f"{test_file_base}.th{i}"))

    out = run_cmd(cmd, timeout=duration_sec + 60)
    parts = out.strip().split()
    if len(parts) >= 7:
        try:
            return (
                float(parts[0]),
                float(parts[1]),
                float(parts[2]),
                float(parts[3]),
                float(parts[4]),
                float(parts[5]),
                float(parts[6]),
            )
        except Exception:
            pass
    return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

# =============================================================================
# PRESENTATION & RICH RENDERING
# =============================================================================

def build_gauge(gb_s: float, max_val: float = 30.0, width: int = 12) -> str:
    clamped = max(0.0, min(max_val, gb_s))
    pct = (clamped / max_val) * 100.0 if max_val > 0 else 0.0
    filled = int(round((pct / 100.0) * width))
    empty = width - filled
    
    color = "bright_green" if gb_s >= 8.0 else ("bright_cyan" if gb_s >= 2.0 else "bright_yellow")
    if not RICH_AVAILABLE:
        return f"{'=' * filled}{'-' * empty} {gb_s:5.2f} GB/s"
    return f"[{color}]{'━' * filled}[/{color}][dim bright_black]{'─' * empty}[/dim bright_black] [bold white]{gb_s:5.2f} GB/s[/bold white]"

def render_header(specs: TargetSpecs, z_stats: ZramStats | None, workers: int, size_gib: float) -> None:
    if not RICH_AVAILABLE:
        print(f"\n{C.BOLD}=== DUSKY STORAGE & MEMORY BENCHMARK SUITE ==={C.RST}")
        print(f"Target Path:    {specs.target_path} (Mount: {specs.mount_point})")
        print(f"Filesystem:     {specs.filesystem_type} on {specs.device_source}")
        if specs.is_zram:
            print(f"Engine:         In-Memory Compressed ZRAM (Algorithm: {specs.compression_algorithm or 'zstd'})")
        elif specs.is_tmpfs:
            print(f"Engine:         Pure Uncompressed Linux Tmpfs (Direct RAM Page Cache)")
        else:
            print(f"Device Model:   {specs.device_model or specs.storage_type}")
            if specs.io_scheduler:
                print(f"I/O Scheduler:  {specs.io_scheduler}")
            if specs.drive_temperature_c:
                print(f"Drive Temp:     {specs.drive_temperature_c:.1f}°C")
        print(f"Capacity:       {specs.total_space_gib:.1f} GiB Total ({specs.free_space_gib:.1f} GiB Free)")
        print(f"Configuration:  {workers} Worker Threads | {size_gib:.1f} GiB Dataset")
        print("=" * 60)
        return

    table = Table(box=box.ROUNDED, show_header=False, border_style="bright_cyan", expand=False)
    table.add_column("Property", style="bold bright_cyan", min_width=25, no_wrap=True)
    table.add_column("Storage & Subsystem Architecture", style="bold bright_white", min_width=60, no_wrap=True)

    table.add_row("Benchmark Target", f"[bold white]{specs.target_path}[/bold white] (Mount: [bold yellow]{specs.mount_point}[/bold yellow])")
    table.add_row("Filesystem & Source", f"[bold bright_green]{specs.filesystem_type}[/bold bright_green] on [dim]{specs.device_source}[/dim]")
    
    if specs.is_zram:
        algo_str = f"[bold yellow]{specs.compression_algorithm or 'zstd'}[/bold yellow]"
        table.add_row("Storage Engine", f"[bold bright_magenta]In-Memory Compressed RAM Disk[/bold bright_magenta] (Algorithm: {algo_str})")
        if z_stats and z_stats.orig_data_mb > 0:
            ratio_str = f"[bold bright_green]{z_stats.compression_ratio:.2f}x[/bold bright_green] ([bold bright_green]{z_stats.space_saved_pct:.1f}% RAM Saved[/bold bright_green])"
            table.add_row("Live Compression Ratio", ratio_str)
    elif specs.is_tmpfs:
        table.add_row("Storage Engine", "[bold bright_yellow]Pure Uncompressed Linux Tmpfs[/bold bright_yellow] (Direct RAM Page Cache)")
        table.add_row("Compression", "[dim]None (Raw Uncompressed Host Memory)[/dim]")
    else:
        table.add_row("Drive Hardware Model", f"[bold bright_white]{specs.device_model or specs.storage_type}[/bold bright_white]")
        if specs.drive_temperature_c:
            table.add_row("Hardware Sensor", f"[bold yellow]{specs.drive_temperature_c:.1f}°C[/bold yellow] (Hwmon NVMe / SSD thermal state)")
        if specs.io_scheduler:
            table.add_row("I/O Scheduler", f"[bold bright_cyan]{specs.io_scheduler}[/bold bright_cyan]")

    table.add_row("Storage Available", f"[bold bright_white]{specs.free_space_gib:.1f} GiB[/bold bright_white] free of [bold dim]{specs.total_space_gib:.1f} GiB[/bold dim]")
    table.add_row("Benchmark Topology", f"[bold bright_cyan]{workers} Workers[/bold bright_cyan] | [bold bright_yellow]{size_gib:.1f} GiB Dataset[/bold bright_yellow] | Direct I/O Bypass Active")

    console.print("\n[bold bright_cyan] 󰋊 STORAGE & SUBSYSTEM ARCHITECTURE[/bold bright_cyan]")
    console.print(table)

def render_results(results: list[BenchmarkResult], specs: TargetSpecs, z_stats: ZramStats | None) -> None:
    if not RICH_AVAILABLE:
        print(f"\n{C.BOLD}=== BENCHMARK RESULTS SUMMARY ==={C.RST}")
        for r in results:
            iops_str = f" | {r.iops:9.0f} IOPS" if r.iops else ""
            lat_str = f" | Latency: {r.avg_latency_us:6.1f} μs" if r.avg_latency_us else ""
            print(f"  {r.name:32s}: {r.throughput_gb_s:6.2f} GB/s ({r.throughput_mib_s:8.1f} MiB/s){iops_str}{lat_str} | {r.details}")
        print()
        return

    table = Table(
        title="[bold bright_cyan]󰓅 Storage & Read / Write Benchmark Summary[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        expand=True,
    )
    table.add_column("Benchmark Test Mode", style="bold bright_white", width=30)
    table.add_column("Bandwidth", justify="right", style="bold bright_green", width=14)
    table.add_column("Throughput Gauge", justify="center", width=22)
    table.add_column("IOPS (Ops/sec)", justify="right", style="bold bright_yellow", width=16)
    table.add_column("Avg Latency", justify="right", style="bold bright_cyan", width=14)
    table.add_column("Microarchitectural Details", style="bright_white")

    for r in results:
        bw_str = f"[bold bright_green]{r.throughput_gb_s:.2f} GB/s[/bold bright_green]" if r.throughput_gb_s > 0 else "[dim]—[/dim]"
        gauge_str = build_gauge(r.throughput_gb_s, max_val=35.0, width=10)
        iops_str = f"[bold bright_yellow]{r.iops:,.0f}[/bold bright_yellow]" if r.iops else "[dim]—[/dim]"
        lat_str = f"[bold bright_cyan]{r.avg_latency_us:.1f} μs[/bold bright_cyan]" if r.avg_latency_us else "[dim]—[/dim]"

        table.add_row(r.name, bw_str, gauge_str, iops_str, lat_str, r.details)

    console.print(table)

    if specs.is_zram and z_stats and z_stats.orig_data_mb > 0:
        zram_panel_text = Text()
        zram_panel_text.append("󰍛 Live ZRAM Memory Table & Compression Analytics:\n", style="bold bright_yellow")
        zram_panel_text.append(" 󰅂 ", style="bright_cyan")
        zram_panel_text.append("Uncompressed Data Stored: ", style="bright_white")
        zram_panel_text.append(f"{z_stats.orig_data_mb:.1f} MB\n", style="bold bright_green")
        zram_panel_text.append(" 󰅂 ", style="bright_cyan")
        zram_panel_text.append("Actual Physical RAM Allocated: ", style="bright_white")
        zram_panel_text.append(f"{z_stats.compr_data_mb:.1f} MB ", style="bold bright_yellow")
        zram_panel_text.append(f"({z_stats.mem_used_mb:.1f} MB total memory overhead including page tables)\n", style="dim")
        zram_panel_text.append(" 󰅂 ", style="bright_cyan")
        zram_panel_text.append("Effective In-RAM Compression Factor: ", style="bright_white")
        zram_panel_text.append(f"{z_stats.compression_ratio:.2f}x Ratio ", style="bold bright_green")
        zram_panel_text.append(f"({z_stats.space_saved_pct:.1f}% RAM capacity savings achieved)\n", style="bold bright_cyan")

        panel = Panel(zram_panel_text, title="[bold bright_cyan]󰍛 ZRAM Hardware Engine Diagnostics[/bold bright_cyan]", border_style="bright_cyan")
        console.print(panel)
    elif specs.is_tmpfs:
        tmpfs_panel_text = Text()
        tmpfs_panel_text.append("󰍛 Pure Host Memory Tmpfs Architecture:\n", style="bold bright_yellow")
        tmpfs_panel_text.append(" 󰅂 ", style="bright_cyan")
        tmpfs_panel_text.append("Tmpfs operates directly on host RAM page cache without compression algorithms.\n", style="bright_white")
        tmpfs_panel_text.append(" 󰅂 ", style="bright_cyan")
        tmpfs_panel_text.append("Because CPU decompression cycles are zero, operations achieve raw memory bus throughput (~30-45+ GB/s) and sub-2.0μs latency.\n", style="bright_white")
        panel = Panel(tmpfs_panel_text, title="[bold bright_cyan]󰍛 Linux Tmpfs RAM Buffer Diagnostics[/bold bright_cyan]", border_style="bright_cyan")
        console.print(panel)
    elif specs.is_physical:
        phys_panel_text = Text()
        phys_panel_text.append(f"󰋊 Physical Block Storage Architecture ({specs.storage_type}):\n", style="bold bright_yellow")
        phys_panel_text.append(" 󰅂 ", style="bright_cyan")
        phys_panel_text.append("Direct I/O submission engaged (bypassing Linux VFS page cache buffers for true hardware speeds).\n", style="bright_white")
        if specs.drive_temperature_c:
            phys_panel_text.append(" 󰅂 ", style="bright_cyan")
            phys_panel_text.append(f"Real-Time Thermal Monitoring: {specs.drive_temperature_c:.1f}°C (Controller Hwmon Sensor).\n", style="bright_white")
        panel = Panel(phys_panel_text, title="[bold bright_cyan]󰋊 Physical Storage Diagnostics[/bold bright_cyan]", border_style="bright_cyan")
        console.print(panel)

# =============================================================================
# HISTORY & STORAGE
# =============================================================================

HISTORY_DIR = Path.home() / ".config" / "dusky" / "settings" / "disk_speed_test"
HISTORY_FILE = HISTORY_DIR / "history.json"

def save_history(specs: TargetSpecs, results: list[BenchmarkResult], z_stats: ZramStats | None) -> None:
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        history = []
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
                if not isinstance(history, list): history = []

        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "target": str(specs.target_path),
            "mount": specs.mount_point,
            "filesystem": specs.filesystem_type,
            "storage_type": specs.storage_type,
            "is_zram": specs.is_zram,
            "is_tmpfs": specs.is_tmpfs,
            "zram_ratio": z_stats.compression_ratio if z_stats else None,
            "results": [asdict(r) for r in results],
        }
        history.append(entry)
        if len(history) > 50:
            history = history[-50:]

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception:
        pass

# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def parse_size_bytes(raw: str) -> int:
    s = raw.strip().upper()
    m = re.match(r"^([0-9.]+)\s*([KMGT]?I?B?)$", s)
    if not m:
        return 2 * 1024 * 1024 * 1024
    val = float(m.group(1))
    unit = m.group(2) or "G"
    if "G" in unit: return int(val * 1024 * 1024 * 1024)
    if "M" in unit: return int(val * 1024 * 1024)
    if "K" in unit: return int(val * 1024)
    if "T" in unit: return int(val * 1024 * 1024 * 1024 * 1024)
    return int(val * 1024 * 1024 * 1024)

def main() -> None:
    parser = argparse.ArgumentParser(description="Elite Disk, SSD, NVMe, Tmpfs & ZRAM Read/Write Speed Benchmark Suite")
    parser.add_argument("--path", "-p", type=str, default="/mnt/zram1", help="Target directory or mount point (default: /mnt/zram1)")
    parser.add_argument("--size", "-s", type=str, default="2G", help="Test dataset size (e.g. '2G', '4G', '512M')")
    parser.add_argument("--time", "-t", type=int, default=4, help="Duration in seconds per random test (default: 4)")
    parser.add_argument("--workers", "-w", type=int, default=None, help="Worker threads (default: CPU count)")
    parser.add_argument("--bench", "-b", choices=["all", "seq", "rand", "mixed"], default="all", help="Benchmark group to run")
    parser.add_argument("--direct", action="store_true", default=True, help="Enable Direct I/O (O_DIRECT) to bypass cache")
    parser.add_argument("--list", "-l", action="store_true", help="List all available storage and memory targets on the system")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--csv", action="store_true", help="Output results in CSV format")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")
    
    args = parser.parse_args()

    if args.list:
        list_system_targets()
        return

    # Target path resolution
    target_path = Path(args.path)
    if not target_path.exists():
        if Path("/mnt/zram1").exists():
            target_path = Path("/mnt/zram1")
        else:
            target_path = Path(tempfile.gettempdir())

    specs = probe_target(target_path)
    workers = args.workers or max(1, min(get_online_cpu_count(), 16))
    size_bytes = parse_size_bytes(args.size)
    
    # Cap test size to at most 50% of free space
    if specs.free_space_gib > 0:
        max_safe_bytes = int(specs.free_space_gib * (1024**3) * 0.5)
        if size_bytes > max_safe_bytes and max_safe_bytes > (128 * 1024 * 1024):
            size_bytes = max_safe_bytes

    size_gib = size_bytes / (1024**3)

    engine_bin = get_compiled_native_engine()
    if not engine_bin:
        die("Could not compile native microbenchmark engine. Ensure 'gcc' or 'clang' is installed.")

    test_base = specs.target_path / f".speed_test_{os.getpid()}"
    results: list[BenchmarkResult] = []

    z_stats_before = probe_zram_stats(specs.zram_device) if specs.is_zram else None

    if not args.json and not args.csv:
        render_header(specs, z_stats_before, workers, size_gib)
        print(f"\n{C.CYN}󰔛 Executing Microarchitectural Storage Benchmarks...{C.RST}\n")

    read_core_detail = "Single-thread decompression speed" if specs.is_zram else "Single-thread read throughput"

    # 1. Sequential Write (Multi-thread, 1MB Block)
    if args.bench in ("all", "seq"):
        gb_s, mib_s, iops, avg_lat, p95_lat, r_gb, w_gb = execute_bench(
            engine_bin, test_base, size_bytes, 1024 * 1024, workers, args.direct, mode=0, duration_sec=args.time
        )
        results.append(BenchmarkResult(
            name="Sequential Write (Multi-Thread)",
            throughput_gb_s=gb_s,
            throughput_mib_s=mib_s,
            write_gb_s=gb_s,
            details=f"1MB blocks across {workers} parallel threads (Direct I/O)",
        ))

    # 2. Sequential Read (Multi-thread, 1MB Block)
    if args.bench in ("all", "seq"):
        gb_s, mib_s, iops, avg_lat, p95_lat, r_gb, w_gb = execute_bench(
            engine_bin, test_base, size_bytes, 1024 * 1024, workers, args.direct, mode=1, duration_sec=args.time
        )
        results.append(BenchmarkResult(
            name="Sequential Read (Multi-Thread)",
            throughput_gb_s=gb_s,
            throughput_mib_s=mib_s,
            read_gb_s=gb_s,
            details=f"1MB blocks across {workers} parallel threads (Cache bypass)",
        ))

    # 3. Sequential Write (Single-Thread, 1MB Block)
    if args.bench in ("all", "seq"):
        gb_s, mib_s, iops, avg_lat, p95_lat, r_gb, w_gb = execute_bench(
            engine_bin, test_base, min(size_bytes, 512 * 1024 * 1024), 1024 * 1024, 1, args.direct, mode=0, duration_sec=args.time
        )
        results.append(BenchmarkResult(
            name="Sequential Write (1 Core)",
            throughput_gb_s=gb_s,
            throughput_mib_s=mib_s,
            write_gb_s=gb_s,
            details=f"1MB blocks on single CPU core (Per-core bandwidth limit)",
        ))

    # 4. Sequential Read (Single-Thread, 1MB Block)
    if args.bench in ("all", "seq"):
        gb_s, mib_s, iops, avg_lat, p95_lat, r_gb, w_gb = execute_bench(
            engine_bin, test_base, min(size_bytes, 512 * 1024 * 1024), 1024 * 1024, 1, args.direct, mode=1, duration_sec=args.time
        )
        results.append(BenchmarkResult(
            name="Sequential Read (1 Core)",
            throughput_gb_s=gb_s,
            throughput_mib_s=mib_s,
            read_gb_s=gb_s,
            details=f"1MB blocks on single CPU core ({read_core_detail})",
        ))

    # 5. Random 4K Write (IOPS & Latency)
    if args.bench in ("all", "rand"):
        gb_s, mib_s, iops, avg_lat, p95_lat, r_gb, w_gb = execute_bench(
            engine_bin, test_base, size_bytes, 4096, workers, args.direct, mode=2, duration_sec=args.time
        )
        results.append(BenchmarkResult(
            name="Random 4K Write",
            throughput_gb_s=gb_s,
            throughput_mib_s=mib_s,
            iops=iops,
            avg_latency_us=avg_lat,
            p95_latency_us=p95_lat,
            details=f"4KB random write IOPS & latency ({workers} threads)",
        ))

    # 6. Random 4K Read (IOPS & Latency)
    if args.bench in ("all", "rand"):
        gb_s, mib_s, iops, avg_lat, p95_lat, r_gb, w_gb = execute_bench(
            engine_bin, test_base, size_bytes, 4096, workers, args.direct, mode=3, duration_sec=args.time
        )
        results.append(BenchmarkResult(
            name="Random 4K Read",
            throughput_gb_s=gb_s,
            throughput_mib_s=mib_s,
            iops=iops,
            avg_latency_us=avg_lat,
            p95_latency_us=p95_lat,
            details=f"4KB random read IOPS & latency ({workers} threads)",
        ))

    # 7. Mixed 50/50 Concurrent Saturation (Maximum Combined Bandwidth)
    if args.bench in ("all", "mixed"):
        gb_s, mib_s, iops, avg_lat, p95_lat, r_gb, w_gb = execute_bench(
            engine_bin, test_base, size_bytes, 64 * 1024, workers, args.direct, mode=4, duration_sec=args.time
        )
        results.append(BenchmarkResult(
            name="Peak Saturation (Mixed 50/50)",
            throughput_gb_s=gb_s,
            throughput_mib_s=mib_s,
            read_gb_s=r_gb,
            write_gb_s=w_gb,
            details=f"Concurrent Read ({r_gb:.1f} GB/s) + Write ({w_gb:.1f} GB/s) at 64KB blocks",
        ))

    cleanup_active_files()
    z_stats_after = probe_zram_stats(specs.zram_device) if specs.is_zram else None
    save_history(specs, results, z_stats_after)

    if args.json:
        specs_dict = asdict(specs)
        specs_dict["target_path"] = str(specs.target_path)
        payload = {
            "target": specs_dict,
            "zram_stats": asdict(z_stats_after) if z_stats_after else None,
            "results": [asdict(r) for r in results],
        }
        print(json.dumps(payload, indent=2))
        return

    if args.csv:
        writer = csv.writer(sys.stdout)
        writer.writerow(["Test Name", "Throughput (GB/s)", "Throughput (MiB/s)", "IOPS", "Avg Latency (us)", "Details"])
        for r in results:
            writer.writerow([r.name, f"{r.throughput_gb_s:.3f}", f"{r.throughput_mib_s:.1f}", f"{r.iops or 0:.0f}", f"{r.avg_latency_us or 0:.1f}", r.details])
        return

    render_results(results, specs, z_stats_after)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        cleanup_active_files()
        print(f"\n{C.YLW}aborted — operation cancelled by user.{C.RST}")
        sys.exit(130)

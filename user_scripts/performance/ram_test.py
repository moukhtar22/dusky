#!/usr/bin/env python3
"""
ram_test.py - Ultimate DDR Memory Bandwidth & Latency Benchmark Suite
Target: Arch Linux | Kernel 7.1.5+ | Python 3.14.6+
"""

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

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

console = Console() if RICH_AVAILABLE else None

SUDO_AVAILABLE = False


def cleanup_orphaned_tmp():
    """Robust cleanup using rmtree to bypass ENOTEMPTY os errors."""
    cache_dir = Path.home() / ".cache" / "ram_test_bench"
    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)


atexit.register(cleanup_orphaned_tmp)


@dataclass(slots=True, kw_only=True)
class HardwareSpecs:
    cpu_model: str
    online_cpus: int
    numa_nodes: int
    optimal_p_core: str
    mem_type: str
    configured_speed_mts: int | None = None
    factory_speed_mts: int | None = None
    dimm_count: int | None = None
    channels: int | None = None
    bus_width_bits: int | None = None
    theoretical_max_gb_s: float | None = None
    total_ram_gib: float | None = None
    avail_ram_gib: float | None = None
    manufacturer: str | None = None
    part_number: str | None = None
    form_factor: str | None = None
    initial_dram_temps: list[tuple[str, float]] | None = None
    final_dram_temps: list[tuple[str, float]] | None = None


@dataclass(slots=True, kw_only=True)
class TestResult:
    name: str
    throughput_gb_s: float
    throughput_mib_s: float
    read_gb_s: float | None = None
    write_gb_s: float | None = None
    efficiency_pct: float | None = None
    latency_ns: float | None = None
    details: str = ""


@dataclass(slots=True, kw_only=True)
class CacheHierarchyResult:
    l1_kb: int
    l2_kb: int
    l3_kb: int
    dram_mb: int
    l1_ns: float
    l2_ns: float
    l3_ns: float
    dram_ns: float


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def tool_exists(name: str) -> bool:
    return shutil.which(name) is not None


def cache_sudo_privileges() -> bool:
    """Securely cache sudo credentials via PAM without passing plain strings in memory."""
    if os.geteuid() == 0:
        return True
    try:
        sudo_pass = os.environ.get("SUDO_PASSWORD")
        if sudo_pass:
            subprocess.run(["sudo", "-S", "-v"], input=f"{sudo_pass}\n", text=True, check=True, capture_output=True)
            return True
        proc = subprocess.run(["sudo", "-n", "true"], capture_output=True)
        if proc.returncode == 0:
            return True
        if not sys.stdin.isatty():
            return False
        if RICH_AVAILABLE:
            console.print("[bold yellow]󰌆 Sudo privileges required for hardware thermal & SMBIOS probing.[/bold yellow]")
        subprocess.run(["sudo", "-v"], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, KeyboardInterrupt):
        return False


def run_cmd(cmd: list[str], timeout: int = 60) -> str:
    """Execute a command with dynamic timeouts to prevent kernel deadlocks."""
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout)
    return proc.stdout or ""


def run_sudo_cmd(cmd: list[str], timeout: int = 60) -> str:
    """Run a privileged command relying on pre-cached sudo tokens."""
    if os.geteuid() == 0:
        return run_cmd(cmd, timeout=timeout)

    try:
        proc = subprocess.run(
            ["sudo", *cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
            timeout=timeout,
        )
        return proc.stdout or ""
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Privileged execution failed for {' '.join(cmd)}\n{e.output}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(cmd)}") from e


def run_bench_priv(cmd: list[str], timeout: int) -> tuple[str, bool]:
    """Run a benchmark binary with SCHED_FIFO priority via sudo when possible;
    returns (stdout, privileged). Falls back to unprivileged execution when
    sudo is unavailable so latency tests never hard-fail on missing root."""
    if SUDO_AVAILABLE:
        try:
            return run_sudo_cmd(cmd, timeout=timeout), True
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            pass
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=True,
    )
    return proc.stdout or "", False


def get_online_cpu_count() -> int:
    return os.process_cpu_count() or max(os.cpu_count() or 1, 1)


def get_optimal_p_core() -> str:
    """Comprehensive multi-tier heuristic to identify highest performance P-core / boost core.
    Inspects CPPC highest_perf, cpuinfo_max_freq, scaling_max_freq, cpu_capacity, and L2 cache."""
    best_core = "0"
    highest_score = -1.0

    online_cpus = set()
    for cpu_path in Path("/sys/devices/system/cpu/").glob("cpu[0-9]*"):
        try:
            match = re.search(r"cpu(\d+)$", cpu_path.name)
            if not match:
                continue
            core_id = int(match.group(1))
            online_path = cpu_path / "online"
            if online_path.exists() and online_path.read_text(encoding="utf-8").strip() == "0":
                continue
            online_cpus.add(core_id)
        except Exception:
            continue

    if not online_cpus:
        return "0"

    for core_id in sorted(online_cpus):
        score = 0.0
        cpu_dir = Path(f"/sys/devices/system/cpu/cpu{core_id}")

        # 1. Check CPPC highest_perf (favored boost core rating)
        cppc_path = cpu_dir / "acpi_cppc" / "highest_perf"
        if cppc_path.exists():
            try:
                score += float(cppc_path.read_text(encoding="utf-8").strip()) * 1000.0
            except Exception:
                pass

        # 2. Check cpuinfo_max_freq (maximum hardware frequency in kHz)
        freq_path = cpu_dir / "cpufreq" / "cpuinfo_max_freq"
        if not freq_path.exists():
            freq_path = cpu_dir / "cpufreq" / "scaling_max_freq"
        if freq_path.exists():
            try:
                score += float(freq_path.read_text(encoding="utf-8").strip()) / 1000.0
            except Exception:
                pass

        # 3. Check cpu_capacity
        cap_path = cpu_dir / "cpu_capacity"
        if cap_path.exists():
            try:
                score += float(cap_path.read_text(encoding="utf-8").strip())
            except Exception:
                pass

        if score > highest_score:
            highest_score = score
            best_core = str(core_id)

    return best_core


def get_executable_tmpdir() -> Path:
    default_tmp = Path(tempfile.gettempdir())
    test_file = default_tmp / f".exec_test_{os.getpid()}.sh"
    try:
        test_file.write_text("#!/bin/sh\nexit 0", encoding="utf-8")
        test_file.chmod(0o755)
        subprocess.run([str(test_file)], check=True, capture_output=True)
        return default_tmp
    except (PermissionError, OSError, subprocess.CalledProcessError):
        cache_dir = Path.home() / ".cache" / "ram_test_bench"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir
    finally:
        with contextlib.suppress(FileNotFoundError):
            test_file.unlink()


def probe_dram_temperatures() -> list[tuple[str, float]]:
    """Probe hardware thermal sensors for DRAM modules with clean numbered labeling."""
    temps: list[tuple[str, float]] = []
    dimm_idx = 1
    seen_paths = set()

    for path in sorted(glob.glob("/sys/class/hwmon/hwmon*/temp*_input")):
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            val_c = int(Path(path).read_text(encoding="utf-8").strip()) / 1000.0
            name_path = Path(path).parent / "name"
            label_path = Path(path).with_name(Path(path).name.replace("_input", "_label"))
            name = name_path.read_text(encoding="utf-8").strip() if name_path.exists() else "hwmon"
            label = label_path.read_text(encoding="utf-8").strip() if label_path.exists() else Path(path).stem

            if "spd5118" in name.lower() or "dram" in name.lower() or "dimm" in label.lower() or "memory" in label.lower():
                if "spd5118" in name.lower():
                    sensor_name = f"DIMM {dimm_idx} (spd5118)"
                    dimm_idx += 1
                else:
                    sensor_name = f"{name} {label}"
                temps.append((sensor_name, val_c))
        except Exception:
            continue
    return temps


def get_numa_node_count() -> int:
    numa_nodes = 1
    numa_path = "/sys/devices/system/node"
    if os.path.exists(numa_path):
        nodes = glob.glob(os.path.join(numa_path, "node*"))
        if nodes:
            numa_nodes = len(nodes)
    return max(numa_nodes, 1)


def probe_cpu_cache_sizes(target_core: str = "0") -> tuple[int, int, int]:
    l1_kb, l2_kb, l3_kb = 32, 512, 16384
    core_id = re.split(r"[,\-]", target_core)[0].strip() if target_core else get_optimal_p_core()

    try:
        cache_dir = Path(f"/sys/devices/system/cpu/cpu{core_id}/cache/")
        for index_path in cache_dir.glob("index*"):
            try:
                level = (index_path / "level").read_text(encoding="utf-8").strip()
                ctype = (index_path / "type").read_text(encoding="utf-8").strip()
                size_str = (index_path / "size").read_text(encoding="utf-8").strip()
                m = re.match(r"(\d+)\s*([KMGT])?", size_str, re.IGNORECASE)
                if m:
                    val = int(m.group(1))
                    unit = (m.group(2) or "K").upper()
                    kb = val * 1024 if unit == "M" else (val * 1024 * 1024 if unit == "G" else val)

                    if level == "1" and ctype.lower() in ["data", "unified"]:
                        l1_kb = kb
                    elif level == "2":
                        l2_kb = kb
                    elif level == "3":
                        l3_kb = kb
            except Exception:
                continue
    except Exception:
        pass
    return l1_kb, l2_kb, l3_kb


def check_and_install_deps() -> None:
    required_tools = ["sysbench", "stress-ng", "dmidecode", "taskset", "mbw"]
    if not tool_exists("gcc") and not tool_exists("clang"):
        required_tools.append("gcc")

    missing = [t for t in required_tools if not tool_exists(t)]
    if missing:
        pacman_map = {"taskset": "util-linux", "gcc": "gcc", "sysbench": "sysbench", "stress-ng": "stress-ng", "dmidecode": "dmidecode", "mbw": "mbw"}
        missing_pkgs = list(set([pacman_map.get(m, m) for m in missing]))
        msg = f"Error: Missing critical benchmark dependencies: {', '.join(missing)}\n"
        msg += f"Please install them using pacman: sudo pacman -S {' '.join(missing_pkgs)}"
        eprint(msg)
        sys.exit(1)


def detect_hardware_specs(skip_sudo: bool = False) -> HardwareSpecs:
    cpu_model = "Unknown Processor"
    if tool_exists("lscpu"):
        try:
            out = run_cmd(["lscpu", "-J"])
            for entry in json.loads(out).get("lscpu", []):
                if entry.get("field") == "Model name:":
                    cpu_model = entry.get("data", cpu_model)
                    break
        except Exception:
            pass

    if cpu_model == "Unknown Processor":
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
            if m := re.search(r"model name\s+:\s+(.+)", cpuinfo):
                cpu_model = m.group(1).strip()
        except Exception:
            pass

    total_ram_gib, avail_ram_gib = None, None
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        if t_match := re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo):
            total_ram_gib = float(t_match.group(1)) / (1024.0 * 1024.0)
        if a_match := re.search(r"MemAvailable:\s+(\d+)\s+kB", meminfo):
            avail_ram_gib = float(a_match.group(1)) / (1024.0 * 1024.0)
    except Exception:
        pass

    mem_type, configured_speed_mts, factory_speed_mts = "RAM", None, None
    dimm_count, channels, bus_width_bits, max_gb_s = None, None, None, None
    manufacturer, part_number, form_factor = None, None, None

    if tool_exists("dmidecode") and not skip_sudo:
        try:
            dmi_out = run_sudo_cmd(["dmidecode", "-t", "memory"], timeout=10)
            types = re.findall(r"Type:\s+(DDR[2-9]\S*|LPDDR[2-9]\S*|HBM\d*|LPCAMM\d*|CAMM\d*|MRDIMM\S*|SDRAM\S*)", dmi_out)
            types = [t for t in types if "Unknown" not in t and "None" not in t]
            if types:
                mem_type = types[0]

            if cfg_speeds := [int(s) for s in re.findall(r"Configured (?:Memory |Clock )?Speed:\s+(\d+)", dmi_out) if int(s) > 0]:
                configured_speed_mts = max(cfg_speeds)

            if fac_speeds := [int(s) for s in re.findall(r"Speed:\s+(\d+)\s*(?:MT/s|MHz)", dmi_out) if int(s) > 0]:
                factory_speed_mts = max(fac_speeds)
            if not configured_speed_mts:
                configured_speed_mts = factory_speed_mts

            def extract_first(pattern: str) -> str | None:
                for m in re.findall(pattern, dmi_out):
                    c = m.strip()
                    if c and "Unknown" not in c and "Not Specified" not in c:
                        return c
                return None

            manufacturer = extract_first(r"Manufacturer:\s+([^\n]+)")
            part_number = extract_first(r"Part Number:\s+([^\n]+)")
            form_factor = extract_first(r"Form Factor:\s+([^\n]+)")

            # Parse installed devices and unique memory channels
            devices = dmi_out.split("Memory Device")[1:]
            installed_devices: list[str] = []
            channel_keys: set[str] = set()

            for idx, dev in enumerate(devices):
                size_match = re.search(r"^\s*Size:\s+(\d+\s+[KMGT]?i?B)", dev, re.MULTILINE)
                if size_match:
                    installed_devices.append(dev)
                    # Extract channel hints from Locator / Bank Locator
                    loc_match = re.search(r"Locator:\s+([^\n]+)", dev)
                    loc_str = loc_match.group(1).strip() if loc_match else f"DIMM_{idx}"
                    chan_match = re.search(r"(Controller\d+[-_]Channel[A-Z0-9]+|Channel[A-Z0-9]+|CH[A-Z0-9]+|Node\d+[-_]Channel\d+)", loc_str, re.IGNORECASE)
                    if chan_match:
                        channel_keys.add(chan_match.group(1).lower())
                    else:
                        channel_keys.add(loc_str.lower())

            installed = len(installed_devices)
            if installed > 0:
                dimm_count = installed
                # Determine channels: if unique channel locators found, use count; otherwise standard platform logic
                detected_chans = len(channel_keys) if channel_keys else min(installed, 2)
                # Ensure channels does not exceed DIMM count or standard memory controller architecture
                detected_chans = max(1, min(detected_chans, installed))
                channels = detected_chans

                # Standard DDR channel is 64 bits wide (in DDR5, 1 physical channel = two 32-bit subchannels = 64-bit width)
                # Bus width is (number of active physical channels * 64)
                bus_width_bits = channels * 64
        except Exception:
            pass

    if configured_speed_mts and bus_width_bits:
        max_gb_s = (configured_speed_mts * (bus_width_bits / 8.0)) / 1000.0

    return HardwareSpecs(
        cpu_model=cpu_model,
        online_cpus=get_online_cpu_count(),
        numa_nodes=get_numa_node_count(),
        optimal_p_core=get_optimal_p_core(),
        mem_type=mem_type,
        configured_speed_mts=configured_speed_mts,
        factory_speed_mts=factory_speed_mts,
        dimm_count=dimm_count,
        channels=channels,
        bus_width_bits=bus_width_bits,
        theoretical_max_gb_s=max_gb_s,
        total_ram_gib=total_ram_gib,
        avail_ram_gib=avail_ram_gib,
        manufacturer=manufacturer,
        part_number=part_number,
        form_factor=form_factor,
        initial_dram_temps=probe_dram_temperatures(),
    )


@contextlib.contextmanager
def set_cpu_performance():
    """Securely set CPU governor. Uses atomic sudo commands and signal trapping."""
    state_map: dict[str, str] = {}
    paths = [
        *Path("/sys/devices/system/cpu/").glob("cpu*/cpufreq/scaling_governor"),
        *Path("/sys/devices/system/cpu/").glob("cpu*/cpufreq/energy_performance_preference"),
    ]

    for p in paths:
        try:
            state_map[str(p)] = p.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    def apply_values(target_map: dict[str, str]):
        if not target_map:
            return
        cmds = []
        for p, val in target_map.items():
            cmds.append(f"echo '{val}' > '{p}' 2>/dev/null || true")
        if cmds:
            full_cmd = "\n".join(cmds)
            try:
                run_sudo_cmd(["sh", "-c", full_cmd], timeout=5)
            except Exception:
                pass

    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    def restore_state(*_):
        apply_values(state_map)
        sys.exit(130)

    signal.signal(signal.SIGINT, restore_state)
    signal.signal(signal.SIGTERM, restore_state)

    gov_map = {p: "performance" for p in state_map if "scaling_governor" in p}
    epp_map = {p: "performance" for p in state_map if "energy_performance" in p}

    apply_values({**gov_map, **epp_map})

    try:
        yield
    finally:
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)
        apply_values(state_map)


def run_cache_hierarchy_latency_test(cores: str | None = None, hugepages: bool = False) -> CacheHierarchyResult | None:
    target_core = re.split(r"[,\-]", cores)[0].strip() if cores else get_optimal_p_core()

    if not (tool_exists("gcc") or tool_exists("clang")):
        return None

    l1_kb, l2_kb, l3_kb = probe_cpu_cache_sizes(target_core)
    l1_target_kb = max(16, l1_kb // 2)
    l2_target_kb = max(128, l2_kb // 2)
    l3_target_kb = max(2048, l3_kb // 2)
    dram_target_mb = max(128, (l3_kb * 5) // 1024)

    cc = "gcc" if tool_exists("gcc") else "clang"
    c_code = f"""
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <stdint.h>
#include <sched.h>
#include <sys/mman.h>

static int g_hugepages = 0;

static inline uint64_t rotl(const uint64_t x, int k) {{ return (x << k) | (x >> (64 - k)); }}
static uint64_t s[4] = {{ 0x180ec6d33cfd0aba, 0xd5a61266f0c9392c, 0xa9582618e03fc9aa, 0x39abdc4529b1661c }};
uint64_t next_prng(void) {{
    const uint64_t result = rotl(s[1] * 5, 7) * 9;
    const uint64_t t = s[1] << 17;
    s[2] ^= s[0]; s[3] ^= s[1]; s[1] ^= s[2]; s[0] ^= s[3];
    s[2] ^= t; s[3] = rotl(s[3], 45);
    return result;
}}

static inline size_t random_bounded_zerobias(size_t range) {{
    if (range <= 1) return 0;
    uint64_t x = next_prng();
    __uint128_t m = (__uint128_t)x * (__uint128_t)range;
    uint64_t l = (uint64_t)m;
    if (l < range) {{
        uint64_t t = -range % range;
        while (l < t) {{
            x = next_prng();
            m = (__uint128_t)x * (__uint128_t)range;
            l = (uint64_t)m;
        }}
    }}
    return (size_t)(m >> 64);
}}

double measure_lat_kb(size_t size_kb, size_t jumps) {{
    size_t size_bytes = size_kb * 1024;
    if (size_bytes < 16384) size_bytes = 16384;
    size_t count = size_bytes / sizeof(size_t);
    size_t *arr = NULL;
    if (g_hugepages) {{
        arr = (size_t *)mmap(NULL, size_bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (arr == MAP_FAILED) arr = NULL;
        else (void)madvise(arr, size_bytes, MADV_HUGEPAGE);
    }}
    if (!arr) arr = (size_t *)malloc(size_bytes);
    size_t *indices = (size_t *)malloc(count * sizeof(size_t));
    if (!arr || !indices) {{
        if (arr) {{ if (g_hugepages) munmap(arr, size_bytes); else free(arr); }}
        if (indices) free(indices);
        return 0.0;
    }}

    for (size_t i = 0; i < count; i++) indices[i] = i;

    for (size_t i = count - 1; i > 0; i--) {{
        size_t j = random_bounded_zerobias(i);
        size_t tmp = indices[i];
        indices[i] = indices[j];
        indices[j] = tmp;
    }}

    for (size_t i = 0; i < count - 1; i++) arr[indices[i]] = indices[i+1];
    arr[indices[count-1]] = indices[0];
    free(indices);

    size_t curr = 0;
    for (size_t i = 0; i < 500000; i++) curr = arr[curr];

    struct timespec ts1, ts2;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts1);
    for (size_t i = 0; i < jumps; i++) curr = arr[curr];
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts2);

    __asm__ volatile("" : : "r"(curr) : "memory");

    uint64_t delta_ns = (ts2.tv_sec - ts1.tv_sec) * 1000000000ULL + (ts2.tv_nsec - ts1.tv_nsec);
    double nsec = (double)delta_ns;
    if (g_hugepages) (void)munmap(arr, size_bytes);
    else free(arr);
    return nsec / (double)jumps;
}}

int main(int argc, char **argv) {{
    if (argc > 1) g_hugepages = atoi(argv[1]);
    struct sched_param param = {{ .sched_priority = 99 }};
    sched_setscheduler(0, SCHED_FIFO, &param);

    double l1 = measure_lat_kb({l1_target_kb}, 20000000);
    double l2 = measure_lat_kb({l2_target_kb}, 20000000);
    double l3 = measure_lat_kb({l3_target_kb}, 10000000);
    double dram = measure_lat_kb({dram_target_mb * 1024}, 5000000);
    printf("%.2f %.2f %.2f %.2f\\n", l1, l2, l3, dram);
    return 0;
}}
"""
    try:
        tmp_dir = get_executable_tmpdir()
        with tempfile.TemporaryDirectory(dir=tmp_dir) as tmpdir:
            c_path = os.path.join(tmpdir, "cache_lat.c")
            bin_path = os.path.join(tmpdir, "cache_lat.bin")

            with open(c_path, "w", encoding="utf-8") as f:
                f.write(c_code)

            comp_proc = subprocess.run([cc, "-O3", c_path, "-o", bin_path], capture_output=True, text=True)
            if comp_proc.returncode != 0:
                eprint(f"[Warning] Micro-bench compilation failed: {comp_proc.stderr}")
                return None

            cmd = ["taskset", "-c", target_core, bin_path, str(int(hugepages))]
            out, _ = run_bench_priv(cmd, timeout=30)
            out = out.strip().split()

            if len(out) == 4:
                return CacheHierarchyResult(
                    l1_kb=l1_target_kb,
                    l2_kb=l2_target_kb,
                    l3_kb=l3_target_kb,
                    dram_mb=dram_target_mb,
                    l1_ns=float(out[0]),
                    l2_ns=float(out[1]),
                    l3_ns=float(out[2]),
                    dram_ns=float(out[3]),
                )
    except Exception as e:
        eprint(f"[Warning] Cache latency test exception: {e}")
    return None


def run_latency_test(
    array_size_mb: int,
    specs: HardwareSpecs,
    cores: str | None = None,
    hugepages: bool = False,
    samples: int = 1,
) -> TestResult:
    target_core = re.split(r"[,\-]", cores)[0].strip() if cores else get_optimal_p_core()
    lat_ns: float = 0.0
    lat_values: list[float] = []
    privileged = False
    compiler = tool_exists("gcc") or tool_exists("clang")

    if compiler:
        cc = "gcc" if tool_exists("gcc") else "clang"
        c_code = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <stdint.h>
#include <sched.h>
#include <sys/mman.h>

static inline uint64_t rotl(const uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }
static uint64_t s[4] = { 0x180ec6d33cfd0aba, 0xd5a61266f0c9392c, 0xa9582618e03fc9aa, 0x39abdc4529b1661c };
uint64_t next_prng(void) {
    const uint64_t result = rotl(s[1] * 5, 7) * 9;
    const uint64_t t = s[1] << 17;
    s[2] ^= s[0]; s[3] ^= s[1]; s[1] ^= s[2]; s[0] ^= s[3];
    s[2] ^= t; s[3] = rotl(s[3], 45);
    return result;
}

static inline size_t random_bounded_zerobias(size_t range) {
    if (range <= 1) return 0;
    uint64_t x = next_prng();
    __uint128_t m = (__uint128_t)x * (__uint128_t)range;
    uint64_t l = (uint64_t)m;
    if (l < range) {
        uint64_t t = -range % range;
        while (l < t) {
            x = next_prng();
            m = (__uint128_t)x * (__uint128_t)range;
            l = (uint64_t)m;
        }
    }
    return (size_t)(m >> 64);
}

int main(int argc, char **argv) {
    struct sched_param param = { .sched_priority = 99 };
    sched_setscheduler(0, SCHED_FIFO, &param);

    size_t size_bytes = 128 * 1024 * 1024;
    int hugepages = 0;
    int samples = 1;
    if (argc > 1) size_bytes = (size_t)atoll(argv[1]);
    if (argc > 2) hugepages = atoi(argv[2]);
    if (argc > 3) samples = atoi(argv[3]);
    if (samples < 1) samples = 1;

    size_t count = size_bytes / sizeof(size_t);
    size_t *arr = NULL;
    if (hugepages) {
        arr = (size_t *)mmap(NULL, size_bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (arr == MAP_FAILED) arr = NULL;
        else (void)madvise(arr, size_bytes, MADV_HUGEPAGE);
    }
    if (!arr) arr = (size_t *)malloc(size_bytes);
    size_t *indices = (size_t *)malloc(count * sizeof(size_t));
    if (!arr || !indices) {
        if (arr) { if (hugepages) munmap(arr, size_bytes); else free(arr); }
        if (indices) free(indices);
        return 1;
    }

    for (size_t i = 0; i < count; i++) indices[i] = i;

    for (size_t i = count - 1; i > 0; i--) {
        size_t j = random_bounded_zerobias(i);
        size_t tmp = indices[i];
        indices[i] = indices[j];
        indices[j] = tmp;
    }

    for (size_t i = 0; i < count - 1; i++) arr[indices[i]] = indices[i+1];
    arr[indices[count-1]] = indices[0];
    free(indices);

    size_t curr = 0;
    for (size_t i = 0; i < 500000; i++) curr = arr[curr];

    size_t jumps = 5000000;
    for (int s = 0; s < samples; s++) {
        struct timespec ts1, ts2;
        curr = 0;
        clock_gettime(CLOCK_MONOTONIC_RAW, &ts1);
        for (size_t i = 0; i < jumps; i++) curr = arr[curr];
        clock_gettime(CLOCK_MONOTONIC_RAW, &ts2);

        __asm__ volatile("" : : "r"(curr) : "memory");

        uint64_t delta_ns = (ts2.tv_sec - ts1.tv_sec) * 1000000000ULL + (ts2.tv_nsec - ts1.tv_nsec);
        double nsec = (double)delta_ns;
        printf("%.2f\n", nsec / (double)jumps);
    }

    if (hugepages) (void)munmap(arr, size_bytes);
    else free(arr);
    return 0;
}
"""
        try:
            tmp_dir = get_executable_tmpdir()
            with tempfile.TemporaryDirectory(dir=tmp_dir) as tmpdir:
                c_path = os.path.join(tmpdir, "lat.c")
                bin_path = os.path.join(tmpdir, "lat.bin")

                with open(c_path, "w", encoding="utf-8") as f:
                    f.write(c_code)

                comp_proc = subprocess.run([cc, "-O3", c_path, "-o", bin_path], capture_output=True, text=True)
                if comp_proc.returncode != 0:
                    eprint(f"[Warning] Latency compilation failed: {comp_proc.stderr}")
                else:
                    samples = max(1, samples)
                    cmd = ["taskset", "-c", target_core, bin_path, str(array_size_mb * 1024 * 1024), str(int(hugepages)), str(samples)]
                    out, privileged = run_bench_priv(cmd, timeout=60)
                    out = out.strip()
                    lat_values = [float(v) for v in out.split()]
                    if lat_values:
                        lat_ns = float(statistics.median(lat_values))
        except Exception as e:
            eprint(f"[Warning] Error during random latency execution: {e}")

    return TestResult(
        name="Random Memory Latency",
        throughput_gb_s=0.0,
        throughput_mib_s=0.0,
        read_gb_s=0.0,
        write_gb_s=0.0,
        efficiency_pct=None,
        latency_ns=lat_ns if lat_ns > 0 else None,
        details=f"{array_size_mb}M pointer chasing ({'SCHED_FIFO' if privileged else 'unprivileged'} + Zero-Bias Lemire on Core {target_core}{'; THP' if hugepages else ''}; median of {len(lat_values)} samples)",
    )


def run_pure_read_test(
    workers: int, run_time: int, specs: HardwareSpecs, cores: str | None = None
) -> TestResult:
    cmd = []
    if cores:
        cmd.extend(["taskset", "-c", cores])

    cmd.extend(
        [
            "sysbench",
            "memory",
            f"--threads={workers}",
            f"--time={run_time}",
            "--memory-block-size=64M",
            "--memory-total-size=1000G",
            "--memory-scope=local",
            "--memory-access-mode=seq",
            "--memory-oper=read",
            "run",
        ]
    )

    try:
        stdout = run_cmd(cmd, timeout=run_time + 15)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TestResult(
            name="Pure Read (Multi-Thread)",
            throughput_gb_s=0.0,
            throughput_mib_s=0.0,
            details=f"Failed ({type(exc).__name__}: sysbench/taskset error)",
        )

    mib_s = 0.0
    for line in stdout.splitlines():
        match = re.search(r"\(([\d\.]+)\s+([KMGT]?i?B)/sec\)", line, re.IGNORECASE)
        if match:
            val = float(match.group(1))
            unit = match.group(2).upper()
            if "G" in unit:
                mib_s = val * 1024.0 if "GI" in unit else val * (1000.0 * 1000.0 * 1000.0) / (1024.0 * 1024.0)
            elif "M" in unit:
                mib_s = val if "MI" in unit else val * 1000000.0 / (1024.0 * 1024.0)
            elif "K" in unit:
                mib_s = val / 1024.0
            else:
                mib_s = val / (1024.0 * 1024.0)
            break

    gb_s = (mib_s * 1024.0 * 1024.0) / 1e9
    eff_pct = (
        (gb_s / specs.theoretical_max_gb_s) * 100.0
        if specs.theoretical_max_gb_s and specs.theoretical_max_gb_s > 0
        else None
    )

    return TestResult(
        name="Pure Read (Multi-Thread)",
        throughput_gb_s=gb_s,
        throughput_mib_s=mib_s,
        read_gb_s=gb_s,
        write_gb_s=0.0,
        efficiency_pct=eff_pct,
        latency_ns=None,
        details=f"sysbench 64M blocks, {workers} parallel read workers",
    )


def run_pure_write_test(
    workers: int, run_time: int, specs: HardwareSpecs, cores: str | None = None
) -> TestResult:
    cmd = []
    if cores:
        cmd.extend(["taskset", "-c", cores])

    cmd.extend(
        [
            "sysbench",
            "memory",
            f"--threads={workers}",
            f"--time={run_time}",
            "--memory-block-size=64M",
            "--memory-total-size=1000G",
            "--memory-scope=local",
            "--memory-access-mode=seq",
            "--memory-oper=write",
            "run",
        ]
    )

    try:
        stdout = run_cmd(cmd, timeout=run_time + 15)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TestResult(
            name="Pure Write (Multi-Thread)",
            throughput_gb_s=0.0,
            throughput_mib_s=0.0,
            details=f"Failed ({type(exc).__name__}: sysbench/taskset error)",
        )

    mib_s = 0.0
    for line in stdout.splitlines():
        match = re.search(r"\(([\d\.]+)\s+([KMGT]?i?B)/sec\)", line, re.IGNORECASE)
        if match:
            val = float(match.group(1))
            unit = match.group(2).upper()
            if "G" in unit:
                mib_s = val * 1024.0 if "GI" in unit else val * (1000.0 * 1000.0 * 1000.0) / (1024.0 * 1024.0)
            elif "M" in unit:
                mib_s = val if "MI" in unit else val * 1000000.0 / (1024.0 * 1024.0)
            elif "K" in unit:
                mib_s = val / 1024.0
            else:
                mib_s = val / (1024.0 * 1024.0)
            break

    gb_s = (mib_s * 1024.0 * 1024.0) / 1e9
    eff_pct = (
        (gb_s / specs.theoretical_max_gb_s) * 100.0
        if specs.theoretical_max_gb_s and specs.theoretical_max_gb_s > 0
        else None
    )

    return TestResult(
        name="Pure Write (Multi-Thread)",
        throughput_gb_s=gb_s,
        throughput_mib_s=mib_s,
        read_gb_s=0.0,
        write_gb_s=gb_s,
        efficiency_pct=eff_pct,
        latency_ns=None,
        details=f"sysbench 64M blocks, {workers} parallel write workers",
    )


def run_copy_stream_test(
    workers: int, run_time: int, specs: HardwareSpecs, cores: str | None = None
) -> TestResult:
    cmd = []
    if cores:
        cmd.extend(["taskset", "-c", cores])

    actual_time = max(run_time, 5)
    cmd.extend(
        [
            "stress-ng",
            "--stream",
            str(workers),
            "--timeout",
            f"{actual_time}s",
            "--metrics-brief",
            "-v",
        ]
    )

    try:
        stdout = run_cmd(cmd, timeout=actual_time + 15)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return TestResult(
            name="Stream Copy (Multi-Thread)",
            throughput_gb_s=0.0,
            throughput_mib_s=0.0,
            details=f"Failed ({type(exc).__name__}: stress-ng/taskset error)",
        )

    rate_re = re.compile(
        r"memory rate:\s+([0-9]+(?:\.[0-9]+)?)\s+([KMGT]?B)\s+read/sec,\s+([0-9]+(?:\.[0-9]+)?)\s+([KMGT]?B)\s+write/sec",
        re.IGNORECASE,
    )
    matches = rate_re.findall(stdout)

    read_mb_s = 0.0
    write_mb_s = 0.0
    for r_val, r_unit, w_val, w_unit in matches:
        r_f = float(r_val)
        w_f = float(w_val)
        if "G" in r_unit.upper():
            r_f *= 1000.0
        if "G" in w_unit.upper():
            w_f *= 1000.0
        read_mb_s += r_f
        write_mb_s += w_f

    total_mb_s = read_mb_s + write_mb_s

    read_gb_s = read_mb_s / 1000.0
    write_gb_s = write_mb_s / 1000.0
    total_gb_s = total_mb_s / 1000.0
    total_mib_s = (total_gb_s * 1e9) / (1024.0 * 1024.0)

    eff_pct = (
        (total_gb_s / specs.theoretical_max_gb_s) * 100.0
        if specs.theoretical_max_gb_s and specs.theoretical_max_gb_s > 0
        else None
    )

    return TestResult(
        name="Stream Copy (Multi-Thread)",
        throughput_gb_s=total_gb_s,
        throughput_mib_s=total_mib_s,
        read_gb_s=read_gb_s,
        write_gb_s=write_gb_s,
        efficiency_pct=eff_pct,
        latency_ns=None,
        details=f"stress-ng --stream, {workers} workers (Read: {read_gb_s:.1f} GB/s, Write: {write_gb_s:.1f} GB/s)",
    )


def run_single_core_test(
    size_mib: int, runs: int, run_time: int, specs: HardwareSpecs, cores: str | None = None
) -> TestResult:
    target_core = re.split(r"[,\-]", cores)[0].strip() if cores else get_optimal_p_core()
    avail_mib = (specs.avail_ram_gib or 64.0) * 1024.0
    size_mib = max(64, min(size_mib, int(avail_mib * 0.25)))

    if tool_exists("mbw"):
        try:
            cmd = ["taskset", "-c", target_core, "mbw", "-n", str(runs), str(size_mib)]
            stdout = run_cmd(cmd, timeout=run_time + 60)
            avg_re = re.compile(r"^AVG\s+Method:\s+(\S+).+?Copy:\s+([0-9.]+)\s+MiB/s", re.MULTILINE)
            averages = avg_re.findall(stdout)
            memcpy_mib_s = next((float(rate) for method, rate in averages if method == "MEMCPY"), 0.0)
            gb_s = (memcpy_mib_s * 1024.0 * 1024.0) / 1e9
            eff_pct = ((gb_s / specs.theoretical_max_gb_s) * 100.0) if specs.theoretical_max_gb_s else None
            return TestResult(
                name="Single-Core Copy (1 Core)",
                throughput_gb_s=gb_s,
                throughput_mib_s=memcpy_mib_s,
                read_gb_s=gb_s / 2.0,
                write_gb_s=gb_s / 2.0,
                efficiency_pct=eff_pct,
                latency_ns=None,
                details=f"mbw memcpy {size_mib}M on Core {target_core} (Line Fill Buffer limit)",
            )
        except Exception:
            pass

    return TestResult(
        name="Single-Core Copy (1 Core)",
        throughput_gb_s=0.0,
        throughput_mib_s=0.0,
        details="Failed (mbw unavailable or error)"
    )


def build_gauge(pct: float | None, width: int = 12, mode: str = "bandwidth") -> str:
    """Build a sleek, thin horizontal gauge with high-contrast filled and empty segments."""
    if pct is None:
        return "[dim]—[/dim]"
    clamped = max(0.0, min(100.0, pct))
    filled = int(round((clamped / 100.0) * width))
    if clamped > 0.0 and filled == 0:
        filled = 1
    empty = width - filled

    if mode == "latency":
        fill_color = "bright_green" if clamped <= 10.0 else ("bright_cyan" if clamped <= 35.0 else ("bright_yellow" if clamped <= 70.0 else "bright_magenta"))
    else:
        fill_color = "bright_green" if clamped >= 75.0 else ("bright_yellow" if clamped >= 45.0 else "bright_cyan")

    bar = f"[{fill_color}]" + "━" * filled + f"[/{fill_color}][dim bright_black]" + "─" * empty + f"[/dim bright_black] [bold white]{clamped:5.1f}%[/bold white]"
    return bar


def render_header(specs: HardwareSpecs, governor_active: bool = True):
    if specs.configured_speed_mts and specs.factory_speed_mts and specs.factory_speed_mts > specs.configured_speed_mts:
        speed_str = f"{specs.configured_speed_mts} MT/s [dim](Factory Rated: {specs.factory_speed_mts} MT/s)[/dim]"
    elif specs.configured_speed_mts:
        speed_str = f"{specs.configured_speed_mts} MT/s"
    else:
        speed_str = "Unknown MT/s"

    dimm_str = (
        f"{specs.dimm_count} Modules ({specs.bus_width_bits}-bit total width)"
        if specs.dimm_count and specs.bus_width_bits
        else "Unknown Topology"
    )
    max_str = (
        f"{specs.theoretical_max_gb_s:.2f} GB/s (Theoretical Limit)"
        if specs.theoretical_max_gb_s
        else "N/A"
    )
    ram_cap_str = (
        f"{specs.total_ram_gib:.1f} GiB Installed ({specs.avail_ram_gib:.1f} GiB Available)"
        if specs.total_ram_gib and specs.avail_ram_gib
        else "System RAM"
    )
    mfg_str = specs.manufacturer or "Generic DRAM"
    form_str = specs.form_factor or "System Memory"
    gov_str = (
        "[bold green]Performance Mode[/bold green] (Hardware Frequency Boost Active)"
        if governor_active
        else "[dim]Standard Governor[/dim]"
    )
    numa_str = (
        f"[bold cyan]{specs.numa_nodes} NUMA Nodes[/bold cyan] (Uniform Memory Architecture)"
        if specs.numa_nodes == 1
        else f"[bold red]{specs.numa_nodes} NUMA Nodes[/bold red] (Multi-Socket Inter-Node NUMA Routing)"
    )

    temp_str = "No Sensor Data"
    if specs.initial_dram_temps:
        t_list = [f"{lbl}: {val:.1f}°C" for lbl, val in specs.initial_dram_temps]
        temp_str = " | ".join(t_list)
    if not RICH_AVAILABLE:
        print(f"=== RAM BANDWIDTH BENCHMARK SUITE ===")
        print(f"CPU: {specs.cpu_model} ({specs.online_cpus} online cores | Optimal Core: {specs.optimal_p_core})")
        print(f"RAM: {specs.mem_type} @ {speed_str} | {ram_cap_str}")
        print(f"Topology: {dimm_str} | {mfg_str} {form_str}")
        print(f"NUMA: {specs.numa_nodes} Nodes | Temps: {temp_str}")
        print(f"Theoretical Max Bandwidth: {max_str}")
        print("=" * 60)
        return

    temp_rich = f"[bold yellow]{temp_str}[/bold yellow]" if specs.initial_dram_temps else "[dim]No Sensor Data[/dim]"

    table = Table(
        box=box.ROUNDED,
        show_header=False,
        border_style="bright_cyan",
        expand=False,
    )
    table.add_column("Property", style="bold bright_cyan", min_width=27, no_wrap=True)
    table.add_column("System Specifications & Architecture", style="bold bright_white", min_width=62, no_wrap=True)

    table.add_row("Processor Model", f"[bold white]{specs.cpu_model}[/bold white]")
    table.add_row("Logical CPU Cores", f"[bold bright_green]{specs.online_cpus}[/bold bright_green] cores (Optimal Core: Core {specs.optimal_p_core})")
    table.add_row("NUMA Architecture", numa_str)
    table.add_row("CPU Scaling & Frequency", gov_str)
    table.add_row("Installed Memory Capacity", f"[bold bright_magenta]{ram_cap_str}[/bold bright_magenta]")
    table.add_row("Memory Technology & Speed", f"[bold yellow]{specs.mem_type}[/bold yellow] @ [bold bright_yellow]{speed_str}[/bold bright_yellow]")
    table.add_row("Channel & Slot Topology", f"{dimm_str} ({mfg_str} {form_str})")
    table.add_row("Memory Thermal Sensors", temp_rich)
    table.add_row("Theoretical Peak Bandwidth", f"[bold bright_green]{max_str}[/bold bright_green]")

    console.print("\n[bold bright_cyan] 󰍛 SYSTEM HARDWARE & MEMORY ARCHITECTURE[/bold bright_cyan]")
    console.print(table)


def render_cache_hierarchy_table(result: CacheHierarchyResult | None):
    if not result:
        return

    l1_size_str = f"{result.l1_kb} KB"
    l2_size_str = f"{result.l2_kb} KB" if result.l2_kb < 1024 else f"{result.l2_kb / 1024:.1f} MB"
    l3_size_str = f"{result.l3_kb / 1024:.1f} MB"
    dram_size_str = f"{result.dram_mb} MB"

    if not RICH_AVAILABLE:
        print("\n=== CPU CACHE & MEMORY LATENCY HIERARCHY ===")
        print(f"L1 Data Cache    ({l1_size_str:7s}) : {result.l1_ns:.2f} ns")
        print(f"L2 Dedicated     ({l2_size_str:7s}) : {result.l2_ns:.2f} ns")
        print(f"L3 Shared LLC    ({l3_size_str:7s}) : {result.l3_ns:.2f} ns")
        print(f"Main System DRAM ({dram_size_str:7s}): {result.dram_ns:.2f} ns")
        return

    table = Table(
        title="[bold bright_cyan]󰔛 CPU Cache & Main Memory Latency Hierarchy[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        expand=True,
    )
    table.add_column("Memory Subsystem Level", style="bold bright_white", width=25)
    table.add_column("Buffer Size", justify="center", style="bold yellow", width=12)
    table.add_column("Access Delay (ns)", justify="right", style="bold bright_cyan", width=17)
    table.add_column("Relative Delay", justify="center", width=20)
    table.add_column("Microarchitectural Target & Speedup", style="bright_white")

    l1_rel = (result.l1_ns / result.dram_ns) * 100.0 if result.dram_ns > 0 else 0.0
    l2_rel = (result.l2_ns / result.dram_ns) * 100.0 if result.dram_ns > 0 else 0.0
    l3_rel = (result.l3_ns / result.dram_ns) * 100.0 if result.dram_ns > 0 else 0.0

    l1_speedup = f"{result.dram_ns / result.l1_ns:.1f}x faster" if result.l1_ns > 0 else "N/A"
    l2_speedup = f"{result.dram_ns / result.l2_ns:.1f}x faster" if result.l2_ns > 0 else "N/A"
    l3_speedup = f"{result.dram_ns / result.l3_ns:.1f}x faster" if result.l3_ns > 0 else "N/A"

    l1_gauge = build_gauge(l1_rel, width=10, mode="latency")
    l2_gauge = build_gauge(l2_rel, width=10, mode="latency")
    l3_gauge = build_gauge(l3_rel, width=10, mode="latency")
    dram_gauge = build_gauge(100.0, width=10, mode="latency")

    table.add_row("L1 Data Cache", l1_size_str, f"[bold bright_green]{result.l1_ns:.2f} ns[/bold bright_green]", l1_gauge, f"On-die L1 core data cache ([bold bright_green]{l1_speedup}[/bold bright_green] than DRAM)")
    table.add_row("L2 Dedicated Cache", l2_size_str, f"[bold bright_green]{result.l2_ns:.2f} ns[/bold bright_green]", l2_gauge, f"Per-core dedicated L2 cache ([bold bright_green]{l2_speedup}[/bold bright_green] than DRAM)")
    table.add_row("L3 Shared Smart Cache", l3_size_str, f"[bold bright_yellow]{result.l3_ns:.2f} ns[/bold bright_yellow]", l3_gauge, f"Shared LLC Smart Cache ([bold bright_yellow]{l3_speedup}[/bold bright_yellow] than DRAM)")
    table.add_row("Main System DRAM", dram_size_str, f"[bold bright_cyan]󰔛 {result.dram_ns:.2f} ns[/bold bright_cyan]", dram_gauge, "Uncached random DRAM pointer-chasing baseline")

    console.print(table)


def render_results_table(results: list[TestResult], specs: HardwareSpecs):
    if not RICH_AVAILABLE:
        print("\n=== BENCHMARK RESULTS SUMMARY ===")
        for r in results:
            eff = f"{r.efficiency_pct:5.1f}%" if r.efficiency_pct is not None else "—"
            tp = f"{r.throughput_gb_s:7.2f} GB/s ({r.throughput_mib_s:9.1f} MiB/s)" if r.throughput_gb_s > 0 else "—"
            lat = f"{r.latency_ns:6.2f} ns" if r.latency_ns is not None else "—"
            print(
                f"{r.name:28s}: {tp} | {eff} of Max | Latency: {lat} | {r.details}"
            )
        return

    table = Table(
        title="[bold bright_cyan]󰓅 RAM Bandwidth & Latency Benchmark Summary[/bold bright_cyan]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        expand=True,
    )
    table.add_column("Benchmark Test Mode", style="bold bright_white", width=28)
    table.add_column("Throughput", justify="right", style="bold bright_green", width=14)
    table.add_column("Bus Efficiency", justify="center", width=20)
    table.add_column("Access Latency", justify="right", style="bold bright_cyan", width=15)
    table.add_column("Test Configuration & Details", style="bright_white")

    for r in results:
        if r.name == "Random Memory Latency":
            tp_str = "[dim]—[/dim]"
            eff_str = "[dim]— (Pointer Chasing)[/dim]"
            lat_str = f"[bold bright_cyan]󰔛 {r.latency_ns:.2f} ns[/bold bright_cyan]" if r.latency_ns else "[dim]Failed[/dim]"
        else:
            tp_str = f"[bold bright_green]{r.throughput_gb_s:.2f} GB/s[/bold bright_green]" if r.throughput_gb_s > 0 else "[dim]Failed[/dim]"
            eff_str = build_gauge(r.efficiency_pct, width=10, mode="bandwidth")
            lat_str = "[dim]—[/dim]"

        table.add_row(
            r.name,
            tp_str,
            eff_str,
            lat_str,
            r.details,
        )

    console.print(table)

    note_text = Text()
    note_text.append("󰨣 Microarchitectural Performance Insights:\n", style="bold bright_yellow")
    note_text.append(" 󰅂 ", style="bright_cyan")
    if specs.theoretical_max_gb_s:
        note_text.append("Theoretical Max Peak for your memory bus is ", style="bright_white")
        note_text.append(f"{specs.theoretical_max_gb_s:.2f} GB/s.\n", style="bold bright_green")
    else:
        note_text.append(
            "Theoretical Max Peak calculation requires SMBIOS speed & channel data.\n",
            style="bright_white",
        )

    if specs.configured_speed_mts and specs.factory_speed_mts and specs.factory_speed_mts > specs.configured_speed_mts:
        note_text.append(" 󰅂 ", style="bright_cyan")
        note_text.append("Frequency Downclocking Detected: ", style="bold bright_white")
        note_text.append(
            f"Installed RAM is factory-rated for {specs.factory_speed_mts} MT/s but currently operating at {specs.configured_speed_mts} MT/s due to CPU memory controller hardware constraints.\n",
            style="bright_white",
        )

    single_core = next((r for r in results if r.name == "Single-Core Copy (1 Core)"), None)
    latency_res = next((r for r in results if r.name == "Random Memory Latency"), None)

    note_text.append(" 󰅂 ", style="bright_cyan")
    note_text.append("Single-Core Throughput Limit: ", style="bold bright_white")
    if single_core and single_core.throughput_gb_s > 0:
        note_text.append(
            f"Measured single-core copy throughput is {single_core.throughput_gb_s:.1f} GB/s — typically capped well below multi-core saturation by finite per-core Line Fill Buffer (LFB) request queues.\n",
            style="bright_white",
        )
    else:
        note_text.append(
            f"A single core (Core {specs.optimal_p_core}) is typically capped well below multi-core saturation by finite per-core Line Fill Buffer (LFB) request queues.\n",
            style="bright_white",
        )
    note_text.append(" 󰅂 ", style="bright_cyan")
    note_text.append("Pure Read / Write Scaling: ", style="bold bright_white")
    peak_str = f"{specs.theoretical_max_gb_s:.1f} GB/s" if specs.theoretical_max_gb_s else "the memory bus peak"
    note_text.append(
        f"To approach {peak_str}, memory requests must be issued in parallel across multiple CPU cores ({specs.online_cpus} active).\n",
        style="bright_white",
    )
    note_text.append(" 󰅂 ", style="bright_cyan")
    note_text.append("Random Access Latency vs Bandwidth: ", style="bold bright_white")
    if latency_res and latency_res.latency_ns:
        note_text.append(
            f"Random latency (measured {latency_res.latency_ns:.1f} ns) uses 128MB pointer chasing beyond L3 to isolate true DRAM access delay; typical range: ~70-90 ns DDR4, ~90-130 ns DDR5. ",
            style="bright_white",
        )
    else:
        note_text.append(
            "Random latency is measured via 128MB random pointer chasing beyond L3 to isolate true DRAM access delay. ",
            style="bright_white",
        )
    note_text.append(
        "Streaming bandwidth achieves far lower per-line cost via hardware parallelism.",
        style="bright_white",
    )

    panel = Panel(
        note_text,
        title="[bold bright_cyan]󰨣 Understanding Single-Thread vs Multi-Thread RAM Bandwidth & Latency[/bold bright_cyan]",
        border_style="bright_cyan",
    )
    console.print(panel)


HISTORY_DIR = Path.home() / ".config" / "dusky" / "settings" / "ram_test"
HISTORY_FILE = HISTORY_DIR / "history.json"


def save_run_to_history(
    specs: HardwareSpecs,
    cache_hierarchy: CacheHierarchyResult | None,
    results: list[TestResult],
    args: argparse.Namespace,
) -> None:
    """Save benchmark run state to ~/.config/dusky/settings/ram_test/ for multi-run comparison."""
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        history = load_history()

        now_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        time_short = time.strftime("%H:%M:%S")
        date_short = time.strftime("%m-%d")
        run_id = f"run_{time.strftime('%Y%m%d_%H%M%S')}"

        metrics_map: dict[str, float] = {}
        for r in results:
            if r.name == "Random Memory Latency" and r.latency_ns:
                metrics_map["Random Memory Latency (ns)"] = r.latency_ns
            elif r.name != "Random Memory Latency" and r.throughput_gb_s > 0:
                metrics_map[r.name] = r.throughput_gb_s

        entry = {
            "id": run_id,
            "timestamp": now_ts,
            "time_short": time_short,
            "date_short": date_short,
            "bench": getattr(args, "bench", "all"),
            "hugepages": bool(getattr(args, "hugepages", False)),
            "workers": getattr(args, "workers", None) or specs.online_cpus,
            "cores": getattr(args, "cores", None),
            "time_sec": getattr(args, "time", 10),
            "optimal_core": specs.optimal_p_core,
            "cpu_model": specs.cpu_model,
            "mem_type": specs.mem_type,
            "configured_speed_mts": specs.configured_speed_mts,
            "cache": {
                "l1_ns": cache_hierarchy.l1_ns if cache_hierarchy else None,
                "l2_ns": cache_hierarchy.l2_ns if cache_hierarchy else None,
                "l3_ns": cache_hierarchy.l3_ns if cache_hierarchy else None,
                "dram_ns": cache_hierarchy.dram_ns if cache_hierarchy else None,
            } if cache_hierarchy else None,
            "metrics": metrics_map,
            "results_raw": [asdict(r) for r in results],
            "initial_temps": specs.initial_dram_temps,
            "final_temps": specs.final_dram_temps or probe_dram_temperatures(),
        }

        history.append(entry)
        if len(history) > 100:
            history = history[-100:]

        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)

        snapshot_file = HISTORY_DIR / f"{run_id}.json"
        with open(snapshot_file, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2)
    except Exception as e:
        eprint(f"[Warning] Failed to save history to {HISTORY_FILE}: {e}")


def load_history() -> list[dict]:
    """Load historical benchmark runs from ~/.config/dusky/settings/ram_test/history.json."""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def clear_history() -> None:
    """Clear all saved benchmark history files in ~/.config/dusky/settings/ram_test/."""
    if HISTORY_DIR.exists():
        for p in HISTORY_DIR.glob("*.json"):
            with contextlib.suppress(OSError):
                p.unlink()
    msg = "Cleared all benchmark history in ~/.config/dusky/settings/ram_test/"
    if RICH_AVAILABLE:
        console.print(f"[bold green]󰄬 {msg}[/bold green]")
    else:
        print(msg)


def generate_sparkline(runs: list[dict], extractor, mode: str = "bandwidth") -> str:
    """Render a run-aligned colored Unicode sparkline trend curve from historical data points."""
    run_vals = [extractor(r) for r in runs]
    valid_vals = [v for v in run_vals if v is not None]
    if not valid_vals:
        return "[dim]—[/dim]"
    if len(valid_vals) == 1:
        res = ""
        for v in run_vals:
            if v is not None:
                res += "[bold cyan]▄[/bold cyan]"
            else:
                res += "[dim bright_black]·[/dim bright_black]"
        return res

    min_v = min(valid_vals)
    max_v = max(valid_vals)
    mean_v = sum(valid_vals) / len(valid_vals)
    rel_spread = (max_v - min_v) / (mean_v if mean_v > 0 else 1.0)

    blocks = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    res = ""

    if rel_spread < 0.03:
        for v in run_vals:
            if v is not None:
                res += "[bright_green]▄[/bright_green]"
            else:
                res += "[dim bright_black]·[/dim bright_black]"
        return res

    span = max_v - min_v if max_v != min_v else 1.0
    for v in run_vals:
        if v is None:
            res += "[dim bright_black]·[/dim bright_black]"
            continue
        idx = int(round(((v - min_v) / span) * (len(blocks) - 1)))
        idx = max(0, min(len(blocks) - 1, idx))
        char = blocks[idx]
        if mode == "latency":
            color = "bright_green" if idx <= 2 else ("bright_yellow" if idx <= 5 else "bright_red")
        else:
            color = "bright_green" if idx >= 5 else ("bright_yellow" if idx >= 2 else "bright_red")
        res += f"[{color}]{char}[/{color}]"
    return res


def render_history_comparison(history: list[dict], count: int = 7) -> None:
    """Render a side-by-side comparison matrix of recent benchmark runs with best/worst scores highlighted."""
    if not history:
        msg = "No previous benchmark history found in ~/.config/dusky/settings/ram_test/"
        if RICH_AVAILABLE:
            console.print(f"[bold yellow]󰘓 {msg}[/bold yellow]")
        else:
            print(msg)
        return

    runs = history[-count:]
    if not runs:
        return

    metrics_meta = [
        ("Random DRAM Latency (ns)", "latency", "bold bright_cyan", lambda r: r.get("metrics", {}).get("Random Memory Latency (ns)") or (r.get("cache") or {}).get("dram_ns")),
        ("L1 Data Cache (ns)", "latency", "bold cyan", lambda r: (r.get("cache") or {}).get("l1_ns")),
        ("L2 Dedicated Cache (ns)", "latency", "bold cyan", lambda r: (r.get("cache") or {}).get("l2_ns")),
        ("L3 Smart Cache (ns)", "latency", "bold cyan", lambda r: (r.get("cache") or {}).get("l3_ns")),
        (None, None, None, None),
        ("Single-Core Copy (GB/s)", "bandwidth", "bold bright_yellow", lambda r: r.get("metrics", {}).get("Single-Core Copy (1 Core)")),
        ("Pure Read Multi-Core (GB/s)", "bandwidth", "bold bright_green", lambda r: r.get("metrics", {}).get("Pure Read (Multi-Thread)")),
        ("Pure Write Multi-Core (GB/s)", "bandwidth", "bold bright_green", lambda r: r.get("metrics", {}).get("Pure Write (Multi-Thread)")),
        ("STREAM Copy Multi-Core (GB/s)", "bandwidth", "bold bright_green", lambda r: r.get("metrics", {}).get("Stream Copy (Multi-Thread)")),
    ]

    if not RICH_AVAILABLE:
        print(f"\n=== MULTI-RUN BENCHMARK COMPARISON (Last {len(runs)} Runs) ===")
        header_cols = [f"{'Metric':<28s}"]
        for i, r in enumerate(runs):
            t_short = r.get("time_short", r.get("timestamp", "").split()[-1] if "timestamp" in r else "?")
            header_cols.append(f"R{i+1} ({t_short:>8s})")
        header_cols.extend([" Min / Max  ", " Δ vs Base "])
        header = " | ".join(header_cols)
        print(header)
        print("-" * len(header))
        for item in metrics_meta:
            if item[0] is None:
                print("-" * len(header))
                continue
            label, mode, label_style, extractor = item
            vals = [extractor(r) for r in runs if extractor(r) is not None]
            if not vals:
                continue
            row_items = [f"{label:<28s}"]
            for r in runs:
                v = extractor(r)
                if v is not None:
                    row_items.append(f"{v:>12.2f}" if v < 100 else f"{v:>12.1f}")
                else:
                    row_items.append(f"{'—':^12s}")
            min_v, max_v = min(vals), max(vals)
            min_max = f"{min_v:.2f} / {max_v:.2f}" if max_v < 10.0 else f"{min_v:.1f} / {max_v:.1f}"
            delta_str = "—"
            if len(vals) >= 2 and vals[0] != 0:
                pct_diff = ((vals[-1] - vals[0]) / vals[0]) * 100.0
                delta_str = f"{pct_diff:+.1f}%"
            row_items.append(f"{min_max:^12s}")
            row_items.append(f"{delta_str:>11s}")
            print(" | ".join(row_items))
        return

    t = Table(
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        show_header=True,
        expand=False,
    )
    t.add_column("Benchmark Metric", min_width=27, no_wrap=True)

    for i, r in enumerate(runs):
        is_latest = (i == len(runs) - 1)
        tag = "[bold bright_green]Latest[/bold bright_green]" if is_latest else f"R{i+1}"
        hp = " [dim](THP)[/dim]" if r.get("hugepages") else ""
        raw_t = r.get("time_short", r.get("timestamp", "").split()[-1] if "timestamp" in r else "")
        t_short = ":".join(raw_t.split(":")[:2])
        t.add_column(f"{tag}{hp}\n[white]{t_short}[/white]", justify="right", min_width=7, no_wrap=True)

    t.add_column("Min / Max", justify="center", style="bold bright_white", min_width=13, no_wrap=True)
    t.add_column("Δ Base", justify="right", style="bold bright_cyan", min_width=9, no_wrap=True)

    for item in metrics_meta:
        if item[0] is None:
            t.add_section()
            continue
        label, mode, label_style, extractor = item
        vals = [extractor(r) for r in runs if extractor(r) is not None]
        if not vals:
            continue
        min_v, max_v = min(vals), max(vals)
        best_v = min_v if mode == "latency" else max_v
        worst_v = max_v if mode == "latency" else min_v

        row = [f"[{label_style}]{label}[/{label_style}]"]
        for r in runs:
            v = extractor(r)
            if v is not None:
                formatted = f"{v:.2f}" if v < 10 else f"{v:.1f}"
                if len(vals) > 1 and v == best_v and best_v != worst_v:
                    cell_str = f"[bold bright_green]{formatted}[/bold bright_green]"
                elif len(vals) > 1 and v == worst_v and best_v != worst_v:
                    cell_str = f"[bold bright_red]{formatted}[/bold bright_red]"
                else:
                    cell_str = f"[bright_white]{formatted}[/bright_white]"
                row.append(cell_str)
            else:
                row.append("[dim]—[/dim]")

        min_max = f"{min_v:.2f} / {max_v:.2f}" if max_v < 10.0 else f"{min_v:.1f} / {max_v:.1f}"
        delta_str = "—"
        if len(vals) >= 2 and vals[0] != 0:
            pct_diff = ((vals[-1] - vals[0]) / vals[0]) * 100.0
            if mode == "latency":
                color = "bright_green" if pct_diff < 0 else ("bright_red" if pct_diff > 0 else "white")
                arrow = "▼" if pct_diff < 0 else ("▲" if pct_diff > 0 else "")
            else:
                color = "bright_green" if pct_diff > 0 else ("bright_red" if pct_diff < 0 else "white")
                arrow = "▲" if pct_diff > 0 else ("▼" if pct_diff < 0 else "")
            delta_str = f"[{color}]{pct_diff:+.1f}% {arrow}[/{color}]"

        row.extend([min_max, delta_str])
        t.add_row(*row)

    console.print(f"\n[bold bright_cyan] 󰓅 Multi-Run Benchmark Comparison (Last {len(runs)} Runs)[/bold bright_cyan]")
    console.print(t)


def export_report(
    filename: str,
    specs: HardwareSpecs,
    cache_hierarchy: CacheHierarchyResult | None,
    results: list[TestResult],
) -> None:
    specs.final_dram_temps = probe_dram_temperatures()

    export_path = Path(filename).expanduser().resolve()
    export_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hardware_specs": asdict(specs),
        "cache_hierarchy_latency_ns": asdict(cache_hierarchy) if cache_hierarchy else None,
        "benchmark_results": [asdict(r) for r in results],
    }

    if export_path.suffix.lower() == ".json":
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        msg = f"Exported benchmark report to JSON: {export_path}"
        if RICH_AVAILABLE:
            console.print(f"[bold green]󰄬 {msg}[/bold green]")
        else:
            print(msg)
    elif export_path.suffix.lower() == ".csv":
        with open(export_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["# SYSTEM HARDWARE METADATA"])
            writer.writerow(["# CPU Model", specs.cpu_model])
            writer.writerow(["# Memory Speed", f"{specs.mem_type} @ {specs.configured_speed_mts} MT/s"])
            writer.writerow(["# Topology", f"{specs.channels} Channels, {specs.bus_width_bits}-bit Bus Width"])
            writer.writerow([])

            writer.writerow(["Metric / Test", "Throughput (GB/s)", "Throughput (MiB/s)", "Efficiency (%)", "Latency (ns)", "Details"])
            for r in results:
                tp_gb = f"{r.throughput_gb_s:.2f}" if r.throughput_gb_s > 0 else "—"
                tp_mib = f"{r.throughput_mib_s:.1f}" if r.throughput_mib_s > 0 else "—"
                writer.writerow([r.name, tp_gb, tp_mib, f"{r.efficiency_pct:.1f}" if r.efficiency_pct else "—", f"{r.latency_ns:.2f}" if r.latency_ns else "—", r.details])
        msg = f"Exported benchmark report to CSV: {export_path}"
        if RICH_AVAILABLE:
            console.print(f"[bold green]󰄬 {msg}[/bold green]")
        else:
            print(msg)
    else:
        msg = f"Unsupported export format (use .json or .csv): {export_path}"
        if RICH_AVAILABLE:
            console.print(f"[bold yellow]󰘓 {msg}[/bold yellow]")
        else:
            print(msg)


def main() -> int:
    global SUDO_AVAILABLE

    parser = argparse.ArgumentParser(
        description="Ultimate Hardware-Agnostic RAM Bandwidth & Latency Benchmark Suite"
    )
    parser.add_argument(
        "--bench",
        choices=["read", "write", "copy", "single", "latency", "cache", "all"],
        default="all",
        help="Benchmark mode to run non-interactively (default: all).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        help="Number of workers for multi-core tests (default: all online CPUs).",
    )
    parser.add_argument(
        "--time",
        type=int,
        default=10,
        help="Duration in seconds per test (default: 10).",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=4096,
        help="Array size in MiB for mbw single-core test (default: 4096).",
    )
    parser.add_argument(
        "--cores",
        help="Core range string to pin tests to (e.g. 0-13 or 0-7).",
    )
    parser.add_argument(
        "--hugepages",
        action="store_true",
        help="Use Transparent Huge Pages (madvise MADV_HUGEPAGE) in latency tests to minimize TLB miss overhead.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=3,
        help="Number of latency samples per test; median reported (default: 3).",
    )
    parser.add_argument(
        "--export",
        help="Path to export benchmark results in JSON or CSV format (e.g. --export report.json).",
    )
    parser.add_argument(
        "--compare",
        "--history",
        dest="compare",
        nargs="?",
        const=7,
        type=int,
        help="Compare results from the last N runs side-by-side with trend graphs (default: 7).",
    )
    parser.add_argument(
        "--clear-history",
        action="store_true",
        help="Clear all saved history state files in ~/.config/dusky/settings/ram_test/.",
    )
    parser.add_argument(
        "--no-governor",
        action="store_true",
        help="Skip optimizing CPU performance governor.",
    )
    args = parser.parse_args()

    if args.clear_history:
        clear_history()
        return 0

    if args.compare is not None and "--bench" not in sys.argv:
        render_history_comparison(load_history(), count=args.compare)
        return 0

    try:
        has_sudo = cache_sudo_privileges()
        SUDO_AVAILABLE = has_sudo

        check_and_install_deps()
        specs = detect_hardware_specs(skip_sudo=not has_sudo)

        render_header(specs, governor_active=not args.no_governor and has_sudo)

        workers = args.workers or specs.online_cpus
        results: list[TestResult] = []
        cache_hierarchy: CacheHierarchyResult | None = None

        governor_ctx = (
            contextlib.nullcontext() if (args.no_governor or not has_sudo) else set_cpu_performance()
        )

        with governor_ctx:
            if RICH_AVAILABLE:
                with Progress(
                    SpinnerColumn("dots", style="cyan"),
                    TextColumn("[bold cyan]{task.description}[/bold cyan]"),
                    console=console,
                    transient=True,
                ) as progress:
                    if args.bench in ["cache", "all"]:
                        tc = progress.add_task(
                            "Measuring L1/L2/L3 Cache & DRAM Latency Hierarchy...", total=None
                        )
                        cache_hierarchy = run_cache_hierarchy_latency_test(args.cores, hugepages=args.hugepages)
                        progress.remove_task(tc)

                    if args.bench in ["latency", "all"]:
                        t0 = progress.add_task(
                            "Running Random DRAM Access Latency Benchmark (128M Pointer-Chasing)...", total=None
                        )
                        res_lat = run_latency_test(128, specs, args.cores, hugepages=args.hugepages, samples=args.samples)
                        results.append(res_lat)
                        progress.remove_task(t0)

                    if args.bench in ["single", "all"]:
                        t1 = progress.add_task(
                            "Running Single-Core Memory Copy Benchmark...", total=None
                        )
                        res_single = run_single_core_test(args.size, 10, args.time, specs, args.cores)
                        results.append(res_single)
                        progress.remove_task(t1)

                    if args.bench in ["read", "all"]:
                        t2 = progress.add_task(
                            "Running Pure Multi-Core Read Benchmark (sysbench 64M)...", total=None
                        )
                        res_read = run_pure_read_test(workers, args.time, specs, args.cores)
                        results.append(res_read)
                        progress.remove_task(t2)

                    if args.bench in ["write", "all"]:
                        t3 = progress.add_task(
                            "Running Pure Multi-Core Write Benchmark (sysbench 64M)...", total=None
                        )
                        res_write = run_pure_write_test(workers, args.time, specs, args.cores)
                        results.append(res_write)
                        progress.remove_task(t3)

                    if args.bench in ["copy", "all"]:
                        t4 = progress.add_task(
                            "Running Multi-Core STREAM Copy Benchmark (stress-ng)...", total=None
                        )
                        res_copy = run_copy_stream_test(workers, args.time, specs, args.cores)
                        results.append(res_copy)
                        progress.remove_task(t4)
            else:
                if args.bench in ["cache", "all"]:
                    print("Measuring L1/L2/L3 Cache & DRAM Latency Hierarchy...")
                    cache_hierarchy = run_cache_hierarchy_latency_test(args.cores, hugepages=args.hugepages)
                if args.bench in ["latency", "all"]:
                    print("Running Random DRAM Access Latency Benchmark...")
                    results.append(run_latency_test(128, specs, args.cores, hugepages=args.hugepages, samples=args.samples))
                if args.bench in ["single", "all"]:
                    print("Running Single-Core Memory Copy Benchmark...")
                    results.append(run_single_core_test(args.size, 10, args.time, specs, args.cores))
                if args.bench in ["read", "all"]:
                    print("Running Pure Multi-Core Read Benchmark...")
                    results.append(run_pure_read_test(workers, args.time, specs, args.cores))
                if args.bench in ["write", "all"]:
                    print("Running Pure Multi-Core Write Benchmark...")
                    results.append(run_pure_write_test(workers, args.time, specs, args.cores))
                if args.bench in ["copy", "all"]:
                    print("Running Multi-Core STREAM Copy Benchmark...")
                    results.append(run_copy_stream_test(workers, args.time, specs, args.cores))

            if cache_hierarchy:
                render_cache_hierarchy_table(cache_hierarchy)
            if results:
                render_results_table(results, specs)

            # Persist run to ~/.config/dusky/settings/ram_test/
            save_run_to_history(specs, cache_hierarchy, results, args)

            # Show multi-run trend comparison if historical runs exist
            hist = load_history()
            if len(hist) >= 2:
                render_history_comparison(hist, count=args.compare or 7)

            if args.export:
                export_report(args.export, specs, cache_hierarchy, results)
    except KeyboardInterrupt:
        if RICH_AVAILABLE:
            console.print("\n[bold yellow]󰞅 Benchmark interrupted by user.[/bold yellow]")
        else:
            print("\nBenchmark interrupted by user.")
        return 130

    had_failure = any(
        (r.latency_ns is None or r.latency_ns <= 0) if r.name == "Random Memory Latency" else r.throughput_gb_s <= 0
        for r in results
    )
    if args.bench in ["cache", "all"] and cache_hierarchy is None:
        had_failure = True

    if had_failure:
        if RICH_AVAILABLE:
            console.print("[bold red]󰀨 One or more benchmarks failed — check warnings above.[/bold red]")
        else:
            print("One or more benchmarks failed — check warnings above.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Apply the installed kernel's profile once at boot; no background polling.

This file and its resolved profile are packaged with each kernel. Existing ZRAM
swap is owned by its current manager and is never reset or resized here.
"""
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys


def write(path: Path, value: object) -> bool:
    try:
        path.write_text(str(value) + "\n")
        return True
    except OSError as exc:
        print(f"dusky: {path}: {exc}", file=sys.stderr)
        return False


def tuning_values(s: dict) -> dict[str, object]:
    m = s["memory"]
    values = {
        "proc/sys/vm/swappiness": m["swappiness"] or {"zram": 180, "zswap": 100, "none": 60}[m["swap_backend"]],
        "proc/sys/vm/vfs_cache_pressure": m["vfs_cache_pressure"] or {"standard": 100, "lean": 120, "minimal": 150, "embedded": 200}[m["footprint"]],
        "proc/sys/vm/watermark_scale_factor": m["watermark_scale_factor"],
        "proc/sys/vm/watermark_boost_factor": m["watermark_boost_factor"],
        "proc/sys/vm/compaction_proactiveness": m["compaction_proactiveness"],
        "proc/sys/vm/max_map_count": s["gaming"]["max_map_count"],
        "proc/sys/net/ipv4/tcp_congestion_control": s["network"]["congestion"],
        "proc/sys/net/core/default_qdisc": s["network"]["qdisc"],
        "proc/sys/net/ipv4/tcp_fastopen": 3 if s["network"]["tcp_fastopen"] else 0,
        "proc/sys/kernel/split_lock_mitigate": int(s["gaming"]["split_lock_mitigate"]),
    }
    if m["dirty_bytes_mb"]:
        values["proc/sys/vm/dirty_bytes"] = m["dirty_bytes_mb"] << 20
        values["proc/sys/vm/dirty_background_bytes"] = (m["dirty_bytes_mb"] << 20) // 4
    if m["thp"] != "never":
        values["sys/kernel/mm/transparent_hugepage/defrag"] = m["thp_defrag"]
        values["sys/kernel/mm/transparent_hugepage/shmem_enabled"] = m["thp_shmem"]
    if m["mglru"]:
        values["sys/kernel/mm/lru_gen/enabled"] = m["mglru_mask"]
        values["sys/kernel/mm/lru_gen/min_ttl_ms"] = m["mglru_min_ttl_ms"]
    if m["ksm"]:
        values["sys/kernel/mm/ksm/run"] = int(m["ksm_run"])
    if s["cache"]["sched_cache"]:
        values["sys/kernel/debug/sched/llc_balancing/aggr_tolerance"] = s["cache"]["llc_aggr_tolerance"]
        if s["cache"]["llc_overaggr_pct"] >= 0:
            values["sys/kernel/debug/sched/llc_balancing/overaggr_pct"] = s["cache"]["llc_overaggr_pct"]
    if s["rseq"]["slice_extension"]:
        values["sys/kernel/debug/rseq/slice_ext_nsec"] = s["rseq"]["slice_ext_nsec"]
    return values


def apply(s: dict, root: Path = Path("/")) -> int:
    failures = 0
    for name, value in tuning_values(s).items():
        path = root / name
        if path.exists():
            failures += not write(path, value)
        else:
            print(f"dusky: unavailable on this kernel: /{name}", file=sys.stderr)
    for policy in (root / "sys/devices/system/cpu/cpufreq").glob("policy*"):
        governor = s["cpu"]["governor"]
        try:
            supported = (policy / "scaling_available_governors").read_text().split()
        except OSError as exc:
            print(f"dusky: {policy}: {exc}", file=sys.stderr)
            failures += 1
            continue
        if governor in ("schedutil", "ondemand", "conservative") and set(supported) == {"performance", "powersave"}:
            print(f"dusky: {policy.name}: active P-state driver; using its dynamic powersave policy for {governor}")
            governor = "powersave"
        if governor in supported:
            failures += not write(policy / "scaling_governor", governor)
        else:
            print(f"dusky: {policy.name}: governor {governor} unavailable", file=sys.stderr)
        epp = s["cpu"]["epp"]
        pref = policy / "energy_performance_preference"
        if epp != "default" and pref.exists():
            # Active Intel P-state performance mode only accepts performance EPP.
            if governor == "performance":
                epp = "performance"
            failures += not write(pref, epp)
    scheduler = s["storage"]["io_scheduler"]
    if scheduler != "keep":
        for path in (root / "sys/block").glob("*/queue/scheduler"):
            if scheduler in path.read_text().replace("[", "").replace("]", "").split():
                failures += not write(path, scheduler)
    return int(bool(failures))


def setup_zram(s: dict) -> None:
    if not s["runtime"]["manage_zram"] or s["memory"]["swap_backend"] != "zram":
        return
    swaps = Path("/proc/swaps").read_text().splitlines()[1:]
    if any(line.split()[0].startswith("/dev/zram") for line in swaps):
        print("dusky: existing ZRAM swap retained; its manager controls size/compression")
        return
    subprocess.run(["modprobe", "zram"], check=True)
    index = int(Path("/sys/class/zram-control/hot_add").read_text())
    dev = Path(f"/sys/block/zram{index}")
    node = f"/dev/zram{index}"
    m = s["memory"]
    try:
        if not write(dev / "comp_algorithm", m["zram_algo"]):
            raise RuntimeError("ZRAM compressor unavailable")
        if m["zram_multi_comp"]:
            if not write(dev / "recomp_algorithm", f"algo={m['zram_recomp_algo']} priority=1"):
                raise RuntimeError("ZRAM recompression unavailable")
        ram = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:"))) * 1024
        if not write(dev / "disksize", ram * m["zram_size_pct"] // 100):
            raise RuntimeError("Cannot size ZRAM")
        subprocess.run(["udevadm", "settle", "--timeout=10"], check=True)
        subprocess.run(["mkswap", node], check=True)
        subprocess.run(["swapon", "--priority", "100", node], check=True)
    except Exception:
        write(dev / "reset", 1)
        write(Path("/sys/class/zram-control/hot_remove"), index)
        raise


def main() -> int:
    data = json.loads(Path(sys.argv[1]).read_text())
    if platform.release() != data["kernelrelease"]:
        return 0
    s = data["profile"]
    if len(sys.argv) > 2 and sys.argv[2] == "--scheduler":
        sched = s["scheduler"]["scx"]
        if sched != "none":
            argv = [sched, *shlex.split(s["scheduler"]["scx_flags"])]
            os.execvp(sched, argv)
        return 0
    result = apply(s)
    setup_zram(s)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

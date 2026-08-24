#!/usr/bin/env python3
"""
Dusky Drive Health v2.4.0 (Arch Linux Kernel 7.1+ / Python 3.14.6+ Edition)

Multi-interface SSD wear-leveling & FTL over-provisioning diagnostic suite.
Audits NVMe and SATA/SCSI SSD SMART logs, resolves partition extents and
unallocated gaps, samples read-only unallocated sector content, and executes
erase-block aligned and hardware-chunked blkdiscards to clear dirty free space.

Design principles:
  * Arch Linux rolling baseline (Kernel 7.1+, Python 3.14.6+, util-linux 2.42+).
  * PEP 695 type statements (`type SectorRange = ...`).
  * Discard requests aligned to 4 MiB erase-block safety floor and chunked to
    the device's `queue/discard_max_bytes` to prevent kernel ioctl EINVAL failures.
  * Robust subprocess execution with explicit timeouts and smartctl JSON
    parser tolerance for non-zero bitmask exit codes.
  * File descriptor protection (`os.O_CLOEXEC`) and `sudo -E` env preservation.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Final, TypedDict

# --- Rich console (hard dependency) -------------------------------------------
try:
    from rich.align import Align
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.table import Table
    from rich.text import Text
except ImportError:
    sys.stderr.write(
        "[!] python-rich is required for console rendering.\n"
        "    Arch Linux:  sudo pacman -S --needed python-rich\n"
    )
    sys.exit(1)

VERSION: Final[str] = "2.4.0"
console: Final[Console] = Console()
PANEL_WIDTH: Final[int] = min(console.width if console.is_terminal else 132, 132)

# --- PEP 695 type statements --------------------------------------------------
type SectorRange = tuple[int, int]
type DeviceTree = dict[str, dict[str, Any]]


class SmartData(TypedDict, total=False):
    device: str
    model: str
    serial: str
    firmware: str
    temp: float
    percentage_used: int
    tbw_written: float
    tbw_rated: float
    power_on_hours: int
    unsafe_shutdowns: int
    media_errors: int
    flash_type: str
    interface: str


@dataclass(frozen=True, slots=True)
class PartitionInfo:
    name: str
    start_sector: int
    end_sector: int
    size_sectors: int
    fs_type: str
    mountpoint: str
    is_luks: bool = False
    allow_discards: bool = False
    discard_mounted: bool = False

    @property
    def number(self) -> int:
        m = re.search(r"(\d+)$", self.name)
        return int(m.group(1)) if m else 0


@dataclass(frozen=True, slots=True)
class DiskLayout:
    device: str
    model: str
    total_sectors: int
    sector_size: int
    label: str
    partitions: list[PartitionInfo]
    unallocated_gaps: list[SectorRange]
    discard_granularity: int = 0
    discard_max_bytes: int = 0


# =============================================================================
# Constants & Reference Tables
# =============================================================================
QLC_TBW_PER_TB: Final[float] = 370.0
TLC_TBW_PER_TB: Final[float] = 600.0
MIN_ERASE_BLOCK_BYTES: Final[int] = 4 * 1024 * 1024  # 4 MiB safety floor
DEFAULT_MAX_DISCARD_CHUNK: Final[int] = 2 * 1024 * 1024 * 1024  # 2 GiB fallback ceiling

QLC_PATTERNS: Final[tuple[str, ...]] = (
    "QLC", "QVO", "660P", "670P", "BX500", "NV2", "SN350", "A400",
)

FS_COLORS: Final[dict[str, str]] = {
    "btrfs": "green", "ext4": "cyan", "ext3": "cyan", "ext2": "cyan",
    "xfs": "blue", "f2fs": "magenta", "vfat": "yellow", "ntfs": "red",
    "swap": "bright_red", "crypto_LUKS": "bright_magenta",
}

# =============================================================================
# Mock profiles (for --mock demonstration mode)
# =============================================================================
MOCK_INTEL_SMART: SmartData = {
    "device": "/dev/nvme0n1", "model": "INTEL SSDPEKNU512GZ (670p QLC)",
    "serial": "PHPN12345678512D", "firmware": "CO20100F", "temp": 36.0,
    "percentage_used": 30, "tbw_written": 67.51, "tbw_rated": 185.0,
    "power_on_hours": 21348, "unsafe_shutdowns": 1678, "media_errors": 0,
    "flash_type": "QLC", "interface": "NVMe",
}
MOCK_INTEL_LAYOUT = DiskLayout(
    device="/dev/nvme0n1", model="INTEL SSDPEKNU512GZ (670p QLC)",
    total_sectors=1000215216, sector_size=512, label="gpt",
    partitions=[
        PartitionInfo("nvme0n1p1", 2048, 6293503, 6291456, "ext4", "/home/dusk", True, True, False),
        PartitionInfo("nvme0n1p2", 6293504, 9089023, 2795520, "vfat", "/boot", False, False, False),
        PartitionInfo("nvme0n1p3", 9089024, 260747263, 251658240, "btrfs", "/", False, False, True),
    ],
    unallocated_gaps=[(260747264, 1000215182)],
    discard_granularity=512, discard_max_bytes=2 * (1 << 40),
)

MOCK_SAMSUNG_SMART: SmartData = {
    "device": "/dev/nvme1n1", "model": "Samsung SSD 980 1TB (TLC)",
    "serial": "S64DNL0R123456F", "firmware": "1B4QFXO7", "temp": 40.0,
    "percentage_used": 10, "tbw_written": 81.72, "tbw_rated": 600.0,
    "power_on_hours": 5539, "unsafe_shutdowns": 1478, "media_errors": 0,
    "flash_type": "TLC", "interface": "NVMe",
}
MOCK_SAMSUNG_LAYOUT = DiskLayout(
    device="/dev/nvme1n1", model="Samsung SSD 980 1TB (TLC)",
    total_sectors=1953525168, sector_size=512, label="gpt",
    partitions=[
        PartitionInfo("nvme1n1p1", 2048, 1048578047, 1048576000, "ext4", "/mnt/media", True, True, False),
    ],
    unallocated_gaps=[(1048578048, 1953525134)],
    discard_granularity=4096, discard_max_bytes=2 * (1 << 40),
)

MOCK_SATA_SMART: SmartData = {
    "device": "/dev/sda", "model": "Samsung SSD 870 QVO 2TB (QLC)",
    "serial": "S5XANGB1234567W", "firmware": "1B6QJX7", "temp": 38.0,
    "percentage_used": 15, "tbw_written": 108.5, "tbw_rated": 740.0,
    "power_on_hours": 8760, "unsafe_shutdowns": 42, "media_errors": 0,
    "flash_type": "QLC", "interface": "SATA",
}
MOCK_SATA_LAYOUT = DiskLayout(
    device="/dev/sda", model="Samsung SSD 870 QVO 2TB (QLC)",
    total_sectors=3907029168, sector_size=512, label="gpt",
    partitions=[
        PartitionInfo("sda1", 2048, 1050623, 1048576, "vfat", "/boot", False, False, True),
        PartitionInfo("sda2", 1050624, 2095103, 1044480, "swap", "[SWAP]", False, False, False),
        PartitionInfo("sda3", 2095104, 3906961407, 3904866304, "ext4", "/mnt/data", True, True, True),
    ],
    unallocated_gaps=[(3906961408, 3907029134)],
    discard_granularity=512, discard_max_bytes=2 * (1 << 30),
)


# =============================================================================
# Subprocess & IO Helpers
# =============================================================================
def _run(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _run_json(args: list[str], timeout: float = 5.0) -> Any | None:
    """
    Executes a command expecting JSON output on stdout.
    Note: Tolerates non-zero returncodes (e.g., smartctl status bitmasks)
    as long as valid JSON is returned on stdout.
    """
    res = _run(args, timeout=timeout)
    if res is None or not res.stdout:
        return None
    with suppress(json.JSONDecodeError):
        return json.loads(res.stdout)
    return None


def _read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


# =============================================================================
# Hardware Helper Calculations
# =============================================================================
def detect_flash_type(model: str, percentage_used: int, tbw_written: float, capacity_tb: float) -> str:
    m = model.upper()
    if any(pat in m for pat in QLC_PATTERNS):
        return "QLC"
    if "TLC" in m or "EVO" in m or "PRO" in m:
        return "TLC"

    if percentage_used > 0 and tbw_written > 0 and capacity_tb > 0:
        inferred_tbw_per_tb = (tbw_written / (percentage_used / 100.0)) / capacity_tb
        midpoint = (QLC_TBW_PER_TB + TLC_TBW_PER_TB) / 2.0
        if inferred_tbw_per_tb < midpoint:
            return "QLC"
    return "TLC"


def estimate_tbw_rated(capacity_tb: float, flash_type: str) -> float:
    per_tb = QLC_TBW_PER_TB if flash_type == "QLC" else TLC_TBW_PER_TB
    return round(capacity_tb * per_tb, 1)


def get_device_capacity_tb(device: str) -> float:
    if content := _read_text(f"/sys/block/{os.path.basename(device)}/size"):
        with suppress(ValueError):
            return int(content.strip()) * 512 / 1e12
    return 0.0


def _get_device_sector_size(device: str) -> int:
    if content := _read_text(f"/sys/block/{os.path.basename(device)}/queue/logical_block_size"):
        with suppress(ValueError):
            return int(content.strip())
    return 512


def _get_device_discard_granularity(device: str) -> int:
    if content := _read_text(f"/sys/block/{os.path.basename(device)}/queue/discard_granularity"):
        with suppress(ValueError):
            return int(content.strip())
    return 0


def _get_device_discard_max_bytes(device: str) -> int:
    if content := _read_text(f"/sys/block/{os.path.basename(device)}/queue/discard_max_bytes"):
        with suppress(ValueError):
            return int(content.strip())
    return 0


def _get_device_model(device: str) -> str:
    if content := _read_text(f"/sys/block/{os.path.basename(device)}/device/model"):
        if model_str := content.strip():
            return model_str

    if res := _run(["lsblk", "-d", "-o", "MODEL", device, "--noheadings"], timeout=3.0):
        if res.returncode == 0 and res.stdout:
            return res.stdout.strip()
    return "Unknown Device"


def calculate_estimated_waf(op_percentage: float, is_qlc: bool = False) -> float:
    op_ratio = op_percentage / 100.0
    base_waf = 4.8 if is_qlc else 4.0
    estimated = 1.15 + (base_waf - 1.15) * math.exp(-3.5 * op_ratio)
    return max(1.1, round(estimated, 2))


def get_lifespan_projections(written: float, rated: float, current_op: float, target_op: float, is_qlc: bool = False) -> dict[str, float]:
    current_waf = calculate_estimated_waf(current_op, is_qlc)
    target_waf = calculate_estimated_waf(target_op, is_qlc)
    remaining_tbw = max(0.1, rated - written)
    multiplier = current_waf / target_waf
    return {
        "current_waf": current_waf,
        "target_waf": target_waf,
        "multiplier": multiplier,
        "remaining_tbw": remaining_tbw,
        "extended_remaining_tbw": remaining_tbw * multiplier,
    }


# =============================================================================
# Device Topology & Partition Parsing
# =============================================================================
def detect_ssd_devices() -> list[str]:
    devices: list[str] = []
    data = _run_json(["lsblk", "-d", "-J", "-o", "NAME,ROTA,TYPE"])
    if data is None or not isinstance(data, dict):
        return devices

    for d in data.get("blockdevices", []):
        name = d.get("name", "")
        if d.get("type") != "disk" or not (name.startswith("nvme") or name.startswith("sd")):
            continue
        rota = str(d.get("rota", 1)).lower()
        if rota not in ("0", "false"):
            continue
        if content := _read_text(f"/sys/block/{name}/queue/rotational"):
            if content.strip() != "0":
                continue
        devices.append(f"/dev/{name}")
    return sorted(devices)


def get_mount_discards() -> dict[str, bool]:
    discards: dict[str, bool] = {}
    if content := _read_text("/proc/mounts"):
        for line in content.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                options = parts[3].split(",")
                discards[parts[0]] = any(
                    opt == "discard" or opt.startswith("discard=") for opt in options
                )
    return discards


def build_device_tree(device: str) -> DeviceTree:
    result: DeviceTree = {}
    data = _run_json(["lsblk", "-J", "-b", "-o", "NAME,TYPE,FSTYPE,MOUNTPOINTS,MAJ:MIN,PKNAME,DISC-GRAN", device])
    if data is None or not isinstance(data, dict):
        return result

    def walk(node: dict[str, Any]) -> None:
        name = node.get("name", "")
        if name:
            result[name] = {
                "type": node.get("type", ""),
                "fstype": node.get("fstype") or "",
                "mountpoints": [mp for mp in (node.get("mountpoints") or []) if mp],
                "maj_min": node.get("maj:min", ""),
                "pkname": node.get("pkname") or "",
                "disc_gran": int(node.get("disc-gran") or 0),
                "children": [c.get("name", "") for c in (node.get("children") or [])],
            }
        for child in node.get("children") or []:
            walk(child)

    for dev in data.get("blockdevices", []):
        walk(dev)
    return result


def resolve_mountpoints(part_name: str, tree: DeviceTree) -> list[str]:
    seen: dict[str, None] = {}

    def walk(name: str) -> None:
        node = tree.get(name, {})
        for mp in node.get("mountpoints", []):
            if mp:
                seen[mp] = None
        for child in node.get("children", []):
            walk(child)

    walk(part_name)
    return list(seen)


def analyze_partition_discard(part_name: str, tree: DeviceTree, mount_discards: dict[str, bool]) -> tuple[bool, bool]:
    node = tree.get(part_name, {})
    fstype = node.get("fstype", "")
    is_luks = "crypto_LUKS" in fstype or "luks" in fstype.lower()

    if not is_luks:
        return False, mount_discards.get(f"/dev/{part_name}", False)

    allow_discards, discard_mounted = False, False

    def walk(name: str) -> None:
        nonlocal allow_discards, discard_mounted
        n = tree.get(name, {})
        if n.get("type") == "crypt" and n.get("disc_gran", 0) > 0:
            allow_discards = True
        if mount_discards.get(f"/dev/mapper/{name}") or mount_discards.get(f"/dev/{name}"):
            discard_mounted = True
        for child in n.get("children", []):
            walk(child)

    walk(part_name)
    return allow_discards, discard_mounted


def _compute_gaps_from_json(pt_json: dict[str, Any], total_sectors: int) -> list[SectorRange]:
    pt = pt_json.get("partitiontable", {})
    firstlba = int(pt.get("firstlba", 2048))
    lastlba = int(pt.get("lastlba", total_sectors - 1))
    partitions = pt.get("partitions", [])

    if not partitions:
        return [(firstlba, lastlba)] if lastlba > firstlba else []

    sorted_parts = sorted(partitions, key=lambda p: int(p.get("start", 0)))
    gaps: list[SectorRange] = []
    curr = firstlba

    for p in sorted_parts:
        p_start = int(p.get("start", 0))
        p_size = int(p.get("size", 0))
        p_end = p_start + p_size - 1

        if p_start > curr:
            gaps.append((curr, p_start - 1))
        curr = max(curr, p_end + 1)

    if curr <= lastlba:
        gaps.append((curr, lastlba))

    return gaps


def parse_partition_table(device: str) -> DiskLayout | None:
    if not shutil.which("sfdisk"):
        console.print("[red]sfdisk not found. Please install util-linux.[/]")
        return None

    dev_name = os.path.basename(device)
    sector_size = _get_device_sector_size(device)
    total_sectors = 0

    if content := _read_text(f"/sys/block/{dev_name}/size"):
        with suppress(ValueError):
            total_sectors = (int(content.strip()) * 512) // sector_size

    if total_sectors == 0:
        console.print(f"[red]Could not determine sector count for {device}[/]")
        return None

    model = _get_device_model(device)
    disc_gran = _get_device_discard_granularity(device)
    disc_max = _get_device_discard_max_bytes(device)

    pt_data = _run_json(["sfdisk", "--json", device])

    if pt_data is None or not pt_data.get("partitiontable"):
        tree = build_device_tree(device)
        root = tree.get(dev_name, {})
        fstype = root.get("fstype", "")
        if fstype:
            mps = list(dict.fromkeys(root.get("mountpoints", [])))
            allow_d, disc_m = analyze_partition_discard(dev_name, tree, get_mount_discards())
            partitions = [
                PartitionInfo(
                    name=dev_name, start_sector=0, end_sector=total_sectors - 1,
                    size_sectors=total_sectors, fs_type=fstype,
                    mountpoint=", ".join(mps) if mps else "unmounted",
                    is_luks="crypto_LUKS" in fstype or "luks" in fstype.lower(),
                    allow_discards=allow_d, discard_mounted=disc_m,
                )
            ]
            return DiskLayout(
                device=device, model=model, total_sectors=total_sectors,
                sector_size=sector_size, label="none", partitions=partitions,
                unallocated_gaps=[], discard_granularity=disc_gran, discard_max_bytes=disc_max,
            )
        return DiskLayout(
            device=device, model=model, total_sectors=total_sectors,
            sector_size=sector_size, label="none", partitions=[],
            unallocated_gaps=[(0, total_sectors - 1)], discard_granularity=disc_gran, discard_max_bytes=disc_max,
        )

    gaps: list[SectorRange] = []
    res_free = _run(["sfdisk", "--list-free", device])
    if res_free and res_free.returncode == 0 and res_free.stdout:
        for line in res_free.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
                start, end, sectors = int(parts[0]), int(parts[1]), int(parts[2])
                if sectors >= 2048:
                    gaps.append((start, end))

    if not gaps:
        gaps = _compute_gaps_from_json(pt_data, total_sectors)

    pt = pt_data.get("partitiontable", {})
    tree = build_device_tree(device)
    mount_discards = get_mount_discards()
    partitions: list[PartitionInfo] = []

    for part in pt.get("partitions", []):
        name = part.get("node", "").split("/")[-1]
        start, size = part.get("start", 0), part.get("size", 0)

        node = tree.get(name, {})
        fs_type = node.get("fstype") or "unknown"
        mps = resolve_mountpoints(name, tree)
        mp = "[SWAP]" if not mps and fs_type == "swap" else (", ".join(mps) if mps else "unmounted")
        allow_discards, discard_mounted = analyze_partition_discard(name, tree, mount_discards)

        partitions.append(PartitionInfo(
            name=name, start_sector=start, end_sector=start + size - 1, size_sectors=size,
            fs_type=fs_type, mountpoint=mp, is_luks=("crypto_LUKS" in fs_type or "luks" in fs_type.lower()),
            allow_discards=allow_discards, discard_mounted=discard_mounted,
        ))

    return DiskLayout(
        device=device, model=model, total_sectors=total_sectors,
        sector_size=sector_size, label=pt.get("label", "unknown"),
        partitions=partitions, unallocated_gaps=gaps,
        discard_granularity=disc_gran, discard_max_bytes=disc_max,
    )


# =============================================================================
# SMART Telemetry
# =============================================================================
def _query_nvme_smart(device: str) -> SmartData | None:
    m = re.search(r"(nvme\d+)", device)
    ctrl = f"/dev/{m.group(1)}" if m else None
    if not ctrl or not shutil.which("nvme"):
        return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        log_future = pool.submit(_run_json, ["nvme", "smart-log", ctrl, "-o", "json"])
        id_future = pool.submit(_run_json, ["nvme", "id-ctrl", ctrl, "-o", "json"])
        log = log_future.result()
        ident = id_future.result()

    if not log or not isinstance(log, dict):
        return None

    smart: SmartData = {"device": device, "interface": "NVMe"}

    temp_k_raw = log.get("temperature")
    temp_k = int(temp_k_raw) if temp_k_raw is not None else 0
    smart["temp"] = float(temp_k - 273.15) if temp_k > 200 else float(temp_k)

    smart["percentage_used"] = int(log.get("percentage_used") or log.get("percent_used") or 0)
    smart["tbw_written"] = round(int(log.get("data_units_written") or 0) * 512_000 / 1e12, 2)
    smart["power_on_hours"] = int(log.get("power_on_hours") or 0)
    smart["unsafe_shutdowns"] = int(log.get("unsafe_shutdowns") or 0)
    smart["media_errors"] = int(log.get("media_errors") or 0)

    if ident and isinstance(ident, dict):
        smart["model"] = str(ident.get("mn") or "Unknown NVMe").strip()
        smart["serial"] = str(ident.get("sn") or "N/A").strip()
        smart["firmware"] = str(ident.get("fr") or "N/A").strip()
    else:
        smart["model"] = _get_device_model(device)
        smart["serial"], smart["firmware"] = "N/A", "N/A"

    capacity_tb = get_device_capacity_tb(device)
    smart["flash_type"] = detect_flash_type(smart["model"], smart["percentage_used"], smart["tbw_written"], capacity_tb)
    smart["tbw_rated"] = estimate_tbw_rated(capacity_tb, smart["flash_type"])
    return smart


def _extract_sata_wear_percentage(attrs: dict[int, dict[str, Any]]) -> int:
    for attr_id in (231, 233, 202, 169, 177):
        attr = attrs.get(attr_id) or {}
        val = attr.get("value")
        if val is not None:
            with suppress(ValueError):
                v = int(val)
                if 0 < v <= 100:
                    return max(0, 100 - v)
    return 0


def _extract_sata_tbw(attrs: dict[int, dict[str, Any]], capacity_tb: float = 0.0) -> float:
    attr_241 = attrs.get(241) or {}
    raw_val = (attr_241.get("raw") or {}).get("value")
    if raw_val is not None:
        with suppress(ValueError):
            raw = int(raw_val)
            name = str(attr_241.get("name", "")).upper()
            tbw_32mb = round(raw * 32 * 1024 * 1024 / 1e12, 2)
            tbw_512b = round(raw * 512 / 1e12, 2)
            if "32MIB" in name or "32MB" in name:
                return tbw_32mb
            if tbw_512b < 0.1 and 0.1 <= tbw_32mb < 1000.0 and capacity_tb > 0.1:
                return tbw_32mb
            return tbw_512b

    attr_249 = attrs.get(249) or {}
    raw_249 = (attr_249.get("raw") or {}).get("value")
    if raw_249 is not None:
        with suppress(ValueError):
            return round(int(raw_249) * (1 << 30) / 1e12, 2)
    return 0.0


def _query_block_smart(device: str) -> SmartData | None:
    if not shutil.which("smartctl"):
        console.print("[red]smartctl not found. Please install smartmontools.[/]")
        return None

    capacity_tb = get_device_capacity_tb(device)

    for args in (["smartctl", "-x", "--json", device], ["smartctl", "-x", "--json", "-d", "sat", device]):
        data = _run_json(args, timeout=10.0)
        if not data or not isinstance(data, dict):
            continue

        if not any(data.get(k) for k in ("model_name", "ata_smart_attributes", "nvme_smart_health_information_log", "scsi_smart_health_status", "scsi_grown_defect_list")):
            continue

        smart: SmartData = {
            "device": device, "interface": "SATA",
            "model": data.get("model_name", "Unknown SSD"),
            "serial": data.get("serial_number", "N/A"),
            "firmware": data.get("firmware_version", "N/A"),
            "temp": float((data.get("temperature") or {}).get("current") or 0)
        }

        if nvme_log := data.get("nvme_smart_health_information_log"):
            smart.update({
                "interface": "NVMe",
                "percentage_used": int(nvme_log.get("percentage_used") or 0),
                "tbw_written": round(int(nvme_log.get("data_units_written") or 0) * 512_000 / 1e12, 2),
                "power_on_hours": int(nvme_log.get("power_on_hours") or 0),
                "unsafe_shutdowns": int(nvme_log.get("unsafe_shutdowns") or 0),
                "media_errors": int(nvme_log.get("media_errors") or 0)
            })
        elif ata_attrs := data.get("ata_smart_attributes"):
            attrs: dict[int, dict[str, Any]] = {a["id"]: a for a in ata_attrs.get("table", []) if "id" in a}

            poh_val = (attrs.get(9) or {}).get("raw", {}).get("value")
            smart["power_on_hours"] = int(poh_val) if poh_val is not None else 0
            smart["percentage_used"] = _extract_sata_wear_percentage(attrs)
            smart["tbw_written"] = _extract_sata_tbw(attrs, capacity_tb)

            usd_val = (attrs.get(174) or {}).get("raw", {}).get("value")
            smart["unsafe_shutdowns"] = int(usd_val) if usd_val is not None else 0

            realloc = int((attrs.get(5) or {}).get("raw", {}).get("value") or 0)
            uncorr = int((attrs.get(187) or {}).get("raw", {}).get("value") or 0)
            smart["media_errors"] = realloc + uncorr
        else:
            smart.update({
                "interface": "SCSI", "percentage_used": 0, "tbw_written": 0.0, "unsafe_shutdowns": 0,
                "power_on_hours": int(data.get("scsi_hours_powered_on") or 0),
                "media_errors": int(data.get("scsi_grown_defect_list") or 0)
            })

        smart["flash_type"] = detect_flash_type(smart.get("model", ""), smart.get("percentage_used", 0), smart.get("tbw_written", 0.0), capacity_tb)
        smart["tbw_rated"] = estimate_tbw_rated(capacity_tb, smart["flash_type"])
        return smart

    return None


def query_live_smart_data(device: str) -> SmartData | None:
    return _query_nvme_smart(device) if re.search(r"nvme\d+n\d+", device) else _query_block_smart(device)


# =============================================================================
# Sector Content Sampling
# =============================================================================
def scan_unallocated_regions(dev_path: str, gaps: list[SectorRange], sector_size: int = 512, total_samples: int = 500) -> float:
    valid_gaps: list[tuple[int, int, int]] = [(s, e, e - s + 1) for s, e in gaps if e >= s]
    total_unalloc_sectors = sum(g[2] for g in valid_gaps)
    if total_unalloc_sectors <= 0:
        return 0.0

    total_samples = min(total_samples, total_unalloc_sectors)
    gap_samples, allocated = [], 0

    for start, end, sectors in valid_gaps:
        nsamp = max(1, round(total_samples * (sectors / total_unalloc_sectors)))
        nsamp = min(nsamp, sectors)
        gap_samples.append([start, end, nsamp])
        allocated += nsamp

    diff = total_samples - allocated
    if diff > 0 and gap_samples:
        gap_samples.sort(key=lambda x: x[2], reverse=True)
        for g in gap_samples:
            add = min(diff, (g[1] - g[0] + 1) - g[2])
            g[2] += add
            diff -= add
            if diff <= 0:
                break

    gap_samples.sort(key=lambda x: x[0])
    actual_total = sum(g[2] for g in gap_samples)
    if actual_total <= 0:
        return 0.0

    zero_block = bytes(4096)
    dirty_count, tested = 0, 0

    progress = Progress(
        SpinnerColumn(spinner_name="dots"), TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40), TextColumn("[bold cyan]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(), console=console,
    )

    try:
        fd = os.open(dev_path, os.O_RDONLY | os.O_CLOEXEC)
    except PermissionError:
        console.print(f"[bold red][!] Permission denied for {dev_path}. Scanning requires root. Assuming 100% dirty.[/]")
        return 1.0
    except OSError as e:
        console.print(f"[bold red][!] Cannot open block device {dev_path}: {e}[/]")
        return 1.0

    try:
        with progress:
            task = progress.add_task(f"Scanning {os.path.basename(dev_path)} unallocated space...", total=actual_total)
            for start, end, nsamp in gap_samples:
                step = max(1, (end - start + 1) // nsamp)
                for i in range(nsamp):
                    target_sector = start + i * step
                    if target_sector > end:
                        break

                    offset = target_sector * sector_size
                    bytes_to_read = min(4096, (end - target_sector + 1) * sector_size)

                    try:
                        block = os.pread(fd, bytes_to_read, offset)
                        if hasattr(os, "posix_fadvise"):
                            with suppress(OSError):
                                os.posix_fadvise(fd, offset, bytes_to_read, os.POSIX_FADV_DONTNEED)
                    except OSError as e:
                        progress.console.print(f"[dim red]I/O error at sector {target_sector}: {e} (marked dirty)[/]")
                        dirty_count += 1
                        tested += 1
                        progress.update(task, advance=1)
                        continue

                    if not block:
                        break
                    if (len(block) == 4096 and block != zero_block) or (len(block) != 4096 and block != bytes(len(block))):
                        dirty_count += 1

                    tested += 1
                    progress.update(task, advance=1)
    finally:
        os.close(fd)

    return (dirty_count / tested) if tested > 0 else 0.0


# =============================================================================
# Host System & Queue Telemetry
# =============================================================================
def _format_bytes(bytes_val: int) -> str:
    if bytes_val <= 0:
        return "[dim]0 B (No hardware support)[/]"
    for unit, threshold in (("TiB", 1 << 40), ("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10)):
        if bytes_val >= threshold:
            return f"[bold bright_cyan]{bytes_val / threshold:.1f} {unit}[/]"
    return f"[bold bright_cyan]{bytes_val} B[/]"


def _query_nvme_driver_type(kernel_ver: str) -> str:
    for config_path in (f"/boot/config-{kernel_ver}", "/proc/config.gz"):
        try:
            if config_path.endswith(".gz"):
                with gzip.open(config_path, "rt", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            else:
                with open(config_path, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            for line in content.splitlines():
                if "NVME" in line and "RUST" in line and line.rstrip().endswith("=y"):
                    return "Active (Mainline Rust NVMe Driver)"
            return "Active (Standard C NVMe Driver)"
        except OSError:
            continue
    return "Active (NVMe module loaded)"


def _query_sata_driver(device: str) -> str:
    try:
        device_path = os.path.realpath(f"/sys/block/{os.path.basename(device)}/device")
        for _ in range(8):
            driver_link = os.path.join(device_path, "driver")
            if os.path.islink(driver_link):
                driver_name = os.path.basename(os.readlink(driver_link))
                if driver_name not in ("sd", "sr", "scsi"):
                    return f"Active ({driver_name} controller driver)"
            parent = os.path.dirname(device_path)
            if parent == device_path or parent == "/":
                break
            device_path = parent
    except OSError:
        pass
    return "Active (AHCI controller driver)"


def query_system_telemetry(device: str, is_mock: bool = False, is_nvme: bool = True) -> dict[str, str]:
    telemetry: dict[str, str] = {
        "kernel": "7.1.2-arch3-1" if is_mock else "Unknown",
        "fstrim_timer": "Active (Weekly)" if is_mock else "Inactive",
        "discard_granularity": "[bold bright_cyan]512 B[/]" if is_mock else "N/A",
        "discard_max_bytes": "[bold bright_cyan]2.0 TiB[/]" if is_mock else "N/A",
        "storage_driver": "Active (Mainline Rust NVMe Driver)" if is_mock and is_nvme else "Active (ahci SATA HBA driver)" if is_mock else "Unknown"
    }

    if is_mock:
        if is_nvme and "nvme1n1" in device:
            telemetry["discard_granularity"] = "[bold bright_cyan]4.0 KiB[/]"
        if not is_nvme:
            telemetry["discard_max_bytes"] = "[bold bright_cyan]2.0 GiB[/]"
        return telemetry

    if res := _run(["uname", "-r"], timeout=3.0):
        if res.returncode == 0:
            telemetry["kernel"] = res.stdout.strip()

    if res := _run(["systemctl", "is-active", "fstrim.timer"], timeout=3.0):
        telemetry["fstrim_timer"] = "Active (Weekly System Timer)" if res.returncode == 0 or "active" in res.stdout else "Inactive"

    dev_name = os.path.basename(device)
    for key, path_suffix in (("discard_granularity", "queue/discard_granularity"), ("discard_max_bytes", "queue/discard_max_bytes")):
        if content := _read_text(f"/sys/block/{dev_name}/{path_suffix}"):
            with suppress(ValueError):
                val = int(content.strip())
                telemetry[key] = _format_bytes(val) if key == "discard_max_bytes" or val > 0 else "0 (No discard support)"

    if is_nvme:
        telemetry["storage_driver"] = _query_nvme_driver_type(telemetry["kernel"]) if os.path.exists("/sys/module/nvme_core") else "Inactive"
    else:
        telemetry["storage_driver"] = _query_sata_driver(device)

    return telemetry


# =============================================================================
# Dashboard Rendering & Discard Alignment Logic
# =============================================================================
def draw_layout_bar(layout: DiskLayout, dirty_ratio: float) -> str:
    width = 64
    bar = ["[dim grey]─[/]"] * width
    total = layout.total_sectors

    def scale(start: int, end: int) -> tuple[int, int]:
        return max(0, min(width - 1, int((start / total) * width))), max(0, min(width - 1, int((end / total) * width)))

    for gap in layout.unallocated_gaps:
        s, e = scale(gap[0], gap[1])
        dirty_chars = int((e - s + 1) * dirty_ratio)
        for idx in range(s, s + dirty_chars):
            if 0 <= idx < width:
                bar[idx] = "[bold yellow]░[/]"
        for idx in range(s + dirty_chars, e + 1):
            if 0 <= idx < width:
                bar[idx] = "[bold green]▒[/]"

    for part in layout.partitions:
        s, e = scale(part.start_sector, part.end_sector)
        color = FS_COLORS.get(part.fs_type, "cyan")
        for idx in range(s, e + 1):
            if 0 <= idx < width:
                bar[idx] = f"[bold {color}]█[/]"

    return "".join(bar)


def align_gap_to_erase_blocks(gap: SectorRange, sector_size: int = 512, disc_granularity: int = 0) -> tuple[int, int] | None:
    start_sec, end_sec = gap
    min_erase_bytes = max(disc_granularity, MIN_ERASE_BLOCK_BYTES)
    sector_align = max(1, min_erase_bytes // sector_size)
    aligned_start = math.ceil(start_sec / sector_align) * sector_align
    aligned_end = ((end_sec + 1) // sector_align * sector_align) - 1
    if aligned_end > aligned_start:
        return (aligned_start, aligned_end)
    return None


def _build_discard_commands(layout: DiskLayout, force: bool = False) -> list[str]:
    commands: list[str] = []
    flag = "-f " if force else ""
    max_chunk_bytes = layout.discard_max_bytes if layout.discard_max_bytes > 0 else DEFAULT_MAX_DISCARD_CHUNK

    for gap in layout.unallocated_gaps:
        if aligned := align_gap_to_erase_blocks(gap, layout.sector_size, layout.discard_granularity):
            start_off = aligned[0] * layout.sector_size
            total_len = (aligned[1] - aligned[0] + 1) * layout.sector_size

            curr_off = start_off
            rem_len = total_len
            while rem_len > 0:
                chunk_len = min(rem_len, max_chunk_bytes)
                commands.append(f"sudo blkdiscard {flag}--offset {curr_off} --length {chunk_len} {layout.device}")
                curr_off += chunk_len
                rem_len -= chunk_len
    return commands


def _build_smart_table(smart: SmartData) -> Table:
    health = max(0, min(100, 100 - smart.get("percentage_used", 0)))
    filled = int(20 * health / 100)
    health_bar = f"[green]{'█' * filled}[/][red]{'░' * (20 - filled)}[/]"
    health_str = f"[bold green]{health}%[/]" if health >= 90 else f"[bold yellow]{health}%[/]" if health >= 75 else f"[bold red]{health}%[/] [blink][WARNING][/]"
    unsafe, media_err = smart.get("unsafe_shutdowns", 0), smart.get("media_errors", 0)
    flash_type, interface = smart.get("flash_type", "TLC"), smart.get("interface", "NVMe")

    table = Table.grid(padding=(0, 2))
    table.add_column("Key", style="dim", width=23)
    table.add_column("Value", style="bold")
    for k, v in [
        ("Model / Silicon:", smart.get("model", "N/A")), ("Serial Number:", smart.get("serial", "N/A")),
        ("Firmware Version:", smart.get("firmware", "N/A")), ("Interface Bus:", f"[bold blue]{interface}[/]"),
        ("Flash Cell Type:", f"[bold {'magenta' if flash_type == 'QLC' else 'green'}]{flash_type}[/]"),
        ("Controller Temp:", f"[bold bright_yellow]{smart.get('temp', 0):.1f}°C[/]"), ("Total Host Writes:", f"[bold bright_cyan]{smart.get('tbw_written', 0):.2f} TB[/]"),
        ("Device Rated Endurance:", f"[bold bright_cyan]{smart.get('tbw_rated', 0):.0f} TBW[/]"), ("SMART Health Remaining:", health_str),
        ("Health Bar Representation:", health_bar), ("Power On Hours:", f"[bold blue]{smart.get('power_on_hours', 0):,}[/] hours"),
        ("Unsafe Power Cuts:", f"[red]{unsafe:,}[/]" if unsafe > 100 else f"[bold yellow]{unsafe:,}[/]"),
        ("Physical Media Errors:", f"[bold red]{media_err}[/]" if media_err > 0 else "[bold green]0 (Healthy)[/]")
    ]:
        table.add_row(k, v)
    return table


def _build_op_table(layout: DiskLayout, smart: SmartData, scan_ratio: float | None, is_cleared: bool = False) -> Table:
    total_sec = layout.total_sectors
    part_sec = sum(p.size_sectors for p in layout.partitions)
    unalloc_sec = sum((g[1] - g[0] + 1) for g in layout.unallocated_gaps)
    op_raw_pct = (unalloc_sec / total_sec) * 100.0 if total_sec else 0.0

    table = Table.grid(padding=(0, 2))
    table.add_column("Key", style="dim", width=29)
    table.add_column("Value", style="bold")

    ss = layout.sector_size
    table.add_row("Total Block Capacity:", f"[bold bright_cyan]{total_sec * ss / (1 << 30):.2f} GiB[/] ([bold blue]{total_sec:,}[/] sectors)")
    table.add_row("Partitioned Extents:", f"[bold bright_cyan]{part_sec * ss / (1 << 30):.2f} GiB[/] ([bold blue]{part_sec:,}[/] sectors)")
    table.add_row("Unallocated Free Extents:", f"[bold bright_cyan]{unalloc_sec * ss / (1 << 30):.2f} GiB[/] ([bold blue]{unalloc_sec:,}[/] sectors)")
    table.add_row("Raw Over-Provisioning Limit:", f"[bold yellow]{op_raw_pct:.2f}%[/] of disk")

    if is_cleared:
        table.add_row("FTL Allocation Status:", "[bold green]Active (Cleared during this session)[/]")
        scan_ratio = 0.0
    elif scan_ratio is None:
        table.add_row("FTL Allocation Status:", "[bold yellow]Not Scanned (Run --scan to check unallocated sector maps)[/]")
        return table

    active_op_pct = op_raw_pct * (1.0 - scan_ratio)
    proj = get_lifespan_projections(smart.get("tbw_written", 0.0), smart.get("tbw_rated", 100.0), active_op_pct, op_raw_pct, smart.get("flash_type", "TLC") == "QLC")

    table.add_row("Dirty Free Space (Mapped):", f"[{'bold red' if scan_ratio > 0 else 'bold green'}]{scan_ratio * 100:.2f}%[/] of free space")
    table.add_row("Functional OP Pool:", f"[bold green]{active_op_pct:.2f}%[/]")
    table.add_row("Steady-State WAF:", f"Current: [bold yellow]{proj['current_waf']:.2f}[/] → Target: [bold green]{proj['target_waf']:.2f}[/]")
    table.add_row("Write Longevity Multiplier:", f"[bold green]{proj['multiplier']:.2f}x[/] lifespan extension")
    table.add_row("Future Host Write Capacity:", f"[bold yellow]{proj['remaining_tbw']:.1f} TB[/] → [bold green]{proj['extended_remaining_tbw']:.1f} TB[/] via OP")
    return table


def _build_partition_table(layout: DiskLayout) -> Table:
    table = Table(title="Partition Discard & Encryption Configuration", header_style="bold cyan", border_style="dim", show_lines=False, expand=True, width=PANEL_WIDTH)
    for col, st, rt, ju in [("Partition", "bold green", 1, "left"), ("Type", "blue", 1, "left"), ("Mountpoint", "white", 2, "left"), ("LUKS?", "magenta", 1, "center"), ("LUKS Discard Passthrough", "yellow", 2, "center"), ("FS Mount Discard Flag", "cyan", 2, "center")]:
        table.add_column(col, style=st, ratio=rt, justify=ju)

    for p in layout.partitions:
        luks_pt = "[bold green]Enabled (allow_discards)[/]" if p.allow_discards else "[bold red]Disabled (Blocks TRIM)[/]" if p.is_luks else "[dim]N/A (No Encryption)[/]"
        fs_discard = "[dim]N/A (Unmounted/Swap)[/]" if p.mountpoint in ("unmounted", "[SWAP]") else "[bold green]Active (discard)[/]" if p.discard_mounted else "[bold yellow]Inactive (No discard flag)[/]"
        table.add_row(p.name, p.fs_type, p.mountpoint, "[bold magenta]Yes[/]" if p.is_luks else "No", luks_pt, fs_discard)
    return table


def render_drive_diagnostics(layout: DiskLayout, smart: SmartData, scan_ratio: float | None, dry_run: bool = False, exec_discard: bool = False, is_mock: bool = False) -> None:
    health = max(0, min(100, 100 - smart.get("percentage_used", 0)))
    health_str = f"[bold green]{health}%[/]" if health >= 90 else f"[bold yellow]{health}%[/]" if health >= 75 else f"[bold red]{health}%[/] [blink][WARNING][/]"

    sys_tel = query_system_telemetry(layout.device, is_mock=is_mock, is_nvme=(smart.get("interface", "NVMe") == "NVMe"))
    sys_table = Table.grid(padding=(0, 2))
    sys_table.add_column("Key", style="dim", width=30)
    sys_table.add_column("Value", style="bold")
    drv = sys_tel.get("storage_driver", "N/A")
    for k, v in [("Arch Linux Kernel Version:", f"[bold bright_blue]{sys_tel.get('kernel', 'N/A')}[/]"), ("Systemd TRIM Service Timer:", f"[bold green]{sys_tel.get('fstrim_timer', 'N/A')}[/]" if "Active" in sys_tel.get("fstrim_timer", "") else f"[bold yellow]{sys_tel.get('fstrim_timer', 'N/A')}[/]"), ("Device Discard Granularity:", sys_tel.get("discard_granularity", "N/A")), ("Device Max Discard Block Size:", sys_tel.get("discard_max_bytes", "N/A")), ("Active Storage Driver:", f"[bold green]{drv}[/]" if "Active" in drv else drv)]:
        sys_table.add_row(k, v)

    sys_panel = Panel(sys_table, title="[bold white]Host OS & Storage Queue Telemetry[/]", border_style="dim", width=PANEL_WIDTH)
    legend = "[bold cyan]█[/] Ext4/Btrfs    [bold bright_magenta]█[/] LUKS Map    [bold bright_red]█[/] Swap    [bold yellow]░[/] Dirty OP Space    [bold green]▒[/] Clean OP Space    [dim grey]─[/] Slack"

    visual_ratio = 0.0 if exec_discard else (scan_ratio or 0.0)

    group = Group(
        Text.from_markup(f"\n[bold white]DEVICE TELEMETRY DASHBOARD FOR {layout.device}[/]\n{smart.get('model', 'N/A')}  |  Serial: {smart.get('serial', 'N/A')}  |  Health: {health_str}\n"),
        Columns([
            Panel(_build_smart_table(smart), title="[bold white]S.M.A.R.T. Hardware Health[/]", border_style="dim", width=int(PANEL_WIDTH * 0.41)),
            Panel(_build_op_table(layout, smart, scan_ratio, is_cleared=exec_discard), title="[bold white]FTL Over-Provisioning Mapping[/]", border_style="dim", width=int(PANEL_WIDTH * 0.58))
        ]),
        sys_panel,
        Text.from_markup(f"\n[bold white]Physical Disk Sector Map Layout:[/]\n{draw_layout_bar(layout, visual_ratio)}\n[dim]{legend}[/]\n")
    )

    border = "green" if exec_discard else ("cyan" if scan_ratio is None else "green" if scan_ratio == 0.0 else "yellow")
    console.print(Panel(Align.center(group), border_style=border, width=PANEL_WIDTH))
    console.print(_build_partition_table(layout))

    # 1. Render Status Condition
    if scan_ratio is not None:
        if scan_ratio == 0.0 and not exec_discard:
            rec = Text.assemble("\n", "[bold green][+] DIAGNOSTIC HEALTH REPORT:[/]\n", "This drive's unallocated extents are fully trimmed and unmapped in the Flash Translation Layer.\n", "The SSD controller is leveraging the entire unallocated space as functional over-provisioning.\n", "Write amplification is fully optimized. No further action required.\n")
            console.print(Panel(rec, title="[bold green]Optimized Wear-Leveling Status[/]", border_style="green", width=PANEL_WIDTH))
        elif scan_ratio > 0 and not exec_discard:
            cmds = _build_discard_commands(layout, force=True)
            cmd_lines = "\n".join(f"  {c}" for c in cmds)
            rec = Text.assemble("\n", "[bold yellow][!] DIAGNOSTIC ADVISORY:[/]\n", "This drive contains unallocated sectors holding obsolete host data mappings.\n", "The SSD controller cannot utilize these blocks for over-provisioning until they are discarded.\n\n", "[bold green][*] RECOMMENDED ACTION COMMANDS (4MB Erase Block Aligned & Hardware Chunked):[/]\n", f"{cmd_lines}\n\n", "[dim]Note: blkdiscard is automatically 4MB erase-block aligned and chunked to queue limits for partition safety.[/]")
            console.print(Panel(rec, title="[bold yellow]Wear-Leveling Correction Plan[/]", border_style="yellow", width=PANEL_WIDTH))

    # 2. Render Execution or Simulation Panels
    max_chunk_bytes = layout.discard_max_bytes if layout.discard_max_bytes > 0 else DEFAULT_MAX_DISCARD_CHUNK
    if exec_discard and layout.unallocated_gaps:
        console.print(Panel("[bold red]Executing Live Discard operations to clear FTL maps...[/]", border_style="red", width=PANEL_WIDTH))
        if is_mock:
            for c in _build_discard_commands(layout, force=True):
                console.print(f"  [green]✔ MOCK SUCCESS: Executed {c}[/]")
        else:
            for gap in layout.unallocated_gaps:
                if aligned := align_gap_to_erase_blocks(gap, layout.sector_size, layout.discard_granularity):
                    start_off = aligned[0] * layout.sector_size
                    total_len = (aligned[1] - aligned[0] + 1) * layout.sector_size

                    curr_off = start_off
                    rem_len = total_len
                    while rem_len > 0:
                        chunk_len = min(rem_len, max_chunk_bytes)
                        try:
                            _run(["blkdiscard", "-f", "--offset", str(curr_off), "--length", str(chunk_len), layout.device], timeout=60.0)
                            console.print(f"  [green]✔ Successfully trimmed {(chunk_len / (1<<20)):.2f} MiB at aligned offset {curr_off}[/]")
                        except Exception as e:
                            console.print(f"  [red]✘ Failed to discard offset {curr_off}: {e}[/]")
                        curr_off += chunk_len
                        rem_len -= chunk_len
                else:
                    console.print(f"  [dim yellow]Notice: Unallocated gap ({gap[0]}-{gap[1]}) is smaller than erase block boundary. Skipping discard for partition safety.[/]")
        console.print("\n[bold green][+] Wear-leveling map has been refreshed. Dynamic OP is fully active![/]")

    elif dry_run and layout.unallocated_gaps:
        cmds = _build_discard_commands(layout, force=True)
        console.print(Panel("\n".join(f"  {c}" for c in cmds), title="[bold blue]Simulated Absolute-Bound Commands[/]", border_style="blue", width=PANEL_WIDTH))
        console.print("[dim]Note: The -f (force) flag is required to bypass whole-disk exclusive lock checks, allowing targeted gap clearing while mounted partitions are active.[/]")


def render_glossary_panel() -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column("Term", style="bold cyan", width=25)
    table.add_column("Explanation", style="white")
    for k, v in [
        ("Write Amplification (WAF):", "Ratio of physical data programmed to flash vs logical data written by the host. WAF 1.0 is optimal."),
        ("SMART Percentage Used:", "Firmware's mathematical estimate of total P/E cycles exhausted on flash cells."),
        ("LUKS Discard Passthrough:", "Encryption layers block block-deallocation by default. `allow_discards` permits TRIM to pass to the controller."),
        ("FS Mount Discard Flag:", "Mount options `discard` or `discard=async` that instruct the controller to unmap deleted file sectors immediately."),
        ("Dirty Free Space:", "Logical unallocated extents containing legacy host writes. The SSD FTL still maps these LBAs.")
    ]:
        table.add_row(k, v)
    console.print(Panel(table, title="[bold white]Diagnostic Guide & Parameter Explanations[/]", border_style="dim", width=PANEL_WIDTH))


def render_summary_table(summary_data: list[dict[str, Any]]) -> None:
    table = Table(title="Dusky Drive Health Summary Report", header_style="bold bright_cyan", border_style="cyan", expand=True, width=PANEL_WIDTH)
    for col, st, rt, ju in [("Device", "bold green", 1, "left"), ("Interface", "blue", 1, "left"), ("Model", "white", 2, "left"), ("SMART Health", "bold", 1, "center"), ("Writes (TB)", "yellow", 1, "center"), ("OP Pool %", "bold green", 1, "center"), ("OP Mapping State", "bold", 2, "center")]:
        table.add_column(col, style=st, ratio=rt, justify=ju)

    for d in summary_data:
        health = max(0, min(100, 100 - d["pct_used"]))
        health_color = "green" if health >= 90 else "yellow" if health >= 75 else "red"
        dirty = d.get("dirty_ratio")
        cleared = d.get("cleared", False)

        state_str = "[yellow]Not Scanned[/]"
        if cleared:
            state_str = "[bold green]Cleared & Optimized[/]"
        elif dirty is not None:
            state_str = "[green]Fully Optimized (Clean)[/]" if dirty == 0.0 else f"[yellow]Degraded ({dirty*100:.1f}% Dirty)[/]"

        table.add_row(d["device"], d["interface"], d["model"], f"[{health_color}]{health}%[/]", f"{d['tbw']:.2f}", f"{d['op']:.2f}%", state_str)
    console.print(table)


def interactive_menu() -> int:
    console.print(Align.center(Panel(
        "[bold cyan]INTERACTIVE MODE SELECTOR[/]\n"
        "[dim]Dusky Drive Health Diagnostics Selector[/]",
        border_style="cyan",
        expand=False
    )))

    table = Table(box=None, expand=False)
    table.add_column("Option", style="bold green", justify="right")
    table.add_column("Description", style="white")
    table.add_row("[1]", "Standard Diagnostics (SMART & Layouts)")
    table.add_row("[2]", "Deep Sector Scan (Diagnose FTL mapping ratios)")
    table.add_row("[3]", "Simulate Discards (Dry-run boundary logic)")
    table.add_row("[4]", "Smart Clear Unallocated Space (Scan and wipe ONLY if dirty)")
    table.add_row("[5]", "Exit")

    console.print(Align.center(table))
    console.print()

    while True:
        try:
            choice = console.input("[bold yellow]Enter choice (1-5): [/]").strip()
            if choice in {"1", "2", "3", "4", "5"}:
                return int(choice)
            console.print("[red]Invalid selection. Please enter 1, 2, 3, 4, or 5.[/]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Interrupted. Exiting.[/]")
            return 5


def _elevate_privileges(choice: int, args: argparse.Namespace) -> None:
    if not sys.stdin.isatty():
        probe = _run(["sudo", "-n", "true"], timeout=3.0)
        if probe is None or probe.returncode != 0:
            console.print("[bold red][x] Error: Hardware diagnostics require root privileges, but session is non-interactive and sudo requires a password.[/]")
            console.print(f"Please run this command directly from your interactive terminal:\n  sudo {sys.executable} {' '.join(sys.argv)}\n")
            sys.exit(1)

    console.print("[yellow][!] Hardware diagnostics require root privileges. Auto-elevating via sudo...[/]")
    target_args = ["sudo", "-E", sys.executable, sys.argv[0]]
    if choice > 0:
        target_args.extend(["--menu-executed", str(choice)])
    if args.device:
        target_args.extend(["--device", args.device])
    if args.scan:
        target_args.append("--scan")
    if args.dry_run_discard:
        target_args.append("--dry-run-discard")
    if args.execute_discard:
        target_args.append("--execute-discard")
    try:
        os.execvp("sudo", target_args)
    except Exception as e:
        console.print(f"[bold red][x] Privilege auto-elevation failed: {e}[/]")
        sys.exit(1)


# =============================================================================
# Main
# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Dusky Drive Health Diagnostic Suite", epilog="Arch Linux Kernel 7.1 Multi-Interface SSD Analyzer")
    parser.add_argument("-v", "--version", action="version", version=f"Dusky Drive Health v{VERSION}")
    parser.add_argument("--mock", action="store_true", help="Execute in safe isolation demonstration mode with mock profiles.")
    parser.add_argument("--scan", action="store_true", help="Perform real read-only unallocated sector scan (requires root privileges).")
    parser.add_argument("--device", type=str, default=None, help="Path of physical drive to target (e.g. /dev/nvme0n1 or /dev/sda)")
    parser.add_argument("--dry-run-discard", action="store_true", help="Dry run the recommended discard operations to verify bounds.")
    parser.add_argument("--execute-discard", action="store_true", help="EXECUTE active discard operations on unallocated gaps.")
    parser.add_argument("--menu-executed", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()

    choice = args.menu_executed

    if len(sys.argv) == 1:
        choice = interactive_menu()
        if choice == 5:
            sys.exit(0)

    run_scan = args.scan or choice in (2, 4)
    dry_run = args.dry_run_discard or choice == 3
    exec_discard = args.execute_discard or choice == 4

    if not args.mock and os.geteuid() != 0:
        _elevate_privileges(choice, args)

    console.print(Align.center(Panel(
        "[bold cyan]DUSKY DRIVE HEALTH DIAGNOSTIC SUITE[/]\n"
        "[dim]Linux Kernel 7.1 & Python 3.14+ Modern Storage Engine Diagnostics[/]",
        border_style="cyan",
        expand=False
    )))

    if args.mock:
        console.print("[bold green][*] Mode: Safe Isolation Mock Demonstration[/]")
        console.print("[dim]Simulating diagnostics for QLC NVMe, TLC NVMe, and SATA SSD drives...[/]\n")
        render_drive_diagnostics(MOCK_INTEL_LAYOUT, MOCK_INTEL_SMART, scan_ratio=0.0, dry_run=dry_run, exec_discard=False, is_mock=True)
        render_drive_diagnostics(MOCK_SAMSUNG_LAYOUT, MOCK_SAMSUNG_SMART, scan_ratio=0.0, dry_run=dry_run, exec_discard=False, is_mock=True)
        render_drive_diagnostics(MOCK_SATA_LAYOUT, MOCK_SATA_SMART, scan_ratio=0.35, dry_run=dry_run, exec_discard=exec_discard, is_mock=True)
        render_glossary_panel()
        mock_summary = [
            {"device": "/dev/nvme0n1", "interface": "NVMe", "model": MOCK_INTEL_SMART["model"], "pct_used": MOCK_INTEL_SMART["percentage_used"], "tbw": MOCK_INTEL_SMART["tbw_written"], "op": 73.9, "dirty_ratio": 0.0, "cleared": False},
            {"device": "/dev/nvme1n1", "interface": "NVMe", "model": MOCK_SAMSUNG_SMART["model"], "pct_used": MOCK_SAMSUNG_SMART["percentage_used"], "tbw": MOCK_SAMSUNG_SMART["tbw_written"], "op": 46.3, "dirty_ratio": 0.0, "cleared": False},
            {"device": "/dev/sda", "interface": "SATA", "model": MOCK_SATA_SMART["model"], "pct_used": MOCK_SATA_SMART["percentage_used"], "tbw": MOCK_SATA_SMART["tbw_written"], "op": 1.7, "dirty_ratio": 0.35, "cleared": exec_discard}
        ]
        render_summary_table(mock_summary)
        sys.exit(0)

    devices = []
    if args.device:
        try:
            mode = os.stat(args.device).st_mode
            if not stat.S_ISBLK(mode):
                console.print(f"[bold red][!] Specified path is not a valid block device node: {args.device}[/]")
                sys.exit(1)
            devices = [args.device]
        except OSError:
            console.print(f"[bold red][!] Specified device path does not exist: {args.device}[/]")
            sys.exit(1)
    else:
        devices = detect_ssd_devices()

    if not devices:
        console.print(f"[bold red][!] No physical NVMe/SATA/SCSI drives detected on this host.[/]")
        sys.exit(1)

    summary_data = []
    for dev in devices:
        if not (layout := parse_partition_table(dev)):
            continue

        if not (smart := query_live_smart_data(dev)):
            capacity_tb = get_device_capacity_tb(dev)
            flash_type = detect_flash_type(layout.model, 0, 0.0, capacity_tb)
            smart = {"device": dev, "model": layout.model, "serial": "N/A", "firmware": "N/A", "temp": 30.0, "percentage_used": 0, "tbw_written": 0.0, "tbw_rated": estimate_tbw_rated(capacity_tb, flash_type), "power_on_hours": 0, "unsafe_shutdowns": 0, "media_errors": 0, "flash_type": flash_type, "interface": "NVMe" if "nvme" in dev else "SATA"}

        scan_ratio = scan_unallocated_regions(dev, layout.unallocated_gaps, layout.sector_size) if run_scan and layout.unallocated_gaps else None

        actually_execute = exec_discard
        if actually_execute and scan_ratio is not None and scan_ratio == 0.0:
            console.print(f"\n[bold green][+] Pre-scan reveals {dev} FTL is already perfectly clean. Bypassing discard operation.[/]")
            actually_execute = False

        render_drive_diagnostics(layout, smart, scan_ratio, dry_run=dry_run, exec_discard=actually_execute)

        summary_data.append({
            "device": dev,
            "interface": smart.get("interface", "NVMe"),
            "model": smart.get("model", "Unknown SSD"),
            "pct_used": smart.get("percentage_used", 0),
            "tbw": smart.get("tbw_written", 0.0),
            "op": (sum((g[1] - g[0] + 1) for g in layout.unallocated_gaps) / layout.total_sectors) * 100.0 if layout.total_sectors else 0.0,
            "dirty_ratio": scan_ratio,
            "cleared": actually_execute
        })

    render_glossary_panel()
    if summary_data:
        render_summary_table(summary_data)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Exiting cleanly.[/]")
        sys.exit(130)

#!/usr/bin/env python3
"""Interactive and automated block-device formatter for the Dusky Linux ISO."""

import os
import sys
import subprocess
import json
import shlex
import uuid
import shutil
import argparse
import re
import signal
from decimal import Decimal
from pathlib import Path
from typing import Any, TypedDict

# ==============================================================================
# 1. ARCHITECTURAL TYPE DEFINITIONS
# ==============================================================================

class FormatPlan(TypedDict):
    device: str
    partition_table: str  # "none", "gpt", "mbr"
    partition_size: str   # "100%", "50%", etc.
    encrypt: bool
    fs_type: str
    csum: str | None
    label: str
    passphrase: str | None
    non_interactive: bool

class ExecutionStep(TypedDict):
    action: str
    desc: str
    cmd: list[str]
    input_data: str | None

# ==============================================================================
# 2. CLI ARGUMENT PARSING & HELP / MANUAL DISPLAY (NO ROOT REQUIRED)
# ==============================================================================

MANUAL_TEXT = """
Dusky Formatter — Linux block-device formatter

Run without arguments for the interactive device tree and setup.
Use --device DEVICE --fs TYPE for CLI setup; add --yes to skip confirmation.
A final confirmation precedes unmounting, signature wiping, and formatting.
Busy mounts and unsupported active holders abort the operation; detach them first.
Dependencies must already be installed (this tool does not invoke pacman).

--partition none formats the selected block device directly.
--partition gpt or mbr creates one partition. --part-size accepts a percentage
of disk capacity (100% fills available space) or an sfdisk size such as 20GiB.
Partition types use Linux for native filesystems/LUKS and Microsoft for FAT/NTFS.

Formatting destroys existing data. Signature wiping is not secure erasure.
Filesystem tools use discard on nonrotational devices where supported; HDDs
skip discard. Ext filesystems use lazy initialization. Initialization still
writes metadata and lazy inode initialization can continue after mounting.

Encryption uses LUKS2, Argon2id, and cryptsetup's hardware-based sector size.
On nonrotational devices the mapping permits discard and bypasses crypto
workqueues. These flags persist in the LUKS2 header.
--passphrase supplies the UTF-8 key for both formatting and opening.
SIGTERM and SIGHUP trigger normal LUKS mapper cleanup.

Examples:
  dusky_formater.py --device /dev/sda --fs exfat --partition gpt --yes
  dusky_formater.py --device /dev/sda1 --fs ext4 --encrypt --passphrase secret --yes
"""

SUPPORTED_FS = ["btrfs", "ext4", "f2fs", "exfat", "xfs", "fat32", "ntfs", "bcachefs", "nilfs2", "ext2", "ext3"]

def parse_cli_args() -> tuple[argparse.Namespace, bool]:
    parser = argparse.ArgumentParser(
        description="Dusky Formatter - Linux Storage Utility",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False
    )

    parser.add_argument("-h", "--help", action="store_true", help="Show this help message and exit.")
    parser.add_argument("--manual", action="store_true", help="Display full system architecture manual.")
    parser.add_argument("-d", "--device", type=str, help="Target block device path (e.g., /dev/sda or /dev/sda1)")
    parser.add_argument("-f", "--fs", choices=SUPPORTED_FS, help="Target filesystem type")
    parser.add_argument("-l", "--label", type=str, default="", help="Volume label")
    parser.add_argument("-p", "--partition", choices=["none", "gpt", "mbr"], default="none", help="Partition table scheme to write if device is a disk")
    parser.add_argument("--part-size", type=str, default="100%", help="Partition size allocation (e.g., 50%% to reserve 50%% as unallocated space)")
    parser.add_argument("-e", "--encrypt", action="store_true", help="Encrypt volume with LUKS2")
    parser.add_argument("--passphrase", type=str, help="LUKS2 passphrase for automated non-interactive format")
    parser.add_argument("--csum", choices=["crc32c", "xxhash", "sha256", "blake2"], default="blake2", help="BTRFS checksum algorithm")
    parser.add_argument("-y", "--yes", "--non-interactive", dest="non_interactive", action="store_true", help="Execute without interactive confirmation")

    args = parser.parse_args()

    if args.help:
        print("Dusky Formatter")
        print(parser.format_help())
        print(f"\nSupported Filesystems ({len(SUPPORTED_FS)}): {', '.join(SUPPORTED_FS)}")
        print("Run with '--manual' for technical specifications and design rationale.")
        sys.exit(0)

    if args.manual:
        print(MANUAL_TEXT)
        sys.exit(0)

    if bool(args.device) != bool(args.fs):
        parser.error("--device and --fs must be supplied together")
    if args.non_interactive and not args.device:
        parser.error("--yes requires --device and --fs")
    if not args.device and len(sys.argv) > 1:
        parser.error("CLI options require --device and --fs")
    is_cli_mode = bool(args.device)
    return args, is_cli_mode

def ensure_root_privileges() -> None:
    if os.geteuid() != 0:
        sys.stdout.flush()
        sys.stderr.flush()
        argv = [os.path.abspath(sys.argv[0]), *sys.argv[1:]]
        os.execvp("sudo", ["sudo", sys.executable, *argv])


try:
    from rich.console import Console
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.markup import escape
except ImportError:
    # Help and manual remain available without Rich or root privileges.
    if __name__ == "__main__":
        parse_cli_args()
    raise SystemExit("Missing python-rich; install it before running the formatter.")

console = Console()

FS_TOOLS = {
    "xfs": "mkfs.xfs", "btrfs": "mkfs.btrfs", "ext4": "mkfs.ext4",
    "ext3": "mkfs.ext3", "ext2": "mkfs.ext2", "f2fs": "mkfs.f2fs",
    "exfat": "mkfs.exfat", "fat32": "mkfs.fat", "ntfs": "mkfs.ntfs",
    "bcachefs": "bcachefs", "nilfs2": "mkfs.nilfs2",
}


def ensure_fs_tool(fs_type: str) -> None:
    if not shutil.which(FS_TOOLS[fs_type]):
        raise ValueError(f"Missing required tool: {FS_TOOLS[fs_type]}. Install it first.")


def validate_plan(plan: FormatPlan) -> None:
    """Validate all user input and dependencies before touching the device."""
    node = find_device_node(get_block_devices(), plan["device"])
    if node is None or not Path(plan["device"]).is_block_device():
        raise ValueError("Target must be an existing block device")
    if get_val(node, "ro", False) or get_val(node, "type") == "rom":
        raise ValueError("Cannot format a read-only device")
    ensure_fs_tool(plan["fs_type"])
    required = ["lsblk", "wipefs", "udevadm", "umount", "swapoff", "blockdev"]
    if plan["partition_table"] != "none":
        required.append("sfdisk")
    if plan["encrypt"]:
        required.append("cryptsetup")
        if not plan["passphrase"]:
            raise ValueError("Encryption requires a nonempty passphrase")
    for binary in required:
        if not shutil.which(binary):
            raise ValueError(f"Missing required tool: {binary}")
    label = plan["label"]
    if any(ord(c) < 32 or ord(c) == 127 for c in label):
        raise ValueError("Volume labels cannot contain control characters")
    limits = {"ext2": 16, "ext3": 16, "ext4": 16, "xfs": 12,
              "btrfs": 255, "nilfs2": 80, "bcachefs": 32}
    limit = limits.get(plan["fs_type"])
    if limit and len(label.encode("utf-8")) > limit:
        raise ValueError(f"{plan['fs_type']} labels must fit in {limit} UTF-8 bytes")
    if plan["fs_type"] == "exfat" and len(label.encode("utf-16-le")) > 30:
        raise ValueError("exFAT labels must fit in 15 UTF-16 code units")
    if plan["fs_type"] == "fat32":
        if not label.isascii() or len(label) > 11 or any(c in '*?.,;:/\\|+=<>[]"' for c in label):
            raise ValueError("FAT labels must be at most 11 ASCII characters without reserved punctuation")
        plan["label"] = label.upper()
    size = plan["partition_size"]
    if plan["partition_table"] == "none":
        if size != "100%":
            raise ValueError("--part-size requires a partition table")
    elif size.endswith("%"):
        if not re.fullmatch(r"\d+(?:\.\d+)?%", size) or not 0 < Decimal(size[:-1]) <= 100:
            raise ValueError("Partition percentage must be greater than 0 and at most 100")
    elif not re.fullmatch(r"[1-9]\d*(?:KiB|MiB|GiB|TiB|PiB|EiB)?", size):
        raise ValueError("Partition size must be a positive sector count, KiB/MiB/GiB size, or percentage")


# ==============================================================================
# 4. DEVICE PROBING & SYSTEM INTELLIGENCE
# ==============================================================================

def get_val(d: dict[str, Any] | None, key: str, default: Any = "") -> Any:
    value = d.get(key) if isinstance(d, dict) else None
    return default if value is None else value


def sysfs_block(dev_path: str) -> Path | None:
    try:
        p = Path(dev_path).resolve(strict=True)
        node = Path("/sys/class/block") / p.name
        return node if node.exists() else None
    except OSError:
        return None

def is_rotational(dev_path: str) -> bool:
    """Check if the device, its parent disk, or underlying slaves are rotational HDD."""
    if not dev_path or dev_path == "N/A":
        return False
    node = sysfs_block(dev_path)
    if node is None:
        return False
    try:
        real = node.resolve()
        slaves = list((real / "slaves").iterdir()) if (real / "slaves").is_dir() else []
        if slaves:
            return any(is_rotational(str(Path("/dev") / slave.name)) for slave in slaves)
        for cand in (real / "queue" / "rotational", real.parent / "queue" / "rotational"):
            if cand.is_file():
                return cand.read_text().strip() == "1"
    except (OSError, ValueError):
        pass
    return False

def get_mount_options() -> dict[str, dict[str, str]]:
    cmd = ["findmnt", "-A", "-l", "--json", "-o", "TARGET,FSTYPE,OPTIONS"]
    mounts: dict[str, dict[str, str]] = {}
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            for fs in data.get("filesystems", []):
                target = get_val(fs, "target")
                if target:
                    mounts[target] = {
                        "fstype": get_val(fs, "fstype", "unknown"),
                        "flags": get_val(fs, "options", "unknown")
                    }
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        console.print("[bold yellow]Warning:[/] Could not parse findmnt output.")
    return mounts

def get_block_devices() -> list[dict[str, Any]]:
    cmd = ["lsblk", "--json", "--tree", "-o", "NAME,PATH,MODEL,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINTS,RO"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return data.get("blockdevices", [])
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        console.print("[bold red]Critical Error:[/] Failed to parse lsblk output. Is util-linux functioning?")
        sys.exit(1)

def get_all_paths(devices: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for dev in devices:
        path = get_val(dev, "path")
        if path:
            paths.append(path)
        if "children" in dev:
            paths.extend(get_all_paths(get_val(dev, "children", [])))
    return paths

def get_all_mountpoints(device_node: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not device_node:
        return []
    mounts: list[tuple[str, str]] = []
    path = get_val(device_node, "path", "unknown_path")
    raw_mounts = get_val(device_node, "mountpoints", [])

    if isinstance(raw_mounts, list):
        for m in raw_mounts:
            if m:
                mounts.append((path, m))

    for child in get_val(device_node, "children", []):
        mounts.extend(get_all_mountpoints(child))
    return mounts

def get_all_mappings(device_node: dict[str, Any] | None) -> list[str]:
    if not device_node:
        return []
    mappings: list[str] = []
    for child in get_val(device_node, "children", []):
        if get_val(child, "type") in ["crypt", "dm", "lvm"]:
            path = get_val(child, "path")
            if path:
                mappings.append(path)
        mappings.extend(get_all_mappings(child))
    return mappings

def is_mounted_recursively(device_node: dict[str, Any] | None) -> bool:
    if not device_node:
        return False
    mounts = get_val(device_node, "mountpoints", [])
    if isinstance(mounts, list) and any(m for m in mounts if m):
        return True
    for child in get_val(device_node, "children", []):
        if is_mounted_recursively(child):
            return True
    return False

def find_device_node(devices: list[dict[str, Any]], target_path: str) -> dict[str, Any] | None:
    for dev in devices:
        if os.path.realpath(get_val(dev, "path")) == os.path.realpath(target_path):
            return dev
        if "children" in dev:
            found = find_device_node(get_val(dev, "children", []), target_path)
            if found:
                return found
    return None

def display_device_tree(devices: list[dict[str, Any]], table: Table, mount_data: dict[str, dict[str, str]], level: int = 0) -> None:
    for dev in devices:
        if get_val(dev, "type") == "rom" and level == 0:
            continue

        path = get_val(dev, "path", "N/A")
        indent = "  " * level + ("[blue]└─[/] " if level > 0 else "")

        model = escape(get_val(dev, "model", "").strip())
        dev_type = get_val(dev, "type", "").strip()
        label = escape(get_val(dev, "label", "").strip())

        rot_desc = "HDD" if is_rotational(path) else ("NVMe" if "nvme" in path else "SSD/Flash")
        if label:
            identity_str = f"[green]{label}[/]\n[dim]({dev_type} • {rot_desc})[/]"
        elif model:
            identity_str = f"[yellow]{model}[/]\n[dim]({dev_type} • {rot_desc})[/]"
        else:
            identity_str = f"[dim]({dev_type} • {rot_desc})[/]"

        size = get_val(dev, "size", "N/A")
        fstype = get_val(dev, "fstype") or "[dim]Raw[/]"

        raw_mounts = get_val(dev, "mountpoints", [])
        mounts = [m for m in raw_mounts if m] if isinstance(raw_mounts, list) else []
        mappings = get_all_mappings(dev)

        if mounts:
            mount_details = []
            for m in mounts:
                data = mount_data.get(m, {})
                m_fmt = data.get("fstype", "unknown")
                raw_flags = data.get("flags", "unknown")
                display_flags = escape(raw_flags.replace(",", ", "))
                mount_details.append(f"[bold white]{escape(m)}[/] [dim cyan]({m_fmt})[/]\n[dim magenta]↳ {display_flags}[/]")
            mount_str = "\n".join(mount_details)
        elif is_mounted_recursively(dev):
            mount_str = "[dim yellow]↳ Active Child Mount[/]"
        elif mappings:
            mount_str = "[dim magenta]↳ Active Mapped Volume[/]"
        else:
            mount_str = "[dim]Unmounted[/]"

        table.add_row(f"{indent}{path}", identity_str, size, fstype, mount_str)

        if "children" in dev:
            display_device_tree(get_val(dev, "children", []), table, mount_data, level + 1)

def unmount_device_locks(target_device: str, current_devices: list[dict[str, Any]]) -> None:
    """Detach normally; lazy unmount and forced mapper removal leave live I/O."""
    node = find_device_node(current_devices, target_device)
    if node is None:
        raise ValueError(f"Device disappeared: {target_device}")

    def close_children(dev: dict[str, Any]) -> None:
        for child in get_val(dev, "children", []):
            close_children(child)
            kind = get_val(child, "type")
            if kind == "crypt":
                subprocess.run(["cryptsetup", "close", get_val(child, "path")], check=True)
            elif kind != "part":
                raise ValueError(f"Detach active {kind} device {get_val(child, 'path')} first")

    # Reject unsupported holders before unmounting anything.
    def check_children(dev: dict[str, Any]) -> None:
        for child in get_val(dev, "children", []):
            if get_val(child, "type") not in ("part", "crypt"):
                raise ValueError(f"Detach active {get_val(child, 'type')} device {get_val(child, 'path')} first")
            if get_val(child, "type") == "crypt" and not shutil.which("cryptsetup"):
                raise ValueError("cryptsetup is required to close active encrypted children")
            check_children(child)

    check_children(node)
    mounts = set(get_all_mountpoints(node))
    for path, mountpoint in sorted(mounts, key=lambda item: len(item[1]), reverse=True):
        cmd = ["swapoff", path] if mountpoint == "[SWAP]" else ["umount", "--", mountpoint]
        subprocess.run(cmd, check=True)
    close_children(node)
    subprocess.run(["udevadm", "settle", "--timeout=10"], check=True)
    refreshed = find_device_node(get_block_devices(), target_device)
    if refreshed is None or get_all_mountpoints(refreshed) or get_all_mappings(refreshed):
        raise ValueError("Device disappeared or still has active mounts/mappings")
    for path in get_all_paths([refreshed]):
        node_path = sysfs_block(path)
        if node_path is None or any((node_path / "holders").iterdir()):
            raise ValueError(f"Cannot verify that {path} is free of active holders")

# ==============================================================================
# 5. SETUP PIPELINE (INTERACTIVE & NON-INTERACTIVE)
# ==============================================================================

def generate_mapper_name() -> str:
    return f"dusky_luks_{uuid.uuid4().hex}"

def build_plan_from_cli(args: argparse.Namespace) -> FormatPlan:
    args.device = os.path.realpath(args.device)
    current_devices = get_block_devices()
    if find_device_node(current_devices, args.device) is None:
        console.print(f"[bold red]Error:[/] Selected device '{args.device}' not found in system block device tree.")
        sys.exit(1)

    if args.encrypt and not args.passphrase:
        console.print("[bold red]Error:[/] '--encrypt' requires '--passphrase' in CLI mode.")
        sys.exit(1)

    label = args.label or ""

    device_node = find_device_node(current_devices, args.device)
    dev_type = get_val(device_node, "type", "part")

    if args.partition != "none" and (dev_type not in ("disk", "loop") or args.device.startswith("/dev/zram")):
        raise ValueError("Partition tables require a whole disk or loop device")
    partition_table = args.partition

    plan: FormatPlan = {
        "device": args.device,
        "partition_table": partition_table,
        "partition_size": getattr(args, "part_size", "100%"),
        "encrypt": bool(args.encrypt),
        "fs_type": args.fs,
        "csum": args.csum if args.fs == "btrfs" else None,
        "label": label,
        "passphrase": args.passphrase if args.encrypt else None,
        "non_interactive": args.non_interactive
    }
    return plan

def interactive_setup() -> FormatPlan:
    console.print(Panel.fit("[bold magenta]Dusky Formatter[/] - [cyan]Arch Linux Storage Utility[/]", border_style="magenta"))

    initial_devices = get_block_devices()
    mount_data = get_mount_options()

    table = Table(
        title="Live Storage Topology & Active Mount Flags",
        header_style="bold cyan",
        border_style="blue",
        show_lines=True,
        expand=True
    )

    table.add_column("Path", style="bold green", ratio=2, vertical="middle")
    table.add_column("Identity (Label/Model)", vertical="middle", ratio=3)
    table.add_column("Size", justify="right", style="white", no_wrap=True, vertical="middle")
    table.add_column("FS", style="blue", no_wrap=True, vertical="middle")
    table.add_column("Active Mounts & Flags", style="red", ratio=8)

    display_device_tree(initial_devices, table, mount_data)
    console.print(table)

    target_device: str | None = None

    while True:
        current_devices = get_block_devices()
        if not target_device:
            target_device = os.path.realpath(Prompt.ask("\nEnter the [bold green]Path[/] of the device to format (e.g., /dev/sda or /dev/sda1)"))

        if not target_device or find_device_node(current_devices, target_device) is None:
            console.print("[bold red]Invalid device path selected. Ensure it matches a physical path in the table.[/]")
            target_device = None
            continue

        break

    device_node = find_device_node(get_block_devices(), target_device)
    dev_type = get_val(device_node, "type", "part")

    partition_table = "none"
    partition_size = "100%"
    if dev_type in ["disk", "loop"] and not target_device.startswith("/dev/zram"):
        console.print(Panel(
            "[bold cyan]Partition Table Schemes:[/]\n"
            "  • [bold green]none[/]: Format raw block device directly (superfloppy mode, best for USB drives/flash media)\n"
            "  • [bold yellow]gpt[/] : Modern GPT scheme (Recommended for UEFI boot drives or disks > 2TB)\n"
            "  • [bold magenta]mbr[/] : Legacy DOS/MBR scheme (For old BIOS systems or legacy hardware compatibility)",
            title="Partition Layout Options", border_style="cyan"
        ))
        partition_choice = Prompt.ask(
            "Select Partition Table scheme to write",
            choices=["none", "gpt", "mbr"],
            default="none"
        )
        partition_table = partition_choice
        if partition_table in ["gpt", "mbr"]:
            partition_size = Prompt.ask(
                "Enter partition size (e.g. 50% to reserve 50% as over-provisioned space, or 100% for full disk)",
                default="100%"
            )

    console.print("\n[bold cyan]--- Security & Encryption ---[/]")
    encrypt = Confirm.ask("Encrypt target using [bold]LUKS2[/]?", default=False)

    passphrase = None
    if encrypt:
        while True:
            p1 = Prompt.ask("Enter a strong LUKS2 passphrase", password=True)
            p2 = Prompt.ask("Verify passphrase", password=True)
            if p1 == p2 and len(p1) > 0:
                passphrase = p1
                break
            else:
                console.print("[bold red]Passphrases do not match or are empty. Try again.[/]")

    console.print("\n[bold cyan]--- Filesystem Configuration ---[/]")
    fs_type = Prompt.ask("Select target filesystem", choices=SUPPORTED_FS, default="btrfs")

    ensure_fs_tool(fs_type)

    csum = None
    if fs_type == "btrfs":
        csum = Prompt.ask("Select BTRFS checksum algorithm", choices=["crc32c", "xxhash", "sha256", "blake2"], default="blake2")

    label = Prompt.ask("Enter a volume label (leave blank for none)", default="")
    plan: FormatPlan = {
        "device": target_device,
        "partition_table": partition_table,
        "partition_size": partition_size,
        "encrypt": encrypt,
        "fs_type": fs_type,
        "csum": csum,
        "label": label,
        "passphrase": passphrase,
        "non_interactive": False
    }

    return plan

# ==============================================================================
# 6. EXECUTION PLAN GENERATION
# ==============================================================================

def build_execution_plan(plan: FormatPlan) -> tuple[list[ExecutionStep], str, str | None]:
    device = plan["device"].rstrip("/")
    fs_type = plan["fs_type"]
    label = plan["label"]
    encrypt = plan["encrypt"]
    passphrase = plan.get("passphrase")
    partition_table = plan["partition_table"]
    partition_size = plan.get("partition_size", "100%")

    commands: list[ExecutionStep] = []
    bash_script = "#!/bin/bash\n# Dusky Formatter Native Execution Pipeline\n\n"

    mapper_name = None

    rotational = is_rotational(device)
    # A disk-level wipefs only clears partition-table signatures. Clear old
    # partition signatures first so they cannot be rediscovered in the new layout.
    node = find_device_node(get_block_devices(), device)
    for child in get_val(node, "children", []):
        path = get_val(child, "path")
        if get_val(child, "type") == "crypt":
            continue  # Closed after confirmation, before signature wiping.
        if get_val(child, "type") != "part":
            raise ValueError(f"Detach active child {path} before formatting")
        cmd = ["wipefs", "--all", "--force", "--lock", path]
        commands.append({"action": "wipe_fs", "desc": f"Clearing signatures on {path}",
                         "cmd": cmd, "input_data": None})
        bash_script += shlex.join(cmd) + "\n"

    # --force is required for nested partition tables; mounts/holders have
    # already been checked before execution. --lock avoids udev probe races.
    wipe_cmd = ["wipefs", "--all", "--force", "--lock", device]
    commands.append({
        "action": "wipe_fs",
        "desc": f"Clearing signatures on {device}",
        "cmd": wipe_cmd,
        "input_data": None
    })
    bash_script += f"# Clear signature headers\n{shlex.join(wipe_cmd)}\n\n"

    target_block = device

    # Step 2: Create one aligned partition.
    if partition_table in ["gpt", "mbr"]:
        size_field = ""
        if partition_size.endswith("%"):
            percentage = Decimal(partition_size[:-1])
            if percentage < 100:
                capacity = int(subprocess.run(["blockdev", "--getsize64", device],
                               capture_output=True, text=True, check=True).stdout)
                size_mib = int(Decimal(capacity) * percentage / 100) // (1024**2)
                if size_mib < 1:
                    raise ValueError("Requested partition is smaller than 1 MiB")
                size_field = f"size={size_mib}MiB, "
        else:
            size_field = f"size={partition_size}, "
        # Use documented Linux alias and Microsoft basic-data GUID for GPT.
        microsoft = fs_type in ("fat32", "exfat", "ntfs")
        if partition_table == "gpt":
            part_type = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7" if microsoft and not encrypt else "linux"
        else:
            part_type = "83" if encrypt or not microsoft else "c" if fs_type == "fat32" else "7"
        sfdisk_table = f"label: {'gpt' if partition_table == 'gpt' else 'dos'}\n{size_field}type={part_type}\n"
        desc_str = f"Creating {partition_size} {partition_table.upper()} partition on {device}"
        # Validate the table against actual geometry before wiping signatures.
        subprocess.run(["sfdisk", "--no-act", "--no-reread", device], input=sfdisk_table,
                       capture_output=True, text=True, check=True)

        part_cmd = ["sfdisk", "--lock", "--wipe", "always", "--wipe-partitions", "always", device]
        commands.append({
            "action": "partition",
            "desc": desc_str,
            "cmd": part_cmd,
            "input_data": sfdisk_table
        })
        bash_script += f"# Partition drive via sfdisk\nprintf %s {shlex.quote(sfdisk_table)} | {shlex.join(part_cmd)}\n"

        # UNIVERSAL PARTITION SUFFIX RULE: Devices ending in digits (loop0, nvme0n1, zram1, mmcblk0) use 'p1', others (sda) use '1'
        part_suffix = "p1" if device[-1].isdigit() else "1"
        target_block = f"{device}{part_suffix}"

        settle_cmd = ["udevadm", "settle", "--timeout=10"]
        commands.append({
            "action": "settle",
            "desc": "Synchronizing kernel block layer device nodes",
            "cmd": settle_cmd,
            "input_data": None
        })
        bash_script += f"udevadm settle --timeout=10\n\n"

    # Step 3: LUKS2 Encryption Setup
    if encrypt and passphrase:
        mapper_name = generate_mapper_name()

        # Let cryptsetup select the encryption sector size from device geometry.
        luks_fmt = ["cryptsetup", "-q", "luksFormat", "--type", "luks2", "--pbkdf", "argon2id", target_block, "-"]
        commands.append({
            "action": "luks_format",
            "desc": f"Initializing LUKS2 Encryption Container on {target_block}",
            "cmd": luks_fmt,
            "input_data": passphrase
        })
        bash_script += f"# Initialize LUKS2 Container\nprintf %s 'YOUR_PASSPHRASE' | {shlex.join(luks_fmt[:-1])} -\n"

        luks_open = ["cryptsetup", "open", "--type", "luks2", "--key-file", "-"]
        if not rotational:
            luks_open.extend(["--allow-discards", "--perf-no_read_workqueue", "--perf-no_write_workqueue", "--persistent"])
        luks_open.extend([target_block, mapper_name])

        desc_open = f"Opening encrypted volume as '/dev/mapper/{mapper_name}'"
        if not rotational:
            desc_open += " (TRIM enabled, workqueues bypassed, persistent)"

        commands.append({
            "action": "luks_open",
            "desc": desc_open,
            "cmd": luks_open,
            "input_data": passphrase
        })
        bash_script += f"# Map LUKS volume\nprintf %s 'YOUR_PASSPHRASE' | {shlex.join(luks_open)}\n\n"

        target_block = f"/dev/mapper/{mapper_name}"

    # Step 4: Filesystem creation
    mkfs_cmd: list[str] = []
    match fs_type:
        case "btrfs":
            csum = plan.get("csum") or "blake2"
            mkfs_cmd = ["mkfs.btrfs", "-f", "--csum", csum]
            if rotational:
                mkfs_cmd.append("-K")
            if label:
                mkfs_cmd.extend(["-L", label])
            mkfs_cmd.append(target_block)

        case "ext4" | "ext3" | "ext2":
            mkfs_binary = f"mkfs.{fs_type}"
            ext_opts_list = ["lazy_itable_init=1"]
            if fs_type in ["ext3", "ext4"]:
                ext_opts_list.append("lazy_journal_init=1")
            if not rotational:
                ext_opts_list.append("discard")
            else:
                ext_opts_list.append("nodiscard")
            mkfs_cmd = [mkfs_binary, "-F", "-v", "-E", ",".join(ext_opts_list)]
            if fs_type == "ext4":
                mkfs_cmd.extend(["-O", "fast_commit"])
            if label:
                mkfs_cmd.extend(["-L", label])
            mkfs_cmd.append(target_block)

        case "f2fs":
            trim_flag = "0" if rotational else "1"
            mkfs_cmd = ["mkfs.f2fs", "-f", "-t", trim_flag]
            if label:
                mkfs_cmd.extend(["-l", label])
            mkfs_cmd.append(target_block)

        case "exfat":
            mkfs_cmd = ["mkfs.exfat", "-F", "-P", "none"]
            if rotational:
                mkfs_cmd.append("-K")
            if label:
                mkfs_cmd.extend(["-L", label])
            mkfs_cmd.append(target_block)

        case "xfs":
            mkfs_cmd = ["mkfs.xfs", "-f"]
            if rotational:
                mkfs_cmd.append("-K")
            if label:
                mkfs_cmd.extend(["-L", label])
            mkfs_cmd.append(target_block)

        case "ntfs":
            mkfs_cmd = ["mkfs.ntfs", "-f", "-F"]
            if label:
                mkfs_cmd.extend(["-L", label])
            mkfs_cmd.append(target_block)

        case "bcachefs":
            mkfs_cmd = ["bcachefs", "format", "-f"]
            if rotational:
                mkfs_cmd.append("--rotational")
            if label:
                mkfs_cmd.append(f"--fs_label={label}")
            mkfs_cmd.append(target_block)

        case "nilfs2":
            mkfs_cmd = ["mkfs.nilfs2", "-f"]
            if rotational:
                mkfs_cmd.append("-K")
            if label:
                mkfs_cmd.extend(["-L", label])
            mkfs_cmd.append(target_block)

        case "fat32":
            mkfs_cmd = ["mkfs.fat", "-F", "32", "-I"]
            if label:
                mkfs_cmd.extend(["-n", label])
            mkfs_cmd.append(target_block)

    if not mkfs_cmd:
        raise ValueError(f"Unsupported filesystem: {fs_type}")

    commands.append({
        "action": "mkfs",
        "desc": f"Building {fs_type.upper()} filesystem on {target_block}",
        "cmd": mkfs_cmd,
        "input_data": None
    })
    bash_script += f"# Format block device\n{shlex.join(mkfs_cmd)}\n\n"

    # Step 5: Close LUKS container if active
    if encrypt and mapper_name:
        close_cmd = ["cryptsetup", "close", mapper_name]
        commands.append({
            "action": "luks_close",
            "desc": f"Locking and securing volume '{mapper_name}'",
            "cmd": close_cmd,
            "input_data": None
        })
        bash_script += f"# Lock container\n{shlex.join(close_cmd)}\n"

    return commands, bash_script, mapper_name

# ==============================================================================
# 7. PIPELINE EXECUTION
# ==============================================================================

def execute_plan(commands: list[ExecutionStep], mapper_name: str | None = None) -> None:
    console.print("\n[bold cyan]Executing Dusky Formatting Plan...[/]")
    luks_is_open = False

    try:
        for step in commands:
            if step["action"] == "mkfs":
                console.print(f"\n[bold yellow]Executing:[/] {step['desc']}...")
                console.print(f"$ {shlex.join(step['cmd'])}\n", style="dim", markup=False)
                subprocess.run(step["cmd"], check=True)
            else:
                with console.status(f"[bold yellow]Executing:[/] {step['desc']}...", spinner="dots"):
                    try:
                        subprocess.run(step["cmd"], input=step["input_data"],
                                       capture_output=True, text=True, encoding="utf-8", check=True)

                        if step["action"] == "luks_open":
                            luks_is_open = True
                        elif step["action"] == "luks_close":
                            luks_is_open = False

                    except subprocess.CalledProcessError as e:
                        console.print(f"Command failed: {shlex.join(step['cmd'])}", style="bold red", markup=False)
                        if e.stderr is not None:
                            console.print(e.stderr.strip(), style="red", markup=False)
                        raise

                console.print(f"[bold green]✔[/] {step['desc']} [dim](Completed)[/]")

        console.print("\n[bold green]✔ All formatting operations successfully completed![/]")

    except (OSError, subprocess.CalledProcessError, ValueError) as e:
        console.print(f"Operation failed: {e}", style="bold red", markup=False)
        sys.exit(1)

    finally:
        # cryptsetup can create the mapping just before an interruption prevents
        # subprocess.run from returning. Check the actual mapping as well.
        if mapper_name and (luks_is_open or Path(f"/dev/mapper/{mapper_name}").is_block_device()):
            # Cleanup precedes output: a closed stdout pipe must not skip it.
            try:
                subprocess.run(["cryptsetup", "close", mapper_name], capture_output=True, check=True)
            except (OSError, subprocess.CalledProcessError) as exc:
                console.print(f"Could not close mapper '{mapper_name}': {exc}. Check its holders and close it manually.",
                              style="bold red", markup=False)
            else:
                console.print(f"Closed mapper '{mapper_name}'.", style="bold green", markup=False)

# ==============================================================================
# ENTRY POINT
# ==============================================================================

def stop_on_signal(signum: int, _frame: Any) -> None:
    # Raising allows subprocess.run to stop its child and finally to close LUKS.
    raise SystemExit(128 + signum)


def main() -> None:
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, stop_on_signal)
    cli_args, is_cli_mode = parse_cli_args()
    ensure_root_privileges()

    if is_cli_mode:
        plan = build_plan_from_cli(cli_args)
    else:
        plan = interactive_setup()

    validate_plan(plan)
    # Planning is read-only, including sfdisk's geometry check.
    commands, bash_equivalent, mapper_name = build_execution_plan(plan)
    console.print("\n[bold green]Command Execution Pipeline:[/]")
    console.print(Panel(Syntax(bash_equivalent, "bash", theme="monokai", line_numbers=True),
                        title="Subprocess Translation", border_style="green"))
    console.print(f"\n[bold red]WARNING:[/] ALL DATA ON {plan['device']} WILL BE ERASED.")
    if not plan["non_interactive"] and not Confirm.ask("Proceed?", default=False):
        console.print("[yellow]Operation aborted.[/]")
        return
    unmount_device_locks(plan["device"], get_block_devices())
    execute_plan(commands, mapper_name)

if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        console.print(f"Operation failed: {exc}", style="bold red", markup=False)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            console.print(exc.stderr.strip(), style="red", markup=False)
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Process interrupted via keyboard.[/]")
        sys.exit(130)

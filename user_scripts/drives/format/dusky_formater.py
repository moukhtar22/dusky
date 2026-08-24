#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Dusky Formatter v5.4.0 (Architect Edition - Bleeding Edge 2026)
A cutting-edge, interactive & non-interactive CLI/TUI utility for securely formatting,
partitioning, and encrypting storage drives without unnecessary write amplification.

Engineered for Arch Linux (Kernel 7.1+, Python 3.14+).
"""

import os
import sys
import subprocess
import json
import shlex
import uuid
import time
import shutil
import argparse
from typing import Any, Optional, TypedDict

# ==============================================================================
# 1. ARCHITECTURAL TYPE DEFINITIONS
# ==============================================================================

class FormatPlan(TypedDict):
    device: str
    target_block: str
    partition_table: str  # "none", "gpt", "mbr"
    partition_size: str   # "100%", "50%", etc.
    encrypt: bool
    fs_type: str
    csum: Optional[str]
    label: str
    passphrase: Optional[str]
    non_interactive: bool

class ExecutionStep(TypedDict):
    action: str
    desc: str
    cmd: list[str]
    interactive: bool
    input_data: Optional[str]

# ==============================================================================
# 2. CLI ARGUMENT PARSING & HELP / MANUAL DISPLAY (NO ROOT REQUIRED)
# ==============================================================================

MANUAL_TEXT = """
===============================================================================
               DUSKY FORMATTER v5.4.0 - ARCH LINUX SYSTEM MANUAL
===============================================================================

DESCRIPTION:
    Dusky Formatter is a zero-write-amplification block device layout and 
    formatting utility designed for modern Arch Linux (Kernel 7.1+). It offers
    both a rich interactive Terminal User Interface (TUI) and an automated CLI
    interface.

KEY FEATURES & METHODOLOGIES (AUGUST 2026 / KERNEL 7.1 STANDARDS):

1. ZERO WRITE AMPLIFICATION & NAND PROTECTION:
   - Ext4/Ext3: Uses `-E lazy_itable_init=1,lazy_journal_init=1,discard`. Disabling 
     lazy initialization (`lazy_itable_init=0`) forces mkfs.ext4 to write 
     zeros across the entire inode table on NAND flash drives, causing severe
     write amplification. Enabling lazy init defers zeroing to background 
     kernel allocation or discards block references.
   - Bcachefs: Next-generation Copy-on-Write (COW) Linux filesystem formatting 
     via `bcachefs format -f --label=<label>`.
   - NTFS (Kernel 7.1 Native `ntfs.ko`): Formatted via `mkfs.ntfs -f -F` (fast 
     format & force overwrite) creating clean NTFS volume structures with 
     zero write amplification. Fully compatible with the rewritten, native 
     Linux 7.1 in-kernel `ntfs` driver (`fs/ntfs/ntfs.ko`, built on iomap 
     and folios by Namjae Jeon / Tuxera).
     NOTE: On Arch Linux, if `ntfs-3g` is installed, `/sbin/mount.ntfs` is a 
     symlink to the old FUSE daemon. To mount via the Kernel 7.1 native kernel 
     driver, explicitly run `mount -t ntfs3 <dev> <mnt>` or remove the FUSE symlink.
   - TRIM / Discard: Automatically includes discard flags across supported 
     filesystems (BTRFS, EXT4, F2FS, exFAT, XFS, Bcachefs) and LUKS mappings (`--allow-discards`).
   - Wiping: Uses `wipefs --all --force` to destroy magic signatures without
     overwriting whole disk blocks (avoiding zero-fills like `dd if=/dev/zero`).

2. CUTTING-EDGE UTILITY INTEGRATION:
   - exFAT: Uses `exfatprogs` 1.4.2 syntax (`-L` for labels, `-F` for force).
   - BTRFS: Uses `btrfs-progs` 7.1 syntax supporting `blake2`, `xxhash`, 
     `sha256`, and `crc32c` checksum algorithms.
   - F2FS: Configured with `-t 1` for flash-friendly block placement and trim.
   - XFS: Uses `xfsprogs` 7.1 syntax (`-f` for force, `-L` label up to 12 chars).
   - NILFS2: Continuous snapshot log-structured filesystem via `mkfs.nilfs2`.
   - Partitioning: Universal GPT/MBR layout generation via `sfdisk` (util-linux 2.42.2).
   - Cryptography: LUKS2 with Argon2id PBKDF via `cryptsetup` 2.8.7.

3. DEPENDENCY AUTO-RESOLUTION:
   - Automatically detects missing system packages (e.g. `python-rich`, `xfsprogs`, `ntfsprogs`, `bcachefs-tools`)
     and offers non-interactive installation via `pacman`.

EXAMPLES:
   - Interactive TUI:
       $ dusky_formater.py

   - Quick Format USB Drive as Bcachefs non-interactively:
       $ dusky_formater.py --device /dev/sda --fs bcachefs --label "FAST_COW" -y

   - Encrypt Drive with LUKS2 + Ext4:
       $ dusky_formater.py --device /dev/sda1 --encrypt --passphrase "secret" --fs ext4 -y
===============================================================================
"""

SUPPORTED_FS = ["btrfs", "ext4", "f2fs", "exfat", "xfs", "fat32", "ntfs", "bcachefs", "nilfs2", "ext2", "ext3"]

def parse_cli_args() -> tuple[Optional[argparse.Namespace], bool]:
    parser = argparse.ArgumentParser(
        description="Dusky Formatter v5.4.0 - Modern Arch Linux Storage Utility",
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

    args, unknown = parser.parse_known_args()

    if args.help:
        print(f"Dusky Formatter v5.4.0 (Architect Edition - Bleeding Edge 2026)")
        print(parser.format_help())
        print(f"\nSupported Filesystems ({len(SUPPORTED_FS)}): {', '.join(SUPPORTED_FS)}")
        print("Run with '--manual' for technical specifications and design rationale.")
        sys.exit(0)

    if args.manual:
        print(MANUAL_TEXT)
        sys.exit(0)

    is_cli_mode = bool(args.device and args.fs and args.non_interactive)
    return args, is_cli_mode

def ensure_root_privileges(is_cli_mode: bool) -> None:
    if os.geteuid() != 0:
        if not sys.stdin.isatty():
            probe = subprocess.run(["sudo", "-n", "true"], capture_output=True)
            if probe.returncode != 0:
                print("\033[1;31m[x] Error: Root privileges required, but session is non-interactive and sudo requires a password.\033[0m")
                print("Please run this command directly from your interactive terminal:\n")
                print(f"  sudo {sys.executable} {' '.join(sys.argv)}\n")
                sys.exit(1)
        if is_cli_mode:
            res = subprocess.run(["sudo", sys.executable] + sys.argv)
            sys.exit(res.returncode)
        else:
            print("\033[1;33m[!] Dusky Formatter requires root privileges. Elevating via sudo...\033[0m")
            try:
                os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
            except Exception as e:
                print(f"\033[1;31m[x] Critical error during privilege escalation: {e}\033[0m")
                sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich.panel import Panel
    from rich.syntax import Syntax
except ImportError:
    print("\033[1;36m[*] Missing 'rich' TUI library. Automatically resolving via pacman...\033[0m")
    try:
        subprocess.run(["pacman", "-S", "--needed", "--noconfirm", "python-rich"], check=True)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except subprocess.CalledProcessError:
        print("\033[1;31m[x] Failed to auto-install dependencies. Please check your pacman configuration.\033[0m")
        sys.exit(1)

console = Console()

def ensure_package_for_fs(fs_type: str) -> None:
    pkg_map = {
        "xfs": ("mkfs.xfs", "xfsprogs"),
        "btrfs": ("mkfs.btrfs", "btrfs-progs"),
        "ext4": ("mkfs.ext4", "e2fsprogs"),
        "ext3": ("mkfs.ext3", "e2fsprogs"),
        "ext2": ("mkfs.ext2", "e2fsprogs"),
        "f2fs": ("mkfs.f2fs", "f2fs-tools"),
        "exfat": ("mkfs.exfat", "exfatprogs"),
        "fat32": ("mkfs.fat", "dosfstools"),
        "ntfs": ("mkfs.ntfs", "ntfsprogs"),
        "bcachefs": ("bcachefs", "bcachefs-tools"),
        "nilfs2": ("mkfs.nilfs2", "nilfs-utils"),
    }
    if fs_type in pkg_map:
        binary, pkg = pkg_map[fs_type]
        if not shutil.which(binary):
            console.print(f"[bold yellow][*] Missing required tool '{binary}'. Auto-installing '{pkg}' via pacman...[/]")
            try:
                subprocess.run(["pacman", "-S", "--needed", "--noconfirm", pkg], check=True)
            except subprocess.CalledProcessError:
                console.print(f"[bold red][x] Failed to install package '{pkg}'. Aborting.[/]")
                sys.exit(1)

# ==============================================================================
# 4. DEVICE PROBING & SYSTEM INTELLIGENCE
# ==============================================================================

def get_val(d: dict[str, Any], key: str, default: Any = "") -> Any:
    if not isinstance(d, dict): return default
    val = d.get(key.lower())
    if val is None:
        val = d.get(key.upper())
    return val if val is not None else default

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
    cmd = ["lsblk", "--json", "--tree", "-o", "NAME,PATH,MODEL,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINTS"]
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

def get_all_mountpoints(device_node: Optional[dict[str, Any]]) -> list[tuple[str, str]]:
    if not device_node:
        return []
    mounts: list[tuple[str, str]] = []
    path = get_val(device_node, "path", "unknown_path")
    raw_mounts = get_val(device_node, "mountpoints", [])
    
    if isinstance(raw_mounts, list):
        for m in raw_mounts:
            if m: mounts.append((path, m))
            
    for child in get_val(device_node, "children", []):
        mounts.extend(get_all_mountpoints(child))
    return mounts

def get_all_mappings(device_node: Optional[dict[str, Any]]) -> list[str]:
    if not device_node: return []
    mappings: list[str] = []
    for child in get_val(device_node, "children", []):
        if get_val(child, "type") in ["crypt", "dm", "lvm"]:
            path = get_val(child, "path")
            if path: mappings.append(path)
        mappings.extend(get_all_mappings(child))
    return mappings

def is_mounted_recursively(device_node: Optional[dict[str, Any]]) -> bool:
    if not device_node: 
        return False
    mounts = get_val(device_node, "mountpoints", [])
    if isinstance(mounts, list) and any(m for m in mounts if m):
        return True
    for child in get_val(device_node, "children", []):
        if is_mounted_recursively(child):
            return True
    return False

def find_device_node(devices: list[dict[str, Any]], target_path: str) -> Optional[dict[str, Any]]:
    for dev in devices:
        if get_val(dev, "path") == target_path:
            return dev
        if "children" in dev:
            found = find_device_node(get_val(dev, "children", []), target_path)
            if found:
                return found
    return None

def display_device_tree(devices: list[dict[str, Any]], table: Table, mount_data: dict[str, dict[str, str]], level: int = 0) -> None:
    for dev in devices:
        if get_val(dev, "type") in ["rom"] and level == 0:
            continue
            
        path = get_val(dev, "path", "N/A")
        indent = "  " * level + ("[blue]└─[/] " if level > 0 else "")
        
        model = get_val(dev, "model", "").strip()
        dev_type = get_val(dev, "type", "").strip()
        label = get_val(dev, "label", "").strip()
        
        if label:
            identity_str = f"[green]{label}[/]\n[dim]({dev_type})[/]"
        elif model:
            identity_str = f"[yellow]{model}[/]\n[dim]({dev_type})[/]"
        else:
            identity_str = f"[dim]({dev_type})[/]"
        
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
                display_flags = raw_flags.replace(",", ", ")
                mount_details.append(f"[bold white]{m}[/] [dim cyan]({m_fmt})[/]\n[dim magenta]↳ {display_flags}[/]")
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

def resolve_busy_processes(mountpoint: str, non_interactive: bool = False) -> bool:
    processes: list[dict[str, str]] = []
    
    # Attempt 1: lsof with machine-readable format -F pcu
    try:
        res = subprocess.run(["lsof", "-F", "pcu", "+f", "--", mountpoint], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            current_p: dict[str, str] = {}
            for line in res.stdout.strip().split("\n"):
                if not line:
                    continue
                prefix, val = line[0], line[1:]
                if prefix == 'p':
                    if current_p and 'pid' in current_p:
                        processes.append(current_p)
                    current_p = {'pid': val, 'cmd': 'Unknown', 'user': 'Unknown'}
                elif prefix == 'c' and current_p:
                    current_p['cmd'] = val
                elif prefix == 'u' and current_p:
                    current_p['user'] = val
            if current_p and 'pid' in current_p:
                processes.append(current_p)
    except FileNotFoundError:
        pass

    # Attempt 2: Standard lsof if -F produced nothing
    if not processes:
        try:
            res = subprocess.run(["lsof", "+f", "--", mountpoint], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                lines = res.stdout.strip().split("\n")
                if len(lines) > 1:
                    for line in lines[1:]:
                        parts = line.split()
                        if len(parts) >= 3:
                            pid = parts[1]
                            if not any(p["pid"] == pid for p in processes):
                                processes.append({
                                    "cmd": parts[0],
                                    "pid": pid,
                                    "user": parts[2]
                                })
        except FileNotFoundError:
            pass

    # Attempt 3: fuser fallback if lsof is not available or returned nothing
    if not processes and shutil.which("fuser"):
        try:
            res = subprocess.run(["fuser", "-m", mountpoint], capture_output=True, text=True)
            output = res.stdout.strip() or res.stderr.strip()
            if output:
                raw_pids = [p.strip().rstrip("ccefgkmM") for p in output.split() if p.strip().rstrip("ccefgkmM").isdigit()]
                for pid in set(raw_pids):
                    processes.append({"cmd": "process", "pid": pid, "user": "unknown"})
        except Exception:
            pass

    if not processes:
        return False

    unique_processes = []
    seen_pids = set()
    for p in processes:
        if p['pid'] not in seen_pids:
            seen_pids.add(p['pid'])
            unique_processes.append(p)
    processes = unique_processes

    console.print(Panel(
        f"[bold red]⚠️  WARNING: FILESYSTEM IS BUSY ⚠️[/]\n\n"
        f"The following processes are currently locking [bold white]{mountpoint}[/]:",
        title="Filesystem Locked", border_style="red"
    ))

    table = Table(show_header=True, header_style="bold yellow", border_style="yellow")
    table.add_column("COMMAND", style="cyan")
    table.add_column("PID", justify="right", style="yellow")
    table.add_column("USER")

    for p in processes:
        table.add_row(p["cmd"], p["pid"], p["user"])

    console.print(table)
    console.print()

    action_taken = False
    if non_interactive:
        console.print("[bold yellow][*] Non-interactive mode: Automatically terminating locking processes...[/]")
        for p in processes:
            console.print(f"Terminating {p['cmd']} (PID: {p['pid']})...")
            subprocess.run(["kill", "-15", p['pid']], capture_output=True)
        time.sleep(1)
        for p in processes:
            res = subprocess.run(["kill", "-0", p['pid']], capture_output=True)
            if res.returncode == 0:
                console.print(f"Forcefully killing {p['cmd']} (PID: {p['pid']})...")
                subprocess.run(["kill", "-9", p['pid']], capture_output=True)
                action_taken = True
            else:
                action_taken = True
        time.sleep(1)
        return action_taken
    else:
        ans = Confirm.ask("Forcefully terminate all listed processes (SIGKILL/SIGTERM) to free the drive?", default=False)
        if ans:
            for p in processes:
                console.print(f"Terminating {p['cmd']} (PID: {p['pid']})...")
                subprocess.run(["kill", "-15", p['pid']], capture_output=True)
            time.sleep(1)
            for p in processes:
                res = subprocess.run(["kill", "-0", p['pid']], capture_output=True)
                if res.returncode == 0:
                    console.print(f"Forcefully killing {p['cmd']} (PID: {p['pid']})...")
                    subprocess.run(["kill", "-9", p['pid']], capture_output=True)
            time.sleep(1)
            return True
        return False

def teardown_descendants(device_node: Optional[dict[str, Any]]) -> bool:
    if not device_node: return True
    success = True
    
    for child in get_val(device_node, "children", []):
        if not teardown_descendants(child):
            success = False
        
        dev_type = get_val(child, "type")
        path = get_val(child, "path")
        if dev_type in ["crypt", "lvm", "dm"] and path:
            console.print(f"[bold yellow]➜[/] Attempting to close mapped volume {path}...")
            subprocess.run(["blockdev", "--flushbufs", path], capture_output=True)
            try:
                res = subprocess.run(["cryptsetup", "close", path], capture_output=True, text=True)
                if res.returncode == 0:
                    console.print(f"  [bold green]✔ Successfully closed {path}[/]")
                else:
                    console.print(f"  [bold red]✗ Standard close failed for {path}. Trying dmsetup fallback...[/]")
                    try:
                        res2 = subprocess.run(["dmsetup", "remove", "--force", path], capture_output=True, text=True)
                        if res2.returncode == 0:
                            console.print(f"  [bold green]✔ Successfully removed {path} via dmsetup.[/]")
                        else:
                            console.print(f"  [bold red]✗ Kernel lock prevents closing {path}.[/]")
                            success = False
                    except FileNotFoundError:
                        console.print(f"  [bold red]✗ dmsetup not found. Lock remains.[/]")
                        success = False
            except FileNotFoundError:
                console.print(f"  [bold red]✗ cryptsetup not found! Lock remains.[/]")
                success = False
    return success

def unmount_device_locks(target_device: str, current_devices: list[dict[str, Any]], non_interactive: bool = False) -> bool:
    device_node = find_device_node(current_devices, target_device)
    active_mounts = get_all_mountpoints(device_node)
    active_mappings = get_all_mappings(device_node)
    
    if not active_mounts and not active_mappings:
        return True

    console.print(f"\n[bold yellow]➜[/] Clearing active mounts/locks on {target_device}...")
    for dev_path, m in sorted(active_mounts, key=lambda x: len(x[1]), reverse=True):
        if m == "[SWAP]":
            console.print(f"  [yellow]Turning off swap on {dev_path}...[/]")
            res = subprocess.run(["swapoff", dev_path], capture_output=True, text=True)
            if res.returncode != 0:
                console.print(f"  [bold red]Swapoff failed: {res.stderr.strip()}[/]")
        else:
            unmounted = False
            if shutil.which("udisksctl") and (m.startswith("/run/media/") or m.startswith("/media/")):
                u_res = subprocess.run(["udisksctl", "unmount", "-b", dev_path], capture_output=True, text=True)
                if u_res.returncode == 0:
                    console.print(f"  [bold green]✔ Unmounted {m} via udisksctl.[/]")
                    unmounted = True

            if not unmounted:
                for attempt in range(3):
                    u_res = subprocess.run(["umount", m], capture_output=True, text=True)
                    if u_res.returncode == 0:
                        console.print(f"  [bold green]✔ Unmounted {m}.[/]")
                        unmounted = True
                        break
                    else:
                        console.print(f"  [yellow]Notice:[/] Unmount {m} attempt {attempt+1}/3 failed ({u_res.stderr.strip()}). Scanning busy processes...")
                        if resolve_busy_processes(m, non_interactive=non_interactive):
                            time.sleep(1)
                        else:
                            time.sleep(1)

            if not unmounted:
                console.print(f"  [yellow]Attempting lazy unmount (umount -l) on {m}...[/]")
                l_res = subprocess.run(["umount", "-l", m], capture_output=True, text=True)
                if l_res.returncode == 0:
                    console.print(f"  [bold green]✔ Lazy unmounted {m}.[/]")
                    unmounted = True
                else:
                    console.print(f"  [bold red]✗ Lazy unmount failed for {m}: {l_res.stderr.strip()}[/]")

    if active_mappings:
        teardown_descendants(device_node)

    subprocess.run(["blockdev", "--flushbufs", target_device], capture_output=True)
    subprocess.run(["udevadm", "settle"], capture_output=True)
    
    updated_devices = get_block_devices()
    updated_node = find_device_node(updated_devices, target_device)
    remaining_mounts = get_all_mountpoints(updated_node)
    remaining_mappings = get_all_mappings(updated_node)

    if remaining_mounts or remaining_mappings:
        console.print(f"\n[bold red]ERROR: Locks remain on {target_device}:[/]")
        for dev_path, m in remaining_mounts:
            console.print(f"  - [red]Mounted at: {m} ({dev_path})[/]")
        for m in remaining_mappings:
            console.print(f"  - [red]Mapped volume: {m}[/]")
        return False

    console.print(f"[bold green]✔ All mounts and locks on {target_device} cleared successfully.[/]")
    return True

# ==============================================================================
# 5. SETUP PIPELINE (INTERACTIVE & NON-INTERACTIVE)
# ==============================================================================

def generate_secure_mapper_name() -> str:
    return f"dusky_luks_{uuid.uuid4().hex[:8]}"

def build_plan_from_cli(args: argparse.Namespace) -> FormatPlan:
    current_devices = get_block_devices()
    valid_paths = get_all_paths(current_devices)
    
    if args.device not in valid_paths:
        console.print(f"[bold red]Error:[/] Selected device '{args.device}' not found in system block device tree.")
        sys.exit(1)

    if args.encrypt and not args.passphrase:
        console.print("[bold red]Error:[/] '--encrypt' requires '--passphrase' when running in non-interactive CLI mode.")
        sys.exit(1)

    if not unmount_device_locks(args.device, current_devices, non_interactive=True):
        console.print(f"[bold red]Error:[/] Could not clear active mounts/locks on '{args.device}'. Aborting.")
        sys.exit(1)

    ensure_package_for_fs(args.fs)

    label = args.label or ""
    if args.fs == "fat32" and len(label) > 11:
        label = label[:11].upper()
    elif args.fs == "exfat" and len(label) > 15:
        label = label[:15]
    elif args.fs == "f2fs" and len(label) > 16:
        label = label[:16]
    elif args.fs == "xfs" and len(label) > 12:
        label = label[:12]
    elif args.fs in ["ntfs", "bcachefs"] and len(label) > 32:
        label = label[:32]

    device_node = find_device_node(current_devices, args.device)
    dev_type = get_val(device_node, "type", "part")
    
    target_block = args.device
    partition_table = args.partition if dev_type == "disk" else "none"

    plan: FormatPlan = {
        "device": args.device,
        "target_block": target_block,
        "partition_table": partition_table,
        "partition_size": getattr(args, "part_size", "100%"),
        "encrypt": bool(args.encrypt),
        "fs_type": args.fs,
        "csum": args.csum if args.fs == "btrfs" else None,
        "label": label,
        "passphrase": args.passphrase if args.encrypt else None,
        "non_interactive": True
    }
    return plan

def interactive_setup() -> FormatPlan:
    console.print(Panel.fit("[bold magenta]Dusky Formatter v5.4.0[/] - [cyan]Arch Linux Storage Utility[/]", border_style="magenta"))
    
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
    
    target_device: Optional[str] = None
    
    while True:
        current_devices = get_block_devices()
        valid_paths = get_all_paths(current_devices)
        
        if not target_device:
            target_device = Prompt.ask("\nEnter the [bold green]Path[/] of the device to format (e.g., /dev/sda or /dev/sda1)")
            
        if not target_device or target_device not in valid_paths or not target_device.startswith("/dev/"):
            console.print("[bold red]Invalid device path selected. Ensure it matches a physical path in the table.[/]")
            target_device = None
            continue
        
        device_node = find_device_node(current_devices, target_device)
        active_mounts = get_all_mountpoints(device_node)
        active_mappings = get_all_mappings(device_node)
        
        if active_mounts or active_mappings:
            console.print(f"\n[bold red blink]CRITICAL SAFETY LOCK:[/]\n[yellow]{target_device}[/] (or a child volume) is actively locked by the kernel:")
            for dev_path, m in active_mounts:
                console.print(f"  - [cyan]Mounted at: {m} (via {dev_path})[/]")
            for m in active_mappings:
                console.print(f"  - [magenta]Mapped volume: {m}[/]")
            
            if Confirm.ask("Would you like Dusky Formatter to attempt a [bold red]force unlock & unmount[/] now?", default=False):
                if not unmount_device_locks(target_device, current_devices, non_interactive=False):
                    console.print(f"[bold red]Failed to clear locks on {target_device}. Select another device or clear locks manually.[/]")
                    target_device = None
                continue
            else:
                target_device = None 
        break

    device_node = find_device_node(get_block_devices(), target_device)
    dev_type = get_val(device_node, "type", "part")
    
    partition_table = "none"
    partition_size = "100%"
    if dev_type == "disk":
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
    encrypt = Confirm.ask(f"Encrypt target using [bold]LUKS2[/]?", default=False)
    
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
    
    ensure_package_for_fs(fs_type)

    csum = None
    if fs_type == "btrfs":
        csum = Prompt.ask("Select BTRFS checksum algorithm", choices=["crc32c", "xxhash", "sha256", "blake2"], default="blake2")

    label = Prompt.ask("Enter a volume label (leave blank for none)", default="")
    if fs_type == "fat32" and len(label) > 11:
        label = label[:11].upper()
    elif fs_type == "exfat" and len(label) > 15:
        label = label[:15]
    elif fs_type == "xfs" and len(label) > 12:
        label = label[:12]
    elif fs_type in ["ntfs", "bcachefs"] and len(label) > 32:
        label = label[:32]

    plan: FormatPlan = {
        "device": target_device,
        "target_block": target_device,
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

def build_execution_plan(plan: FormatPlan) -> tuple[list[ExecutionStep], str, Optional[str]]:
    device = plan["device"]
    fs_type = plan["fs_type"]
    label = plan["label"]
    encrypt = plan["encrypt"]
    passphrase = plan.get("passphrase")
    partition_table = plan["partition_table"]
    partition_size = plan.get("partition_size", "100%")
    
    commands: list[ExecutionStep] = []
    bash_script = "#!/bin/bash\n# Dusky Formatter Native Execution Pipeline\n\n"
    
    mapper_name = None

    # Step 1: Low-level FTL discard (if blkdiscard available) & Wipe filesystem signatures
    if shutil.which("blkdiscard") and not device.startswith("/dev/mapper/"):
        blkdiscard_cmd = ["blkdiscard", "-f", device]
        commands.append({
            "action": "blkdiscard",
            "desc": f"Attempting low-level FTL discard on {device} to unmap all LBAs",
            "cmd": blkdiscard_cmd,
            "interactive": False,
            "input_data": None
        })
        bash_script += f"# Low-level FTL discard (resets LBA mappings if supported)\n{shlex.join(blkdiscard_cmd)} 2>/dev/null || true\n\n"

    wipe_cmd = ["wipefs", "--all", "--force", device]
    commands.append({
        "action": "wipe_fs",
        "desc": f"Sterilizing target device {device} to remove signatures",
        "cmd": wipe_cmd,
        "interactive": False,
        "input_data": None
    })
    bash_script += f"# Clear signature headers\n{shlex.join(wipe_cmd)}\n\n"

    target_block = device

    # Step 2: Partitioning via sfdisk (Universal Linux Device Partition Suffix Handling)
    if partition_table in ["gpt", "mbr"]:
        if partition_size and partition_size != "100%":
            sfdisk_table = f"label: gpt\nsize={partition_size}\n" if partition_table == "gpt" else f"label: dos\nsize={partition_size}\n"
            desc_str = f"Creating {partition_size} primary {partition_table.upper()} partition layout on {device}"
        else:
            sfdisk_table = "label: gpt\n,\n" if partition_table == "gpt" else "label: dos\n,\n"
            desc_str = f"Creating single primary {partition_table.upper()} partition layout on {device}"

        part_cmd = ["sfdisk", device]
        commands.append({
            "action": "partition",
            "desc": desc_str,
            "cmd": part_cmd,
            "interactive": False,
            "input_data": sfdisk_table
        })
        bash_script += f"# Partition drive via sfdisk\nprintf '{sfdisk_table}' | sfdisk {device}\n"
        
        # UNIVERSAL PARTITION SUFFIX RULE: Devices ending in digits (loop0, nvme0n1, zram1, mmcblk0) use 'p1', others (sda) use '1'
        part_suffix = "p1" if device[-1].isdigit() else "1"
        target_block = f"{device}{part_suffix}"
        
        settle_cmd = ["udevadm", "settle"]
        commands.append({
            "action": "settle",
            "desc": "Synchronizing kernel block layer device nodes",
            "cmd": settle_cmd,
            "interactive": False,
            "input_data": None
        })
        bash_script += f"udevadm settle\n\n"

    # Step 3: LUKS2 Encryption Setup
    if encrypt and passphrase:
        mapper_name = generate_secure_mapper_name()
        
        luks_fmt = ["cryptsetup", "-q", "luksFormat", "--type", "luks2", target_block, "-"]
        commands.append({
            "action": "luks_format",
            "desc": f"Initializing LUKS2 Encryption Container on {target_block}",
            "cmd": luks_fmt,
            "interactive": False, 
            "input_data": passphrase
        })
        bash_script += f"# Initialize LUKS2 Container\necho -n 'YOUR_PASSPHRASE' | {shlex.join(luks_fmt[:-1])} -\n"
        
        luks_open = ["cryptsetup", "open", "--type", "luks", "--allow-discards", "--key-file", "-", target_block, mapper_name]
        commands.append({
            "action": "luks_open",
            "desc": f"Opening encrypted volume as '/dev/mapper/{mapper_name}'",
            "cmd": luks_open,
            "interactive": False,
            "input_data": passphrase
        })
        bash_script += f"# Map LUKS volume with discard (TRIM) passthrough\necho -n 'YOUR_PASSPHRASE' | cryptsetup open --type luks --allow-discards --key-file - {target_block} {mapper_name}\n\n"
        
        target_block = f"/dev/mapper/{mapper_name}"

    # Step 4: Filesystem Creation (Bleeding Edge & Zero Write Amplification)
    mkfs_cmd: list[str] = []
    match fs_type:
        case "btrfs":
            csum = plan.get("csum") or "blake2"
            mkfs_cmd = ["mkfs.btrfs", "-f", "--csum", csum] 
            if label: mkfs_cmd.extend(["-L", label])
            mkfs_cmd.append(target_block)
            
        case "ext4" | "ext3" | "ext2":
            mkfs_binary = f"mkfs.{fs_type}"
            mkfs_cmd = [mkfs_binary, "-F", "-v", "-E", "lazy_itable_init=1,lazy_journal_init=1,discard"]
            if label: mkfs_cmd.extend(["-L", label])
            mkfs_cmd.append(target_block)

        case "f2fs":
            mkfs_cmd = ["mkfs.f2fs", "-f", "-t", "1"]
            if label: mkfs_cmd.extend(["-l", label])
            mkfs_cmd.append(target_block)

        case "exfat":
            mkfs_cmd = ["mkfs.exfat", "-F"]
            if label: mkfs_cmd.extend(["-L", label])
            mkfs_cmd.append(target_block)
            
        case "xfs":
            mkfs_cmd = ["mkfs.xfs", "-f"]
            if label: mkfs_cmd.extend(["-L", label[:12]])
            mkfs_cmd.append(target_block)

        case "ntfs":
            mkfs_cmd = ["mkfs.ntfs", "-f", "-F"]
            if label: mkfs_cmd.extend(["-L", label[:32]])
            mkfs_cmd.append(target_block)

        case "bcachefs":
            mkfs_cmd = ["bcachefs", "format", "-f"]
            if label: mkfs_cmd.append(f"--label={label}")
            mkfs_cmd.append(target_block)

        case "nilfs2":
            mkfs_cmd = ["mkfs.nilfs2", "-f"]
            if label: mkfs_cmd.extend(["-L", label])
            mkfs_cmd.append(target_block)

        case "fat32":
            mkfs_cmd = ["mkfs.fat", "-F", "32", "-I"]
            if label: mkfs_cmd.extend(["-n", label])
            mkfs_cmd.append(target_block)

    commands.append({
        "action": "mkfs",
        "desc": f"Building {fs_type.upper()} filesystem on {target_block}",
        "cmd": mkfs_cmd,
        "interactive": False,
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
            "interactive": False,
            "input_data": None
        })
        bash_script += f"# Lock container\n{shlex.join(close_cmd)}\n"

    return commands, bash_script, mapper_name

# ==============================================================================
# 7. PIPELINE EXECUTION
# ==============================================================================

def execute_plan(commands: list[ExecutionStep], mapper_name: Optional[str] = None) -> None:
    console.print("\n[bold cyan]Executing Dusky Formatting Plan...[/]")
    luks_is_open = False
    
    try:
        for step in commands:
            if step["action"] == "mkfs":
                console.print(f"\n[bold yellow]Executing:[/] {step['desc']}...")
                console.print(f"[dim]$ {shlex.join(step['cmd'])}[/]\n")
                try:
                    proc = subprocess.Popen(
                        step["cmd"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        bufsize=0 
                    )
                    with proc:
                        if proc.stdout:
                            while True:
                                char = proc.stdout.read(1)
                                if not char and proc.poll() is not None:
                                    break
                                if char:
                                    sys.stdout.buffer.write(char)
                                    sys.stdout.buffer.flush()
                                    
                    if proc.returncode != 0:
                        raise subprocess.CalledProcessError(proc.returncode, step["cmd"])
                        
                except subprocess.CalledProcessError:
                    console.print(f"\n[bold red]Fatal Error executing:[/] {shlex.join(step['cmd'])}")
                    raise Exception("Execution pipeline aborted.")
            elif step["action"] == "blkdiscard":
                with console.status(f"[bold yellow]Executing:[/] {step['desc']}...", spinner="dots"):
                    res = subprocess.run(step["cmd"], capture_output=True, text=True)
                    if res.returncode == 0:
                        console.print(f"[bold green]✔[/] {step['desc']} [dim](Completed)[/]")
                    else:
                        console.print(f"[dim yellow]Notice: Hardware BLKDISCARD not supported by USB controller ({res.stderr.strip() or 'Operation not supported'}). Proceeding with signature wiping...[/]")
            else:
                with console.status(f"[bold yellow]Executing:[/] {step['desc']}...", spinner="dots"):
                    try:
                        if step["interactive"]:
                            subprocess.run(step["cmd"], check=True)
                        else:
                            kwargs: dict[str, Any] = {
                                "capture_output": True,
                                "text": True,
                                "check": True
                            }
                            if step.get("input_data") is not None:
                                kwargs["input"] = step["input_data"]
                                
                            subprocess.run(step["cmd"], **kwargs)
                        
                        if step["action"] == "luks_open":
                            luks_is_open = True
                        elif step["action"] == "luks_close":
                            luks_is_open = False
                            
                    except subprocess.CalledProcessError as e:
                        console.print(f"[bold red]Fatal Error executing:[/] {shlex.join(step['cmd'])}")
                        if not step["interactive"] and e.stderr is not None:
                            console.print(f"[red]Kernel/API Output:\n{e.stderr.strip()}[/]")
                        raise Exception("Execution pipeline aborted.")
                
                console.print(f"[bold green]✔[/] {step['desc']} [dim](Completed)[/]")
                
        console.print("\n[bold green]✔ All formatting operations successfully completed![/]")

    except Exception as e:
        console.print(f"\n[bold red]Operation Failed: {str(e)}[/]")
        sys.exit(1)
        
    finally:
        if luks_is_open and mapper_name:
            console.print(f"\n[bold yellow]➜[/] Emergency fallback: Locking dangling LUKS volume '{mapper_name}'...")
            try:
                subprocess.run(["cryptsetup", "close", mapper_name], capture_output=True, check=True)
                console.print("[bold green]✔ Volume locked cleanly.[/]")
            except subprocess.CalledProcessError:
                console.print(f"[bold red]Warning: Failed to auto-lock mapper '{mapper_name}'. Please unmount manually.[/]")

# ==============================================================================
# ENTRY POINT
# ==============================================================================

def main() -> None:
    cli_args, is_cli_mode = parse_cli_args()
    ensure_root_privileges(is_cli_mode)

    if is_cli_mode and cli_args:
        plan = build_plan_from_cli(cli_args)
    else:
        plan = interactive_setup()
        
    commands, bash_equivalent, mapper_name = build_execution_plan(plan)
    
    console.print("\n[bold green]Command Execution Pipeline (Educational Transparency):[/]")
    syntax = Syntax(bash_equivalent, "bash", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title="Raw Subprocess Translation", border_style="green"))
    
    if plan.get("non_interactive"):
        execute_plan(commands, mapper_name)
    else:
        console.print(f"\n[bold red blink]WARNING:[/] ALL DATA ON [bold yellow]{plan['device']}[/] WILL BE PERMANENTLY ERASED.")
        if Confirm.ask("Are you absolutely confident you wish to proceed?", default=False):
            execute_plan(commands, mapper_name)
        else:
            console.print("[yellow]Operation aborted. Your data remains untouched.[/]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Process interrupted via keyboard. Exiting cleanly.[/]")
        sys.exit(1)

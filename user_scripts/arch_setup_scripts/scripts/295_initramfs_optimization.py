#!/usr/bin/env python3
# appending hooks to initramfs file
"""
Arch Linux Mkinitcpio Optimizer (Userspace)
Architecture: Python 3.10+ | Topology Auto-Detection | Drop-in Compliant
"""

import os
import sys
import re
import stat
import argparse
import tempfile
import subprocess
from pathlib import Path

try:
    from rich.console import Console
    from rich.prompt import Confirm
    from rich.panel import Panel
except ImportError:
    print("\033[1;31m[ERR]\033[0m The 'rich' library is required. Install it with: sudo pacman -S python-rich")
    sys.exit(1)

console = Console()

# --- 1. Robust Atomic File Operations ---
def atomic_write(file_path: Path, content: str) -> bool:
    """Writes to a file atomically, handling permissions securely."""
    temp_file_path = None
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', dir=file_path.parent) as tf:
            temp_file_path = Path(tf.name)
            tf.write(content)
            tf.flush()
            os.fsync(tf.fileno())

        if file_path.exists():
            stat_info = file_path.stat()
            os.chmod(temp_file_path, stat.S_IMODE(stat_info.st_mode))
            os.chown(temp_file_path, stat_info.st_uid, stat_info.st_gid)
        else:
            os.chmod(temp_file_path, 0o644)

        os.replace(temp_file_path, file_path)
        return True

    except Exception as e:
        if temp_file_path and temp_file_path.exists():
            try: temp_file_path.unlink()
            except OSError: pass
        console.print(f"[bold red][ERR][/bold red] Atomic commit failed: {e}")
        return False

# --- 2. Live Topology Detection ---
def detect_btrfs() -> bool:
    try:
        root_fs = subprocess.check_output(['findmnt', '-n', '-e', '-o', 'FSTYPE', '-T', '/']).decode().strip()
        return root_fs == "btrfs"
    except subprocess.CalledProcessError:
        return False

def detect_luks() -> bool:
    try:
        # Extract the raw block device holding the root filesystem
        root_dev = subprocess.check_output(['findmnt', '-n', '-v', '-e', '-o', 'SOURCE', '-T', '/']).decode().strip()
        root_dev = root_dev.split('[')[0] # Strip subvol tags if present
        
        # Inverse trace the block tree to see if it routes through a crypt layer
        lsblk_out = subprocess.check_output(['lsblk', '-nrspo', 'PATH,TYPE', '-s', '--', root_dev]).decode().strip()
        return "crypt" in lsblk_out
    except subprocess.CalledProcessError:
        return False

# --- 3. Configuration Parsing ---
def extract_array(file_path: Path, var_name: str) -> list:
    """Extracts elements from a bash array like HOOKS=(a b c)."""
    if not file_path.exists():
        return []
    content = file_path.read_text(encoding='utf-8')
    match = re.search(fr'^[ \t]*{var_name}=\((.*?)\)', content, re.MULTILINE)
    if match:
        raw_elements = re.split(r'\s+', match.group(1).strip())
        return [e for e in raw_elements if e]
    return []

# --- 4. Main Execution Flow ---
def main():
    parser = argparse.ArgumentParser(description="Mkinitcpio Optimizer")
    parser.add_argument("--auto", action="store_true", help="Run autonomously without interactive prompts.")
    args = parser.parse_args()

    # Privilege Escalation
    if os.geteuid() != 0:
        console.print("[bold blue][INFO][/bold blue] Root privileges required. Elevating...")
        os.execvp("sudo", ["sudo", sys.executable, os.path.realpath(sys.argv[0])] + sys.argv[1:])

    if not args.auto:
        os.system('clear' if os.name == 'posix' else 'cls')
    
    console.print(Panel.fit("[bold white]Arch Linux Initramfs Optimizer[/bold white]", border_style="cyan"))

    # Architecture Enforcement: We target the drop-in file exclusively
    conf_dir = Path("/etc/mkinitcpio.conf.d")
    target_file = conf_dir / "10-arch-btrfs-luks.conf"

    console.print("[bold blue][INFO][/bold blue] Analyzing live system topology...")
    
    is_btrfs = detect_btrfs()
    is_luks = detect_luks()
    
    console.print(f"   [white]- BTRFS Root:[/white] {'[green]Detected[/green]' if is_btrfs else '[yellow]Not Detected[/yellow]'}")
    console.print(f"   [white]- LUKS Layer:[/white] {'[green]Detected[/green]' if is_luks else '[yellow]Not Detected[/yellow]'}\n")

    if not args.auto:
        proceed = Confirm.ask("[bold cyan][?][/bold cyan] Generate optimized mkinitcpio configuration based on topology?")
        if not proceed:
            console.print("\n[bold blue][INFO][/bold blue] No changes requested. Exiting.")
            sys.exit(0)

    # --- Build Optimized Arrays ---
    
    # 1. Modules & Binaries (Mirroring 120_mkinitcpio_optimizer.sh exactly)
    modules = ["btrfs"] if is_btrfs else []
    binaries = ["/usr/bin/btrfs"] if is_btrfs else []

    # 2. Extract existing hooks to preserve Plymouth
    existing_hooks = extract_array(target_file, "HOOKS")
    has_plymouth = any("plymouth" in h for h in existing_hooks)

    # 3. Construct exact hook order mapping
    hooks = ["systemd"]

    # Plymouth must immediately follow systemd to capture the LUKS prompt
    if has_plymouth:
        hooks.append("plymouth")
        console.print("[bold blue][INFO][/bold blue] Preserved 'plymouth' integration hook.")

    hooks.extend([
        "keyboard",
        "autodetect",
        "microcode",
        "modconf",
        "kms",
        "sd-vconsole",
        "block"
    ])

    # sd-encrypt must follow block so the drive is visible for decryption
    if is_luks:
        hooks.append("sd-encrypt")

    hooks.append("filesystems")

    # --- Commit Configuration ---
    console.print(f"\n[bold]:: Writing configuration to {target_file}...[/bold]")
    
    config_payload = (
        "# Dynamic Topology Architecture (Python Optimized)\n"
        "# Managed by Arch Userspace Optimizer\n"
        f"MODULES=({' '.join(modules)})\n"
        f"BINARIES=({' '.join(binaries)})\n"
        f"HOOKS=({' '.join(hooks)})\n"
    )

    if atomic_write(target_file, config_payload):
        console.print(f"[bold green][OK][/bold green]   Drop-in configuration deployed.")
    else:
        sys.exit(1)

    # --- Generate Initramfs ---
    console.print("\n[bold]:: Regenerating Initramfs (mkinitcpio -P)...[/bold]")
    
    try:
        subprocess.run(["mkinitcpio", "-P"], check=True)
        console.print(f"\n[bold green]   Initramfs regeneration complete. Changes active on next reboot.[/bold green]")
    except subprocess.CalledProcessError:
        console.print(f"\n[bold red][ERR][/bold red] mkinitcpio encountered an error during generation.")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print(f"\n[bold red][!] Script interrupted.[/bold red]")
        sys.exit(1)

#!/usr/bin/env python3
#d: Add personal mount entries to /etc/fstab

import sys
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Try to import Rich modules for beautiful styling
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.status import Status
    from rich.prompt import Confirm
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# --- CONFIGURATION AREA ---
FSTAB_CONTENT = """
#XXXXXXXXXXXXXXXXXXXXXXXX--SSDs BTRFS, NTFS, & EXT4--XXXXXXXXXXXXXXXXXXXXXXXXX

# SSD NTFS (Windows)
UUID=848a215e8a214e4c	/mnt/windows	ntfs	defaults,noatime,uid=1000,gid=1000,umask=002,windows_names,iocharset=utf8,prealloc,nofail,user,comment=x-gvfs-show	0 0

# SSD Ext4 (Browser)
UUID=b982f02e-32f6-40cd-86de-964c90676cc2	/home/new/.config/mozilla	ext4	defaults,noatime,lazytime,nofail,user,comment=x-gvfs-show	0 2

# SSD Ext4 (Media)
UUID=a7230e67-34e8-4cd2-981d-ea02d1253539	/mnt/media	ext4	defaults,noatime,lazytime,nofail,user,comment=x-gvfs-show	0 2

#XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

#XXXXXXXXXXXXXXXXXXXXXXXX--HARD DISKS BTRFS & NTFS--XXXXXXXXXXXXXXXXXXXXXXXXX

# HDD Ext4 (Fast)
UUID=8c1f87fc-dcea-4d2e-81fd-d8028c0fa86b	/mnt/fast	ext4	defaults,noatime,lazytime,nofail,user,comment=x-gvfs-show	0 2

# HDD NTFS (Slow)
UUID=5a921a119219f26d	/mnt/slow	ntfs	defaults,noatime,uid=1000,gid=1000,umask=002,windows_names,iocharset=utf8,prealloc,nofail,user,comment=x-gvfs-show	0 0

# HDD BTRFS (WD Book Fast)
UUID=46798d3b-cda7-4031-818f-37a06abbeb37	/mnt/wdfast	btrfs	defaults,noatime,compress=zstd:3,autodefrag,subvol=/,nofail,user,comment=x-gvfs-show	0 0

# HDD BTRFS (WD Book Slow)
UUID=2765359f-232e-4165-bc69-ef402b50c74c	/mnt/wdslow	btrfs	defaults,noatime,compress=zstd:3,autodefrag,subvol=/,nofail,user,comment=x-gvfs-show	0 0

# HDD NTFS (Enclosure)
UUID=5a428b8a428b6a19	/mnt/enclosure	ntfs	defaults,noatime,uid=1000,gid=1000,umask=002,windows_names,iocharset=utf8,prealloc,nofail,user,comment=x-gvfs-show	0 0

#XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
"""

TARGET_FILE = Path("/etc/fstab")
MARKER_START = "# === ARCH ORCHESTRA: DUSK PERSONAL MOUNTS [START] ==="
MARKER_END = "# === ARCH ORCHESTRA: DUSK PERSONAL MOUNTS [END] ==="

if HAS_RICH:
    console = Console()
    error_console = Console(stderr=True)
else:
    console = None
    error_console = None

def log_info(msg: str):
    if HAS_RICH:
        console.print(Text.assemble(("[INFO]", "bold blue"), f" {msg}"))
    else:
        print(f"\033[1;34m[INFO]\033[0m {msg}")

def log_success(msg: str):
    if HAS_RICH:
        console.print(Text.assemble(("[OK]", "bold green"), f" {msg}"))
    else:
        print(f"\033[1;32m[OK]\033[0m {msg}")

def log_warn(msg: str):
    if HAS_RICH:
        error_console.print(Text.assemble(("[WARN]", "bold yellow"), f" {msg}"))
    else:
        print(f"\033[1;33m[WARN]\033[0m {msg}", file=sys.stderr)

def log_error(msg: str):
    if HAS_RICH:
        error_console.print(Text.assemble(("[ERROR]", "bold red"), f" {msg}"))
    else:
        print(f"\033[1;31m[ERROR]\033[0m {msg}", file=sys.stderr)

def elevate_privileges():
    """Reruns the script under sudo if not root."""
    if os.geteuid() != 0:
        log_info("Root privileges required. Elevating...")
        try:
            subprocess.run(["sudo", sys.executable] + sys.argv, check=True)
            sys.exit(0)
        except subprocess.CalledProcessError as e:
            log_error(f"Failed to elevate privileges: {e}")
            sys.exit(1)

def confirm_target_machine():
    """Asks the user for confirmation if they are running interactively."""
    sys_vendor = "Unknown"
    sys_product = "Unknown"

    vendor_path = Path("/sys/class/dmi/id/sys_vendor")
    product_path = Path("/sys/class/dmi/id/product_name")

    if vendor_path.exists():
        sys_vendor = vendor_path.read_text().strip()
    if product_path.exists():
        sys_product = product_path.read_text().strip()

    if HAS_RICH:
        title_text = Text("Dusky Personal FSTAB Configurator", style="bold white")
        console.print(Panel(
            Text.assemble(
                ("Target System: ", "bold"), f"{sys_vendor} {sys_product}\n",
                ("Intended For:  ", "bold"), "Dusk's Personal ASUS Laptop FX507\n",
                ("Action:        ", "bold"), "Surgically appends optimized mounts block to /etc/fstab"
            ),
            title=title_text,
            border_style="cyan"
        ))
    else:
        print(f"\nSystem identifies as: {sys_vendor} {sys_product}")
        print("This script is configured for: Dusk's Personal ASUS Laptop")
        print(f"It will modify {TARGET_FILE}.")

    # Only ask for input if stdin is a terminal
    if sys.stdin.isatty():
        if HAS_RICH:
            try:
                # Beautiful Rich Confirmation Prompt
                if not Confirm.ask("[bold yellow]Is this the correct target machine?[/bold yellow]"):
                    log_info("User selected NO. Exiting cleanly.")
                    sys.exit(0)
            except (KeyboardInterrupt, EOFError):
                log_error("Input interrupted. Aborting.")
                sys.exit(1)
        else:
            try:
                response = input("\nIs this the correct target machine? [y/N] ").strip().lower()
                if response not in ("y", "yes"):
                    log_info("User selected NO. Exiting cleanly.")
                    sys.exit(0)
            except (KeyboardInterrupt, EOFError):
                log_error("Input interrupted. Aborting.")
                sys.exit(1)
    else:
        log_info("Non-interactive run detected. Proceeding automatically.")

def main():
    elevate_privileges()
    confirm_target_machine()

    # Pre-flight Checks
    if not TARGET_FILE.exists():
        log_error(f"Critical: {TARGET_FILE} not found.")
        sys.exit(1)

    fstab_text = TARGET_FILE.read_text(encoding="utf-8", errors="surrogateescape")

    # Idempotency Check
    if MARKER_START in fstab_text:
        log_success("Custom mounts already present in fstab. Skipping.")
        sys.exit(0)

    # Ephemeral Backup Strategy
    temp_backup = Path(tempfile.mktemp())
    shutil.copy2(TARGET_FILE, temp_backup)
    
    log_info("Applying fstab configuration changes...")

    try:
        # Prepare content to write
        sep = ""
        if fstab_text and not fstab_text.endswith("\n"):
            sep = "\n"
        if fstab_text and fstab_text.strip() and fstab_text.endswith("\n"):
            sep = "\n"

        append_block = f"{sep}{MARKER_START}\n{FSTAB_CONTENT.strip()}\n{MARKER_END}\n"
        
        with open(TARGET_FILE, "a", encoding="utf-8", errors="surrogateescape") as f:
            f.write(append_block)

        # Verification & Rollback
        if HAS_RICH:
            # Beautiful Rich spinning status bar
            with console.status("[bold green]Verifying fstab syntax (mount --fake)...[/bold green]"):
                res = subprocess.run(["mount", "--fake", "--all", "--verbose"], capture_output=True)
        else:
            log_info("Verifying syntax...")
            res = subprocess.run(["mount", "--fake", "--all", "--verbose"], capture_output=True)

        if res.returncode == 0:
            log_success("Syntax check passed.")
            
            # Clean up backup on success
            if temp_backup.exists():
                temp_backup.unlink()
                
            if HAS_RICH:
                with console.status("[bold green]Reloading systemd daemon...[/bold green]"):
                    subprocess.run(["systemctl", "daemon-reload"], check=True)
            else:
                log_info("Reloading systemd...")
                subprocess.run(["systemctl", "daemon-reload"], check=True)
            log_success("Systemd reloaded. Mount setup completed successfully.")
        else:
            log_error("SYNTAX CHECK FAILED. Rolling back changes...")
            # Restore original
            shutil.copy2(temp_backup, TARGET_FILE)
            if temp_backup.exists():
                temp_backup.unlink()
            log_error(f"Changes reverted. {TARGET_FILE} is untouched.")
            sys.exit(1)

    except Exception as e:
        log_error(f"Failed to append mounts: {e}")
        if temp_backup.exists():
            shutil.copy2(temp_backup, TARGET_FILE)
            temp_backup.unlink()
            log_error("Changes reverted due to exception.")
        sys.exit(1)

if __name__ == "__main__":
    main()

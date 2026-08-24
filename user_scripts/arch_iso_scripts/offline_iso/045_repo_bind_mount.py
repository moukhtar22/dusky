#!/usr/bin/env python3
# ==============================================================================
# 045_repo_bind_mount.py
# RAM-Boot Resilient Offline/Online Repository Binder (Python 3.14 / Arch ISO)
# ==============================================================================
# Context: Prepares unmaskable repository bind mounts for pacstrap sandboxing.
# Handles: Offline ISO, Online ISO, copytoram evasion, Ventoy block mapping.
# ==============================================================================

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich import box
    console = Console()
except ImportError:
    print("[ERROR] rich library missing. Run: pacman -S python-rich", file=sys.stderr)
    sys.exit(1)


def run(*cmd, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        check=False
    )
    if check and res.returncode != 0:
        err_msg = res.stderr.strip() if res.stderr else res.stdout.strip()
        console.print(f"[red][ERROR] Command failed ({res.returncode}): {' '.join(cmd)}\n{err_msg}[/red]")
        sys.exit(res.returncode or 1)
    return res


def is_mountpoint(path: Path) -> bool:
    if not path.exists():
        return False
    r = run("mountpoint", "-q", str(path), check=False)
    return r.returncode == 0


def find_repo_source() -> Optional[Path]:
    """
    Search for offline repository directory across multiple candidate locations.
    Handles standard bootmnt, custom mounts, and already-mounted live targets.
    """
    iso_mnt = Path("/run/archiso/bootmnt")
    candidates = [
        iso_mnt / "arch" / "repo",
        iso_mnt / "offline_repo",
        iso_mnt / "repo",
        Path("/offline_repo"),
    ]
    for cand in candidates:
        if cand.is_dir() and (cand / "archrepo.db").is_file():
            return cand.resolve()
        elif cand.is_dir() and any(cand.glob("*.pkg.tar.*")):
            return cand.resolve()
    return None


def recover_iso_block_device() -> Optional[Path]:
    """
    Recovers the ISO block device if unmounted due to copytoram or Ventoy abstraction.
    """
    console.print("[yellow]Searching for ISO block device (copytoram / Ventoy evasion)...[/yellow]")
    
    # 1. Check blkid for iso9660
    r = run("blkid", "-t", "TYPE=iso9660", "-o", "device", check=False)
    if r.stdout:
        for line in r.stdout.splitlines():
            dev = line.strip()
            if dev and Path(dev).exists():
                return Path(dev)

    # 2. Check Ventoy mapper
    ventoy_map = Path("/dev/mapper/ventoy")
    if ventoy_map.is_block_device():
        return ventoy_map

    # 3. Check lsblk JSON for iso9660 or archiso labels
    r = run("lsblk", "--json", "--paths", "-o", "PATH,TYPE,FSTYPE,LABEL", check=False)
    if r.stdout:
        try:
            data = json.loads(r.stdout)
            for dev in data.get("blockdevices", []):
                fstype = (dev.get("fstype") or "").lower()
                label = (dev.get("label") or "").lower()
                if "iso9660" in fstype or "arch" in label:
                    return Path(dev["path"])
                for child in dev.get("children", []) or []:
                    c_fstype = (child.get("fstype") or "").lower()
                    c_label = (child.get("label") or "").lower()
                    if "iso9660" in c_fstype or "arch" in c_label:
                        return Path(child["path"])
        except Exception:
            pass

    return None


def main():
    console.print(Panel("[bold cyan]045 - Offline/Online Repository Bind-Mount Orchestrator[/bold cyan]", box=box.ROUNDED))

    if os.geteuid() != 0:
        console.print("[red][ERROR] Root privileges required. Please run as root.[/red]")
        sys.exit(1)

    iso_mnt = Path("/run/archiso/bootmnt")
    source_dir = find_repo_source()

    # If source is missing, attempt ISO block device remount (copytoram / Ventoy recovery)
    if not source_dir:
        iso_dev = recover_iso_block_device()
        if iso_dev:
            console.print(f"[cyan]Remounting ISO block device ({iso_dev}) to {iso_mnt}...[/cyan]")
            iso_mnt.mkdir(parents=True, exist_ok=True)
            if not is_mountpoint(iso_mnt):
                m_res = run("mount", "-o", "ro", str(iso_dev), str(iso_mnt), check=False)
                if m_res.returncode != 0:
                    console.print(f"[yellow]Warning: Could not mount {iso_dev} to {iso_mnt}[/yellow]")
            source_dir = find_repo_source()

    # Online mode detection: If no offline repo source exists anywhere
    if not source_dir:
        console.print("[bold yellow][INFO] Online mode detected — No offline repository present on boot media.[/bold yellow]")
        console.print("[bold green][OK] Skipping repository bind mounts. Installation will proceed via online network mirrors.[/bold green]")
        sys.exit(0)

    console.print(f"[bold green][OK] Discovered offline repository source: {source_dir}[/bold green]")

    # 1. Live ISO Target (/offline_repo)
    target_live = Path("/offline_repo")
    target_live.mkdir(parents=True, exist_ok=True)
    if not is_mountpoint(target_live):
        console.print(f"[cyan]Bind-mounting {source_dir} -> {target_live}...[/cyan]")
        run("mount", "--bind", str(source_dir), str(target_live))
        console.print(f"[bold green][OK] Live ISO bind mount active at {target_live}[/bold green]")
    else:
        console.print(f"[green][OK] Live ISO target already mounted at {target_live}[/green]")

    # 2. Target System Chroot Target (/mnt/offline_repo)
    mnt_dir = Path("/mnt")
    if mnt_dir.is_dir() and is_mountpoint(mnt_dir):
        target_chroot = Path("/mnt/offline_repo")
        target_chroot.mkdir(parents=True, exist_ok=True)
        if not is_mountpoint(target_chroot):
            console.print(f"[cyan]Bind-mounting {target_live} -> {target_chroot}...[/cyan]")
            run("mount", "--bind", str(target_live), str(target_chroot))
            console.print(f"[bold green][OK] Target chroot bind mount active at {target_chroot}[/bold green]")
        else:
            console.print(f"[green][OK] Target chroot already mounted at {target_chroot}[/green]")
    else:
        console.print("[dim]/mnt not mounted yet — skipping chroot target bind mount[/dim]")

    console.print(Panel("[bold green]Repository Bind Mount Complete[/bold green]", box=box.ROUNDED))


if __name__ == "__main__":
    main()

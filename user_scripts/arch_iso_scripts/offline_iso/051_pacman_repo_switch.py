#!/usr/bin/env python3
# ==============================================================================
# 051_pacman_repo_switch.py
# Pacman Repository State Manager (Python 3.14 / Arch Linux)
# Context: Toggles pacman between Offline (file://) and Online (HTTPS) modes.
# Handles: Pacman 7.1 Landlock Sandbox, stale lock cleanup, atomic writes.
# ==============================================================================

import os
import sys
import argparse
import subprocess
import datetime
from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich import box
    console = Console()
except ImportError:
    print("[ERROR] rich library missing. Run: pacman -S python-rich", file=sys.stderr)
    sys.exit(1)


def get_offline_repo_path() -> str:
    candidates = [
        Path("/offline_repo"),
        Path("/mnt/offline_repo"),
        Path("/run/archiso/bootmnt/arch/repo"),
        Path("/run/archiso/bootmnt/offline_repo"),
        Path("/run/archiso/bootmnt/repo"),
    ]
    for cand in candidates:
        if cand.is_dir() and ((cand / "archrepo.db").is_file() or any(cand.glob("*.pkg.tar.*"))):
            return f"file://{cand.resolve()}"
    return "file:///offline_repo"


def atomic_write(target: Path, content: str, mode: int = 0o644) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = target.parent / f".{target.name}.tmp_{os.getpid()}"
    try:
        tmp_file.write_text(content, encoding="utf-8")
        tmp_file.chmod(mode)
        os.replace(tmp_file, target)
    except Exception as e:
        if tmp_file.exists():
            tmp_file.unlink()
        console.print(f"[red][ERROR] Failed to write {target}: {e}[/red]")
        sys.exit(1)


def backup_file(target: Path) -> None:
    if target.is_file():
        bak = target.with_name(target.name + ".pacman-switch.bak")
        try:
            bak.write_bytes(target.read_bytes())
            console.print(f"[dim]Backup saved: {bak}[/dim]")
        except Exception as e:
            console.print(f"[yellow][WARN] Could not create backup for {target}: {e}[/yellow]")


def clear_stale_pacman_lock() -> None:
    lck = Path("/var/lib/pacman/db.lck")
    if lck.is_file():
        res = subprocess.run(["pgrep", "-x", "pacman"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            try:
                lck.unlink()
                console.print("[yellow][WARN] Removed stale pacman lock file (/var/lib/pacman/db.lck)[/yellow]")
            except Exception as e:
                console.print(f"[yellow][WARN] Could not remove stale lock file: {e}[/yellow]")


def switch_to_online(pacman_conf: Path, mirrorlist: Path) -> None:
    console.print(Panel("[bold cyan]Switching to ONLINE Repositories (Arch Linux HTTPS)[/bold cyan]", box=box.ROUNDED))
    
    backup_file(pacman_conf)
    backup_file(mirrorlist)

    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    conf_content = f"""# ==============================================================================
# /etc/pacman.conf — ONLINE MODE
# ==============================================================================
# Managed by: 051_pacman_repo_switch.py
# State:      ONLINE (ARCH LINUX)
# Written:    {now_utc}
# ==============================================================================

[options]
Color
ILoveCandy
VerbosePkgLists
HoldPkg     = pacman glibc
CheckSpace
ParallelDownloads = 10
DisableDownloadTimeout
DownloadUser = alpm

SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional

Architecture = auto

[core]
Include = /etc/pacman.d/mirrorlist

[extra]
Include = /etc/pacman.d/mirrorlist

[multilib]
Include = /etc/pacman.d/mirrorlist
"""

    mirror_content = f"""################################################################################
# /etc/pacman.d/mirrorlist — ONLINE MODE
################################################################################
# Managed by: 051_pacman_repo_switch.py
# State:      ONLINE (HTTPS Mirrors)
# Written:    {now_utc}

Server = https://frankfurt.mirror.pkgbuild.com/$repo/os/x86_64
Server = https://johannesburg.mirror.pkgbuild.com/$repo/os/x86_64
Server = https://london.mirror.pkgbuild.com/$repo/os/x86_64
Server = https://losangeles.mirror.pkgbuild.com/$repo/os/x86_64
Server = https://mirror.moson.org/arch/$repo/os/x86_64
Server = https://mirror.sunred.org/archlinux/$repo/os/x86_64
Server = https://arch.mirror.constant.com/$repo/os/x86_64
Server = https://arch.phinau.de/$repo/os/x86_64
Server = https://mirror.theo546.fr/archlinux/$repo/os/x86_64
Server = https://berlin.mirror.pkgbuild.com/$repo/os/x86_64
"""

    atomic_write(pacman_conf, conf_content)
    atomic_write(mirrorlist, mirror_content)

    console.print("[bold green][OK] Online repository configuration successfully applied.[/bold green]")

    clear_stale_pacman_lock()

    console.print("[cyan]Syncing online package databases (core, extra, multilib)...[/cyan]")
    res = subprocess.run(["pacman", "-Sy"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode == 0:
        console.print("[bold green][OK] Online package databases synced successfully.[/bold green]")
    else:
        console.print(f"[yellow][WARN] 'pacman -Sy' returned exit code {res.returncode}:\n{res.stdout}[/yellow]")


def switch_to_offline(pacman_conf: Path, mirrorlist: Path) -> None:
    console.print(Panel("[bold cyan]Switching to OFFLINE Repositories (Local Media file://)[/bold cyan]", box=box.ROUNDED))
    
    backup_file(pacman_conf)
    backup_file(mirrorlist)

    repo_url = get_offline_repo_path()
    repo_name = "archrepo"
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    mirror_content = f"""################################################################################
# /etc/pacman.d/mirrorlist — OFFLINE MODE
################################################################################
# Managed by: 051_pacman_repo_switch.py
# State:      OFFLINE (Local Media)
# Written:    {now_utc}

Server = {repo_url}
"""

    conf_content = f"""# ==============================================================================
# /etc/pacman.conf — OFFLINE MODE
# ==============================================================================
# Managed by: 051_pacman_repo_switch.py
# State:      OFFLINE (ARCH LINUX)
# Written:    {now_utc}

[options]
Color
ILoveCandy
VerbosePkgLists
HoldPkg     = pacman glibc
CheckSpace
ParallelDownloads = 5

# Pacman 7.1.0+ Landlock Sandbox Bypass for file:// block device mounts
DisableSandbox

# DownloadUser Disabled: Prevents 'alpm' permission drops on root mounts
# DownloadUser = alpm

SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional

Architecture = auto

[{repo_name}]
SigLevel = Never
Include = /etc/pacman.d/mirrorlist
"""

    atomic_write(mirrorlist, mirror_content)
    atomic_write(pacman_conf, conf_content)

    console.print("[bold green][OK] Offline repository configuration written.[/bold green]")

    repo_fs_path = Path(repo_url.replace("file://", ""))
    db_file = repo_fs_path / f"{repo_name}.db"

    if not repo_fs_path.is_dir():
        console.print(f"[yellow][WARN] Offline repository directory '{repo_fs_path}' not currently mounted.[/yellow]")
        return

    if not db_file.is_file():
        console.print(f"[yellow][WARN] Offline repository database '{db_file}' not found.[/yellow]")
        return

    clear_stale_pacman_lock()

    console.print(f"[cyan]Syncing offline package database ({db_file.name})...[/cyan]")
    res = subprocess.run(["pacman", "-Sy"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode == 0:
        console.print("[bold green][OK] Offline package database synced successfully.[/bold green]")
    else:
        console.print(f"[yellow][WARN] 'pacman -Sy' returned exit code {res.returncode}:\n{res.stdout}[/yellow]")


def main():
    if os.geteuid() != 0:
        console.print("[red][ERROR] Root privileges required. Please run as root.[/red]")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Pacman Repository State Manager")
    parser.add_argument("--online", action="store_true", help="Switch to Online HTTPS mirrors")
    parser.add_argument("--offline", action="store_true", help="Switch to Offline local file:// repository")
    parser.add_argument("--arch", action="store_true", help="Backward compatibility flag (ignored)")
    args = parser.parse_args()

    pacman_conf = Path("/etc/pacman.conf")
    mirrorlist = Path("/etc/pacman.d/mirrorlist")

    if args.online:
        switch_to_online(pacman_conf, mirrorlist)
    elif args.offline:
        switch_to_offline(pacman_conf, mirrorlist)
    else:
        console.print("[bold yellow]No mode specified. Use --online or --offline.[/bold yellow]")
        sys.exit(1)


if __name__ == "__main__":
    main()

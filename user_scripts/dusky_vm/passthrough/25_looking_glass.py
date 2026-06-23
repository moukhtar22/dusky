#!/usr/bin/env python3
"""
Phase 5: Looking Glass KVMFR Host Configuration
Target: Arch Linux (Kernel 7.1.0+), Python 3.14.5+, systemd 260
Scope: KVMFR Modprobe, udev rules, cgroup whitelisting, dynamic IVSHMEM calculation.
Philosophy: Zero-Clutter Idempotency, Atomic Writes, Strict Cgroup Regex Parsing, Ring 0 Safety.
"""

import os
import sys
import re
import pwd
import stat
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Never, Tuple

# ==============================================================================
# BOOTSTRAP: Strict Privilege & Auto-Elevation
# ==============================================================================
def require_root() -> None:
    """Enforce eUID 0. Auto-elevate via sudo if executed as a standard user."""
    if os.geteuid() != 0:
        print("\n[INFO] Administrative privileges required. Elevating via sudo...")
        try:
            # Replace the current process with a sudo call, preserving exact binary and args
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except OSError as e:
            print(f"\n[FATAL] Failed to elevate privileges dynamically: {e}")
            sys.exit(1)

require_root()

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
except ImportError:
    print("\n[FATAL] 'python-rich' is missing. Please run: sudo pacman -S python-rich")
    sys.exit(1)

# Force terminal characteristics for orchestrator tee compatibility 
console = Console(force_terminal=True, force_interactive=True)

# ==============================================================================
# CORE UTILITIES
# ==============================================================================
def bail(msg: str) -> Never:
    """Exit gracefully with a clear error panel."""
    console.print(Panel(f"[bold red]FATAL ERROR:[/bold red] {msg}", border_style="red"))
    sys.exit(1)

def atomic_write(target_path: Path, new_content: str) -> bool:
    """
    Safely writes data using a temporary file and an atomic swap.
    Inherits exact file permissions (st_mode) to prevent security regressions.
    Zero-clutter: NO .bak files are ever created.
    """
    if target_path.exists():
        if target_path.read_text(encoding="utf-8") == new_content:
            return False
        mode = target_path.stat().st_mode
    else:
        mode = 0o644 # Default standard file permissions
        
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=target_path.parent, prefix=f".{target_path.name}.tmp.")
    tmp_path = Path(tmp_path_str)
    
    try:
        with os.fdopen(fd, 'w', encoding="utf-8") as f:
            f.write(new_content)
        os.chmod(tmp_path, stat.S_IMODE(mode))
        shutil.move(tmp_path, target_path)
        return True
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        bail(f"Atomic write failed on {target_path}: {e}")

def run_cmd(cmd: list, check: bool = True) -> int:
    """
    Execute shell commands silently. 
    Raises fatal error if check=True and command fails. Returns the exit code.
    """
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check and result.returncode != 0:
        bail(f"Command execution failed: {' '.join(cmd)}\nExit Code: {result.returncode}")
    return result.returncode

# ==============================================================================
# USER RESOLUTION & PACKAGE INSTALLATION
# ==============================================================================
def resolve_target_user() -> str:
    """Forensically determine the real human user interacting with the system."""
    user = os.environ.get("SUDO_USER") or os.environ.get("DOAS_USER")
    
    if not user or user == "root":
        try:
            user = os.getlogin()
        except OSError:
            pass # TTY might not be attached properly
            
    if not user or user == "root":
        console.print("[yellow]⚠ Could not automatically determine standard user from environment.[/yellow]")
        user = Prompt.ask("[bold cyan]Enter your non-root Arch username[/bold cyan]").strip()
    
    try:
        pwd.getpwnam(user)
    except KeyError:
        bail(f"The user '{user}' does not exist in the local passwd database.")
        
    return user

def install_looking_glass_packages(user: str) -> None:
    """Install required packages via AUR using the standard user."""
    packages = ["looking-glass-module-dkms-git", "looking-glass-git", "freerdp", "dkms"]
    console.print("\n[bold blue]==>[/bold blue] [bold]Synchronizing Looking Glass packages...[/bold]")
    
    if not shutil.which("paru"):
        bail("'paru' not found in PATH. Cannot install AUR packages.")
        
    # Drop privileges to standard user to run paru; bypass pagers with --noconfirm
    cmd = ["sudo", "-u", user, "paru", "-S", "--needed", "--noconfirm"] + packages
    
    try:
        subprocess.run(cmd, check=True)
        console.print("[bold green]  ✓ Looking Glass & DKMS packages staged successfully.[/bold green]")
    except subprocess.CalledProcessError as e:
        bail(f"Package installation failed with code {e.returncode}.")

# ==============================================================================
# DYNAMIC IVSHMEM CALCULATION
# ==============================================================================
def calculate_kvmfr_size() -> Tuple[int, int]:
    """Interactively map SDR resolution targets to strict KVMFR sizing."""
    console.print("\n[bold blue]==>[/bold blue] [bold]SDR Resolution & IVSHMEM Memory Calculation[/bold]")
    
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Option", style="cyan", justify="center")
    table.add_column("SDR Resolution Target", style="green")
    table.add_column("Base Calculation", style="dim")
    table.add_column("Required KVMFR (MiB)", style="bold yellow")

    table.add_row("1", "1080p / 1200p", "16-18 MB + 10 MB Overhead", "32 MiB")
    table.add_row("2", "1440p (Recommended)", "29 MB + 10 MB Overhead", "64 MiB")
    table.add_row("3", "4K", "66 MB + 10 MB Overhead", "128 MiB")
    
    console.print(table)
    
    choice = Prompt.ask(
        "[bold cyan]Select your target SDR resolution[/bold cyan]", 
        choices=["1", "2", "3"], 
        default="2"
    )

    size_map = {"1": 32, "2": 64, "3": 128}
    mib_size = size_map[choice]
    byte_size = mib_size * 1024 * 1024
    
    console.print(f"[bold green]  ✓ Locked KVMFR size to {mib_size} MiB ({byte_size} bytes).[/bold green]")
    return mib_size, byte_size

# ==============================================================================
# HOST CONFIGURATION & RACE CONDITION PREVENTION
# ==============================================================================
def configure_host_modules(mib_size: int) -> None:
    """Idempotently configure modprobe, modules-load, and udev rules."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Staging KVMFR Kernel Module & Udev Permissions...[/bold]")

    # 1. Modprobe configuration
    modprobe_path = Path("/etc/modprobe.d/kvmfr.conf")
    modprobe_content = f"# KVMFR Looking Glass — static IVSHMEM device size\noptions kvmfr static_size_mb={mib_size}\n"
    if atomic_write(modprobe_path, modprobe_content):
        console.print(f"[bold green]  ✓ Modprobe options enforced: {modprobe_path}[/bold green]")
    else:
        console.print(f"[bold green]  ✓ Modprobe options already optimal: {modprobe_path}[/bold green]")

    # 2. Modules-load configuration
    load_path = Path("/etc/modules-load.d/kvmfr.conf")
    load_content = "# Load KVMFR before any VM that uses it\nkvmfr\n"
    if atomic_write(load_path, load_content):
        console.print(f"[bold green]  ✓ Systemd module load enforced: {load_path}[/bold green]")
    else:
        console.print(f"[bold green]  ✓ Systemd module load already optimal: {load_path}[/bold green]")

    # 3. Udev Rules (Must sort before 73-seat-late.rules per systemd 260 spec)
    udev_path = Path("/etc/udev/rules.d/70-kvmfr.rules")
    udev_content = 'SUBSYSTEM=="kvmfr", GROUP="kvm", MODE="0660", TAG+="uaccess"\n'
    if atomic_write(udev_path, udev_content):
        console.print(f"[bold green]  ✓ Udev access controls enforced: {udev_path}[/bold green]")
    else:
        console.print(f"[bold green]  ✓ Udev access controls already optimal: {udev_path}[/bold green]")

    with console.status("[cyan]Triggering surgical udev rule reload...", spinner="dots"):
        run_cmd(["udevadm", "control", "--reload"])
        # Surgical trigger: only target the kvmfr subsystem to prevent micro-stutters
        run_cmd(["udevadm", "trigger", "--action=add", "--subsystem-match=kvmfr"])

def enforce_device_integrity() -> None:
    """Detects and mitigates the QEMU regular file creation race condition."""
    dev_path = Path("/dev/kvmfr0")
    console.print("\n[bold blue]==>[/bold blue] [bold]Verifying KVMFR DMA Integrity...[/bold]")
    
    if dev_path.exists():
        mode = dev_path.stat().st_mode
        if not stat.S_ISCHR(mode):
            console.print("[bold yellow]  ⚠ FATAL RACE DETECTED: /dev/kvmfr0 is a regular file, not a char device![/bold yellow]")
            console.print("[cyan]    Purging corrupted file...[/cyan]")
            dev_path.unlink()
    
    with console.status("[cyan]Injecting KVMFR into Ring 0...", spinner="dots"):
        # We pass check=False here to bypass the strict gatekeeper check in run_cmd.
        # This allows us to catch the missing module without assassinating the script.
        if run_cmd(["modprobe", "kvmfr"], check=False) == 0:
            console.print("[bold green]  ✓ KVMFR char device dynamically loaded and secured.[/bold green]")
        else:
            console.print("[bold yellow]  ⚠ KVMFR module failed to load. (DKMS build might be pending or requires a reboot).[/bold yellow]")

# ==============================================================================
# LIBVIRT CGROUP INJECTION
# ==============================================================================
def configure_qemu_cgroups() -> None:
    """
    Bulletproof Regex parsing to cleanly uncomment and inject /dev/kvmfr0.
    Utilizes [^\\\\]* to prevent multiline runaway regex crashes.
    """
    conf_path = Path("/etc/libvirt/qemu.conf")
    console.print("\n[bold blue]==>[/bold blue] [bold]Securing QEMU Cgroup Device ACLs...[/bold]")

    if not conf_path.exists():
        bail(f"Configuration file {conf_path} does not exist. Ensure libvirt is installed.")

    content = conf_path.read_text(encoding="utf-8")
    target_device = '"/dev/kvmfr0"'

    # Regex constraints explicitly bound inside the array braces
    pattern_active = re.compile(r'^\s*cgroup_device_acl\s*=\s*\[([^\]]*)\]', re.MULTILINE)
    pattern_commented = re.compile(r'^\s*#\s*cgroup_device_acl\s*=\s*\[([^\]]*)\]', re.MULTILINE)

    if match := pattern_active.search(content):
        inner = match.group(1)
        if target_device not in inner:
            clean_inner = inner.rstrip(" \n\r\t,")
            new_block = f"cgroup_device_acl = [{clean_inner},\n    {target_device}\n]"
            content = content[:match.start()] + new_block + content[match.end():]
            
    elif match := pattern_commented.search(content):
        inner = match.group(1)
        # Strip comments line-by-line to preserve structure perfectly
        uncommented_inner = "\n".join([line.lstrip(' \t#') for line in inner.splitlines()])
        clean_inner = uncommented_inner.rstrip(" \n\r\t,")
        new_block = f"cgroup_device_acl = [{clean_inner},\n    {target_device}\n]"
        content = content[:match.start()] + new_block + content[match.end():]
        
    else:
        # Failsafe fallback appended to EOF
        fallback_block = (
            '\ncgroup_device_acl = [\n'
            '    "/dev/null", "/dev/full", "/dev/zero",\n'
            '    "/dev/random", "/dev/urandom",\n'
            '    "/dev/ptmx", "/dev/kvm", "/dev/kqemu",\n'
            '    "/dev/rtc","/dev/hpet", "/dev/sev",\n'
            f'    {target_device}\n]\n'
        )
        content += fallback_block

    if atomic_write(conf_path, content):
        console.print("[bold green]  ✓ qemu.conf strictly parsed and injected with KVMFR ACL.[/bold green]")
        with console.status("[cyan]Restarting Libvirt modular daemons...", spinner="dots"):
            # Target modular daemons dynamically based on Phase 2 architecture
            run_cmd(["systemctl", "restart", "virtqemud.socket", "virtqemud.service"])
        console.print("[bold green]  ✓ virtqemud service/socket restarted successfully.[/bold green]")
    else:
        console.print("[bold green]  ✓ qemu.conf already whitelists /dev/kvmfr0 perfectly. No changes made.[/bold green]")

# ==============================================================================
# MAIN EXECUTION & XML OUTPUT
# ==============================================================================
def main() -> None:
    console.clear()
    console.print(Panel("[bold green]Phase 5: KVMFR Host Configuration[/bold green]\nTarget: Arch Linux | Kernel 7.1.0+ | systemd 260", expand=False))
    
    try:
        target_user = resolve_target_user()
        install_looking_glass_packages(target_user)
        
        mib_size, byte_size = calculate_kvmfr_size()
        configure_host_modules(mib_size)
        enforce_device_integrity()
        configure_qemu_cgroups()
        
        # Absolute correct QOM JSON formatting for QEMU commandline mapping
        xml_payload = f"""  <qemu:commandline>
    <qemu:arg value="-device"/>
    <qemu:arg value="{{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}}"/>
    <qemu:arg value="-object"/>
    <qemu:arg value="{{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/kvmfr0','size':{byte_size},'share':true}}"/>
  </qemu:commandline>"""

        console.print("\n[bold green]=== PHASE 5 COMPLETE ===[/bold green]")
        console.print("The host kernel environment, udev rules, and QEMU cgroups are fully staged.")
        
        console.print("\n[bold yellow]CRITICAL ACTION REQUIRED IN XML (sudo virsh edit <vm_name>):[/bold yellow]")
        console.print("  [cyan]1.[/cyan] Change the first line to: [bold]<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>[/bold]")
        console.print("  [cyan]2.[/cyan] Find your memory balloon and disable it to prevent DMA latency: [bold]<memballoon model='none'/>[/bold]")
        console.print("  [cyan]3.[/cyan] Paste the following block at the absolute bottom of the file, just before [bold]</domain>[/bold]:\n")
        
        # SURGICAL FIX: Bypass 'rich' entirely for the payload.
        # Printing directly to standard output prevents the library from injecting
        # artificial line breaks or box-drawing characters that complicate copy-pasting.
        console.print("[cyan]━━━━━━━━━━━━━━━━━━━━━━━━━ libvirt QOM JSON Payload ━━━━━━━━━━━━━━━━━━━━━━━━━[/cyan]")
        print(xml_payload)
        console.print("[cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/cyan]\n")

    except KeyboardInterrupt:
        console.print("\n\n[bold red]⚠ Process interrupted by operator. Exiting cleanly.[/bold red]\n")
        sys.exit(130)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Phase 2: Libvirt Modular Daemon & IPC Configuration
Target: Arch Linux (Kernel 7.1.0+), Python 3.14+, systemd 260
Scope: Monolithic daemon eradication, Modular socket activation, virtqemud config.
Philosophy: Zero-Clutter Idempotency, Strict 0MB-Idle-RAM Enforcement.
"""

import os
import sys
import re
import subprocess
from pathlib import Path
from typing import List, Tuple

# ==============================================================================
# BOOTSTRAP: Strict Privilege & Auto-Elevation
# ==============================================================================
def require_root() -> None:
    """Enforce eUID 0. Auto-elevates via sudo if executed as standard user."""
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
    from rich.table import Table
except ImportError:
    print("\n[FATAL] 'python-rich' is missing. Please ensure Phase 1 completed successfully.")
    sys.exit(1)

console = Console()

# ==============================================================================
# CONFIGURATION DEFINITIONS
# ==============================================================================
LEGACY_UNITS = [
    "libvirtd.service", "libvirtd.socket", "libvirtd-ro.socket",
    "libvirtd-admin.socket", "libvirtd-tcp.socket", "libvirtd-tls.socket"
]

PRIMARY_DRIVERS = [
    "qemu", "interface", "network", "nodedev", "nwfilter", 
    "secret", "storage", "proxy", "lxc", "ch", "vbox"
]

LOG_LOCK_DRIVERS = ["log", "lock"]

# ==============================================================================
# CORE LOGIC: SYSTEMD MANAGEMENT
# ==============================================================================
def run_sysctl(action: str, units: List[str], ignore_errors: bool = False) -> None:
    """Wrapper to cleanly execute systemctl commands without cluttering stdout."""
    if not units:
        return
    cmd = ["systemctl", action] + units
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        if not ignore_errors:
            console.print(f"[bold red]  ✖ systemctl {action} failed on some units.[/bold red]")
            sys.exit(e.returncode)

def eradicate_legacy_daemon() -> None:
    """Stops, disables, and masks all monolithic libvirtd components."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Eradicating legacy monolithic libvirtd components...[/bold]")
    
    with console.status("[cyan]Terminating and masking legacy services...[/cyan]"):
        run_sysctl("stop", LEGACY_UNITS, ignore_errors=True)
        run_sysctl("disable", LEGACY_UNITS, ignore_errors=True)
        run_sysctl("mask", LEGACY_UNITS, ignore_errors=True)
        
        # Resync systemd state tree after massive masking operation
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        
    console.print("[bold green]  ✓ Legacy daemon masked and systemd state reloaded.[/bold green]")

def enforce_pure_socket_activation() -> None:
    """Explicitly disables .service units to guarantee 0MB RAM idle usage."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Enforcing strict Socket-Activation (0MB RAM Rule)...[/bold]")
    
    services_to_disable = [f"virt{drv}d.service" for drv in PRIMARY_DRIVERS + LOG_LOCK_DRIVERS]
    
    with console.status("[cyan]Nullifying direct .service executions...[/cyan]"):
        run_sysctl("stop", services_to_disable, ignore_errors=True)
        run_sysctl("disable", services_to_disable, ignore_errors=True)
        
    console.print("[bold green]  ✓ Direct services neutralized. Systemd will strictly rely on IPC sockets.[/bold green]")

def activate_modular_sockets() -> None:
    """Enables and starts the modular socket units."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Activating modern modular sockets...[/bold]")
    
    primary_sockets = []
    for drv in PRIMARY_DRIVERS:
        primary_sockets.extend([f"virt{drv}d.socket", f"virt{drv}d-ro.socket", f"virt{drv}d-admin.socket"])
        
    log_lock_sockets = []
    for drv in LOG_LOCK_DRIVERS:
        log_lock_sockets.extend([f"virt{drv}d.socket", f"virt{drv}d-admin.socket"])
        
    all_sockets = primary_sockets + log_lock_sockets

    with console.status("[cyan]Enabling and starting modular IPC sockets...[/cyan]"):
        run_sysctl("enable", all_sockets)
        run_sysctl("start", all_sockets)
        
    console.print("[bold green]  ✓ Modular sockets dynamically bound and listening.[/bold green]")

def configure_libvirt_guests() -> None:
    """Enables libvirt-guests to ensure VMs are gracefully shutdown on host reboot."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Securing VM shutdown states...[/bold]")
    run_sysctl("enable", ["--now", "libvirt-guests.service"])
    console.print("[bold green]  ✓ libvirt-guests.service enabled and running.[/bold green]")

# ==============================================================================
# CORE LOGIC: IDEMPOTENT IN-PLACE FILE EDITING
# ==============================================================================
def enforce_virtqemud_config() -> None:
    """Idempotently parses and injects group and permission settings."""
    conf_path = Path("/etc/libvirt/virtqemud.conf")
    
    console.print("\n[bold blue]==>[/bold blue] [bold]Enforcing IPC socket permissions in virtqemud.conf...[/bold]")
    
    if not conf_path.exists():
        console.print(Panel(f"[bold red]FATAL:[/bold red] Configuration file {conf_path} does not exist. Was the libvirt package installed?", border_style="red"))
        sys.exit(1)

    original_content = conf_path.read_text(encoding="utf-8")
    new_content = original_content
    
    targets = {
        "unix_sock_group": '"libvirt"',
        "unix_sock_rw_perms": '"0770"'
    }

    for key, val in targets.items():
        # Match the key, handling existing comments and varied whitespace safely
        pattern = re.compile(rf"^\s*#?\s*{key}\s*=.*$", re.MULTILINE)
        replacement = f'{key} = {val}'
        
        if pattern.search(new_content):
            new_content = pattern.sub(replacement, new_content)
        else:
            if not new_content.endswith("\n"):
                new_content += "\n"
            new_content += f"{replacement}\n"

    if original_content == new_content:
        console.print("[bold green]  ✓ virtqemud.conf is already correctly configured. No changes made.[/bold green]")
    else:
        conf_path.write_text(new_content, encoding="utf-8")
        # Critical: If config changed, we MUST kill the running daemon so the socket spawns a fresh one
        run_sysctl("stop", ["virtqemud.service"], ignore_errors=True)
        run_sysctl("restart", ["virtqemud.socket"])
        console.print("[bold green]  ✓ virtqemud.conf successfully updated in-place.[/bold green]")

# ==============================================================================
# REPORTING & VERIFICATION
# ==============================================================================
def get_unit_status(unit_name: str) -> str:
    """Returns the precise ActiveState of a systemd unit."""
    try:
        res = subprocess.run(
            ["systemctl", "show", "-p", "ActiveState", "--value", unit_name], 
            capture_output=True, text=True, check=True
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError:
        return "unknown"

def print_verification_table() -> None:
    """Builds a rich table proving both Socket Readiness AND 0MB RAM Idle State."""
    console.print("\n[bold blue]==>[/bold blue] [bold]System Verification: Architecture Status[/bold]")
    
    table = Table(title="Libvirt Specialist Daemons", show_header=True, header_style="bold magenta")
    table.add_column("Daemon Driver", style="cyan")
    table.add_column(".socket (Doorway)", justify="center")
    table.add_column(".service (RAM Usage)", justify="center")

    critical_drivers = ["qemu", "network", "nodedev", "storage", "secret", "log", "lock"]

    with console.status("[cyan]Querying deep systemd states...[/cyan]"):
        for drv in critical_drivers:
            sock_state = get_unit_status(f"virt{drv}d.socket")
            srv_state = get_unit_status(f"virt{drv}d.service")
            
            # Format Socket
            sock_fmt = "[green]LISTEN[/green]" if sock_state == "active" else f"[red]{sock_state.upper()}[/red]"
            
            # Format Service (Should be inactive/dead to prove 0MB RAM)
            srv_fmt = "[blue]SLEEPING (0MB)[/blue]" if srv_state == "inactive" else f"[yellow]{srv_state.upper()}[/yellow]"
            
            table.add_row(f"virt{drv}d", sock_fmt, srv_fmt)

    console.print(table)
    
    # Prove Legacy Monolithic is Eradicated
    legacy_srv = get_unit_status("libvirtd.service")
    legacy_sock = get_unit_status("libvirtd.socket")
    if legacy_srv == "inactive" and legacy_sock == "inactive":
        console.print("  [bold green]✓ Legacy monolithic libvirtd is completely dead and masked.[/bold green]")
    else:
        console.print(f"  [bold red]✖ WARNING: Legacy libvirtd state anomaly detected! (Srv: {legacy_srv}, Sock: {legacy_sock})[/bold red]")

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def main() -> None:
    console.clear()
    console.print(Panel("[bold green]KVM GPU Passthrough: Phase 2[/bold green]\nTarget: Modular Daemon & IPC Architecture", expand=False))
    
    try:
        eradicate_legacy_daemon()
        enforce_pure_socket_activation()
        enforce_virtqemud_config()
        activate_modular_sockets()
        configure_libvirt_guests()
        print_verification_table()
        
        console.print("\n[bold green]=== PHASE 2 COMPLETE ===[/bold green]")
        console.print("The hypervisor 'engine' is now running securely, isolated, and highly efficiently.")
        console.print("System is ready for Phase 3 (IOMMU Mapping & VFIO Isolation).\n")

    except KeyboardInterrupt:
        console.print("\n\n[bold red]⚠ Process interrupted by operator. Exiting cleanly.[/bold red]\n")
        sys.exit(130)

if __name__ == "__main__":
    main()

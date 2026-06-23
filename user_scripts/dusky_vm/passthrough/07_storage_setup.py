#!/usr/bin/env python3
"""
Phase 1.5: KVM Storage & ACL Provisioning
Target: Arch Linux (Kernel 7.1.0+), Python 3.14+, systemd 260
Scope: Storage Provisioning, Idempotent ACL Traversal, JSON State Serialization.
Philosophy: Zero-Clutter Idempotency, Strict Access Controls.
"""

import os
import sys
import json
import glob
import readline
import subprocess
from pathlib import Path

# ==============================================================================
# BOOTSTRAP: Strict Privilege & Auto-Elevation
# ==============================================================================
def require_root() -> None:
    """Enforce eUID 0. Auto-elevates via sudo if executed as standard user."""
    if os.geteuid() != 0:
        print("\n[INFO] Administrative privileges required. Elevating via sudo...")
        try:
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except OSError as e:
            print(f"\n[FATAL] Failed to elevate privileges dynamically: {e}")
            sys.exit(1)

require_root()

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
except ImportError:
    print("\n[FATAL] 'python-rich' is missing. Please ensure Phase 1 completed successfully.")
    sys.exit(1)

console = Console()

# ==============================================================================
# CORE LOGIC
# ==============================================================================
def setup_path_autocomplete() -> None:
    """Binds readline to emulate Bash's 'read -e' path autocompletion natively."""
    def path_completer(text: str, state: int) -> str | None:
        expanded = os.path.expanduser(text)
        matches = glob.glob(expanded + '*')
        formatted = [m + '/' if os.path.isdir(m) and not m.endswith('/') else m for m in matches]
        return (formatted + [None])[state]

    readline.set_completer_delims(' \t\n;')
    readline.parse_and_bind("tab: complete")
    readline.set_completer(path_completer)

def check_acl_exists(path: Path, target_rule: str) -> bool:
    """Idempotent check to see if a specific ACL is already bound."""
    try:
        res = subprocess.run(["getfacl", "-e", str(path)], capture_output=True, text=True, check=True)
        return target_rule in res.stdout
    except subprocess.CalledProcessError:
        return False

def apply_acls(target_dir: Path) -> None:
    """Walks the directory tree granting QEMU execution traversal and strict R/W/X defaults."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Enforcing QEMU Storage Traversal ACLs...[/bold]")
    
    if not target_dir.exists():
        console.print(f"[cyan]Directory {target_dir} missing. Provisioning...[/cyan]")
        target_dir.mkdir(parents=True, exist_ok=True)
    
    # Walk up the tree ensuring QEMU has execute rights, ignoring root
    with console.status("[cyan]Validating parent directory traversal rights...", spinner="dots"):
        for parent in target_dir.parents:
            if str(parent) != "/":
                if not check_acl_exists(parent, "user:qemu:--x"):
                    subprocess.run(["setfacl", "-m", "u:qemu:x", str(parent)], check=False, stderr=subprocess.DEVNULL)
                
    # Grant full R/W/X to target directory and set defaults
    with console.status("[cyan]Applying strict R/W/X controls to target...", spinner="dots"):
        if not check_acl_exists(target_dir, "user:qemu:rwx"):
            subprocess.run(["setfacl", "-m", "u:qemu:rwx", str(target_dir)], check=True)
        if not check_acl_exists(target_dir, "default:user:qemu:rwx"):
            subprocess.run(["setfacl", "-d", "-m", "u:qemu:rwx", str(target_dir)], check=True)
            
    console.print("[bold green]  ✓ ACL Traversal and Inheritance perfectly staged.[/bold green]")

def serialize_state(target_dir: Path) -> None:
    """Serializes the selected path for Phase 6 VM Deployment."""
    state_file = Path("/tmp/kvm_storage_state.json")
    state_data = {"KVM_TARGET_DIR": str(target_dir)}
    
    # Atomic write equivalent for JSON state
    with state_file.open('w', encoding='utf-8') as f:
        json.dump(state_data, f, indent=4)
        
    state_file.chmod(0o644)
    console.print(f"[bold green]  ✓ Storage state serialized for Phase 6 pipeline: {state_file}[/bold green]")

def main() -> None:
    console.clear()
    console.print(Panel("[bold green]Phase 1.5: VM Storage Provisioning[/bold green]\nTarget: Arch Linux | Kernel 7.1.0+", expand=False))
    
    default_path = Path("/var/lib/libvirt/images")
    
    console.print("\n[bold cyan]Select Storage Architecture:[/bold cyan]")
    console.print(f"  [1] Persistent Storage [dim](Default: {default_path})[/dim]")
    console.print("  [2] Ephemeral / RAM Disk [dim](e.g., /mnt/zram1)[/dim]")
    console.print("  [3] Custom Absolute Path\n")
    
    choice = Prompt.ask("Selection", choices=["1", "2", "3"], default="1")
    
    match choice:
        case "1":
            target_dir = default_path
        case "2":
            ephemeral = Prompt.ask("Enter ephemeral drive path", default="/mnt/zram1")
            target_dir = Path(ephemeral)
        case "3":
            setup_path_autocomplete()
            console.print("[cyan]Enter absolute custom directory path (Tab-completion enabled):[/cyan]")
            custom = input("> ").strip()
            readline.set_completer(None) # Unbind
            
            target_dir = Path(custom)
            if not target_dir.is_absolute():
                console.print("[bold red]FATAL: Path must be absolute (starting with '/'). Aborting.[/bold red]")
                sys.exit(1)
        case _:
            sys.exit(1)

    apply_acls(target_dir)
    serialize_state(target_dir)
    console.print("\n[bold green]=== PHASE 1.5 COMPLETE ===[/bold green]\n")

if __name__ == "__main__":
    main()

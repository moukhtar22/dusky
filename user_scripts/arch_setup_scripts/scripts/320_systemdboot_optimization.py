#!/usr/bin/env python3
"""
Arch Linux Systemd-boot Optimizer
Architecture: Python 3.10+ | Atomic Writes | AST-style Tokenization | Rich TUI
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
    """Writes to a file atomically, with a robust 'sudo tee' fallback for ESP quirks."""
    temp_file_path = None
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', dir=file_path.parent) as tf:
            temp_file_path = Path(tf.name)
            tf.write(content)
            tf.flush()
            os.fsync(tf.fileno())

        if file_path.exists():
            try:
                stat_info = file_path.stat()
                os.chmod(temp_file_path, stat.S_IMODE(stat_info.st_mode))
                os.chown(temp_file_path, stat_info.st_uid, stat_info.st_gid)
            except OSError:
                pass # Non-fatal, FAT32 often doesn't support chown

        os.replace(temp_file_path, file_path)
        return True

    except (OSError, PermissionError) as e:
        if temp_file_path and temp_file_path.exists():
            try: temp_file_path.unlink()
            except OSError: pass
            
        try:
            res = subprocess.run(
                ["sudo", "-n", "tee", str(file_path)],
                input=content.encode(), capture_output=True, timeout=5
            )
            if res.returncode == 0:
                return True
            console.print(f"[bold red][ERR][/bold red] Sudo tee fallback failed for {file_path.name}.")
        except Exception as tee_err:
            console.print(f"[bold red][ERR][/bold red] Atomic commit failed completely: {e} | {tee_err}")
            
    finally:
        if temp_file_path and temp_file_path.exists():
            try: temp_file_path.unlink()
            except OSError: pass
            
    return False

# --- 2. Optimization Logic ---
def optimize_loader(loader_conf: Path) -> bool:
    """Enforces timeout 0 in loader.conf cleanly."""
    content = loader_conf.read_text(encoding='utf-8')
    lines = content.splitlines()
    out_lines = []
    found_timeout = False
    modified = False

    for line in lines:
        if re.match(r'^[ \t]*timeout', line):
            found_timeout = True
            if line.strip() != "timeout  0":
                out_lines.append("timeout  0")
                modified = True
            else:
                out_lines.append(line)
        else:
            out_lines.append(line)

    if not found_timeout:
        out_lines.append("timeout  0")
        modified = True

    if modified:
        if atomic_write(loader_conf, "\n".join(out_lines) + "\n"):
            console.print(f"[bold green][OK][/bold green]   Boot menu disabled (timeout set to 0).")
            return True
    else:
        console.print(f"[bold blue][INFO][/bold blue] Timeout is already 0 in loader.conf.")
    return False

def optimize_entry(entry_file: Path, do_quiet: bool, do_zswap: bool, do_aspm: bool, do_ipv6: bool, do_mem_opt: bool) -> bool:
    """Safely tokenizes options, enforcing selected parameters."""
    content = entry_file.read_text(encoding='utf-8')
    lines = content.splitlines()
    out_lines = []
    modified = False

    for line in lines:
        match = re.match(r'^([ \t]*)options([ \t]+)(.*)$', line)
        if match:
            leading_space, spacing, options_val = match.groups()
            
            # Extract tokens preserving exact whitespace architecture
            tokens = re.split(r'((?:[^\s"\']|"[^"]*"|\'[^\']*\')+)', options_val)
            
            def enforce_param(search_prefix: str, exact_target: str):
                nonlocal tokens, modified
                found = False
                for i, t in enumerate(tokens):
                    if t.strip().startswith(search_prefix):
                        found = True
                        if t.strip() != exact_target:
                            tokens[i] = exact_target
                            modified = True
                        break
                
                if not found:
                    # FIX: Join the tokens to accurately check the true ending character
                    current_str = "".join(tokens)
                    if current_str and not current_str[-1].isspace():
                        tokens.append(" ")
                    tokens.append(exact_target)
                    modified = True

            if do_quiet:
                enforce_param("quiet", "quiet")
            if do_zswap:
                enforce_param("zswap.enabled", "zswap.enabled=0")
            if do_aspm:
                enforce_param("pcie_aspm", "pcie_aspm=force")
            if do_ipv6:
                enforce_param("ipv6.disable", "ipv6.disable=1")
            if do_mem_opt:
                enforce_param("slub_debug", "slub_debug=0")
                enforce_param("init_on_alloc", "init_on_alloc=0")
                enforce_param("init_on_free", "init_on_free=0")

            out_lines.append(f"{leading_space}options{spacing}{''.join(tokens)}")
        else:
            out_lines.append(line)

    if modified:
        if atomic_write(entry_file, "\n".join(out_lines) + "\n"):
            console.print(f"[bold green][OK][/bold green]   Optimized parameters in [cyan]{entry_file.name}[/cyan].")
            return True
    else:
        console.print(f"[bold blue][INFO][/bold blue] [cyan]{entry_file.name}[/cyan] is already optimized.")
    return False

# --- 3. Main Execution Flow ---
def main():
    parser = argparse.ArgumentParser(description="Systemd-boot Optimizer")
    parser.add_argument("--auto", action="store_true", help="Run autonomously without interactive prompts.")
    args = parser.parse_args()

    # Privilege Escalation while preserving arguments
    if os.geteuid() != 0:
        console.print("[bold blue][INFO][/bold blue] Root privileges required. Elevating...")
        os.execvp("sudo", ["sudo", sys.executable, os.path.realpath(sys.argv[0])] + sys.argv[1:])

    loader_conf = Path("/boot/loader/loader.conf")
    entries_dir = Path("/boot/loader/entries")

    if not loader_conf.exists() or not entries_dir.exists():
        console.print("[bold yellow][WARN][/bold yellow] Systemd-boot configuration not found at /boot/loader/.")
        sys.exit(0)

    if not args.auto:
        os.system('clear' if os.name == 'posix' else 'cls')
    
    console.print(Panel.fit("[bold white]Arch Linux Systemd-boot Optimizer[/bold white]", border_style="cyan"))

    # Securely target ONLY primary active kernels
    target_entries = [f for f in entries_dir.glob("*.conf") if "-fallback" not in f.name]
    
    # Auto logic
    if args.auto:
        console.print("[bold blue][INFO][/bold blue] Running in autonomous mode...")
        do_timeout = True
        do_quiet = True
        do_zswap = True
        do_aspm = False # Strictly prevented in auto mode
        do_ipv6 = True
        do_mem_opt = True
    # Interactive logic
    else:
        do_timeout = Confirm.ask("[bold cyan][?][/bold cyan] Speed up boot? (Disable menu: [yellow]timeout 0[/yellow])")
        
        do_quiet = False
        do_zswap = False
        do_aspm = False
        do_ipv6 = False
        do_mem_opt = False

        if target_entries:
            do_quiet = Confirm.ask("[bold cyan][?][/bold cyan] Ensure graphical boot? (Inject [yellow]quiet[/yellow])")
            do_zswap = Confirm.ask("[bold cyan][?][/bold cyan] Disable Kernel Zswap to favor Zram? (Inject [yellow]zswap.enabled=0[/yellow])")
            do_aspm  = Confirm.ask("[bold magenta][!][/bold magenta] Force PCIe ASPM for battery? (Inject [yellow]pcie_aspm=force[/yellow])")
            do_ipv6 = Confirm.ask("[bold cyan][?][/bold cyan] Disable IPv6 to save kernel memory? (Inject [yellow]ipv6.disable=1[/yellow])", default=True)
            do_mem_opt = Confirm.ask("[bold cyan][?][/bold cyan] Apply core kernel memory optimizations? (Inject [yellow]slub_debug=0 init_on_alloc=0 init_on_free=0[/yellow])", default=True)

    if not any([do_timeout, do_quiet, do_zswap, do_aspm, do_ipv6, do_mem_opt]):
        console.print("\n[bold blue][INFO][/bold blue] No changes requested. Exiting.")
        sys.exit(0)

    console.print(f"\n[bold]:: Applying Configuration...[/bold]")

    if do_timeout:
        optimize_loader(loader_conf)

    if any([do_quiet, do_zswap, do_aspm, do_ipv6, do_mem_opt]):
        for entry in target_entries:
            optimize_entry(entry, do_quiet, do_zswap, do_aspm, do_ipv6, do_mem_opt)

    console.print(f"\n[bold green]   Optimization complete. Changes active on next reboot.[/bold green]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print(f"\n[bold red][!] Script interrupted.[/bold red]")
        sys.exit(1)

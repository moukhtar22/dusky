#!/usr/bin/env python3
"""
Dusky Core Affinity Wrapper - Final Golden Release
Optimized exclusively for Python 3.14+ | Arch Linux Kernel 7.1.2+
"""

import os
import sys
import subprocess
import argparse
import shutil
import json
import signal
import time
import termios
import tty
import select
import shlex
from pathlib import Path
from typing import Any

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    from rich.live import Live
    from rich.prompt import Confirm
except ImportError:
    print("\033[91m[X] Critical: 'rich' library missing. Install via: sudo pacman -S python-rich\033[0m")
    sys.exit(1)

# Prevent kernel suspension on background PTY terminal modifications
try:
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    signal.signal(signal.SIGTTIN, signal.SIG_IGN)
except AttributeError:
    pass

console = Console(force_interactive=True)
CACHE_FILE = Path("/var/tmp/core_runner_topology.json")
SETTINGS_FILE = Path(os.path.expanduser("~/.config/dusky/settings/core_runner"))

def flush_input() -> None:
    try:
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass

def daemonize_process() -> None:
    """Safely decouples the process from the parent terminal session."""
    if os.fork() > 0: sys.exit(0)
    os.setsid()
    os.umask(0)
    if os.fork() > 0: sys.exit(0)
    
    try: os.chdir('/')
    except OSError: pass

    sys.stdout.flush()
    sys.stderr.flush()
    fd_in = os.open(os.devnull, os.O_RDONLY)
    fd_out = os.open(os.devnull, os.O_WRONLY)
    os.dup2(fd_in, sys.stdin.fileno())
    os.dup2(fd_out, sys.stdout.fileno())
    os.dup2(fd_out, sys.stderr.fileno())
    os.close(fd_in)
    os.close(fd_out)

def extract_binary_name(cmd_args: list[str]) -> str:
    """Extracts the true binary name, skipping environment variable assignments."""
    for arg in cmd_args:
        if '=' not in arg:
            return os.path.basename(arg)
    return os.path.basename(cmd_args[0]) if cmd_args else "unknown"

def load_settings() -> dict[str, list[int]]:
    if not SETTINGS_FILE.is_file():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        if isinstance(data, dict):
            return {str(k): [int(x) for x in v] for k, v in data.items() if isinstance(v, list)}
    except Exception:
        pass
    return {}

def save_settings(settings: dict[str, list[int]]) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
        SETTINGS_FILE.chmod(0o644)
    except OSError:
        pass

# ==========================================
# Low-Level Core Utilities
# ==========================================
def safe_read(path: Path, default: str = "") -> str:
    try:
        if path.is_file():
            return path.read_text().strip()
    except OSError:
        pass
    return default

def parse_cpu_list(cpu_list_str: str) -> list[int]:
    cores: set[int] = set()
    for part in filter(None, (p.strip() for p in cpu_list_str.split(','))):
        if '-' in part:
            try:
                start, end = map(int, part.split('-'))
                cores.update(range(start, end + 1))
            except ValueError:
                pass
        elif part.isdigit():
            cores.add(int(part))
    return sorted(list(cores))

def get_core_status(cpu_id: int) -> bool:
    path = Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online")
    return safe_read(path, "1") == "1" if path.exists() else True 

def get_helper_path() -> str:
    return str(Path(__file__).parent.resolve() / "core_helper.py")

def manipulate_core_state(cpu_ids: list[int], state: str) -> bool:
    if not cpu_ids:
        return True
    flag = "--online" if state == "1" else "--offline"
    target_state = state == "1"
    
    try:
        subprocess.run(
            ['sudo', get_helper_path(), flag, ",".join(map(str, cpu_ids))],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        for _ in range(10):
            if all((get_core_status(c) == target_state) for c in cpu_ids):
                return True
            time.sleep(0.05)
        return False
    except subprocess.SubprocessError:
        return False

# ==========================================
# Topology Detection Engine
# ==========================================
def get_system_signature() -> dict[str, Any]:
    cpu_model = next((line.split(":", 1)[1].strip() for line in safe_read(Path("/proc/cpuinfo")).splitlines() if line.startswith("model name")), "unknown")
    total_cores = len(parse_cpu_list(safe_read(Path("/sys/devices/system/cpu/possible"))))
    machine_id = safe_read(Path("/etc/machine-id")) or safe_read(Path("/var/lib/dbus/machine-id"))

    return {"cpu_model": cpu_model, "total_cores": total_cores, "machine_id": machine_id}

def load_cached_topology() -> dict[int, dict[str, Any]] | None:
    if not CACHE_FILE.is_file():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        if get_system_signature() == data.get("system_signature", {}):
            return {int(k): v for k, v in data.get("topology", {}).items()}
    except Exception:
        pass
    return None

def save_cached_topology(topology: dict[int, dict[str, Any]]) -> None:
    try:
        CACHE_FILE.write_text(json.dumps({
            "system_signature": get_system_signature(),
            "topology": {str(k): v for k, v in topology.items()}
        }, indent=2))
        CACHE_FILE.chmod(0o644)
    except OSError:
        pass

def detect_topology() -> dict[int, dict[str, Any]]:
    if (cached := load_cached_topology()) is not None:
        for cpu_id, data in cached.items():
            data["online"] = get_core_status(cpu_id)
        return cached

    cpu_sysfs = Path("/sys/devices/system/cpu")
    cpu_ids = sorted([int(p.name[3:]) for p in cpu_sysfs.glob("cpu[0-9]*") if p.is_dir()])
    
    offline_cores = [c for c in cpu_ids if not get_core_status(c)]
    woke_any = manipulate_core_state(offline_cores, "1") if offline_cores else False

    topology: dict[int, dict[str, Any]] = {}
    cppc_perf = {c: int(p) for c in cpu_ids if (p := safe_read(cpu_sysfs / f"cpu{c}/acpi_cppc/highest_perf")).isdigit()}
    max_freqs = {c: int(f) for c in cpu_ids if (f := safe_read(cpu_sysfs / f"cpufreq/policy{c}/cpuinfo_max_freq")).isdigit()}
    
    cppc_mid = (min(cppc_perf.values()) + max(cppc_perf.values())) / 2.0 if len(set(cppc_perf.values())) > 1 else 0.0
    freq_mid = (min(max_freqs.values()) + max(max_freqs.values())) / 2.0 if len(set(max_freqs.values())) > 1 else 0.0
    
    smt_siblings = {c: parse_cpu_list(safe_read(cpu_sysfs / f"cpu{c}/topology/core_cpus_list")) or [c] for c in cpu_ids}

    for cpu_id in cpu_ids:
        core_type_val = safe_read(cpu_sysfs / f"cpu{cpu_id}/topology/core_type")
        c_type = "P"

        if cppc_perf and cppc_mid > 0:
            c_type = "P" if cppc_perf.get(cpu_id, 0) > cppc_mid else "E"
        elif core_type_val in {"1", "0x10", "intel_atom"}:
            c_type = "E"
        elif core_type_val in {"2", "0x20", "intel_core"}:
            c_type = "P"
        elif max_freqs and freq_mid > 0:
            c_type = "P" if max_freqs.get(cpu_id, 0) > freq_mid else "E"
        else:
            siblings = smt_siblings.get(cpu_id, [cpu_id])
            if len(siblings) > 1:
                c_type = "P"
            else:
                is_sibling_of_smt = any(cpu_id in sibs and len(sibs) > 1 for sibs in smt_siblings.values())
                c_type = "E" if not is_sibling_of_smt else "P"

        topology[cpu_id] = {
            "type": c_type,
            "online": get_core_status(cpu_id),
            "smt_group": smt_siblings.get(cpu_id, [cpu_id])
        }

    # Symmetry Failsafe
    if not (any(d["type"] == "P" for d in topology.values()) and any(d["type"] == "E" for d in topology.values())):
        for data in topology.values(): data["type"] = "P"

    save_cached_topology(topology)

    if woke_any and offline_cores:
        manipulate_core_state(offline_cores, "0")
        for cpu_id in offline_cores: topology.get(cpu_id, {})["online"] = False

    return topology

# ==========================================
# UI Rendering and TUI Logic
# ==========================================
def getch() -> str:
    """Safely captures keystrokes natively without breaking terminal layout processing."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = os.read(fd, 1).decode('utf-8', errors='ignore')
        if ch == '\x1b' and select.select([sys.stdin], [], [], 0.05)[0]:
            ch += os.read(fd, 2).decode('utf-8', errors='ignore')
        elif ch == 'g' and select.select([sys.stdin], [], [], 0.15)[0]:
            next_ch = os.read(fd, 1).decode('utf-8', errors='ignore')
            if next_ch == 'g':
                ch = 'gg'
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch

def interactive_menu_select(options: list[str], title: str, subtitle: str) -> int:
    """Allows navigating a generic menu with up/down arrows and Enter selection."""
    current_idx = 0
    if not options:
        return -1
        
    def generate_menu_panel() -> Panel:
        table = Table(box=box.SIMPLE_HEAVY, show_edge=False, show_header=False)
        table.add_column("Choice", justify="left")
        for i, opt in enumerate(options):
            cursor = "[bold yellow]>[/bold yellow]" if i == current_idx else " "
            style = "bold cyan on grey15" if i == current_idx else "white"
            table.add_row(f"{cursor} {opt}", style=style)
        return Panel(
            table,
            title=f"[bold white]{title}[/bold white]",
            subtitle=f"[dim]{subtitle}[/dim]",
            border_style="cyan",
            expand=False
        )

    with Live(generate_menu_panel(), console=console, refresh_per_second=20, transient=False, redirect_stdout=False, redirect_stderr=False) as live:
        live.update(generate_menu_panel(), refresh=True)
        sys.stdout.flush()
        sys.stderr.flush()
        while True:
            ch = getch()
            match ch:
                case '\r' | '\n':
                    return current_idx
                case '\x1b[A' | 'k' | 'h': # Up / k / h
                    current_idx = (current_idx - 1) % len(options)
                case '\x1b[B' | 'j' | 'l': # Down / j / l
                    current_idx = (current_idx + 1) % len(options)
                case 'gg':
                    current_idx = 0
                case 'G':
                    current_idx = len(options) - 1
                case '\x15': # Ctrl-U
                    current_idx = max(0, current_idx - 5)
                case '\x04': # Ctrl-D
                    current_idx = min(len(options) - 1, current_idx + 5)
                case 'q' | '\x03' | '\x1b': # Quit/Ctrl-C/ESC
                    sys.exit(130)
            live.update(generate_menu_panel())

def interactive_checklist(topology: dict[int, dict[str, Any]], cmd_name: str) -> list[int]:
    cores = sorted(list(topology.keys()))
    selected: set[int] = {c for c, d in topology.items() if d["type"] == "P"}
    if not selected:
        selected = set(cores)
    current_idx = 0

    def generate_table() -> Panel:
        table = Table(box=box.SIMPLE_HEAVY, show_edge=False)
        table.add_column(" ", justify="center", width=3)
        table.add_column("Core", justify="center", style="bold white")
        table.add_column("Type", justify="left")
        table.add_column("Status", justify="left")
        table.add_column("SMT Siblings", justify="left")

        for i, c in enumerate(cores):
            data = topology[c]
            type_str = "[bold cyan]P-Core[/bold cyan]" if data["type"] == "P" else "[bold magenta]E-Core[/bold magenta]"
            status_str = "[bold green]Online[/bold green]" if data["online"] else "[dim red]Offline[/dim red]"
            check = "[bold green]☑[/bold green]" if c in selected else "[dim white]☐[/dim white]"
            cursor = "[bold yellow]>[/bold yellow]" if i == current_idx else " "
            
            siblings = [sib for sib in data.get("smt_group", []) if sib != c]
            sibling_str = f"[dim]CPU {', '.join(map(str, siblings))}[/dim]" if siblings else "[dim]None[/dim]"
            
            table.add_row(
                f"{cursor} {check}", 
                f"CPU {c}", 
                type_str, 
                status_str, 
                sibling_str, 
                style="on grey15" if i == current_idx else ""
            )
        
        return Panel(
            table,
            title=f"[bold white]Select Target Cores for[/bold white] [bold yellow]{cmd_name}[/bold yellow]",
            subtitle="[dim]Space: Toggle | Enter: Confirm | a: Toggle All | p: P-Cores | e: E-Cores | j/k/gg/G/Ctrl-U/D: Vim | q: Quit[/dim]",
            border_style="cyan"
        )

    with Live(generate_table(), console=console, refresh_per_second=20, transient=False, redirect_stdout=False, redirect_stderr=False) as live:
        live.update(generate_table(), refresh=True)
        sys.stdout.flush()
        sys.stderr.flush()
        while True:
            ch = getch()
            match ch:
                case '\r' | '\n':
                    break
                case ' ':
                    selected.symmetric_difference_update({cores[current_idx]})
                case '\x1b[A' | 'k':
                    current_idx = max(0, current_idx - 1)
                case '\x1b[B' | 'j':
                    current_idx = min(len(cores) - 1, current_idx + 1)
                case 'gg':
                    current_idx = 0
                case 'G':
                    current_idx = len(cores) - 1
                case '\x15': # Ctrl-U
                    current_idx = max(0, current_idx - 5)
                case '\x04': # Ctrl-D
                    current_idx = min(len(cores) - 1, current_idx + 5)
                case 'a':
                    selected = set() if len(selected) == len(cores) else set(cores)
                case 'e':
                    e_cores = {c for c, d in topology.items() if d["type"] == "E"}
                    if e_cores:
                        if e_cores.issubset(selected):
                            selected.difference_update(e_cores)
                        else:
                            selected.update(e_cores)
                case 'p':
                    p_cores = {c for c, d in topology.items() if d["type"] == "P"}
                    if p_cores:
                        if p_cores.issubset(selected):
                            selected.difference_update(p_cores)
                        else:
                            selected.update(p_cores)
                case 'q' | '\x03' | '\x1b':
                    sys.exit(130)
            
            live.update(generate_table())

    return sorted(list(selected))

def interactive_launcher_menu(topology: dict[int, dict[str, Any]]) -> None:
    while True:
        menu_options = [
            "Run New Command...",
            "App Profiles & History...",
            "View CPU Topology Status",
            "Show Help Dashboard",
            "Exit"
        ]
        
        selected_idx = interactive_menu_select(
            menu_options,
            "Dusky Core Runner - Main Menu",
            "Up/Down: Navigate | Enter: Select | Esc/q: Exit"
        )
        
        if selected_idx < 0:
            return
            
        choice = menu_options[selected_idx]
        
        if choice == "Exit":
            return
        elif choice == "View CPU Topology Status":
            display_status(topology)
            console.print("\nPress Enter to return to main menu...")
            try: input()
            except (KeyboardInterrupt, EOFError): return
        elif choice == "Show Help Dashboard":
            print_beautiful_help()
            console.print("\nPress Enter to return to main menu...")
            try: input()
            except (KeyboardInterrupt, EOFError): return
        elif choice == "App Profiles & History...":
            manage_profiles_menu(topology)
        elif choice == "Run New Command...":
            run_new_command_flow(topology)

def run_new_command_flow(topology: dict[int, dict[str, Any]]) -> None:
    flush_input()
    console.print("[bold yellow]Enter command to run: [/bold yellow]", end="")
    try:
        custom_cmd_str = input().strip()
    except (KeyboardInterrupt, EOFError):
        return
    if not custom_cmd_str:
        console.print("[bold red]Error: No command entered.[/bold red]")
        time.sleep(1)
        return
        
    cmd_args = shlex.split(custom_cmd_str)
    if not cmd_args:
        console.print("[bold red]Error: No command entered.[/bold red]")
        time.sleep(1)
        return
        
    executable_name = extract_binary_name(cmd_args)
    
    settings = load_settings()
    saved_cores = settings.get(executable_name)

    affinity_options = []
    if saved_cores:
        saved_str = ",".join(map(str, saved_cores))
        affinity_options.append(f"Use Saved Cores ({saved_str})")
    
    affinity_options.extend([
        "P-Cores only (Fast)",
        "E-Cores only (Power saving)",
        "All Cores",
        "Custom selection (Interactive TUI)"
    ])
    
    aff_idx = interactive_menu_select(
        affinity_options,
        f"Select Core Affinity for {executable_name}",
        "Up/Down: Navigate | Enter: Select"
    )
    
    if aff_idx < 0:
        return
        
    target_cores: list[int] = []
    aff_choice = affinity_options[aff_idx]
    should_prompt_save = False
    
    if aff_choice.startswith("Use Saved Cores"):
        target_cores = saved_cores
    elif aff_choice.startswith("P-Cores"):
        target_cores = [c for c, d in topology.items() if d["type"] == "P"]
        should_prompt_save = True
    elif aff_choice.startswith("E-Cores"):
        target_cores = [c for c, d in topology.items() if d["type"] == "E"]
        if not target_cores:
            console.print("[bold yellow]Notice:[/bold yellow] No E-Cores exist. Falling back to P-Cores.")
            target_cores = [c for c, d in topology.items() if d["type"] == "P"]
        should_prompt_save = True
    elif aff_choice == "All Cores":
        target_cores = list(topology.keys())
        should_prompt_save = True
    else:
        target_cores = interactive_checklist(topology, executable_name)
        if not target_cores:
            console.print("[bold red]Aborted.[/bold red]")
            return
        should_prompt_save = True

    if should_prompt_save:
        flush_input()
        if Confirm.ask(f"\n[bold yellow]Save cores {target_cores} as default for {executable_name}?[/bold yellow]", default=True):
            settings[executable_name] = target_cores
            save_settings(settings)
            console.print("[bold green]✔ Preference saved.[/bold green]")

    flush_input()
    detach = Confirm.ask("\n[bold yellow]Run detached (background)?[/bold yellow]", default=False)
    
    offline_targets = [c for c in target_cores if not topology[c]["online"]]
    if offline_targets:
        console.print(Panel(f"[bold yellow]Waking offline targets {offline_targets}...[/bold yellow]", border_style="yellow", expand=False))
        if not manipulate_core_state(offline_targets, "1"):
            console.print("[bold red]✖ ACPI Error: Hardware modification failed.[/bold red]")
            sys.exit(1)

    target_cores_str = ",".join(map(str, target_cores))
    console.print(f"[bold green]🚀 Bounding execution to cores:[/bold green] [white]{target_cores_str}[/white]")
    
    taskset_cmd = f"exec taskset -c {target_cores_str} {custom_cmd_str}"
    
    if detach:
        daemonize_process()

    sys.exit(run_target_command(taskset_cmd, offline_targets))

def manage_profiles_menu(topology: dict[int, dict[str, Any]]) -> None:
    while True:
        settings = load_settings()
        if not settings:
            console.print(Panel("[bold yellow]No profiles saved yet.[/bold yellow]\nRun a command first to save a default core selection.", expand=False))
            console.print("\nPress Enter to return to main menu...")
            try:
                flush_input()
                input()
            except (KeyboardInterrupt, EOFError): pass
            return
            
        menu_options = []
        app_list = sorted(list(settings.keys()))
        for app in app_list:
            cores_str = ",".join(map(str, settings[app]))
            menu_options.append(f"{app} (Cores: {cores_str})")
        menu_options.append("Back to Main Menu")
        
        selected_idx = interactive_menu_select(
            menu_options,
            "App Profiles & History",
            "Select an application profile to manage"
        )
        
        if selected_idx < 0 or selected_idx == len(app_list):
            return
            
        app_name = app_list[selected_idx]
        saved_cores = settings[app_name]
        
        action_options = [
            f"Launch {app_name}",
            f"Modify Default Cores for {app_name}",
            f"Delete profile for {app_name}",
            "Back to Profiles List"
        ]
        
        action_idx = interactive_menu_select(
            action_options,
            f"Manage Profile: {app_name}",
            "Select an action"
        )
        
        if action_idx < 0 or action_idx == 3:
            continue
            
        action = action_options[action_idx]
        
        if action.startswith("Launch"):
            flush_input()
            console.print(f"[bold yellow]Enter optional arguments to append to {app_name} (press Enter to run as is): [/bold yellow]", end="")
            try:
                args_str = input().strip()
            except (KeyboardInterrupt, EOFError):
                continue
                
            custom_cmd_str = f"{app_name} {args_str}" if args_str else app_name
            
            flush_input()
            detach = Confirm.ask("\n[bold yellow]Run detached (background)?[/bold yellow]", default=False)
            
            offline_targets = [c for c in saved_cores if not topology[c]["online"]]
            if offline_targets:
                console.print(Panel(f"[bold yellow]Waking offline targets {offline_targets}...[/bold yellow]", border_style="yellow", expand=False))
                if not manipulate_core_state(offline_targets, "1"):
                    console.print("[bold red]✖ ACPI Error: Hardware modification failed.[/bold red]")
                    sys.exit(1)

            target_cores_str = ",".join(map(str, saved_cores))
            console.print(f"[bold green]🚀 Bounding execution to cores:[/bold green] [white]{target_cores_str}[/white]")
            
            taskset_cmd = f"exec taskset -c {target_cores_str} {custom_cmd_str}"
            
            if detach:
                daemonize_process()

            sys.exit(run_target_command(taskset_cmd, offline_targets))
            
        elif action.startswith("Modify"):
            affinity_options = [
                "P-Cores only (Fast)",
                "E-Cores only (Power saving)",
                "All Cores",
                "Custom selection (Interactive TUI)"
            ]
            aff_idx = interactive_menu_select(
                affinity_options,
                f"Modify Default Cores for {app_name}",
                "Select new default core layout"
            )
            if aff_idx < 0:
                continue
            
            new_cores = []
            aff_choice = affinity_options[aff_idx]
            if aff_choice.startswith("P-Cores"):
                new_cores = [c for c, d in topology.items() if d["type"] == "P"]
            elif aff_choice.startswith("E-Cores"):
                new_cores = [c for c, d in topology.items() if d["type"] == "E"]
                if not new_cores:
                    console.print("[bold yellow]Notice:[/bold yellow] No E-Cores exist. Falling back to P-Cores.")
                    new_cores = [c for c, d in topology.items() if d["type"] == "P"]
            elif aff_choice == "All Cores":
                new_cores = list(topology.keys())
            else:
                new_cores = interactive_checklist(topology, app_name)
                if not new_cores:
                    console.print("[bold red]Aborted.[/bold red]")
                    time.sleep(1)
                    continue
            
            settings[app_name] = new_cores
            save_settings(settings)
            console.print("[bold green]✔ Default cores updated successfully.[/bold green]")
            time.sleep(1)
            
        elif action.startswith("Delete"):
            flush_input()
            if Confirm.ask(f"[bold red]Are you sure you want to delete the profile for {app_name}?[/bold red]", default=False):
                del settings[app_name]
                save_settings(settings)
                console.print("[bold green]✔ Profile deleted.[/bold green]")
                time.sleep(1)

def print_beautiful_help() -> None:
    console.print(Panel("[bold green]Dusky Core Affinity Wrapper[/bold green]", border_style="green", box=box.ROUNDED, expand=False))
    
    usage = Table(show_header=False, box=None, padding=(0, 4, 0, 0))
    usage.add_row("  [bold green]core[/bold green]", "[dim]Interactive Launcher Menu (Default)[/dim]")
    usage.add_row("  [bold green]core[/bold green] [white]<command>[/white]", "[dim]Interactive Core Selection Checklist[/dim]")
    usage.add_row("  [bold green]core[/bold green] [white]<core1> <core2> ... <command>[/white]", "[dim]Direct core index routing[/dim]")
    console.print("\n[bold yellow]Usage Patterns:[/bold yellow]", usage)

def display_status(topology: dict[int, dict[str, Any]]) -> None:
    console.print(Panel("[bold cyan]System Hardware Topology Status[/bold cyan]", expand=False))
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Core ID", justify="center")
    table.add_column("Architecture", justify="center")
    table.add_column("Current State", justify="center")

    for cpu_id, data in topology.items():
        arch_str = "[bold cyan]P-Core[/bold cyan]" if data["type"] == "P" else "[bold magenta]E-Core[/bold magenta]"
        state_str = "[bold green]● Online[/bold green]" if data["online"] else "[dim red]○ Offline[/dim red]"
        table.add_row(f"CPU {cpu_id}", arch_str, state_str)

    console.print(table)

def run_target_command(taskset_cmd: list[str] | str, offline_targets_to_restore: list[int]) -> int:
    proc = None
    original_handlers = {}
    
    def signal_handler(signum: int, frame: Any) -> None:
        if proc and proc.poll() is None:
            try: proc.send_signal(signum)
            except OSError: pass

    try:
        for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGQUIT, signal.SIGHUP]:
            original_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, signal_handler)

        proc = subprocess.Popen(taskset_cmd, shell=isinstance(taskset_cmd, str))
        return proc.wait()
    except Exception as e:
        console.print(f"[bold red]Execution Error:[/bold red] {e}")
        return 1
    finally:
        for sig, handler in original_handlers.items():
            signal.signal(sig, handler)
        
        if offline_targets_to_restore:
            console.print(f"\n[bold yellow]Putting cores {offline_targets_to_restore} back to sleep...[/bold yellow]")
            if manipulate_core_state(offline_targets_to_restore, "0"):
                console.print("[bold green]✔ Hardware asleep.[/bold green]")

def main() -> None:
    if not shutil.which("taskset"):
        console.print("[bold red]Critical Error:[/bold red] 'taskset' utility missing.")
        sys.exit(1)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("-s", "--status", action="store_true")
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("-t", "--type", choices=["pcores", "ecores", "all"], default=None)
    parser.add_argument("-c", "--custom", type=str)
    parser.add_argument("-d", "--detach", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    
    args = parser.parse_args()

    if args.help: return print_beautiful_help()
    topology = detect_topology()
    if args.status: return display_status(topology)

    if not args.command:
        if sys.stdin.isatty():
            return interactive_launcher_menu(topology)
        else:
            console.print("[bold red]Execution Error:[/bold red] No target command provided.")
            sys.exit(1)
            
    if args.command[0] == "--":
        args.command = args.command[1:]
        if not args.command: sys.exit(1)

    args.command[0] = os.path.expanduser(args.command[0])
    target_cores: list[int] = []

    if args.custom:
        target_cores = parse_cpu_list(args.custom)
        if invalid_cores := [c for c in target_cores if c not in topology]:
            console.print(f"[bold red]Hardware Error:[/bold red] Cores {invalid_cores} do not exist.")
            sys.exit(1)
    elif args.type:
        match args.type:
            case "all": target_cores = list(topology.keys())
            case "pcores": target_cores = [c for c, d in topology.items() if d["type"] == "P"]
            case "ecores":
                target_cores = [c for c, d in topology.items() if d["type"] == "E"]
                if not target_cores:
                    console.print("[bold yellow]Notice:[/bold yellow] No E-Cores exist. Falling back to P-Cores.")
                    target_cores = [c for c, d in topology.items() if d["type"] == "P"]
    else:
        cmd_name = extract_binary_name(args.command)
        settings = load_settings()
        if cmd_name in settings and not args.interactive:
            target_cores = settings[cmd_name]
            target_cores_str = ",".join(map(str, target_cores))
            console.print(f"[bold green]🚀 Loading saved core configuration for {cmd_name}:[/bold green] [white]{target_cores_str}[/white]")
        elif sys.stdin.isatty():
            target_cores = interactive_checklist(topology, cmd_name)
            if not target_cores:
                console.print("[bold red]Aborted.[/bold red]")
                sys.exit(130)
            flush_input()
            if Confirm.ask(f"\n[bold yellow]Save cores {target_cores} as default for {cmd_name}?[/bold yellow]", default=True):
                settings[cmd_name] = target_cores
                save_settings(settings)
                console.print("[bold green]✔ Preference saved.[/bold green]")
            if not args.detach:
                flush_input()
                args.detach = Confirm.ask("\n[bold yellow]Run detached (background)?[/bold yellow]", default=False)
        else:
            target_cores = [c for c, d in topology.items() if d["type"] == "P"]

    if not target_cores:
        console.print("[bold red]Fatal Error:[/bold red] Unable to map target cores.")
        sys.exit(1)

    offline_targets = [c for c in target_cores if not topology[c]["online"]]
    if offline_targets:
        console.print(Panel(f"[bold yellow]Waking offline targets {offline_targets}...[/bold yellow]", border_style="yellow", expand=False))
        if not manipulate_core_state(offline_targets, "1"):
            console.print("[bold red]✖ ACPI Error: Hardware modification failed.[/bold red]")
            sys.exit(1)

    target_cores_str = ",".join(map(str, target_cores))
    console.print(f"[bold green]🚀 Bounding execution to cores:[/bold green] [white]{target_cores_str}[/white]")
    
    taskset_cmd = ["taskset", "-c", target_cores_str] + args.command
    
    if args.detach:
        daemonize_process()

    sys.exit(run_target_command(taskset_cmd, offline_targets))

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: sys.exit(130)

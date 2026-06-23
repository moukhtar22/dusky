#!/usr/bin/env python3
import subprocess
import sys
import os
import re
import threading
from pathlib import Path

# Pre-flight check for the rich library
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
except ImportError:
    print("\n[CRITICAL ERROR] The 'rich' library is not installed.")
    print("Please install it before running this script.")
    print("Run: sudo pacman -S python-rich")
    sys.exit(1)

console = Console()

def keep_sudo_alive(stop_event: threading.Event):
    """
    Background daemon thread to prevent the 'sudo' timestamp from timing out.
    Refreshes the credential cache every 5 minutes invisibly to prevent deadlocks
    during long package downloads over slow network connections.
    """
    while not stop_event.is_set():
        subprocess.run(
            ["sudo", "-v"], 
            check=False, 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        stop_event.wait(300)

def check_root_and_locks():
    """Ensure proper privileges and intelligently check for pacman database locks."""
    if os.geteuid() == 0:
        console.print("[bold red]CRITICAL: Do not run this script as root.[/bold red]")
        console.print("Run it as your normal user. Sudo will be invoked securely when needed.")
        sys.exit(1)
        
    db_lck = Path("/var/lib/pacman/db.lck")
    if db_lck.exists():
        console.print(f"[bold red]CRITICAL: Pacman database is locked ({db_lck}).[/bold red]")
        console.print("Another package manager is actively running, or an update crashed.")
        console.print("Do [bold red]NOT[/bold red] blindly delete this file. Check what is using it first:")
        console.print("  [cyan]fuser /var/lib/pacman/db.lck[/cyan]")
        console.print("Only remove the lock file if you are absolutely certain pacman is not running.")
        sys.exit(1)

def enable_multilib_safely() -> bool:
    """Intelligently parses and modifies pacman.conf to enable [multilib] safely."""
    pacman_conf = Path('/etc/pacman.conf')
    if not pacman_conf.exists():
        console.print("[bold red]Critical system file /etc/pacman.conf not found![/bold red]")
        sys.exit(1)

    try:
        lines = pacman_conf.read_text().splitlines()
    except Exception as e:
        console.print(f"[bold red]Failed to read pacman.conf: {e}[/bold red]")
        sys.exit(1)

    new_lines = []
    modified = False
    
    # Pass 1: Check if the repository is already fully enabled
    multilib_active = False
    include_active = False
    in_multilib_check = False
    for line in lines:
        if re.match(r'^\s*\[multilib\]\s*$', line):
            multilib_active = True
            in_multilib_check = True
        elif in_multilib_check and re.match(r'^\s*\[.*\]\s*$', line):
            in_multilib_check = False
        elif in_multilib_check and re.match(r'^\s*Include\s*=', line):
            include_active = True
            
    if multilib_active and include_active:
        return True

    # Pass 2: Carefully uncomment [multilib] and its Include directive
    in_multilib_edit = False
    for line in lines:
        if re.match(r'^#\s*\[multilib\]\s*$', line):
            new_lines.append('[multilib]')
            in_multilib_edit = True
            modified = True
            continue

        if in_multilib_edit:
            # Stop editing if we hit a new section bracket
            if re.match(r'^#?\s*\[.*\]\s*$', line) and not 'multilib' in line:
                in_multilib_edit = False
            # Uncomment the Include directive safely
            elif re.match(r'^#\s*Include\s*=', line):
                new_lines.append(line.replace('#', '', 1))
                modified = True
                continue
                
        new_lines.append(line)

    if modified:
        temp_conf = Path('/tmp/pacman_new.conf')
        temp_conf.write_text('\n'.join(new_lines) + '\n')
        run_command(f"sudo cp {temp_conf} /etc/pacman.conf", "Applying multilib configuration to pacman.conf", show_command=False)
        return True
        
    return False

def get_installed_flatpaks() -> list:
    """Dynamically fetches a list of all installed Flatpak Application IDs."""
    try:
        result = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application"],
            capture_output=True, text=True, check=True
        )
        return [app_id.strip() for app_id in result.stdout.splitlines() if app_id.strip()]
    except subprocess.CalledProcessError:
        return []

def integrate_desktop_entries():
    """
    Idempotently symlinks Flatpak .desktop files to the user's local directory.
    Guarantees safety by NEVER deleting physical files created by the user, and properly
    evaluates broken symlinks to prevent FileExistsError crashes on redundant execution.
    """
    user_apps_dir = Path.home() / ".local/share/applications"
    user_apps_dir.mkdir(parents=True, exist_ok=True)
    
    system_export_dir = Path("/var/lib/flatpak/exports/share/applications")
    user_export_dir = Path.home() / ".local/share/flatpak/exports/share/applications"
    
    # 1. Clean broken symlinks safely
    try:
        for f in user_apps_dir.iterdir():
            if f.is_symlink() and not f.exists():
                f.unlink()
    except Exception as e:
        console.print(f"[yellow]Warning while cleaning symlinks: {e}[/yellow]")

    # 2. Bridge active Flatpak desktop entries
    for app_id in get_installed_flatpaks():
        desktop_file = f"{app_id}.desktop"
        target_path = None
        
        if (system_export_dir / desktop_file).exists():
            target_path = system_export_dir / desktop_file
        elif (user_export_dir / desktop_file).exists():
            target_path = user_export_dir / desktop_file
            
        if target_path:
            symlink_path = user_apps_dir / desktop_file
            
            # Use is_symlink() alongside exists() to explicitly catch broken links
            if symlink_path.is_symlink() or symlink_path.exists():
                if not symlink_path.is_symlink():
                    console.print(f"[yellow]Skipping {desktop_file} - Custom user file detected.[/yellow]")
                    continue
                    
                # Evaluate existing target to avoid redundant writes
                try:
                    if os.readlink(symlink_path) == str(target_path):
                        continue
                except OSError:
                    pass 
                
                # Unlink incorrect or broken symlink
                symlink_path.unlink() 
            
            symlink_path.symlink_to(target_path)
            
    subprocess.run(["update-desktop-database", str(user_apps_dir)], capture_output=True)

def run_command(command: str, description: str, critical: bool = True, show_command: bool = True) -> bool:
    """
    Executes a shell command natively. Stdout/Stderr are NOT piped. This prevents 
    deadlocks by ensuring PGP prompts, Polkit passwords, and downloads are fully interactive.
    """
    console.print(f"\n[bold cyan]Task:[/bold cyan] {description}")
    if show_command:
        console.print(f"[dim]{command}[/dim]")
    
    if not Confirm.ask("[bold yellow]Execute this step?[/bold yellow]", default=True):
        console.print("[dim]Skipped by user.[/dim]")
        return True

    console.print("[dim]" + "─" * 60 + "[/dim]")
    
    try:
        result = subprocess.run(command, shell=True)
        console.print("[dim]" + "─" * 60 + "[/dim]")
        
        if result.returncode == 0:
            console.print("[bold green]✔ Success[/bold green]")
            return True
        else:
            console.print(f"[bold red]✘ Failed with exit code {result.returncode}[/bold red]")
            if critical:
                console.print("[bold red]A critical step failed. Aborting script to maintain system stability.[/bold red]")
                sys.exit(1)
            return False
    except Exception as e:
        console.print(f"[bold red]✘ Execution error: {e}[/bold red]")
        if critical:
            sys.exit(1)
        return False

def main():
    console.clear()
    console.print(Panel.fit(
        "[bold magenta]Arch Linux Universal Gaming Architecture[/bold magenta]\n"
        "[white]Idempotent automated installer for Drivers, Steam, Bottles, Gamescope, & Flatpaks.[/white]",
        border_style="magenta"
    ))

    check_root_and_locks()

    console.print("\n[cyan]Authenticating with sudo for system operations...[/cyan]")
    subprocess.run(["sudo", "-v"], check=True)

    # Initialize the sudo keep-alive daemon
    stop_sudo_event = threading.Event()
    sudo_thread = threading.Thread(target=keep_sudo_alive, args=(stop_sudo_event,), daemon=True)
    sudo_thread.start()

    try:
        run_command(
            "sudo pacman -Syu --noconfirm",
            "Synchronize package databases and apply core system updates."
        )

        if enable_multilib_safely():
            console.print(Panel("The \[multilib] repository is [bold green]ENABLED[/bold green].", style="green"))
            run_command(
                "sudo pacman -Sy",
                "Refresh package databases to include 32-bit libraries.",
                show_command=False
            )
        else:
            console.print(Panel(
                "Failed to parse or enable the \[multilib] repository automatically.\n"
                "Please edit /etc/pacman.conf manually to enable it.",
                style="red"
            ))

        console.print("\n[bold cyan]Select your GPU Vendor for strictly required Vulkan Drivers:[/bold cyan]")
        console.print("1. AMD (Radeon)")
        console.print("2. NVIDIA (GeForce)")
        console.print("3. Intel (Arc/iGPU)")
        console.print("4. Skip (I manage my own graphics drivers)")
        
        gpu_choice = Prompt.ask("Enter choice", choices=["1", "2", "3", "4"], default="4")
        
        if gpu_choice == "1":
            run_command(
                "sudo pacman -S --needed --noconfirm vulkan-radeon lib32-vulkan-radeon mesa lib32-mesa",
                "Install native AMD 32-bit/64-bit Vulkan & Mesa drivers."
            )
        elif gpu_choice == "2":
            run_command(
                "sudo pacman -S --needed --noconfirm nvidia-utils lib32-nvidia-utils egl-wayland",
                "Install NVIDIA proprietary utilities, Wayland EGL bridge, and 32-bit Vulkan drivers."
            )
        elif gpu_choice == "3":
            run_command(
                "sudo pacman -S --needed --noconfirm vulkan-intel lib32-vulkan-intel mesa lib32-mesa",
                "Install native Intel 32-bit/64-bit Vulkan & Mesa drivers."
            )

        run_command(
            "sudo pacman -S --needed --noconfirm steam lutris wine flatpak gamemode lib32-gamemode mangohud lib32-mangohud gamescope desktop-file-utils",
            "Install Steam, Lutris, System Wine, Flatpak daemon, Gamescope, GameMode, MangoHud, and Utils."
        )

        run_command(
            "flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo && "
            "sudo flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo",
            "Initialize Flathub remote server (User & System scope)."
        )

        flatpak_apps = [
            ("Bottles", "com.usebottles.bottles"),
            ("Flatseal", "com.github.tchx84.Flatseal"),
            ("ProtonPlus", "com.vysp3r.ProtonPlus")
        ]

        for app_name, app_id in flatpak_apps:
            # Reverted to strictly invoke sudo to bypass Polkit dependency on barebones WMs
            run_command(
                f"sudo flatpak install --system flathub {app_id} -y",
                f"Install {app_name} securely via Flatpak sandbox.",
                critical=False 
            )

        run_command(
            "sudo flatpak override --system --filesystem=host com.usebottles.bottles",
            "Grant Bottles global filesystem permissions to detect secondary/game drives natively.",
            critical=False
        )

        with console.status("[bold green]Bridging Flatpaks into Application Launchers...[/bold green]", spinner="dots"):
            integrate_desktop_entries()
        console.print("[bold green]✔ Application Launcher integration complete![/bold green]")

        console.print(Panel.fit(
            "[bold green]✔ Architecture Established![/bold green]\n"
            "Your Arch Linux system is fully armed for native games, Proton, and modern Windows repacks.\n\n"
            "[bold]Important Notes & Next Steps:[/bold]\n"
            "1. [yellow]LOGOUT AND LOGIN REQUIRED:[/yellow] To ensure Flatpak icons load correctly in your launcher, you must reboot or re-login.\n"
            "2. Open your launcher — native apps like [cyan]Steam[/cyan] and [cyan]Lutris[/cyan] are ready to launch.\n"
            "3. [cyan]Bottles[/cyan] has been auto-configured to detect secondary storage drives without needing Flatseal tweaks.\n"
            "4. Use [cyan]Lutris[/cyan] to centralize your GOG, Epic Games, and Amazon libraries.\n"
            "5. [bold red]CRITICAL (If using Heavy Repacks):[/bold red] Check the 'Limit installer to 2GB' box in Bottles to prevent OOM crashes.",
            border_style="green"
        ))

    finally:
        stop_sudo_event.set()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]Script terminated abruptly by user. Exiting safely.[/bold red]")
        sys.exit(0)

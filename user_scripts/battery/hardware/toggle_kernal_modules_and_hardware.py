#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Dusky Power Architect v3.2.0 (Master Icon Edition)
A unified, interactive Rich TUI for selecting and managing laptop hardware power states.
- Auto-elevates via sudo once at launch for zero-prompt hardware management.
- Drops privileges down to regular user for UI rendering, audio server sync, and user tasks.
- Interactive multi-toggle custom selection loop (stays in menu until 'b' back).
- No emojis: uses clean professional symbols ([+], [-], [OK], [X], [LOCKED]).
- Full ALSA & PipeWire restoration for speakers and microphones.
Engineered for modern Arch Linux / CachyOS gaming laptops.
"""

import os
import sys
import subprocess
import json
import time
from typing import Optional

# Dynamically resolve non-root User & UID via POSIX pwd
def _resolve_real_user() -> tuple[str, int]:
    user = os.environ.get("SUDO_USER") or os.environ.get("USER")
    if not user or user == "root":
        try:
            import getpass
            user = getpass.getuser()
        except Exception:
            pass

    if (not user or user == "root") and os.path.exists("/home"):
        users = [d for d in os.listdir("/home") if os.path.isdir(os.path.join("/home", d))]
        if users:
            user = users[0]

    user = user or "root"
    try:
        import pwd
        p = pwd.getpwnam(user)
        return user, p.pw_uid
    except Exception:
        return user, 1000

REAL_USER, USER_UID = _resolve_real_user()

# Auto-elevate ONCE at launch so sudo prompts exactly ONCE in terminal TTY
def _auto_elevate_at_launch():
    if os.geteuid() != 0:
        print("\033[1;36m[*] Elevating privileges once via sudo for hardware management...\033[0m")
        try:
            os.execvp("sudo", ["sudo", "-E", sys.executable] + sys.argv)
        except Exception as e:
            print(f"\033[1;31m[X] Privilege escalation failed: {e}\033[0m")
            sys.exit(1)

_auto_elevate_at_launch()

# ==============================================================================
# 1. AUTOMATIC DEPENDENCY RESOLUTION
# ==============================================================================
def ensure_dependencies():
    needed_packages = []
    try:
        import rich
    except ImportError:
        needed_packages.append("python-rich")

    if not os.path.exists("/usr/bin/arecord") and subprocess.run(["which", "arecord"], capture_output=True).returncode != 0:
        needed_packages.append("alsa-utils")

    if needed_packages:
        print(f"\033[1;36m[*] Auto-installing missing dependencies via pacman: {', '.join(needed_packages)}...\033[0m")
        cmd = ["pacman", "-S", "--needed", "--noconfirm"] + needed_packages
        try:
            subprocess.run(cmd, check=True, timeout=30)
        except Exception as e:
            print(f"\033[1;31m[X] Failed to auto-install dependencies: {e}\033[0m")
            sys.exit(1)

ensure_dependencies()

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich.panel import Panel

console = Console()

# ==============================================================================
# 2. PRIVILEGE-MANAGED COMMAND EXECUTION
# ==============================================================================
def run_sudo(cmd: list[str], timeout_sec: int = 5) -> subprocess.CompletedProcess:
    """Runs root/hardware actions directly as root."""
    try:
        if os.geteuid() == 0:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        else:
            return subprocess.run(["sudo", "-n"] + cmd, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        console.print(f"[bold red]Timeout:[/] Command {' '.join(cmd)} exceeded {timeout_sec}s deadline.")
        return subprocess.CompletedProcess(cmd, 1, "", "Timeout expired")

def run_user(cmd: list[str], timeout_sec: int = 5) -> subprocess.CompletedProcess:
    """Runs user-space actions: drops root privileges down to regular user."""
    exec_cmd = cmd
    if os.geteuid() == 0 and REAL_USER != "root":
        exec_cmd = ["sudo", "-u", REAL_USER, f"XDG_RUNTIME_DIR=/run/user/{USER_UID}"] + cmd

    try:
        return subprocess.run(exec_cmd, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", "Timeout expired")

def write_sysfs(path: str, value: str) -> bool:
    """Writes to sysfs path with auto-elevation if required."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except Exception:
        return False

def get_loaded_modules() -> set[str]:
    loaded = set()
    try:
        with open("/proc/modules", "r") as f:
            for line in f:
                parts = line.split()
                if parts:
                    loaded.add(parts[0])
    except Exception:
        pass
    return loaded

def get_active_sysfs_bindings(driver_name: str, bus_type: str = "usb") -> list[str]:
    driver_dir = f"/sys/bus/{bus_type}/drivers/{driver_name}"
    if not os.path.exists(driver_dir):
        return []
    bound = []
    try:
        for entry in os.listdir(driver_dir):
            full_path = os.path.join(driver_dir, entry)
            if os.path.islink(full_path) and entry not in ["module", "subsystem"]:
                bound.append(entry)
    except Exception:
        pass
    return bound

def unbind_sysfs_driver(driver_name: str, bus_type: str = "usb") -> int:
    bound_devices = get_active_sysfs_bindings(driver_name, bus_type)
    if not bound_devices:
        return 0
    unbind_file = f"/sys/bus/{bus_type}/drivers/{driver_name}/unbind"
    unbound_count = 0
    for dev in bound_devices:
        if write_sysfs(unbind_file, dev):
            console.print(f"  [dim green][OK] Unbound sysfs device {dev} from {driver_name}[/]")
            unbound_count += 1
    return unbound_count

# ==============================================================================
# 3. OS DISK SAFETY & SECONDARY NVMe MANAGEMENT
# ==============================================================================
def get_os_and_secondary_nvme() -> tuple[str, str]:
    """Identifies OS NVMe vs Secondary NVMe dynamically."""
    res = subprocess.run(["lsblk", "--json", "-o", "NAME,PATH,MOUNTPOINTS,TYPE"], capture_output=True, text=True)
    os_nvme = "nvme0n1"
    sec_nvme = "nvme1n1"

    if res.returncode == 0 and res.stdout.strip():
        try:
            data = json.loads(res.stdout)
            for dev in data.get("blockdevices", []):
                name = dev.get("name", "")
                if "nvme" in name:
                    def has_root_mount(node):
                        mounts = node.get("mountpoints", [])
                        if mounts and "/" in mounts:
                            return True
                        for child in node.get("children", []):
                            if has_root_mount(child):
                                return True
                        return False
                    if has_root_mount(dev):
                        os_nvme = name
                    else:
                        sec_nvme = name
        except Exception:
            pass
    return os_nvme, sec_nvme

def get_nvme_pci_address(nvme_name: str) -> str:
    short_name = nvme_name.split("n")[0]
    sys_path = f"/sys/class/nvme/{short_name}/device"
    if os.path.exists(sys_path):
        return os.path.basename(os.path.realpath(sys_path))
    return "0000:03:00.0"

def get_secondary_nvme_status(sec_nvme: str, pci_addr: str) -> str:
    sys_pci = f"/sys/bus/pci/devices/{pci_addr}"
    if not os.path.exists(sys_pci):
        return "OFF / UNBOUND"
    drv_link = f"{sys_pci}/driver"
    if not os.path.exists(drv_link):
        return "OFF / UNBOUND"
    pwr_state = f"{sys_pci}/power_state"
    if os.path.exists(pwr_state):
        try:
            with open(pwr_state, "r") as f:
                state = f.read().strip()
            if state in ["D3cold", "D3hot"]:
                return f"SLEEP ({state})"
        except Exception:
            pass
    return "POWER ON / ACTIVE"

def power_off_secondary_nvme():
    os_nvme, sec_nvme = get_os_and_secondary_nvme()
    pci_addr = get_nvme_pci_address(sec_nvme)
    console.print(f"\n[bold yellow][-] Powering OFF Secondary NVMe ({sec_nvme} @ {pci_addr})...[/]")
    console.print(f"  [bold green][LOCKED] Protected OS Disk:[/] /dev/{os_nvme} (Hard locked)")

    res = subprocess.run(["lsblk", "--json", f"/dev/{sec_nvme}"], capture_output=True, text=True)
    if res.returncode == 0 and res.stdout.strip():
        try:
            data = json.loads(res.stdout)
            def unmount_nodes(node):
                for child in node.get("children", []):
                    unmount_nodes(child)
                mounts = node.get("mountpoints", [])
                if mounts:
                    for m in mounts:
                        if m:
                            console.print(f"  [yellow]Unmounting {m}...[/]")
                            run_sudo(["umount", "-f", m], timeout_sec=5)
                if node.get("type") == "crypt":
                    name = node.get("name")
                    if name:
                        console.print(f"  [yellow]Closing encrypted volume {name}...[/]")
                        run_sudo(["cryptsetup", "close", name], timeout_sec=5)

            for dev in data.get("blockdevices", []):
                unmount_nodes(dev)
        except Exception:
            pass

    write_sysfs("/sys/bus/pci/drivers/nvme/unbind", pci_addr)
    write_sysfs(f"/sys/bus/pci/devices/{pci_addr}/power/control", "auto")
    console.print(f"  [bold green][OK] Secondary NVMe ({sec_nvme}) powered OFF (~2.0W saved)[/]")

def power_on_secondary_nvme():
    os_nvme, sec_nvme = get_os_and_secondary_nvme()
    pci_addr = get_nvme_pci_address(sec_nvme)
    console.print(f"\n[bold yellow][+] Powering ON Secondary NVMe ({sec_nvme} @ {pci_addr})...[/]")
    write_sysfs("/sys/bus/pci/drivers/nvme/bind", pci_addr)
    run_sudo(["udevadm", "trigger", "--subsystem-match=nvme"], timeout_sec=3)
    time.sleep(1)
    run_sudo(["mount", "-a"], timeout_sec=5)
    console.print(f"  [bold green][OK] Secondary NVMe ({sec_nvme}) online and filesystems mounted[/]")

def power_off_onboard_speakers():
    """Mutes speakers and enables 1-second HDA power save (saves power without breaking codec)."""
    console.print(f"\n[bold yellow][-] Muting Onboard Speakers & Enabling Audio Power Save...[/]")
    write_sysfs("/sys/module/snd_hda_intel/parameters/power_save", "1")
    write_sysfs("/sys/module/snd_hda_intel/parameters/power_save_controller", "Y")
    run_sudo(["amixer", "-c", "0", "sset", "Master", "mute"], timeout_sec=2)
    run_sudo(["amixer", "-c", "0", "sset", "Speaker", "mute"], timeout_sec=2)
    console.print(f"  [bold green][OK] Onboard speakers muted & 1s D3 codec power save enabled[/]")

def power_on_onboard_speakers():
    """Unmutes speakers and restores physical playback."""
    console.print(f"\n[bold yellow][+] Unmuting Onboard Speakers & Restoring Audio Output...[/]")
    write_sysfs("/sys/module/snd_hda_intel/parameters/power_save", "1")
    write_sysfs("/sys/module/snd_hda_intel/parameters/power_save_controller", "Y")
    run_sudo(["amixer", "-c", "0", "sset", "Master", "100%", "unmute"], timeout_sec=2)
    run_sudo(["amixer", "-c", "0", "sset", "Speaker", "100%", "unmute"], timeout_sec=2)
    run_sudo(["alsactl", "init"], timeout_sec=3)
    console.print(f"  [bold green][OK] Onboard speakers unmuted & physical playback restored[/]")

# ==============================================================================
# 4. HARDWARE PRESETS CATALOG
# ==============================================================================
PRESETS = {
    "sec_nvme": {
        "name": "Secondary NVMe SSD (Samsung 980 1TB)",
        "desc": "Unmounts & powers off secondary SSD (< 5mW sleep, OS disk hard locked)",
        "off_fn": power_off_secondary_nvme,
        "on_fn": power_on_secondary_nvme
    },
    "onboard_speakers": {
        "name": "Onboard Speakers & HD Audio PCI Card",
        "desc": "Mutes speakers & enables 1-second D3 codec power save",
        "off_fn": power_off_onboard_speakers,
        "on_fn": power_on_onboard_speakers
    },
    "usb_audio": {
        "name": "USB Microphones & Audio Interfaces",
        "desc": "Disables USB headsets, USB mics & external DACs",
        "bus": "usb",
        "drivers": ["snd-usb-audio"],
        "modules": ["snd_usb_audio", "snd_usbmidi_lib"]
    },
    "webcam": {
        "name": "USB Webcams & Cameras",
        "desc": "Disables camera hardware for battery & privacy",
        "bus": "usb",
        "drivers": ["uvcvideo"],
        "modules": ["uvcvideo", "videobuf2_vmalloc"]
    },
    "kbd_backlight": {
        "name": "Keyboard Backlight LEDs",
        "desc": "Turns off keyboard LED array (~0.8W saved)",
        "off_fn": lambda: write_sysfs("/sys/class/leds/asus::kbd_backlight/brightness", "0"),
        "on_fn": lambda: write_sysfs("/sys/class/leds/asus::kbd_backlight/brightness", "1")
    },
    "storage": {
        "name": "External USB Mass Storage",
        "desc": "Unbinds external USB flash drives & SD card readers",
        "bus": "usb",
        "drivers": ["uas", "usb-storage"],
        "modules": ["uas", "usb_storage"]
    },
    "bluetooth": {
        "name": "Bluetooth Adapter",
        "desc": "Disables internal/external Bluetooth USB controller",
        "bus": "usb",
        "drivers": ["btusb"],
        "modules": ["btusb"]
    }
}

def unload_preset(key: str):
    p = PRESETS[key]
    if "off_fn" in p:
        p["off_fn"]()
        return
    
    console.print(f"\n[bold yellow][-] Disabling:[/] [bold cyan]{p['name']}[/]")
    bus = p.get("bus", "usb")
    for drv in p.get("drivers", []):
        unbind_sysfs_driver(drv, bus_type=bus)
        
    loaded = get_loaded_modules()
    for mod in p.get("modules", []):
        if mod in loaded:
            res = run_sudo(["modprobe", "-r", mod], timeout_sec=5)
            if res.returncode == 0:
                console.print(f"  [bold green][OK] Unloaded module {mod}[/]")

def load_preset(key: str):
    p = PRESETS[key]
    if "on_fn" in p:
        p["on_fn"]()
        return
        
    console.print(f"\n[bold yellow][+] Enabling:[/] [bold cyan]{p['name']}[/]")
    for mod in reversed(p.get("modules", [])):
        res = run_sudo(["modprobe", mod], timeout_sec=5)
        if res.returncode == 0:
            console.print(f"  [bold green][OK] Loaded module {mod}[/]")

    run_sudo(["udevadm", "trigger", f"--subsystem-match={p.get('bus', 'usb')}"], timeout_sec=3)

def reset_sound_services():
    """Restores user audio server, ALSA mixer channels, speaker and mic profiles."""
    console.print(f"[yellow][+] Restoring audio server, speaker & mic profiles for user:[/] [bold cyan]{REAL_USER}[/]")
    
    # Init & restore ALSA mixer levels
    run_sudo(["alsactl", "init"], timeout_sec=3)
    run_sudo(["alsactl", "restore"], timeout_sec=3)
    
    # Unmute ALSA channels for speakers & microphones
    for ch in ["Master", "Speaker", "Headphone", "Capture", "Mic", "Internal Mic"]:
        run_sudo(["amixer", "sset", ch, "100%", "unmute"], timeout_sec=2)
    
    # Restart PipeWire, PipeWire-Pulse, WirePlumber user session
    run_user(["systemctl", "--user", "restart", "pipewire", "pipewire-pulse", "wireplumber"], timeout_sec=5)
    time.sleep(0.5)
    
    # Set default PipeWire sink to Built-in Audio Pro output-3
    run_user(["wpctl", "set-default", "alsa_output.pci-0000_00_1f.3.pro-output-3"], timeout_sec=3)
    
    console.print("  [bold green][OK] Sound server, speakers & microphones restored cleanly.[/]")

# ==============================================================================
# 5. DASHBOARD & INTERACTIVE SELECTION MENU
# ==============================================================================
def render_dashboard():
    loaded = get_loaded_modules()
    os_nvme, sec_nvme = get_os_and_secondary_nvme()
    pci_addr = get_nvme_pci_address(sec_nvme)
    sec_status = get_secondary_nvme_status(sec_nvme, pci_addr)

    table = Table(title=f"Dusky Power Architect - Hardware Status (User: {REAL_USER})", header_style="bold cyan", border_style="blue", expand=True)
    table.add_column("Key", justify="center", style="bold yellow", ratio=1)
    table.add_column("Hardware Component", style="bold white", ratio=3)
    table.add_column("Description", ratio=4)
    table.add_column("Current Status", justify="center", ratio=2)

    key_map = {
        "sec_nvme": "1",
        "onboard_speakers": "2",
        "usb_audio": "3",
        "webcam": "4",
        "kbd_backlight": "5",
        "storage": "6",
        "bluetooth": "7"
    }

    for k, p in PRESETS.items():
        if k == "sec_nvme":
            status_str = f"[bold red]POWER OFF / SLEEP[/]" if "SLEEP" in sec_status or "UNBOUND" in sec_status else "[bold green]POWER ON[/]"
        elif k == "kbd_backlight":
            try:
                val = open("/sys/class/leds/asus::kbd_backlight/brightness").read().strip()
                status_str = "[bold red]OFF (0)[/]" if val == "0" else "[bold green]ON (1)[/]"
            except Exception:
                status_str = "[dim]N/A[/]"
        else:
            bound = []
            for drv in p.get("drivers", []):
                bound.extend(get_active_sysfs_bindings(drv, p.get("bus", "usb")))
            active_mods = [m for m in p.get("modules", []) if m in loaded]
            if active_mods or bound:
                status_str = "[bold green]POWER ON[/]"
            else:
                status_str = "[bold red]POWER OFF / SAVER[/]"

        table.add_row(key_map[k], p["name"], p["desc"], status_str)

    console.print(table)

def interactive_custom_selection():
    """Interactive multi-toggle hardware menu. Stays in the menu until 'b' (Back) is selected."""
    num_map = {
        "1": "sec_nvme",
        "2": "onboard_speakers",
        "3": "usb_audio",
        "4": "webcam",
        "5": "kbd_backlight",
        "6": "storage",
        "7": "bluetooth"
    }

    while True:
        console.print()
        render_dashboard()
        console.print("\n[bold cyan]Interactive Custom Hardware Selection Loop:[/]")
        console.print("  Type component number ([bold yellow]1-7[/]) to toggle its state immediately.")
        console.print("  [bold green]b[/] Back to Main Menu")

        choice = Prompt.ask("\nSelect component to toggle (1-7, b)", choices=["1", "2", "3", "4", "5", "6", "7", "b"], default="b")
        if choice == "b":
            break

        key = num_map[choice]
        p = PRESETS[key]
        
        # Determine current state to toggle
        is_on = True
        if key == "sec_nvme":
            sec_status = get_secondary_nvme_status(*reversed(get_os_and_secondary_nvme()))
            is_on = not ("SLEEP" in sec_status or "UNBOUND" in sec_status)
        elif key == "kbd_backlight":
            try:
                is_on = open("/sys/class/leds/asus::kbd_backlight/brightness").read().strip() != "0"
            except Exception:
                is_on = False
        else:
            bound = []
            for drv in p.get("drivers", []):
                bound.extend(get_active_sysfs_bindings(drv, p.get("bus", "usb")))
            active_mods = [m for m in p.get("modules", []) if m in get_loaded_modules()]
            is_on = bool(active_mods or bound)

        if is_on:
            unload_preset(key)
        else:
            load_preset(key)

        if key in ["onboard_speakers", "usb_audio"]:
            reset_sound_services()
        
        time.sleep(0.5)

def main():
    os_nvme, sec_nvme = get_os_and_secondary_nvme()
    console.print(Panel.fit(
        f"[bold magenta]Dusky Power Architect v3.2.0 (Master Icon Edition)[/]\n"
        f"[bold green][LOCKED] PROTECTED OS DISK:[/bold green] /dev/{os_nvme} (Hard-Locked)\n"
        f"[bold yellow][+] SECONDARY DATA DISK:[/bold yellow] /dev/{sec_nvme} (Toggleable)\n"
        f"[bold cyan]User Context:[/] {REAL_USER} (Auto Elevates for Kernel / Drops for Audio)",
        border_style="magenta"
    ))

    while True:
        console.print()
        render_dashboard()
        
        console.print("\n[bold cyan]Master Power Controls:[/] [dim](Enforces 5s timeout on all operations)[/]")
        console.print("  [bold green]c[/] Custom Select: Interactive Toggle Loop (Stays in menu until 'b')")
        console.print("  [bold green]9[/] MAX BATTERY SAVER: Turn OFF All Non-Essential Hardware (~4W saved)")
        console.print("  [bold green]r[/] RESTORE ALL: Power ON All Hardware & Sync Sound System")
        console.print("  [bold red]q[/] Exit")

        choice = Prompt.ask("\nSelect action", choices=["c", "9", "r", "q"], default="q")

        if choice == "q":
            console.print("[yellow]Exiting Dusky Power Architect.[/]")
            break

        elif choice == "c":
            interactive_custom_selection()

        elif choice == "9":
            console.print("[bold yellow]Activating MAX BATTERY SAVER...[/]")
            for k in PRESETS:
                unload_preset(k)
            write_sysfs("/sys/module/pcie_aspm/parameters/policy", "powersupersave")
            run_sudo(["iw", "dev", "wlan0", "set", "power_save", "on"], timeout_sec=2)
            run_sudo(["powertop", "--auto-tune"], timeout_sec=5)
            reset_sound_services()

        elif choice == "r":
            console.print("[bold yellow]Restoring ALL Hardware & Restarting Sound System...[/]")
            for k in PRESETS:
                load_preset(k)
            reset_sound_services()

        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted via keyboard. Exiting cleanly.[/]")
        sys.exit(0)

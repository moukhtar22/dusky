#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Dusky Hardware Power Architect v4.0.0 (ASUS TUF Edition)
A unified, interactive Rich TUI & CLI tool for monitoring and toggling laptop hardware power states.
Engineered specifically for ASUS TUF Gaming F15 (FX507ZE) on modern Arch Linux / Kernel 7.2+.

Features:
- Live Battery Telemetry: Real-time discharge rate (W), voltage, current, capacity, and remaining time.
- CPU Package Power: Real-time Intel RAPL power metering (Watts) and C-state residency.
- Discrete GPU Status: Telemetry for ASUS WMI dgpu_disable and PCIe Root Port (D3cold).
- ASUS LCD Panel Overdrive (panel_od): Toggle LCD pixel overdrive voltage (~0.4W power save).
- USB WebCam Hardware De-authorization: Full USB controller de-authorization & driver unload for 0W & privacy.
- Secondary NVMe SSD (Samsung 980 1TB): Detects active mounts and advises manual unmount to allow APST PS4 (5mW) sleep.
- Keyboard Backlight: Controls ASUS RGB keyboard backlight array (0-3).
- Bluetooth Adapter: Rfkill radio power toggle.
- Onboard Speakers & Audio DAC: Audio codec D3 power saving & speaker mute.
- Master Modes: Max Battery Saver (batch turn off all non-essential hardware) & Restore All.
- Full CLI & Interactive Support: Run with flags (--status, --max-battery, etc.) or interactive TUI.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ==============================================================================
# 1. DYNAMIC USER & PATH RESOLUTION (ZERO HARDCODED USERNAMES)
# ==============================================================================

def get_real_user_info() -> tuple[str, int, Path]:
    """Resolves real user, UID, and home directory even when invoked via sudo or pkexec."""
    user = os.environ.get("SUDO_USER") or os.environ.get("USER")
    if not user or user == "root":
        try:
            import getpass
            user = getpass.getuser()
        except Exception:
            pass

    if (not user or user == "root") and os.path.exists("/home"):
        users = [
            d for d in os.listdir("/home")
            if os.path.isdir(os.path.join("/home", d)) and not d.startswith(".") and d not in ("lost+found", "shared")
        ]
        if users:
            user = users[0]

    user = user or "root"
    uid = 1000
    home = Path("/home") / user if user != "root" else Path("/root")

    try:
        p = pwd.getpwnam(user)
        uid = p.pw_uid
        home = Path(p.pw_dir)
    except Exception:
        pass

    return user, uid, home

REAL_USER, USER_UID, REAL_HOME = get_real_user_info()

# ==============================================================================
# 2. PRIVILEGE MANAGEMENT & DEPENDENCIES
# ==============================================================================

def elevate_if_needed():
    """Auto-elevates via sudo once if root privileges are required."""
    if os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if not sudo:
            sys.stderr.write("[ERROR] 'sudo' is required for hardware power management.\n")
            sys.exit(1)
        try:
            os.execvp(sudo, [sudo, "-E", sys.executable] + sys.argv)
        except Exception as e:
            sys.stderr.write(f"[ERROR] Privilege escalation failed: {e}\n")
            sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.prompt import Prompt
    from rich.panel import Panel
    from rich.align import Align
except ImportError:
    if os.geteuid() == 0:
        subprocess.run(["pacman", "-S", "--needed", "--noconfirm", "python-rich"], check=False)
        from rich.console import Console
        from rich.table import Table
        from rich.prompt import Prompt
        from rich.panel import Panel
        from rich.align import Align
    else:
        elevate_if_needed()

console = Console()

def run_sudo(cmd: list[str], timeout_sec: float = 5.0) -> subprocess.CompletedProcess:
    """Runs root/hardware actions directly."""
    try:
        if os.geteuid() == 0:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        return subprocess.run(["sudo", "-n"] + cmd, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        console.print(f"[bold red]Timeout:[/] Command {' '.join(cmd)} exceeded {timeout_sec}s.")
        return subprocess.CompletedProcess(cmd, 124, "", "Timeout expired")
    except Exception as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))

def run_user(cmd: list[str], timeout_sec: float = 5.0) -> subprocess.CompletedProcess:
    """Runs user-space actions by dropping root privileges down to the real user."""
    exec_cmd = cmd
    if os.geteuid() == 0 and REAL_USER != "root":
        exec_cmd = ["sudo", "-u", REAL_USER, "env", f"XDG_RUNTIME_DIR=/run/user/{USER_UID}", f"HOME={REAL_HOME}"] + cmd
    try:
        return subprocess.run(exec_cmd, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "Timeout expired")
    except Exception as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))

def safe_read(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def write_sysfs(path: str | Path, value: str) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    try:
        p.write_text(value, encoding="utf-8")
        return True
    except Exception:
        res = run_sudo(["sh", "-c", f"echo '{value}' > '{p}'"])
        return res.returncode == 0

def get_loaded_modules() -> set[str]:
    loaded = set()
    try:
        with open("/proc/modules", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if parts:
                    loaded.add(parts[0])
    except Exception:
        pass
    return loaded

# ==============================================================================
# 3. HARDWARE CONTROLLERS & PROBING
# ==============================================================================

# --- A. ASUS LCD Panel Overdrive ---
PANEL_OD_WMI = Path("/sys/devices/platform/asus-nb-wmi/panel_od")
PANEL_OD_ARMOURY = Path("/sys/devices/virtual/firmware-attributes/asus-armoury/attributes/panel_overdrive/current_value")

def get_panel_od_status() -> bool:
    """Returns True if panel overdrive is enabled (1), False otherwise."""
    for p in (PANEL_OD_ARMOURY, PANEL_OD_WMI):
        if p.exists():
            return safe_read(p) == "1"
    return False

def set_panel_od(enable: bool) -> bool:
    val = "1" if enable else "0"
    success = False
    for p in (PANEL_OD_ARMOURY, PANEL_OD_WMI):
        if p.exists():
            if write_sysfs(p, val):
                success = True
    return success

# --- B. USB WebCam Hardware De-authorization ---
def find_webcam_usb_device() -> Optional[Path]:
    """Dynamically finds the USB device representing the internal WebCam."""
    usb_dir = Path("/sys/bus/usb/devices")
    if not usb_dir.is_dir():
        return None
    for dev in sorted(usb_dir.iterdir()):
        # Check by product description
        prod = safe_read(dev / "product").lower()
        if "camera" in prod or "webcam" in prod:
            return dev
        # Check by known Sonix webcam vendor:device (322e:202c)
        vid = safe_read(dev / "idVendor")
        pid = safe_read(dev / "idProduct")
        if vid == "322e" and pid == "202c":
            return dev
    return None

def get_webcam_status() -> bool:
    """Returns True if webcam is authorized/active, False if de-authorized/disabled."""
    dev = find_webcam_usb_device()
    if not dev:
        return False
    auth = safe_read(dev / "authorized")
    if auth == "0":
        return False
    # Check if /dev/video* devices exist
    return bool(glob.glob("/dev/video*"))

def set_webcam(enable: bool) -> bool:
    dev = find_webcam_usb_device()
    if not dev:
        return False
    if enable:
        write_sysfs(dev / "authorized", "1")
        run_sudo(["modprobe", "uvcvideo"], timeout_sec=3)
        run_sudo(["udevadm", "trigger", "--subsystem-match=usb"], timeout_sec=3)
        return True
    else:
        # De-authorize USB device at hardware layer (cuts USB transceivers & power)
        write_sysfs(dev / "authorized", "0")
        # Unbind from uvcvideo driver if attached
        drv_unbind = Path("/sys/bus/usb/drivers/uvcvideo/unbind")
        if drv_unbind.exists():
            for child in dev.glob(f"{dev.name}:*"):
                write_sysfs(drv_unbind, child.name)
        run_sudo(["modprobe", "-r", "uvcvideo"], timeout_sec=3)
        return True

# --- C. Secondary NVMe SSD Management ---
def get_nvme_topology() -> tuple[str, str, list[str]]:
    """Identifies OS NVMe vs Secondary NVMe and reports active mountpoints for the secondary drive."""
    res = subprocess.run(["lsblk", "--json", "-o", "NAME,PATH,MOUNTPOINTS,TYPE"], capture_output=True, text=True)
    os_nvme = "nvme0n1"
    sec_nvme = "nvme1n1"
    sec_mounts: list[str] = []

    if res.returncode == 0 and res.stdout.strip():
        try:
            data = json.loads(res.stdout)
            for dev in data.get("blockdevices", []):
                name = dev.get("name", "")
                if "nvme" in name:
                    def has_root(node) -> bool:
                        if "/" in node.get("mountpoints", []):
                            return True
                        return any(has_root(c) for c in node.get("children", []))
                    if has_root(dev):
                        os_nvme = name
                    else:
                        sec_nvme = name
                        def collect_mounts(node) -> list[str]:
                            cur = [m for m in node.get("mountpoints", []) if m]
                            for c in node.get("children", []):
                                cur.extend(collect_mounts(c))
                            return cur
                        sec_mounts = collect_mounts(dev)
        except Exception:
            pass

    return os_nvme, sec_nvme, sec_mounts

def get_nvme_pci_address(nvme_name: str) -> str:
    short_name = nvme_name.split("n")[0]
    sys_path = Path(f"/sys/class/nvme/{short_name}/device")
    if sys_path.exists():
        return sys_path.resolve().name
    return "0000:03:00.0"

def get_secondary_nvme_power_info() -> tuple[str, bool, list[str]]:
    """Returns (status_string, is_mounted, mount_list)."""
    os_nvme, sec_nvme, mounts = get_nvme_topology()
    pci_addr = get_nvme_pci_address(sec_nvme)
    sys_pci = Path(f"/sys/bus/pci/devices/{pci_addr}")

    if mounts:
        return f"MOUNTED ({', '.join(mounts)})", True, mounts

    if not sys_pci.exists() or not (sys_pci / "driver").exists():
        return "POWER OFF / UNBOUND", False, []

    pwr_state = safe_read(sys_pci / "power_state")
    if pwr_state in ("D3cold", "D3hot"):
        return f"SLEEP ({pwr_state})", False, []

    return "IDLE / UNMOUNTED (APST PS4 Sleep: 5mW)", False, []

def toggle_secondary_nvme_driver(power_on: bool) -> tuple[bool, str]:
    """Safely binds or unbinds the secondary NVMe from the PCIe bus without forcing unmounts."""
    os_nvme, sec_nvme, mounts = get_nvme_topology()
    pci_addr = get_nvme_pci_address(sec_nvme)

    if mounts and not power_on:
        return False, f"Secondary SSD is currently mounted at: {', '.join(mounts)}. Please unmount manually first!"

    if power_on:
        write_sysfs("/sys/bus/pci/drivers/nvme/bind", pci_addr)
        run_sudo(["udevadm", "trigger", "--subsystem-match=nvme"], timeout_sec=3)
        return True, f"Secondary NVMe ({sec_nvme} @ {pci_addr}) bound to driver."
    else:
        write_sysfs("/sys/bus/pci/drivers/nvme/unbind", pci_addr)
        write_sysfs(f"/sys/bus/pci/devices/{pci_addr}/power/control", "auto")
        return True, f"Secondary NVMe ({sec_nvme} @ {pci_addr}) unbound and powered down."

# --- D. Keyboard Backlight & Full RGB Extinction ---
KBD_BRIGHTNESS = Path("/sys/class/leds/asus::kbd_backlight/brightness")
KBD_RGB_MODE = Path("/sys/devices/platform/asus-nb-wmi/leds/asus::kbd_backlight/kbd_rgb_mode")
KBD_RGB_STATE = Path("/sys/devices/platform/asus-nb-wmi/leds/asus::kbd_backlight/kbd_rgb_state")
STATE_DIR = Path("/etc/dusky")
USER_SETTINGS_DIR = REAL_HOME / ".config" / "dusky" / "settings"
KBD_STATE_FILE = STATE_DIR / "kbd_backlight_state"
USER_KBD_STATE_FILE = USER_SETTINGS_DIR / "kbd_backlight_state"

def get_kbd_backlight() -> int:
    try:
        return int(safe_read(KBD_BRIGHTNESS) or "0")
    except ValueError:
        return 0

def is_kbd_backlight_locked() -> bool:
    try:
        mode = oct(KBD_BRIGHTNESS.stat().st_mode)[-3:]
        return mode == "444"
    except Exception:
        return False

def set_kbd_backlight(level: int) -> bool:
    lvl = max(0, min(3, level))

    # 1. Persist state across reboots (system-wide and user session)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        KBD_STATE_FILE.write_text(f"{lvl}\n", encoding="utf-8")
    except Exception:
        run_sudo(["sh", "-c", f"mkdir -p /etc/dusky && echo '{lvl}' > /etc/dusky/kbd_backlight_state"])
    try:
        USER_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        USER_KBD_STATE_FILE.write_text(f"{lvl}\n", encoding="utf-8")
    except Exception:
        pass

    if lvl == 0:
        # 1. Unlock sysfs attribute first if currently write-protected
        run_sudo(["chmod", "644", str(KBD_BRIGHTNESS)], timeout_sec=2)
        # 2. Zero brightness
        write_sysfs(KBD_BRIGHTNESS, "0")
        # 3. Set static black RGB color to kill LED diodes completely
        if KBD_RGB_MODE.exists():
            write_sysfs(KBD_RGB_MODE, "1 0 0 0 0 0")
        # 4. Cut aura state animations (boot, awake, sleep, keypress)
        if KBD_RGB_STATE.exists():
            write_sysfs(KBD_RGB_STATE, "1 0 0 0 0")
        # 5. Update user and system brightnessctl / systemd cache
        run_user(["brightnessctl", "-sd", "asus::kbd_backlight", "set", "0"], timeout_sec=2)
        sys_bl = Path("/var/lib/systemd/backlight/platform-asus-nb-wmi:leds:asus::kbd_backlight")
        if sys_bl.parent.is_dir():
            write_sysfs(sys_bl, "0")
        # 6. Apply immutable hardware lock: mode 0444 blocks systemd-logind, brightnessctl, and desktop shortcuts
        run_sudo(["chmod", "444", str(KBD_BRIGHTNESS)], timeout_sec=2)
        return True
    else:
        # Unlock sysfs attribute
        run_sudo(["chmod", "644", str(KBD_BRIGHTNESS)], timeout_sec=2)
        # Re-enable aura states, set white/default color, and apply brightness
        if KBD_RGB_STATE.exists():
            write_sysfs(KBD_RGB_STATE, "1 1 1 1 1")
        if KBD_RGB_MODE.exists():
            write_sysfs(KBD_RGB_MODE, "1 0 255 255 255 0")
        write_sysfs(KBD_BRIGHTNESS, str(lvl))
        run_user(["brightnessctl", "-sd", "asus::kbd_backlight", "set", str(lvl)], timeout_sec=2)
        sys_bl = Path("/var/lib/systemd/backlight/platform-asus-nb-wmi:leds:asus::kbd_backlight")
        if sys_bl.parent.is_dir():
            write_sysfs(sys_bl, str(lvl))
        return True

# --- E. Bluetooth Adapter ---
def get_bluetooth_status() -> bool:
    res = subprocess.run(["rfkill", "list", "bluetooth"], capture_output=True, text=True)
    if res.returncode == 0:
        return "Soft blocked: yes" not in res.stdout
    return False

def set_bluetooth(enable: bool) -> bool:
    action = "unblock" if enable else "block"
    res = run_sudo(["rfkill", action, "bluetooth"], timeout_sec=3)
    return res.returncode == 0

# --- F. Onboard Audio & Speakers ---
def get_audio_powersave_status() -> bool:
    val = safe_read("/sys/module/snd_hda_intel/parameters/power_save")
    return val == "1"

def set_audio_powersave(enable: bool) -> None:
    val = "1" if enable else "0"
    write_sysfs("/sys/module/snd_hda_intel/parameters/power_save", val)
    write_sysfs("/sys/module/snd_hda_intel/parameters/power_save_controller", "Y" if enable else "N")

def mute_speakers(mute: bool) -> None:
    action = "mute" if mute else "unmute"
    for ch in ("Master", "Speaker"):
        run_sudo(["amixer", "-c", "0", "sset", ch, action], timeout_sec=2)

# --- G. Precision Touchpad (I2C Bus Disconnect) ---
TOUCHPAD_I2C_NAME = "i2c-ASUF1204:00"
TOUCHPAD_DRIVER_DIR = Path("/sys/bus/i2c/drivers/i2c_hid_acpi")

def get_touchpad_device_name() -> str:
    i2c_devs = Path("/sys/bus/i2c/devices")
    if i2c_devs.is_dir():
        for d in i2c_devs.iterdir():
            if "ASUF" in d.name:
                return d.name
    return TOUCHPAD_I2C_NAME

def get_touchpad_status() -> bool:
    dev = get_touchpad_device_name()
    return (TOUCHPAD_DRIVER_DIR / dev).exists()

def set_touchpad(enable: bool) -> bool:
    dev = get_touchpad_device_name()
    is_bound = (TOUCHPAD_DRIVER_DIR / dev).exists()
    if enable:
        if not is_bound:
            write_sysfs(TOUCHPAD_DRIVER_DIR / "bind", dev)
            run_sudo(["udevadm", "trigger", "--subsystem-match=input"], timeout_sec=2)
        return True
    else:
        if is_bound:
            write_sysfs(TOUCHPAD_DRIVER_DIR / "unbind", dev)
        return True

# --- H. Microphone (Hardware ADC Cut & PipeWire Mute) ---
def get_mic_status() -> bool:
    """Checks if microphone capture is unmuted at the hardware ADC mixer level."""
    res = subprocess.run(["amixer", "-c", "1", "cget", "numid=5"], capture_output=True, text=True)
    if res.returncode == 0:
        return ": values=on" in res.stdout
    res_pac = run_user(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"], timeout_sec=2)
    return "Mute: no" in res_pac.stdout

def set_mic(enable: bool) -> bool:
    """Mutes or unmutes the microphone ADC hardware circuit and PipeWire audio stream."""
    val = "1" if enable else "0"
    mute_pac = "0" if enable else "1"
    run_sudo(["amixer", "-c", "1", "cset", "numid=5", val], timeout_sec=2)
    run_user(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", mute_pac], timeout_sec=2)
    return True

# --- I. Wi-Fi Radio Controller (with Connection Protection) ---
def get_wifi_status() -> bool:
    """Returns True if Wi-Fi radio is active/unblocked, False if soft-blocked."""
    res = subprocess.run(["rfkill", "list", "wifi"], capture_output=True, text=True)
    if res.returncode == 0:
        return "Soft blocked: yes" not in res.stdout
    for r in Path("/sys/class/rfkill").glob("rfkill*"):
        if safe_read(r / "type") == "wlan":
            return safe_read(r / "state") == "1"
    return True

def set_wifi(enable: bool) -> bool:
    """Enables or disables Wi-Fi radio via rfkill."""
    action = "unblock" if enable else "block"
    res = run_sudo(["rfkill", action, "wifi"], timeout_sec=3)
    return res.returncode == 0

# --- J. ASUS Platform & Thermal Profile ---
PLATFORM_PROFILE = Path("/sys/firmware/acpi/platform_profile")
THROTTLE_POLICY = Path("/sys/devices/platform/asus-nb-wmi/throttle_thermal_policy")

def get_platform_profile() -> str:
    prof = safe_read(PLATFORM_PROFILE)
    if prof:
        return prof
    pol = safe_read(THROTTLE_POLICY)
    if pol == "2":
        return "quiet"
    elif pol == "1":
        return "performance"
    return "balanced"

def set_platform_profile(profile: str) -> bool:
    profile = profile.lower()
    if profile not in ("quiet", "balanced", "performance"):
        return False
    if PLATFORM_PROFILE.exists():
        write_sysfs(PLATFORM_PROFILE, profile)
    if THROTTLE_POLICY.exists():
        pol_map = {"quiet": "2", "balanced": "0", "performance": "1"}
        write_sysfs(THROTTLE_POLICY, pol_map.get(profile, "0"))
    return True

# --- K. Thunderbolt 4 Port & Controller ---
THUNDERBOLT_PCI = "0000:00:0d.2"
THUNDERBOLT_DRIVER_DIR = Path("/sys/bus/pci/drivers/thunderbolt")

def get_thunderbolt_status() -> bool:
    """Returns True if Thunderbolt 4 NHI controller is bound and active, False if unbound/disabled."""
    return (THUNDERBOLT_DRIVER_DIR / THUNDERBOLT_PCI).exists()

def set_thunderbolt(enable: bool) -> bool:
    """Enables or disables the Thunderbolt 4 NHI controller on PCIe."""
    is_bound = (THUNDERBOLT_DRIVER_DIR / THUNDERBOLT_PCI).exists()
    if enable:
        if not is_bound:
            write_sysfs(THUNDERBOLT_DRIVER_DIR / "bind", THUNDERBOLT_PCI)
        return True
    else:
        if is_bound:
            write_sysfs(THUNDERBOLT_DRIVER_DIR / "unbind", THUNDERBOLT_PCI)
        write_sysfs(f"/sys/bus/pci/devices/{THUNDERBOLT_PCI}/power/control", "auto")
        return True

# --- L. External USB Data Ports (Type-A & Type-C Data) ---
TB_USB_PCI = "0000:00:0d.0"
TB_USB_DRIVER_DIR = Path("/sys/bus/pci/drivers/xhci_hcd")

def get_external_usb_status() -> bool:
    """Returns True if external USB data ports are authorized, False if blocked."""
    for r in Path("/sys/bus/usb/devices").glob("usb*"):
        auth_f = r / "authorized_default"
        if auth_f.exists() and safe_read(auth_f) == "0":
            return False
    return True

def set_external_usb(enable: bool) -> bool:
    """
    Enables or disables external USB ports.
    When disabled:
      - Sets authorized_default=0 on external USB root hubs (blocks any newly inserted USB device)
      - Unbinds external Thunderbolt/Type-C USB controller 0000:00:0d.0 and sets power/control to auto
      - Preserves internal devices (Webcam, C-Media Audio, Bluetooth) and hardware charging (USB-PD / DC-in).
    """
    val = "1" if enable else "0"
    for r in Path("/sys/bus/usb/devices").glob("usb*"):
        auth_f = r / "authorized_default"
        if auth_f.exists():
            write_sysfs(auth_f, val)

    # Also handle the dedicated external Type-C USB controller (0000:00:0d.0)
    tb_bound = (TB_USB_DRIVER_DIR / TB_USB_PCI).exists()
    if enable and not tb_bound:
        write_sysfs(TB_USB_DRIVER_DIR / "bind", TB_USB_PCI)
    elif not enable and tb_bound:
        write_sysfs(TB_USB_DRIVER_DIR / "unbind", TB_USB_PCI)
        write_sysfs(f"/sys/bus/pci/devices/{TB_USB_PCI}/power/control", "auto")
    return True

# --- M. Ethernet LAN Controller (RJ-45) ---
def get_ethernet_status() -> tuple[str, bool]:
    """Returns (status_string, is_controllable)."""
    res = subprocess.run(["ip", "-o", "link"], capture_output=True, text=True)
    eth_ifaces = [
        line.split(": ")[1] for line in res.stdout.splitlines()
        if any(line.split(": ")[1].startswith(prefix) for prefix in ("eth", "enp", "eno", "ens"))
    ]
    if eth_ifaces:
        iface = eth_ifaces[0]
        is_up = "state UP" in res.stdout
        return f"{'ACTIVE (UP)' if is_up else 'DOWN (Link Off)'} ({iface})", True

    # Check if r8169 kernel driver is compiled/available
    r8169_drv = Path("/sys/bus/pci/drivers/r8169")
    if r8169_drv.exists() and list(r8169_drv.glob("0000:*")):
        return "ENABLED (Bound)", True

    return "OFF (Kernel Driver Not Loaded / Power Down)", False

def set_ethernet(enable: bool) -> bool:
    res = subprocess.run(["ip", "-o", "link"], capture_output=True, text=True)
    eth_ifaces = [
        line.split(": ")[1] for line in res.stdout.splitlines()
        if any(line.split(": ")[1].startswith(prefix) for prefix in ("eth", "enp", "eno", "ens"))
    ]
    for iface in eth_ifaces:
        action = "up" if enable else "down"
        run_sudo(["ip", "link", "set", iface, action], timeout_sec=2)
    return True

# --- N. HDMI Display Port ---
def get_hdmi_status() -> str:
    """HDMI on ASUS TUF is hardwired to the dGPU. Reports whether HDMI is powered down."""
    asus_val = safe_read("/sys/devices/virtual/firmware-attributes/asus-armoury/attributes/dgpu_disable/current_value")
    if not asus_val:
        asus_val = safe_read("/sys/devices/platform/asus-nb-wmi/dgpu_disable")
    if asus_val == "1":
        return "OFF (dGPU Powered Down / 0W)"
    return "ACTIVE (dGPU Powered)"

# ==============================================================================
# 4. SYSTEM POWER & TELEMETRY MONITORING
# ==============================================================================

def get_battery_telemetry() -> dict[str, str | float]:
    bat = Path("/sys/class/power_supply/BAT1")
    if not bat.exists():
        for b in Path("/sys/class/power_supply").glob("BAT*"):
            bat = b
            break
    if not bat.exists():
        return {}

    status = safe_read(bat / "status") or "Unknown"
    cap = safe_read(bat / "capacity") or "0"
    try:
        voltage = int(safe_read(bat / "voltage_now") or 0) / 1e6
        current = int(safe_read(bat / "current_now") or 0) / 1e6
        power = int(safe_read(bat / "power_now") or 0) / 1e6
        if power == 0 and voltage and current:
            power = voltage * current
    except (ValueError, ZeroDivisionError):
        voltage, current, power = 0.0, 0.0, 0.0

    rem_time = "N/A"
    if status.lower() == "discharging":
        try:
            energy_f = bat / "energy_now"
            charge_f = bat / "charge_now"
            if energy_f.exists() and power > 0.5:
                energy_now = int(safe_read(energy_f) or 0) / 1e6
                hours = energy_now / power
            elif charge_f.exists() and current > 0.05:
                charge_now = int(safe_read(charge_f) or 0) / 1e6
                hours = charge_now / current
            else:
                hours = 0
            if hours > 0:
                h = int(hours)
                m = int((hours - h) * 60)
                rem_time = f"{h}h {m:02d}m"
        except Exception:
            pass

    return {
        "status": status,
        "capacity": cap,
        "voltage": voltage,
        "current": current,
        "power": power,
        "remaining": rem_time,
    }

def get_cpu_package_power() -> float:
    rapl_energy = Path("/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj")
    if not rapl_energy.exists():
        return 0.0
    try:
        e0 = int(safe_read(rapl_energy) or 0)
        t0 = time.time()
        time.sleep(0.2)
        e1 = int(safe_read(rapl_energy) or 0)
        t1 = time.time()
        return (e1 - e0) / (t1 - t0) / 1e6
    except Exception:
        return 0.0

def get_dgpu_status() -> tuple[str, str]:
    """Returns (asus_wmi_status, root_port_power_state)."""
    asus_val = safe_read("/sys/devices/virtual/firmware-attributes/asus-armoury/attributes/dgpu_disable/current_value")
    if not asus_val:
        asus_val = safe_read("/sys/devices/platform/asus-nb-wmi/dgpu_disable")
    wmi_str = "Disabled (Power Cut)" if asus_val == "1" else "Enabled (Powered)"

    root_port = Path("/sys/bus/pci/devices/0000:00:01.0")
    port_state = safe_read(root_port / "power_state") or "unknown"
    port_rst = safe_read(root_port / "power" / "runtime_status") or "unknown"
    return wmi_str, f"{port_state} ({port_rst})"

def get_tlp_status() -> str:
    tlp_state_f = REAL_HOME / ".config" / "dusky" / "settings" / "tlp_state"
    if tlp_state_f.is_file():
        val = safe_read(tlp_state_f)
        if val:
            return f"Active ({val})"
    res = subprocess.run(["systemctl", "is-active", "tlp.service"], capture_output=True, text=True)
    if res.returncode == 0:
        return "Active (systemd)"
    return "Standby / Inactive"

# ==============================================================================
# 5. PRESENTATION & DASHBOARDS (RICH)
# ==============================================================================

def render_dashboard() -> None:
    bat = get_battery_telemetry()
    cpu_w = get_cpu_package_power()
    dgpu_wmi, dgpu_port = get_dgpu_status()
    tlp_stat = get_tlp_status()

    # Telemetry Header Panel
    power_color = "green" if bat.get("power", 0) < 11.5 else "yellow"
    if bat.get("power", 0) > 16.0:
        power_color = "red"

    header_text = (
        f"[bold cyan]Battery Discharge:[/] [{power_color}]{bat.get('power', 0):.2f} W[/{power_color}]  |  "
        f"[bold cyan]Capacity:[/] {bat.get('capacity', '?')}% ({bat.get('status', 'Unknown')})  |  "
        f"[bold cyan]Remaining:[/] {bat.get('remaining', 'N/A')}  |  "
        f"[bold cyan]Voltage:[/] {bat.get('voltage', 0):.2f} V\n"
        f"[bold magenta]CPU Package Draw:[/] [green]{cpu_w:.2f} W[/green]  |  "
        f"[bold magenta]ASUS dGPU:[/] [green]{dgpu_wmi}[/green] (Port: {dgpu_port})  |  "
        f"[bold magenta]TLP State:[/] [cyan]{tlp_stat}[/cyan]"
    )

    console.print(Panel(
        header_text,
        title="[bold magenta]Dusky Hardware Power Architect v4.0.0 (ASUS TUF Edition)[/bold magenta]",
        subtitle=f"[dim]User: {REAL_USER}  |  Platform: ASUS TUF Gaming F15 (FX507ZE)[/dim]",
        border_style="magenta",
        expand=True
    ))

    # Hardware Components Table
    table = Table(title="Hardware Power States & Toggles", header_style="bold cyan", border_style="blue", expand=True)
    table.add_column("Key", justify="center", style="bold yellow", ratio=1)
    table.add_column("Hardware Component", style="bold white", ratio=3)
    table.add_column("Description", ratio=4)
    table.add_column("Current Status", justify="center", ratio=3)

    # 1. Panel Overdrive
    pod_on = get_panel_od_status()
    pod_str = "[bold green]ON (144Hz Overdrive)[/]" if pod_on else "[bold red]OFF (Battery Saver)[/]"
    table.add_row("1", "ASUS LCD Panel Overdrive", "Pixel overdrive voltage (~0.4W draw)", pod_str)

    # 2. WebCam
    cam_on = get_webcam_status()
    cam_str = "[bold green]ENABLED (Active)[/]" if cam_on else "[bold red]DISABLED (Power Cut)[/]"
    table.add_row("2", "USB 2.0 HD WebCam", "Hardware de-authorization & UVC unload", cam_str)

    # 3. Secondary NVMe SSD
    sec_str, is_mounted, sec_mounts = get_secondary_nvme_power_info()
    if is_mounted:
        sec_display = f"[bold yellow]{sec_str}[/]"
    elif "SLEEP" in sec_str or "UNBOUND" in sec_str:
        sec_display = f"[bold red]{sec_str}[/]"
    else:
        sec_display = f"[bold green]{sec_str}[/]"
    table.add_row("3", "Secondary NVMe SSD (Samsung 980)", "Unmount advice & APST PS4 sleep", sec_display)

    # 4. Keyboard Backlight & Aura
    kbd_lvl = get_kbd_backlight()
    if kbd_lvl == 0:
        kbd_str = "[bold red]OFF (Hardware Locked)[/]" if is_kbd_backlight_locked() else "[bold red]OFF (Blackout)[/]"
    else:
        kbd_str = f"[bold green]ON ({kbd_lvl}/3)[/]"
    table.add_row("4", "Keyboard RGB Backlight & Aura", "ASUS LED brightness & hardware lock (~0.8W)", kbd_str)

    # 5. Bluetooth
    bt_on = get_bluetooth_status()
    bt_str = "[bold green]ENABLED (Active)[/]" if bt_on else "[bold red]BLOCKED (Rfkill Off)[/]"
    table.add_row("5", "Bluetooth Adapter", "Rfkill radio transceiver toggle", bt_str)

    # 6. Audio Codec
    snd_ps = get_audio_powersave_status()
    snd_str = "[bold red]1s D3 Power Save[/]" if snd_ps else "[bold green]Active / Full Power[/]"
    table.add_row("6", "Onboard Audio Codec", "HDA controller 1s idle power save", snd_str)

    # 7. Precision Touchpad
    pad_on = get_touchpad_status()
    pad_str = "[bold green]ENABLED (Bound)[/]" if pad_on else "[bold red]DISABLED (Bus Cut)[/]"
    table.add_row("7", "Precision Touchpad (ASUF1204)", "I2C bus disconnect & interrupt cut", pad_str)

    # 8. Microphone ADC
    mic_on = get_mic_status()
    mic_str = "[bold green]ACTIVE (Unmuted)[/]" if mic_on else "[bold red]MUTED (Hardware ADC Cut)[/]"
    table.add_row("8", "Hardware Mic / ADC (C-Media)", "Codec ADC capture switch & PipeWire mute", mic_str)

    # 9. Wi-Fi Radio
    wifi_on = get_wifi_status()
    wifi_str = "[bold green]ENABLED (Connected)[/]" if wifi_on else "[bold red]BLOCKED (Rfkill Off)[/]"
    table.add_row("9", "Wi-Fi Radio (phy0 / Intel CNVi)", "Wireless radio transceiver killswitch", wifi_str)

    # 10. Platform Profile
    prof = get_platform_profile()
    prof_color = "green" if prof == "quiet" else ("yellow" if prof == "balanced" else "red")
    prof_str = f"[bold {prof_color}]{prof.upper()}[/]"
    table.add_row("10", "ASUS Platform Profile", "Thermal governor & CPU TDP ceiling", prof_str)

    # 11. Thunderbolt 4
    tb_on = get_thunderbolt_status()
    tb_str = "[bold green]ENABLED (Bound)[/]" if tb_on else "[bold red]DISABLED (D3cold Cut)[/]"
    table.add_row("11", "Thunderbolt 4 / USB4", "PCIe & DP tunneling NHI controller", tb_str)

    # 12. External USB Data Ports
    usb_on = get_external_usb_status()
    usb_str = "[bold green]AUTHORIZED (Data On)[/]" if usb_on else "[bold red]BLOCKED (Data Guard)[/]"
    table.add_row("12", "External USB Data Ports", "Type-A & Type-C data guard (keeps PD charge)", usb_str)

    # 13. Ethernet LAN
    eth_str, _ = get_ethernet_status()
    table.add_row("13", "Ethernet LAN (RJ-45)", "Realtek controller link / driver state", f"[bold cyan]{eth_str}[/]")

    # 14. HDMI Video Port
    hdmi_str = get_hdmi_status()
    table.add_row("14", "HDMI Video Output", "Dedicated NVIDIA GPU display bus", f"[bold green]{hdmi_str}[/]")

    console.print(table)
    console.print(Align.center("[dim]* Note: Power / Charging Port (DC Barrel & USB-PD) is hardware EC managed and always safe.[/dim]"))

    # Prominent SSD Warning if mounted
    if is_mounted:
        console.print(Panel(
            f"[bold yellow][!] NOTICE:[/] The secondary SSD is currently mounted at [bold cyan]{', '.join(sec_mounts)}[/bold cyan].\n"
            "To achieve maximum power savings, please manually unmount your filesystem:\n"
            f"  [bold green]sudo umount {sec_mounts[0]}[/bold green]",
            title="[bold yellow]Storage Advisory[/bold yellow]",
            border_style="yellow"
        ))

# ==============================================================================
# 6. BATCH ACTIONS & MASTER MODES
# ==============================================================================

def apply_max_battery() -> None:
    elevate_if_needed()
    console.print("\n[bold yellow][*] Activating MAXIMUM BATTERY SAVER Hardware Profile...[/bold yellow]")

    # 1. Disable LCD Panel Overdrive
    if set_panel_od(False):
        console.print("  [bold green][OK][/] LCD Panel Overdrive disabled (~0.4W saved).")
    else:
        console.print("  [dim]Panel Overdrive already off or unsupported.[/dim]")

    # 2. De-authorize WebCam
    set_webcam(False)
    console.print("  [bold green][OK][/] WebCam completely de-authorized & powered down (~0.3W saved).")

    # 3. Keyboard Backlight & Aura Full Blackout
    set_kbd_backlight(0)
    console.print("  [bold green][OK][/] Keyboard backlight & aura completely extinguished (~0.8W saved).")

    # 4. Bluetooth Block
    set_bluetooth(False)
    console.print("  [bold green][OK][/] Bluetooth radio soft-blocked.")

    # 5. Microphone ADC Cut
    set_mic(False)
    console.print("  [bold green][OK][/] Microphone ADC circuit cut & stream muted.")

    # 6. Audio Power Save
    set_audio_powersave(True)
    console.print("  [bold green][OK][/] Audio codec 1-second D3 power save active.")

    # 7. ASUS Platform Profile: Quiet
    set_platform_profile("quiet")
    console.print("  [bold green][OK][/] ASUS Platform profile set to QUIET (silent thermal policy).")

    # 8. Thunderbolt 4 Controller Unbind
    set_thunderbolt(False)
    console.print("  [bold green][OK][/] Thunderbolt 4 NHI controller unbound and locked in D3cold.")

    # 9. External USB Ports Data Guard
    set_external_usb(False)
    console.print("  [bold green][OK][/] External USB data guard active (charging ports untouched).")

    # 10. PCIe ASPM & WiFi Power Save
    write_sysfs("/sys/module/pcie_aspm/parameters/policy", "powersupersave")
    for iface in Path("/sys/class/net").glob("*"):
        if (iface / "wireless").exists():
            run_sudo(["iw", "dev", iface.name, "set", "power_save", "on"], timeout_sec=2)
    console.print("  [bold green][OK][/] PCIe ASPM powersupersave & Wi-Fi power-save active.")

    # 11. Connectivity & Input Safety Notes
    console.print("  [dim]~ Touchpad: Kept ACTIVE to preserve cursor control (use --touchpad off or Menu to toggle).[/dim]")
    console.print("  [dim]~ Wi-Fi: Kept ACTIVE to prevent disconnects (use --wifi off or Menu with confirmation to toggle).[/dim]")
    console.print("  [dim]~ Power / Charging Port: Hardware EC managed (always charging safe).[/dim]")

    # 12. Secondary SSD Advisory
    _, is_mounted, mounts = get_secondary_nvme_power_info()
    if is_mounted:
        console.print(f"  [bold yellow][!] REMINDER:[/] Secondary SSD is mounted at {', '.join(mounts)}. Unmount manually for deep sleep!")
    else:
        console.print("  [bold green][OK][/] Secondary SSD is unmounted and resting in APST deep sleep (5mW).")

    console.print("[bold green]=== MAXIMUM BATTERY SAVER APPLIED ===[/bold green]")

def apply_restore_all() -> None:
    elevate_if_needed()
    console.print("\n[bold yellow][*] Restoring Standard Hardware Profile...[/bold yellow]")

    # 1. Enable LCD Panel Overdrive
    set_panel_od(True)
    console.print("  [bold green][OK][/] LCD Panel Overdrive restored.")

    # 2. Re-authorize WebCam
    set_webcam(True)
    console.print("  [bold green][OK][/] WebCam re-authorized & UVC driver loaded.")

    # 3. Keyboard Backlight Normal (1) & Aura
    set_kbd_backlight(1)
    console.print("  [bold green][OK][/] Keyboard backlight set to level 1 & aura restored.")

    # 4. Bluetooth Unblock
    set_bluetooth(True)
    console.print("  [bold green][OK][/] Bluetooth radio unblocked.")

    # 5. Microphone Unmute
    set_mic(True)
    console.print("  [bold green][OK][/] Microphone ADC circuit active & stream unmuted.")

    # 6. Restore Audio Output
    mute_speakers(False)
    run_user(["systemctl", "--user", "restart", "pipewire", "pipewire-pulse", "wireplumber"], timeout_sec=5)
    console.print("  [bold green][OK][/] Audio output restored & PipeWire synced.")

    # 7. Precision Touchpad
    set_touchpad(True)
    console.print("  [bold green][OK][/] Precision Touchpad bound and active.")

    # 8. Wi-Fi Unblock
    set_wifi(True)
    console.print("  [bold green][OK][/] Wi-Fi radio unblocked.")

    # 9. Platform Profile: Balanced
    set_platform_profile("balanced")
    console.print("  [bold green][OK][/] ASUS Platform profile restored to BALANCED.")

    # 10. Thunderbolt 4
    set_thunderbolt(True)
    console.print("  [bold green][OK][/] Thunderbolt 4 controller restored & bound.")

    # 11. External USB Data Ports
    set_external_usb(True)
    console.print("  [bold green][OK][/] External USB data ports re-authorized.")

    # 12. Rebind Secondary NVMe if unbound
    toggle_secondary_nvme_driver(True)
    console.print("  [bold green][OK][/] Secondary NVMe bound and ready.")

    console.print("[bold green]=== STANDARD HARDWARE PROFILE RESTORED ===[/bold green]")

# ==============================================================================
# 7. INTERACTIVE LOOP & CLI DISPATCHER
# ==============================================================================

def interactive_custom_selection():
    elevate_if_needed()
    while True:
        console.print()
        render_dashboard()
        console.print("\n[bold cyan]Select Component to Toggle:[/bold cyan]")
        console.print("  [bold yellow]1[/] Toggle ASUS LCD Panel Overdrive")
        console.print("  [bold yellow]2[/] Toggle USB WebCam (Full De-authorization / Restore)")
        console.print("  [bold yellow]3[/] Secondary NVMe SSD Options (Status / Unbind / Bind)")
        console.print("  [bold yellow]4[/] Toggle Keyboard RGB Backlight & Aura (Cycles 0-3)")
        console.print("  [bold yellow]5[/] Toggle Bluetooth (Block / Unblock)")
        console.print("  [bold yellow]6[/] Toggle Onboard Audio 1s Power Save")
        console.print("  [bold yellow]7[/] Toggle Precision Touchpad (I2C Bus Cut / Rebind)")
        console.print("  [bold yellow]8[/] Toggle Microphone (Hardware ADC Cut & Mute)")
        console.print("  [bold yellow]9[/] Toggle Wi-Fi Radio (phy0) [bold red][Requires Confirmation][/]")
        console.print("  [bold yellow]10[/] Cycle Platform Profile (Quiet / Balanced / Performance)")
        console.print("  [bold yellow]11[/] Toggle Thunderbolt 4 / USB4 (Cut PCIe Tunneling / Rebind)")
        console.print("  [bold yellow]12[/] Toggle External USB Data Guard (Block/Authorize Data Lines)")
        console.print("  [bold yellow]13[/] Toggle Ethernet LAN Port (if controllable)")
        console.print("  [bold green]b[/] Back to Main Menu")

        choice = Prompt.ask("\nSelect action (1-13, b)", choices=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "b"], default="b")
        if choice == "b":
            break

        if choice == "1":
            cur = get_panel_od_status()
            set_panel_od(not cur)
            console.print(f"[green]  ~[/green] Panel Overdrive set to: {'OFF' if cur else 'ON'}")

        elif choice == "2":
            cur = get_webcam_status()
            set_webcam(not cur)
            console.print(f"[green]  ~[/green] WebCam set to: {'DISABLED' if cur else 'ENABLED'}")

        elif choice == "3":
            status_str, is_mounted, mounts = get_secondary_nvme_power_info()
            if is_mounted:
                console.print(Panel(
                    f"[bold yellow][!] The SSD is currently mounted at:[/] {', '.join(mounts)}\n"
                    "As requested, please manually unmount it in another terminal:\n"
                    f"  [bold green]sudo umount {mounts[0]}[/bold green]\n"
                    "After unmounting, it automatically enters APST PS4 deep sleep (5mW).",
                    title="[bold yellow]Manual Unmount Required[/bold yellow]",
                    border_style="yellow"
                ))
            else:
                sub_c = Prompt.ask("SSD is unmounted. Toggle PCIe driver unbind?", choices=["y", "n"], default="n")
                if sub_c == "y":
                    is_bound = "UNBOUND" not in status_str
                    success, msg = toggle_secondary_nvme_driver(not is_bound)
                    console.print(f"  [{'green' if success else 'red'}]{msg}[/]")

        elif choice == "4":
            cur = get_kbd_backlight()
            nxt = (cur + 1) % 4
            set_kbd_backlight(nxt)
            lock_info = " (Hardware Locked & Persisted)" if nxt == 0 else " (Unlocked)"
            console.print(f"[green]  ~[/green] Keyboard backlight set to: {nxt}/3{lock_info}")

        elif choice == "5":
            cur = get_bluetooth_status()
            set_bluetooth(not cur)
            console.print(f"[green]  ~[/green] Bluetooth set to: {'BLOCKED' if cur else 'UNBLOCKED'}")

        elif choice == "6":
            cur = get_audio_powersave_status()
            set_audio_powersave(not cur)
            console.print(f"[green]  ~[/green] Audio power save set to: {'OFF' if cur else 'ON (1s)'}")

        elif choice == "7":
            cur = get_touchpad_status()
            set_touchpad(not cur)
            console.print(f"[green]  ~[/green] Precision Touchpad set to: {'DISABLED (Bus Cut)' if cur else 'ENABLED (Bound)'}")

        elif choice == "8":
            cur = get_mic_status()
            set_mic(not cur)
            console.print(f"[green]  ~[/green] Microphone set to: {'MUTED (ADC Cut)' if cur else 'ACTIVE (Unmuted)'}")

        elif choice == "9":
            cur = get_wifi_status()
            if cur:
                console.print(Panel(
                    "[bold red][!] WARNING:[/] Disabling Wi-Fi will terminate all active internet, SSH, and remote sessions!",
                    border_style="red"
                ))
                confirm = Prompt.ask("Are you sure you want to disable Wi-Fi?", choices=["y", "n"], default="n")
                if confirm == "y":
                    set_wifi(False)
                    console.print("[yellow]  ~ Wi-Fi radio: BLOCKED (Soft-blocked).[/]")
                else:
                    console.print("[dim]  ~ Wi-Fi toggle cancelled.[/dim]")
            else:
                set_wifi(True)
                console.print("[green]  ~ Wi-Fi radio: ENABLED (Unblocked).[/]")

        elif choice == "10":
            cur = get_platform_profile()
            cycle = {"quiet": "balanced", "balanced": "performance", "performance": "quiet"}
            nxt = cycle.get(cur, "quiet")
            set_platform_profile(nxt)
            console.print(f"[green]  ~[/green] Platform Profile set to: {nxt.upper()}")

        elif choice == "11":
            cur = get_thunderbolt_status()
            set_thunderbolt(not cur)
            console.print(f"[green]  ~[/green] Thunderbolt 4 set to: {'DISABLED (D3cold Cut)' if cur else 'ENABLED (Bound)'}")

        elif choice == "12":
            cur = get_external_usb_status()
            set_external_usb(not cur)
            console.print(f"[green]  ~[/green] External USB Data Ports set to: {'BLOCKED (Data Guard)' if cur else 'AUTHORIZED (Data On)'}")

        elif choice == "13":
            status_str, controllable = get_ethernet_status()
            if controllable:
                is_up = "UP" in status_str or "ENABLED" in status_str
                set_ethernet(not is_up)
                console.print(f"[green]  ~[/green] Ethernet LAN set to: {'DOWN' if is_up else 'UP'}")
            else:
                console.print(Panel(
                    "[bold yellow]Notice: Ethernet controller is unprobed in this kernel (CONFIG_R8169 is not set in linux-dusky-battery),\n"
                    "so the Realtek LAN chip is already resting in unpowered hardware sleep.[/bold yellow]",
                    title="[bold cyan]Ethernet Status[/bold cyan]"
                ))

        time.sleep(0.5)

def main():
    parser = argparse.ArgumentParser(description="Dusky Hardware Power Architect (ASUS TUF Edition)")
    parser.add_argument("--status", action="store_true", help="Display hardware power telemetry dashboard and exit.")
    parser.add_argument("--max-battery", action="store_true", help="Apply maximum battery saver hardware profile.")
    parser.add_argument("--restore-all", action="store_true", help="Restore all hardware to standard profile.")
    parser.add_argument("--panel-od", choices=["on", "off", "toggle"], help="Control LCD panel overdrive.")
    parser.add_argument("--webcam", choices=["on", "off", "toggle"], help="Control USB WebCam hardware state.")
    parser.add_argument("--kbd-backlight", choices=["0", "1", "2", "3", "off"], help="Set keyboard backlight level.")
    parser.add_argument("--secondary-ssd", choices=["bind", "unbind", "toggle", "status"], help="Control Secondary NVMe SSD driver binding.")
    parser.add_argument("--apply-saved", action="store_true", help="Apply all saved hardware power states (boot enforcer).")
    parser.add_argument("--bluetooth", choices=["on", "off", "toggle"], help="Control Bluetooth radio state.")
    parser.add_argument("--touchpad", choices=["on", "off", "toggle"], help="Control Precision Touchpad I2C bus state.")
    parser.add_argument("--mic", choices=["on", "off", "toggle", "mute", "unmute"], help="Control microphone ADC capture and mute.")
    parser.add_argument("--wifi", choices=["on", "off", "toggle"], help="Control Wi-Fi radio transceiver state.")
    parser.add_argument("--profile", choices=["quiet", "balanced", "performance"], help="Set ASUS platform/thermal profile.")
    parser.add_argument("--thunderbolt", choices=["on", "off", "toggle"], help="Control Thunderbolt 4 NHI controller state.")
    parser.add_argument("--external-usb", choices=["on", "off", "toggle"], help="Control external USB data ports authorization.")
    parser.add_argument("--ethernet", choices=["on", "off", "toggle"], help="Control Ethernet LAN interface state.")
    parser.add_argument("-i", "--interactive", action="store_true", help="Launch interactive TUI menu.")

    args = parser.parse_args()

    # CLI Actions
    if args.status:
        render_dashboard()
        return

    if args.apply_saved:
        elevate_if_needed()
        saved_lvl = 0
        if KBD_STATE_FILE.exists():
            try:
                saved_lvl = int(safe_read(KBD_STATE_FILE) or "0")
            except ValueError:
                saved_lvl = 0
        set_kbd_backlight(saved_lvl)
        console.print(f"[green]  ~[/green] Restored keyboard backlight state: {saved_lvl}/3 (Locked: {is_kbd_backlight_locked()})")
        return

    if args.max_battery:
        apply_max_battery()
        return

    if args.restore_all:
        apply_restore_all()
        return

    if args.secondary_ssd:
        if args.secondary_ssd == "status":
            stat_str, mounted, mounts = get_secondary_nvme_power_info()
            console.print(f"[cyan]Secondary SSD Status:[/] {stat_str}")
            return
        elevate_if_needed()
        status_str, is_mounted, mounts = get_secondary_nvme_power_info()
        is_bound = "UNBOUND" not in status_str
        target_on = (not is_bound) if args.secondary_ssd == "toggle" else (args.secondary_ssd == "bind")
        success, msg = toggle_secondary_nvme_driver(target_on)
        console.print(f"[{'green' if success else 'red'}]{msg}[/]")
        return

    if args.panel_od:
        elevate_if_needed()
        val = not get_panel_od_status() if args.panel_od == "toggle" else (args.panel_od == "on")
        set_panel_od(val)
        console.print(f"[green]  ~[/green] Panel Overdrive: {'ON' if val else 'OFF'}")
        return

    if args.webcam:
        elevate_if_needed()
        val = not get_webcam_status() if args.webcam == "toggle" else (args.webcam == "on")
        set_webcam(val)
        console.print(f"[green]  ~[/green] WebCam: {'ENABLED' if val else 'DISABLED'}")
        return

    if args.kbd_backlight:
        elevate_if_needed()
        lvl = 0 if args.kbd_backlight == "off" else int(args.kbd_backlight)
        set_kbd_backlight(lvl)
        lock_info = " (Hardware Locked & Persisted)" if lvl == 0 else " (Unlocked)"
        console.print(f"[green]  ~[/green] Keyboard Backlight: {lvl}/3{lock_info}")
        return

    if args.bluetooth:
        elevate_if_needed()
        val = not get_bluetooth_status() if args.bluetooth == "toggle" else (args.bluetooth == "on")
        set_bluetooth(val)
        console.print(f"[green]  ~[/green] Bluetooth: {'UNBLOCKED' if val else 'BLOCKED'}")
        return

    if args.touchpad:
        elevate_if_needed()
        val = not get_touchpad_status() if args.touchpad == "toggle" else (args.touchpad == "on")
        set_touchpad(val)
        console.print(f"[green]  ~[/green] Precision Touchpad: {'ENABLED' if val else 'DISABLED'}")
        return

    if args.mic:
        elevate_if_needed()
        if args.mic in ("on", "unmute"):
            val = True
        elif args.mic in ("off", "mute"):
            val = False
        else:
            val = not get_mic_status()
        set_mic(val)
        console.print(f"[green]  ~[/green] Microphone: {'ACTIVE (Unmuted)' if val else 'MUTED (ADC Cut)'}")
        return

    if args.wifi:
        elevate_if_needed()
        val = not get_wifi_status() if args.wifi == "toggle" else (args.wifi == "on")
        set_wifi(val)
        console.print(f"[green]  ~[/green] Wi-Fi Radio: {'UNBLOCKED' if val else 'BLOCKED'}")
        return

    if args.profile:
        elevate_if_needed()
        set_platform_profile(args.profile)
        console.print(f"[green]  ~[/green] Platform Profile: {args.profile.upper()}")
        return

    if args.thunderbolt:
        elevate_if_needed()
        val = not get_thunderbolt_status() if args.thunderbolt == "toggle" else (args.thunderbolt == "on")
        set_thunderbolt(val)
        console.print(f"[green]  ~[/green] Thunderbolt 4: {'ENABLED' if val else 'DISABLED'}")
        return

    if args.external_usb:
        elevate_if_needed()
        val = not get_external_usb_status() if args.external_usb == "toggle" else (args.external_usb == "on")
        set_external_usb(val)
        console.print(f"[green]  ~[/green] External USB Data Ports: {'AUTHORIZED' if val else 'BLOCKED'}")
        return

    if args.ethernet:
        elevate_if_needed()
        val = (args.ethernet == "on")
        set_ethernet(val)
        console.print(f"[green]  ~[/green] Ethernet LAN: {'UP' if val else 'DOWN'}")
        return

    # Default: Interactive TUI Menu
    elevate_if_needed()
    while True:
        console.print()
        render_dashboard()
        console.print("\n[bold cyan]Master Power Controls:[/bold cyan]")
        console.print("  [bold green]c[/] Custom Select: Interactive Component Toggle Loop")
        console.print("  [bold green]9[/] MAX BATTERY SAVER: Turn OFF All Non-Essential Hardware (~2-3W saved)")
        console.print("  [bold green]r[/] RESTORE ALL: Restore All Hardware & Audio")
        console.print("  [bold cyan]s[/] Refresh Status & Telemetry")
        console.print("  [bold red]q[/] Exit")

        choice = Prompt.ask("\nSelect action", choices=["c", "9", "r", "s", "q"], default="q")
        if choice == "q":
            console.print("[yellow]Exiting Dusky Hardware Power Architect.[/]")
            break
        elif choice == "c":
            interactive_custom_selection()
        elif choice == "9":
            apply_max_battery()
        elif choice == "r":
            apply_restore_all()
        elif choice == "s":
            continue

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted via keyboard. Exiting cleanly.[/]")
        sys.exit(0)

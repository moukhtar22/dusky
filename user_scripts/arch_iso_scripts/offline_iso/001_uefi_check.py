#!/usr/bin/env python3
"""
001_uefi_check.py - DUSKY Boot Environment Probe - Python 3.14.6 - July 2026
Target: kernel 7.1+, systemd 261, Arch ISO environments only. No legacy compatibility.

Role:   Detect the firmware boot mode (UEFI vs BIOS) using a never-fail multi-signal probe
        and report it. This script is a PURE INFORMER: it ALWAYS exits 0, in auto mode
        and interactive mode alike, because both firmware modes are supported end-to-end:

          UEFI -> bootloader stage (151_systemd_bootloader.py) deploys systemd-boot
          BIOS -> bootloader stage deploys GRUB i386-pc fallback

        Firmware policy gates (--allow-bios in 030_partitioning.py) are enforced
        downstream. This probe never claims a mode is unsupported and never aborts.
        If python-rich is unavailable (or even broken), it degrades to a plain-text
        report instead of dying. Every sysfs/proc/kmsg read is exception-proof.
"""
from __future__ import annotations
import os, re, sys, shutil, signal, subprocess
from pathlib import Path

def _ensure_rich() -> bool:
    """Returns True when rich is importable; never raises; never exits."""
    try:
        import importlib.util
        if importlib.util.find_spec("rich") is not None:
            return True
    except Exception:
        return False
    try:
        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            return False
        try:
            du = shutil.disk_usage("/run/archiso/cowspace")
            if du.free < 250*1024*1024:
                subprocess.run(["mount","-o","remount,size=2G","/run/archiso/cowspace"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        print(">> Installing python-rich...", file=sys.stderr)
        subprocess.run(["pacman","-Sy","--needed","--noconfirm","python-rich"], stdout=sys.stderr, stderr=sys.stderr)
        import importlib.util
        return importlib.util.find_spec("rich") is not None
    except Exception:
        return False

HAVE_RICH = False
try:
    if _ensure_rich():
        from rich.console import Console
        from rich.panel import Panel
        from rich.align import Align
        from rich.text import Text
        from rich import box
        HAVE_RICH = True
except Exception:
    HAVE_RICH = False

def make_console():
    if not HAVE_RICH:
        return None
    try:
        term = os.environ.get("TERM", "")
        if term in ("dumb", "unknown"):
            return Console(color_system=None, force_terminal=False, no_color=True, legacy_windows=False)
        return Console(color_system="auto", force_terminal=None, legacy_windows=False, safe_box=False, highlight=False, markup=True)
    except Exception:
        return None

console = make_console()

# Probe paths as module-level constants so tests can redirect them.
SYS_FIRMWARE_EFI = Path("/sys/firmware/efi")
SYS_FW_PLATFORM_SIZE = Path("/sys/firmware/efi/fw_platform_size")
PROC_MOUNTS = Path("/proc/mounts")
DMESG_BIN = "dmesg"

def _dir_exists(p: Path) -> bool:
    """Never raises: any exception is treated as 'directory absent'."""
    try:
        return p.is_dir()
    except Exception:
        return False

def _file_read(p: Path) -> str:
    """Never raises: returns '' on any failure."""
    try:
        return p.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""

def read_fw_platform_size() -> int | None:
    """fw_platform_size is the definitive firmware width on kernels 6.6+/7.x (64 or 32)."""
    try:
        return int(_file_read(SYS_FW_PLATFORM_SIZE))
    except Exception:
        return None

def efivarfs_mounted() -> bool:
    """
    Catches UEFI systems where /sys/firmware/efi exists but efivarfs is unmounted.
    Precision: only the device field (0) or fstype field (2) may be 'efivarfs',
    so a mount whose mountpoint is coincidentally named 'efivarfs' cannot
    false-positive.
    """
    for line in _file_read(PROC_MOUNTS).splitlines():
        fields = line.split()
        if len(fields) >= 3 and (fields[0] == "efivarfs" or fields[2] == "efivarfs"):
            return True
    return False

def kernel_messages_mention_efi() -> bool:
    """Last-resort signal: EFI initialization in the kernel ring buffer."""
    try:
        r = subprocess.run([DMESG_BIN], check=False, capture_output=True, text=True, timeout=10)
        return bool(re.search(r"efi:.*v[0-9]", r.stdout or "", re.IGNORECASE))
    except Exception:
        return False

def detect_boot_mode() -> tuple[str, int | None]:
    """
    Never-fail multi-signal UEFI detection. Returns (mode, fw_platform_size).
    Signals in order of definitiveness on kernel 7.1+:
      1. /sys/firmware/efi present (source of truth for EFI stub/bootloader boots)
      2. fw_platform_size file (definitive architecture check)
      3. efivarfs in /proc/mounts (efivars may be unmounted)
      4. 'efi: vX' in dmesg (kernel initialized EFI runtime)
    Any exception anywhere degrades to a safe default; never raises, never aborts.
    """
    fw_size = read_fw_platform_size()
    if _dir_exists(SYS_FIRMWARE_EFI):
        return "UEFI", fw_size
    if fw_size is not None:
        return "UEFI", fw_size
    if efivarfs_mounted():
        return "UEFI", fw_size
    if kernel_messages_mention_efi():
        return "UEFI", fw_size
    return "BIOS", fw_size

def _firmware_width(fw_size: int | None) -> str:
    return f"{fw_size}-bit firmware" if fw_size else "bitness unknown"

def build_report() -> tuple[str, str, str, str, int | None]:
    """Returns (mode, kernel, systemd, width, fw_size). Never raises."""
    try:
        kernel = os.uname().release or "unknown"
    except Exception:
        kernel = "unknown"
    try:
        r = subprocess.run(["systemctl", "--version"], check=False, capture_output=True, text=True, timeout=5)
        systemd = (r.stdout.splitlines()[0].strip() if r.stdout else "") or "systemd 261"
    except Exception:
        systemd = "systemd 261"
    mode, fw_size = detect_boot_mode()
    return mode, kernel, systemd, _firmware_width(fw_size), fw_size

def main() -> int:
    mode, kernel, systemd, width, _fw_size = build_report()

    try:
        import json
        state_file = Path("/tmp/dusky_state.json")
        state_data = {}
        if state_file.exists():
            try:
                state_data = json.loads(state_file.read_text())
            except Exception:
                pass
        state_data["boot_mode"] = mode
        state_file.write_text(json.dumps(state_data, indent=2))
    except Exception:
        pass

    if console is None:
        # Plain-text fallback: same information, zero dependencies.
        print("DUSKY BOOT ENVIRONMENT PROBE")
        print(f"  Firmware Mode : {mode}  ({width})")
        print(f"  Kernel        : {kernel}")
        print(f"  systemd       : {systemd}")
        print("  Bootloader stage will deploy " + ("systemd-boot." if mode == "UEFI" else "the GRUB i386-pc BIOS fallback."))
        print("  Status        : OK - proceeding")
        if mode != "UEFI":
            print("  Optional      : enable UEFI in firmware to use systemd-boot instead")
        return 0

    header = Text.from_markup("[bold cyan]DUSKY BOOT ENVIRONMENT PROBE[/bold cyan]", justify="center")

    if mode == "UEFI":
        body = Text()
        body.append(f"\n  Firmware Mode : ", style="bold white")
        body.append("UEFI", style="green bold")
        body.append(f"  ({width})\n", style="dim")
        body.append(f"  Kernel        : ", style="bold white")
        body.append(f"{kernel}\n", style="cyan")
        body.append(f"  systemd       : ", style="bold white")
        body.append(f"{systemd}\n\n", style="cyan")
        body.append("  Bootloader stage will deploy systemd-boot.\n", style="white")
        body.append("  Status        : ", style="bold white")
        body.append("OK — proceeding\n", style="green bold")
        panel = Panel.fit(body, box=box.ROUNDED, border_style="green", padding=(0, 2))
    else:
        body = Text()
        body.append(f"\n  Firmware Mode : ", style="bold white")
        body.append("BIOS (Legacy / CSM)", style="yellow bold")
        body.append(f"  ({width})\n", style="dim")
        body.append(f"  Kernel        : ", style="bold white")
        body.append(f"{kernel}\n", style="cyan")
        body.append(f"  systemd       : ", style="bold white")
        body.append(f"{systemd}\n\n", style="cyan")
        body.append("  Bootloader stage will deploy the GRUB i386-pc BIOS fallback.\n", style="white")
        body.append("  Status        : ", style="bold white")
        body.append("OK — proceeding\n", style="green bold")
        body.append("  Optional      : ", style="dim")
        body.append("enable UEFI in firmware to use systemd-boot instead\n", style="dim")
        panel = Panel.fit(body, box=box.ROUNDED, border_style="yellow", padding=(0, 2))

    console.print(Align.center(header))
    console.print(Align.center(panel))
    return 0

if __name__ == "__main__":
    def _h(sig, frame):
        if console is not None:
            console.print(f"\n[yellow]Signal {signal.Signals(sig).name}, exiting[/yellow]")
        sys.exit(128 + sig)
    signal.signal(signal.SIGINT, _h)
    signal.signal(signal.SIGTERM, _h)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

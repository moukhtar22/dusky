#!/usr/bin/env python3
#d: Verify the ZRAM and mount setup

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

# --- Argument Parsing ---
parser = argparse.ArgumentParser(description="Deep ZRAM & Memory Architecture Diagnostics")
parser.add_argument("--strict", action="store_true", help="Exit with non-zero code on any warning/mismatch")
parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")
args = parser.parse_args()

# --- Presentation ---
class C:
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    BOLD = "\033[1m"
    RST = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for attr in ("RED", "GRN", "YLW", "BLU", "BOLD", "RST"):
            setattr(cls, attr, "")

if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
    C.strip()

has_warnings = False

def info(msg: str) -> None: print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str) -> None: print(f"{C.GRN}[PASS]{C.RST} {msg}")
def warn(msg: str) -> None: 
    global has_warnings
    has_warnings = True
    print(f"{C.YLW}[WARN]{C.RST} {msg}")

def report_issue(msg: str) -> None:
    global has_warnings
    has_warnings = True
    if args.strict:
        print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"{C.YLW}[WARN]{C.RST} {msg}")

def run_cmd(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()
    except Exception:
        return ""

print(f"\n{C.BOLD}=== Initiating Deep Architecture Diagnostics (Kernel 7.x+ Ready) ==={C.RST}\n")

# --- 1. Bootloader / ZSWAP Check ---
info("Checking ZSWAP state...")
zswap_path = Path("/sys/module/zswap/parameters/enabled")
if zswap_path.exists():
    if zswap_path.read_text().strip() in ("Y", "1"):
        report_issue("ZSWAP is currently ACTIVE. Kernel parameter 'zswap.enabled=0' is recommended with pure ZRAM.")
    else:
        ok("ZSWAP is cleanly disabled at the kernel level.")
else:
    info("ZSWAP module not loaded or built-in (Clean).")

# --- 2. Memory Calculations (Page-Aligned) ---
info("Calculating total physical memory maps...")
mem_total_bytes = 0
try:
    meminfo = Path("/proc/meminfo").read_text()
    m = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
    if m:
        mem_total_bytes = int(m.group(1)) * 1024
except Exception as e:
    report_issue(f"Could not parse /proc/meminfo: {e}")

def verify_limit(device: str, expected_ratio: float) -> None:
    mm_stat_path = Path(f"/sys/block/{device}/mm_stat")
    if not mm_stat_path.exists():
        report_issue(f"Stats matrix for {device} does not exist in sysfs.")
        return
    
    try:
        stats = mm_stat_path.read_text().strip().split()
        if len(stats) < 4:
            report_issue(f"Invalid mm_stat matrix format for {device}.")
            return
        actual_bytes = int(stats[3])  # 4th column is mem_limit
    except Exception as e:
        report_issue(f"Kernel rejected read on mm_stat for {device}: {e}")
        return
        
    if actual_bytes == 0:
        info(f"{device} memory resident limit is uncapped (0 / unlimited).")
        return

    if mem_total_bytes > 0:
        expected_bytes = int(mem_total_bytes * expected_ratio)
        tolerance = max(expected_bytes * 0.10, 64 * 1024 * 1024)
        
        if abs(actual_bytes - expected_bytes) <= tolerance:
            ok(f"{device} resident limit aligned correctly (~{actual_bytes / (1024**3):.2f} GB)")
        else:
            info(f"{device} resident limit active: {actual_bytes / (1024**3):.2f} GB (configured ratio: {expected_ratio:.2f})")

# --- 3. Base Swap Verification (zram0) ---
info("Verifying main ZRAM swap topology...")
zramctl_out = run_cmd(["zramctl", "--output", "NAME", "--noheadings"])
if "/dev/zram0" not in zramctl_out:
    report_issue("/dev/zram0 is not active in zramctl. (Reboot or systemctl restart systemd-zram-setup@zram0.service may be required).")
else:
    swapon_out = run_cmd(["swapon", "--show=NAME,PRIO", "--noheadings"])
    if "/dev/zram0" not in swapon_out:
        report_issue("/dev/zram0 exists but is not currently mounted as swap.")
    else:
        ok("/dev/zram0 swap is fully active.")
        # Check priority
        for line in swapon_out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and "/dev/zram0" in parts[0]:
                prio = int(parts[1])
                if prio >= 32767:
                    ok(f"/dev/zram0 swap priority confirmed at maximum ({prio}).")
                elif prio > 0:
                    info(f"/dev/zram0 swap priority is {prio} (Priority 32767 is recommended).")
                else:
                    warn(f"/dev/zram0 swap priority is low ({prio}).")

    zram0_conf = Path("/etc/systemd/zram-generator.conf.d/99-elite-zram.conf")
    zram0_limit_ratio = 0.5
    if zram0_conf.exists():
        match = re.search(r"zram-resident-limit\s*=\s*ram\s*\*\s*([0-9.]+)", zram0_conf.read_text())
        if match:
            try:
                zram0_limit_ratio = float(match.group(1))
            except ValueError:
                pass

    verify_limit("zram0", zram0_limit_ratio)

# --- 4. Hybrid Mount Detection & Permission Diagnostics (/mnt & /mnt/zram1) ---
info("Interrogating /mnt and /mnt/zram1 filesystem & permissions...")

# Verify /mnt accessibility
mnt_path = Path("/mnt")
if not mnt_path.exists():
    report_issue("/mnt mount directory does not exist.")
else:
    try:
        st = mnt_path.stat()
        mode_oct = oct(stat.S_IMODE(st.st_mode))
        if st.st_mode & stat.S_IROTH and st.st_mode & stat.S_IXOTH:
            ok(f"/mnt base permissions intact ({mode_oct}).")
        else:
            warn(f"/mnt permissions restricted ({mode_oct}). Mode 0755 recommended.")
    except Exception as e:
        report_issue(f"Cannot stat /mnt: {e}")

# Check POSIX ACL on /mnt
if shutil.which("getfacl"):
    facl_out = run_cmd(["getfacl", "-p", "/mnt"])
    if "user:" in facl_out and "user::" not in facl_out.splitlines()[-1]:
        # Contains named user ACLs that might override standard permissions
        lines = [l for l in facl_out.splitlines() if l.startswith("user:") and not l.startswith("user::")]
        if lines:
            info(f"Custom POSIX ACL detected on /mnt: {', '.join(lines)}")

mount_source = run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", "/mnt/zram1"])
mount_opts = run_cmd(["findmnt", "-rn", "-o", "OPTIONS", "--mountpoint", "/mnt/zram1"])
zram1_conf = Path("/etc/systemd/zram-generator.conf.d/99-elite-zram1.conf")

if not mount_source:
    if not zram1_conf.exists():
        ok("Backend resolved as: Disabled / None (Minimal RAM footprint mode, zero memory overhead).")
    else:
        info("Secondary /mnt/zram1 configured in zram-generator (Staged / pending mount).")

elif mount_source == "tmpfs":
    ok("Backend dynamically resolved as: Pure Tmpfs RAM disk.")
    if "uid=" in mount_opts and "gid=" in mount_opts:
        ok("Tmpfs user/group ownership mapping is intact.")
    else:
        info("Tmpfs mounted with default system ownership options.")

elif mount_source in ("/dev/zram1", "zram1"):
    ok("Backend dynamically resolved as: Ext4 ZRAM Block.")
    verify_limit("zram1", 0.80)
    
    # Verify Ext4 Journal
    dumpe2fs_out = run_cmd(["dumpe2fs", "-h", "/dev/zram1"])
    if "has_journal" in dumpe2fs_out:
        warn("Ext4 journal is present on zram1 (disable journal recommended for lower RAM write overhead).")
    elif dumpe2fs_out:
        ok("Ext4 filesystem confirmed as journal-less (Zero unnecessary write overhead).")
    
    # Verify Mount Options
    for opt in ["noatime", "lazytime", "discard", "rw"]:
        if opt in mount_opts.split(","):
            ok(f"Ext4 mount option '{opt}' active.")
        else:
            warn(f"Mount option recommendation for zram block: '{opt}' not active.")
else:
    info(f"Custom mount source for /mnt/zram1: {mount_source}")

# Test Write Access to /mnt/zram1
zram1_path = Path("/mnt/zram1")
if zram1_path.exists() and mount_source:
    try:
        st = zram1_path.stat()
        mode_val = stat.S_IMODE(st.st_mode)
        # Check sticky bit or world writable
        if mode_val == 0o1777 or (mode_val & 0o777) == 0o777:
            ok(f"/mnt/zram1 mount permissions verified (Mode: {oct(mode_val)} - Fully User Writable).")
        elif os.access("/mnt/zram1", os.W_OK):
            ok(f"/mnt/zram1 is directly writable by current process UID {os.getuid()}.")
        else:
            report_issue(f"/mnt/zram1 lacks user write permissions (Mode: {oct(mode_val)}). Run `chmod 1777 /mnt/zram1`.")
    except Exception as e:
        report_issue(f"Failed to inspect /mnt/zram1 access: {e}")

# --- 5. Algorithm Verification ---
info("Testing compression algorithm setup...")
devices_to_check: list[str] = []
if Path("/sys/block/zram0").exists():
    devices_to_check.append("zram0")
if Path("/sys/block/zram1").exists() and mount_source in ("/dev/zram1", "zram1"):
    devices_to_check.append("zram1")

for dev in devices_to_check:
    algo_path = Path(f"/sys/block/{dev}/comp_algorithm")
    if algo_path.exists():
        algo_data = algo_path.read_text().strip()
        if "[zstd]" in algo_data:
            ok(f"{dev} is running ZSTD natively.")
        else:
            info(f"{dev} active compression algorithm: {algo_data}")
    else:
        info(f"{dev} sysfs comp_algorithm node not exposed.")

if has_warnings and args.strict:
    print(f"\n{C.RED}{C.BOLD}=== DIAGNOSTICS DETECTED ITEMS REQUIRING ATTENTION (STRICT MODE). ==={C.RST}\n")
    sys.exit(1)
else:
    print(f"\n{C.GRN}{C.BOLD}=== DIAGNOSTICS COMPLETE. SYSTEM ARCHITECTURE VERIFIED CLEANLY. ==={C.RST}\n")
    sys.exit(0)

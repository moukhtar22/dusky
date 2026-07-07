#!/usr/bin/env python3
# =============================================================================
# Elite Arch Linux Boot-Time Memory Reclaimer
# Target: Arch Linux Cutting-Edge (Kernel 7.1+, Python 3.14+, systemd 260+)
# Scope: Platinum Grade. Forcefully pushes cold boot-initialization RAM to ZRAM.
# =============================================================================

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import NoReturn

# --- Presentation (Zero-Dependency ANSI) ---
class C:
    BOLD = "\033[1m"
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    BLU = "\033[1;34m"
    RST = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for name in ("BOLD", "RED", "GRN", "BLU", "RST"):
            setattr(cls, name, "")

def info(msg: str) -> None: print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str) -> None: print(f"{C.GRN}[ OK ]{C.RST} {msg}")
def err(msg: str) -> None: print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)
def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)

# --- Argument Parsing (Executed BEFORE Privilege Escalation) ---
parser = argparse.ArgumentParser(description="Elite Arch Linux Boot-Time Memory Reclaimer")
parser.add_argument("--run", action="store_true", help="Directly trigger the memory reclaim task")
parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")

args = parser.parse_args()

if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
    C.strip()

# --- Privilege Escalation ---
def escalate_privileges() -> None:
    if os.geteuid() != 0:
        info("Root privileges required. Escalating...")
        if subprocess.call(["command", "-v", "sudo"], stdout=subprocess.DEVNULL, shell=True) != 0:
            die("sudo is required to run this script as root.")
        os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])

escalate_privileges()

def write_file_atomic(path: Path, content: str, mode: int = 0o644) -> None:
    if path.exists() and path.read_text() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

# --- Reclaim Logic ---
def perform_reclaim() -> None:
    info("Initiating targeted boot-time cold memory sweep...")
    
    slices = ["user.slice", "system.slice"]
    reclaimed_total_bytes = 0
    
    for slice_name in slices:
        cgroup_dir = Path("/sys/fs/cgroup") / slice_name
        current_file = cgroup_dir / "memory.current"
        reclaim_file = cgroup_dir / "memory.reclaim"
        stat_file = cgroup_dir / "memory.stat"
        
        if not current_file.exists() or not reclaim_file.exists() or not stat_file.exists():
            info(f"Cgroup slice {slice_name} does not support memory reclaim. Skipping.")
            continue
            
        try:
            # Parse anonymous memory from memory.stat to compute target for swappiness=max
            stat_content = stat_file.read_text()
            anon_match = re.search(r"^anon\s+(\d+)", stat_content, re.M)
            if not anon_match:
                info(f"Could not parse anonymous memory stats for {slice_name}. Skipping.")
                continue
            anon_bytes = int(anon_match.group(1))
            
            # Capture real state before reclaim request
            before_bytes = int(current_file.read_text().strip())
            
            # Request 50% eviction of anonymous memory
            target_reclaim = int(anon_bytes * 0.50)
            
            if target_reclaim > 0:
                # Synchronous kernel command
                reclaim_command = f"{target_reclaim} swappiness=max"
                reclaim_file.write_text(reclaim_command)
                
                # Capture real state after kernel processing to calculate the true delta
                after_bytes = int(current_file.read_text().strip())
                actual_reclaimed = before_bytes - after_bytes
                
                if actual_reclaimed > 0:
                    reclaimed_total_bytes += actual_reclaimed
                    ok(f"Reclaimed {actual_reclaimed / (1024*1024):.1f} MB of cold memory from {slice_name}")
                else:
                    info(f"Kernel refused eviction for {slice_name}. No cold pages available.")
            else:
                info(f"No cold anonymous pages available in {slice_name}.")
                    
        except Exception as e:
            info(f"Failed to reclaim memory from {slice_name}: {e}")
            
    ok(f"Targeted cold memory sweep completed. Swapped ~{reclaimed_total_bytes / (1024*1024):.1f} MB of cold pages to ZRAM.")

# --- Installer Logic ---
def deploy_systemd_units() -> None:
    info("Deploying boot-time memory reclaim systemd units...")
    
    # Secure Self-Installation prevents dead systemd units if the source file is moved/deleted
    install_path = Path("/usr/local/bin/dusky_boot_mem_reclaim")
    current_script = Path(os.path.abspath(__file__))
    
    if current_script != install_path:
        install_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current_script, install_path)
        os.chmod(install_path, 0o755)
        ok(f"Binary securely installed to {install_path}")
    
    service_path = Path("/etc/systemd/system/dusky_boot_mem_reclaim.service")
    service_content = f"""[Unit]
Description=Boot-time Cold Memory Reclaimer
After=local-fs.target

[Service]
Type=oneshot
ExecStart={sys.executable} {install_path} --run
"""
    write_file_atomic(service_path, service_content)
    ok(f"Service unit written to {service_path}")
    
    timer_path = Path("/etc/systemd/system/dusky_boot_mem_reclaim.timer")
    timer_content = """[Unit]
Description=Trigger Boot-time Cold Memory Reclaimer 1 minute after boot

[Timer]
OnBootSec=1min

[Install]
WantedBy=timers.target
"""
    write_file_atomic(timer_path, timer_content)
    ok(f"Timer unit written to {timer_path}")
    
    info("Reloading systemd daemon...")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    
    info("Enabling and starting dusky_boot_mem_reclaim.timer...")
    subprocess.run(["systemctl", "enable", "--now", "dusky_boot_mem_reclaim.timer"], check=True)
    
    ok("Boot-time reclaimer timer is active. Cold memory will be purged 1 minute after boot.")

def main() -> None:
    if sys.version_info < (3, 14):
        die(f"Python 3.14+ required, running {sys.version.split()[0]}")
        
    if args.run:
        perform_reclaim()
    else:
        deploy_systemd_units()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.BOLD}{C.RED}aborted — operation cancelled by user.{C.RST}")
        sys.exit(130)

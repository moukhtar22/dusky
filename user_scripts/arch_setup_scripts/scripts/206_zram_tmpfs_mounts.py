#!/usr/bin/env python3
#d: Set up tmpfs or ZRAM RAM disks

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

# --- Presentation (Zero-Dependency ANSI) ---
class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    CYN = "\033[1;36m"
    RST = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for name in ("BOLD", "DIM", "RED", "GRN", "YLW", "BLU", "CYN", "RST"):
            setattr(cls, name, "")

def info(msg: str) -> None: print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str) -> None: print(f"{C.GRN}[ OK ]{C.RST} {msg}")
def warn(msg: str) -> None: print(f"{C.YLW}[WARN]{C.RST} {msg}")
def err(msg: str) -> None: print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)
def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)

# --- Argument Parsing (Executed BEFORE Privilege Escalation) ---
parser = argparse.ArgumentParser(description="Elite Arch Linux Hybrid Memory Mount Configurator")
group = parser.add_mutually_exclusive_group()
group.add_argument("--tmpfs", action="store_true", help="Autonomously deploy pure Tmpfs mapping")
group.add_argument("--zram", action="store_true", help="Autonomously deploy Ext4 ZRAM block mapping")
group.add_argument("--disable", "--none", dest="disable", action="store_true", help="Disable secondary RAM mount / clean up zram1")
parser.add_argument("--size", "-s", type=str, default="", help="Size expression for ZRAM / Tmpfs (e.g. '100%%', '8G', 'ram')")
parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")

args = parser.parse_args()

if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
    C.strip()

# --- Privilege Escalation ---
def escalate_privileges() -> None:
    if os.geteuid() != 0:
        info("Root privileges required. Escalating...")
        if shutil.which("sudo"):
            os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        elif shutil.which("pkexec"):
            os.execvp("pkexec", ["pkexec", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        else:
            die("sudo or pkexec is required to run this script as root.")

escalate_privileges()

# --- Core Constants ---
MOUNT_POINT = Path("/mnt/zram1")
BASE_MOUNT = Path("/mnt")
ZRAM_CONF_FILE = Path("/etc/systemd/zram-generator.conf.d/99-elite-zram1.conf")
TMPFILES_CONF = Path("/etc/tmpfiles.d/zram-mounts.conf")
OVERRIDE_DIR = Path("/etc/systemd/system/systemd-zram-setup@zram1.service.d")
OVERRIDE_CONF = OVERRIDE_DIR / "override.conf"
ZRAM_RESIDENT_LIMIT_EXPR = "ram * 4 / 5"
COMPRESSION_ALGORITHM = "zstd(level=2)"
FS_OPTIONS = "rw,nosuid,nodev,discard,noatime,lazytime,X-mount.mode=1777"
CMD_TIMEOUT = 15

# --- Target User Identification (Dynamic & Wayland/Hyprland Safe) ---
def resolve_target_user() -> tuple[int, int]:
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_uid and int(sudo_uid) != 0:
        return int(sudo_uid), int(sudo_gid or sudo_uid)
    
    try:
        loginuid_path = Path("/proc/self/loginuid")
        if loginuid_path.exists():
            l_uid = int(loginuid_path.read_text().strip())
            if 0 < l_uid < 65534:
                import pwd
                pw = pwd.getpwuid(l_uid)
                return pw.pw_uid, pw.pw_gid
    except Exception:
        pass

    try:
        import pwd
        for pw in pwd.getpwall():
            if 1000 <= pw.pw_uid < 60000 and pw.pw_name != "nobody":
                return pw.pw_uid, pw.pw_gid
    except Exception:
        pass

    uid = os.getuid()
    gid = os.getgid()
    return (1000, 1000) if uid == 0 else (uid, gid)

TARGET_UID, TARGET_GID = resolve_target_user()

# --- Utility Functions ---
def run_cmd(cmd: list[str], ignore_errors: bool = False) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=not ignore_errors, timeout=CMD_TIMEOUT)
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        die(f"Command timed out after {CMD_TIMEOUT}s: {' '.join(cmd)}")
    except subprocess.CalledProcessError as e:
        if not ignore_errors:
            err(f"Command failed: {' '.join(cmd)}")
            print(e.stderr)
            sys.exit(1)
        return ""

def write_file_atomic(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == content:
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

def parse_size_expression(raw: str) -> str:
    s = raw.strip().lower()
    if not s or s in ("auto", "default"):
        return "ram"
    if s.endswith("%"):
        try:
            pct = float(s[:-1])
            return "ram" if pct == 100.0 else f"ram * {pct / 100.0:.2f}".rstrip("0").rstrip(".")
        except ValueError:
            pass
    try:
        n = float(s)
        if 0.0 < n <= 2.0:
            return f"ram * {n:.2f}".rstrip("0").rstrip(".")
        elif 3.0 <= n <= 100.0 and n.is_integer():
            pct = n / 100.0
            return "ram" if pct == 1.0 else f"ram * {pct:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        pass
    m = re.match(r"^([0-9.]+)\s*([gmk]b?)$", s)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        if unit.startswith("g"):
            return str(int(val * 1024))
        elif unit.startswith("m"):
            return str(int(val))
        elif unit.startswith("k"):
            return str(int(val / 1024))
    return re.sub(r"\s*([*/+-])\s*", r" \1 ", s)

def pre_flight_checks() -> None:
    if subprocess.run(["systemd-detect-virt", "--quiet", "--container"], capture_output=True).returncode == 0:
        die("Container detected — refusing to tune memory mounts inside a container.")
    
    cmdline = Path("/proc/cmdline").read_text() if Path("/proc/cmdline").exists() else ""
    if re.search(r"(^|\s)systemd\.zram=0(\s|$)", cmdline):
        die("Kernel cmdline carries systemd.zram=0 — zram device creation is disabled by boot policy.")

def unit_is_loaded(unit: str) -> bool:
    stdout = run_cmd(["systemctl", "show", "-p", "LoadState", "--value", unit], ignore_errors=True)
    return stdout == "loaded"

def assert_unit_loaded(unit: str) -> None:
    if not unit_is_loaded(unit):
        die(f"Systemd failed to ingest the generated unit: {unit}")

def get_mount_source() -> str:
    return run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", str(MOUNT_POINT)], ignore_errors=True)

def fix_mount_permissions() -> None:
    """Fixes /mnt and /mnt/zram1 permissions, ACLs, and tmpfiles rules."""
    if not BASE_MOUNT.exists():
        BASE_MOUNT.mkdir(parents=True, mode=0o755)
    try:
        os.chmod(BASE_MOUNT, 0o755)
        if shutil.which("setfacl"):
            subprocess.run(["setfacl", "-b", "/mnt"], capture_output=True, check=False)
    except Exception:
        pass

    if not MOUNT_POINT.exists():
        MOUNT_POINT.mkdir(parents=True, mode=0o755)
    try:
        os.chmod(MOUNT_POINT, 0o1777)
        if shutil.which("setfacl"):
            subprocess.run(["setfacl", "-b", str(MOUNT_POINT)], capture_output=True, check=False)
    except Exception:
        pass

    tmpfiles_content = """# Managed by Dusky Memory & Swap Subsystem
d /mnt 0755 root root -
d /mnt/zram1 1777 root root -
z /mnt 0755 root root -
z /mnt/zram1 1777 root root -
"""
    try:
        write_file_atomic(TMPFILES_CONF, tmpfiles_content)
        if shutil.which("systemd-tmpfiles"):
            subprocess.run(["systemd-tmpfiles", "--create", str(TMPFILES_CONF)], capture_output=True, check=False)
    except Exception:
        pass

    override_content = """[Service]
ExecStartPost=/usr/sbin/tune2fs -O ^has_journal /dev/%i
ExecStartPost=-/usr/bin/chmod 1777 /mnt/zram1
"""
    try:
        OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
        write_file_atomic(OVERRIDE_CONF, override_content)
    except Exception:
        pass

def prepare_mount_directory() -> None:
    fix_mount_permissions()
def safely_unmount_and_stage(target_backend: str, mount_point: Path = MOUNT_POINT) -> Path | None:
    """
    Intelligently unmounts mount_point and stages files for seamless migration:
    - Releasing active processes holding the mountpoint (SIGTERM -> SIGKILL) to quiesce filesystem.
    - Syncs dirty buffers to prevent data loss.
    - Accurately checks allocated disk block size (sparse-aware).
    - Stages files using rsync with sparse and metadata preservation (fallback to cp -a).
    - Cleans systemd failed states and performs unmount with lazy unmount fallback.
    """
    stage_dir: Path | None = None

    src = run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", str(mount_point)], ignore_errors=True)
    if src:
        # 1. Quiesce filesystem by releasing holding processes
        info(f"Releasing active processes holding {mount_point}...")
        if shutil.which("fuser"):
            subprocess.run(["fuser", "-km", "-TERM", str(mount_point)], capture_output=True, check=False)
            time.sleep(0.3)
            subprocess.run(["fuser", "-km", "-KILL", str(mount_point)], capture_output=True, check=False)
            time.sleep(0.2)

        try:
            subprocess.run(["sync", "-f", str(mount_point)], capture_output=True, check=False)
        except Exception:
            pass

        # 2. Check for user files to migrate
        try:
            items = [p for p in mount_point.iterdir() if p.name not in ("lost+found", ".Trash-1000")]
            if items:
                info(f"Detected {len(items)} file(s)/folder(s) on {mount_point}. Staging for migration...")
                du_out = run_cmd(["du", "-s", "-B1", "--exclude=lost+found", "--exclude=.Trash-1000", str(mount_point)], ignore_errors=True)
                allocated_bytes = int(du_out.split()[0]) if du_out and du_out.split()[0].isdigit() else 1024 * 1024 * 1024

                candidate_dirs = [Path("/var/tmp"), Path("/tmp")]
                for cand in candidate_dirs:
                    try:
                        st = os.statvfs(str(cand))
                        free_bytes = st.f_bavail * st.f_frsize
                        if free_bytes > (allocated_bytes + 1024 * 1024 * 200):  # +200MB margin
                            s_dir = cand / f".zram1_migration_{os.getpid()}_{int(time.time())}"
                            s_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
                            
                            if shutil.which("rsync"):
                                res = subprocess.run(
                                    ["rsync", "-aHAX", "--sparse", "--exclude=lost+found", "--exclude=.Trash-1000", f"{mount_point}/", f"{s_dir}/"],
                                    capture_output=True, text=True, check=False
                                )
                                if res.returncode == 0:
                                    stage_dir = s_dir
                            
                            if not stage_dir:
                                for it in items:
                                    if it.is_dir():
                                        shutil.copytree(it, s_dir / it.name, symlinks=True, dirs_exist_ok=True)
                                    else:
                                        subprocess.run(["cp", "-a", "--sparse=always", str(it), str(s_dir / it.name)], capture_output=True, check=False)
                                stage_dir = s_dir

                            if stage_dir:
                                ok(f"Successfully staged {len(items)} item(s) to {stage_dir}.")
                                break
                    except Exception as e:
                        warn(f"Staging to {cand} failed: {e}. Trying next candidate...")
                
                if not stage_dir:
                    warn("Could not stage files (insufficient space or permission). Discarding files as requested.")
        except Exception as e:
            warn(f"Error inspecting files in {mount_point}: {e}. Proceeding with discard.")

        # 3. Stop systemd mount / generator services
        run_cmd(["systemctl", "stop", "mnt-zram1.mount"], ignore_errors=True)
        run_cmd(["systemctl", "stop", "systemd-zram-setup@zram1.service"], ignore_errors=True)

        # 4. Standard unmount -> force lazy unmount fallback
        run_cmd(["umount", "-q", str(mount_point)], ignore_errors=True)
        cur_src = run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", str(mount_point)], ignore_errors=True)
        if cur_src:
            warn(f"{mount_point} still busy. Performing lazy unmount (umount -f -l)...")
            run_cmd(["umount", "-f", "-l", str(mount_point)], ignore_errors=True)
            time.sleep(0.3)

    # Reset zram1 block device if existing and clear systemd failure states
    run_cmd(["zramctl", "--reset", "/dev/zram1"], ignore_errors=True)
    run_cmd(["systemctl", "reset-failed", "mnt-zram1.mount", "systemd-zram-setup@zram1.service"], ignore_errors=True)
    return stage_dir


def restore_staged_files(stage_dir: Path | None, mount_point: Path = MOUNT_POINT) -> None:
    """Restores previously staged files back to mount_point after the new filesystem is mounted."""
    if not stage_dir or not stage_dir.exists():
        return
    try:
        info(f"Restoring migrated data to {mount_point}...")
        if shutil.which("rsync"):
            res = subprocess.run(
                ["rsync", "-aHAX", "--sparse", f"{stage_dir}/", f"{mount_point}/"],
                capture_output=True, text=True, check=False
            )
            if res.returncode == 0:
                shutil.rmtree(stage_dir, ignore_errors=True)
                ok(f"Restored migrated data to {mount_point} successfully.")
                return
        
        items = list(stage_dir.iterdir())
        for it in items:
            dest = mount_point / it.name
            if it.is_dir():
                shutil.copytree(it, dest, symlinks=True, dirs_exist_ok=True)
            else:
                subprocess.run(["cp", "-a", "--sparse=always", str(it), str(dest)], capture_output=True, check=False)
        shutil.rmtree(stage_dir, ignore_errors=True)
        ok(f"Restored {len(items)} item(s) to {mount_point} successfully.")
    except Exception as e:
        warn(f"Failed to restore some files from {stage_dir}: {e}")


def resolve_live_conflicts(target_backend: str, mount_unit_name: str) -> Path | None:
    return safely_unmount_and_stage(target_backend)

# --- Backend Configurators ---
def configure_tmpfs(mount_unit_name: str, mount_unit_path: Path) -> None:
    info(f"Initializing Pure Tmpfs Mount for: {C.BOLD}{MOUNT_POINT}{C.RST}")
    stage_dir = resolve_live_conflicts("tmpfs", mount_unit_name)

    if ZRAM_CONF_FILE.exists():
        ZRAM_CONF_FILE.unlink()
        run_cmd(["systemctl", "daemon-reload"])
    run_cmd(["zramctl", "--reset", "/dev/zram1"], ignore_errors=True)

    tmpfs_content = f"""# Managed by Elite Arch Linux Configurator
[Unit]
Description=High-Performance tmpfs for {MOUNT_POINT}
Before=local-fs.target
ConditionPathExists={MOUNT_POINT}

[Mount]
What=tmpfs
Where={MOUNT_POINT}
Type=tmpfs
Options=rw,nosuid,nodev,relatime,size=100%,mode=1777,uid={TARGET_UID},gid={TARGET_GID}

[Install]
WantedBy=local-fs.target
"""
    write_file_atomic(mount_unit_path, tmpfs_content)
    ok(f"Tmpfs mount unit written atomically to {mount_unit_path}")

    info("Reloading systemd daemon...")
    run_cmd(["systemctl", "daemon-reload"])
    assert_unit_loaded(mount_unit_name)

    info("Enabling systemd mount unit...")
    run_cmd(["systemctl", "enable", "--now", mount_unit_name], ignore_errors=True)
    
    for _ in range(8):
        if get_mount_source() == "tmpfs": break
        time.sleep(0.3)
    
    if get_mount_source() != "tmpfs":
        run_cmd(["mount", "-t", "tmpfs", "-o", f"rw,nosuid,nodev,relatime,size=100%,mode=1777,uid={TARGET_UID},gid={TARGET_GID}", "tmpfs", str(MOUNT_POINT)], ignore_errors=True)

    fix_mount_permissions()
    restore_staged_files(stage_dir)
    fix_mount_permissions()

    if get_mount_source() == "tmpfs":
        ok(f"Live memory: Pure tmpfs successfully attached to {MOUNT_POINT} (Mode: 1777).")
    else:
        die(f"Failed to mount pure tmpfs. Check 'systemctl status {mount_unit_name}'.")

def configure_zram(mount_unit_name: str, mount_unit_path: Path, size_override: str = "") -> None:
    # Resolve size
    size_expr = "ram"
    if size_override:
        size_expr = parse_size_expression(size_override)
    elif ZRAM_CONF_FILE.exists():
        m = re.search(r"zram-size\s*=\s*(.+)", ZRAM_CONF_FILE.read_text())
        if m:
            size_expr = m.group(1).strip()

    info(f"Initializing Ext4 ZRAM Block Mount for: {C.BOLD}{MOUNT_POINT}{C.RST} (Size: {size_expr})")
    stage_dir = resolve_live_conflicts("zram", mount_unit_name)

    if mount_unit_path.exists():
        run_cmd(["systemctl", "disable", "--now", mount_unit_name], ignore_errors=True)
        mount_unit_path.unlink(missing_ok=True)
        run_cmd(["systemctl", "daemon-reload"])

    zram_content = f"""# Managed by Elite Arch Linux Configurator.
[zram1]
zram-size = {size_expr}
zram-resident-limit = {ZRAM_RESIDENT_LIMIT_EXPR}
fs-type = ext4
mount-point = {MOUNT_POINT}
compression-algorithm = {COMPRESSION_ALGORITHM}
options = {FS_OPTIONS}
"""

    write_file_atomic(ZRAM_CONF_FILE, zram_content)
    ok(f"ZRAM pool configuration written atomically to {ZRAM_CONF_FILE}")

    fix_mount_permissions()

    info("Reloading systemd generators...")
    run_cmd(["systemctl", "daemon-reload"])
    assert_unit_loaded("systemd-zram-setup@zram1.service")
    
    info("Engaging ZRAM generator pipeline...")
    run_cmd(["systemctl", "restart", "systemd-zram-setup@zram1.service"], ignore_errors=True)
    run_cmd(["systemctl", "restart", "mnt-zram1.mount"], ignore_errors=True)
    run_cmd(["mount", str(MOUNT_POINT)], ignore_errors=True)

    for _ in range(10):
        if get_mount_source() in ("/dev/zram1", "zram1"): break
        time.sleep(0.3)

    fix_mount_permissions()
    restore_staged_files(stage_dir)
    fix_mount_permissions()

    if get_mount_source() in ("/dev/zram1", "zram1"):
        ok(f"Live memory: Ext4 ZRAM successfully attached to {MOUNT_POINT} (Mode: 1777).")
    else:
        warn("Live ZRAM configuration staged. Systemd generator will activate on next boot.")

def configure_none(mount_unit_name: str, mount_unit_path: Path) -> None:
    info(f"Disabling secondary RAM disk for {C.BOLD}{MOUNT_POINT}{C.RST} (Minimal RAM mode)...")
    safely_unmount_and_stage("none")

    if ZRAM_CONF_FILE.exists():
        ZRAM_CONF_FILE.unlink()

    if OVERRIDE_DIR.exists():
        for child in OVERRIDE_DIR.glob("*"):
            child.unlink()
        OVERRIDE_DIR.rmdir()

    if mount_unit_path.exists():
        run_cmd(["systemctl", "disable", "--now", mount_unit_name], ignore_errors=True)
        mount_unit_path.unlink()

    run_cmd(["zramctl", "--reset", "/dev/zram1"], ignore_errors=True)
    run_cmd(["systemctl", "daemon-reload"])
    ok(f"Secondary RAM disk ({MOUNT_POINT}) disabled cleanly (zero RAM table overhead).")

def ask_backend() -> str:
    current = get_mount_source()
    mount_unit_name = run_cmd(["systemd-escape", "--path", f"--suffix=mount", str(MOUNT_POINT)], ignore_errors=True)
    mount_unit_path = Path("/etc/systemd/system") / mount_unit_name
    
    tmpfs_tag = f"{C.GRN} [LIVE & ACTIVE]{C.RST}" if current == "tmpfs" else ""
    
    if current in ("/dev/zram1", "zram1"):
        zram_tag = f"{C.GRN} [LIVE & ACTIVE]{C.RST}"
    elif ZRAM_CONF_FILE.exists() and current != "tmpfs":
        zram_tag = f"{C.YLW} [STAGED - PENDING REBOOT]{C.RST}"
    else:
        zram_tag = ""

    none_tag = f"{C.GRN} [CURRENTLY DISABLED]{C.RST}" if (not current and not ZRAM_CONF_FILE.exists() and not mount_unit_path.exists()) else ""

    print(f"\n  {C.CYN}[ Select backend for {MOUNT_POINT} ]{C.RST}")
    print(f"   {C.BOLD}1{C.RST}) tmpfs   (Pure RAM Mapping){tmpfs_tag}")
    print(f"   {C.BOLD}2{C.RST}) zram    (Ext4 Compressed Block){zram_tag}")
    print(f"   {C.BOLD}3{C.RST}) disable (No secondary RAM disk / Zero RAM overhead){none_tag}")
    while True:
        raw = input("  > ").strip().lower()
        if raw in ("1", "tmpfs"): return "tmpfs"
        if raw in ("2", "zram"): return "zram"
        if raw in ("3", "none", "disable", "disabled", "off"): return "none"
        if raw in ("q", "quit"): sys.exit(0)
        print(f"  {C.RED}Invalid choice.{C.RST} Select 1, 2, or 3.")

# --- Entry Point ---
def main() -> None:
    pre_flight_checks()

    match (args.tmpfs, args.zram, args.disable):
        case (True, False, False): backend = "tmpfs"
        case (False, True, False): backend = "zram"
        case (False, False, True): backend = "none"
        case _: backend = ask_backend()

    for cmd in ["systemctl", "systemd-escape", "findmnt", "umount"]:
        if shutil.which(cmd) is None:
            die(f"'{cmd}' is required but missing from system PATH.")

    mount_unit_name = run_cmd(["systemd-escape", "--path", f"--suffix=mount", str(MOUNT_POINT)])
    mount_unit_path = Path("/etc/systemd/system") / mount_unit_name

    if backend != "none":
        prepare_mount_directory()

    match backend:
        case "tmpfs": configure_tmpfs(mount_unit_name, mount_unit_path)
        case "zram": configure_zram(mount_unit_name, mount_unit_path, args.size)
        case "none": configure_none(mount_unit_name, mount_unit_path)
            
    ok("Subsystem configured successfully.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YLW}aborted — operation cancelled by user.{C.RST}")
        sys.exit(130)

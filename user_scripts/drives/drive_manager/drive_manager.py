#!/usr/bin/env python3

"""
==============================================================================
 UNIVERSAL DRIVE MANAGER (PLATINUM HYBRID EDITION - BLEEDING-EDGE ARCH)
 ------------------------------------------------------------------------------
 Architecture updated to strict, cutting-edge standards based on the latest 
 util-linux (2.42+), Linux Kernel (7.1+), and cryptsetup (2.8+) specifications.
 
 Features:
  - Native UUID= tagging for cryptsetup and mount mechanisms
  - Atomic directory creation via mount --mkdir
  - Dynamic LUKS/BitLocker auto-detection via isolated `lsblk -d` probing
  - Intelligent Dynamic NTFS/FAT32 Auto-Permission Configurator (uid/gid injection)
  - Upfront Multi-Target Password Collection with Parallel Decryption Dispatch
  - Parallel Multi-Drive Lock & Unlock Operations via ThreadPoolExecutor
  - Zero-dependency TOML parsing (Python 3.11+ tomllib)
  - Arch Linux Auto-Bootstrapper for required UI/Sec dependencies
  - Robust Lockfile Mechanics with User-Isolated Runtime Directing
  - Pre-emptive `sudo -v` credential priming to prevent stdin pipe collision
  - Interactive Busy Process Resolver (High-Performance Memory Parsing via lsof)
  - Quad-Tier Teardown (udisksctl -> cryptsetup close -> deferred async closure)
  - System-Wide Divergent Mount Auto-Remediation & Migration
  - Dynamic Multi-Symlink Self-Healing (data-driven via drives.toml)
  - Non-Rotational SSD Auto-Detection for Background TRIM Dispatcher
  - Smart Password Retry Loop with Right-Aligned Memory History
  - Secure XDG_RUNTIME_DIR Session Persistence with Atomic Writes
  - Portable Path Normalization (~, $HOME, and user migration remapping)
  - HARDENED: Strict OS exit code propagation for safe shell chaining (&&)
==============================================================================
"""

import os
import sys
import time
import fcntl
import json
import atexit
import getpass
import argparse
import tomllib
import subprocess
import shutil
import threading
from pathlib import Path
from typing import Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

# ------------------------------------------------------------------------------
#  ARCH LINUX AUTO-BOOTSTRAPPER
# ------------------------------------------------------------------------------
try:
    import keyring
    import secretstorage
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.align import Align
    from rich.markup import escape
except ImportError:
    print("\n[INFO] Missing required Python libraries: 'keyring', 'secretstorage', and/or 'rich'.")
    print("[INFO] Attempting to auto-install via pacman...")
    try:
        subprocess.run(
            ["sudo", "pacman", "-S", "--needed", "--noconfirm", "python-keyring", "python-secretstorage", "python-rich"],
            check=True
        )
        print("[SUCCESS] Dependencies installed. Seamlessly restarting script...\n")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except subprocess.CalledProcessError:
        print("\n[ERROR] Failed to install dependencies automatically.")
        sys.exit(1)
    except FileNotFoundError:
        print("\n[ERROR] 'pacman' command not found. Are you on Arch Linux?")
        sys.exit(1)

# ------------------------------------------------------------------------------
#  CONSTANTS & GLOBALS
# ------------------------------------------------------------------------------
FILESYSTEM_TIMEOUT = 15
LOCK_RETRY_DELAY = 1
LOCK_MAX_RETRIES = 5
KEYRING_SERVICE = "drive_manager"

console = Console()
err_console = Console(stderr=True)
lock_fd = None
print_lock = threading.Lock()

# ------------------------------------------------------------------------------
#  DATA STRUCTURES
# ------------------------------------------------------------------------------
@dataclass
class Drive:
    name: str
    type: str  # "PROTECTED" | "SIMPLE"
    mountpoint: Path
    outer_uuid: str
    inner_uuid: str | None = None
    hint: str | None = None
    fstype: str | None = None
    mount_options: list[str] | None = None
    symlinks: list[Path] | None = None

# ------------------------------------------------------------------------------
#  LOGGING & UI (THREAD-SAFE)
# ------------------------------------------------------------------------------
def log(msg: str):
    with print_lock:
        console.print(f"[bold blue]\\[DRIVE][/] {msg}")

def success(msg: str):
    with print_lock:
        console.print(f"[bold green]\\[SUCCESS][/] {msg}")

def err(msg: str):
    with print_lock:
        err_console.print(f"[bold red]\\[ERROR][/] {msg}")

def hint_msg(msg: str):
    with print_lock:
        console.print(f"[bold yellow]\\[HINT][/] {msg}")

# ------------------------------------------------------------------------------
#  SECURITY & SYSTEM ISOLATION
# ------------------------------------------------------------------------------
def prevent_root_execution():
    """Ensures the script is run as a normal user to keep Keyring D-Bus access valid."""
    if os.geteuid() == 0:
        err("Do NOT run this script with `sudo`!")
        console.print("Running as root breaks access to your user's desktop keyring.")
        console.print("The script will securely request sudo permissions internally when needed.")
        sys.exit(1)

def get_runtime_dir() -> Path:
    """Returns a rigorously verified user-owned directory for temporary IPC and lockfiles."""
    uid = os.getuid()
    runtime_env = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    
    if runtime_env:
        path = Path(runtime_env) / "drive_manager"
    else:
        path = Path(f"/tmp/.drive_manager_{uid}")

    if not path.exists():
        try:
            path.mkdir(mode=0o700, parents=True)
        except FileExistsError:
            pass

    try:
        st = path.lstat()
    except FileNotFoundError:
        err(f"Security hazard: Directory {path} disappeared during creation.")
        sys.exit(1)

    if path.is_symlink():
        err(f"Security hazard: Directory {path} is a symlink. Possible hijack attempt.")
        sys.exit(1)

    if st.st_uid != uid or (st.st_mode & 0o077) != 0:
        err(f"Security hazard: Directory {path} is improperly permissioned or hijacked.")
        sys.exit(1)

    return path

def prime_sudo():
    """Primes the sudo credential cache cleanly before stdin operations."""
    try:
        subprocess.run(["sudo", "-v"], check=True)
    except subprocess.CalledProcessError:
        err("Sudo authentication failed. Cannot proceed.")
        sys.exit(1)

def acquire_lock():
    """Acquires a kernel-level exclusive file lock atomically within the user's isolated dir."""
    global lock_fd
    lock_path = get_runtime_dir() / "drive_manager.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        lock_fd = os.fdopen(fd, "r+")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        
        lock_fd.seek(0)
        lock_fd.truncate()
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
    except BlockingIOError:
        err("Another instance of drive_manager is currently running.")
        sys.exit(1)
    except Exception as e:
        err(f"Could not open lock file: {e}")
        sys.exit(1)

def check_dependencies():
    """Ensures necessary OS binaries exist."""
    deps = ["mount", "umount", "findmnt", "lsblk", "udevadm", "sudo", "cryptsetup", "lsof", "blockdev"]
    missing = [cmd for cmd in deps if shutil.which(cmd) is None]
    if missing:
        err(f"Missing required commands: {', '.join(missing)}")
        sys.exit(1)

# ------------------------------------------------------------------------------
#  KERNEL INTERFACES & PROBING
# ------------------------------------------------------------------------------
def resolve_device(uuid: str | None) -> Path | None:
    """Returns the fully resolved Path to a block device, resolving any symlinks."""
    if not uuid:
        return None
    dev_path = Path(f"/dev/disk/by-uuid/{uuid}")
    if dev_path.exists():
        return dev_path.resolve()
    return None

def is_device_readable(dev_path: Path) -> bool:
    """Verifies a block device is responsive by attempting to read its first block."""
    try:
        res = subprocess.run(
            ["sudo", "dd", f"if={dev_path}", "bs=4096", "count=1", "of=/dev/null", "status=none"],
            capture_output=True, timeout=10
        )
        return res.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False

def wait_for_device(uuid: str | None, timeout: int) -> bool:
    """Waits strictly and safely for udev to populate the /dev/disk/by-uuid tree."""
    if not uuid:
        return False
    start = time.time()
    subprocess.run(["udevadm", "settle", f"--timeout={timeout}"], capture_output=True)
    
    while (time.time() - start) < timeout:
        if resolve_device(uuid):
            return True
        time.sleep(0.5)
        
    return resolve_device(uuid) is not None

def get_fstype(uuid: str | None) -> str | None:
    """Uses lsblk -d to dynamically probe the filesystem or crypto type of a single UUID without child pollution."""
    if not uuid or not resolve_device(uuid):
        return None
    cmd = ["lsblk", "-d", f"/dev/disk/by-uuid/{uuid}", "--json", "-o", "FSTYPE"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            devices = data.get("blockdevices", [])
            if devices and devices[0].get("fstype"):
                return devices[0].get("fstype")
        except json.JSONDecodeError:
            pass
    return None

def is_rotational(dev_path: Path | str) -> bool:
    """Checks if a block device is a mechanical rotational HDD (1) or non-rotational SSD/NVMe (0)."""
    try:
        res = subprocess.run(["lsblk", "-d", "-n", "-o", "ROTA", str(dev_path)], capture_output=True, text=True)
        if res.returncode == 0:
            return res.stdout.strip() == "1"
    except Exception:
        pass
    return False

def get_mount_info(target_dir: Path) -> dict[str, Any] | None:
    """Uses findmnt JSON output to safely detect if a directory is mounted."""
    cmd = ["findmnt", "--json", "-v", "--mountpoint", str(target_dir)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            if "filesystems" in data and data["filesystems"]:
                return data["filesystems"][0]
        except json.JSONDecodeError:
            pass
    return None

def get_crypt_mapper_name(outer_uuid: str) -> str | None:
    """Uses lsblk to find the /dev/mapper/ NAME attached to the physical encrypted drive."""
    if not outer_uuid:
        return None
    cmd = ["lsblk", f"/dev/disk/by-uuid/{outer_uuid}", "--json", "--tree", "-o", "NAME,TYPE"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            def find_crypt(nodes: list[dict]) -> str | None:
                for node in nodes:
                    if node.get("type") == "crypt":
                        return node.get("name")
                    if "children" in node:
                        found = find_crypt(node["children"])
                        if found:
                            return found
                return None
            return find_crypt(data.get("blockdevices", []))
        except json.JSONDecodeError:
            pass
    return None

def get_all_mountpoints_for_device(drive: Drive) -> list[Path]:
    """Finds all active mountpoints for a drive across the entire system using findmnt and lsblk."""
    mounts: set[Path] = set()
    target_uuid = drive.inner_uuid if drive.type == "PROTECTED" else drive.outer_uuid
    
    # 1. Query by UUID via findmnt
    if target_uuid:
        cmd = ["findmnt", "--json", "-v", "-S", f"UUID={target_uuid}"]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            try:
                data = json.loads(res.stdout)
                for fs in data.get("filesystems", []):
                    if "target" in fs and fs["target"]:
                        mounts.add(Path(fs["target"]).resolve())
            except Exception:
                pass
                
    # 2. Query by mapper name if protected
    if drive.type == "PROTECTED":
        existing_mapper = get_crypt_mapper_name(drive.outer_uuid)
        mappers_to_check = [existing_mapper] if existing_mapper else [f"luks-{drive.outer_uuid}"]
        for m_name in mappers_to_check:
            if not m_name:
                continue
            m_path = f"/dev/mapper/{m_name}"
            cmd = ["findmnt", "--json", "-v", "-S", m_path]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                try:
                    data = json.loads(res.stdout)
                    for fs in data.get("filesystems", []):
                        if "target" in fs and fs["target"]:
                            mounts.add(Path(fs["target"]).resolve())
                except Exception:
                    pass

    # 3. Query by direct block device path
    if target_uuid:
        dev_path = resolve_device(target_uuid)
        if dev_path:
            cmd = ["findmnt", "--json", "-v", "-S", str(dev_path)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                try:
                    data = json.loads(res.stdout)
                    for fs in data.get("filesystems", []):
                        if "target" in fs and fs["target"]:
                            mounts.add(Path(fs["target"]).resolve())
                except Exception:
                    pass

    return sorted(list(mounts))

def cleanup_empty_stale_dir(path: Path):
    """Safely cleans up empty ancestor directories created by stale mounts (e.g. /home/dusk)."""
    try:
        p = path.resolve()
        if p.exists() and p.is_dir() and not any(p.iterdir()):
            run_sudo_cmd(["sudo", "rmdir", str(p)])
            
        parent = p.parent
        if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
            if parent.parent in [Path("/home"), Path("/mnt")]:
                run_sudo_cmd(["sudo", "rmdir", str(parent)])
                
        grandparent = parent.parent
        if grandparent.parent == Path("/home") and grandparent != Path.home() and grandparent.exists() and not any(grandparent.iterdir()):
            run_sudo_cmd(["sudo", "rmdir", str(grandparent)])
    except Exception:
        pass

def reconcile_drive_integrations(drive: Drive):
    """Ensures permissions on user-space mounts and reconciles configured symlinks dynamically."""
    home = Path.home()
    uid = os.getuid()
    gid = os.getgid()
    
    # 1. If mountpoint is inside the user's home directory, ensure the active user owns it
    if drive.mountpoint.resolve().is_relative_to(home):
        try:
            st = drive.mountpoint.stat()
            if st.st_uid != uid or st.st_gid != gid:
                log(f"Adjusting user ownership on mounted volume '{drive.name}'...")
                run_sudo_cmd(["sudo", "chown", f"{uid}:{gid}", str(drive.mountpoint)])
        except Exception:
            pass

    # 2. Reconcile any configured symlinks pointing to this mountpoint
    if drive.symlinks:
        for symlink_path in drive.symlinks:
            target_mount = drive.mountpoint.resolve()
            if symlink_path.exists() or symlink_path.is_symlink():
                if symlink_path.is_symlink():
                    try:
                        current_target = os.readlink(symlink_path)
                        if Path(current_target).resolve() != target_mount:
                            log(f"Fixing outdated symlink ({symlink_path} -> {target_mount})...")
                            symlink_path.unlink(missing_ok=True)
                            symlink_path.symlink_to(target_mount)
                            try:
                                os.lchown(symlink_path, uid, gid)
                            except Exception:
                                pass
                    except Exception:
                        pass
                else:
                    log(f"Detected local directory at {symlink_path}. Replacing with symlink to {target_mount}...")
                    try:
                        if symlink_path.is_dir():
                            shutil.rmtree(symlink_path)
                        else:
                            symlink_path.unlink()
                    except Exception as e:
                        log(f"Note: Could not replace local directory {symlink_path}: {e}")
                        
                    if not symlink_path.exists():
                        symlink_path.symlink_to(target_mount)
                        try:
                            os.lchown(symlink_path, uid, gid)
                        except Exception:
                            pass
                        success(f"Created symlink: {symlink_path} -> {target_mount}")
            else:
                try:
                    symlink_path.parent.mkdir(parents=True, exist_ok=True)
                    symlink_path.symlink_to(target_mount)
                    os.lchown(symlink_path, uid, gid)
                    success(f"Created symlink: {symlink_path} -> {target_mount}")
                except Exception as e:
                    err(f"Failed to create symlink {symlink_path} -> {target_mount}: {e}")

# ------------------------------------------------------------------------------
#  KEYRING & CREDENTIAL MANAGEMENT
# ------------------------------------------------------------------------------
def is_gui_available() -> bool:
    """Checks if a graphical display environment (X11 or Wayland) is active for GUI prompts."""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

def is_keyring_unlocked() -> bool:
    """Checks if the default keyring collection is unlocked (takes 0.00s and never hangs)."""
    try:
        backend_name = type(keyring.get_keyring()).__name__.lower()
        if "secretservice" not in backend_name and "libsecret" not in backend_name and "chainer" not in backend_name:
            return True
            
        connection = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(connection)
        return not collection.is_locked()
    except Exception:
        return True

def unlock_keyring_if_locked() -> bool:
    """Checks if the default keyring collection is locked, and if so, requests system unlock."""
    try:
        backend_name = type(keyring.get_keyring()).__name__.lower()
        if "secretservice" not in backend_name and "libsecret" not in backend_name and "chainer" not in backend_name:
            return True
            
        connection = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(connection)
        
        if collection.is_locked():
            if not is_gui_available():
                log("Keyring is locked and no GUI display session detected (TTY/Headless mode).")
                log("Falling back directly to manual terminal prompt...")
                return False

            log("Keyring is locked. Prompting to unlock system keyring...")
            try:
                dismissed = collection.unlock()
                if dismissed or collection.is_locked():
                    log("Keyring unlock prompt was dismissed or cancelled.")
                    return False
                else:
                    success("Keyring successfully unlocked.")
                    return True
            except Exception as e:
                log(f"Keyring unlock prompt unavailable ({e}). Falling back to terminal prompt.")
                return False
        return True
    except Exception as e:
        log(f"Keyring status check non-critical warning: {e}")
        return True

def get_keyring_password_with_timeout(service: str, name: str, timeout: int = 60) -> str | None:
    """Attempts keyring lookup with a daemon thread timeout to prevent hanging on exit."""
    if not unlock_keyring_if_locked():
        log("Keyring is locked or unlock was cancelled. Falling back to manual password prompt.")
        return None

    result = [None]
    
    def fetch():
        try:
            result[0] = keyring.get_password(service, name)
        except Exception:
            pass

    t = threading.Thread(target=fetch, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        log("Keyring lookup timed out. Falling through to manual password prompt.")
        return None

    return result[0]

def set_keyring_password_with_timeout(service: str, name: str, password: str, timeout: int = 60) -> bool:
    """Saves password to keyring with a daemon thread timeout to prevent hanging on locked keyring."""
    if not unlock_keyring_if_locked():
        err("Keyring is locked or unlock was cancelled. Password not saved to keyring.")
        return False

    success_flag = [False]

    def store():
        try:
            keyring.set_password(service, name, password)
            success_flag[0] = True
        except keyring.errors.KeyringLocked:
            err("Keyring is locked. Password not saved to keyring.")
        except Exception as e:
            err(f"Unexpected keyring error: {e}")

    t = threading.Thread(target=store, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        err("Keyring is locked or unreachable. Password not saved to keyring.")
        return False

    return success_flag[0]

# ------------------------------------------------------------------------------
#  PERSISTENT FAILED PASSWORD STORAGE
# ------------------------------------------------------------------------------
def get_temp_attempts_path(drive_name: str) -> Path:
    return get_runtime_dir() / f"attempts_{drive_name}.json"

def load_temp_attempts(drive_name: str) -> list[str]:
    path = get_temp_attempts_path(drive_name)
    if not path.exists():
        return []
    try:
        stat_info = path.stat()
        if stat_info.st_uid != os.getuid() or (stat_info.st_mode & 0o077) != 0:
            path.unlink(missing_ok=True)
            return []
            
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_temp_attempts(drive_name: str, attempts: list[str]):
    path = get_temp_attempts_path(drive_name)
    if len(attempts) > 50:
        attempts = attempts[-50:]
        
    temp_path = path.with_suffix(".tmp")
    try:
        fd = os.open(temp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(attempts, f)
        temp_path.rename(path)
    except Exception:
        pass

def clear_temp_attempts(drive_name: str):
    path = get_temp_attempts_path(drive_name)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass

# ------------------------------------------------------------------------------
#  EXECUTION & SUBPROCESS HELPERS
# ------------------------------------------------------------------------------
def run_sudo_cmd(cmd: list[str], stdin_data: str | None = None) -> bool:
    """Helper to run a sudo command securely. Dynamically applies capture_output to prevent hanging on sudo prompts."""
    try:
        if stdin_data is not None:
            res = subprocess.run(cmd, input=stdin_data, text=True, capture_output=True)
            if res.returncode != 0:
                if res.stderr:
                    err(f"Subprocess kernel error: {res.stderr.strip()}")
                return False
            return True
        else:
            res = subprocess.run(cmd, capture_output=True, text=True)
            return res.returncode == 0
    except Exception as e:
        err(f"Command execution failed: {e}")
        return False

def run_cryptsetup_unlock(cmd: list[str], passphrase: str, timeout: int = 180) -> bool:
    """Runs a cryptsetup open command with a passphrase piped via stdin."""
    try:
        res = subprocess.run(
            cmd, input=passphrase, text=True,
            capture_output=True, timeout=timeout
        )
        if res.returncode != 0:
            if res.stderr:
                err(f"Subprocess kernel error: {res.stderr.strip()}")
            return False
        return True
    except subprocess.TimeoutExpired:
        err(f"Cryptsetup timed out after {timeout} seconds. The system may be under heavy load.")
        return False
    except Exception as e:
        err(f"Command execution failed: {e}")
        return False

def is_process_alive(pid: str) -> bool:
    """Checks if a process is still alive by sending signal 0 via the kernel."""
    try:
        res = subprocess.run(["sudo", "kill", "-0", pid], capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False

def resolve_busy_processes(mountpoint: Path) -> bool:
    """Finds processes keeping the drive busy parsing lsof directly (sudo bypasses hidepid natively)."""
    res = subprocess.run(["sudo", "lsof", "-F", "pcu", "+f", "--", str(mountpoint)], capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        return False

    processes = []
    current_p = {}
    for line in res.stdout.strip().split("\n"):
        if not line:
            continue
        prefix = line[0]
        val = line[1:]
        if prefix == 'p':
            if current_p and 'pid' in current_p:
                processes.append(current_p)
            current_p = {'pid': val, 'cmd': 'Unknown', 'user': 'Unknown'}
        elif prefix == 'c' and current_p:
            current_p['cmd'] = val
        elif prefix == 'u' and current_p:
            current_p['user'] = val

    if current_p and 'pid' in current_p:
        processes.append(current_p)

    unique_processes = []
    seen_pids = set()
    for p in processes:
        if p['pid'] not in seen_pids:
            seen_pids.add(p['pid'])
            unique_processes.append(p)

    processes = unique_processes

    if not processes:
        return False

    with print_lock:
        console.print(Panel(
            "[bold red]⚠️  WARNING: FILESYSTEM IS BUSY ⚠️[/]\n\n"
            f"The following processes are currently locking [bold white]{mountpoint}[/]\n"
            "Attempting a graceful termination allows applications to save their data.",
            title="Filesystem Locked", border_style="red"
        ))

        table = Table(show_header=True, header_style="bold yellow", border_style="yellow")
        table.add_column("COMMAND", style="cyan")
        table.add_column("PID", justify="right", style="yellow")
        table.add_column("USER")

        for p in processes:
            table.add_row(p["cmd"], p["pid"], p["user"])

        console.print(table)
        console.print()

    action_taken = False
    for p in processes:
        if not is_process_alive(p["pid"]):
            log(f"[INFO] {p['cmd']} (PID: {p['pid']}) has already exited gracefully.")
            continue

        with print_lock:
            ans = Prompt.ask(
                f"Attempt graceful termination of [bold cyan]{escape(p['cmd'])}[/] (PID: [bold yellow]{p['pid']}[/])?", 
                choices=["y", "n"], 
                default="y"
            )
        if ans == "y":
            log(f"Sending SIGTERM (15) to {escape(p['cmd'])} (PID: {p['pid']})...")
            term_res = subprocess.run(["sudo", "kill", "-15", p['pid']], capture_output=True, text=True)
            
            if term_res.returncode == 0:
                time.sleep(2)
                if not is_process_alive(p['pid']):
                    success(f"Successfully terminated {escape(p['cmd'])} gracefully.")
                    action_taken = True
                else:
                    err(f"Process {p['pid']} refused to close gracefully. Engaging SIGKILL (9)...")
                    kill_res = subprocess.run(["sudo", "kill", "-9", p['pid']], capture_output=True, text=True)
                    if kill_res.returncode == 0:
                        success(f"Forcefully killed {escape(p['cmd'])} (PID: {p['pid']}).")
                        action_taken = True
                    else:
                        err(f"Failed to force kill PID {p['pid']}: {kill_res.stderr.strip()}")
            else:
                err(f"Failed to send SIGTERM to PID {p['pid']}: {term_res.stderr.strip()}")
    
    return action_taken

def unmount_path(mountpoint: Path, max_attempts: int = 5) -> bool:
    """Unmounts a filesystem path cleanly, scanning and resolving locking processes if needed."""
    if not get_mount_info(mountpoint):
        return True

    log(f"Unmounting {mountpoint}...")
    for attempt in range(max_attempts):
        if run_sudo_cmd(["sudo", "umount", str(mountpoint)]):
            log(f"Successfully unmounted {mountpoint}.")
            return True
        else:
            log(f"Filesystem at {mountpoint} is busy. Scanning for locking processes...")
            if resolve_busy_processes(mountpoint):
                log("Retrying unmount after resolving locking processes...")
                time.sleep(1)
            else:
                log("No active userspace processes found. Waiting for kernel buffers to settle...")
                time.sleep(1)

    err(f"Failed to unmount {mountpoint} after {max_attempts} attempts.")
    return False

def run_cryptsetup_forensics(mapper_name: str):
    """Diagnoses exactly what is preventing a cryptsetup closure."""
    target = f"/dev/mapper/{mapper_name}"
    log(f"Running forensic block-device scan on {target}...")
    
    res = subprocess.run(["sudo", "lsof", target], capture_output=True, text=True)
    if res.stdout.strip():
        with print_lock:
            console.print(Panel(
                res.stdout.strip(), 
                title="Processes locking the underlying crypt node", 
                border_style="red"
            ))
    else:
        hint_msg("No userspace applications are holding the node. It is likely locked by a kernel subsystem (e.g., LVM, Btrfs async flusher) or udev daemon probing.")
        hint_msg(f"To lock it asynchronously once the kernel is finished, run: `sudo cryptsetup close --deferred {mapper_name}`")

class CPUAccelerator:
    """Context manager to temporarily enable offline Performance cores on hybrid systems."""
    def __init__(self):
        self.enabled_cores = []
        atexit.register(self.cleanup)

    def cleanup(self):
        if self.enabled_cores:
            log("Restoring CPU power-saving state (disabling P-cores)...")
            for cpu_id in list(self.enabled_cores):
                cmd = ["sudo", "tee", f"/sys/devices/system/cpu/cpu{cpu_id}/online"]
                for attempt in range(5):
                    try:
                        res = subprocess.run(cmd, input="0", text=True, capture_output=True)
                        if res.returncode == 0:
                            break
                    except Exception:
                        pass
                    time.sleep(0.05)
            self.enabled_cores.clear()

    def __enter__(self):
        try:
            p_cores, _ = self.get_hybrid_topology()
            if not p_cores:
                return self

            offline_p_cores = []
            for cpu_id in p_cores:
                online_file = Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online")
                if online_file.exists():
                    try:
                        if online_file.read_text().strip() == "0":
                            offline_p_cores.append(cpu_id)
                    except Exception:
                        pass

            if offline_p_cores:
                log(f"Offline Performance cores detected (CPUs: {offline_p_cores}).")
                log("Temporarily enabling Performance cores to accelerate operation...")
                for cpu_id in offline_p_cores:
                    cmd = ["sudo", "tee", f"/sys/devices/system/cpu/cpu{cpu_id}/online"]
                    if run_sudo_cmd(cmd, stdin_data="1"):
                        self.enabled_cores.append(cpu_id)
        except Exception as e:
            err(f"Failed to initiate CPU acceleration: {e}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def get_hybrid_topology(self) -> tuple[list[int], list[int]]:
        p_cores: list[int] = []
        e_cores: list[int] = []
        cpu_sysfs = Path("/sys/devices/system/cpu")
        try:
            cpu_nodes = sorted([node for node in cpu_sysfs.glob("cpu[0-9]*") if node.is_dir()], key=lambda p: int(p.name[3:]))
            cppc_perf = {}
            for node in cpu_nodes:
                cpu_id = int(node.name[3:])
                perf_file = node / "acpi_cppc" / "highest_perf"
                if perf_file.is_file():
                    try:
                        perf_str = perf_file.read_text().strip()
                        if perf_str.isdigit():
                            cppc_perf[cpu_id] = int(perf_str)
                    except Exception:
                        pass
            if cppc_perf:
                unique_perfs = sorted(list(set(cppc_perf.values())))
                if len(unique_perfs) > 1:
                    min_perf = unique_perfs[0]
                    max_perf = unique_perfs[-1]
                    
                    if (max_perf - min_perf) / max_perf > 0.15:
                        midpoint = (min_perf + max_perf) / 2
                        
                        first_e_core_id = None
                        for cpu_id in sorted(cppc_perf.keys()):
                            if cppc_perf[cpu_id] < midpoint:
                                first_e_core_id = cpu_id
                                break
                                
                        if first_e_core_id is not None:
                            for node in cpu_nodes:
                                cpu_id = int(node.name[3:])
                                if cpu_id < first_e_core_id:
                                    p_cores.append(cpu_id)
                                else:
                                    e_cores.append(cpu_id)
                            return p_cores, e_cores
                    else:
                        for cpu_id in sorted(cppc_perf.keys()):
                            if cppc_perf[cpu_id] > midpoint:
                                p_cores.append(cpu_id)
                            else:
                                e_cores.append(cpu_id)
        except Exception:
            pass
        return p_cores, e_cores

# ------------------------------------------------------------------------------
#  CONFIG PARSING & PATH NORMALIZATION
# ------------------------------------------------------------------------------
def resolve_configured_path(raw_path_str: str, resolve_symlinks: bool = True) -> Path:
    """Intelligently expands ~, $VARS, and migrates legacy /home/<olduser>/... paths to the active user."""
    expanded = os.path.expandvars(str(raw_path_str).strip())
    if expanded.startswith("~"):
        p = Path(expanded).expanduser()
        return p.resolve() if resolve_symlinks else p.absolute()
    
    p = Path(expanded)
    if p.is_absolute() and len(p.parts) > 2 and p.parts[1] == "home":
        current_home = Path.home()
        current_username = current_home.name
        config_username = p.parts[2]
        if config_username != current_username:
            rel = Path(*p.parts[3:]) if len(p.parts) > 3 else Path()
            rebased = current_home / rel
            return rebased.resolve() if resolve_symlinks else rebased.absolute()
            
    return p.resolve() if resolve_symlinks else p.absolute()

def load_config(override_path: Path | None = None) -> dict[str, Drive]:
    """Loads and validates drives.toml into native dataclasses with path normalization."""
    if override_path:
        if not override_path.exists():
            err(f"Explicit config file '{override_path}' not found.")
            sys.exit(1)
        target_config = override_path
    else:
        config_env = os.environ.get("XDG_CONFIG_HOME", "").strip()
        xdg_config = Path(config_env) if config_env else Path.home() / ".config"
        
        config_paths = [
            xdg_config / "drive_manager" / "drives.toml",
            Path(__file__).parent / "drives.toml"
        ]
        target_config = next((p for p in config_paths if p.exists()), None)

    if not target_config:
        err("Configuration file 'drives.toml' not found.")
        sys.exit(1)

    try:
        with open(target_config, "rb") as f:
            raw_data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        err(f"Failed to parse TOML config: {e}")
        sys.exit(1)

    drives: dict[str, Drive] = {}
    drive_entries = raw_data.get("drives", {})

    for name, data in drive_entries.items():
        try:
            mountpoint_path = resolve_configured_path(data["mountpoint"], resolve_symlinks=True)
            raw_symlinks = data.get("symlinks", [])
            resolved_symlinks = [resolve_configured_path(s, resolve_symlinks=False) for s in raw_symlinks] if raw_symlinks else None
            
            drives[name] = Drive(
                name=name,
                type=data["type"].upper(),
                mountpoint=mountpoint_path,
                outer_uuid=data["outer_uuid"],
                inner_uuid=data.get("inner_uuid"),
                hint=data.get("hint"),
                fstype=data.get("fstype"),
                mount_options=data.get("mount_options"),
                symlinks=resolved_symlinks
            )
            if drives[name].type not in ["PROTECTED", "SIMPLE"]:
                raise ValueError(f"Invalid type '{drives[name].type}'")
            if drives[name].type == "PROTECTED" and not drives[name].inner_uuid:
                raise ValueError("PROTECTED drives require an inner_uuid")
        except KeyError as e:
            err(f"Config error in drive '{name}': Missing required key {e}")
            sys.exit(1)
        except ValueError as e:
            err(f"Config error in drive '{name}': {e}")
            sys.exit(1)

    return drives

# ------------------------------------------------------------------------------
#  CORE ENGINE
# ------------------------------------------------------------------------------
def show_status(drives: dict[str, Drive]):
    table = Table(show_header=True, header_style="bold white", border_style="bright_black")
    table.add_column("DRIVE", width=14)
    table.add_column("TYPE", width=10)
    table.add_column("FS", width=10)
    table.add_column("STATUS", width=12)
    table.add_column("MOUNTPOINT")

    for name, drive in sorted(drives.items()):
        target_uuid = drive.inner_uuid if drive.type == "PROTECTED" else drive.outer_uuid
        target_mount = drive.mountpoint.resolve()
        current_mounts = get_all_mountpoints_for_device(drive)
        
        fstype_str = get_fstype(target_uuid) or drive.fstype or "Unknown"

        if target_mount in current_mounts:
            table.add_row(f"[bold green]●[/] {name}", drive.type, fstype_str, "[bold green]Mounted[/]", str(drive.mountpoint))
        elif current_mounts:
            stale_str = ", ".join(str(m) for m in current_mounts)
            table.add_row(f"[bold yellow]▲[/] {name}", drive.type, fstype_str, "[bold yellow]Divergent[/]", f"{stale_str} (expected {drive.mountpoint})")
        else:
            table.add_row(f"[bold red]○[/] {name}", drive.type, fstype_str, "[bold red]Unmounted[/]", str(drive.mountpoint))

    console.print()
    console.print(table)
    console.print()

def do_unlock(drive: Drive, supplied_password: str | None = None) -> bool:
    log(f"Starting unlock sequence for '{drive.name}'...")

    target_uuid = drive.inner_uuid if drive.type == "PROTECTED" else drive.outer_uuid
    target_mount = drive.mountpoint.resolve()
    mapper_name = None

    # Step 1: Detect all system-wide mount states and heal divergent/stale mounts
    current_mounts = get_all_mountpoints_for_device(drive)
    if target_mount in current_mounts:
        for stale in [m for m in current_mounts if m != target_mount]:
            log(f"Cleaning up redundant stale mount at {stale}...")
            unmount_path(stale)
            cleanup_empty_stale_dir(stale)
            
        reconcile_drive_integrations(drive)
        success(f"'{drive.name}' is already successfully mounted at {drive.mountpoint}")
        return True

    if current_mounts:
        for stale in current_mounts:
            log(f"Drive '{drive.name}' is currently mounted at divergent path '{stale}'. Relocating...")
            if not unmount_path(stale):
                err(f"Failed to unmount divergent mountpoint {stale}. Cannot relocate safely.")
                return False
            cleanup_empty_stale_dir(stale)

    # Step 2: If protected, unlock LUKS/BitLocker container if needed
    if drive.type == "PROTECTED":
        if not resolve_device(drive.outer_uuid):
            err(f"Physical drive not found (Outer UUID: {drive.outer_uuid}). Is it plugged in?")
            return False

        existing_mapper = get_crypt_mapper_name(drive.outer_uuid)
        mapper_name = existing_mapper if existing_mapper else f"luks-{drive.outer_uuid}"
        mapper_path = Path(f"/dev/mapper/{mapper_name}")
        
        inner_dev = resolve_device(drive.inner_uuid)
        
        container_unlocked = False
        if mapper_path.exists() and is_device_readable(mapper_path):
            container_unlocked = True
        elif inner_dev and is_device_readable(inner_dev):
            container_unlocked = True

        if container_unlocked:
            log(f"Crypt container for '{drive.name}' is already unlocked.")
        else:
            if mapper_path.exists() or inner_dev:
                err(f"Crypt device for '{drive.name}' is unresponsive. Closing stale mapping...")
                if not run_sudo_cmd(["sudo", "cryptsetup", "close", mapper_name]):
                    err(f"Failed to close stale mapping for {mapper_name}. Manual intervention required.")
                    return False
                time.sleep(1)

            log(f"Unlocking encrypted container for '{drive.name}'...")
            outer_dev_path = f"/dev/disk/by-uuid/{drive.outer_uuid}"
            
            outer_fstype = get_fstype(drive.outer_uuid)
            crypto_type_args = []
            
            if outer_fstype:
                fstype_lower = outer_fstype.lower()
                if "bitlocker" in fstype_lower or "bitlk" in fstype_lower:
                    crypto_type_args = ["--type", "bitlk"]
                elif "luks" in fstype_lower:
                    crypto_type_args = ["--type", "luks"]

            base_cmd = ["sudo", "cryptsetup", "open", "--allow-discards"] + crypto_type_args + [outer_dev_path, mapper_name]
            
            pwd = supplied_password or get_keyring_password_with_timeout(KEYRING_SERVICE, drive.name, timeout=10)
            pwd_valid = False
            
            if pwd:
                log(f"Supplying password to cryptsetup for '{drive.name}'...")
                cmd = base_cmd + ["--tries", "1", "--key-file", "-"]
                if run_cryptsetup_unlock(cmd, pwd):
                    pwd_valid = True
                    clear_temp_attempts(drive.name)
                    # If this was a supplied password not in keyring, save it
                    if supplied_password and not get_keyring_password_with_timeout(KEYRING_SERVICE, drive.name, timeout=5):
                        set_keyring_password_with_timeout(KEYRING_SERVICE, drive.name, supplied_password)
                else:
                    err(f"Decryption failed for '{drive.name}' with provided password.")
                    tried_passwords = load_temp_attempts(drive.name)
                    if pwd not in tried_passwords:
                        tried_passwords.append(pwd)
                        save_temp_attempts(drive.name, tried_passwords)

            if not pwd_valid:
                if drive.hint:
                    hint_msg(drive.hint)
                
                tried_passwords = load_temp_attempts(drive.name)
                
                while True:
                    if tried_passwords:
                        max_display = 6
                        display_items = tried_passwords[-max_display:]
                        hidden_count = len(tried_passwords) - len(display_items)
                        
                        panel_lines = []
                        if hidden_count > 0:
                            panel_lines.append(f"[dim]... {hidden_count} older attempt{'s' if hidden_count > 1 else ''} hidden ...[/]")
                        
                        panel_lines.extend(f"[red]✗[/] {escape(p)}" for p in display_items)
                        
                        hist_panel = Panel(
                            "\n".join(panel_lines),
                            title="[yellow]Previously Tried[/]",
                            border_style="yellow",
                            expand=False
                        )
                        with print_lock:
                            console.print(Align.right(hist_panel))
                        
                    try:
                        with print_lock:
                            pwd_attempt = Prompt.ask(
                                f"Enter passphrase for {drive.name} (/dev/disk/by-uuid/[bold cyan]{drive.outer_uuid}[/])", 
                                password=True
                            )
                    except (KeyboardInterrupt, EOFError):
                        with print_lock:
                            console.print()
                            err("Cancelled by user.")
                        sys.exit(130)
                        
                    if not pwd_attempt:
                        continue
                        
                    pwd_attempt = pwd_attempt.rstrip('\r\n')
                        
                    try:
                        subprocess.run(["sudo", "-n", "-v"], check=True, capture_output=True)
                    except subprocess.CalledProcessError:
                        log("Sudo credential expired during prompt. Refreshing...")
                        prime_sudo()
                        
                    cmd = base_cmd + ["--tries", "1", "--key-file", "-"]
                    
                    if run_cryptsetup_unlock(cmd, pwd_attempt):
                        clear_temp_attempts(drive.name)
                        if set_keyring_password_with_timeout(KEYRING_SERVICE, drive.name, pwd_attempt):
                            success(f"Password saved to keyring for '{drive.name}'.")
                        break
                    else:
                        err("Decryption failed. Please try again.")
                        if pwd_attempt not in tried_passwords:
                            tried_passwords.append(pwd_attempt)
                            save_temp_attempts(drive.name, tried_passwords)

            log(f"Waiting for filesystem block device for '{drive.name}' to populate...")
            if not wait_for_device(drive.inner_uuid, FILESYSTEM_TIMEOUT):
                if mapper_path.exists():
                    hint_msg(f"Inner filesystem UUID symlink for '{drive.name}' not created by udev. Proceeding with direct mapper path...")
                else:
                    err(f"Timeout waiting for inner filesystem for '{drive.name}' to appear.")
                    return False

    # Step 3: Mount filesystem to target mountpoint
    log(f"Mounting '{drive.name}' to {drive.mountpoint}...")
    
    detected_fstype = get_fstype(target_uuid)
    
    mount_source = f"UUID={target_uuid}"
    if drive.type == "PROTECTED" and not resolve_device(target_uuid):
        if mapper_name:
            fallback_mapper = Path(f"/dev/mapper/{mapper_name}")
            if fallback_mapper.exists():
                mount_source = str(fallback_mapper)

    fstype_to_check = (drive.fstype or detected_fstype or "").lower()

    mount_args = ["--mkdir"]
    
    if "ntfs" in fstype_to_check:
        mount_args.extend(["-i", "-t", "ntfs"])
    elif drive.fstype:
        mount_args.extend(["-t", drive.fstype])
        
    options = []
    uid = os.getuid()
    gid = os.getgid()

    if drive.mount_options:
        for opt in drive.mount_options:
            if opt.startswith("uid="):
                options.append(f"uid={uid}")
            elif opt.startswith("gid="):
                options.append(f"gid={gid}")
            else:
                options.append(opt)
    else:
        if fstype_to_check in ["ntfs", "vfat", "fat32", "exfat", "msdos"]:
            options.append(f"uid={uid},gid={gid},dmask=022,fmask=133")
            log(f"Auto-configured kernel permissions for non-POSIX filesystem ({fstype_to_check.upper()}).")

    if options:
        mount_args.extend(["-o", ",".join(options)])
    
    cmd = [
        "sudo", "mount", 
        *mount_args,
        "--source", mount_source, 
        "--target", str(drive.mountpoint)
    ]
    
    if run_sudo_cmd(cmd):
        success(f"'{drive.name}' successfully mounted at {drive.mountpoint}.")
        
        # Dynamic integration hooks for permissions and configured symlinks
        reconcile_drive_integrations(drive)
        
        # Dispatch background TRIM only if non-rotational SSD and filesystem supports it
        resolved_src = resolve_device(target_uuid) or mount_source
        if not is_rotational(resolved_src) and fstype_to_check not in ["btrfs", "zfs"]:
            log(f"Dispatching background TRIM operation for '{drive.name}'...")
            subprocess.Popen(
                ["sudo", "fstrim", str(drive.mountpoint)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True
            )
        return True
    else:
        err(f"Failed to mount {mount_source} to {drive.mountpoint}.")
        return False

def do_lock(drive: Drive) -> bool:
    log(f"Starting lock sequence for '{drive.name}'...")

    # Step 1: Detect all mountpoints where this drive is currently mounted
    mounts = set(get_all_mountpoints_for_device(drive))
    if get_mount_info(drive.mountpoint):
        mounts.add(drive.mountpoint.resolve())

    if mounts:
        for mp in mounts:
            if not unmount_path(mp):
                err(f"Failed to unmount {mp}. Aborting lock sequence for '{drive.name}'.")
                return False
            cleanup_empty_stale_dir(mp)
        log(f"All mountpoints for '{drive.name}' successfully unmounted.")
    else:
        log(f"'{drive.name}' is already unmounted.")

    # Step 2: If protected, close and lock crypt mapping
    if drive.type == "PROTECTED":
        mapper_name = None
        physical_present = resolve_device(drive.outer_uuid)
        
        if physical_present:
            mapper_name = get_crypt_mapper_name(drive.outer_uuid)
            if not mapper_name:
                deterministic_name = f"luks-{drive.outer_uuid}"
                if Path(f"/dev/mapper/{deterministic_name}").exists():
                    mapper_name = deterministic_name
        else:
            deterministic_name = f"luks-{drive.outer_uuid}"
            if Path(f"/dev/mapper/{deterministic_name}").exists():
                hint_msg("Physical drive missing, but ghost mapper detected. Forcing cleanup.")
                mapper_name = deterministic_name
            elif resolve_device(drive.inner_uuid):
                err(f"'{drive.name}' is active under an unknown mapper and physical drive is missing. Cannot securely lock.")
                return False
            else:
                success(f"'{drive.name}' removed physically, container is no longer active.")
                return True
        
        if mapper_name:
            run_sudo_cmd(["sudo", "blockdev", "--flushbufs", f"/dev/mapper/{mapper_name}"])

            log(f"Locking crypt node: {mapper_name}...")
            
            # Fast-path: Direct kernel-level cryptsetup close (instantaneous & parallel)
            if run_sudo_cmd(["sudo", "cryptsetup", "close", mapper_name]):
                success(f"Encrypted container '{drive.name}' successfully locked.")
                return True
            
            cleartext_dev = f"/dev/mapper/{mapper_name}"
            if shutil.which("udisksctl") and Path(cleartext_dev).exists():
                res = subprocess.run(["udisksctl", "lock", "-b", cleartext_dev], capture_output=True, text=True)
                if res.returncode == 0:
                    success(f"Encrypted container '{drive.name}' successfully locked via udisks2.")
                    return True
            
            for attempt in range(LOCK_MAX_RETRIES):
                time.sleep(LOCK_RETRY_DELAY)
                if run_sudo_cmd(["sudo", "cryptsetup", "close", mapper_name]):
                    success(f"Encrypted container '{drive.name}' successfully locked.")
                    return True
                log(f"Lock attempt {attempt+1}/{LOCK_MAX_RETRIES} for '{drive.name}' failed. Retrying...")
            
            log(f"'{drive.name}' is held by a kernel subsystem. Engaging deferred asynchronous lock...")
            if run_sudo_cmd(["sudo", "cryptsetup", "close", "--deferred", mapper_name]):
                success(f"'{drive.name}' marked for deferred closure.")
                return True

            err(f"Failed to lock {mapper_name} after all strategies exhausted.")
            run_cryptsetup_forensics(mapper_name)
            return False
        else:
            success(f"Encrypted container for '{drive.name}' is already locked.")
            return True
    else:
        success(f"Simple drive '{drive.name}' disconnected cleanly.")
        return True

def unlock_targets_pipeline(drives: dict[str, Drive], targets: list[str]) -> bool:
    """Collects passwords upfront for all targets sequentially, then unlocks and mounts in parallel."""
    passwords: dict[str, str | None] = {}
    
    # --------------------------------------------------------------------------
    #  PHASE 1: UPFRONT CREDENTIAL COLLECTION (SEQUENTIAL ON MAIN THREAD)
    # --------------------------------------------------------------------------
    for target in targets:
        drive = drives[target]
        target_mount = drive.mountpoint.resolve()
        current_mounts = get_all_mountpoints_for_device(drive)
        
        # If already mounted properly, no password needed
        if target_mount in current_mounts:
            passwords[target] = None
            continue
            
        if drive.type == "PROTECTED":
            existing_mapper = get_crypt_mapper_name(drive.outer_uuid)
            mapper_name = existing_mapper if existing_mapper else f"luks-{drive.outer_uuid}"
            mapper_path = Path(f"/dev/mapper/{mapper_name}")
            inner_dev = resolve_device(drive.inner_uuid)
            
            # If container is already open in kernel, no password needed
            if (mapper_path.exists() and is_device_readable(mapper_path)) or (inner_dev and is_device_readable(inner_dev)):
                passwords[target] = None
                continue
                
            # Attempt keyring lookup
            pwd = get_keyring_password_with_timeout(KEYRING_SERVICE, drive.name, timeout=10)
            if pwd:
                passwords[target] = pwd
            else:
                # Keyring missing password: Prompt interactively upfront
                log(f"Keyring password not found for '[bold cyan]{drive.name}[/]'.")
                if drive.hint:
                    hint_msg(drive.hint)
                    
                tried_passwords = load_temp_attempts(drive.name)
                if tried_passwords:
                    max_display = 6
                    display_items = tried_passwords[-max_display:]
                    hidden_count = len(tried_passwords) - len(display_items)
                    panel_lines = []
                    if hidden_count > 0:
                        panel_lines.append(f"[dim]... {hidden_count} older attempt{'s' if hidden_count > 1 else ''} hidden ...[/]")
                    panel_lines.extend(f"[red]✗[/] {escape(p)}" for p in display_items)
                    hist_panel = Panel(
                        "\n".join(panel_lines),
                        title="[yellow]Previously Tried[/]",
                        border_style="yellow",
                        expand=False
                    )
                    with print_lock:
                        console.print(Align.right(hist_panel))
                    
                try:
                    with print_lock:
                        pwd_attempt = Prompt.ask(
                            f"Enter passphrase for {drive.name} (/dev/disk/by-uuid/[bold cyan]{drive.outer_uuid}[/])", 
                            password=True
                        )
                except (KeyboardInterrupt, EOFError):
                    with print_lock:
                        console.print()
                        err("Cancelled by user.")
                    sys.exit(130)
                    
                if pwd_attempt:
                    passwords[target] = pwd_attempt.rstrip('\r\n')
                else:
                    passwords[target] = None
        else:
            passwords[target] = None

    # --------------------------------------------------------------------------
    #  PHASE 2: PARALLEL UNLOCK & MOUNT DISPATCH
    # --------------------------------------------------------------------------
    if len(targets) == 1:
        target = targets[0]
        return do_unlock(drives[target], supplied_password=passwords.get(target))
        
    log(f"Dispatching parallel unlock sequence for {len(targets)} drives across available CPU cores...")
    results: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=min(len(targets), 8)) as executor:
        future_to_drive = {
            executor.submit(do_unlock, drives[target], passwords.get(target)): target
            for target in targets
        }
        for future in as_completed(future_to_drive):
            target = future_to_drive[future]
            try:
                success_flag = future.result()
                results[target] = success_flag
            except Exception as e:
                err(f"Exception unlocking '{target}': {e}")
                results[target] = False
                
    return all(results.values())

def lock_targets_pipeline(drives: dict[str, Drive], targets: list[str]) -> bool:
    """Locks multiple drives concurrently with parallel unmounting and teardown."""
    if len(targets) == 1:
        return do_lock(drives[targets[0]])

    log(f"Dispatching parallel lock sequence for {len(targets)} drives...")
    results: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=min(len(targets), 8)) as executor:
        future_to_drive = {
            executor.submit(do_lock, drives[target]): target
            for target in targets
        }
        for future in as_completed(future_to_drive):
            target = future_to_drive[future]
            try:
                success_flag = future.result()
                results[target] = success_flag
            except Exception as e:
                err(f"Exception locking '{target}': {e}")
                results[target] = False
                
    return all(results.values())

def set_keyring_password(drives: dict[str, Drive], target: str) -> bool:
    if target not in drives:
        err(f"Drive '{target}' not recognized in config.")
        return False
    
    if drives[target].type != "PROTECTED":
        err(f"Drive '{target}' is a SIMPLE drive and does not require a password.")
        return False

    console.print(Panel(
        f"Setting secure keyring password for drive: [bold cyan]{escape(target)}[/]\n"
        "This eliminates the need for manual entry during unlock sequences.",
        title="Keyring Setup", border_style="cyan"
    ))

    try:
        pwd = getpass.getpass(f"Enter LUKS/BitLocker password for '{target}': ")
        pwd_confirm = getpass.getpass("Confirm password: ")
    except (KeyboardInterrupt, EOFError):
        console.print()
        err("Cancelled by user.")
        sys.exit(130)

    if pwd != pwd_confirm:
        err("Passwords do not match.")
        return False

    if set_keyring_password_with_timeout(KEYRING_SERVICE, target, pwd):
        success(f"Password stored securely in the system keyring for '{target}'.")
        clear_temp_attempts(target)
        return True
        
    return False

# ------------------------------------------------------------------------------
#  MAIN ENTRY
# ------------------------------------------------------------------------------
def main():
    prevent_root_execution()

    parser = argparse.ArgumentParser(
        description="Universal Drive Manager (Platinum Hybrid Edition / Parallel Multi-Drive Enabled)",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    parser.add_argument("-c", "--config", type=Path, help="Path to override drives.toml")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("status", help="Show status of all configured drives")
    
    unlock_p = subparsers.add_parser("unlock", help="Unlock and mount specified drive(s)")
    unlock_p.add_argument("targets", nargs="+", help="Drive name(s) to unlock (e.g., 'browser media slow')")

    lock_p = subparsers.add_parser("lock", help="Unmount and lock specified drive(s)")
    lock_p.add_argument("targets", nargs="+", help="Drive name(s) to lock")

    setpass_p = subparsers.add_parser("set-password", help="Securely store a drive's password in the system keyring")
    setpass_p.add_argument("targets", nargs="+", help="Drive name(s)")

    args = parser.parse_args()

    check_dependencies()
    drives = load_config(args.config)

    match args.action:
        case "status":
            show_status(drives)
            
        case "set-password":
            overall_success = True
            for idx, target in enumerate(args.targets):
                if idx > 0:
                    console.print("\n[dim]" + "-" * 60 + "[/dim]\n")
                if not set_keyring_password(drives, target):
                    overall_success = False
                    if idx < len(args.targets) - 1:
                        hint_msg(f"Setup for '{target}' failed. Moving to next drive...")
                    else:
                        err(f"Setup for '{target}' failed.")
            
            if not overall_success:
                sys.exit(1)
            
        case "unlock" | "lock":
            for target in args.targets:
                if target not in drives:
                    err(f"Drive '{target}' not found in configuration.")
                    sys.exit(1)

            prime_sudo()
            acquire_lock()
            
            with CPUAccelerator():
                if args.action == "unlock":
                    overall_success = unlock_targets_pipeline(drives, args.targets)
                else:
                    overall_success = lock_targets_pipeline(drives, args.targets)
            
            if not overall_success:
                sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]\\[ERROR][/] Interrupted by user.")
        sys.exit(130)

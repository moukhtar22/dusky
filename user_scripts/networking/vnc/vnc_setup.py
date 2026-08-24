#!/usr/bin/env python3
"""
===============================================================================
 Title:        Arch Linux Remote Control & Screen Link for iOS (v7.0 Golden)
 Target:       Arch Linux (Wayland / Hyprland) — any modern iOS (VNC). Generic Linux client.
 Python:       Python 3.10 - 3.14+ (Modern Syntax & Strict Audit Compliance)
 -------------------------------------------------------------------------------
 Audit & Safety Guarantees:
  1. Environment Preservation: Preserves WAYLAND_DISPLAY, HYPRLAND_INSTANCE_SIGNATURE,
     and XDG_RUNTIME_DIR across sudo privilege escalation.
  2. Dual Execution Contexts: System-level root actions (pacman, udev, firewall)
     run as root; Wayland/IPC actions (hyprctl, wayvnc, sunshine) run in the real
     unprivileged user session (SUDO_USER).
  3. Immediate uinput Permissions: Applies POSIX ACLs (`setfacl -m u:$USER:rw /dev/uinput`)
     and boot module persistence (/etc/modules-load.d/uinput.conf) so current
     sessions work immediately without re-login.
  4. Sunshine Configuration: Auto-configures output_name = HEADLESS-1 in sunshine.conf,
     syncs D-Bus activation environment, and manages user units safely.
  5. WayVNC Audited Pipeline: Auto-generates 4096-bit RSA TLS certs, configures
     /etc/pam.d/wayvnc, cleans stale sockets (/run/user/UID/wayvncctl), and validates
     IPs using ipaddress.ip_address with 0.0.0.0 fallback.
  6. USB Tethering & Pairing: Validates idevicepair trust records, detects local
     port conflicts before launching iproxy (skips busy ports), and relies on
     NetworkManager (or the distro's DHCP client) to bring up ipheth/USB tether links.
  7. Idempotent Firewalls: Handles UFW, Firewalld, nftables, and iptables idempotently
     without throwing duplicate rule or missing table/chain errors.
  8. Unbreakable Cleanup: Signal handlers (SIGINT/SIGTERM), atexit hooks, and try/finally
     blocks guarantee background processes and virtual displays are destroyed on exit.
===============================================================================
"""

import os
import sys
import pwd
import json
import shlex
import shutil
import signal
import atexit
import time
import ipaddress
import socket
import subprocess
import contextlib
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable

# --- 1. Early Privilege Escalation & Environment Preservation ---
def bootstrap_environment() -> None:
    """Ensures root privileges while preserving critical Wayland & Hyprland environment variables."""
    # Client-only mode does not require root privileges for pacman/udev system setup
    if "--client" in sys.argv:
        return

    if os.geteuid() != 0:
        print("[\033[1;33m*\033[0m] Escalating privileges via sudo for Arch Linux system-level setup...", flush=True)
        preserve_vars = "WAYLAND_DISPLAY,HYPRLAND_INSTANCE_SIGNATURE,XDG_RUNTIME_DIR,XDG_CURRENT_DESKTOP,XDG_SESSION_TYPE"
        try:
            os.execvp("sudo", ["sudo", f"--preserve-env={preserve_vars}", sys.executable] + sys.argv)
        except Exception as e:
            print(f"[\033[1;31m!\033[0m] FATAL: Privilege escalation failed: {e}", flush=True)
            sys.exit(1)

    # Auto-install bootstrap dependencies if missing
    required_pkgs = ["python-rich", "qrencode", "usbmuxd", "libimobiledevice", "iproute2", "gawk", "openssl"]
    missing_pkgs: list[str] = []

    try:
        import rich
    except ImportError:
        missing_pkgs.append("python-rich")

    for pkg in required_pkgs:
        if pkg != "python-rich":
            res = subprocess.run(["pacman", "-Qq", pkg], capture_output=True, text=True)
            if res.returncode != 0:
                missing_pkgs.append(pkg)

    if missing_pkgs:
        print(f"[\033[1;36m*\033[0m] Auto-installing required packages via pacman: {', '.join(missing_pkgs)}...", flush=True)
        if Path("/var/lib/pacman/db.lck").exists():
            print("[\033[1;31m!\033[0m] FATAL: Pacman database locked (/var/lib/pacman/db.lck). Please release pacman first.", flush=True)
            sys.exit(1)
        try:
            subprocess.run(["pacman", "-S", "--needed", "--noconfirm"] + missing_pkgs, check=True)
        except subprocess.CalledProcessError:
            # Fresh installs may have a stale sync DB: refresh mirrors, then retry once.
            print("[\033[1;36m*\033[0m] First pass failed; syncing pacman databases and retrying...", flush=True)
            try:
                subprocess.run(["pacman", "-Sy", "--noconfirm"], check=True)
                subprocess.run(["pacman", "-S", "--needed", "--noconfirm"] + missing_pkgs, check=True)
            except subprocess.CalledProcessError as e:
                print(f"[\033[1;31m!\033[0m] FATAL: Failed to install packages: {e}", flush=True)
                sys.exit(1)
        print("[\033[1;32m✔\033[0m] Dependencies installed. Reloading runtime environment...", flush=True)
        os.execvp(sys.executable, [sys.executable] + sys.argv)

bootstrap_environment()

# --- Rich UI Imports ---
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.text import Text
from rich.align import Align

console = Console()

# --- 2. User Context & Environment Resolution Engine ---
@dataclass
class UserContext:
    username: str
    uid: int
    gid: int
    home: Path
    xdg_runtime_dir: Path
    wayland_display: str | None
    hyprland_signature: str | None

    def get_user_env(self) -> dict[str, str]:
        """Constructs an isolated, complete environment dictionary for unprivileged user execution."""
        env = os.environ.copy()
        env["USER"] = self.username
        env["LOGNAME"] = self.username
        env["HOME"] = str(self.home)
        env["XDG_RUNTIME_DIR"] = str(self.xdg_runtime_dir)
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={self.xdg_runtime_dir}/bus"

        if self.wayland_display:
            env["WAYLAND_DISPLAY"] = self.wayland_display
        if self.hyprland_signature:
            env["HYPRLAND_INSTANCE_SIGNATURE"] = self.hyprland_signature

        return env

    def demote_fn(self) -> Callable[[], None]:
        """Subprocess preexec_fn to drop root privileges to user UID/GID while inheriting supplementary groups."""
        uid, gid, username = self.uid, self.gid, self.username
        def _demote() -> None:
            if username != "root":
                with contextlib.suppress(Exception):
                    os.initgroups(username, gid)
            os.setgid(gid)
            os.setuid(uid)
        return _demote

class UserResolver:
    @staticmethod
    def resolve() -> UserContext:
        username = os.environ.get("SUDO_USER")
        if not username or username == "root":
            if shutil.which("loginctl"):
                with contextlib.suppress(Exception):
                    res = subprocess.run(["loginctl", "list-sessions", "--json=short"], capture_output=True, text=True, timeout=10)
                    if res.returncode == 0:
                        sessions = json.loads(res.stdout)
                        for s in sessions:
                            if s.get("class") != "user":
                                continue
                            if isinstance(s.get("uid"), int) and s["uid"] >= 1000 and s.get("user") != "root":
                                username = s["user"]
                                break

        if not username or username == "root":
            username = "root"

        try:
            pw = pwd.getpwnam(username)
            uid = pw.pw_uid
            gid = pw.pw_gid
            home = Path(pw.pw_dir)
        except KeyError:
            uid = os.getuid()
            gid = os.getgid()
            home = Path.home()

        xdg_runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}"))
        wayland_disp = os.environ.get("WAYLAND_DISPLAY")
        hypr_sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")

        # Fallback inspection of runtime directory if variables were omitted
        if not wayland_disp and xdg_runtime.exists():
            wayland_sockets = sorted(
                [p for p in xdg_runtime.glob("wayland-*") if not p.name.endswith(".lock")],
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            if wayland_sockets:
                wayland_disp = wayland_sockets[0].name

        if not hypr_sig:
            for hypr_dir in (xdg_runtime / "hypr", Path("/tmp/hypr")):
                if hypr_dir.exists():
                    with contextlib.suppress(Exception):
                        sigs = [p.name for p in hypr_dir.iterdir() if p.is_dir()]
                        if sigs:
                            hypr_sig = sigs[0]
                            break

        return UserContext(
            username=username,
            uid=uid,
            gid=gid,
            home=home,
            xdg_runtime_dir=xdg_runtime,
            wayland_display=wayland_disp,
            hyprland_signature=hypr_sig
        )

user_ctx = UserResolver.resolve()

# --- 3. Data Models ---
@dataclass
class NetworkInterface:
    name: str
    ip: str
    if_type: str  # 'usb', 'tailscale', 'lan'

@dataclass
class ServiceState:
    name: str
    active: bool
    enabled: bool

# --- 4. Subprocess & Resource Management Engine ---
class CommandRunner:
    @staticmethod
    def run(
        cmd: str | list[str],
        user_context: UserContext | None = None,
        check: bool = False,
        timeout: int | None = 15,
        as_user: bool = False
    ) -> subprocess.CompletedProcess[str]:
        if isinstance(cmd, str):
            args = shlex.split(cmd)
        else:
            args = list(cmd)

        ctx = user_context or user_ctx
        env = ctx.get_user_env()

        if as_user and ctx.username != "root":
            args = ["sudo", "-u", ctx.username, "-E"] + args

        try:
            if timeout is None:
                return subprocess.run(args, capture_output=True, text=True, check=check, env=env)
            return subprocess.run(args, capture_output=True, text=True, check=check, timeout=timeout, env=env)
        except subprocess.CalledProcessError as e:
            if check:
                raise e
            return subprocess.CompletedProcess(args, e.returncode, stdout=e.stdout or "", stderr=e.stderr or "")
        except FileNotFoundError:
            return subprocess.CompletedProcess(args, 127, stdout="", stderr=f"Binary not found: {args[0]}")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args, 124, stdout="", stderr=f"Command timed out after {timeout}s")

class ResourceManager:
    _managed_processes: list[subprocess.Popen[Any]] = []
    _managed_headless_displays: list[str] = []

    @classmethod
    def register_process(cls, proc: subprocess.Popen[Any]) -> None:
        if proc not in cls._managed_processes:
            cls._managed_processes.append(proc)

    @classmethod
    def unregister_process(cls, proc: subprocess.Popen[Any]) -> None:
        if proc in cls._managed_processes:
            cls._managed_processes.remove(proc)

    @classmethod
    def register_headless_display(cls, name: str) -> None:
        if name not in cls._managed_headless_displays:
            cls._managed_headless_displays.append(name)

    @classmethod
    def unregister_headless_display(cls, name: str) -> None:
        if name in cls._managed_headless_displays:
            cls._managed_headless_displays.remove(name)

    @classmethod
    def cleanup_all(cls) -> None:
        """Guaranteed destruction of processes and virtual monitors."""
        for proc in list(cls._managed_processes):
            if proc.poll() is None:
                with contextlib.suppress(Exception):
                    proc.terminate()
                    proc.wait(timeout=2)
            if proc.poll() is None:
                with contextlib.suppress(Exception):
                    proc.kill()
        cls._managed_processes.clear()

        for display in list(cls._managed_headless_displays):
            with contextlib.suppress(Exception):
                CommandRunner.run(f"hyprctl output remove {shlex.quote(display)}", user_context=user_ctx, as_user=True)
        cls._managed_headless_displays.clear()

def _signal_handler(signum: int, frame: Any) -> None:
    console.print(f"\n[bold red]✖ Signal {signum} received. Cleaning up system resources...[/bold red]")
    ResourceManager.cleanup_all()
    sys.exit(128 + signum)

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
atexit.register(ResourceManager.cleanup_all)

# --- 5. System Inspection Subsystem ---
class SystemChecker:
    @staticmethod
    def verify_hyprland_environment() -> tuple[bool, str]:
        if not user_ctx.wayland_display:
            return False, "No WAYLAND_DISPLAY detected in environment or runtime directory."
        if not user_ctx.hyprland_signature:
            return False, "Hyprland instance signature missing (Not running inside Hyprland)."
        return True, f"Hyprland verified (Display: {user_ctx.wayland_display}, Signature: {user_ctx.hyprland_signature[:8]}...)."

    @staticmethod
    def get_service_state(service_name: str) -> ServiceState:
        active = CommandRunner.run(f"systemctl is-active {shlex.quote(service_name)}").stdout.strip() == "active"
        enabled = CommandRunner.run(f"systemctl is-enabled {shlex.quote(service_name)}").stdout.strip() in ("enabled", "linked", "alias")
        return ServiceState(name=service_name, active=active, enabled=enabled)

    @staticmethod
    def is_pkg_installed(pkg: str) -> bool:
        return CommandRunner.run(f"pacman -Qq {shlex.quote(pkg)}").returncode == 0

    @staticmethod
    def install_packages(pkgs: list[str]) -> bool:
        """Autonomous package provisioning: real-time mirror sync retry, no timeout, lock check.
        Returns True if everything is installed afterwards."""
        missing = [p for p in pkgs if not SystemChecker.is_pkg_installed(p)]
        if not missing:
            return True
        console.print(f"[bold blue]  ::[/] Installing: {', '.join(missing)}")
        if Path("/var/lib/pacman/db.lck").exists():
            console.print("[bold red]  ✖[/] Pacman database locked (/var/lib/pacman/db.lck). Release pacman and retry.")
            return False

        install_cmd = ["pacman", "-S", "--noconfirm", "--needed"] + missing
        res = CommandRunner.run(install_cmd, timeout=None)
        if res.returncode == 0:
            return True

        # Fresh/minimal installs may have a stale sync DB: refresh mirrors once
        # and retry before giving up (partial `-Sy` risk is limited to the DB).
        console.print("[bold blue]  ::[/] First pass failed; syncing pacman databases and retrying...")
        res = CommandRunner.run("pacman -Sy --noconfirm", timeout=None)
        if res.returncode != 0:
            console.print(
                "[bold yellow]  ⚠[/] `pacman -Sy` failed. Verify network/mirrors in /etc/pacman.d/mirrorlist, "
                "then re-run the orchestrator."
            )
            return False
        res = CommandRunner.run(install_cmd, timeout=None)
        if res.returncode != 0:
            console.print(f"[bold red]  ✖[/] pacman exited {res.returncode}: {res.stderr.strip()[-300:]}")
            return False
        return True

    @staticmethod
    def sync_user_dbus_env() -> None:
        """Syncs Wayland & Hyprland environment variables to user systemd manager."""
        if user_ctx.username == "root":
            return
        cmd = (
            "dbus-update-activation-environment --systemd "
            "WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR"
        )
        CommandRunner.run(cmd, as_user=True)

# --- 6. Network Interface & Sensing Engine ---
class NetworkSensingEngine:
    @staticmethod
    def validate_and_format_ip(ip_candidate: str | None) -> str:
        """Validates IP string or defaults to 0.0.0.0 to prevent socket bind errors."""
        if not ip_candidate or ip_candidate in ("<Waiting for DHCP>", "<Unauthenticated>", "None", ""):
            return "0.0.0.0"
        try:
            return str(ipaddress.ip_address(ip_candidate))
        except ValueError:
            return "0.0.0.0"

    @staticmethod
    def detect_interfaces() -> list[NetworkInterface]:
        interfaces: list[NetworkInterface] = []

        # 1. USB Tethering Interface (ipheth)
        sys_net = Path("/sys/class/net")
        if sys_net.exists():
            for iface_path in sys_net.iterdir():
                iface = iface_path.name
                if iface in ("lo", "docker0") or iface.startswith(("vbox", "virbr", "tun", "wg")):
                    continue
                driver_path = iface_path / "device" / "driver"
                if driver_path.exists() and "ipheth" in os.readlink(driver_path):
                    ip = NetworkSensingEngine._get_iface_ip(iface)
                    interfaces.append(NetworkInterface(name=iface, ip=ip or "<Waiting for DHCP>", if_type="usb"))

        # 2. Tailscale Interface (tailscale0)
        if Path("/sys/class/net/tailscale0").exists():
            ts_ip = CommandRunner.run("tailscale ip -4").stdout.strip()
            interfaces.append(NetworkInterface(name="tailscale0", ip=ts_ip or "<Unauthenticated>", if_type="tailscale"))

        # 3. Local LAN (Ethernet/Wi-Fi)
        try:
            out = CommandRunner.run("ip -j -4 addr show scope global").stdout
            if out:
                data = json.loads(out)
                for iface_info in data:
                    name = iface_info.get("ifname", "")
                    if name.startswith(("e", "w")) and not name.startswith("tailscale"):
                        addrs = iface_info.get("addr_info", [])
                        if addrs:
                            interfaces.append(NetworkInterface(name=name, ip=addrs[0].get("local", ""), if_type="lan"))
        except Exception:
            pass

        return interfaces

    @staticmethod
    def preferred_ip() -> str:
        """First usable (non-placeholder, non-loopback) IP across interfaces."""
        for iface in NetworkSensingEngine.detect_interfaces():
            ip = NetworkSensingEngine.validate_and_format_ip(iface.ip)
            if ip != "0.0.0.0" and not ip.startswith("127."):
                return ip
        return "0.0.0.0"

    @staticmethod
    def firewall_interfaces() -> list[str]:
        """Interfaces to open in the firewall: tailscale0 (if it exists) + USB tether ifaces."""
        fw = ["tailscale0"] if Path("/sys/class/net/tailscale0").exists() else []
        for iface in NetworkSensingEngine.detect_interfaces():
            if iface.if_type == "usb" and iface.name not in fw:
                fw.append(iface.name)
        return fw

    @staticmethod
    def _get_iface_ip(iface: str) -> str | None:
        out = CommandRunner.run(f"ip -4 -o addr show {shlex.quote(iface)}").stdout
        if out:
            parts = out.split()
            for part in parts:
                if "/" in part and part.count(".") == 3:
                    return part.split("/")[0]
        return None

# --- 7. Hyprland Headless Virtual Display Subsystem ---
class HyprlandManager:
    @staticmethod
    def get_headless_monitors() -> list[str]:
        out = CommandRunner.run("hyprctl monitors -j", as_user=True).stdout
        if not out:
            return []
        try:
            monitors = json.loads(out)
            return [m["name"] for m in monitors if m.get("name", "").startswith("HEADLESS-")]
        except Exception:
            return []

    @staticmethod
    def _geometry_applied(name: str, width: int, height: int, scale: float) -> bool:
        """Verifies a monitor's live geometry via `hyprctl monitors -j` (tolerance-aware)."""
        out = CommandRunner.run("hyprctl monitors -j", as_user=True).stdout
        if not out:
            return False
        try:
            for m in json.loads(out):
                if m.get("name") != name:
                    continue
                if m.get("width") != width or m.get("height") != height:
                    return False
                if abs(float(m.get("scale", 0)) - scale) > 0.01:
                    return False
                return True
        except Exception:
            return False
        return False

    @staticmethod
    def create_headless_output(res: str = "1170x2532", fps: int = 60, scale: float = 2.0) -> str | None:
        console.print("[bold blue]  ::[/] Requesting Hyprland virtual headless monitor...")
        CommandRunner.run("hyprctl output create headless HEADLESS-1", as_user=True)

        monitors = HyprlandManager.get_headless_monitors()
        name = monitors[-1] if monitors else "HEADLESS-1"

        width_s, _, height_s = str(res).partition("x")
        width, height = int(width_s), int(height_s)

        # Hyprland >= 0.55 moved monitor config to a Lua DSL parser: the legacy
        # `hyprctl keyword monitor` command silently no-ops (rc=0!) with
        # "keyword can't work with non-legacy parsers". Use hl.monitor via eval.
        eval_cmd = (
            f"hyprctl eval 'hl.monitor({{ output = \"{name}\", mode = \"{res}@{fps}\", "
            f"position = \"auto\", scale = {scale}, disabled = false }})'"
        )
        rc = CommandRunner.run(eval_cmd, as_user=True).returncode
        applied = HyprlandManager._geometry_applied(name, width, height, scale)

        if not applied:
            # Pre-0.55 fallback (legacy Hyprlang parser).
            CommandRunner.run(
                f"hyprctl keyword monitor '{shlex.quote(name)}, {res}@{fps}, auto, {scale}'",
                as_user=True,
            )
            applied = HyprlandManager._geometry_applied(name, width, height, scale)

        tail = ""
        if not applied:
            tail = f" (geometry NOT verified: {name} left at native size)"
            console.print(f"[bold yellow]  ⚠[/] Could not confirm {res}@{fps}fps scale {scale}{tail}")

        ResourceManager.register_headless_display(name)
        console.print(f"[bold green]  ✔[/] Created virtual display: [bold cyan]{name}[/] ({res}@{fps}fps, scale {scale}){tail}")
        return name

    @staticmethod
    def remove_headless_output(name: str) -> None:
        console.print(f"[bold blue]  ::[/] Destroying virtual display: [bold cyan]{name}[/]")
        CommandRunner.run(f"hyprctl output remove {shlex.quote(name)}", as_user=True)
        ResourceManager.unregister_headless_display(name)

# --- 8. Firewall Automation Subsystem ---
class FirewallManager:
    @staticmethod
    def detect_backend() -> str:
        if shutil.which("firewall-cmd"):
            if SystemChecker.get_service_state("firewalld").active or CommandRunner.run("firewall-cmd --state").stdout.strip() == "running":
                return "firewalld"

        if shutil.which("ufw"):
            status = CommandRunner.run("ufw status").stdout
            if SystemChecker.get_service_state("ufw").active or "Status: active" in status:
                return "ufw"

        if shutil.which("nft"):
            tables = CommandRunner.run("nft list tables").stdout.strip()
            if SystemChecker.get_service_state("nftables").active or (tables and "table" in tables):
                return "nftables"

        if shutil.which("iptables"):
            ip_rules = CommandRunner.run("iptables -L INPUT -n").stdout
            if SystemChecker.get_service_state("iptables").active or ("Chain INPUT" in ip_rules and len(ip_rules.splitlines()) > 2):
                return "iptables"

        return "none"

    @staticmethod
    def configure_rules(ports: list[int], interfaces: list[str]) -> None:
        backend = FirewallManager.detect_backend()
        console.print(f"[bold blue]  ::[/] Detected active firewall backend: [bold cyan]{backend}[/]")

        match backend:
            case "ufw":
                status = CommandRunner.run("ufw status").stdout
                for iface in interfaces:
                    if f"ALLOW IN ON {iface}" not in status:
                        CommandRunner.run(f"ufw allow in on {shlex.quote(iface)}")
                for port in ports:
                    if f"{port}/tcp" not in status:
                        CommandRunner.run(f"ufw allow {port}/tcp")
                    if f"{port}/udp" not in status:
                        CommandRunner.run(f"ufw allow {port}/udp")
                console.print("[bold green]  ✔[/] UFW rules updated idempotently.")

            case "firewalld":
                zone = CommandRunner.run("firewall-cmd --get-default-zone").stdout.strip() or "public"
                for iface in interfaces:
                    if CommandRunner.run(f"firewall-cmd --zone=trusted --query-interface={shlex.quote(iface)}").returncode != 0:
                        CommandRunner.run(f"firewall-cmd --permanent --zone=trusted --change-interface={shlex.quote(iface)}")
                for port in ports:
                    if CommandRunner.run(f"firewall-cmd --zone={zone} --query-port={port}/tcp").returncode != 0:
                        CommandRunner.run(f"firewall-cmd --permanent --zone={zone} --add-port={port}/tcp")
                    if CommandRunner.run(f"firewall-cmd --zone={zone} --query-port={port}/udp").returncode != 0:
                        CommandRunner.run(f"firewall-cmd --permanent --zone={zone} --add-port={port}/udp")
                CommandRunner.run("firewall-cmd --reload")
                console.print("[bold green]  ✔[/] Firewalld rules updated idempotently.")

            case "nftables":
                CommandRunner.run("nft add table inet filter")
                CommandRunner.run('nft add chain inet filter input "{ type filter hook input priority filter ; policy accept ; }"')
                rules = CommandRunner.run("nft list chain inet filter input").stdout
                for iface in interfaces:
                    if f'iifname "{iface}" accept' not in rules:
                        CommandRunner.run(f'nft add rule inet filter input iifname "{shlex.quote(iface)}" accept')
                for port in ports:
                    if f'tcp dport {port} accept' not in rules:
                        CommandRunner.run(f'nft add rule inet filter input tcp dport {port} accept')
                    if f'udp dport {port} accept' not in rules:
                        CommandRunner.run(f'nft add rule inet filter input udp dport {port} accept')
                console.print("[bold green]  ✔[/] nftables rules inserted idempotently.")

            case "iptables":
                for iface in interfaces:
                    if CommandRunner.run(f"iptables -C INPUT -i {shlex.quote(iface)} -j ACCEPT").returncode != 0:
                        CommandRunner.run(f"iptables -I INPUT 1 -i {shlex.quote(iface)} -j ACCEPT")
                for port in ports:
                    if CommandRunner.run(f"iptables -C INPUT -p tcp --dport {port} -j ACCEPT").returncode != 0:
                        CommandRunner.run(f"iptables -I INPUT 1 -p tcp --dport {port} -j ACCEPT")
                    if CommandRunner.run(f"iptables -C INPUT -p udp --dport {port} -j ACCEPT").returncode != 0:
                        CommandRunner.run(f"iptables -I INPUT 1 -p udp --dport {port} -j ACCEPT")
                if shutil.which("iptables-save"):
                    Path("/etc/iptables").mkdir(exist_ok=True)
                    out = CommandRunner.run("iptables-save").stdout
                    with open("/etc/iptables/iptables.rules", "w") as f:
                        f.write(out)
                console.print("[bold green]  ✔[/] iptables rules applied and saved idempotently.")

            case _:
                console.print("[bold yellow]  ⚠[/] No active firewall backend detected. Ensure service ports are open.")

# --- 9. WayVNC Audited Setup Engine ---
class WayVNCManager:
    @staticmethod
    def prepare_environment() -> Path:
        console.print("[bold blue]  ::[/] Auditing WayVNC PAM profile and TLS certificate configurations...")

        pam_file = Path("/etc/pam.d/wayvnc")
        if not pam_file.exists():
            pam_file.write_text("auth     include    system-auth\naccount  include    system-auth\n")
            console.print("[bold green]  ✔[/] Created /etc/pam.d/wayvnc PAM profile.")

        wayvnc_dir = user_ctx.home / ".config" / "wayvnc"
        wayvnc_dir.mkdir(parents=True, exist_ok=True)

        key_file = wayvnc_dir / "tls_key.pem"
        cert_file = wayvnc_dir / "tls_cert.pem"

        if not key_file.exists() or not cert_file.exists():
            console.print("[bold blue]  ::[/] Auto-generating self-signed TLS certificates for WayVNC...")
            # PKCS#1 ("BEGIN RSA PRIVATE KEY") is REQUIRED: neatvnc's RSA-AES
            # security type rejects OpenSSL 3's default PKCS#8 ("PRIVATE KEY").
            CommandRunner.run(
                f"openssl genrsa -traditional -out {shlex.quote(str(key_file))} 4096",
                check=True,
            )
            CommandRunner.run(
                f"openssl req -new -x509 -key {shlex.quote(str(key_file))} "
                f"-out {shlex.quote(str(cert_file))} -days 365 -sha256 -subj \"/CN=WayVNC\"",
                check=True,
            )
            os.chown(key_file, user_ctx.uid, user_ctx.gid)
            os.chown(cert_file, user_ctx.uid, user_ctx.gid)
            os.chmod(key_file, 0o600)
            console.print("[bold green]  ✔[/] TLS certificates generated (PKCS#1 RSA key pair).")

        config_file = wayvnc_dir / "config"
        # wayvnc >= 0.10: `pam_service` was removed (use `enable_pam`); TLS
        # key keys are `private_key_file`/`rsa_private_key_file`; add
        # `relax_encryption` so iOS clients can use Apple Diffie-Hellman.
        config_content = (
            f"address = 0.0.0.0\n"
            f"port = 5900\n"
            f"enable_auth = true\n"
            f"enable_pam = true\n"
            f"rsa_private_key_file = {key_file}\n"
            f"private_key_file = {key_file}\n"
            f"certificate_file = {cert_file}\n"
            f"relax_encryption = true\n"
        )
        config_file.write_text(config_content)
        os.chown(config_file, user_ctx.uid, user_ctx.gid)
        os.chown(wayvnc_dir, user_ctx.uid, user_ctx.gid)

        return config_file

    @staticmethod
    def cleanup_stale_sockets() -> None:
        socket_file = user_ctx.xdg_runtime_dir / "wayvncctl"
        if socket_file.exists():
            with contextlib.suppress(Exception):
                socket_file.unlink()
                console.print(f"[bold green]  ✔[/] Cleaned stale IPC control socket: {socket_file}")

# --- 10. Sunshine Configuration Engine ---
class SunshineManager:
    @staticmethod
    def resolve_user_unit() -> str:
        """Finds the systemd USER unit that runs Sunshine (name changed across build/packager).
        Prefers the distro-shipped unit over any stale generated fallback; only generates
        its own unit file when the package ships none."""
        out = CommandRunner.run("systemctl --user list-unit-files", as_user=True).stdout
        names = {ln.split()[0] for ln in out.splitlines() if ln.split()}
        shipped = [
            n for n in names
            if n.endswith(".service") and ("lizardbyte" in n.lower() or n == "sunshine.service")
        ]
        if shipped:
            # Prefer the package's own unit (validated config, correct tray/dbus deps):
            # the plain `sunshine.service` may be an alias or a stale user-level file.
            chosen = next((n for n in shipped if "lizardbyte" in n), shipped[0])
            return chosen

        unit_file = user_ctx.home / ".config" / "systemd" / "user" / "sunshine.service"
        unit_file.parent.mkdir(parents=True, exist_ok=True)
        unit_content = (
            "[Unit]\n"
            "Description=Sunshine (game stream host) - generated by Arch-Link Orchestrator\n"
            "After=graphical-session.target\n"
            "\n"
            "[Service]\n"
            "ExecStart=/usr/bin/sunshine\n"
            "Restart=on-failure\n"
            "\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
        unit_file.write_text(unit_content)
        os.chown(unit_file, user_ctx.uid, user_ctx.gid)
        CommandRunner.run("systemctl --user daemon-reload", as_user=True)
        return "sunshine.service"

    @staticmethod
    def ensure_unit_active(unit: str) -> bool:
        """Starts the unit and VERIFIES it truly reached `active` (polling past the
        ExecStartPre sleep). Auto-recovers from failed state and resurfaces the real
        journal tail on failure instead of a blind green check."""
        if CommandRunner.run(f"systemctl --user is-active {shlex.quote(unit)}", as_user=True).returncode == 0:
            return True
        CommandRunner.run(f"systemctl --user start {shlex.quote(unit)}", as_user=True)
        for _ in range(4):
            if CommandRunner.run(f"systemctl --user is-active {shlex.quote(unit)}", as_user=True).returncode == 0:
                return True
            time.sleep(1.5)
        # Repair path: a previously failed activation can wedge the unit.
        CommandRunner.run(f"systemctl --user reset-failed {shlex.quote(unit)}", as_user=True)
        CommandRunner.run(f"systemctl --user start {shlex.quote(unit)}", as_user=True)
        for _ in range(4):
            if CommandRunner.run(f"systemctl --user is-active {shlex.quote(unit)}", as_user=True).returncode == 0:
                return True
            time.sleep(1.5)
        logs = CommandRunner.run(
            f"journalctl --user -u {shlex.quote(unit)} -n 20 --no-pager", as_user=True
        ).stdout.strip()
        console.print(f"[bold red]  ✖[/] {unit} did not reach active. Recent unit logs:")
        console.print(logs[-900:] or "[dim]no logs available[/]")
        return False

    @staticmethod
    def configure_target_display(output_name: str) -> None:
        conf_dir = user_ctx.home / ".config" / "sunshine"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_file = conf_dir / "sunshine.conf"

        lines = [
            "# Auto-generated by Arch-iOS-Link Orchestrator",
            f"output_name = {output_name}",
            "# XDG Portal capture (Wayland direct capture is broken upstream on Hyprland 0.5x)",
            "capture = portal",
            "encoder = auto",
            "min_log_level = info"
        ]
        conf_file.write_text("\n".join(lines))
        os.chown(conf_dir, user_ctx.uid, user_ctx.gid)
        os.chown(conf_file, user_ctx.uid, user_ctx.gid)

# --- 11. QR Code Rendering Engine ---
class QRRenderer:
    @staticmethod
    def generate_qr(data: str) -> Text:
        out = CommandRunner.run(f"qrencode -t UTF8 -m 2 '{data}'").stdout
        if out:
            return Text(out, style="black on white")
        return Text(f"URI: {data}", style="bold cyan")

# --- 12. Master Interactive Orchestrator CLI ---
class RemoteConnectClient:
    """Laptop-to-laptop / LAN / Tailscale remote desk control (client side).

    Installs the viewing tools, discovers the host machine (Tailscale, mDNS,
    MagicDNS or manual IP), and launches VNC (client of the WayVNC stack) or
    Moonlight (client of the Sunshine stack)."""
    CONFIG_DIR = user_ctx.home / ".config" / "arch-link"
    CONFIG_FILE = CONFIG_DIR / "remote.json"

    @staticmethod
    def _load() -> dict[str, str]:
        try:
            return json.loads(RemoteConnectClient.CONFIG_FILE.read_text())
        except Exception:
            return {}

    @staticmethod
    def _save(profile: dict[str, str]) -> None:
        RemoteConnectClient.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        RemoteConnectClient.CONFIG_FILE.write_text(json.dumps(profile, indent=2))
        os.chown(RemoteConnectClient.CONFIG_DIR, user_ctx.uid, user_ctx.gid)
        os.chown(RemoteConnectClient.CONFIG_FILE, user_ctx.uid, user_ctx.gid)

    @staticmethod
    def viewer_provider() -> str | None:
        """Which VNC client owns /usr/bin/vncviewer: 'realvnc', 'tigervnc' or None."""
        if shutil.which("vncviewer") is None:
            return None
        if shutil.which("vnclicense") or shutil.which("vnclicensehelper"):
            return "realvnc"
        return "tigervnc"

    @staticmethod
    def install_realvnc_viewer() -> None:
        if RemoteConnectClient.viewer_provider() == "realvnc":
            console.print("[bold green]  ✔[/] RealVNC Viewer already installed.")
            return
        launched = RemoteConnectClient.viewer_provider() == "tigervnc"
        if launched:
            console.print(
                "[bold yellow]  ⚠[/] tigervnc's vncviewer is installed. RealVNC provides the same "
                "`vncviewer` binary, so installing it [bold]replaces tigervnc[/]. "
                "You can reinstall tigervnc afterwards at any time."
            )
        helper = next((h for h in ("paru", "yay") if shutil.which(h)), None)
        if not helper:
            console.print(
                "[bold yellow]  ⚠[/] No AUR helper found. Install manually: https://www.realvnc.com/en/connect/download/viewer/"
            )
            return
        console.print(f"[bold blue]  ::[/] Installing RealVNC Viewer via [bold cyan]{helper}[/] (AUR)...")
        CommandRunner.run(f"{helper} -S --noconfirm --needed realvnc-vnc-viewer", as_user=True, timeout=None)
        if RemoteConnectClient.viewer_provider() == "realvnc":
            console.print("[bold green]  ✔[/] RealVNC Viewer ready (tigervnc replaced).")
        else:
            console.print("[bold yellow]  ⚠[/] RealVNC install did not complete — check the AUR build log.")

    @staticmethod
    def install_viewer_tools() -> None:
        SystemChecker.install_packages(["tigervnc"])
        if shutil.which("moonlight") or shutil.which("moonlight-qt"):
            console.print("[bold green]  ✔[/] Moonlight client already available.")
            return
        helper = next((h for h in ("yay", "paru") if shutil.which(h)), None)
        if helper:
            console.print(f"[bold blue]  ::[/] Installing Moonlight client via [bold cyan]{helper}[/] (AUR)...")
            CommandRunner.run(f"{helper} -S --noconfirm --needed moonlight-qt", as_user=True, timeout=None)
        else:
            console.print(
                "[bold yellow]  ⚠[/] Moonlight not present and no AUR helper ([dim]yay[/]/[dim]paru[/]) found. "
                "Install it manually (https://github.com/moonlight-stream/moonlight-qt) or use VNC instead."
            )
        if shutil.which("moonlight") or shutil.which("moonlight-qt"):
            console.print("[bold green]  ✔[/] Moonlight client ready.")
        else:
            console.print("[bold yellow]  ⚠[/] Moonlight client missing — VNC mode still available via tigervnc.")

    @staticmethod
    def discover_host(name: str) -> list[tuple[str, str, str]]:
        """Returns [(via, host, ip)] candidate endpoints for the host."""
        found: dict[str, tuple[str, str, str]] = {}
        name = name.strip().strip(" .")

        # 1) Tailscale: name lookup via CLI; fall back to scanning peer list
        if shutil.which("tailscale"):
            r = CommandRunner.run(f"tailscale ip -4 {shlex.quote(name)}")
            ip = r.stdout.strip().splitlines()[-1].strip() if r.stdout.strip() else ""
            if ip and ip.count(".") == 3:
                found[ip] = ("Tailscale", name, ip)
            else:
                st = CommandRunner.run("tailscale status --json")
                try:
                    for peer in json.loads(st.stdout).get("Peer", {}).values():
                        if peer.get("HostName", "").lower() == name.lower():
                            for p in (peer.get("TailscaleIPs") or []):
                                if ":" not in p:
                                    found[p] = (f"Tailscale [{peer.get('HostName', '')}]", name, p)
                except Exception:
                    pass

        # 2) DNS / MagicDNS + mDNS (.local)
        for probe in (name, f"{name}.local"):
            record = CommandRunner.run(f"getent hosts {shlex.quote(probe)}").stdout
            for line in record.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].count(".") >= 3:
                    found[parts[0]] = ("mDNS/LAN" if probe.endswith(".local") else "DNS", name, parts[0])

        # 3) Literal IP typed by the user
        try:
            ipaddress.ip_address(name)
            found[name] = ("Manual", name, name)
        except ValueError:
            pass

        ordered = [v for v in found.values() if v[0] != "Tailscale"]
        ordered += [v for v in found.values() if v[0] == "Tailscale"]
        return ordered

    @staticmethod
    def probe_port(ip: str, port: int, timeout: float = 2.0) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                return sock.connect_ex((ip, port)) == 0
        except Exception:
            return False

    @staticmethod
    def _launch(app: str, ip: str) -> None:
        moonlight_bin = "moonlight-qt" if shutil.which("moonlight-qt") else "moonlight"
        if app == "moonlight":
            cmd = [moonlight_bin, ip]
        elif app in ("vncviewer", "realvnc"):
            if not os.environ.get("DISPLAY") and not Path("/tmp/.X11-unix").glob("X*"):
                console.print(
                    "[bold yellow]  ⚠[/] VNC viewers (tigervnc/RealVNC) are X11 apps, but no X display/XWayland "
                    "was detected here — launch will fail with 'Can't open display'. Enable XWayland "
                    "(Hyprland: `xwayland:enabled = true`, then relogin) or use Moonlight, which is native Wayland."
                )
            cmd = ["vncviewer", f"{ip}:5900"]
        else:
            return
        subprocess.Popen(
            cmd,
            env=user_ctx.get_user_env(),
            preexec_fn=user_ctx.demote_fn(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        console.print(f"[bold green]  ✔[/] Launched [bold cyan]{cmd[0]}[/] → {ip}. Use the normal PIN / password flow.")

    def connect_to_host(self) -> None:
        self.display_header()
        console.print(Panel("[bold cyan]Remote Connect (Client Mode)[/]", border_style="cyan"))

        profile = RemoteConnectClient._load()
        ans = Prompt.ask("Host hostname or IP", default=profile.get("host") or "", show_default=True)
        ans = ans.strip()
        if not ans:
            console.print("[bold yellow]  ⚠[/] No host given. Aborted.")
            return
        try:
            ipaddress.ip_address(ans)
            candidates = [("Manual", ans, ans)]
        except ValueError:
            candidates = RemoteConnectClient.discover_host(ans)

        if not candidates:
            console.print(f"[bold red]  ✖[/] No route found to '{ans}'. Check Tailscale login, LAN reachability, or hostname.")
            return

        saved_ip = profile.get("ip")
        table = Table(title=f"Host Candidates for {ans}", show_header=True, header_style="bold magenta", border_style="cyan")
        table.add_column("#", justify="right")
        table.add_column("Via", style="bold green")
        table.add_column("IP", style="bold yellow")
        table.add_column("5900 VNC", justify="center")
        table.add_column("47989 ML", justify="center")
        default_idx = 1
        for idx, (via, _, candidate_ip) in enumerate(candidates):
            vnc = "✓" if RemoteConnectClient.probe_port(candidate_ip, 5900) else "–"
            ml = "✓" if RemoteConnectClient.probe_port(candidate_ip, 47989) else "–"
            if candidate_ip == saved_ip:
                default_idx = idx + 1
            table.add_row(str(idx + 1), via, candidate_ip, vnc, ml)
        console.print(table)

        choice_raw = Prompt.ask(
            "Pick endpoint or 'm' for manual IP",
            default=str(default_idx),
            show_default=True,
        ).strip()
        if choice_raw.lower() == "m":
            choice_raw = Prompt.ask("Full IP address")
            try:
                ipaddress.ip_address(choice_raw)
                selected_ip = choice_raw
            except ValueError:
                console.print("[bold yellow]  ⚠[/] Invalid IP. Aborted.")
                return
        else:
            try:
                selected_ip = candidates[int(choice_raw) - 1][2]
            except (ValueError, IndexError):
                selected_ip = next((c[2] for c in candidates), "0.0.0.0")

        RemoteConnectClient._save({"host": ans, "ip": selected_ip})
        console.print(f"[bold green]  ✔[/] Saved profile: {ans} → {selected_ip}")

        provider = RemoteConnectClient.viewer_provider()
        console.print(Panel.fit(
            f"[bold yellow]Connecting to [bold cyan]{selected_ip}[/]:[/]\n\n"
            "  [bold cyan]1.[/] VNC control via [bold]tigervnc[/] [dim](vncviewer → 5900, host's WayVNC stack)[/]\n"
            "  [bold cyan]2.[/] VCN control via [bold]RealVNC Viewer[/] [dim](realvnc-vnc-viewer → 5900)[/]\n"
            "  [bold cyan]3.[/] Moonlight streaming [dim](host's Sunshine stack)[/]\n"
            "  [bold cyan]4.[/] Install / refresh viewer tools\n"
            "  [bold cyan]5.[/] Back",
            title="Choose Protocol",
            border_style="magenta",
            width=70,
        ))
        action = Prompt.ask(
            "Select",
            choices=["1", "2", "3", "4", "5"],
            default="1",
        )
        if action == "1":
            if not RemoteConnectClient.probe_port(selected_ip, 5900):
                console.print("[bold yellow]  ⚠[/] Port 5900 is closed on the host — start its Option 2 (WayVNC) there first.")
            if provider == "realvnc":
                console.print("[bold yellow]  ⚠[/] RealVNC owns `vncviewer` — tigervnc is not installed. Pick 2 (RealVNC) or run 4 & reinstall tigervnc.")
            else:
                if provider is None:
                    console.print("[bold blue]  ::[/] tigervnc not found — installing it now (root session, no extra prompt)...")
                    SystemChecker.install_packages(["tigervnc"])
                    provider = RemoteConnectClient.viewer_provider()
                if provider == "tigervnc":
                    RemoteConnectClient._launch("vncviewer", selected_ip)
                else:
                    console.print("[bold yellow]  ⚠[/] VNC viewer still unavailable. Run option 4 to install the tools.")
        elif action == "2":
            if not RemoteConnectClient.probe_port(selected_ip, 5900):
                console.print("[bold yellow]  ⚠[/] Port 5900 is closed on the host — start its Option 2 (WayVNC) there first.")
            if provider != "realvnc":
                console.print("[bold blue]  ::[/] RealVNC Viewer not installed yet.")
                RemoteConnectClient.install_realvnc_viewer()
                provider = RemoteConnectClient.viewer_provider()
            if provider == "realvnc":
                RemoteConnectClient._launch("realvnc", selected_ip)
            else:
                console.print("[bold yellow]  ⚠[/] RealVNC unavailable — falling back to tigervnc if present.")
                if provider is not None:
                    RemoteConnectClient._launch("vncviewer", selected_ip)
        elif action == "3":
            if not RemoteConnectClient.probe_port(selected_ip, 47989):
                console.print("[bold yellow]  ⚠[/] Port 47989 is closed — start its Option 1 (Sunshine) there first.")
            RemoteConnectClient._launch("moonlight", selected_ip)
        elif action == "4":
            RemoteConnectClient.install_viewer_tools()

# --- 12b. Master Interactive Orchestrator CLI ---
class ArchIOSLinkCLI:
    def __init__(self) -> None:
        self.user = user_ctx.username

    def display_header(self) -> None:
        if console.is_terminal:
            console.clear()
        console.print(
            Panel.fit(
                f"[bold white]Target Architecture:[/] [bold cyan]Arch Linux (Wayland/Hyprland)[/]\n"
                f"[bold white]Target Client:[/] [bold green]iOS (any version, standard VNC) / Linux (Moonlight)[/]\n"
                f"[bold white]Active Desktop User:[/] [bold yellow]{self.user}[/] [dim](UID: {user_ctx.uid})[/]\n"
                f"[bold white]Wayland Display:[/] [bold magenta]{user_ctx.wayland_display or 'Unknown'}[/]",
                title="[bold green]✦ Arch Linux ↔ iOS Remote Link Orchestrator v7.0 Golden ✦[/]",
                border_style="blue",
                padding=(1, 4)
            )
        )

    def render_network_panel(self) -> None:
        interfaces = NetworkSensingEngine.detect_interfaces()
        table = Table(title="[bold]Network Interfaces & Sensing[/]", show_header=True, header_style="bold magenta", border_style="cyan")
        table.add_column("Type", style="bold green", justify="right")
        table.add_column("Interface", style="cyan")
        table.add_column("IP Address", style="bold yellow")

        for iface in interfaces:
            table.add_row(iface.if_type.upper(), iface.name, iface.ip)

        if not interfaces:
            table.add_row("NONE", "None detected", "Check connection")

        console.print(table, justify="center")
        console.print(
            "[dim]Tip: Moonlight won't auto-detect this PC — on the phone tap 'Add Host' "
            "and type any IP from the table above.[/]",
            justify="center",
        )

    def setup_uinput_permissions(self) -> None:
        console.print("[bold blue]  ::[/] Hardening /dev/uinput permissions & boot persistence...")

        # Boot module persistence
        modules_file = Path("/etc/modules-load.d/uinput.conf")
        if not modules_file.exists() or "uinput" not in modules_file.read_text():
            modules_file.write_text("uinput\n")

        CommandRunner.run("modprobe uinput")

        # udev rule
        udev_file = Path("/etc/udev/rules.d/85-uinput-archlink.rules")
        udev_file.write_text('KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess", GROUP="input", MODE="0660"\n')

        if self.user != "root":
            CommandRunner.run(f"usermod -aG input {shlex.quote(self.user)}")

        CommandRunner.run("udevadm control --reload-rules")
        CommandRunner.run("udevadm trigger --name-match=uinput")

        # Immediate ACL fix for current running user session
        uinput_node = Path("/dev/uinput")
        if uinput_node.exists() and self.user != "root":
            CommandRunner.run(f"setfacl -m u:{shlex.quote(self.user)}:rw /dev/uinput")
            CommandRunner.run("chown root:input /dev/uinput")
            CommandRunner.run("chmod 0660 /dev/uinput")

        console.print("[bold green]  ✔[/] /dev/uinput permissions & direct user POSIX ACL granted.")

    def run_sunshine_stack(self) -> None:
        first_run = True
        while True:
            self.display_header()
            if not first_run:
                console.print("[bold blue]  ::[/] Restarting Sunshine stack — stale state was auto-cleaned...")
            first_run = False
            console.print(Panel("[bold cyan]Sunshine + Moonlight Ultra-Low Latency Streaming Stack[/]", border_style="cyan"))

            headless_name: str | None = None
            try:
                if not SystemChecker.install_packages(["sunshine", "pipewire", "wireplumber", "libevdev"]):
                    console.print("[bold red]  ✖[/] Sunshine stack cannot proceed without required packages.")
                    return

                self.setup_uinput_permissions()
                SystemChecker.sync_user_dbus_env()

                headless_name = HyprlandManager.create_headless_output(res="1170x2532", fps=60, scale=2.0)
                if headless_name:
                    SunshineManager.configure_target_display(headless_name)

                FirewallManager.configure_rules(
                    ports=[47984, 47989, 47990, 48010, 47998, 47999, 48000, 48002],
                    interfaces=NetworkSensingEngine.firewall_interfaces()
                )

                console.print(f"[bold blue]  ::[/] Enabling Sunshine user daemon for [bold cyan]{self.user}[/]...")
                unit_name = SunshineManager.resolve_user_unit()
                CommandRunner.run(f"systemctl --user enable {shlex.quote(unit_name)}", as_user=True)
                if not SunshineManager.ensure_unit_active(unit_name):
                    console.print(
                        "[bold yellow]  ⚠[/] Sunshine could not be started. Re-run the stack after fixing the cause "
                        "(journal tail above). No pairing link will be shown."
                    )
                    return
                console.print(f"[bold green]  ✔[/] User unit [bold cyan]{unit_name}[/] enabled, started and verified active.")

                primary_ip = NetworkSensingEngine.preferred_ip()
                if primary_ip == "0.0.0.0":
                    primary_ip = "localhost"
                    console.print(
                        "[bold yellow]  ⚠[/] No routable IP detected yet — showing the localhost URI. "
                        "Pair via USB tunnel or after Wi-Fi/DHCP assignment."
                    )
                web_ui_url = f"https://{primary_ip}:47990"
                console.print(f"\n[bold green]✔ Sunshine Streaming Server Active![/]")
                console.print(f"  Web UI Pair Link: [bold cyan]{web_ui_url}[/]")

                qr = QRRenderer.generate_qr(web_ui_url)
                console.print(Panel(Align.center(qr), title="Scan with iOS to Pair Sunshine Web UI", border_style="cyan", width=60), justify="center")

                ip_rows = [
                    (it.if_type.upper(), it.ip)
                    for it in NetworkSensingEngine.detect_interfaces()
                    if NetworkSensingEngine.validate_and_format_ip(it.ip) != "0.0.0.0"
                    and not it.ip.startswith("127.")
                ]
                ip_block = "\n".join(
                    f"      [bold cyan]•[/] {label}: [bold yellow]{ip}[/]"
                    for label, ip in ip_rows
                ) or "      [dim]no network IP detected yet[/]"

                console.print(
                    Panel.fit(
                        "[bold yellow]On your iPhone (right now):[/]\n\n"
                        "  [bold cyan]1.[/] Turn [bold]off[/] VPNs ([dim]Cloudflare WARP[/], [dim]Tailscale[/], ...) "
                        "on the phone — plain [bold]same Wi-Fi[/]\n"
                        "  [bold cyan]2.[/] Open [bold]Moonlight[/] → tap the [bold]＋ / Add Host[/] button "
                        "[dim](it will NOT auto-detect your PC)[/]\n"
                        "  [bold cyan]3.[/] Type [bold]any[/] of these IPs (your PC's network addresses):\n"
                        f"{ip_block}\n"
                        "  [bold cyan]4.[/] Tap the new host → Moonlight shows a [bold]4-digit PIN[/] — "
                        "a [bold]pairing notification[/] pops up on this laptop\n"
                        "  [bold cyan]5.[/] Click that notification → it opens [bold]https://localhost:47990/pin[/] "
                        "[dim](your own browser)[/]\n"
                        "  [bold cyan]6.[/] Type the PIN and set the name = your PC name "
                        f"[dim](e.g. [bold]{os.uname().nodename}[/])[/] → Save\n"
                        "  [bold cyan]7.[/] Back on the phone: tap the tile → tap again to connect & stream\n\n"
                        "[bold green]USB / offline mode (no Wi-Fi needed):[/]\n"
                        "  [bold cyan]•[/] Connect iPhone with a [bold]USB cable[/] → on the phone enable "
                        "[bold]Personal Hotspot[/] (USB option)\n"
                        "  [bold cyan]•[/] The PC shows a new [bold]USB[/] row above — type THAT IP "
                        "[dim](192.168.42.x)[/] into Moonlight\n"
                        "  [bold cyan]•[/] Works with [bold]Wi-Fi and even airplane mode[/] on "
                        "[dim](link is pure cable LAN; no internet involved)[/]\n\n"
                        "[bold green]On this computer:[/] press Enter when you're done streaming "
                        "(tears down the virtual display).",
                        title="iOS Streaming Instructions",
                        border_style="magenta",
                        width=72,
                    )
                )

                Prompt.ask("\nPress Enter when done streaming to teardown virtual display...")
            finally:
                if headless_name:
                    HyprlandManager.remove_headless_output(headless_name)

            if not Confirm.ask("\nRestart Sunshine stack?", default=False):
                break

    def run_wayvnc_stack(self) -> None:
        first_run = True
        while True:
            self.display_header()
            if not first_run:
                console.print("[bold blue]  ::[/] Restarting WayVNC stack (auto-cleaned previous session)...")
            first_run = False
            console.print(Panel("[bold cyan]WayVNC Lightweight Headless Display Stack[/]", border_style="cyan"))

            headless_name: str | None = None
            vnc_proc: subprocess.Popen[Any] | None = None
            try:
                if not SystemChecker.install_packages(["wayvnc", "openssl"]):
                    console.print("[bold red]  ✖[/] WayVNC stack cannot proceed without required packages.")
                    return

                WayVNCManager.prepare_environment()
                WayVNCManager.cleanup_stale_sockets()

                headless_name = HyprlandManager.create_headless_output(res="1080x1920", fps=60, scale=1.5)
                FirewallManager.configure_rules(
                    ports=[5900],
                    interfaces=NetworkSensingEngine.firewall_interfaces()
                )

                bind_ip = "0.0.0.0"
                connect_ip = NetworkSensingEngine.preferred_ip()
                if connect_ip == "0.0.0.0":
                    connect_ip = "localhost"
                    console.print(
                        "[bold yellow]  ⚠[/] No routable IP detected yet — showing the localhost URI. "
                        "It works immediately over the USB iproxy tunnel; re-run once Wi-Fi/DHCP is up for network access."
                    )

                hostname = socket.gethostname().split(".")[0]
                console.print(f"[bold blue]  ::[/] Launching WayVNC bound to {bind_ip}:5900 on {headless_name}...")

                vnc_env = user_ctx.get_user_env()
                vnc_proc = subprocess.Popen(
                    ["wayvnc", "-o", headless_name, bind_ip, "5900"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=vnc_env,
                    preexec_fn=user_ctx.demote_fn()
                )
                ResourceManager.register_process(vnc_proc)

                vnc_uri = f"vnc://{connect_ip}:5900"
                console.print(f"\n[bold green]✔ WayVNC Server Running at {vnc_uri}[/]")
                console.print(f"[bold white]   Host:[/] [bold yellow]{hostname}:5900[/]   [bold white]IP:[/] [bold yellow]{connect_ip}:5900[/]\n")

                qr = QRRenderer.generate_qr(vnc_uri)
                console.print(Panel(Align.center(qr), title="Scan with RealVNC / VNC App", border_style="cyan", width=60), justify="center")

                console.print(
                    Panel.fit(
                        "[bold yellow]On your iPhone (right now):[/]\n\n"
                        "  [bold cyan]1.[/] Open [bold]RealVNC (RVNC)[/] → + → add a connection\n"
                        f"  [bold cyan]2.[/] Enter the server [bold]NAME[/] — the host's system name: [bold]{hostname}[/] "
                        f"[dim](or IP {connect_ip})[/]\n"
                        "  [bold cyan]3.[/] It validates the host, then asks for the [bold]port[/] — keep [bold]5900[/]\n"
                        f"  [bold cyan]4.[/] Then it asks, separately, for the [bold]username[/] → [bold]{user_ctx.username}[/]\n"
                        f"  [bold cyan]5.[/] And the [bold]password[/] → your Linux account password "
                        "[dim](PAM login, the same one you use to log in)[/]\n"
                        "  [bold cyan]6.[/] Confirm the unknown-certificate warning "
                        "[dim](self-signed WayVNC cert is normal)[/] → Done!\n\n"
                        "[bold green]On this computer:[/] press Enter at any time to stop WayVNC "
                        "[dim](tears down the virtual display)[/].\n"
                        "[bold yellow]Something went wrong?[/] Stale sockets are cleaned automatically; "
                        "re-run Option 2 or confirm restart below — no reinstall needed.\n"
                        "[bold red]Troubleshooting (RVNC):[/] if you can't connect from the phone, open "
                        "RVNC → Settings → [bold]disable 'Connect via proxy'[/] — its own description says "
                        "to turn it off when a connection can't be established, and it is ON by default.",
                        title="iOS VNC Instructions",
                        border_style="magenta",
                        width=74,
                    )
                )

                Prompt.ask("\nPress Enter to stop WayVNC and teardown display...")
            finally:
                if vnc_proc:
                    with contextlib.suppress(Exception):
                        vnc_proc.terminate()
                        vnc_proc.wait(timeout=2)
                    ResourceManager.unregister_process(vnc_proc)
                WayVNCManager.cleanup_stale_sockets()
                if headless_name:
                    HyprlandManager.remove_headless_output(headless_name)

            if not Confirm.ask("\nRestart WayVNC stack?", default=False):
                break

    def setup_usb_tether_and_tunnel(self) -> None:
        first_run = True
        while True:
            self.display_header()
            if not first_run:
                console.print("[bold blue]  ::[/] Restarting USB stack — old forwarding sessions were shut down...")
            first_run = False
            console.print(Panel("[bold cyan]USB Cable Tethering & usbmuxd Reverse Tunneling[/]", border_style="cyan"))

            iproxy_proc: subprocess.Popen[Any] | None = None
            try:
                CommandRunner.run("systemctl enable --now usbmuxd")
                console.print("[bold green]  ✔[/] usbmuxd service active.")

                # Pairing Validation
                if shutil.which("idevicepair"):
                    pair_check = CommandRunner.run("idevicepair validate")
                    if pair_check.returncode == 0 and "SUCCESS" in pair_check.stdout:
                        console.print("[bold green]  ✔[/] iOS device trusted and paired.")
                    else:
                        console.print("[bold yellow]  ⚠[/] Device not paired. Unlock iPhone and tap 'Trust This Computer'...")
                        CommandRunner.run("idevicepair pair", timeout=None)

                console.print("\n[bold yellow]Instructions for iOS Device:[/]")
                console.print("  1. Connect iPhone to Linux PC via Lightning/USB-C cable.")
                console.print("  2. Unlock iPhone and tap 'Trust This Computer' if prompted.")
                console.print("  3. Enable Personal Hotspot (USB Only) in Settings.")

                if Confirm.ask("\nStart usbmuxd port forwarding (`iproxy`) for VNC, Sunshine & SSH?", default=True):
                    console.print("[bold blue]  ::[/] Launching `iproxy` multi-port tunnel over USB...")

                    proxy_maps = ["5900:5900", "3389:3389", "47989:47989", "47990:47990", "2222:22"]
                    listen_ports = {int(m.split(":")[0]) for m in proxy_maps}

                    busy: set[int] = set()
                    ss_out = CommandRunner.run("ss -tlnH").stdout
                    for line in ss_out.splitlines():
                        for tok in line.split():
                            if tok.startswith(("0.0.0.0:", "[::]:", "127.0.0.1:", "*:")):
                                with contextlib.suppress(ValueError):
                                    busy.add(int(tok.rsplit(":", 1)[1]))

                    conflicting = listen_ports & busy
                    if conflicting:
                        console.print(f"[bold yellow]  ⚠[/] Ports already in use locally, skipping: {sorted(conflicting)}")
                    proxy_maps = [m for m in proxy_maps if int(m.split(":")[0]) not in conflicting]

                    if not proxy_maps:
                        console.print("[bold yellow]  ⚠[/] All requested forwarded ports are busy. Refusing to start iproxy.")
                    else:
                        iproxy_proc = subprocess.Popen(
                            ["iproxy"] + proxy_maps,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        ResourceManager.register_process(iproxy_proc)
                        pending = [m.split(":")[1] for m in proxy_maps]
                        console.print(f"[bold green]  ✔[/] USB Tunnel active! Localhost ports {', '.join(m.split(':')[0] for m in proxy_maps)} forwarded to device ports {', '.join(pending)} over USB cable.")
                        Prompt.ask("\nPress Enter to stop USB forwarding...")
            finally:
                if iproxy_proc:
                    with contextlib.suppress(Exception):
                        iproxy_proc.terminate()
                        iproxy_proc.wait(timeout=2)
                    ResourceManager.unregister_process(iproxy_proc)

            if not Confirm.ask("\nRestart USB forwarding / re-check the cable?", default=False):
                break

    def display_ios_setup_guide(self) -> None:
        self.display_header()
        guide = Table(title="[bold]iOS Remote Client Setup Guide (Version-Agnostic)[/]", show_header=True, header_style="bold magenta", border_style="green")
        guide.add_column("Category", style="bold cyan")
        guide.add_column("Recommended Tool", style="bold yellow")
        guide.add_column("Purpose & Instructions", style="white")

        guide.add_row(
            "Remote Control Client",
            "Jump Desktop (VNC/RDP/Fluid)",
            "Best for full desktop control, touch gestures, trackpad mode, and extended modifier keybars. Available on any supported iOS version."
        )
        guide.add_row(
            "Streaming Client",
            "Moonlight iOS",
            "Connects to Sunshine for low-latency 60-120 FPS screen streaming over Wi-Fi/Tailscale/USB."
        )
        guide.add_row(
            "Terminal & SSH",
            "NewTerm 3 + OpenSSH",
            "Native terminal emulator on iOS and SSH daemon for command line control over usbmuxd USB tunnel."
        )

        console.print(guide, justify="center")
        Prompt.ask("\nPress Enter to return to main menu")

    def main_menu(self) -> None:
        while True:
            self.display_header()
            self.render_network_panel()

            console.print("\n[bold yellow]Select Action:[/bold yellow]")
            console.print("  [bold cyan]1.[/] Launch Sunshine + Moonlight Ultra-Low Latency Streaming Stack")
            console.print("  [bold cyan]2.[/] Launch WayVNC Lightweight Headless Display Stack")
            console.print("  [bold cyan]3.[/] Configure USB Cable Tethering & usbmuxd Tunnels (`iproxy`)")
            console.print("  [bold cyan]4.[/] View iOS Remote Client Setup Guide (version-agnostic)")
            console.print("  [bold cyan]5.[/] Remote Connect (Client Mode): control another machine via VNC / Moonlight")
            console.print("  [bold cyan]6.[/] Exit Orchestrator")

            try:
                choice = Prompt.ask("\nEnter choice", choices=["1", "2", "3", "4", "5", "6"], default="6")
            except (KeyboardInterrupt, EOFError):
                choice = "6"

            match choice:
                case "1":
                    self.run_sunshine_stack()
                case "2":
                    self.run_wayvnc_stack()
                case "3":
                    self.setup_usb_tether_and_tunnel()
                case "4":
                    self.display_ios_setup_guide()
                case "5":
                    RemoteConnectClient().connect_to_host()
                case "6":
                    console.print("[bold cyan]Exiting orchestrator. Cleaning up resources...[/bold cyan]")
                    ResourceManager.cleanup_all()
                    sys.exit(0)

def main() -> None:
    if "--client" in sys.argv:
        RemoteConnectClient().connect_to_host()
        sys.exit(0)

    ok, msg = SystemChecker.verify_hyprland_environment()
    if not ok:
        console.print(f"[bold red]✖ Environment Error:[/] {msg}")
        sys.exit(1)

    cli = ArchIOSLinkCLI()
    cli.main_menu()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]✖ Interrupted by user. Cleaning up...[/bold red]")
        ResourceManager.cleanup_all()
        sys.exit(130)

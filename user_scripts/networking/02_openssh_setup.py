#!/usr/bin/env python3
# ==============================================================================
# Arch Linux SSH Bootstrap v7.0 (Golden Copy - Modern Python Edition)
# ------------------------------------------------------------------------------
# Purpose: Auto-provision OpenSSH, configure firewalls, smart IP/Tailscale
#          detection, sshd.socket aware. 
# Target:  Arch Linux (latest rolling), Wayland/Hyprland, Python 3.10+
# Usage:   python setup_ssh.py [--auto]
# ==============================================================================

import os
import sys
import json
import shlex
import shutil
import argparse
import subprocess
import contextlib
from pathlib import Path

# --- 1. Early Privilege Escalation & Dependency Bootstrapping ---
def bootstrap_environment():
    """Ensures root privileges and strictly enforces dependencies BEFORE loading UI."""
    # 1. Escalate to root securely
    if os.geteuid() != 0:
        print("[\033[1;33m*\033[0m] Root privileges required for Arch Linux provisioning. Elevating via sudo...", flush=True)
        try:
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except Exception as e:
            print(f"[\033[1;31m!\033[0m] Privilege escalation failed: {e}", flush=True)
            sys.exit(1)

    # 2. Check dependencies (we are guaranteed root now)
    try:
        import rich
        import textual
    except ImportError:
        print("[\033[1;36m*\033[0m] Missing critical Python libraries (rich, textual).", flush=True)
        print("[\033[1;36m*\033[0m] Autonomous mode: Auto-installing via pacman...", flush=True)
        
        if Path("/var/lib/pacman/db.lck").exists():
            print("[\033[1;31m!\033[0m] FATAL: Pacman database locked (/var/lib/pacman/db.lck).", flush=True)
            sys.exit(1)
            
        try:
            subprocess.run(
                ["pacman", "-S", "python-rich", "python-textual", "qrencode", "--noconfirm", "--needed"],
                check=True
            )
            print("[\033[1;32m✔\033[0m] Dependencies installed. Reloading environment...", flush=True)
            # Re-execute the script so the Python runtime recognizes the newly installed packages
            os.execvp(sys.executable, [sys.executable] + sys.argv)
        except subprocess.CalledProcessError as e:
            print(f"[\033[1;31m!\033[0m] FATAL: Failed to install dependencies: {e}", flush=True)
            sys.exit(1)

# Execute bootstrap BEFORE the global imports are evaluated
bootstrap_environment()

from rich.console import Console
from rich.prompt import Confirm
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align

# Initialize global Rich console
console = Console()

# --- Utility Functions ---

def run_cmd(cmd: list[str] | str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command safely, strictly trapping FileNotFoundError for missing binaries."""
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    
    try:
        return subprocess.run(
            cmd, 
            check=check, 
            capture_output=capture, 
            text=True
        )
    except FileNotFoundError:
        # Gracefully return a 127 (POSIX standard for 'command not found') if binary is missing
        if check:
            raise subprocess.CalledProcessError(127, cmd, output=f"Binary not found: {cmd[0]}")
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=f"Binary not found: {cmd[0]}")

def log_info(msg: str):
    console.print(f"[bold blue]  ::[/] {msg}")

def log_success(msg: str):
    console.print(f"[bold green]  ✔[/] {msg}")

def log_warn(msg: str):
    console.print(f"[bold yellow]  ⚠[/] {msg}")

def log_error(msg: str):
    console.print(f"[bold red]  ✖[/] {msg}")

def die(msg: str, exc: Exception | None = None):
    log_error(msg)
    if exc:
        console.print(f"[dim red]    Details: {exc}[/]")
    sys.exit(1)

# --- Core Logic ---

def get_real_user() -> str:
    """Determine the actual user invoking the script (bypassing sudo/root)."""
    user = os.environ.get("SUDO_USER")
    if user and user != "root":
        return user
    
    if shutil.which("loginctl"):
        out = run_cmd("loginctl list-sessions --output=json", check=False)
        if out.returncode == 0:
            with contextlib.suppress(json.JSONDecodeError):
                sessions = json.loads(out.stdout)
                for session in sessions:
                    if session.get("uid", 0) >= 1000:
                        return session.get("user", "root")
    
    return "root"

def check_pacman_lock():
    """Check if Pacman is currently locked."""
    if Path("/var/lib/pacman/db.lck").exists():
        die("Pacman database is locked (/var/lib/pacman/db.lck). Is it running elsewhere?")

def install_openssh():
    """Install OpenSSH using pacman if not present."""
    if run_cmd("pacman -Qi openssh", check=False).returncode == 0:
        log_success("OpenSSH is already installed.")
        return

    check_pacman_lock()
    with console.status("[bold cyan]Installing OpenSSH via pacman..."):
        try:
            run_cmd("pacman -S --noconfirm --needed openssh", check=True, capture=False)
            log_success("OpenSSH installed successfully.")
        except subprocess.CalledProcessError as e:
            die("Installation failed. Run 'sudo pacman -Syu' first to sync repos.", e)

def generate_host_keys():
    """Ensure SSH host keys exist."""
    try:
        run_cmd("ssh-keygen -A", check=True)
        log_success("SSH host keys verified.")
    except subprocess.CalledProcessError:
        log_warn("ssh-keygen -A failed. Check /etc/ssh/ permissions.")

def validate_sshd_config() -> str:
    """Run built-in syntax check for sshd."""
    try:
        run_cmd("sshd -t", check=True)
        log_success("sshd configuration is valid.")
    except subprocess.CalledProcessError as e:
        die("sshd configuration is invalid. Fix /etc/ssh/sshd_config and re-run.", e)
        
    return run_cmd("sshd -T").stdout

def detect_unit_and_port(config_text: str) -> tuple[str, str, int]:
    """Detect if sshd is using socket or service activation, and determine port."""
    unit = "sshd.service"
    unit_type = "service"
    port = 22

    out_socket_active = run_cmd("systemctl is-active sshd.socket", check=False).returncode == 0
    out_socket_enabled = run_cmd("systemctl is-enabled sshd.socket", check=False).returncode == 0
    
    if out_socket_active or out_socket_enabled:
        unit = "sshd.socket"
        unit_type = "socket"
        
        socket_cat = run_cmd("systemctl cat sshd.socket", check=False).stdout
        for line in socket_cat.splitlines():
            line = line.strip()
            if line.startswith("ListenStream="):
                val = line.split("=", 1)[1].strip()
                if not val:
                    continue
                if val.isdigit():
                    port = int(val)
                elif ":" in val:
                    p = val.rsplit(":", 1)[1]
                    if p.isdigit():
                        port = int(p)

    if port == 22:
        for line in config_text.splitlines():
            if line.lower().startswith("port "):
                extracted_port = line.split()[1]
                if extracted_port.isdigit():
                    port = int(extracted_port)
                    break
    
    if not 1 <= port <= 65535:
        log_warn(f"Port {port} out of range (1-65535). Falling back to 22.")
        port = 22
        
    log_info(f"Target SSH Port: [bold magenta]{port}[/]")
    return unit, unit_type, port

def analyze_security_warnings(config_text: str, user: str):
    """Parse output of sshd -T to warn about common lockout issues based strictly on ssh(1) manual."""
    lines = [line.strip().lower() for line in config_text.splitlines()]
    
    listen_addrs = [line.split()[1] for line in lines if line.startswith("listenaddress ")]
    if listen_addrs:
        all_local = all(any(x in addr for x in ("127.", "::1", "localhost")) for addr in listen_addrs)
        if all_local:
            log_warn("sshd listens ONLY on localhost. Remote connections will fail.")

    if user == "root":
        permit_root = next((line.split()[1] for line in lines if line.startswith("permitrootlogin ")), "prohibit-password")
        if permit_root == "no":
            log_warn("PermitRootLogin is 'no'. Root cannot SSH in.")
        elif permit_root in ("prohibit-password", "without-password"):
            log_warn(f"PermitRootLogin is '{permit_root}'. Keys are required (no passwords).")

    pass_auth = next((line.split()[1] for line in lines if line.startswith("passwordauthentication ")), "yes")
    pubkey_auth = next((line.split()[1] for line in lines if line.startswith("pubkeyauthentication ")), "yes")
    kbd_auth = next((line.split()[1] for line in lines if line.startswith("kbdinteractiveauthentication ")), "no")
    host_auth = next((line.split()[1] for line in lines if line.startswith("hostbasedauthentication ")), "no")
    gssapi_auth = next((line.split()[1] for line in lines if line.startswith("gssapiauthentication ")), "no")
    
    if all(a == "no" for a in (pass_auth, pubkey_auth, kbd_auth, host_auth, gssapi_auth)):
        log_error("[bold red]CRITICAL:[/] All primary authentication methods are disabled. You WILL be locked out.")
        
    if pass_auth == "no" and kbd_auth == "no":
        user_home_dir = Path("/root") if user == "root" else Path(f"~{user}").expanduser()
        auth_keys = user_home_dir / ".ssh" / "authorized_keys"
        if not auth_keys.exists() or auth_keys.stat().st_size == 0:
            log_warn(f"Password/Interactive Auth is disabled, and no keys found in {auth_keys}")
            log_info("Use [bold]ssh-copy-id[/] to add your public keys (e.g., Ed25519) before disconnecting.")

    x11_fwd = next((line.split()[1] for line in lines if line.startswith("x11forwarding ")), "no")
    if x11_fwd == "yes":
        log_warn("X11Forwarding is 'yes'. Ensure 'ForwardX11Trusted' is configured carefully to prevent keystroke monitoring.")

def check_port_conflicts(port: int):
    """Ensure the target port isn't bound by an unknown app."""
    if not shutil.which("ss"):
        return

    out = run_cmd(f"ss -Hltnp sport = :{port}", check=False).stdout
    if out.strip():
        if "sshd" in out or "systemd" in out:
            pass
        else:
            log_warn(f"Port {port} is held by another process:\n    {out.strip()}")

def configure_firewalls(port: int):
    """Detect and configure all active firewalls seamlessly using native shutil bindings."""
    active_firewalls = 0
    
    # --- UFW (Primary Focus per ecosystem) ---
    if shutil.which("ufw"):
        if "Status: active" in run_cmd("ufw status", check=False).stdout:
            active_firewalls += 1
            if str(port) not in run_cmd("ufw status", check=False).stdout:
                run_cmd(f"ufw allow {port}/tcp", check=False)
                log_success(f"UFW: Allowed port {port}/tcp.")
            else:
                log_success(f"UFW: Port {port} already allowed.")

    # --- Firewalld ---
    if shutil.which("firewall-cmd"):
        if run_cmd("systemctl is-active firewalld", check=False).returncode == 0:
            active_firewalls += 1
            zone = run_cmd("firewall-cmd --get-default-zone", check=False).stdout.strip() or "public"
            if port == 22:
                if run_cmd(f"firewall-cmd --zone={zone} --query-service=ssh", check=False).returncode != 0:
                    run_cmd(f"firewall-cmd --permanent --zone={zone} --add-service=ssh", check=False)
                    run_cmd("firewall-cmd --reload", check=False)
                    log_success(f"Firewalld: Added 'ssh' service to '{zone}' zone.")
                else:
                    log_success("Firewalld: SSH service already allowed.")
            else:
                if run_cmd(f"firewall-cmd --zone={zone} --query-port={port}/tcp", check=False).returncode != 0:
                    run_cmd(f"firewall-cmd --permanent --zone={zone} --add-port={port}/tcp", check=False)
                    run_cmd("firewall-cmd --reload", check=False)
                    log_success(f"Firewalld: Added {port}/tcp to '{zone}' zone.")
                else:
                    log_success(f"Firewalld: Port {port} already allowed.")

    # --- Raw Iptables ---
    if active_firewalls == 0 and shutil.which("iptables"):
        policy = run_cmd("iptables -S INPUT", check=False).stdout
        if " -P INPUT DROP" in policy or " -P INPUT REJECT" in policy:
            active_firewalls += 1
            if run_cmd(f"iptables -C INPUT -p tcp --dport {port} -j ACCEPT", check=False).returncode != 0:
                run_cmd(f"iptables -I INPUT 1 -p tcp --dport {port} -j ACCEPT", check=False)
                log_success(f"iptables: Inserted ACCEPT rule for port {port}.")
                if shutil.which("iptables-save"):
                    Path("/etc/iptables").mkdir(exist_ok=True)
                    with open("/etc/iptables/iptables.rules", "w") as f:
                        f.write(run_cmd("iptables-save", check=False).stdout)
                    run_cmd("systemctl enable iptables.service", check=False)
            else:
                log_success("iptables: Rule already exists.")
                
    if active_firewalls == 0:
        log_info("No blocking firewalls detected. Port should be open.")

def manage_services(unit: str, unit_type: str):
    """Enable, start, and verify the SSH unit."""
    opposing_unit = "sshd.service" if unit_type == "socket" else "sshd.socket"
    
    if run_cmd(f"systemctl is-active {opposing_unit}", check=False).returncode == 0 or \
       run_cmd(f"systemctl is-enabled {opposing_unit}", check=False).returncode == 0:
        log_info(f"Disabling conflicting unit: {opposing_unit}")
        run_cmd(f"systemctl stop {opposing_unit}", check=False)
        run_cmd(f"systemctl disable {opposing_unit}", check=False)

    if run_cmd(f"systemctl is-active {unit}", check=False).returncode == 0:
        log_success(f"{unit} is already active.")
    else:
        with console.status(f"[bold cyan]Starting {unit}..."):
            run_cmd(f"systemctl enable {unit}", check=False)
            run_cmd(f"systemctl start {unit}", check=False)
            
            if run_cmd(f"systemctl is-active {unit}", check=False).returncode == 0:
                log_success(f"{unit} started successfully.")
            else:
                die(f"Failed to start {unit}. Check: journalctl -xeu {unit}")

def configure_tailscale_autonomous() -> str | None:
    """Detect Tailscale, extract IP, and autonomously configure trust without prompting."""
    if not shutil.which("tailscale"):
        return None
    if run_cmd("systemctl is-active tailscaled", check=False).returncode != 0:
        return None

    ip_out = run_cmd("tailscale ip -4", check=False).stdout.strip()
    if not ip_out:
        return None

    console.print(f"\n[magenta bold]✦ Tailscale Network Detected[/] : {ip_out}")
    log_success("Autonomous Mode: Tailscale automatically trusted for SSH ingress.")
    
    # Trust tailscale interface in firewalld if active (UFW is handled natively by 068_ufw_firewall.sh)
    if shutil.which("firewall-cmd") and run_cmd("systemctl is-active firewalld", check=False).returncode == 0:
        if shutil.which("ip"):
            ts_iface_raw = run_cmd("ip -o link show", check=False).stdout
            iface = "tailscale0"
            for line in ts_iface_raw.splitlines():
                if "tailscale" in line:
                    parts = line.split(": ")
                    if len(parts) >= 2:
                        iface = parts[1].split("@")[0].strip()
                        break
            
            if run_cmd(f"firewall-cmd --zone=trusted --query-interface={iface}", check=False).returncode != 0:
                run_cmd(f"firewall-cmd --permanent --zone=trusted --add-interface={iface}", check=False)
                run_cmd("firewall-cmd --reload", check=False)
                log_success(f"Firewalld: Trusted interface '{iface}'.")
                
    return ip_out

def get_lan_ip() -> str:
    """Use modern iproute2 JSON to deterministically find physical IP."""
    if not shutil.which("ip"):
        return "<IP-NOT-FOUND>"

    with contextlib.suppress(Exception):
        out = run_cmd("ip -j -4 addr show scope global", check=True).stdout
        data = json.loads(out)
        
        excludes = ("docker", "br-", "vbox", "virbr", "waydroid", "tun", "warp", "wg", "tailscale")
        
        for iface in data:
            name = iface.get("ifname", "")
            if name.startswith(("e", "w")) and not any(x in name for x in excludes):
                addrs = iface.get("addr_info", [])
                if addrs:
                    return addrs[0].get("local", "")
                    
        route_out = run_cmd("ip -j -4 route show default", check=True).stdout
        route_data = json.loads(route_out)
        if route_data:
            def_iface = route_data[0].get("dev", "")
            for iface in data:
                if iface.get("ifname") == def_iface:
                    addrs = iface.get("addr_info", [])
                    if addrs:
                        return addrs[0].get("local", "")

    return "<IP-NOT-FOUND>"

# --- Application Entry Point ---

def main():
    parser = argparse.ArgumentParser(description="Arch Linux SSH Bootstrapper (Rich Edition)")
    parser.add_argument("-a", "--auto", action="store_true", help="Run non-interactively")
    args = parser.parse_args()

    user = get_real_user()

    # Initial Header
    console.print(Panel.fit(
        f"[bold white]Targeting Arch Linux / User:[/] [bold cyan]{user}[/]\n"
        "[dim]Provisions: OpenSSH · Firewalls · Tailscale · Systemd Sockets[/]",
        title="[bold green]Arch Linux SSH Provisioning v7.0[/]",
        border_style="blue",
        padding=(1, 4)
    ))

    # Exactly ONE prompt in the entire script
    if not args.auto:
        if not Confirm.ask("Enable secure SSH access to this machine?", default=True):
            log_warn("Aborted by user.")
            sys.exit(0)
            
    print()

    # Pipeline
    install_openssh()
    generate_host_keys()
    
    config_text = validate_sshd_config()
    unit, unit_type, port = detect_unit_and_port(config_text)
    
    analyze_security_warnings(config_text, user)
    check_port_conflicts(port)
    configure_firewalls(port)
    manage_services(unit, unit_type)
    
    # Networking (Fully Autonomous)
    ts_ip = configure_tailscale_autonomous()
    lan_ip = get_lan_ip()

    # Final Output Table (Dual-IP Rendering)
    print()
    table = Table(title="[bold]SSH Setup Complete[/]", show_header=False, border_style="green", padding=(0, 2))
    table.add_column("Key", style="bold cyan", justify="right")
    table.add_column("Value", style="white")
    
    if ts_ip:
        table.add_row("Tailscale IP", f"[bold magenta]{ts_ip}[/]")
    if lan_ip and lan_ip != "<IP-NOT-FOUND>":
        table.add_row("Local LAN IP", f"[bold green]{lan_ip}[/]")
    
    table.add_row("Port", f"[bold green]{port}[/]")
    table.add_row("User", f"[bold green]{user}[/]")
    if unit_type == "socket":
        table.add_row("Activation", "[italic]socket (on-demand)[/]")
    
    console.print(table, justify="center")
    
    # Format the Connection Command (Prefer Tailscale if available, fallback to LAN)
    primary_target = ts_ip if ts_ip else lan_ip
    if not primary_target or primary_target == "<IP-NOT-FOUND>":
        primary_target = "<UNKNOWN-IP>"
        
    conn_cmd = f"ssh {user}@{primary_target}"
    if port != 22:
        conn_cmd = f"ssh -p {port} {user}@{primary_target}"

    cmd_panel = Panel(
        Text(conn_cmd, justify="center", style="bold magenta"), 
        title="Connect from another device", 
        border_style="magenta",
        width=60
    )
    console.print(cmd_panel, justify="center")
    
    # Generate and display a highly-compatible URI QR Code
    if primary_target != "<UNKNOWN-IP>":
        ssh_uri = f"ssh://{user}@{primary_target}:{port}"
        # -t UTF8 creates a clean block-character QR code. -m 2 sets a margin of 2 blocks.
        qr_out = run_cmd(f"qrencode -t UTF8 -m 2 '{ssh_uri}'", check=False)
        
        if qr_out.returncode == 0 and qr_out.stdout:
            # Force black text on white background for maximum camera scanner readability
            qr_text = Text(qr_out.stdout, style="black on white")
            qr_panel = Panel(
                Align.center(qr_text),
                title="[bold cyan]Scan to Connect (Mobile / Termius)[/]",
                border_style="cyan",
                width=60
            )
            console.print(qr_panel, justify="center")

    print()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]✖ Setup interrupted by user.[/]")
        sys.exit(130)

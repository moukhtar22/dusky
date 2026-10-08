"""Shared setup and on-demand diagnostics for the two WayVNC services."""

import ipaddress
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
MASTER = "dusky_vnc_desktop.service"
PHONE = "dusky_vnc_display.service"
DESKTOP_PORT = 5902  # 5900 is commonly occupied by a local QEMU VNC console.
PHONE_PORT = 5901
FIREWALL_RULE = ("allow", f"{PHONE_PORT},{DESKTOP_PORT}/tcp", "comment", "Dusky VNC")


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check,
                          stdin=subprocess.DEVNULL, timeout=15,
                          env={**os.environ, "LC_ALL": "C"})


def message(value: str, *, error: bool = False) -> None:
    # The service replaces Python with WayVNC; Rich is only needed by the CLI.
    try:
        from rich.console import Console
    except ModuleNotFoundError:
        print(value, file=sys.stderr if error else sys.stdout)
        return
    Console(stderr=error).print(value, markup=False, highlight=False,
                                style="red" if error else None)


def atomic_write(path: Path, content: str, mode: int = 0o600) -> bool:
    if path.exists() and path.read_text() == content:
        path.chmod(mode)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        replacement = Path(directory) / path.name
        replacement.write_text(content)
        replacement.chmod(mode)
        replacement.replace(path)
    return True


def write_config(config: Path, key: Path, cert: Path, port: int) -> bool:
    config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    names = sorted({"localhost", socket.gethostname()})
    ips = sorted({"127.0.0.1", *[ip for _, ip in addresses()]})
    valid = False
    if key.is_file() and cert.is_file():
        expiry = run("openssl", "x509", "-checkend", "2592000", "-noout", "-in", str(cert), check=False)
        public_key = run("openssl", "pkey", "-in", str(key), "-pubout", check=False)
        cert_key = run("openssl", "x509", "-in", str(cert), "-pubkey", "-noout", check=False)
        valid = expiry.returncode == public_key.returncode == cert_key.returncode == 0 and public_key.stdout == cert_key.stdout
        if valid:
            # VeNCrypt clients validate the connection address, including LAN
            # and Tailscale IPs. Retain certificates while all names still fit.
            valid = all(run("openssl", "x509", "-in", str(cert), "-noout", option, value,
                            check=False).returncode == 0
                        for option, values in (("-checkhost", names), ("-checkip", ips))
                        for value in values)
    if not valid:
        with tempfile.TemporaryDirectory(dir=config.parent) as directory:
            new_key, new_cert = Path(directory) / "key.pem", Path(directory) / "cert.pem"
            # Traditional RSA PEM is required by NeatVNC's RSA-AES reader.
            if key.is_file() and run("openssl", "rsa", "-in", str(key), "-check", "-noout", check=False).returncode == 0:
                shutil.copyfile(key, new_key)  # Keep the RSA identity when refreshing TLS names.
            else:
                run("openssl", "genrsa", "-traditional", "-out", str(new_key), "3072")
            run("openssl", "req", "-new", "-x509", "-key", str(new_key), "-out", str(new_cert),
                "-days", "3650", "-sha256", "-subj", "/CN=WayVNC",
                "-addext", "subjectAltName=" + ",".join([*("DNS:" + name for name in names), *("IP:" + ip for ip in ips)]))
            new_key.chmod(0o600)
            new_cert.chmod(0o600)
            new_key.replace(key)
            new_cert.replace(cert)
    key.chmod(0o600)
    changed = atomic_write(config, (
        f"address=0.0.0.0\nport={port}\nenable_auth=true\nenable_pam=true\n"
        f"rsa_private_key_file={key}\nprivate_key_file={key}\ncertificate_file={cert}\n"
    ))
    return changed or not valid


def session() -> dict | None:
    result = run("hyprctl", "instances", "-j", check=False)
    if result.returncode:
        return None
    candidates = sorted(json.loads(result.stdout), key=lambda item: item.get("time", 0), reverse=True)
    preferred = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    candidates.sort(key=lambda item: item.get("instance") != preferred)
    for item in candidates:
        name = item.get("wl_socket", "")
        if not name:
            continue
        try:
            info = (RUNTIME / name).stat()
        except FileNotFoundError:
            continue  # A session may exit between discovery and inspection.
        if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
            return item
    return None


def wait_session() -> dict:
    while not (current := session()):
        time.sleep(2)
    return current


def exec_wayvnc(current: dict, config: Path, control: Path, *args: str) -> None:
    env = {**os.environ, "XDG_RUNTIME_DIR": str(RUNTIME),
           "WAYLAND_DISPLAY": current["wl_socket"],
           "HYPRLAND_INSTANCE_SIGNATURE": current["instance"]}
    os.execve("/usr/bin/wayvnc", ["wayvnc", "-C", str(config), "-S", str(control), *args], env)


def script_command(script: Path, action: str) -> str:
    try:
        name = "%h/" + script.resolve().relative_to(HOME).as_posix().replace("%", "%%")
    except ValueError:
        name = str(script.resolve()).replace("%", "%%")
    # Stable Arch interpreter path avoids restarts when setup is invoked through
    # different Python aliases. systemd needs literal dollars escaped as well.
    return " ".join(json.dumps(part.replace("$", "$$"), ensure_ascii=False)
                    for part in ("/usr/bin/python3", name, action))


def ensure_dependencies(requirements: dict[str, tuple[str, ...]]) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Run setup as the desktop user, without sudo")
    missing = [package for package, commands in requirements.items()
               if any(not shutil.which(command) for command in commands)]
    if importlib.util.find_spec("rich") is None:
        missing.append("python-rich")
    if missing:
        message("Installing missing packages: " + ", ".join(missing))
        subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", *missing], check=True)
    unavailable = [command for commands in requirements.values() for command in commands if not shutil.which(command)]
    if unavailable or importlib.util.find_spec("rich") is None:
        raise RuntimeError("Dependency installation incomplete: " + ", ".join(unavailable or ["python-rich"]))


def prepare() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Run setup as the desktop user, without sudo")
    ensure_dependencies({"wayvnc": ("wayvnc", "wayvncctl"), "hyprland": ("hyprctl",),
                         "openssl": ("openssl",), "systemd": ("systemctl",), "iproute2": ("ip",)})
    if not Path("/etc/pam.d/wayvnc").is_file():
        raise RuntimeError("WayVNC PAM profile is missing; reinstall the wayvnc package")
    if not session():
        raise RuntimeError("Start a Hyprland desktop session before setup")


def show_clients(control: Path) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    clients = control_data(control, "client-list")
    if clients is None:
        raise RuntimeError("WayVNC control is unavailable; use --diagnose or --reconnect")
    table = Table(title="Connected VNC viewers")
    for title in ("ID", "Address", "User"):
        table.add_column(title)
    for client in clients:
        table.add_row(Text(str(client.get("id", ""))), Text(str(client.get("address", ""))), Text(str(client.get("username", ""))))
    Console().print(table)
    message("Disconnect one: --disconnect CLIENT_ID    Disconnect all: --disconnect-all")
    message("Saved connection entries live in the receiving device's viewer; remove them there if needed.")


def disconnect_clients(control: Path, identifier: str | None = None) -> None:
    clients = control_data(control, "client-list")
    if clients is None:
        raise RuntimeError("WayVNC control is unavailable; use --diagnose or --reconnect")
    selected = [client for client in clients if identifier is None or str(client.get("id")) == identifier]
    if identifier is not None and not selected:
        raise RuntimeError("Viewer ID not found; use --clients")
    for client in selected:
        run("wayvncctl", "-S", str(control), "client-disconnect", str(client["id"]))
    message(f"Disconnected {len(selected)} viewer(s). Reopen the connection in the receiving device's viewer.")


def show_diagnostics(unit: str, port: int, control: Path) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    table = Table(title="VNC diagnostics")
    table.add_column("Check")
    table.add_column("Result")
    for label, command in (
        ("Master service", ("systemctl", "--user", "is-active", MASTER)),
        ("Master startup", ("systemctl", "--user", "is-enabled", MASTER)),
        ("Selected service", ("systemctl", "--user", "is-active", unit)),
        ("Wi-Fi/default route", ("ip", "-4", "route", "show", "default")),
    ):
        table.add_row(label, Text(run(*command, check=False).stdout.strip() or "Unavailable"))
    table.add_row(f"RFB handshake ({port})", "Responding" if rfb_ready(port) else "Unavailable")
    outputs = control_data(control, "output-list")
    captured = [item.get("name", "Unknown") for item in outputs or [] if item.get("captured")]
    table.add_row("WayVNC control", "Responding" if outputs is not None else "Unavailable")
    table.add_row("Captured monitor", Text(", ".join(captured) or "None"))
    clients = control_data(control, "client-list")
    table.add_row("Connected viewers", str(len(clients)) if clients is not None else "Unavailable")
    table.add_row("Linux login", Text(pwd.getpwuid(os.getuid()).pw_name))
    Console().print(table)
    message("Stuck session: --reconnect restarts this display; --disconnect-all only disconnects its viewers.")
    message("Timeout: rerun --setup to repair UFW allowances; check Wi-Fi client isolation on the router.")
    message("Login failure: use the displayed Linux username and Linux account password.")
    message("Separate display looks black: move a window onto its active workspace.")
    message(f"Detailed logs: journalctl --user -u {unit} -n 40 --no-pager")


def parse_action(description: str, actions: tuple[str, ...], *, orientation: bool = False):
    import argparse
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("action", nargs="?", choices=actions)
    parser.add_argument("value", nargs="?", choices=("landscape", "portrait"))
    group = parser.add_mutually_exclusive_group()
    help_text = {"setup": "Install missing packages, configure and start VNC",
                 "status": "Show readiness and connection instructions",
                 "stop": "Disable and stop this display",
                 "reconnect": "Restart this display and restore its capture",
                 "clients": "List connected viewers and their IDs",
                 "diagnose": "Check services, protocol, capture and network route",
                 "offline": "Prepare a hotspot on an unused second Wi-Fi adapter",
                 "remote": "Install/configure optional Tailscale access"}
    for name in actions:
        if name not in {"serve", "cleanup", "orientation"}:
            group.add_argument("--" + name, dest="flag_action", action="store_const", const=name, help=help_text.get(name))
    group.add_argument("--disconnect", metavar="CLIENT_ID", help="Disconnect a viewer ID from --clients")
    group.add_argument("--disconnect-all", action="store_true", help="Disconnect every viewer of this display")
    if orientation:
        group.add_argument("--orientation", dest="flag_orientation", choices=("landscape", "portrait"))
    args = parser.parse_args()
    value = getattr(args, "flag_orientation", None) or args.value
    flagged = args.flag_action or getattr(args, "flag_orientation", None) or args.disconnect is not None or args.disconnect_all
    if args.action and flagged:
        parser.error("choose a positional action or an action flag")
    action = args.action or args.flag_action or ("orientation" if getattr(args, "flag_orientation", None) else "disconnect" if args.disconnect is not None or args.disconnect_all else "setup")
    if value and action != "orientation":
        parser.error("an orientation value requires orientation")
    if os.geteuid() == 0:
        parser.error("run as the desktop user, without sudo")
    return action, value, args.disconnect


def configure_firewall() -> None:
    if shutil.which("ufw"):
        subprocess.run(["sudo", sys.executable, str(Path(__file__).resolve()), "firewall"], check=True)


def firewall_worker() -> None:
    """Put the VNC allowance before user denies without resetting the firewall."""
    if os.geteuid() != 0:
        raise RuntimeError("Firewall configuration requires sudo")

    def rules() -> list[list[str]]:
        return [shlex.split(line)[1:] for line in run("ufw", "show", "added").stdout.splitlines()
                if line.startswith("ufw ")]

    existing = rules()
    if existing and existing[0] == list(FIREWALL_RULE):
        return
    # UFW skips insertion of equivalent rules already present. Normalize its
    # action/comment first, then remove and prepend that exact two-port rule.
    run("ufw", *FIREWALL_RULE)
    run("ufw", "--force", "delete", *FIREWALL_RULE)
    run("ufw", "prepend", *FIREWALL_RULE)
    existing = rules()
    if not existing or existing[0] != list(FIREWALL_RULE):
        raise RuntimeError("UFW did not prioritize the VNC allowance")


def install_unit(unit: Path, content: str) -> bool:
    changed = atomic_write(unit, content, 0o644)
    if changed:
        run("systemctl", "--user", "daemon-reload")
    enabled = run("systemctl", "--user", "is-enabled", unit.name, check=False).stdout.strip() == "enabled"
    if changed and enabled:
        run("systemctl", "--user", "reenable", unit.name)
    elif not enabled:
        run("systemctl", "--user", "enable", unit.name)
    return changed


def control_data(control: Path, command: str) -> list[dict] | None:
    result = run("wayvncctl", "-S", str(control), "--json", command, check=False)
    if result.returncode:
        return None
    try:
        data = json.loads(result.stdout)
        return data if isinstance(data, list) and all(isinstance(item, dict) for item in data) else None
    except ValueError:
        return None


def rfb_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            deadline = time.monotonic() + 1
            greeting = bytearray()
            while len(greeting) < 12:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                connection.settimeout(remaining)
                chunk = connection.recv(12 - len(greeting))
                if not chunk:
                    return False
                greeting.extend(chunk)
        return greeting == b"RFB 003.008\n"
    except OSError:
        return False


def wait_ready(predicate, unit: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise RuntimeError(f"Not ready after 15 seconds. Check: journalctl --user -u {unit} -n 30 --no-pager")


def addresses() -> list[tuple[str, str]]:
    links = json.loads(run("ip", "-j", "-4", "addr", "show", "scope", "global").stdout)
    routes = json.loads(run("ip", "-j", "-4", "route", "show", "default").stdout)
    preferred = min(routes, key=lambda item: item.get("metric", 0)).get("dev") if routes else None
    found = []
    for link in links:
        iface = link["ifname"]
        # VM/container bridges do not give other devices a directly usable LAN address.
        net = Path("/sys/class/net") / iface
        physical = (net / "device").exists() or (net / "phy80211").exists()
        if "UP" not in link.get("flags", []) or not (physical or iface == preferred or iface == "tailscale0"):
            continue
        for value in link.get("addr_info", []):
            ip = ipaddress.ip_address(value["local"])
            if not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified):
                found.append((iface, str(ip)))
    found.sort(key=lambda item: (item[0] != preferred, item[0], item[1]))
    return found


def show_status(unit: str, port: int, control: Path, ready: bool, description: str) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    console = Console()
    state = run("systemctl", "--user", "is-active", unit, check=False).stdout.strip() or "inactive"
    enabled = run("systemctl", "--user", "is-enabled", unit, check=False).stdout.strip()
    label = "Ready" if state == "active" and ready else "Off" if state == "inactive" else "Not ready"
    console.print(Text(f"{description}: {label} ({state}, {enabled})", style="bold green" if label == "Ready" else "yellow"))
    table = Table(title="Connection addresses (LAN or Tailscale)")
    table.add_column("Network")
    table.add_column("Address", style="bold cyan")
    ips = addresses()
    for iface, ip in ips:
        table.add_row(Text("Tailscale" if iface == "tailscale0" else iface), Text(f"{ip}:{port}"))
    console.print(table)
    if not ips:
        console.print(Text("No usable IPv4 network address. Connect to Wi-Fi or Ethernet and rerun status."))
    username = pwd.getpwuid(os.getuid()).pw_name
    address = f"{ips[0][1]}:{port}" if ips else "an address from the table after connecting to Wi-Fi"
    tiger_address = f"{ips[0][1]}::{port}" if ips else f"SERVER_IP::{port}"
    guide = (
        "1. Install a VNC viewer on the receiving phone, tablet or PC.\n"
        "   iPhone/Android: RealVNC Viewer (RVNC Viewer), from App Store/Google Play.\n"
        "   Linux/Wayland: Remmina with the VNC protocol; run vnc_viewer.py on the receiving PC.\n"
        "   Manual Arch install: sudo pacman -S --needed remmina libvncserver\n"
        "   Windows/macOS: TigerVNC. Linux TigerVNC requires an X11 display/XWayland.\n"
        "   Downloads: https://remmina.org/ | https://tigervnc.org/ | https://www.realvnc.com/en/connect/download/viewer/\n"
        "2. Use the same Wi-Fi/Ethernet network, or connect both devices to the same Tailscale tailnet.\n"
        "   On iPhone, allow Local Network access. For remote access, choose the Tailscale address.\n"
        f"3. Add the server manually in the viewer: {address}\n"
        "   Remmina: select VNC and use IP:port. Or run: ./vnc_viewer.py IP:port\n"
        f"   TigerVNC uses {tiger_address}; a separate Port field should contain {port}.\n"
        f"4. Sign in as {username} with this Linux server's account password.\n"
        "   Confirm the server identity if asked. No browser/PIN setup is needed.\n"
        "5. Local streaming needs no internet; downloads and remote Tailscale access need internet."
    )
    if port == PHONE_PORT:
        guide += "\nThis is a separate monitor: an empty workspace may look black. Move a window onto it."
    else:
        guide += "\nThis shares your desktop. For a separate virtual monitor, run second_display.py."
    console.print(Panel(Text(guide), title="Connect another device (when Ready)", border_style="cyan"))
    if ready and state == "active":
        clients = control_data(control, "client-list")
        console.print(Text(f"Connected viewers: {len(clients)}" if clients is not None else "Connected viewers: unavailable"))
    elif state != "inactive":
        console.print(Text(f"Check: journalctl --user -u {unit} -n 30 --no-pager"))
        raise RuntimeError(f"{description} is not ready")
    console.print(Text(f"All VNC: systemctl --user {'disable' if state == 'active' else 'enable'} --now {MASTER}"))


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["firewall"]:
            raise RuntimeError("Internal helper: expected firewall")
        firewall_worker()
    except subprocess.CalledProcessError as error:
        print(f"Firewall setup failed: {error.stderr.strip() or error.stdout.strip() or error}", file=sys.stderr)
        sys.exit(1)
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"Firewall setup failed: {error}", file=sys.stderr)
        sys.exit(1)

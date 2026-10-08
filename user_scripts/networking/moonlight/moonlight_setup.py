#!/usr/bin/env python3
"""Stream a separate Hyprland display to another device through Sunshine/Moonlight.

Run ``orientation portrait`` or ``orientation landscape`` to switch its shape.
"""

import argparse
import http.client
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import shutil
import shlex
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
SCRIPT = Path(__file__).resolve()
UNIT_NAME = "dusky_moonlight_display.service"
UNIT = CONFIG_HOME / "systemd/user" / UNIT_NAME
CONFIG = CONFIG_HOME / "sunshine-moonlight/sunshine.conf"
STATE = RUNTIME / "dusky-moonlight-display.json"
OUTPUT = "DUSKY-MOONLIGHT"
LANDSCAPE_SIZE = (1280, 720)
PREFERENCES = CONFIG_HOME / "dusky/settings/remote/moonlight_display.json"
SUNSHINE_PORT = 47989
FIREWALL_RULES = (
    ("allow", "47984,47989,48010/tcp", "comment", "Dusky Moonlight display"),
    ("allow", "47998:48000/udp", "comment", "Dusky Moonlight display"),
)
IPHONE_USB_PROFILE = "iPhone USB local"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check,
                          stdin=subprocess.DEVNULL, timeout=15,
                          env={**os.environ, "LC_ALL": "C"})


def message(value: str, *, error: bool = False) -> None:
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


def save_preferences(values: dict) -> None:
    atomic_write(PREFERENCES, json.dumps(values, indent=2) + "\n")


def preferences() -> dict:
    if not PREFERENCES.exists():
        return {"orientation": "landscape"}
    try:
        values = json.loads(PREFERENCES.read_text())
    except ValueError as error:
        raise RuntimeError(f"Invalid JSON in {PREFERENCES}: {error}") from error
    if not isinstance(values, dict) or values.get("orientation") not in {"landscape", "portrait"}:
        raise RuntimeError(f"Set orientation to landscape or portrait in {PREFERENCES}")
    return values


def display_size(value: str | None = None) -> tuple[int, int]:
    width, height = LANDSCAPE_SIZE
    return (width, height) if (value or preferences()["orientation"]) == "landscape" else (height, width)


def hypr(instance: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run("hyprctl", "--instance", instance, *args, check=check)


def session() -> dict | None:
    result = run("hyprctl", "instances", "-j", check=False)
    if result.returncode:
        return None
    candidates = sorted(json.loads(result.stdout), key=lambda value: value.get("time", 0), reverse=True)
    preferred = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    candidates.sort(key=lambda item: item.get("instance") != preferred)
    for item in candidates:
        name = item.get("wl_socket", "")
        if not name:
            continue
        try:
            info = (RUNTIME / name).stat()
        except FileNotFoundError:
            continue
        if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
            return item
    return None


def monitors(instance: str) -> list[dict]:
    return json.loads(hypr(instance, "-j", "monitors").stdout)


def ensure_dependencies() -> None:
    requirements = {"hyprland": "hyprctl", "systemd": "systemctl", "iproute2": "ip",
                    "libva-utils": "vainfo", "xdg-utils": "xdg-open"}
    missing = [package for package, command in requirements.items() if not shutil.which(command)]
    if importlib.util.find_spec("rich") is None:
        missing.append("python-rich")
    if missing:
        message("Installing missing packages: " + ", ".join(missing))
        subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", *missing], check=True)
    unavailable = [command for command in requirements.values() if not shutil.which(command)]
    if unavailable or importlib.util.find_spec("rich") is None:
        raise RuntimeError("Dependency installation incomplete: " + ", ".join(unavailable or ["python-rich"]))


def aur_helper() -> str:
    helper = shutil.which("paru") or shutil.which("yay")
    if helper:
        if run("pacman", "-Q", "base-devel", check=False).returncode or not shutil.which("git"):
            subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", "git", "base-devel"], check=True)
        return helper
    message("Installing Paru for the missing AUR package; builds run as your desktop user")
    subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", "git", "base-devel"], check=True)
    with tempfile.TemporaryDirectory(prefix="dusky-paru-") as directory:
        source = Path(directory) / "paru"
        subprocess.run(["git", "clone", "https://aur.archlinux.org/paru.git", str(source)], check=True)
        subprocess.run(["makepkg", "-si", "--needed", "--noconfirm"], cwd=source, check=True)
    helper = shutil.which("paru")
    if not helper:
        raise RuntimeError("Paru installation did not provide its executable")
    return helper


def sunshine_package(package: Path | None) -> None:
    if shutil.which("sunshine"):
        return
    if package:
        if not package.is_file():
            raise RuntimeError(f"Sunshine package not found: {package}")
        subprocess.run(["sudo", "pacman", "-U", "--needed", "--noconfirm", str(package)], check=True)
        if not shutil.which("sunshine"):
            raise RuntimeError("The supplied package did not install the Sunshine executable")
        return
    if run("pacman", "-Si", "sunshine", check=False).returncode == 0:
        message("Installing Sunshine from the configured pacman repository")
        subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", "sunshine"], check=True)
    else:
        helper = aur_helper()
        # The binary package includes upstream-built encoders without a CUDA build toolchain.
        message("Installing Sunshine using the AUR sunshine-bin package")
        subprocess.run([helper, "-S", "--needed", "--noconfirm", "sunshine-bin"], check=True)
    if not shutil.which("sunshine"):
        raise RuntimeError("Sunshine installation did not provide its executable")


def configure_firewall() -> None:
    if shutil.which("ufw"):
        subprocess.run(["sudo", "/usr/bin/python3", str(SCRIPT), "firewall"], check=True)


def firewall_worker() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Firewall configuration requires sudo")

    def rules() -> list[tuple[str, ...]]:
        return [tuple(shlex.split(line)[1:]) for line in run("ufw", "show", "added").stdout.splitlines()
                if line.startswith("ufw ")]

    if rules()[:len(FIREWALL_RULES)] == list(FIREWALL_RULES):
        return
    for rule in reversed(FIREWALL_RULES):
        # Normalize equivalent rules before moving them: UFW skips inserting
        # duplicates and cannot update their comments through prepend.
        run("ufw", *rule)
        run("ufw", "--force", "delete", *rule)
        run("ufw", "prepend", *rule)
    if rules()[:len(FIREWALL_RULES)] != list(FIREWALL_RULES):
        raise RuntimeError("UFW did not prioritize the Moonlight allowances")


def setup_iphone_usb() -> None:
    """Keep iPhone USB tethering local, even when the phone has no internet."""
    if not shutil.which("nmcli"):
        if os.geteuid() == 0:
            raise RuntimeError("Run USB setup as the desktop user, without sudo")
        subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", "networkmanager"], check=True)
    settings = (
        "connection.interface-name", "",
        "match.driver", "ipheth",
        "connection.autoconnect", "yes",
        "connection.autoconnect-priority", "999",
        "ipv4.method", "auto",
        "ipv4.never-default", "yes",
        "ipv4.ignore-auto-dns", "yes",
        "ipv6.method", "disabled",
    )
    fields = ("connection.type,connection.interface-name,match.driver,connection.autoconnect,"
              "connection.autoconnect-priority,ipv4.method,ipv4.never-default,"
              "ipv4.ignore-auto-dns,ipv6.method")
    existing = run("nmcli", "-g", fields, "connection", "show", IPHONE_USB_PROFILE, check=False)
    values = existing.stdout.splitlines()
    changed = False
    if existing.returncode:
        run("nmcli", "connection", "add", "type", "ethernet", "ifname", "*",
            "con-name", IPHONE_USB_PROFILE, "autoconnect", "yes", "--", *settings)
        changed = True
    elif not values or values[0] != "802-3-ethernet":
        raise RuntimeError(f"A non-Ethernet profile already uses the name {IPHONE_USB_PROFILE}")
    elif values != ["802-3-ethernet", "", "ipheth", "yes", "999",
                    "auto", "yes", "yes", "disabled"]:
        run("nmcli", "connection", "modify", IPHONE_USB_PROFILE, *settings)
        changed = True
    for device in Path("/sys/class/net").iterdir():
        driver = device / "device/driver"
        if not driver.is_symlink() or driver.resolve().name != "ipheth":
            continue
        if not (device / "carrier").exists() or (device / "carrier").read_text().strip() != "1":
            continue
        active = run("nmcli", "-g", "GENERAL.CONNECTION", "device", "show", device.name).stdout.strip()
        if active != IPHONE_USB_PROFILE or changed:
            run("nmcli", "--wait", "10", "connection", "up", IPHONE_USB_PROFILE, "ifname", device.name)
    message("iPhone USB profile prepared: local traffic only; Wi-Fi remains the default route")


def prefer_vaapi() -> str | None:
    if not shutil.which("vainfo"):
        return None
    current = session()
    if not current:
        return None
    active = {item["name"] for item in monitors(current["instance"])}
    for connector in Path("/sys/class/drm").glob("card*-*"):
        if not any(connector.name.endswith(f"-{name}") for name in active):
            continue
        if not (connector / "status").is_file() or (connector / "status").read_text().strip() != "connected":
            continue
        card = connector.name.split("-", 1)[0]
        vendor = Path("/sys/class/drm") / card / "device/vendor"
        if vendor.is_file() and vendor.read_text().strip() in {"0x8086", "0x1002"}:
            for render in (vendor.parent / "drm").glob("renderD*"):
                probe = run("vainfo", "--display", "drm", "--device", f"/dev/dri/{render.name}", check=False)
                if probe.returncode == 0 and any(
                    line.strip().startswith("VAProfileH264High") and "VAEntrypointEncSlice" in line
                    for line in probe.stdout.splitlines()
                ):
                    return f"/dev/dri/{render.name}"
    return None


def write_config() -> bool:
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    (CONFIG.parent / "credentials").mkdir(mode=0o700, exist_ok=True)
    apps = CONFIG.parent / "apps.json"
    apps_created = not apps.exists()
    if apps_created:
        atomic_write(apps, json.dumps({"env": {}, "apps": [{"name": "Desktop", "image-path": "desktop.png"}]}, indent=2) + "\n")
    wanted = {
        "capture": "wlr",
        "output_name": OUTPUT,
        "port": str(SUNSHINE_PORT),
        "system_tray": "disabled",
        "stream_audio": "disabled",
        "file_apps": str(apps),
        "credentials_file": str(CONFIG.parent / "sunshine_state.json"),
        "file_state": str(CONFIG.parent / "sunshine_state.json"),
        "log_path": str(CONFIG.parent / "sunshine.log"),
        "pkey": str(CONFIG.parent / "credentials/cakey.pem"),
        "cert": str(CONFIG.parent / "credentials/cacert.pem"),
    }
    adapter = prefer_vaapi()
    wanted["encoder"] = "vaapi" if adapter else None
    wanted["adapter_name"] = adapter
    current = CONFIG.read_text().splitlines() if CONFIG.exists() else []
    found = set()
    lines = []
    for line in current:
        key = line.split("=", 1)[0].strip()
        if key in wanted:
            if key in found:
                continue
            found.add(key)
            if wanted[key] is None:
                continue
            line = f"{key} = {wanted[key]}"
        lines.append(line)
    lines.extend(f"{key} = {value}" for key, value in wanted.items() if key not in found and value is not None)
    content = "\n".join(lines) + "\n"
    return atomic_write(CONFIG, content) or apps_created


def unit_content() -> str:
    try:
        script = f"%h/{SCRIPT.relative_to(HOME).as_posix().replace('%', '%%')}"
    except ValueError:
        script = str(SCRIPT).replace("%", "%%")
    script = json.dumps(script.replace("$", "$$"), ensure_ascii=False)
    python = '"/usr/bin/python3"'
    return (
        "[Unit]\nDescription=Secondary display over Sunshine/Moonlight\n"
        "After=graphical-session.target\nPartOf=graphical-session.target\n"
        "StartLimitIntervalSec=0\n\n"
        "[Service]\nType=exec\n"
        f"ExecStart={python} {script} serve\n"
        f"ExecStopPost={python} {script} cleanup\n"
        "Restart=always\nRestartSec=5\nRestartSteps=5\nRestartMaxDelaySec=30\n\n"
        "[Install]\nWantedBy=default.target graphical-session.target\n"
    )


def listening() -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", SUNSHINE_PORT, timeout=1)
    try:
        connection.request("GET", "/serverinfo?uniqueid=dusky-status")
        response = connection.getresponse()
        if response.status != 200:
            return False
        document = ET.fromstring(response.read(65536))
        return (document.tag == "root" and document.get("status_code") == "200"
                and document.findtext("state") in {"SUNSHINE_SERVER_FREE", "SUNSHINE_SERVER_BUSY"})
    except (OSError, http.client.HTTPException, ET.ParseError):
        return False
    finally:
        connection.close()


def ready() -> bool:
    current = session()
    if not current or not listening():
        return False
    width, height = display_size()
    return any(item["name"] == OUTPUT and (item["width"], item["height"]) == (width, height)
               for item in monitors(current["instance"]))


def setup(package: Path | None) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Run setup as the desktop user, without sudo")
    ensure_dependencies()
    if not session():
        raise RuntimeError("Start a Hyprland desktop session before setup")
    preferences()
    sunshine_package(package)
    configure_firewall()
    config_changed = write_config()
    duplicate_pairings = repair_duplicate_pairings(repair=False)
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    content = unit_content()
    unit_changed = atomic_write(UNIT, content, 0o644)
    if unit_changed:
        run("systemctl", "--user", "daemon-reload")
    enabled = run("systemctl", "--user", "is-enabled", UNIT_NAME, check=False).stdout.strip() == "enabled"
    if unit_changed and enabled:
        run("systemctl", "--user", "reenable", UNIT_NAME)
    elif not enabled:
        run("systemctl", "--user", "enable", UNIT_NAME)
    state = run("systemctl", "--user", "is-active", UNIT_NAME, check=False).stdout.strip() or "inactive"
    active = state == "active"
    if not active:
        run("systemctl", "--user", "start", UNIT_NAME)
    elif config_changed or unit_changed or duplicate_pairings or not ready():
        run("systemctl", "--user", "restart", UNIT_NAME)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if ready():
            break
        time.sleep(0.25)
    else:
        raise RuntimeError(f"Moonlight display did not start; check journalctl --user -u {UNIT_NAME}")
    status()
    open_pairing_page(only_unpaired=True)


def repair_duplicate_pairings(*, repair: bool = True) -> int:
    """Inspect legacy duplicates; write repairs only before Sunshine starts."""
    path = CONFIG.parent / "sunshine_state.json"
    if not path.exists():
        return 0
    original = path.read_text()
    data = json.loads(original)
    devices = data.get("root", {}).get("named_devices", [])
    retained = []
    identities = {}
    for device in devices:
        try:
            identity = ssl.PEM_cert_to_DER_cert(device["cert"])
        except (KeyError, ValueError):
            retained.append(device)
            continue
        if identity not in identities:
            identities[identity] = len(retained)
            retained.append(device)
        else:
            index = identities[identity]
            previous = retained[index]
            # A disabled record must not become enabled during maintenance.
            if str(previous.get("enabled", True)).lower() != "false":
                retained[index] = device
    removed = len(devices) - len(retained)
    if removed and repair:
        backup = path.with_suffix(".json.before-dedup")
        if not backup.exists():
            atomic_write(backup, original)
        data["root"]["named_devices"] = retained
        atomic_write(path, json.dumps(data, indent=2) + "\n")
        message(f"Repaired {removed} duplicate paired-client records; backup: {backup}")
    return removed


def serve() -> None:
    if not CONFIG.is_file():
        raise RuntimeError("Run setup first")
    repair_duplicate_pairings()
    width, height = display_size()
    while not (current := session()):
        time.sleep(2)
    instance = current["instance"]
    existing = next((item for item in monitors(instance) if item["name"] == OUTPUT), None)
    if existing:
        previous = json.loads(STATE.read_text()) if STATE.exists() else {}
        if previous.get("instance") != instance:
            raise RuntimeError(f"Output {OUTPUT} already exists and is not owned by this service")
        hypr(instance, "output", "remove", OUTPUT)
    atomic_write(STATE, json.dumps({"instance": instance}))
    try:
        hypr(instance, "output", "create", "headless", OUTPUT)
        rule = (f'hl.monitor({{ output = "{OUTPUT}", mode = "{width}x{height}@60", '
                'position = "auto-right", scale = 1, disabled = false })')
        hypr(instance, "eval", rule)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            monitor = next((item for item in monitors(instance) if item["name"] == OUTPUT), None)
            if monitor and (monitor["width"], monitor["height"]) == (width, height):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(f"Hyprland did not configure the {width}x{height} Moonlight output")
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = str(RUNTIME)
        env["WAYLAND_DISPLAY"] = current["wl_socket"]
        env["HYPRLAND_INSTANCE_SIGNATURE"] = instance
        sunshine = shutil.which("sunshine")
        if not sunshine:
            raise RuntimeError("Sunshine executable not found; rerun setup")
        os.execve(sunshine, [sunshine, str(CONFIG)], env)
    except Exception:
        cleanup()
        raise


def cleanup() -> None:
    if not STATE.exists():
        return
    instance = json.loads(STATE.read_text())["instance"]
    result = hypr(instance, "-j", "monitors", check=False)
    if result.returncode:
        instances = json.loads(run("hyprctl", "instances", "-j").stdout)
        if any(item["instance"] == instance for item in instances):
            raise RuntimeError("Cannot inspect Moonlight output; ownership record retained for recovery")
    elif any(item["name"] == OUTPUT for item in json.loads(result.stdout)):
        hypr(instance, "output", "remove", OUTPUT)
        if any(item["name"] == OUTPUT for item in monitors(instance)):
            raise RuntimeError("Moonlight output removal failed; ownership record retained for recovery")
    STATE.unlink(missing_ok=True)


def addresses() -> list[tuple[str, str]]:
    result = run("ip", "-j", "-d", "-4", "addr", "show", "scope", "global")
    routes = json.loads(run("ip", "-j", "-4", "route", "show", "default").stdout)
    preferred = min(routes, key=lambda item: item.get("metric", 0)).get("dev") if routes else None
    found = []
    for link in json.loads(result.stdout):
        iface = link["ifname"]
        net = Path("/sys/class/net") / iface
        physical = (net / "device").exists() or (net / "phy80211").exists()
        if "UP" not in link.get("flags", []) or not (physical or iface == preferred or iface == "tailscale0"):
            continue
        for address in link.get("addr_info", []):
            ip = ipaddress.ip_address(address["local"])
            if not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified):
                label = "Tailscale" if iface == "tailscale0" else iface
                driver = net / "device/driver"
                if driver.is_symlink() and driver.resolve().name == "ipheth":
                    label = "iPhone USB"
                found.append((iface != preferred, label, str(ip)))
    found.sort()
    return [(label, ip) for _, label, ip in found]


def status() -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    console = Console()
    state = run("systemctl", "--user", "is-active", UNIT_NAME, check=False).stdout.strip() or "inactive"
    active = state == "active"
    working = active and ready()
    value = preferences()["orientation"]
    width, height = display_size(value)
    label = "Ready" if working else "Off" if state == "inactive" else "Not ready"
    console.print(Text(f"Moonlight display: {label} ({value}, {width}x{height})", style="bold green" if working else "yellow"))
    table = Table(title="Moonlight addresses")
    table.add_column("Network")
    table.add_column("Address", style="bold cyan")
    connection_addresses = addresses()
    for network, ip in connection_addresses:
        table.add_row(Text(network), Text(ip))
    console.print(table)
    if working:
        if repair_duplicate_pairings(repair=False):
            message("Duplicate client certificates detected; rerun setup to repair pairing.", error=True)
        address = connection_addresses[0][1] if connection_addresses else "an IP from the table (none currently available)"
        steps = (
            "1. Install Moonlight on the receiving phone, tablet or PC.\n"
            "   iPhone/Android: Moonlight Game Streaming from App Store/Google Play.\n"
            "   Windows/macOS/Linux PC: download the Moonlight desktop client.\n"
            "   Official downloads: https://moonlight-stream.org/\n"
            "2. Use the same Wi-Fi/Ethernet network, or connect both devices to the same Tailscale tailnet.\n"
            "   On iPhone, allow Local Network access. For remote access, choose the Tailscale address.\n"
            f"3. In Moonlight, add the server manually: {address} (IP only).\n"
            "4. On the streaming server, open https://localhost:47990; create/sign in with Sunshine's web UI account.\n"
            "   Proceed past the local certificate warning if the browser shows one.\n"
            "5. Select the server in Moonlight; enter the receiving device's PIN on Sunshine's PIN page.\n"
            "6. Launch Desktop. Local streaming needs no internet; downloads and remote Tailscale access do."
        )
        console.print(Panel(Text(steps), title="Connect another device", border_style="cyan"))
        message("Open the PC pairing page again: --pair")
        message(f"The {OUTPUT} monitor is to the right of your other displays.")
        message("An empty workspace may look black; move a window there or run --test-display.")
    elif state != "inactive":
        message(f"Check: journalctl --user -u {UNIT_NAME} -n 40 --no-pager")
        raise RuntimeError("Moonlight display is not ready")
    message(f"Moonlight: systemctl --user {'disable' if active else 'enable'} --now {UNIT_NAME}")


def stop() -> None:
    run("systemctl", "--user", "disable", "--now", UNIT_NAME)
    message("Moonlight display stopped; its virtual monitor was removed")


def reconnect() -> None:
    if not UNIT.exists():
        setup(None)
        return
    run("systemctl", "--user", "restart", UNIT_NAME)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if ready():
            status()
            return
        time.sleep(0.25)
    raise RuntimeError(f"Reconnect failed; run {SCRIPT.name} --diagnose")


def clients() -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    path = CONFIG.parent / "sunshine_state.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    table = Table(title="Paired Moonlight clients")
    for title in ("Name", "Client ID", "Enabled"):
        table.add_column(title)
    devices = data.get("root", {}).get("named_devices", [])
    for device in devices:
        enabled = str(device.get("enabled", True)).lower() != "false"
        table.add_row(Text(device.get("name", "Unnamed")), Text(device.get("uuid", "")), "Yes" if enabled else "No")
    Console().print(table)
    if not devices:
        message("No saved clients. Pair using --pair.")
    else:
        message("Remove one: --forget-client CLIENT_ID    Remove all: --forget-all")


def forget_clients(identifier: str | None = None) -> None:
    path = CONFIG.parent / "sunshine_state.json"
    if not path.exists():
        raise RuntimeError("No paired-client state exists")
    devices = json.loads(path.read_text()).get("root", {}).get("named_devices", [])
    if identifier is not None and not any(item.get("uuid") == identifier for item in devices):
        raise RuntimeError("Client ID not found; use --clients to list saved IDs")
    if not devices:
        message("No saved clients to remove")
        return
    # Stop the writer before editing shared pairing/web-credential state.
    active = run("systemctl", "--user", "is-active", UNIT_NAME, check=False).stdout.strip() in {"active", "activating"}
    run("systemctl", "--user", "stop", UNIT_NAME)
    try:
        original = path.read_text()
        data = json.loads(original)
        devices = data.get("root", {}).get("named_devices", [])
        retained = [item for item in devices if identifier is not None and item.get("uuid") != identifier]
        removed = len(devices) - len(retained)
        if identifier is not None and not removed:
            raise RuntimeError("Client ID not found; use --clients to list saved IDs")
        if removed:
            atomic_write(path.with_suffix(".json.before-client-removal"), original)
            data["root"]["named_devices"] = retained
            atomic_write(path, json.dumps(data, indent=2) + "\n")
        message(f"Removed {removed} saved client(s); web UI credentials were preserved")
    finally:
        if active:
            run("systemctl", "--user", "start", UNIT_NAME)
    message("Forget the old host in the receiving device's Moonlight client too, then pair again using --pair.")


def open_pairing_page(*, only_unpaired: bool = False) -> None:
    if only_unpaired:
        path = CONFIG.parent / "sunshine_state.json"
        if path.exists() and json.loads(path.read_text()).get("root", {}).get("named_devices"):
            return
    url = "https://localhost:47990"
    if shutil.which("xdg-open"):
        try:
            result = subprocess.run(["xdg-open", url], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if result.returncode == 0:
                message("Opened Sunshine's pairing page in your PC's browser.")
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    message(f"Open Sunshine's pairing page manually on this PC: {url}")


def pair() -> None:
    if not ready():
        reconnect()
    else:
        status()
    open_pairing_page()


def diagnose() -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    table = Table(title="Moonlight diagnostics")
    table.add_column("Check")
    table.add_column("Result")
    for label, command in (
        ("Service", ("systemctl", "--user", "is-active", UNIT_NAME)),
        ("Startup", ("systemctl", "--user", "is-enabled", UNIT_NAME)),
        ("Wi-Fi/default route", ("ip", "-4", "route", "show", "default")),
    ):
        table.add_row(label, Text(run(*command, check=False).stdout.strip() or "Unavailable"))
    table.add_row("GameStream", "Responding" if listening() else "Unavailable")
    current = session()
    monitor = next((item for item in monitors(current["instance"]) if item["name"] == OUTPUT), None) if current else None
    table.add_row("Virtual monitor", f"{monitor['width']}×{monitor['height']} on workspace {monitor['activeWorkspace']['name']}" if monitor else "Absent")
    table.add_row("Duplicate identities", str(repair_duplicate_pairings(repair=False)))
    Console().print(table)
    clients()
    message("Pairing failure: forget the affected client on the server and receiving device, then --pair.")
    message("Black screen: --test-display distinguishes an empty monitor from a video failure.")
    message("Timeout: rerun --setup for UFW rules; check Wi-Fi client isolation on the router.")
    message(f"Detailed logs: journalctl --user -u {UNIT_NAME} -n 40 --no-pager")


def test_display() -> None:
    if not ready():
        raise RuntimeError("Start Sunshine first using --reconnect")
    if not shutil.which("kitty"):
        raise RuntimeError("The visible test requires kitty; alternatively move a window onto the virtual monitor")
    current = session()
    monitor = next(item for item in monitors(current["instance"]) if item["name"] == OUTPUT)
    command = shlex.join([
        "kitty", "--class", "dusky-moonlight-test", "--title", "Moonlight video test",
        "--override", "background=#ffffff", "--override", "foreground=#000000",
        "--override", "background_opacity=1", "--override", "font_size=28",
        "--hold", "/usr/bin/printf", "Moonlight video works!\n\nTry moving the cursor.\n",
    ])
    existing = json.loads(hypr(current["instance"], "-j", "clients").stdout)
    if not any(item.get("class") == "dusky-moonlight-test" for item in existing):
        hypr(current["instance"], "eval", f"hl.exec_cmd({json.dumps(command)})")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        windows = json.loads(hypr(current["instance"], "-j", "clients").stdout)
        tests = [item for item in windows if item.get("class") == "dusky-moonlight-test"]
        if tests:
            for window in tests:
                options = {"workspace": str(monitor["activeWorkspace"]["id"]), "silent": True, "window": "address:" + window["address"]}
                lua = "{ " + ", ".join(f"{key} = {json.dumps(value)}" for key, value in options.items()) + " }"
                hypr(current["instance"], "eval", f"hl.dispatch(hl.dsp.window.move({lua}))")
            message("A white test window is on the virtual monitor. Close it normally after testing.")
            return
        time.sleep(0.1)
    raise RuntimeError("The test window did not appear; check kitty and Hyprland logs")


def orientation(value: str | None) -> None:
    current = preferences()
    if value is None:
        message(f"Moonlight display orientation: {current['orientation']} ({PREFERENCES})")
        return
    changed = value != current["orientation"]
    if changed:
        save_preferences({**current, "orientation": value})
    width, height = display_size(value)
    active = run("systemctl", "--user", "is-active", UNIT_NAME, check=False).stdout.strip() == "active"
    applied = ready() if active else False
    if active and (changed or not applied):
        run("systemctl", "--user", "restart", UNIT_NAME)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if ready():
                break
            time.sleep(0.25)
        else:
            raise RuntimeError(f"Moonlight display did not start in {value}; check journalctl --user -u {UNIT_NAME}")
    message(f"Moonlight display orientation: {value} ({width}x{height})")
    message(f"Saved in {PREFERENCES}" + ("" if active else "; takes effect when the service starts"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = ("setup", "status", "serve", "cleanup", "stop", "orientation", "usb", "firewall", "reconnect", "clients", "pair", "diagnose", "test-display")
    parser.add_argument("action", nargs="?", choices=actions, help="Action; defaults to setup. Flags below are equivalent shortcuts.")
    parser.add_argument("value", nargs="?", choices=("landscape", "portrait"), help="Display orientation for the orientation action")
    parser.add_argument("--package", type=Path, help="Local Sunshine Arch package for offline setup")
    group = parser.add_mutually_exclusive_group()
    for action, help_text in (
        ("setup", "Configure Sunshine, UFW and service; start if needed"),
        ("status", "Show readiness and connection addresses"),
        ("stop", "Disable and stop the entire secondary display"),
        ("reconnect", "Restart Sunshine and repair duplicate pairings"),
        ("clients", "List saved client names and IDs"),
        ("pair", "Open the local Sunshine PIN page"),
        ("diagnose", "Show service, network, display and pairing checks"),
        ("test-display", "Show a visible test window on the virtual monitor"),
        ("usb", "Prepare optional iPhone USB routing; keep Wi-Fi default"),
    ):
        group.add_argument("--" + action, dest="flag_action", action="store_const", const=action, help=help_text)
    group.add_argument("--orientation", dest="flag_orientation", choices=("landscape", "portrait"), help="Set the virtual monitor orientation")
    group.add_argument("--forget-client", metavar="CLIENT_ID", help="Remove a saved client ID shown by --clients")
    group.add_argument("--forget-all", action="store_true", help="Remove all saved clients; preserve web UI credentials")
    args = parser.parse_args()
    flag_used = args.flag_action or args.flag_orientation or args.forget_client or args.forget_all
    if args.action and flag_used:
        parser.error("choose a positional action or an action flag")
    action = args.action or args.flag_action or ("orientation" if args.flag_orientation else "forget" if args.forget_client or args.forget_all else "setup")
    value = args.flag_orientation or args.value
    if value and action != "orientation":
        parser.error("an orientation value requires the orientation action")
    if args.package and action != "setup":
        parser.error("--package requires setup")
    if os.geteuid() == 0 and action != "firewall":
        parser.error("run as the desktop user, without sudo")
    if action == "setup":
        setup(args.package)
    elif action == "orientation":
        orientation(value)
    elif action == "forget":
        forget_clients(args.forget_client)
    else:
        {"status": status, "serve": serve, "cleanup": cleanup, "stop": stop,
         "usb": setup_iphone_usb, "firewall": firewall_worker, "reconnect": reconnect,
         "clients": clients, "pair": pair, "diagnose": diagnose, "test-display": test_display}[action]()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        message(f"Error: {(error.stderr or '').strip() or (error.stdout or '').strip() or error}", error=True)
        sys.exit(1)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        message(f"Error: {error}", error=True)
        sys.exit(1)

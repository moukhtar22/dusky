#!/usr/bin/env python3
"""Set up an optional WayVNC user service for Hyprland.

Run without arguments once to install and start the user service. Use ``status``
for connection addresses, ``remote`` to set up Tailscale, and ``stop`` to disable
VNC and the optional secondary display. ``offline`` needs a spare Wi-Fi adapter;
the existing Wi-Fi connection is preserved. Desktop sharing uses port 5902.
"""

import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys


from vnc_common import (
    CONFIG_HOME, RUNTIME, MASTER, DESKTOP_PORT, PHONE,
    configure_firewall, control_data, exec_wayvnc, install_unit, message, prepare, rfb_ready as probe_rfb,
    run, script_command, show_status, wait_ready, wait_session, write_config as configure,
    ensure_dependencies, show_clients, disconnect_clients, show_diagnostics, parse_action,
)

CONFIG_DIR = CONFIG_HOME / "wayvnc"
CONFIG = CONFIG_DIR / "arch-ios.conf"
KEY = CONFIG_DIR / "arch-ios-key.pem"
CERT = CONFIG_DIR / "arch-ios-cert.pem"
UNIT_NAME = MASTER
UNIT = CONFIG_HOME / "systemd/user" / UNIT_NAME
CONTROL = RUNTIME / "dusky-desktop-wayvnc.sock"
PORT = DESKTOP_PORT
OFFLINE_PROFILE = "arch-ios-offline"


def unit_content() -> str:
    return (
        "[Unit]\nDescription=VNC desktop sharing over WayVNC\n"
        "After=graphical-session.target\nPartOf=graphical-session.target\n"
        "StartLimitIntervalSec=0\n\n"
        "[Service]\nType=exec\n"
        f"ExecCondition=/usr/bin/systemctl --user is-enabled --quiet {UNIT_NAME}\n"
        f"ExecStart={script_command(Path(__file__), 'serve')}\n"
        "Restart=always\nRestartSec=5\nRestartSteps=5\nRestartMaxDelaySec=30\n\n"
        "[Install]\nWantedBy=default.target graphical-session.target\n"
    )


def ready() -> bool:
    outputs = control_data(CONTROL, "output-list")
    return outputs is not None and any(item.get("captured") for item in outputs) and probe_rfb(PORT)


def install(*, show: bool = True) -> None:
    prepare()
    configure_firewall()
    config_changed = configure(CONFIG, KEY, CERT, PORT)
    unit_changed = install_unit(UNIT, unit_content())
    active = run("systemctl", "--user", "is-active", UNIT_NAME, check=False).stdout.strip() == "active"
    if not active:
        run("systemctl", "--user", "start", UNIT_NAME)
    elif config_changed or unit_changed or not ready():
        run("systemctl", "--user", "restart", UNIT_NAME)
    wait_ready(ready, UNIT_NAME)
    if show:
        status()


def wifi_device() -> str | None:
    """Only use an idle AP-capable adapter; never displace an active link."""
    result = run("nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status")
    for line in result.stdout.splitlines():
        device, _, kind = line.partition(":")
        if kind != "wifi":
            continue
        state = run("nmcli", "-g", "GENERAL.STATE", "device", "show", device).stdout.strip()
        if not state.startswith("30 "):
            continue
        if run("nmcli", "-g", "WIFI-PROPERTIES.AP", "device", "show", device).stdout.strip() == "yes":
            return device
    return None


def offline_credentials() -> tuple[str, str] | None:
    profile = run("nmcli", "-e", "no", "-g", "802-11-wireless.ssid", "connection", "show", OFFLINE_PROFILE,
                  check=False)
    if profile.returncode:
        return None
    secret = run("nmcli", "--show-secrets", "-e", "no", "-g", "802-11-wireless-security.psk",
                 "connection", "show", OFFLINE_PROFILE, check=False)
    if secret.returncode:
        return None
    return profile.stdout.strip(), secret.stdout.strip()


def setup_offline_wifi(device: str) -> None:
    if not Path("/usr/bin/dnsmasq").exists():
        raise RuntimeError("Install dnsmasq from the ISO or distribution repository for hotspot DHCP")
    if offline_credentials():
        mode = run("nmcli", "-g", "802-11-wireless.mode", "connection", "show", OFFLINE_PROFILE).stdout.strip()
        active = run("nmcli", "-g", "GENERAL.STATE", "connection", "show", OFFLINE_PROFILE).stdout.strip()
        if mode != "ap" or active == "activated":
            raise RuntimeError("Existing offline profile must be an inactive Wi-Fi hotspot")
        return
    ssid = f"VNC-{socket.gethostname()[:20]}"
    password = secrets.token_urlsafe(12)
    run("nmcli", "connection", "add", "type", "wifi", "ifname", device,
        "con-name", OFFLINE_PROFILE, "ssid", ssid, "mode", "ap",
        "802-11-wireless-security.key-mgmt", "wpa-psk",
        "802-11-wireless-security.psk", password,
        "ipv4.method", "shared", "ipv4.never-default", "yes", "ipv6.method", "disabled",
        "connection.autoconnect", "no")
    message(f"Offline Wi-Fi prepared: {ssid} (manual activation only)")


def offline() -> None:
    ensure_dependencies({"networkmanager": ("nmcli",), "dnsmasq": ("dnsmasq",)})
    device = wifi_device()
    if not device:
        raise RuntimeError("Offline hotspot needs an unused second Wi-Fi adapter. Keep both devices on your existing network and use status.")
    setup_offline_wifi(device)
    credentials = offline_credentials()
    if not credentials:
        raise RuntimeError("Offline Wi-Fi profile is unavailable")
    if shutil.which("ufw"):
        for port, protocol in ((67, "udp"), (53, "udp"), (53, "tcp")):
            subprocess.run(["sudo", "ufw", "allow", "in", "on", device, "to", "any", "port", str(port),
                            "proto", protocol, "comment", "Dusky VNC hotspot"], check=True)
    run("nmcli", "connection", "modify", OFFLINE_PROFILE,
        "connection.interface-name", device, "ipv4.method", "shared",
        "ipv4.never-default", "yes", "ipv6.method", "disabled", "connection.autoconnect", "no")
    # Check again immediately before activation, including an existing profile.
    if wifi_device() != device:
        raise RuntimeError("The spare adapter is no longer idle; hotspot was not activated")
    run("nmcli", "--wait", "10", "connection", "up", OFFLINE_PROFILE, "ifname", device)
    message(f"Connect the receiving device to Wi-Fi {credentials[0]} with password {credentials[1]}")
    status()


def serve() -> None:
    if not CONFIG.is_file():
        raise RuntimeError("Run setup first")
    current = wait_session()
    configure(CONFIG, KEY, CERT, PORT)
    result = run("hyprctl", "--instance", current["instance"], "-j", "monitors")
    outputs = json.loads(result.stdout)
    # Explicitly select a physical screen; the first advertised output can be virtual.
    physical = [item for item in outputs if item.get("description") and not item.get("disabled")]
    selected = next((item for item in physical if item.get("focused")), next(iter(physical or outputs), None))
    if not selected:
        raise RuntimeError("Hyprland has no active display to share")
    exec_wayvnc(current, CONFIG, CONTROL, "-o", selected["name"])


def rfb_ready() -> bool:
    return ready()


def status() -> None:
    show_status(UNIT_NAME, PORT, CONTROL, ready(), "Desktop sharing")
    enabled = run("systemctl", "--user", "is-enabled", PHONE, check=False).stdout.strip()
    message(f"Secondary display: port 5901 ({enabled}); run second_display.py status for details.")


def remote() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Run setup as the desktop user, without sudo")
    ensure_dependencies({"tailscale": ("tailscale",)})
    enabled = run("systemctl", "is-enabled", "tailscaled.service", check=False).stdout.strip() == "enabled"
    active = run("systemctl", "is-active", "tailscaled.service", check=False).stdout.strip() == "active"
    if not (enabled and active):
        subprocess.run(["sudo", "systemctl", "enable", "--now", "tailscaled.service"], check=True)
    ip = run("tailscale", "ip", "-4", check=False)
    if ip.returncode or not ip.stdout.strip():
        message("Complete the Tailscale sign-in shown below to join your tailnet.")
        subprocess.run(["sudo", "tailscale", "up"], check=True)
        ip = run("tailscale", "ip", "-4", check=False)
    if ip.returncode or not ip.stdout.strip():
        raise RuntimeError("Tailscale has no IPv4 address yet; finish sign-in and retry")
    message(f"Tailscale address: {ip.stdout.strip()}:{PORT}")
    if CONFIG.is_file() and configure(CONFIG, KEY, CERT, PORT):
        if run("systemctl", "--user", "is-active", UNIT_NAME, check=False).stdout.strip() == "active":
            run("systemctl", "--user", "restart", UNIT_NAME)
            wait_ready(ready, UNIT_NAME)
    if rfb_ready():
        message("Connect a VNC viewer on another device signed in to the same tailnet.")
    else:
        message(f"VNC is off; run this script without arguments to start {UNIT_NAME}.")


def stop() -> None:
    run("systemctl", "--user", "disable", "--now", UNIT_NAME)
    message("All Dusky VNC stopped and disabled; the optional secondary monitor is removed")


def reconnect() -> None:
    prepare()
    if not UNIT.exists() or run("systemctl", "--user", "is-enabled", UNIT_NAME, check=False).stdout.strip() != "enabled":
        install(show=False)
    else:
        run("systemctl", "--user", "restart", UNIT_NAME)
        wait_ready(ready, UNIT_NAME)
    if run("systemctl", "--user", "is-enabled", PHONE, check=False).stdout.strip() == "enabled":
        import second_display
        run("systemctl", "--user", "start", PHONE)
        wait_ready(second_display.ready, PHONE)
    status()


def main() -> None:
    action, _, identifier = parse_action(__doc__, ("setup", "status", "serve", "stop", "offline", "remote", "reconnect", "clients", "diagnose"))
    {"setup": install, "status": status, "serve": serve, "stop": stop, "offline": offline, "remote": remote,
     "reconnect": reconnect, "clients": lambda: show_clients(CONTROL),
     "disconnect": lambda: disconnect_clients(CONTROL, identifier),
     "diagnose": lambda: show_diagnostics(UNIT_NAME, PORT, CONTROL)}[action]()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        message(f"Error: {(exc.stderr or '').strip() or (exc.stdout or '').strip() or exc}", error=True)
        sys.exit(1)
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        message(f"Error: {exc}", error=True)
        sys.exit(1)

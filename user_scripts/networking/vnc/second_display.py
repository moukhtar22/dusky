#!/usr/bin/env python3
"""Stream a separate Hyprland display to another device through WayVNC on port 5901.

Run ``orientation portrait`` or ``orientation landscape`` to switch its shape.
"""

import json
from pathlib import Path
import subprocess
import sys
import time


from vnc_common import (
    CONFIG_HOME, RUNTIME, MASTER, PHONE, PHONE_PORT, atomic_write,
    control_data, exec_wayvnc, install_unit, message, prepare, run, session,
    script_command, show_status, wait_ready, wait_session, write_config as configure,
    rfb_ready as probe_rfb,
    show_clients, disconnect_clients, show_diagnostics, parse_action,
)

CONFIG_DIR = CONFIG_HOME / "wayvnc"
CONFIG = CONFIG_DIR / "phone-display.conf"
KEY = CONFIG_DIR / "phone-display-key.pem"
CERT = CONFIG_DIR / "phone-display-cert.pem"
UNIT_NAME = PHONE
UNIT = CONFIG_HOME / "systemd/user" / UNIT_NAME
STATE = RUNTIME / "dusky-phone-display.json"
CONTROL = RUNTIME / "dusky-phone-wayvnc.sock"
OUTPUT = "DUSKY-PHONE"
PORT = PHONE_PORT
LANDSCAPE_SIZE = (1280, 720)
PREFERENCES = CONFIG_HOME / "dusky/settings/remote/vnc_display.json"


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


def monitors(instance: str) -> list[dict]:
    result = hypr(instance, "-j", "monitors")
    return json.loads(result.stdout)


def unit_content() -> str:
    return (
        "[Unit]\nDescription=Secondary display over WayVNC\n"
        f"After=graphical-session.target {MASTER}\n"
        f"BindsTo={MASTER}\nPartOf=graphical-session.target {MASTER}\n"
        "StartLimitIntervalSec=0\n\n"
        "[Service]\nType=exec\n"
        f"ExecStart={script_command(Path(__file__), 'serve')}\n"
        f"ExecStopPost={script_command(Path(__file__), 'cleanup')}\n"
        "Restart=always\nRestartSec=5\nRestartSteps=5\nRestartMaxDelaySec=30\n\n"
        f"[Install]\nWantedBy={MASTER}\n"
    )


def rfb_ready() -> bool:
    return probe_rfb(PORT)


def output_ready() -> bool:
    outputs = control_data(CONTROL, "output-list")
    return outputs is not None and any(item.get("name") == OUTPUT and item.get("captured") for item in outputs)


def ready() -> bool:
    return output_ready() and rfb_ready() and display_ready()


def display_ready() -> bool:
    current = session()
    if not current:
        return False
    width, height = display_size()
    return any(item["name"] == OUTPUT and (item["width"], item["height"]) == (width, height)
               for item in monitors(current["instance"]))


def install() -> None:
    prepare()
    preferences()
    # Desktop service is the master switch, even for a secondary-display-only setup.
    import vnc_setup
    vnc_setup.install(show=False)
    config_changed = configure(CONFIG, KEY, CERT, PORT)
    unit_changed = install_unit(UNIT, unit_content())
    active = run("systemctl", "--user", "is-active", UNIT_NAME, check=False).stdout.strip() == "active"
    if not active:
        run("systemctl", "--user", "start", UNIT_NAME)
    elif config_changed or unit_changed or not ready():
        run("systemctl", "--user", "restart", UNIT_NAME)
    wait_ready(ready, UNIT_NAME)
    status()


def serve() -> None:
    if not CONFIG.is_file():
        raise RuntimeError("Run setup first")
    width, height = display_size()
    current = wait_session()
    configure(CONFIG, KEY, CERT, PORT)
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
            raise RuntimeError(f"Hyprland did not configure the {width}x{height} secondary output")
        # Keep the saved dimensions when clients request desktop resizing.
        exec_wayvnc(current, CONFIG, CONTROL, "-o", OUTPUT, "-R")
    except Exception:
        cleanup()
        raise


def cleanup() -> None:
    if not STATE.exists():
        return
    instance = json.loads(STATE.read_text())["instance"]
    result = hypr(instance, "-j", "monitors", check=False)
    if result.returncode:
        # An exited compositor no longer owns any virtual monitor.
        instances = json.loads(run("hyprctl", "instances", "-j").stdout)
        if any(item["instance"] == instance for item in instances):
            raise RuntimeError("Cannot inspect secondary output; ownership record retained for recovery")
    elif any(item["name"] == OUTPUT for item in json.loads(result.stdout)):
        hypr(instance, "output", "remove", OUTPUT)
        if any(item["name"] == OUTPUT for item in monitors(instance)):
            raise RuntimeError("Secondary output removal failed; ownership record retained for recovery")
    STATE.unlink(missing_ok=True)


def status() -> None:
    value = preferences()["orientation"]
    width, height = display_size(value)
    show_status(UNIT_NAME, PORT, CONTROL, ready(), "Secondary display")
    message(f"Orientation: {value} ({width}x{height}); the virtual monitor is to the right of your desktop.")


def stop() -> None:
    run("systemctl", "--user", "disable", "--now", UNIT_NAME)
    message("Secondary display stopped; the virtual monitor was removed")


def reconnect() -> None:
    if not UNIT.exists():
        install()
        return
    prepare()
    import vnc_setup
    master_enabled = run("systemctl", "--user", "is-enabled", MASTER, check=False).stdout.strip() == "enabled"
    if not master_enabled or not vnc_setup.ready():
        vnc_setup.install(show=False)
    run("systemctl", "--user", "enable", UNIT_NAME)
    run("systemctl", "--user", "restart", UNIT_NAME)
    wait_ready(ready, UNIT_NAME)
    status()


def orientation(value: str | None) -> None:
    current = preferences()
    if value is None:
        message(f"VNC display orientation: {current['orientation']} ({PREFERENCES})")
        return
    changed = value != current["orientation"]
    if changed:
        save_preferences({**current, "orientation": value})
    width, height = display_size(value)
    active = run("systemctl", "--user", "is-active", UNIT_NAME, check=False).stdout.strip() == "active"
    applied = display_ready() if active else False
    if active and (changed or not applied):
        run("systemctl", "--user", "restart", UNIT_NAME)
        wait_ready(ready, UNIT_NAME)
    message(f"VNC display orientation: {value} ({width}x{height})")
    message(f"Saved in {PREFERENCES}" + ("" if active else "; takes effect when the service starts"))


def main() -> None:
    action, value, identifier = parse_action(__doc__, ("setup", "status", "serve", "cleanup", "stop", "orientation", "reconnect", "clients", "diagnose"), orientation=True)
    if action == "orientation":
        orientation(value)
    else:
        {"setup": install, "status": status, "serve": serve, "cleanup": cleanup,
         "stop": stop, "reconnect": reconnect, "clients": lambda: show_clients(CONTROL),
         "disconnect": lambda: disconnect_clients(CONTROL, identifier),
         "diagnose": lambda: show_diagnostics(UNIT_NAME, PORT, CONTROL)}[action]()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        message(f"Error: {(error.stderr or '').strip() or (error.stdout or '').strip() or error}", error=True)
        sys.exit(1)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        message(f"Error: {error}", error=True)
        sys.exit(1)

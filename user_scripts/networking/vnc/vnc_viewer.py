#!/usr/bin/env python3
"""Install and open the native Wayland VNC viewer on a receiving Arch PC."""

import argparse
import configparser
import ctypes.util
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


def server_address(value: str) -> str:
    """Accept a hostname/IP with an optional explicit port; default to desktop sharing."""
    try:
        parsed = urlsplit("//" + value)
        host, port = parsed.hostname, parsed.port or 5902
        if (not host or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or not re.fullmatch(r"[A-Za-z0-9._:\-]+", host)
                or parsed.port == 0):
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("Use SERVER_IP:5902 or HOSTNAME:5902 (one colon before the port)") from None
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def install_dependencies() -> None:
    missing = []
    if not shutil.which("remmina"):
        missing.append("remmina")
    if not shutil.which("openssl"):
        missing.append("openssl")
    if not ctypes.util.find_library("vncclient"):
        missing.append("libvncserver")
    if importlib.util.find_spec("rich") is None:
        missing.append("python-rich")
    if missing:
        print("Installing receiving-PC packages: " + ", ".join(missing), flush=True)
        subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", *missing], check=True)
    if (not shutil.which("remmina") or not shutil.which("openssl") or not ctypes.util.find_library("vncclient")
            or importlib.util.find_spec("rich") is None):
        raise RuntimeError("Viewer installation incomplete; remmina, openssl, libvncserver and python-rich are required")


def connection_profile(server: str, username: str | None, quality: str = "fast") -> Path:
    quality_value = {"fast": "1", "balanced": "2", "best": "9"}[quality]
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    profile = data / "remmina" / ("dusky_vnc_" + hashlib.sha256(server.encode()).hexdigest()[:16] + ".remmina")
    settings = configparser.ConfigParser(interpolation=None)
    if profile.exists():
        settings.read(profile)
        if "remmina" not in settings:
            raise RuntimeError(f"Invalid saved Remmina profile: {profile}")
        if ((username is None or settings["remmina"].get("username") == username)
                and settings["remmina"].get("cacert") == str(profile.with_suffix(".crt"))
                and settings["remmina"].get("quality") == quality_value):
            return profile
    else:
        settings["remmina"] = {"name": f"VNC ({server})", "protocol": "VNC",
                               "server": server, "colordepth": "32", "viewonly": "0",
                               "shared": "1", "scale": "1"}
    if username is not None:
        settings["remmina"]["username"] = username
    # Medium prefers Tight/JPEG with lower image quality and more compression than Good.
    settings["remmina"]["quality"] = quality_value
    settings["remmina"]["cacert"] = str(profile.with_suffix(".crt"))
    profile.parent.mkdir(parents=True, exist_ok=True)
    # Remmina manages subsequent preferences and saved credentials itself.
    with tempfile.TemporaryDirectory(dir=profile.parent) as directory:
        replacement = Path(directory) / profile.name
        with replacement.open("w") as output:
            settings.write(output)
        replacement.chmod(0o600)
        replacement.replace(profile)
    return profile


def disable_applet() -> None:
    """Remmina initializes its applet before parsing the no-tray CLI flag."""
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    preference = config / "remmina" / "remmina.pref"
    settings = configparser.ConfigParser(interpolation=None)
    settings.read(preference)
    if "remmina_pref" not in settings:
        settings["remmina_pref"] = {}
    if settings["remmina_pref"].get("disable_tray_icon") != "true":
        settings["remmina_pref"]["disable_tray_icon"] = "true"
        preference.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=preference.parent) as directory:
            replacement = Path(directory) / preference.name
            with replacement.open("w") as output:
                settings.write(output)
            replacement.chmod(0o600)
            replacement.replace(preference)
    # Remmina 1.4.43 uses ~/.config/autostart even with a custom config home.
    for directory in {config, Path.home() / ".config"}:
        (directory / "autostart/remmina-applet.desktop").unlink(missing_ok=True)


def server_certificate(server: str, context: ssl.SSLContext) -> bytes:
    """Negotiate VeNCrypt X509Plain and inspect TLS before sending credentials."""
    address = urlsplit("//" + server)
    with socket.create_connection((address.hostname, address.port), timeout=8) as connection:
        def receive(size: int) -> bytes:
            data = bytearray()
            while len(data) < size:
                chunk = connection.recv(size - len(data))
                if not chunk:
                    raise RuntimeError("VNC server closed the connection during TLS setup")
                data.extend(chunk)
            return bytes(data)
        if receive(12) != b"RFB 003.008\n":
            raise RuntimeError("Server does not offer the expected VNC protocol")
        connection.sendall(b"RFB 003.008\n")
        if 19 not in receive(receive(1)[0]):
            raise RuntimeError("Server does not offer VeNCrypt; run the Dusky VNC server setup")
        connection.sendall(b"\x13")
        if receive(2) != b"\x00\x02":
            raise RuntimeError("Unsupported VeNCrypt version")
        connection.sendall(b"\x00\x02")
        if receive(1) != b"\x00":
            raise RuntimeError("Server rejected VeNCrypt negotiation")
        schemes = [struct.unpack("!I", receive(4))[0] for _ in range(receive(1)[0])]
        if 262 not in schemes:
            raise RuntimeError("Server does not offer TLS username/password login")
        connection.sendall(struct.pack("!I", 262))
        if receive(1) != b"\x01":
            raise RuntimeError("Server rejected TLS username/password login")
        with context.wrap_socket(connection, server_hostname=address.hostname) as tls:
            return tls.getpeercert(binary_form=True)


def configure_certificate(server: str, profile: Path, reset: bool = False) -> None:
    certificate = profile.with_suffix(".crt")
    if reset:
        certificate.unlink(missing_ok=True)

    def discover() -> bytes:
        # Trust on first use: no credentials are sent during certificate discovery.
        discovery = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        discovery.check_hostname = False
        discovery.verify_mode = ssl.CERT_NONE
        return server_certificate(server, discovery)

    def save(pem: str) -> None:
        with tempfile.TemporaryDirectory(dir=certificate.parent) as directory:
            replacement = Path(directory) / certificate.name
            replacement.write_text(pem)
            replacement.chmod(0o600)
            replacement.replace(certificate)

    if not certificate.exists():
        save(ssl.DER_cert_to_PEM_cert(discover()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(certificate))
    try:
        server_certificate(server, context)
    except ssl.SSLCertVerificationError as error:
        # A renewed self-signed certificate is accepted only when its public
        # key matches saved trust. An address change need not reset identity.
        if error.verify_code in (18, 19, 20):
            der = discover()
            saved_key = subprocess.run(["openssl", "x509", "-in", str(certificate), "-noout", "-pubkey"],
                                       capture_output=True, check=True).stdout
            new_key = subprocess.run(["openssl", "x509", "-inform", "DER", "-noout", "-pubkey"],
                                     input=der, capture_output=True, check=True).stdout
            if saved_key == new_key:
                pem = ssl.DER_cert_to_PEM_cert(der)
                refreshed = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                refreshed.load_verify_locations(cadata=pem)
                try:
                    server_certificate(server, refreshed)
                except ssl.SSLCertVerificationError as refreshed_error:
                    error = refreshed_error
                else:
                    save(pem)
                    return
        raise RuntimeError(
            f"Server certificate verification failed: {error.verify_message}. "
            "On the server, rerun the updated vnc_setup.py --setup (second_display.py --setup for port 5901). "
            "After an intentional server identity replacement, reconnect with --reset-trust."
        ) from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server", nargs="?", type=server_address,
                        help="Server IP/hostname, optionally :5902 (desktop) or :5901 (secondary)")
    parser.add_argument("--username", help="Server's Linux username; enter its password inside Remmina")
    parser.add_argument("--install-only", action="store_true", help="Install missing packages without opening the viewer")
    parser.add_argument("--reset-trust", action="store_true", help="Trust the current server certificate again after an intentional identity replacement")
    parser.add_argument("--quality", choices=("fast", "balanced", "best"), default="fast",
                        help="Compression preset applied on each launch (default: fast, Tight/JPEG)")
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("run as your desktop user, without sudo; only package installation uses sudo")
    if args.username is not None and (not args.server or "\n" in args.username or "\r" in args.username):
        parser.error("--username requires a server address and a single-line username")
    if args.reset_trust and not args.server:
        parser.error("--reset-trust requires a server address")
    install_dependencies()
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    console = Console()
    console.print("[bold green]Native Wayland VNC viewer: installed[/bold green]")
    if args.install_only:
        console.print("Connect: ./vnc_viewer.py SERVER_IP:5902 --username SERVER_USER")
        return
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    display = os.environ.get("WAYLAND_DISPLAY")
    if not display or not (runtime / display).exists():
        raise RuntimeError("Run the viewer from a terminal inside your Wayland desktop session")
    if not args.server:
        if not sys.stdin.isatty():
            parser.error("provide the server IP:5902 when running without an interactive terminal")
        from rich.prompt import Prompt
        while not args.server:
            try:
                args.server = server_address(Prompt.ask("Server IP or hostname (optional :5902 or :5901)"))
            except argparse.ArgumentTypeError as error:
                console.print(Text(str(error), style="red"))
    console.print(Panel(Text(
        "1. Run vnc_setup.py on the PC you want to control.\n"
        f"2. This PC will open a saved VNC connection to {args.server}.\n"
        "3. Certificate trust is configured automatically before the viewer opens.\n"
        "4. Sign in with that server's Linux username and password.\n"
        f"Compression preset: {args.quality}. Use --quality balanced or --quality best for higher quality.\n"
        "Use its LAN address on the same network, or its Tailscale address on the same tailnet.\n"
        "Saved connections, reconnecting and deleting entries are available inside Remmina."
    ), title="Connect from this PC", border_style="cyan"))
    profile = connection_profile(args.server, args.username, args.quality)
    configure_certificate(args.server, profile, args.reset_trust)
    console.print("[green]Server certificate: verified (saved trust on first use)[/green]")
    disable_applet()
    command = ["remmina", "--no-tray-icon", "--connect", str(profile)]
    console.print(Text("Opening " + args.server + "…"))
    os.execvpe("remmina", command, {**os.environ, "GDK_BACKEND": "wayland"})


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, RuntimeError, configparser.Error, subprocess.SubprocessError) as error:
        try:
            from rich.console import Console
            Console(stderr=True).print(str(error), markup=False, style="red")
        except ModuleNotFoundError:
            print(str(error), file=sys.stderr)
        sys.exit(1)

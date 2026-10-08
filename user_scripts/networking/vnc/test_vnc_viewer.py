"""Receiving-PC regressions without installing packages or opening windows."""

import argparse
import configparser
import os
from pathlib import Path
import subprocess
import socket
import ssl
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

import vnc_viewer as viewer
import vnc_common as common


class ViewerTests(unittest.TestCase):
    def test_applet_disabled_before_launch_preserves_other_preferences(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}), \
             patch.object(viewer.Path, "home", return_value=Path(directory) / "home"):
            preference = Path(directory) / "remmina/remmina.pref"
            viewer.disable_applet()
            self.assertIn("disable_tray_icon = true", preference.read_text())
            settings = configparser.ConfigParser(interpolation=None)
            settings.read(preference)
            settings["remmina_pref"]["custom_setting"] = "keep"
            settings["remmina_pref"]["disable_tray_icon"] = "false"
            with preference.open("w") as output:
                settings.write(output)
            for config in (Path(directory), Path(directory) / "home/.config"):
                applet = config / "autostart/remmina-applet.desktop"
                applet.parent.mkdir(parents=True)
                applet.write_text("[Desktop Entry]\nExec=remmina -i\n")
            viewer.disable_applet()
            settings.read(preference)
            self.assertEqual(settings["remmina_pref"]["custom_setting"], "keep")
            self.assertEqual(settings["remmina_pref"]["disable_tray_icon"], "true")
            self.assertFalse((Path(directory) / "autostart/remmina-applet.desktop").exists())
            self.assertFalse((Path(directory) / "home/.config/autostart/remmina-applet.desktop").exists())

    def test_real_tls_bootstrap_and_reuse_after_address_refresh(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(common, "addresses", return_value=[]):
            root = Path(directory)
            key, cert, config = root / "key.pem", root / "cert.pem", root / "server.conf"
            self.assertTrue(common.write_config(config, key, cert, 5902))
            original_key = key.read_bytes()
            original_certificate = cert.read_bytes()
            self.assertFalse(common.write_config(config, key, cert, 5902))
            with patch.object(common, "addresses", return_value=[("tailscale0", "100.64.0.1")]):
                self.assertTrue(common.write_config(config, key, cert, 5902))
            self.assertEqual(key.read_bytes(), original_key)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.num_tickets = 0  # Certificate probes close before session resumption is useful.
            context.load_cert_chain(cert, key)
            errors = []
            rejected_certificates = []
            stop = threading.Event()
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                listener.settimeout(0.1)
                def serve():
                    while not stop.is_set():
                        try:
                            connection, _ = listener.accept()
                        except socket.timeout:
                            continue
                        try:
                            with connection:
                                def receive(size):
                                    data = bytearray()
                                    while len(data) < size:
                                        chunk = connection.recv(size - len(data))
                                        if not chunk:
                                            raise RuntimeError("fixture connection closed")
                                        data.extend(chunk)
                                    return bytes(data)
                                connection.sendall(b"RFB 003.008\n")
                                self.assertEqual(receive(12), b"RFB 003.008\n")
                                connection.sendall(b"\x01\x13")
                                self.assertEqual(receive(1), b"\x13")
                                connection.sendall(b"\x00\x02")
                                self.assertEqual(receive(2), b"\x00\x02")
                                connection.sendall(b"\x00\x01" + struct.pack("!I", 262))
                                self.assertEqual(receive(4), struct.pack("!I", 262))
                                connection.sendall(b"\x01")
                                with context.wrap_socket(connection, server_side=True) as tls:
                                    tls.recv(1)
                        except ssl.SSLError as error:
                            if error.reason == "TLSV1_ALERT_UNKNOWN_CA":
                                rejected_certificates.append(error)
                            else:
                                errors.append(error)
                        except Exception as error:
                            errors.append(error)
                worker = threading.Thread(target=serve, daemon=True)
                worker.start()
                try:
                    server = f"127.0.0.1:{listener.getsockname()[1]}"
                    profile = root / "client.remmina"
                    viewer.configure_certificate(server, profile)
                    ca = profile.with_suffix(".crt")
                    trusted = ca.read_bytes()
                    viewer.configure_certificate(server, profile)
                    self.assertEqual(ca.read_bytes(), trusted)
                    old_profile = root / "existing-client.remmina"
                    old_profile.with_suffix(".crt").write_bytes(original_certificate)
                    viewer.configure_certificate(server, old_profile)
                    self.assertEqual(old_profile.with_suffix(".crt").read_bytes(), cert.read_bytes())
                    common.write_config(root / "replacement.conf", root / "replacement.key", root / "replacement.crt", 5902)
                    context.load_cert_chain(root / "replacement.crt", root / "replacement.key")
                    with self.assertRaisesRegex(RuntimeError, "identity replacement"):
                        viewer.configure_certificate(server, profile)
                    self.assertEqual(ca.read_bytes(), trusted)
                finally:
                    stop.set()
                    worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(rejected_certificates), 2)

    def test_certificate_mismatch_reports_server_repair_and_retains_saved_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "client.remmina"
            ca = profile.with_suffix(".crt")
            ca.write_text("existing trusted certificate")
            error = ssl.SSLCertVerificationError("IP address mismatch")
            error.verify_message = "IP address mismatch"
            error.verify_code = 64
            with patch.object(viewer.ssl, "SSLContext") as context, \
                 patch.object(viewer, "server_certificate", side_effect=error), \
                 self.assertRaisesRegex(RuntimeError, "updated vnc_setup.py --setup"):
                viewer.configure_certificate("server:5902", profile)
            context.return_value.load_verify_locations.assert_called_once_with(cafile=str(ca))
            self.assertEqual(ca.read_text(), "existing trusted certificate")

    def test_addresses_use_explicit_port_and_reject_invalid_inputs(self):
        for source, expected in (("192.168.1.11", "192.168.1.11:5902"),
                                 ("100.104.37.85:5901", "100.104.37.85:5901"),
                                 ("pc.example:5902", "pc.example:5902"),
                                 ("[::1]:5902", "[::1]:5902")):
            self.assertEqual(viewer.server_address(source), expected)
        for source in ("192.168.1.11::5902", "host:0", "host:65536", "vnc://host",
                       "user@host", "host/path", "host\nprotocol=GVNC"):
            with self.subTest(source=source), self.assertRaises(argparse.ArgumentTypeError):
                viewer.server_address(source)

    def test_profile_preserves_preferences_and_uses_user_data_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_DATA_HOME": directory}):
            profile = viewer.connection_profile("server:5902", "alice")
            self.assertEqual(profile.parent, Path(directory) / "remmina")
            self.assertEqual(profile.stat().st_mode & 0o777, 0o600)
            settings = configparser.ConfigParser(interpolation=None)
            settings.read(profile)
            self.assertEqual(settings["remmina"]["protocol"], "VNC")
            self.assertEqual(settings["remmina"]["quality"], "1")
            self.assertNotIn("password", settings["remmina"])
            settings["remmina"]["viewonly"] = "1"
            settings["remmina"]["quality"] = "1"
            with profile.open("w") as output:
                settings.write(output)
            before = profile.read_bytes()
            self.assertEqual(viewer.connection_profile("server:5902", None), profile)
            self.assertEqual(profile.read_bytes(), before)
            viewer.connection_profile("server:5902", "bob")
            settings.read(profile)
            self.assertEqual(settings["remmina"]["viewonly"], "1")
            self.assertEqual(settings["remmina"]["username"], "bob")
            self.assertEqual(settings["remmina"]["quality"], "1")
            viewer.connection_profile("server:5902", "bob", "best")
            settings.read(profile)
            self.assertEqual(settings["remmina"]["quality"], "9")
            viewer.connection_profile("server:5902", None)
            settings.read(profile)
            self.assertEqual(settings["remmina"]["quality"], "1")
            self.assertEqual(settings["remmina"]["viewonly"], "1")
            self.assertEqual(settings["remmina"]["username"], "bob")
            viewer.connection_profile("server:5902", None, "balanced")
            settings.read(profile)
            self.assertEqual(settings["remmina"]["quality"], "2")
            settings["remmina"].pop("quality")
            with profile.open("w") as output:
                settings.write(output)
            viewer.connection_profile("server:5902", None)
            settings.read(profile)
            self.assertEqual(settings["remmina"]["quality"], "1")

    def test_missing_packages_are_batched_and_only_pacman_is_elevated(self):
        with patch.object(viewer.shutil, "which", side_effect=[None, None, "/usr/bin/remmina", "/usr/bin/openssl"]), \
             patch.object(viewer.ctypes.util, "find_library", side_effect=[None, "libvncclient.so.1"]), \
             patch.object(viewer.importlib.util, "find_spec", side_effect=[None, object()]), \
             patch.object(viewer.subprocess, "run") as run:
            viewer.install_dependencies()
        run.assert_called_once_with(["sudo", "pacman", "-S", "--needed", "--noconfirm",
                                     "remmina", "openssl", "libvncserver", "python-rich"], check=True)

    def test_failed_install_does_not_continue(self):
        with patch.object(viewer.shutil, "which", return_value=None), \
             patch.object(viewer.ctypes.util, "find_library", return_value=None), \
             patch.object(viewer.importlib.util, "find_spec", return_value=None), \
             patch.object(viewer.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pacman")), \
             self.assertRaises(subprocess.CalledProcessError):
            viewer.install_dependencies()

    def test_launcher_forces_wayland_without_changing_parent_environment(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory, "WAYLAND_DISPLAY": "wayland-test", "GDK_BACKEND": "x11"}), \
             patch.object(viewer.sys, "argv", ["vnc_viewer.py", "server:5902", "--username", "alice"]), \
             patch.object(viewer.os, "geteuid", return_value=1000), \
             patch.object(viewer, "install_dependencies"), \
             patch.object(viewer, "connection_profile", return_value=Path(directory) / "connection.remmina"), \
             patch.object(viewer, "configure_certificate"), \
             patch.object(viewer, "disable_applet"), \
             patch.object(viewer.os, "execvpe") as launch:
            (Path(directory) / "wayland-test").touch()
            viewer.main()
            viewer.connection_profile.assert_called_once_with("server:5902", "alice", "fast")
            self.assertEqual(launch.call_args.args[1], ["remmina", "--no-tray-icon", "--connect", str(Path(directory) / "connection.remmina")])
            self.assertEqual(launch.call_args.args[2]["GDK_BACKEND"], "wayland")
            self.assertEqual(os.environ["GDK_BACKEND"], "x11")


if __name__ == "__main__":
    unittest.main()

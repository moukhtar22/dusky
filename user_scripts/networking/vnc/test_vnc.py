"""Focused regressions; does not change services, firewall rules, or networks.

Run: python -m unittest discover -s user_scripts/networking/vnc -p 'test_*.py'
"""

import json
from contextlib import ExitStack
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import second_display as phone
import vnc_common as common
import vnc_setup as desktop


def completed(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "")


class ProtocolTests(unittest.TestCase):
    def test_fragmented_greeting(self):
        with patch.object(common.socket, "create_connection") as connect:
            connect.return_value.__enter__.return_value.recv.side_effect = [b"RFB ", b"003.", b"008\n"]
            self.assertTrue(common.rfb_ready(5902))

    def test_wrong_greeting(self):
        with patch.object(common.socket, "create_connection") as connect:
            connect.return_value.__enter__.return_value.recv.return_value = b"HTTP/1.1 200"
            self.assertFalse(common.rfb_ready(5902))

    def test_early_close(self):
        with patch.object(common.socket, "create_connection") as connect:
            connect.return_value.__enter__.return_value.recv.side_effect = [b"RFB ", b""]
            self.assertFalse(common.rfb_ready(5902))

    def test_timeout(self):
        with patch.object(common.socket, "create_connection", side_effect=socket.timeout):
            self.assertFalse(common.rfb_ready(5902))

    def test_control_error_is_not_ready(self):
        with patch.object(common, "run", return_value=completed(code=1)):
            self.assertIsNone(common.control_data(Path("/no/socket"), "output-list"))

    def test_wrong_control_shape_is_not_ready(self):
        for raw in ('{"error": "failed"}', '["invalid"]', 'broken'):
            with self.subTest(raw=raw), patch.object(common, "run", return_value=completed(raw)):
                self.assertIsNone(common.control_data(Path("/no/socket"), "output-list"))


class SetupTests(unittest.TestCase):
    def test_tailscale_certificate_refresh_preserves_vnc_off_switch(self):
        for active in (True, False):
            with self.subTest(active=active), patch.object(desktop, "ensure_dependencies"), \
                 patch.object(desktop, "CONFIG") as config, patch.object(desktop, "configure", return_value=True), \
                 patch.object(desktop, "wait_ready"), patch.object(desktop, "message"), \
                 patch.object(desktop, "rfb_ready", return_value=active):
                config.is_file.return_value = True
                def command(*args, **kwargs):
                    if args[0] == "tailscale":
                        return completed("100.64.0.1")
                    if "is-enabled" in args:
                        return completed("enabled")
                    if "is-active" in args:
                        return completed("active" if "--user" not in args or active else "inactive")
                    return completed()
                with patch.object(desktop, "run", side_effect=command) as run:
                    desktop.remote()
                restart = unittest.mock.call("systemctl", "--user", "restart", common.MASTER)
                self.assertEqual(restart in run.call_args_list, active)
                self.assertNotIn(unittest.mock.call("systemctl", "--user", "start", common.MASTER), run.call_args_list)

    def test_first_run_creates_desktop_unit_in_empty_directory(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            unit = Path(directory) / common.MASTER
            stack.enter_context(patch.object(desktop, "UNIT", unit))
            for name in ("prepare", "configure_firewall", "configure", "wait_ready"):
                stack.enter_context(patch.object(desktop, name))
            def command(*args, **kwargs):
                return completed("disabled" if "is-enabled" in args else "inactive" if "is-active" in args else "")
            deployment = stack.enter_context(patch.object(common, "run", side_effect=command))
            start = stack.enter_context(patch.object(desktop, "run", side_effect=command))
            self.assertFalse(unit.exists())
            desktop.install(show=False)
            self.assertEqual(unit.read_text(), desktop.unit_content())
            self.assertIn(unittest.mock.call("systemctl", "--user", "daemon-reload"), deployment.call_args_list)
            self.assertIn(unittest.mock.call("systemctl", "--user", "enable", common.MASTER), deployment.call_args_list)
            self.assertIn(unittest.mock.call("systemctl", "--user", "start", common.MASTER), start.call_args_list)

    def test_first_run_secondary_setup_deploys_both_units(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            desktop_unit = Path(directory) / common.MASTER
            display_unit = Path(directory) / common.PHONE
            stack.enter_context(patch.object(desktop, "UNIT", desktop_unit))
            stack.enter_context(patch.object(phone, "UNIT", display_unit))
            for module, names in ((desktop, ("prepare", "configure_firewall", "configure", "wait_ready")), (phone, ("prepare", "preferences", "configure", "wait_ready", "status"))):
                for name in names:
                    stack.enter_context(patch.object(module, name))
            def command(*args, **kwargs):
                return completed("disabled" if "is-enabled" in args else "inactive" if "is-active" in args else "")
            for module in (common, desktop, phone):
                stack.enter_context(patch.object(module, "run", side_effect=command))
            phone.install()
            self.assertEqual(desktop_unit.read_text(), desktop.unit_content())
            self.assertEqual(display_unit.read_text(), phone.unit_content())
            self.assertIn("BindsTo=dusky_vnc_desktop.service", display_unit.read_text())
            self.assertIn("second_display.py", display_unit.read_text())

    def test_interpreter_alias_does_not_change_service_command(self):
        with patch.object(common.sys, "executable", "/usr/bin/python"):
            first = common.script_command(Path(desktop.__file__), "serve")
        with patch.object(common.sys, "executable", "/usr/bin/python3"):
            self.assertEqual(first, common.script_command(Path(desktop.__file__), "serve"))

    def test_firewall_already_first_is_unchanged(self):
        rules = "Added user rules:\nufw allow 5901,5902/tcp comment 'Dusky VNC'\nufw deny 22/tcp\n"
        with patch.object(common.os, "geteuid", return_value=0), patch.object(common, "run", return_value=completed(rules)) as run:
            common.firewall_worker()
            self.assertEqual(run.call_args_list, [unittest.mock.call("ufw", "show", "added")])

    def test_firewall_failed_precedence_is_reported(self):
        with patch.object(common.os, "geteuid", return_value=0), patch.object(common, "run", return_value=completed("ufw deny 5901/tcp\n")):
            with self.assertRaisesRegex(RuntimeError, "prioritize"):
                common.firewall_worker()

    def test_active_offline_profile_is_not_modified(self):
        with patch.object(desktop, "offline_credentials", return_value=("VNC", "password")), patch.object(desktop.Path, "exists", return_value=True), patch.object(desktop, "run", side_effect=[completed("ap\n"), completed("activated\n")]) as run:
            with self.assertRaisesRegex(RuntimeError, "inactive Wi-Fi hotspot"):
                desktop.setup_offline_wifi("wlan1")
            self.assertTrue(all("modify" not in call.args for call in run.call_args_list))

    def test_client_profile_cannot_be_reused_as_hotspot(self):
        with patch.object(desktop, "offline_credentials", return_value=("Near", "password")), patch.object(desktop.Path, "exists", return_value=True), patch.object(desktop, "run", side_effect=[completed("infrastructure\n"), completed("")]):
            with self.assertRaisesRegex(RuntimeError, "inactive Wi-Fi hotspot"):
                desktop.setup_offline_wifi("wlan1")

    def test_offline_refuses_to_displace_wifi(self):
        with patch.object(desktop, "ensure_dependencies"), patch.object(desktop, "wifi_device", return_value=None), patch.object(desktop, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "unused second"):
                desktop.offline()
            run.assert_not_called()

    def test_active_wifi_is_not_a_hotspot_candidate(self):
        with patch.object(desktop, "run", side_effect=[completed("wlan0:wifi\n"), completed("100 (connected)\n")]) as run:
            self.assertIsNone(desktop.wifi_device())
            self.assertEqual(run.call_count, 2)

    def test_idle_ap_adapter_is_accepted(self):
        with patch.object(desktop, "run", side_effect=[completed("wlan0:wifi\nwlan1:wifi\n"),
                          completed("100 (connected)\n"), completed("30 (disconnected)\n"), completed("yes\n")]):
            self.assertEqual(desktop.wifi_device(), "wlan1")

    def test_orientation_read_does_not_write_defaults(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(phone, "PREFERENCES", Path(directory) / "absent.json"):
            self.assertEqual(phone.display_size(), (1280, 720))
            self.assertFalse(phone.PREFERENCES.exists())

    def test_invalid_orientation_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"orientation": "sideways"}')
            with patch.object(phone, "PREFERENCES", path), self.assertRaises(RuntimeError):
                phone.preferences()

    def test_failed_cleanup_keeps_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({"instance": "session"}))
            outputs = [{"name": phone.OUTPUT}]
            with patch.object(phone, "STATE", state), patch.object(phone, "hypr", return_value=completed(json.dumps(outputs))), patch.object(phone, "monitors", return_value=outputs):
                with self.assertRaisesRegex(RuntimeError, "removal failed"):
                    phone.cleanup()
                self.assertTrue(state.exists())

    def test_successful_cleanup_removes_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps({"instance": "session"}))
            with patch.object(phone, "STATE", state), patch.object(phone, "hypr", return_value=completed('[]')):
                phone.cleanup()
                self.assertFalse(state.exists())

    def test_atomic_write_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            self.assertTrue(common.atomic_write(path, "one"))
            inode = path.stat().st_ino
            self.assertFalse(common.atomic_write(path, "one"))
            self.assertEqual(path.stat().st_ino, inode)
            self.assertTrue(common.atomic_write(path, "two"))
            self.assertEqual(path.read_text(), "two")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_dependency_installation_is_batched_and_only_pacman_is_elevated(self):
        available = set()
        def install(*args, **kwargs):
            available.update({"wayvnc", "wayvncctl", "openssl"})
            return completed()
        with patch.object(common.shutil, "which", side_effect=lambda name: "/usr/bin/" + name if name in available else None), patch.object(common.importlib.util, "find_spec", return_value=object()), patch.object(common.subprocess, "run", side_effect=install) as installer, patch.object(common, "message"):
            requirements = {"wayvnc": ("wayvnc", "wayvncctl"), "openssl": ("openssl",)}
            common.ensure_dependencies(requirements)
            self.assertEqual(installer.call_args.args[0], ["sudo", "pacman", "-S", "--needed", "--noconfirm", "wayvnc", "openssl"])
            common.ensure_dependencies(requirements)
            self.assertEqual(installer.call_count, 1)

    def test_failed_dependency_installation_cannot_continue(self):
        with patch.object(common.shutil, "which", return_value=None), patch.object(common.importlib.util, "find_spec", return_value=object()), patch.object(common.subprocess, "run", return_value=completed()), patch.object(common, "message"):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                common.ensure_dependencies({"wayvnc": ("wayvnc",)})

    def test_disconnect_only_targets_requested_viewer(self):
        clients = [{"id": 1}, {"id": "2"}]
        with patch.object(common, "control_data", return_value=clients), patch.object(common, "run", return_value=completed()) as run, patch.object(common, "message"):
            common.disconnect_clients(Path("/test.sock"), "2")
            run.assert_called_once_with("wayvncctl", "-S", "/test.sock", "client-disconnect", "2")
            run.reset_mock()
            common.disconnect_clients(Path("/test.sock"))
            self.assertEqual(run.call_count, 2)

    def test_invalid_disconnect_id_does_not_reset_server(self):
        with patch.object(common, "control_data", return_value=[{"id": 1}]), patch.object(common, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "not found"):
                common.disconnect_clients(Path("/test.sock"), "999")
            run.assert_not_called()

    def test_unavailable_control_cannot_claim_disconnect_success(self):
        with patch.object(common, "control_data", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                common.disconnect_clients(Path("/test.sock"))

    def test_reconnect_restores_enabled_phone_service(self):
        with patch.object(desktop, "prepare"), patch.object(desktop, "UNIT") as unit, patch.object(desktop, "run", side_effect=[completed("enabled"), completed(), completed("enabled"), completed()]) as run, patch.object(desktop, "wait_ready") as wait, patch.object(desktop, "status"):
            unit.exists.return_value = True
            desktop.reconnect()
            self.assertIn(unittest.mock.call("systemctl", "--user", "restart", common.MASTER), run.call_args_list)
            self.assertIn(unittest.mock.call("systemctl", "--user", "start", common.PHONE), run.call_args_list)
            self.assertEqual(wait.call_count, 2)

    def test_reconnect_keeps_disabled_phone_service_off(self):
        with patch.object(desktop, "prepare"), patch.object(desktop, "UNIT") as unit, patch.object(desktop, "run", side_effect=[completed("enabled"), completed(), completed("disabled")]) as run, patch.object(desktop, "wait_ready"), patch.object(desktop, "status"):
            unit.exists.return_value = True
            desktop.reconnect()
            self.assertFalse(any(call.args == ("systemctl", "--user", "start", common.PHONE) for call in run.call_args_list))

    def test_flags_keep_legacy_actions_and_target_correct_control(self):
        for module in (desktop, phone):
            for flag, name in (("--reconnect", "reconnect"), ("--diagnose", "show_diagnostics"), ("--clients", "show_clients"), ("status", "status")):
                with self.subTest(module=module.__name__, flag=flag), patch.object(common.sys, "argv", [module.__name__, flag]), patch.object(module, name) as action:
                    module.main()
                    action.assert_called_once()
            with patch.object(common.sys, "argv", [module.__name__, "--disconnect", "2"]), patch.object(module, "disconnect_clients") as disconnect:
                module.main()
                disconnect.assert_called_once_with(module.CONTROL, "2")

    def test_phone_reconnect_does_not_reconfigure_healthy_master(self):
        with patch.object(phone, "UNIT") as unit, patch.object(phone, "prepare"), patch.object(phone, "run", side_effect=[completed("enabled"), completed(), completed()]), patch.object(phone, "wait_ready"), patch.object(phone, "status"), patch.object(desktop, "ready", return_value=True), patch.object(desktop, "install") as install:
            unit.exists.return_value = True
            phone.reconnect()
            install.assert_not_called()

    def test_phone_reconnect_recovers_disabled_master(self):
        with patch.object(phone, "UNIT") as unit, patch.object(phone, "prepare"), patch.object(phone, "run", side_effect=[completed("disabled"), completed(), completed()]), patch.object(phone, "wait_ready"), patch.object(phone, "status"), patch.object(desktop, "install") as install:
            unit.exists.return_value = True
            phone.reconnect()
            install.assert_called_once_with(show=False)


if __name__ == "__main__":
    unittest.main()

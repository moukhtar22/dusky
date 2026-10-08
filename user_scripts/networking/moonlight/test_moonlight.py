"""Focused regressions; never changes live services or network settings."""

import json
from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import moonlight_setup as moon


def result(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "")


class MoonlightTests(unittest.TestCase):
    def test_first_setup_writes_and_enables_unit_in_empty_directory(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            unit = Path(directory) / moon.UNIT_NAME
            stack.enter_context(patch.object(moon, "UNIT", unit))
            for name in ("ensure_dependencies", "sunshine_package", "configure_firewall", "status", "open_pairing_page"):
                stack.enter_context(patch.object(moon, name))
            for name, value in {"session": {"instance": "session"}, "preferences": {}, "write_config": False, "repair_duplicate_pairings": 0, "ready": True}.items():
                stack.enter_context(patch.object(moon, name, return_value=value))
            def command(*args, **kwargs):
                return result("disabled" if "is-enabled" in args else "inactive" if "is-active" in args else "")
            run = stack.enter_context(patch.object(moon, "run", side_effect=command))
            self.assertFalse(unit.exists())
            moon.setup(None)
            self.assertEqual(unit.read_text(), moon.unit_content())
            for command in ("daemon-reload", "enable", "start"):
                self.assertTrue(any(command in call.args for call in run.call_args_list))

    def test_serverinfo_accepts_free_and_busy_servers(self):
        for state in ("SUNSHINE_SERVER_FREE", "SUNSHINE_SERVER_BUSY"):
            with self.subTest(state=state), patch.object(moon.http.client, "HTTPConnection") as connect:
                response = connect.return_value.getresponse.return_value
                response.status = 200
                response.read.return_value = f'<root status_code="200"><state>{state}</state></root>'.encode()
                self.assertTrue(moon.listening())
                connect.return_value.close.assert_called_once()

    def test_wrong_protocol_is_not_ready(self):
        for body in (b"garbage", b'<html/>', b'<root status_code="503"><state>SUNSHINE_SERVER_FREE</state></root>'):
            with self.subTest(body=body), patch.object(moon.http.client, "HTTPConnection") as connect:
                response = connect.return_value.getresponse.return_value
                response.status = 200
                response.read.return_value = body
                self.assertFalse(moon.listening())

    def test_http_error_is_not_ready(self):
        with patch.object(moon.http.client, "HTTPConnection") as connect:
            connect.return_value.getresponse.return_value.status = 503
            self.assertFalse(moon.listening())

    def test_server_timeout_closes_connection(self):
        with patch.object(moon.http.client, "HTTPConnection") as connect:
            connect.return_value.request.side_effect = TimeoutError
            self.assertFalse(moon.listening())
            connect.return_value.close.assert_called_once()

    def test_preferences_read_has_no_side_effect(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(moon, "PREFERENCES", Path(directory) / "absent.json"):
            self.assertEqual(moon.display_size(), (1280, 720))
            self.assertFalse(moon.PREFERENCES.exists())

    def test_failed_output_removal_keeps_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text('{"instance":"session"}')
            outputs = [{"name": moon.OUTPUT}]
            with patch.object(moon, "STATE", state), patch.object(moon, "hypr", return_value=result(json.dumps(outputs))), patch.object(moon, "monitors", return_value=outputs):
                with self.assertRaisesRegex(RuntimeError, "removal failed"):
                    moon.cleanup()
                self.assertTrue(state.exists())

    def test_unreachable_live_compositor_keeps_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text('{"instance":"session"}')
            with patch.object(moon, "STATE", state), patch.object(moon, "hypr", return_value=result(code=1)), patch.object(moon, "run", return_value=result('[{"instance":"session"}]')):
                with self.assertRaisesRegex(RuntimeError, "retained"):
                    moon.cleanup()
                self.assertTrue(state.exists())

    def test_exited_compositor_discards_stale_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text('{"instance":"gone"}')
            with patch.object(moon, "STATE", state), patch.object(moon, "hypr", return_value=result(code=1)), patch.object(moon, "run", return_value=result('[]')):
                moon.cleanup()
                self.assertFalse(state.exists())

    def test_config_keeps_custom_options_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(moon, "CONFIG", Path(directory) / "sunshine.conf"), patch.object(moon, "prefer_vaapi", return_value="/dev/dri/renderD129"):
            moon.CONFIG.write_text('max_bitrate = 12000\nencoder = wrong\nencoder = duplicate\n')
            self.assertTrue(moon.write_config())
            content = moon.CONFIG.read_text()
            self.assertIn('max_bitrate = 12000', content)
            self.assertIn('adapter_name = /dev/dri/renderD129', content)
            self.assertEqual(content.count('encoder ='), 1)
            self.assertFalse(moon.write_config())

    def test_no_vaapi_probe_leaves_encoder_selection_automatic(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(moon, "CONFIG", Path(directory) / "sunshine.conf"), patch.object(moon, "prefer_vaapi", return_value=None):
            moon.write_config()
            self.assertNotIn('encoder =', moon.CONFIG.read_text())
            self.assertNotIn('adapter_name =', moon.CONFIG.read_text())

    def test_repeated_firewall_configuration_is_read_only(self):
        raw = "ufw allow 47984,47989,48010/tcp comment 'Dusky Moonlight display'\nufw allow 47998:48000/udp comment 'Dusky Moonlight display'\nufw deny 22/tcp\n"
        with patch.object(moon.os, "geteuid", return_value=0), patch.object(moon, "run", return_value=result(raw)) as run:
            moon.firewall_worker()
            self.assertEqual(run.call_count, 1)

    @patch.object(moon, "ensure_dependencies")
    def test_setup_does_not_configure_usb_network(self, _dependencies):
        with tempfile.TemporaryDirectory() as directory, patch.object(moon, "UNIT", Path(directory) / "test.service"), patch.object(moon, "session", return_value={"instance":"session"}), patch.object(moon, "sunshine_package"), patch.object(moon, "configure_firewall"), patch.object(moon, "write_config", return_value=False), patch.object(moon, "repair_duplicate_pairings", return_value=0), patch.object(moon, "atomic_write", return_value=False), patch.object(moon, "run", side_effect=[result("enabled"), result("active")]), patch.object(moon, "ready", return_value=True), patch.object(moon, "status"), patch.object(moon, "open_pairing_page") as browser, patch.object(moon, "setup_iphone_usb") as usb:
            moon.setup(None)
            usb.assert_not_called()
            browser.assert_called_once_with(only_unpaired=True)

    def test_setup_restarts_for_duplicate_pairings_only(self):
        for duplicates in (0, 2):
            with self.subTest(duplicates=duplicates), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                stack.enter_context(patch.object(moon, "UNIT", Path(directory) / "test.service"))
                for name in ("ensure_dependencies", "sunshine_package", "configure_firewall", "status", "open_pairing_page"):
                    stack.enter_context(patch.object(moon, name))
                for name, value in {"session": {"instance": "session"}, "preferences": {}, "write_config": False, "atomic_write": False, "ready": True, "repair_duplicate_pairings": duplicates}.items():
                    stack.enter_context(patch.object(moon, name, return_value=value))
                run = stack.enter_context(patch.object(moon, "run", side_effect=[result("enabled"), result("active"), result()]))
                moon.setup(None)
                restarts = [call.args for call in run.call_args_list if "restart" in call.args]
                self.assertEqual(restarts, [("systemctl", "--user", "restart", moon.UNIT_NAME)] if duplicates else [])

    def test_duplicate_pairings_keep_credentials_other_clients_and_backup(self):
        cert = "-----BEGIN CERTIFICATE-----\nAQID\n-----END CERTIFICATE-----"
        other = "-----BEGIN CERTIFICATE-----\nBAUG\n-----END CERTIFICATE-----"
        with tempfile.TemporaryDirectory() as directory, patch.object(moon, "CONFIG", Path(directory) / "sunshine.conf"), patch.object(moon, "message"):
            path = Path(directory) / "sunshine_state.json"
            original = json.dumps({"username": "viewer", "password": "opaque-hash", "root": {"uniqueid": "server-id", "named_devices": [
                {"name": "old phone", "cert": cert, "enabled": "true"},
                {"name": "other", "cert": other},
                {"name": "new phone", "cert": cert + "\n", "enabled": "true"}]}})
            path.write_text(original)
            self.assertEqual(moon.repair_duplicate_pairings(repair=False), 1)
            self.assertEqual(path.read_text(), original)
            self.assertFalse(path.with_suffix(".json.before-dedup").exists())
            self.assertEqual(moon.repair_duplicate_pairings(), 1)
            repaired = json.loads(path.read_text())
            self.assertEqual(repaired["password"], "opaque-hash")
            self.assertEqual(repaired["root"]["uniqueid"], "server-id")
            self.assertEqual([item["name"] for item in repaired["root"]["named_devices"]], ["new phone", "other"])
            self.assertEqual(path.with_suffix(".json.before-dedup").read_text(), original)
            self.assertEqual(moon.repair_duplicate_pairings(), 0)

    def test_pairing_repair_preserves_disabled_identity(self):
        cert = "-----BEGIN CERTIFICATE-----\nAQID\n-----END CERTIFICATE-----"
        for disabled in (False, "false"):
            for reverse in (False, True):
                with self.subTest(disabled=disabled, reverse=reverse), tempfile.TemporaryDirectory() as directory, patch.object(moon, "CONFIG", Path(directory) / "sunshine.conf"), patch.object(moon, "message"):
                    devices = [{"cert": cert, "enabled": True}, {"cert": cert, "enabled": disabled}]
                    path = Path(directory) / "sunshine_state.json"
                    path.write_text(json.dumps({"root": {"named_devices": devices[::-1] if reverse else devices}}))
                    self.assertEqual(moon.repair_duplicate_pairings(), 1)
                    self.assertEqual(json.loads(path.read_text())["root"]["named_devices"][0]["enabled"], disabled)

    def test_forget_client_preserves_credentials_and_restarts_only_active_service(self):
        for active in (True, False):
            with self.subTest(active=active), tempfile.TemporaryDirectory() as directory, patch.object(moon, "CONFIG", Path(directory) / "sunshine.conf"), patch.object(moon, "message"):
                path = Path(directory) / "sunshine_state.json"
                state = {"password": "opaque-hash", "root": {"uniqueid": "server-id", "named_devices": [{"uuid": "iphone"}, {"uuid": "android"}]}}
                path.write_text(json.dumps(state))
                with patch.object(moon, "run", side_effect=[result("active" if active else "inactive"), result(), result()]) as run:
                    moon.forget_clients("iphone")
                    commands = [call.args for call in run.call_args_list]
                    self.assertEqual(any("start" in command for command in commands), active)
                    self.assertIn(("systemctl", "--user", "stop", moon.UNIT_NAME), commands)
                saved = json.loads(path.read_text())
                self.assertEqual(saved["password"], "opaque-hash")
                self.assertEqual(saved["root"]["uniqueid"], "server-id")
                self.assertEqual(saved["root"]["named_devices"], [{"uuid": "android"}])
                self.assertEqual(json.loads(path.with_suffix(".json.before-client-removal").read_text()), state)

    def test_invalid_client_id_does_not_interrupt_stream(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(moon, "CONFIG", Path(directory) / "sunshine.conf"), patch.object(moon, "run") as run:
            (Path(directory) / "sunshine_state.json").write_text('{"root":{"named_devices":[{"uuid":"iphone"}]}}')
            with self.assertRaisesRegex(RuntimeError, "not found"):
                moon.forget_clients("unknown")
            run.assert_not_called()

    def test_forget_all_keeps_web_credentials_and_resumes_after_write_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(moon, "CONFIG", Path(directory) / "sunshine.conf"), patch.object(moon, "message"):
            path = Path(directory) / "sunshine_state.json"
            path.write_text('{"username":"viewer","root":{"named_devices":[{"uuid":"iphone"},{"uuid":"android"}]}}')
            with patch.object(moon, "run", side_effect=[result("active"), result(), result()]):
                moon.forget_clients()
            self.assertEqual(json.loads(path.read_text()), {"username": "viewer", "root": {"named_devices": []}})
            path.write_text('{"root":{"named_devices":[{"uuid":"iphone"}]}}')
            with patch.object(moon, "run", side_effect=[result("active"), result(), result()]) as run, patch.object(moon, "atomic_write", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    moon.forget_clients()
                self.assertEqual(run.call_args.args, ("systemctl", "--user", "start", moon.UNIT_NAME))
                self.assertEqual(len(json.loads(path.read_text())["root"]["named_devices"]), 1)

    def test_action_flags_dispatch_without_live_side_effects(self):
        for flag, function, arguments in (
            ("--status", "status", []), ("--reconnect", "reconnect", []),
            ("--diagnose", "diagnose", []), ("--clients", "clients", []),
            ("--pair", "pair", []), ("--test-display", "test_display", []),
            ("--forget-client", "forget_clients", ["iphone"]),
            ("--forget-all", "forget_clients", []),
            ("--orientation", "orientation", ["portrait"]),
        ):
            with self.subTest(flag=flag), patch.object(moon.sys, "argv", ["moonlight_setup.py", flag, *arguments]), patch.object(moon, function) as action:
                moon.main()
                action.assert_called_once_with(*(arguments if arguments else [None] if flag == "--forget-all" else []))

    def test_first_setup_opens_browser_for_unpaired_state(self):
        for state in (None, {"username": "viewer"}, {"root": {"named_devices": []}}):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory, patch.object(moon, "CONFIG", Path(directory) / "sunshine.conf"), patch.object(moon.shutil, "which", return_value="/usr/bin/xdg-open"), patch.object(moon.subprocess, "run", return_value=result()) as browser, patch.object(moon, "message"):
                if state is not None:
                    (Path(directory) / "sunshine_state.json").write_text(json.dumps(state))
                moon.open_pairing_page(only_unpaired=True)
                self.assertEqual(browser.call_args.args[0], ["xdg-open", "https://localhost:47990"])

    def test_existing_pairing_skips_automatic_browser_but_explicit_pair_opens(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(moon, "CONFIG", Path(directory) / "sunshine.conf"), patch.object(moon.shutil, "which", return_value="/usr/bin/xdg-open"), patch.object(moon.subprocess, "run", return_value=result()) as browser, patch.object(moon, "message"):
            (Path(directory) / "sunshine_state.json").write_text('{"root":{"named_devices":[{"uuid":"iphone"}]}}')
            moon.open_pairing_page(only_unpaired=True)
            browser.assert_not_called()
            moon.open_pairing_page()
            browser.assert_called_once()

    def test_browser_failure_keeps_manual_pairing_available(self):
        for failure in (result(code=1), subprocess.TimeoutExpired("xdg-open", 10)):
            with self.subTest(failure=failure), patch.object(moon.shutil, "which", return_value="/usr/bin/xdg-open"), patch.object(moon.subprocess, "run", side_effect=failure if isinstance(failure, Exception) else None, return_value=failure), patch.object(moon, "message") as message:
                moon.open_pairing_page()
                self.assertIn("manually", message.call_args.args[0])
                self.assertIn("https://localhost:47990", message.call_args.args[0])

    def test_installed_dependencies_do_not_invoke_sudo(self):
        with patch.object(moon.shutil, "which", return_value="/usr/bin/tool"), patch.object(moon.importlib.util, "find_spec", return_value=object()), patch.object(moon.subprocess, "run") as installer:
            moon.ensure_dependencies()
            installer.assert_not_called()

    def test_missing_dependencies_are_batched_and_verified(self):
        installed = set()
        def install(*args, **kwargs):
            installed.update({"hyprctl", "systemctl", "ip", "vainfo", "xdg-open"})
            return result()
        with patch.object(moon.shutil, "which", side_effect=lambda command: "/usr/bin/" + command if command in installed else None), patch.object(moon.importlib.util, "find_spec", return_value=object()), patch.object(moon.subprocess, "run", side_effect=install) as installer, patch.object(moon, "message"):
            moon.ensure_dependencies()
            self.assertEqual(installer.call_args.args[0], ["sudo", "pacman", "-S", "--needed", "--noconfirm", "hyprland", "systemd", "iproute2", "libva-utils", "xdg-utils"])

    def test_sunshine_uses_configured_repository_before_aur(self):
        with patch.object(moon.shutil, "which", side_effect=[None, "/usr/bin/sunshine"]), patch.object(moon, "run", return_value=result()), patch.object(moon.subprocess, "run", return_value=result()) as installer, patch.object(moon, "aur_helper") as aur, patch.object(moon, "message"):
            moon.sunshine_package(None)
            installer.assert_called_once_with(["sudo", "pacman", "-S", "--needed", "--noconfirm", "sunshine"], check=True)
            aur.assert_not_called()

    def test_sunshine_aur_helper_runs_without_sudo(self):
        with patch.object(moon.shutil, "which", side_effect=[None, "/usr/bin/sunshine"]), patch.object(moon, "run", return_value=result(code=1)), patch.object(moon, "aur_helper", return_value="/usr/bin/paru"), patch.object(moon.subprocess, "run", return_value=result()) as installer, patch.object(moon, "message"):
            moon.sunshine_package(None)
            installer.assert_called_once_with(["/usr/bin/paru", "-S", "--needed", "--noconfirm", "sunshine-bin"], check=True)

    def test_bootstrap_build_is_unprivileged_and_install_uses_makepkg(self):
        with patch.object(moon.shutil, "which", side_effect=[None, None, "/usr/bin/paru"]), patch.object(moon.subprocess, "run", return_value=result()) as installer, patch.object(moon, "message"):
            self.assertEqual(moon.aur_helper(), "/usr/bin/paru")
            commands = [call.args[0] for call in installer.call_args_list]
            self.assertEqual(commands[0], ["sudo", "pacman", "-S", "--needed", "--noconfirm", "git", "base-devel"])
            self.assertEqual(commands[1][:3], ["git", "clone", "https://aur.archlinux.org/paru.git"])
            self.assertEqual(commands[2], ["makepkg", "-si", "--needed", "--noconfirm"])

    def test_sunshine_failed_installation_is_reported(self):
        with patch.object(moon.shutil, "which", return_value=None), patch.object(moon, "run", return_value=result()), patch.object(moon.subprocess, "run", return_value=result()), patch.object(moon, "message"):
            with self.assertRaisesRegex(RuntimeError, "did not provide"):
                moon.sunshine_package(None)

    def test_existing_sunshine_is_not_reinstalled(self):
        with patch.object(moon.shutil, "which", return_value="/usr/bin/sunshine"), patch.object(moon, "run") as query, patch.object(moon.subprocess, "run") as installer:
            moon.sunshine_package(None)
            query.assert_not_called()
            installer.assert_not_called()

    def test_missing_rich_is_installed_before_guidance(self):
        with patch.object(moon.shutil, "which", return_value="/usr/bin/tool"), patch.object(moon.importlib.util, "find_spec", side_effect=[None, object()]), patch.object(moon.subprocess, "run", return_value=result()) as installer, patch.object(moon, "message"):
            moon.ensure_dependencies()
            installer.assert_called_once_with(["sudo", "pacman", "-S", "--needed", "--noconfirm", "python-rich"], check=True)


if __name__ == "__main__":
    unittest.main()

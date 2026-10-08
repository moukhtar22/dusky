"""Result reconciliation for systemd operations without changing live units."""

from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from python.engines.systemd import SystemdEngine


class SystemdEngineTests(unittest.TestCase):
    def test_batch_partial_failure_returns_each_observed_identity(self):
        engine = SystemdEngine()
        changes = [(name, "user", "true", "bool") for name in ("one.service", "two.service")]
        with patch.object(SystemdEngine, "list_unit_files", side_effect=[
            {"one.service": "disabled", "two.service": "disabled"},
            {"one.service": "enabled", "two.service": "disabled"},
        ]), patch.object(SystemdEngine, "_run", return_value=subprocess.CompletedProcess([], 1, "", "start failed")) as run:
            results = engine.write_batch_results(changes)
        self.assertEqual(set(results), {(name, "user") for name in ("one.service", "two.service")})
        self.assertEqual(results[("one.service", "user")].actual, "true")
        self.assertEqual(results[("two.service", "user")].actual, "false")
        self.assertFalse(any(result.ok for result in results.values()))
        run.assert_called_once()

    def test_oserror_after_write_rechecks_enablement(self):
        engine = SystemdEngine()
        with patch.object(SystemdEngine, "list_unit_files", side_effect=[
            {"one.service": "disabled"}, {"one.service": "enabled"},
        ]), patch.object(SystemdEngine, "_run", side_effect=OSError("lost client")):
            result = engine.write_value_result("one.service", "user", "true")
        self.assertFalse(result.ok)
        self.assertEqual(result.actual, "true")

    def test_runtime_enabled_and_indirect_readback_remain_distinct(self):
        engine = SystemdEngine()
        for initial, final, expected in (("enabled-runtime", "enabled", True), ("indirect", "indirect", False)):
            with self.subTest(initial=initial), patch.object(SystemdEngine, "list_unit_files", side_effect=[
                {"one.service": initial}, {"one.service": final},
            ]), patch.object(SystemdEngine, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                result = engine.write_value_result("one.service", "user", "true")
            self.assertEqual(result.ok, expected)
            self.assertEqual(result.actual, "true" if expected else "false")
            self.assertEqual(run.call_args.args[0], ["systemctl", "--user", "enable", "--now", "one.service"])

    def test_template_instance_is_discovered_through_manager(self):
        output = "Id=worker@one.service\nLoadState=loaded\nUnitFileState=enabled\nFragmentPath=/usr/lib/systemd/user/worker@.service\n"
        with patch.object(SystemdEngine, "_run", side_effect=[
            subprocess.CompletedProcess([], 0, "", ""), subprocess.CompletedProcess([], 0, output, ""),
        ]) as run:
            state = SystemdEngine.list_unit_files("user", ["worker@one.service"])
        self.assertEqual(state, {"worker@one.service": "enabled"})
        self.assertIn("--property=Id,LoadState,UnitFileState,FragmentPath", run.call_args.args[0])

    def test_batch_rejects_an_unknown_scope(self):
        engine = SystemdEngine()
        result = engine.write_batch_results([("sample.service", "wrong", "true", "bool")])
        self.assertFalse(result[("sample.service", "wrong")].ok)
        self.assertIn("Invalid systemd scope", result[("sample.service", "wrong")].message)

    def test_successful_command_does_not_invent_observed_state(self):
        engine = SystemdEngine()
        with patch.object(SystemdEngine, "list_unit_files", side_effect=[
            {"sample.service": "disabled"}, RuntimeError("readback failed"),
        ]), patch.object(SystemdEngine, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            result = engine.write_value_result("sample.service", "user", "true")
        self.assertTrue(result.ok)
        self.assertIsNone(result.actual)

    def test_timeout_reports_unverified_state(self):
        engine = SystemdEngine()
        with patch.object(SystemdEngine, "list_unit_files", side_effect=[
            {"sample.service": "disabled"}, RuntimeError("readback failed"),
        ]), patch.object(SystemdEngine, "_run", side_effect=subprocess.TimeoutExpired("systemctl", 1)):
            result = engine.write_value_result("sample.service", "user", "true")
        self.assertFalse(result.ok)
        self.assertIsNone(result.actual)
        self.assertIn("could not be verified", result.message)


if __name__ == "__main__":
    unittest.main()

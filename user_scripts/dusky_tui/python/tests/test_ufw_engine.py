#!/usr/bin/env python3
"""
Unit and regression tests for UfwEngine.
Exercises parsing, rule generation, domain management, framework toggles, and presets.
"""

import sys
import tempfile
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

# Ensure repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from python.engines import ufw as ufw_module
from python.engines.ufw import UfwEngine, RuleRecord, UfwError


class TestUfwEngine(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ("DOMAINS_STORAGE", "UFW_CONF", "UFW_BEFORE_RULES", "UFW_BEFORE6_RULES",
                     "UFW_AFTER_RULES", "UFW_AFTER6_RULES", "UFW_SYSCTL_CONF", "UFW_AFTER_INIT"):
            patcher = patch.object(ufw_module, name, root / name)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, prefix in (("UFW_BEFORE_RULES", "ufw"), ("UFW_BEFORE6_RULES", "ufw6")):
            getattr(ufw_module, name).write_text(f"*filter\n:{prefix}-before-input - [0:0]\n-A {prefix}-before-input -p icmp -m icmp --icmp-type echo-request -j ACCEPT\nCOMMIT\n")
        for name in ("UFW_AFTER_RULES", "UFW_AFTER6_RULES"):
            getattr(ufw_module, name).write_text("*filter\nCOMMIT\n")
        ufw_module.UFW_SYSCTL_CONF.write_text("net.ipv4.ip_forward=1\n")
        self.engine = UfwEngine(config_path=str(root / "default_ufw"))

    def test_status_verbose_parsing_active(self):
        sample_output = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), deny (routed)
New profiles: skip

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere                   # OpenSSH
"""
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, sample_output, "")):
            status = self.engine.get_status_verbose()

        self.assertTrue(status["active"])
        self.assertEqual(status["logging"], "low")
        self.assertEqual(status["default_incoming"], "deny")
        self.assertEqual(status["default_outgoing"], "allow")
        self.assertEqual(status["default_routed"], "deny")
        self.assertEqual(status["new_profiles"], "skip")

    def test_status_verbose_parsing_inactive(self):
        sample_output = "Status: inactive\n"
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, sample_output, "")):
            status = self.engine.get_status_verbose()

        self.assertFalse(status["active"])
        self.assertEqual(status["logging"], "off")

    def test_numbered_rules_parsing(self):
        sample_output = """Status: active

     To                         Action      From
     --                         ------      ----
[ 1] 22/tcp                     ALLOW IN    Anywhere                   # OpenSSH
[ 2] 41641/udp                  ALLOW IN    Anywhere                   # Tailscale Direct P2P
[ 3] Anywhere on tailscale0     ALLOW IN    Anywhere                   # Trust IN: tailscale0
[ 4] Anywhere on wlan0          ALLOW FWD   Anywhere on tailscale0     # Forward: tailscale0 -> WAN
[ 5] Anywhere on virbr0         ALLOW FWD   Anywhere                   (out)
[ 6] 80/tcp (v6)                DENY IN     Anywhere (v6)              # Block IPv6 HTTP
"""
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, sample_output, "")):
            rules = self.engine.get_numbered_rules()

        self.assertEqual(len(rules), 6)
        
        # Rule 1
        self.assertEqual(rules[0].number, 1)
        self.assertEqual(rules[0].to_addr, "22/tcp")
        self.assertEqual(rules[0].action, "ALLOW IN")
        self.assertEqual(rules[0].from_addr, "Anywhere")
        self.assertEqual(rules[0].comment, "OpenSSH")
        self.assertFalse(rules[0].is_v6)

        # Rule 4 (Route rule)
        self.assertEqual(rules[3].number, 4)
        self.assertEqual(rules[3].to_addr, "Anywhere on wlan0")
        self.assertEqual(rules[3].action, "ALLOW FWD")
        self.assertEqual(rules[3].from_addr, "Anywhere on tailscale0")
        self.assertEqual(rules[3].comment, "Forward: tailscale0 -> WAN")

        # Rule 6 (IPv6)
        self.assertEqual(rules[5].number, 6)
        self.assertTrue(rules[5].is_v6)
        self.assertEqual(rules[5].action, "DENY IN")

    def test_rule_command_construction(self):
        # 1. Simple rule
        self.engine.cache = {
            "builder/action": "allow",
            "builder/direction": "in",
            "builder/proto": "tcp",
            "builder/port": "22",
            "builder/source": "any",
            "builder/dest": "any",
            "builder/interface": "any",
            "builder/log": "none",
            "builder/comment": "SSH Inbound",
            "builder/placement": "append",
        }
        cmd = self.engine._construct_rule_command()
        self.assertEqual(cmd, ["ufw", "allow", "in", "proto", "tcp", "from", "any", "to", "any", "port", "22", "comment", "SSH Inbound"])

        # 2. Insert rule with specific interface and subnet
        self.engine.cache = {
            "builder/action": "deny",
            "builder/direction": "in",
            "builder/proto": "udp",
            "builder/port": "53",
            "builder/source": "192.168.1.0/24",
            "builder/dest": "any",
            "builder/interface": "eth0",
            "builder/log": "log",
            "builder/comment": "Block Local DNS",
            "builder/placement": "insert",
            "builder/insert_num": "3",
        }
        cmd = self.engine._construct_rule_command()
        self.assertEqual(
            cmd,
            ["ufw", "insert", "3", "deny", "in", "on", "eth0", "log", "proto", "udp", "from", "192.168.1.0/24", "to", "any", "port", "53", "comment", "Block Local DNS"],
        )

        # 3. Route forwarding rule
        self.engine.cache = {
            "builder/action": "allow",
            "builder/direction": "route",
            "builder/proto": "any",
            "builder/port": "",
            "builder/source": "any",
            "builder/dest": "any",
            "builder/interface": "tailscale0",
            "builder/out_interface": "wlan0",
            "builder/log": "none",
            "builder/comment": "Forward Tailscale to WAN",
            "builder/placement": "prepend",
        }
        cmd = self.engine._construct_rule_command()
        self.assertEqual(
            cmd,
            ["ufw", "route", "prepend", "allow", "in", "on", "tailscale0", "out", "on", "wlan0", "from", "any", "to", "any", "comment", "Forward Tailscale to WAN"],
        )

    def test_listening_ports_parsing(self):
        sample_output = """tcp:
  21 * (vsftpd)
   [19] allow from 192.168.29.0/24 to any port 21 proto tcp comment 'LAN FTP Control'

  22 * (sshd)
   [ 1] allow 22/tcp comment 'OpenSSH'

udp:
  41641 * (tailscaled)
   [ 2] allow 41641/udp comment 'Tailscale Direct P2P'
"""
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, sample_output, "")):
            listening = self.engine.get_listening_ports()

        self.assertEqual(len(listening), 3)
        self.assertEqual(listening[0]["port"], 21)
        self.assertEqual(listening[0]["process"], "vsftpd")
        self.assertEqual(listening[0]["proto"], "tcp")
        self.assertTrue(len(listening[0]["rules"]) > 0)

        self.assertEqual(listening[1]["port"], 22)
        self.assertEqual(listening[1]["process"], "sshd")

        self.assertEqual(listening[2]["port"], 41641)
        self.assertEqual(listening[2]["process"], "tailscaled")
        self.assertEqual(listening[2]["proto"], "udp")

    def test_domain_ips_resolution_mock(self):
        fake_addrinfo = [
            (2, 1, 6, '', ('93.184.216.34', 0)),
            (10, 1, 6, '', ('2606:2800:220:1:248:1893:25c8:1946', 0)),
        ]
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps([a[4][0] for a in fake_addrinfo]), "")):
            ips = self.engine.resolve_domain_ips("example.com")

        self.assertIn("93.184.216.34", ips)
        self.assertIn("2606:2800:220:1:248:1893:25c8:1946", ips)

    def test_presets_validation(self):
        valid_presets = [
            "dusky_full",
            "strict_workstation",
            "lockdown_whitelist",
            "dev_lan",
            "stealth",
            "streaming_moonlight",
            "factory_reset",
        ]
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "", "")), \
             patch.object(self.engine, "set_sysctl_forwarding", return_value=(True, "")), \
             patch.object(self.engine, "set_waydroid_nat", return_value=(True, "")), \
             patch.object(self.engine, "set_docker_mitigation", return_value=(True, "")), \
             patch.object(self.engine, "get_network_interfaces", return_value=[]):
            for preset in valid_presets:
                ok, msg = self.engine.apply_preset(preset)
                self.assertTrue(ok, f"Preset '{preset}' failed: {msg}")

        ok_bad, _ = self.engine.apply_preset("non_existent_preset")
        self.assertFalse(ok_bad)

    def test_reports_validation(self):
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "dummy report", "")):
            content = self.engine.get_report("listening")
            self.assertEqual(content, "dummy report")

        invalid_rep = self.engine.get_report("not_a_report")
        self.assertIn("Error: Invalid report type", invalid_rep)

    def test_domain_rules_application(self):
        data = {
            "whitelist_mode": True,
            "domains": [
                {
                    "domain": "test.com",
                    "action": "allow",
                    "ports": "80,443",
                    "ips": ["1.2.3.4"],
                },
                {
                    "domain": "blocked.com",
                    "action": "deny",
                    "ports": "any",
                    "ips": ["5.6.7.8"],
                },
            ],
        }
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "", "")) as mock_cmd, \
             patch.object(self.engine, "get_numbered_rules", return_value=[]):
            self.engine._apply_domain_rules(data)
            calls = [call.args[0] for call in mock_cmd.call_args_list]

            # Core rules when whitelist_mode is True
            self.assertNotIn(["ufw", "allow", "out", "on", "lo", "comment", "core:loopback"], calls)
            self.assertIn(["ufw", "allow", "out", "to", "any", "port", "53", "comment", "core:dns"], calls)
            # Whitelisted domain rule
            self.assertIn(["ufw", "allow", "out", "to", "1.2.3.4", "port", "80,443", "proto", "tcp", "comment", "domain:test.com"], calls)
            # Blocked domain rule
            self.assertIn(["ufw", "prepend", "deny", "out", "to", "5.6.7.8", "comment", "block:blocked.com"], calls)

    def test_detailed_port_map(self):
        sample_ss = """tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=123,fd=4))
tcp LISTEN 0 128 127.0.0.1:40723 0.0.0.0:* users:(("codex",pid=456,fd=5))
udp UNCONN 0 0 192.168.29.125:9580 0.0.0.0:* users:(("qbittorrent",pid=789,fd=6))
"""
        sample_rules = [
            RuleRecord(number=1, to_addr="22/tcp", action="ALLOW IN", from_addr="Anywhere", comment="OpenSSH")
        ]
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, sample_ss, "")), \
             patch.object(self.engine, "get_numbered_rules", return_value=sample_rules), \
             patch.object(self.engine, "get_status_verbose", return_value={"active": True, "default_incoming": "deny"}):
            port_map = self.engine.get_detailed_port_map()

        self.assertEqual(len(port_map), 3)

        # Port 22 should be EXPOSED (listening on 0.0.0.0 and allowed in UFW)
        p22 = next(p for p in port_map if p["port"] == 22)
        self.assertEqual(p22["fw_status"], "RULE ALLOW")
        self.assertEqual(p22["process"], "sshd")

        # Port 40723 should be PROTECTED (bound to 127.0.0.1)
        p_codex = next(p for p in port_map if p["port"] == 40723)
        self.assertEqual(p_codex["fw_status"], "PROTECTED")

        # Port 9580 should be FILTERED (listening on LAN, but dropped by default incoming policy)
        p_qbit = next(p for p in port_map if p["port"] == 9580)
        self.assertEqual(p_qbit["fw_status"], "DEFAULT DENY")

    def test_open_and_close_port(self):
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "", "")) as mock_cmd, \
             patch.object(self.engine, "get_numbered_rules", return_value=[]):
            # Open port
            ok, msg = self.engine.open_port("8080", proto="tcp", scope="any", comment="Test Web")
            self.assertTrue(ok)
            calls = [call.args[0] for call in mock_cmd.call_args_list]
            self.assertIn(["ufw", "allow", "8080/tcp", "comment", "Test Web"], calls)

            # Close port
            mock_cmd.reset_mock()
            ok_c, msg_c = self.engine.close_port("8080", proto="tcp", action="deny", comment="Block Web")
            self.assertTrue(ok_c)
            calls_c = [call.args[0] for call in mock_cmd.call_args_list]
            self.assertIn(["ufw", "prepend", "deny", "8080/tcp", "comment", "Block Web"], calls_c)

    def test_service_switches(self):
        sample_rules = [
            RuleRecord(number=1, to_addr="22/tcp", action="ALLOW IN", from_addr="Anywhere", comment="dusky:service:ssh")
        ]
        with patch.object(self.engine, "get_numbered_rules", return_value=sample_rules), \
             patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "", "")):
            # SSH is currently allowed
            self.assertTrue(self.engine.is_service_allowed("ssh", rules=sample_rules))
            # HTTP is currently not allowed
            self.assertFalse(self.engine.is_service_allowed("http", rules=sample_rules))

            # Toggle HTTP on
            ok, msg = self.engine.toggle_service("http", True)
            self.assertTrue(ok)

    def test_active_connections_and_ban(self):
        sample_conns = """tcp ESTAB 0 0 192.168.29.125:50640 104.18.32.47:443 users:(("firefox",pid=4178,fd=663))
tcp ESTAB 0 0 192.168.29.125:22 192.168.29.50:54321 users:(("sshd",pid=123,fd=3))
"""
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, sample_conns, "")) as mock_cmd:
            conns = self.engine.get_active_connections()
            self.assertEqual(len(conns), 2)
            self.assertEqual(conns[0]["remote_ip"], "104.18.32.47")
            self.assertEqual(conns[0]["process"], "firefox")

            # Ban IP
            mock_cmd.reset_mock()
            ok, msg = self.engine.ban_ip("192.168.29.50")
            self.assertTrue(ok)
            calls = [call.args[0] for call in mock_cmd.call_args_list]
            self.assertIn(["ufw", "prepend", "deny", "from", "192.168.29.50", "comment", "Banned: 192.168.29.50"], calls)

    def test_panic_lockdown(self):
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "", "")) as mock_cmd:
            ok, msg = self.engine.panic_lockdown(True)
            self.assertTrue(ok)
            calls = [call.args[0] for call in mock_cmd.call_args_list]
            self.assertIn(["ufw", "default", "deny", "incoming"], calls)
            self.assertIn(["ufw", "default", "deny", "outgoing"], calls)
            self.assertIn(["ufw", "default", "deny", "routed"], calls)

            mock_cmd.reset_mock()
            ok_r, _ = self.engine.panic_lockdown(False)
            self.assertTrue(ok_r)
            calls_r = [call.args[0] for call in mock_cmd.call_args_list]
            self.assertIn(["ufw", "default", "allow", "outgoing"], calls_r)

    def test_application_trigger_uses_draft_and_false_is_idle(self):
        self.engine.cache["app/target_app"] = "WWW Full"
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "ok", "")) as cmd:
            self.assertTrue(self.engine.write_value("app_allow", "actions", "true", "bool")[0])
            self.assertEqual(cmd.call_args.args[0], ["ufw", "allow", "WWW Full"])
            cmd.reset_mock()
            self.assertTrue(self.engine.write_value("app_allow", "actions", "false", "bool")[0])
            cmd.assert_not_called()

    def test_command_failure_and_timeout_are_visible(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "fixture failure")):
            result = self.engine.write_value("action_open_port", "actions", "true", "bool")
            self.assertFalse(result[0])
        self.engine.cache["ports/quick_port"] = "8080"
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "fixture failure")):
            result = self.engine.write_value("action_open_port", "actions", "true", "bool")
            self.assertFalse(result[0])
            self.assertIn("fixture failure", result[1])
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["ufw"], 30)):
            result = self.engine.write_value("action_reload", "actions", "true", "bool")
            self.assertFalse(result[0])
            self.assertIn("timed out", result[1])

    def test_failed_read_never_becomes_inactive(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "permission denied")):
            with self.assertRaisesRegex(UfwError, "permission denied"):
                self.engine.get_status_verbose()

    def test_refresh_preserves_all_drafts(self):
        drafts = {"builder/port": "5432", "builder/source_port": "80", "ports/quick_port": "1234",
                  "app/target_app": "WWW Full", "domains/draft_domain": "example.org",
                  "reports/selected_report": "raw", "framework/wan_interface": "enp2s0"}
        self.engine.cache.update(drafts)
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "Status: inactive\n", "")):
            state = self.engine.load_state()
            for key, value in drafts.items():
                self.assertEqual(state[key], value)
        self.assertFalse(ufw_module.DOMAINS_STORAGE.exists())

    def test_inactive_status_reads_persisted_policies(self):
        self.engine.config_path.write_text('DEFAULT_INPUT_POLICY="REJECT"\nDEFAULT_OUTPUT_POLICY="DROP"\n')
        ufw_module.UFW_CONF.write_text("LOGLEVEL=medium\n")
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "Status: inactive\n", "")):
            status = self.engine.get_status_verbose()
        self.assertEqual(status["default_incoming"], "reject")
        self.assertEqual(status["default_outgoing"], "deny")
        self.assertEqual(status["logging"], "medium")

    def test_delete_exact_port_protocol_and_direction(self):
        report = "\n".join([
            "ufw allow 22/tcp", "ufw allow 22/udp", "ufw allow 122/tcp",
            "ufw allow from 10.22.1.2 to any port 80 proto tcp",
            "ufw allow 22,80/tcp", "ufw allow out 22/tcp", "ufw route allow to any port 22 proto tcp",
            "ufw allow from any port 22 to any port 80 proto tcp",
        ])
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, report, "")) as cmd:
            self.assertTrue(self.engine.delete_port_rules("22", "tcp")[0])
        deletes = [call.args[0] for call in cmd.call_args_list if "delete" in call.args[0]]
        self.assertEqual(deletes, [["ufw", "--force", "delete", "allow", "22/tcp"]])

    def test_unban_exact_managed_tag(self):
        report = "\n".join(["ufw deny from 10.0.0.1 comment 'Banned: 10.0.0.1'",
                            "ufw deny from 10.0.0.10 comment 'Banned: 10.0.0.10'",
                            "ufw deny from 10.0.0.1 comment 'Manual'"])
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, report, "")) as cmd:
            self.assertTrue(self.engine.unban_ip("10.0.0.1")[0])
        self.assertEqual(len([c for c in cmd.call_args_list if "delete" in c.args[0]]), 1)

    def test_service_protocol_collision_and_moonlight_udp(self):
        wrong = [RuleRecord(number=1, to_addr="122/tcp", action="ALLOW IN", from_addr="Anywhere"),
                 RuleRecord(number=2, to_addr="22/udp", action="ALLOW IN", from_addr="Anywhere")]
        self.assertFalse(self.engine.is_service_allowed("ssh", rules=wrong))
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "", "")) as cmd:
            self.assertTrue(self.engine.toggle_service("moonlight", True)[0])
        self.assertTrue(any("47998:48000/udp" in c.args[0] for c in cmd.call_args_list))
        self.assertFalse(any("reload" in c.args[0] for c in cmd.call_args_list))

    def test_ipv6_scoped_socket_and_outgoing_rule(self):
        sockets = "tcp LISTEN 0 128 [fe80::1%enp1s0]:53 [::]:*\ntcp LISTEN 0 128 [::1]:53 [::]:*\n"
        rule = RuleRecord(number=1, to_addr="53/tcp (v6)", action="ALLOW OUT", from_addr="Anywhere (v6)", is_v6=True)
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, sockets, "")), \
             patch.object(self.engine, "get_numbered_rules", return_value=[rule]), \
             patch.object(self.engine, "get_status_verbose", return_value={"active": True, "default_incoming": "deny"}):
            ports = self.engine.get_detailed_port_map()
        self.assertEqual({p["fw_status"] for p in ports}, {"DEFAULT DENY", "PROTECTED"})

    def test_builder_outbound_without_interface_and_empty_rule(self):
        self.engine.cache = {"builder/direction": "out"}
        command = self.engine._construct_rule_command()
        self.assertEqual(command, ["ufw", "allow", "out", "from", "any", "to", "any"])
        self.engine.cache = {"builder/direction": "route", "builder/placement": "insert", "builder/insert_num": "2"}
        self.assertEqual(self.engine._construct_rule_command()[:5], ["ufw", "route", "insert", "2", "allow"])

    def test_builder_rejects_invalid_protocol_ports_before_command(self):
        for values in ({"builder/port": "80,443", "builder/proto": "any"},
                       {"builder/port": "80", "builder/proto": "gre"},
                       {"builder/port": "65536", "builder/proto": "tcp"},
                       {"builder/placement": "insert", "builder/insert_num": "0"}):
            self.engine.cache = values
            with self.assertRaises(ValueError):
                self.engine._construct_rule_command()

    def test_domain_timestamp_and_failed_dns_preserves_addresses(self):
        data = {"whitelist_mode": False, "domains": [{"domain": "example.org", "action": "allow", "ports": "443", "ips": ["1.2.3.4"]}]}
        self.engine._write_domain_registry(data)
        with patch.object(self.engine, "resolve_domain_ips", return_value=["1.2.3.5"]), \
             patch.object(self.engine, "_apply_domain_rules"):
            self.assertTrue(self.engine.sync_domains()[0])
        saved = self.engine._read_domain_registry()
        self.assertIn("+00:00", saved["domains"][0]["last_resolved"])
        with patch.object(self.engine, "resolve_domain_ips", return_value=[]), \
             patch.object(self.engine, "_apply_domain_rules"):
            self.assertFalse(self.engine.sync_domains()[0])
        self.assertEqual(self.engine._read_domain_registry()["domains"][0]["ips"], ["1.2.3.5"])

    def test_corrupt_registry_is_not_overwritten(self):
        ufw_module.DOMAINS_STORAGE.write_text("broken")
        with self.assertRaises(UfwError):
            self.engine._read_domain_registry()
        self.assertEqual(ufw_module.DOMAINS_STORAGE.read_text(), "broken")

    def test_framework_reload_failure_restores_files(self):
        before = ufw_module.UFW_BEFORE_RULES.read_text()
        with patch.object(self.engine, "_run_cmd", side_effect=UfwError("reload failure")):
            result = self.engine.set_icmp_ping_stealth(True)
        self.assertFalse(result[0])
        self.assertEqual(ufw_module.UFW_BEFORE_RULES.read_text(), before)

    def test_nat_blocks_coexist_and_removal_preserves_other_protocol(self):
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "", "")):
            self.assertTrue(self.engine.add_port_forward("8080", "10.1.0.2", "80", "tcp")[0])
            self.assertTrue(self.engine.add_port_forward("8080", "10.1.0.3", "53", "udp")[0])
            self.assertTrue(self.engine.remove_port_forward("8080", "tcp")[0])
        content = ufw_module.UFW_BEFORE_RULES.read_text()
        self.assertNotIn("--to-destination 10.1.0.2:80", content)
        self.assertIn("--to-destination 10.1.0.3:53", content)
        self.assertEqual(content.count(":dusky-dnat -"), 1)
        self.assertEqual(ufw_module.UFW_AFTER_INIT.read_text().count("# BEGIN DUSKY HOOKS"), 1)

    def test_nat_rejects_invalid_destination_without_writes(self):
        before = ufw_module.UFW_BEFORE_RULES.read_text()
        self.assertFalse(self.engine.add_port_forward("8080", "::1", "80", "tcp")[0])
        self.assertEqual(ufw_module.UFW_BEFORE_RULES.read_text(), before)

    def test_waydroid_discovers_subnet_and_egress(self):
        def command(args, **kwargs):
            output = json.dumps([{"addr_info": [{"family": "inet", "local": "10.90.0.1", "prefixlen": 24}]}]) if args[0] == "ip" else ""
            return subprocess.CompletedProcess(args, 0, output, "")
        self.engine.cache["framework/wan_interface"] = "enp1s0"
        with patch.object(self.engine, "_run_cmd", side_effect=command):
            self.assertTrue(self.engine.set_waydroid_nat(True)[0])
        content = ufw_module.UFW_BEFORE_RULES.read_text()
        self.assertIn("-s 10.90.0.0/24 -o enp1s0", content)
        self.assertNotIn("192.168.240", content)

    def test_app_profile_port_newline(self):
        def command(args, **kwargs):
            output = "Available applications:\n  Test\n" if args[-1] == "list" else "Title: Test\nDescription: A test\n\nPort:\n  1234/tcp\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        with patch.object(self.engine, "_run_cmd", side_effect=command):
            self.assertEqual(self.engine.get_app_profiles()[0]["ports"], "1234/tcp")

    def test_nested_preset_failure_is_not_reported_success(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "policy failure")):
            result = self.engine.write_value("action_preset_strict_workstation", "actions", "true", "bool")
        self.assertFalse(result[0])
        self.assertIn("policy failure", result[1])

    def test_dnat_zero_exit_capability_warning_rejects_before_mutation(self):
        before = ufw_module.UFW_BEFORE_RULES.read_text()
        warning = "Warning: Extension DNAT revision 0 not supported, missing kernel module?"
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, "", warning)) as cmd:
            result = self.engine.add_port_forward("8080", "10.0.0.2", "80")
        self.assertFalse(result[0])
        self.assertIn("kernel extension unavailable", result[1])
        self.assertEqual(ufw_module.UFW_BEFORE_RULES.read_text(), before)
        self.assertFalse(ufw_module.UFW_AFTER_INIT.exists())
        self.assertEqual(cmd.call_count, 1)

    def test_dns_timeout_is_bounded(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["python"], 10)) as command:
            self.assertEqual(self.engine.resolve_domain_ips("fixture.example"), [])
        self.assertEqual(command.call_args.kwargs["timeout"], 10)

    def test_stored_service_switches_work_while_inactive(self):
        report = "ufw allow 22/tcp comment 'dusky:service:ssh'\n"
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, report, "")):
            self.assertTrue(self.engine.is_service_allowed("ssh"))
            self.assertFalse(self.engine.is_service_allowed("http"))

    def test_active_connections_scoped_ipv6_without_state_column(self):
        sample = "tcp 0 0 [fe80::1%enp1s0]:22 [fe80::2%enp1s0]:12345\n"
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, sample, "")):
            connection = self.engine.get_active_connections()[0]
        self.assertEqual(connection["remote_ip"], "fe80::2%enp1s0")
        self.assertEqual(connection["remote_port"], "12345")

    def test_disabled_routing_retains_editable_stored_policy(self):
        self.engine.config_path.write_text('DEFAULT_FORWARD_POLICY="REJECT"\n')
        output = "Status: active\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n"
        with patch.object(self.engine, "_run_cmd", return_value=subprocess.CompletedProcess([], 0, output, "")):
            status = self.engine.get_status_verbose()
        self.assertEqual(status["default_routed"], "reject")
        self.assertTrue(status["routing_disabled"])

    def test_remove_unicode_domain_uses_registered_ascii_name(self):
        domain = "bücher.example"
        ascii_domain = domain.encode("idna").decode("ascii")
        self.engine._write_domain_registry({"whitelist_mode": False, "domains": [
            {"domain": ascii_domain, "action": "allow", "ports": "443", "ips": [], "last_resolved": ""}]})
        self.engine.cache["domains/draft_domain"] = domain
        with patch.object(self.engine, "_delete_tagged") as delete:
            result = self.engine.write_value("action_remove_domain", "actions", "true", "bool")
        self.assertTrue(result[0], result[1])
        self.assertEqual(self.engine._read_domain_registry()["domains"], [])
        delete.assert_called_once_with(tags={f"domain:{ascii_domain}", f"block:{ascii_domain}"})


if __name__ == "__main__":
    unittest.main()

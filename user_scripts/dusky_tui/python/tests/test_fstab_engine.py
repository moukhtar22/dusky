"""fstab regression tests: temporary files only, never mounts or /etc/fstab writes."""
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from python.engines.fstab import FstabEngine, NEW_ENTRY

SCHEMA_PATH = Path(os.environ.get(
    "DUSKY_FSTAB_SCHEMA_SOURCE",
    str(Path(__file__).resolve().parents[3] / "drives/fstab/tui_fstab.py"),
))


def schema_module():
    spec = importlib.util.spec_from_file_location("fstab_schema_test", SCHEMA_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FstabTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "fstab"
        self.engine = FstabEngine(str(self.path))

    def fixture(self, text, source="ABCD-1234"):
        self.path.write_bytes(text.encode("utf-8", "surrogateescape"))
        result = self.engine.write_value("uuid", "mount_info", source)
        self.assertTrue(result[0], result[1])

    def write(self, key, scope, value, kind="string"):
        result = self.engine.write_value(key, scope, value, kind)
        self.assertTrue(result[0], result[1])
        return self.path.read_text(errors="surrogateescape")

    def test_case_preserved(self):
        self.fixture("UUID=ABCD-1234 /data vfat defaults 0 0\n")
        self.assertIn("UUID=ABCD-1234", self.write("gvfs_show", "system_flags", "true"))
        self.assertFalse(self.engine._match_device("UUID=ABCD-1234", "UUID=abcd-1234"))

    def test_selection_never_writes(self):
        text = "UUID=ABCD-1234 /data ntfs defaults 0 0\n"
        self.fixture(text)
        self.assertEqual(self.path.read_text(), text)
        self.assertEqual(self.engine.state["mount_info/mount_point"], "/data")

    def test_no_identifier_write_rejected(self):
        self.assertFalse(self.engine.write_value("gvfs_show", "system_flags", "true")[0])
        self.assertFalse(self.path.exists())

    def test_preserve_shared_device_rows(self):
        text = "# keep\nUUID=ABCD-1234 / btrfs rw,compress=zstd:7,subvol=@ 0 0\nUUID=ABCD-1234 /home btrfs rw,subvol=@home 0 0\n"
        self.fixture(text)
        self.write("entry", "mount_info", "/home")
        result = self.write("gvfs_show", "system_flags", "true")
        self.assertEqual(result.splitlines()[1], text.splitlines()[1])
        self.assertIn("/home btrfs rw,subvol=@home,x-gvfs-show", result)

    def test_mount_point_move_is_single_record(self):
        self.fixture("UUID=ABCD-1234 /a ext4 ro,commit=20 1 2  #  keep spacing\n")
        result = self.write("mount_point", "mount_info", "/b")
        self.assertIn(" /b ext4 ro,commit=20,nofail 1 2  #  keep spacing", result)
        self.assertEqual(self.engine.state["mount_info/entry"], "/b")

    def test_unexposed_options_and_metadata_preserved(self):
        self.fixture("UUID=ABCD-1234\t/data  ext4 ro,noexec,commit=20,x-systemd.device-timeout=4s  1\t2 #  exact\n")
        self.path.chmod(0o640)
        self.engine.load_state(force=True)
        result = self.write("gvfs_show", "system_flags", "true")
        self.assertIn("ro,noexec,commit=20,x-systemd.device-timeout=4s,x-gvfs-show  1\t2 #  exact", result)
        self.assertNotIn(",user", result)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_new_ntfs_options(self):
        self.assertTrue(self.engine.write_value("uuid", "mount_info", "848A215E8A214E4C")[0])
        result = self.engine.write_batch([("fs_type", "filesystem", "ntfs", "cycle"), ("mount_point", "mount_info", "/windows", "string")])
        self.assertTrue(result[0], result[1])
        text = self.path.read_text()
        self.assertIn("UUID=848A215E8A214E4C\t/windows\tntfs\t", text)
        self.assertNotIn("prealloc", text)
        self.assertNotIn("ntfs3", text)
        self.assertNotIn("ntfs-3g", text)

    def test_remove_invalid_ntfs_prealloc(self):
        self.fixture("UUID=ABCD-1234 /data ntfs uid=42,prealloc,windows_names 0 0\n")
        result = self.write("gvfs_show", "system_flags", "false")
        self.assertIn("uid=42,windows_names,x-gvfs-hide", result)

    def test_legacy_ntfs_requires_explicit_migration(self):
        self.fixture("UUID=ABCD-1234 /data ntfs3 ro,prealloc,uid=42,x-systemd.device-timeout=4s 0 0\n")
        self.assertFalse(self.engine.write_value("gvfs_show", "system_flags", "true")[0])
        result = self.write("fs_type", "filesystem", "ntfs")
        self.assertNotIn("ntfs3", result)
        self.assertNotIn("prealloc", result)
        self.assertIn("ro", result)
        self.assertIn("x-systemd.device-timeout=4s", result)

    def test_external_content_conflict_and_rollback(self):
        self.fixture("UUID=ABCD-1234 /data ext4 defaults 0 2\n")
        old = self.engine.cache
        text = self.path.read_text().replace("defaults", "noatime")
        original = self.path.stat()
        self.path.write_text(text)
        os.utime(self.path, ns=(original.st_atime_ns, original.st_mtime_ns))
        result = self.engine.write_value("gvfs_show", "system_flags", "true")
        self.assertFalse(result[0])
        self.assertEqual(self.path.read_text(), text)
        self.assertEqual(self.engine.cache, old)

    def test_deleted_file_conflict(self):
        self.fixture("UUID=ABCD-1234 /data ext4 defaults 0 2\n")
        self.path.unlink()
        self.assertFalse(self.engine.write_value("auto_mount", "system_flags", "false")[0])
        self.assertFalse(self.path.exists())

    def test_two_editors_stale_write(self):
        self.fixture("UUID=ABCD-1234 /data ext4 defaults 0 2\n")
        second = FstabEngine(str(self.path))
        self.assertTrue(second.write_value("uuid", "mount_info", "ABCD-1234")[0])
        self.write("gvfs_show", "system_flags", "true")
        self.assertFalse(second.write_value("auto_mount", "system_flags", "false")[0])

    def test_fsync_failure_leaves_original(self):
        text = "UUID=ABCD-1234 /data ext4 defaults 0 2\n"
        self.fixture(text)
        with patch("python.engines.fstab.os.fsync", side_effect=OSError("write failure")):
            self.assertFalse(self.engine.write_value("auto_mount", "system_flags", "false")[0])
        self.assertEqual(self.path.read_text(), text)
        self.assertEqual(list(self.path.parent.glob(".fstab.*")), [self.engine.lock_path])

    def test_directory_fsync_failure_reports_committed(self):
        self.fixture("UUID=ABCD-1234 /data ext4 defaults 0 2\n")
        original = os.fsync
        def sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("directory sync failure")
            original(fd)
        with patch("python.engines.fstab.os.fsync", side_effect=sync):
            result = self.engine.write_value("auto_mount", "system_flags", "false")
        self.assertTrue(result[0], result[1])
        self.assertIn("durability", result[1])
        self.assertIn("noauto", self.path.read_text())
        self.assertFalse(self.engine.cache["system_flags/auto_mount"])

    def test_conflict_during_save(self):
        self.fixture("UUID=ABCD-1234 /data ext4 defaults 0 2\n")
        original = os.fsync
        def sync(fd):
            original(fd)
            self.path.write_text("# external editor\n")
        with patch("python.engines.fstab.os.fsync", side_effect=sync):
            self.assertFalse(self.engine.write_value("auto_mount", "system_flags", "false")[0])
        self.assertEqual(self.path.read_text(), "# external editor\n")

    def test_invalid_selector_rolls_back(self):
        self.fixture("UUID=ABCD-1234 /data ext4 defaults 0 2\n")
        old = self.engine.cache
        self.assertFalse(self.engine.write_value("entry", "mount_info", "/absent")[0])
        self.assertEqual(self.engine.cache, old)
        self.write("gvfs_show", "system_flags", "true")

    def test_invalid_values_batch_is_atomic(self):
        text = "UUID=ABCD-1234 /data ext4 defaults 0 2\n"
        self.fixture(text)
        for changes in [
            [("auto_mount", "system_flags", "garbage", "bool")],
            [("gvfs_show", "system_flags", "true", "bool"), ("mount_point", "mount_info", "relative", "string")],
            [("drive_type", "filesystem", "mystery", "cycle")],
            [("uuid", "mount_info", "LABEL=x\nnext", "string")],
            [("missing", "filesystem", "x", "string")],
        ]:
            with self.subTest(changes=changes):
                self.assertFalse(self.engine.write_batch(changes)[0])
                self.assertEqual(self.path.read_text(), text)

    def test_swap_noauto(self):
        self.fixture("/swap/file none swap defaults,pri=10 0 0\n", "/swap/file")
        text = self.write("auto_mount", "system_flags", "false")
        self.assertIn("pri=10,noauto,nofail", text)
        self.assertNotIn("gvfs", text)

    def test_duplicate_mount_point_rejected(self):
        text = "UUID=ABCD-1234 /data ext4 defaults 0 2\nUUID=FFFF-1111 /other ext4 defaults 0 2\n"
        self.fixture(text)
        self.assertFalse(self.engine.write_value("mount_point", "mount_info", "/other")[0])
        self.assertEqual(self.path.read_text(), text)

    def test_new_entry_on_shared_device(self):
        text = "UUID=ABCD-1234 / btrfs defaults,subvol=@ 0 0\n"
        self.fixture(text)
        self.write("entry", "mount_info", NEW_ENTRY)
        result = self.engine.write_batch([("mount_point", "mount_info", "/home", "string"), ("subvol", "btrfs_ops", "@home", "string")])
        self.assertTrue(result[0], result[1])
        self.assertTrue(self.path.read_text().startswith(text))
        self.assertEqual(len(self.engine._records(self.path.read_bytes())), 2)

    def test_subvolid_preserved_and_explicitly_cleared(self):
        self.fixture("UUID=ABCD-1234 /data btrfs subvolid=5,compress=zstd:7 0 0\n")
        self.assertIn("subvolid=5,compress=zstd:7", self.write("gvfs_show", "system_flags", "true"))
        self.assertNotIn("subvolid", self.write("subvol", "btrfs_ops", ""))

    def test_cow_disables_compression(self):
        self.fixture("UUID=ABCD-1234 /data btrfs compress-force=zstd:7,autodefrag,subvol=@ 0 0\n")
        text = self.write("cow_enabled", "btrfs_ops", "false")
        self.assertNotIn("compress", text)
        self.assertNotIn("autodefrag", text)
        self.assertIn("nodatacow", text)

    def test_escaping_roundtrip_with_libmount(self):
        source = 'LABEL=space é\\040'
        target = '/mnt/a b\t\\040'
        self.assertTrue(self.engine.write_value("uuid", "mount_info", source)[0])
        result = self.engine.write_batch([("fs_type", "filesystem", "ntfs", "cycle"), ("mount_point", "mount_info", target, "string")])
        self.assertTrue(result[0], result[1])
        self.engine.load_state(force=True)
        self.assertEqual(self.engine.state["mount_info/mount_point"], target)
        result = subprocess.run(["findmnt", "--fstab", "--tab-file", str(self.path), "--json", "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"], capture_output=True, text=True, check=True)
        import json
        row = json.loads(result.stdout)["filesystems"][0]
        self.assertEqual(row["source"], source)
        self.assertEqual(row["target"], target)

    def test_four_field_entry_and_missing_newline(self):
        self.fixture("UUID=ABCD-1234 /data ext4 defaults #  comment")
        text = self.write("gvfs_show", "system_flags", "true")
        self.assertEqual(text, "UUID=ABCD-1234 /data ext4 defaults,x-gvfs-show\t0\t0 #  comment")

    def test_invalid_utf8_comments_survive(self):
        self.fixture("# \udcff\nUUID=ABCD-1234 /data ext4 defaults 0 2\n")
        self.write("gvfs_show", "system_flags", "true")
        self.assertTrue(self.path.read_bytes().startswith(b"# \xff\n"))

    def test_uid_comes_from_invoking_user(self):
        import pwd
        current = pwd.getpwuid(os.getuid())
        with patch.dict(os.environ, {"SUDO_UID": str(current.pw_uid), "SUDO_GID": "99999"}):
            self.assertEqual(self.engine._first_normal_uid_gid(), (current.pw_uid, current.pw_gid))

    def test_discovery_failure_retains_offline_entries(self):
        module = schema_module()
        self.path.write_text("UUID=ABCD-1234 /data vfat defaults 0 0\n")
        module.TARGET_FILE = str(self.path)
        with patch.object(module.subprocess, "run", side_effect=subprocess.TimeoutExpired("lsblk", 10)):
            self.assertEqual(module.DEFERRED_LOAD(), [0])
        self.assertIn("UUID=ABCD-1234", module.SCHEMA[0][0].options)
        self.assertIn("/data", module.SCHEMA[0][1].options)

    def test_unicode_whitespace_and_literal_label_space(self):
        self.fixture("LABEL=Data\\040 /mnt/a\u0085b ext4 defaults 0 2\n", "LABEL=Data ")
        self.assertEqual(self.engine.cache["mount_info/mount_point"], "/mnt/a\u0085b")
        text = self.write("gvfs_show", "system_flags", "true")
        self.assertIn("/mnt/a\u0085b", text)

    def test_ordered_flags(self):
        self.fixture("UUID=ABCD-1234 /data btrfs nodatacow,compress=zstd,auto,noauto,x-gvfs-show,x-gvfs-hide 0 0\n")
        self.assertTrue(self.engine.cache["btrfs_ops/cow_enabled"])
        self.assertFalse(self.engine.cache["system_flags/auto_mount"])
        self.assertFalse(self.engine.cache["system_flags/gvfs_show"])

    def test_identifier_aliases_on_real_device(self):
        result = subprocess.run(["lsblk", "--json", "--paths", "--output", "NAME,UUID,LABEL,PARTUUID"], check=True, capture_output=True, text=True)
        import json
        devices = json.loads(result.stdout)["blockdevices"]
        tested = 0
        while devices:
            device = devices.pop()
            devices.extend(device.get("children") or [])
            if not device.get("uuid"):
                continue
            for tag, key in (("UUID", "uuid"), ("LABEL", "label"), ("PARTUUID", "partuuid")):
                if value := device.get(key):
                    self.assertTrue(self.engine._match_device(device["name"], f"{tag}={value}"))
                    tested += 1
        self.assertGreater(tested, 0)

    def test_mapped_device_hardware_detection(self):
        result = subprocess.run(["lsblk", "--json", "--paths", "--output", "NAME,ROTA"], check=True, capture_output=True, text=True)
        import json
        devices = json.loads(result.stdout)["blockdevices"]
        tested = 0
        while devices:
            device = devices.pop()
            devices.extend(device.get("children") or [])
            if device["name"].startswith("/dev/mapper/"):
                self.assertEqual(self.engine._detect_drive_type(device["name"]), "hdd" if device["rota"] else "ssd")
                tested += 1
        if not tested:
            self.skipTest("No mapped block devices available")


class FstabUITests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_batch_is_not_retried_as_individual_writes(self):
        from python.frontend.ui import DuskyTUI
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fstab"
            text = "UUID=ABCD-1234 /data ext4 defaults 0 2\n"
            path.write_text(text)
            engine = FstabEngine(str(path))
            self.assertTrue(engine.write_value("uuid", "mount_info", "ABCD-1234")[0])
            module = schema_module()
            key = ("fstab", str(path))
            app = DuskyTUI({key: engine}, key, module.SCHEMA, {i: tab for i, tab in enumerate(module.TABS)}, default_mode="batch", enable_user_presets=False)
            app.play_reset_sound = lambda: None
            async with app.run_test() as pilot:
                for _ in range(100):
                    await pilot.pause(0.01)
                    if app._boot_complete:
                        break
                app._apply_states_to_tab(3, app._states)
                app._apply_value(0, 2, module.SCHEMA[0][2], "relative")
                app._apply_value(3, 0, module.SCHEMA[3][0], False)
                app.action_save_batch()
                await pilot.pause(0.6)
                self.assertEqual(path.read_text(), text)
                self.assertEqual(len(app.pending_commits), 2)

    async def test_selection_refreshes_other_fields_in_auto_and_batch(self):
        from python.frontend.ui import DuskyTUI
        for mode in ("auto", "batch"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "fstab"
                text = "UUID=ABCD-1234 /data ntfs noauto,uid=42 0 0\nUUID=ABCD-1234 /other ntfs defaults 0 0\n"
                path.write_text(text)
                engine = FstabEngine(str(path))
                module = schema_module()
                key = ("fstab", str(path))
                app = DuskyTUI({key: engine}, key, module.SCHEMA, {i: tab for i, tab in enumerate(module.TABS)}, default_mode=mode, enable_user_presets=False)
                app.play_reset_sound = lambda: None
                async with app.run_test() as pilot:
                    for _ in range(100):
                        await pilot.pause(0.01)
                        if app._boot_complete:
                            break
                    selected = module.SCHEMA[0][0]
                    app._apply_value(0, 0, selected, "ABCD-1234")
                    if mode == "batch":
                        app.action_save_batch()
                    await pilot.pause(0.8)
                    self.assertEqual(module.SCHEMA[0][2].value, "/data")
                    app._apply_states_to_tab(1, app._states)
                    self.assertEqual(module.SCHEMA[1][0].value, "ntfs")
                    self.assertEqual(path.read_text(), text)
                    app._apply_value(0, 1, module.SCHEMA[0][1], "/other")
                    if mode == "batch":
                        app.action_save_batch()
                    await pilot.pause(0.8)
                    self.assertEqual(module.SCHEMA[0][2].value, "/other")
                    self.assertEqual(path.read_text(), text)


if __name__ == "__main__":
    unittest.main()

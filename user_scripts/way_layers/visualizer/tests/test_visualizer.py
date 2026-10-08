"""Focused regressions; run with python3 -m unittest discover -s tests -v."""

import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


m = load_module("visualizer_test_daemon", ROOT / "visualizer_daemon.py")
ctl = load_module("visualizer_test_ctl", ROOT / "visualizer_ctl.py")


class VisualizerTests(unittest.TestCase):
    def setUp(self):
        self.app = m.Visualizer()
        self.app.ensure_data_arrays()
        self.app.ensure_tick = lambda: None

    def tearDown(self):
        self.app.shutdown()

    def test_gpu_setting_change_clears_failure_while_disabled_or_enabling(self):
        self.app.setup_window = lambda: None
        self.app.start_cava = lambda: True
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                old = m.replace(self.app.config, enabled=False, gpu_acceleration=False)
                self.app.config = m.replace(old, enabled=enabled, gpu_acceleration=True)
                self.app.gl_failed = True
                self.app.apply_config_changes(old)
                self.assertFalse(self.app.gl_failed)

    def test_destroy_window_cancels_its_pending_fallback(self):
        self.app.schedule_cairo_fallback()
        source = self.app.fallback_source
        self.assertIsNotNone(m.GLib.MainContext.default().find_source_by_id(source))
        self.app.destroy_window()
        self.assertIsNone(self.app.fallback_source)
        self.assertFalse(self.app.fallback_pending)
        self.assertIsNone(m.GLib.MainContext.default().find_source_by_id(source))

    def test_bar_count_change_recalculates_window_geometry(self):
        old = self.app.config
        self.app.config = m.replace(old, bars=16)
        self.app.start_cava = lambda: True
        with patch.object(self.app, "setup_window") as setup:
            self.app.apply_config_changes(old)
        setup.assert_called_once_with()

    def test_missing_files_retain_current_settings_on_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(m, "CONFIG_FILE", Path(tmp) / "missing.json"), \
                 patch.object(m, "COLORS_FILE", Path(tmp) / "missing-colors.json"):
                self.app.config.enabled = False
                self.app.config.gain = 3
                self.app.colors.accent = "#123456"
                self.app.apply_config_changes = lambda old: None
                self.app.execute_reload()
                self.assertFalse(self.app.config.enabled)
                self.assertEqual(self.app.config.gain, 3)
                self.assertEqual(self.app.colors.accent, "#123456")

    def test_config_bounds_and_round_trip(self):
        config = m.Config.from_dict({"bars": 17, "inner_glow": 9,
            "specular_shine": -1, "stardust": 5, "fps": "nan",
            "cava_lower_freq": 99999, "cava_upper_freq": 5,
            "cava_source": "bad\n[output]", "height_pct": "inf"})
        self.assertEqual(config.bars, 16)
        self.assertEqual((config.inner_glow, config.specular_shine, config.stardust), (1, 0, 1))
        self.assertLess(config.cava_lower_freq, config.cava_upper_freq)
        self.assertEqual(config.cava_source, "")
        self.assertEqual(config, m.Config.from_dict(config.to_dict()))

    def test_fifo_fragmentation_batch_and_idempotent_enable(self):
        read_fd, write_fd = os.pipe()
        calls = []
        self.app.toggle_enabled = lambda: calls.append("toggle")
        self.app.toggle_overlay = lambda: calls.append("overlay")
        try:
            os.write(write_fd, b"tog")
            self.app.on_fifo_read(read_fd, m.GLib.IOCondition.IN)
            self.assertEqual(calls, [])
            os.write(write_fd, b"gle\noverlay\nenable\nunknown\n")
            self.app.on_fifo_read(read_fd, m.GLib.IOCondition.IN)
            self.assertEqual(calls, ["toggle", "overlay"])
            self.app.config.enabled = False
            os.write(write_fd, b"enable\n")
            self.app.on_fifo_read(read_fd, m.GLib.IOCondition.IN)
            self.assertEqual(calls[-1], "toggle")
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_cava_fragmentation_newest_frame_and_low_signal(self):
        r, w = os.pipe()
        n = self.app.config.bars
        frame = (b"500;" * n) + b"\n"
        try:
            os.write(w, frame[:23])
            self.app.on_cava_stdout(r, m.GLib.IOCondition.IN)
            self.assertFalse(any(self.app.cava_shared_data))
            os.write(w, frame[23:] + b"250;" * n + b"\npartial")
            self.app.on_cava_stdout(r, m.GLib.IOCondition.IN)
            self.assertEqual(self.app.cava_shared_data, [.25] * n)
            self.assertEqual(self.app.cava_buffer, b"partial")
            self.app.cava_buffer = b""
            os.write(w, b"1;" * n + b"\n")
            self.app.on_cava_stdout(r, m.GLib.IOCondition.IN)
            self.assertFalse(any(self.app.cava_shared_data))
        finally:
            os.close(r)
            os.close(w)

    def test_audio_failure_clears_stale_targets_and_restarts(self):
        self.app.cava_shared_data = [.8] * self.app.config.bars
        self.app.cava_available = True
        r, w = os.pipe()
        try:
            self.assertFalse(self.app.on_cava_stdout(r, m.GLib.IOCondition.HUP))
            self.assertFalse(any(self.app.cava_shared_data))
            self.assertIsNotNone(self.app.cava_restart_source)
        finally:
            os.close(r)
            os.close(w)

    def test_idle_decay_finishes(self):
        self.app.config.idle_wave = False
        self.app.smoothed_data = [.9] * self.app.config.bars
        for _ in range(50):
            data = self.app.prepare_render_data()
        self.assertIsNone(data)
        self.assertTrue(self.app.has_rendered_idle_clear)

    def test_mirror_output(self):
        self.app.config.mirror = True
        self.app.config.smoothing = 0
        n = self.app.config.bars
        self.app.cava_shared_data = [i / n for i in range(n)]
        data = self.app.prepare_render_data()
        self.assertEqual(data[n // 2:], data[:n // 2][::-1])

    def test_invalid_config_retains_last_good_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            colors = Path(tmp) / "colors.json"
            with patch.object(m, "CONFIG_FILE", config), patch.object(m, "COLORS_FILE", colors):
                self.app.config.gain = 3
                config.write_text('{"gain":')
                self.app.apply_config_changes = lambda old: None
                self.app.execute_reload()
                self.assertEqual(self.app.config.gain, 3)
                config.write_bytes(b'\xff')
                self.app.execute_reload()
                self.assertEqual(self.app.config.gain, 3)

    def test_immediate_external_edit_is_not_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "visualizer.json"
            with patch.object(m, "CONFIG_FILE", path):
                self.app.save_config()
                queued = []
                self.app.queue_reload = lambda: queued.append(True)
                self.app.on_dir_changed(None, m.Gio.File.new_for_path(str(path)), None,
                    m.Gio.FileMonitorEvent.CHANGES_DONE_HINT)
                self.assertEqual(queued, [True])

    def test_instance_lock_keeps_inode_and_excludes_second_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "instance.lock"
            with patch.object(m, "LOCK_FILE", path):
                self.app.acquire_lock()
                ino = path.stat().st_ino
                result = subprocess.run([sys.executable, "-c",
                    "import os,fcntl,sys; f=os.open(sys.argv[1],os.O_RDWR); "
                    "fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)", str(path)],
                    capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.app.release_lock()
                self.assertEqual(path.stat().st_ino, ino)

    def test_cairo_every_style_position_and_bar_limit_is_visible(self):
        for n in (16, 72, 256):
            self.app.config.bars = n
            for style in m.Style:
                for position in m.Position:
                    with self.subTest(bars=n, style=style, position=position):
                        self.app.config.style = style
                        self.app.config.position = position
                        self.app.content_height = 100
                        surface = m.cairo.ImageSurface(m.cairo.FORMAT_ARGB32, 320, 180)
                        self.app.draw_cairo(m.cairo.Context(surface), 320, 180, [.4] * n)
                        self.assertTrue(any(surface.get_data()))

    def test_cairo_draw_restores_context_transform(self):
        class Widget:
            def get_allocated_width(self):
                return 320
            def get_allocated_height(self):
                return 180
        self.app.config.position = m.Position.BOTTOM
        self.app.content_height = 100
        surface = m.cairo.ImageSurface(m.cairo.FORMAT_ARGB32, 320, 180)
        context = m.cairo.Context(surface)
        before = tuple(context.get_matrix())
        self.app.on_draw(Widget(), context)
        self.assertEqual(tuple(context.get_matrix()), before)
        self.assertTrue(any(surface.get_data()))

    def test_atomic_deploy_normalizes_without_destroying_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "visualizer.json"
            with patch.object(m, "CONFIG_DIR", path.parent), patch.object(m, "CONFIG_FILE", path):
                path.write_text(json.dumps({"fps": 9999, "extension": "keep"}))
                m.deploy_config()
                data = json.loads(path.read_text())
                self.assertEqual(data["fps"], 240)
                self.assertEqual(data["extension"], "keep")
                path.write_text("broken")
                with self.assertRaises(ValueError):
                    m.deploy_config()
                self.assertEqual(path.read_text(), "broken")


class ClientTests(unittest.TestCase):
    def run_client(self, active, command, reader=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fifo = root / "dusky/settings/way_layers/visualizer/visualizer.ctl"
            fifo.parent.mkdir(parents=True)
            os.mkfifo(fifo)
            r = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK) if reader else None
            calls = []
            def systemctl(args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0 if active or "is-active" not in args else 3)
            try:
                with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp, "XDG_RUNTIME_DIR": tmp}), \
                     patch.object(sys, "argv", ["visualizer_ctl.py", command]), \
                     patch.object(ctl.subprocess, "run", side_effect=systemctl):
                    status = ctl.main()
                payload = os.read(r, 100) if r is not None else b""
                return status, payload, calls
            finally:
                if r is not None:
                    os.close(r)

    def test_active_toggle(self):
        status, payload, calls = self.run_client(True, "toggle")
        self.assertEqual((status, payload), (0, b"toggle\n"))
        self.assertEqual(len(calls), 1)

    def test_start_disabled_or_enabled_uses_idempotent_enable(self):
        status, payload, calls = self.run_client(False, "toggle")
        self.assertEqual((status, payload), (0, b"enable\n"))
        self.assertIn("start", calls[-1])

    def test_overlay_starts_without_changing_enabled(self):
        status, payload, _ = self.run_client(False, "overlay")
        self.assertEqual((status, payload), (0, b"overlay\n"))

    def test_stale_fifo_has_bounded_failure(self):
        with patch.object(ctl.time, "monotonic", side_effect=[0, 6]):
            status, payload, _ = self.run_client(True, "toggle", reader=False)
        self.assertEqual((status, payload), (1, b""))


if __name__ == "__main__":
    unittest.main()

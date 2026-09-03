#!/usr/bin/env python3
"""Dusky STT recording indicator (GTK3, stdlib only + PyGObject).

Minimal centered pill shown while the microphone is capturing:

    ( ●  REC 00:07   ▂▄▆_ _ _ _    ❚❚   ■ )

* pulsing red dot (amber + "PAUSED" while paused) and elapsed timer
* live mic level bar (own parec tap; never touches daemon audio)
* Pause/Resume and Stop buttons driving the daemon over the control socket

Spawned by dusky_main on mic-session start, terminated on session end; it
also quits itself if the daemon goes idle or the socket vanishes, so a
crashed daemon can never leave an orphan window behind. Runs on the system
python3 (needs Gtk 3 + parec only).
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, GLib  # noqa: E402

# System theme colors only (adw-gtk3-dark here): nothing hardcoded, so the
# pill follows the user's GTK theme / matugen setup automatically.
# alpha()/shade()/mix() are GTK3 CSS builtins for deriving variants.
APP_CSS = b"""
#dusky-rec {
    background-color: alpha(@theme_bg_color, 0.94);
    border-radius: 18px;
    border: 1px solid shade(@theme_bg_color, 0.72);
}
#dusky-rec label { color: @theme_fg_color; }
#rec-label { font-weight: 800; font-size: 14px; letter-spacing: 2px; }
#time-label { font-size: 14px; color: mix(@theme_fg_color, @theme_bg_color, 0.35); }
#dot { font-size: 15px; color: @theme_selected_bg_color; }
#dusky-rec button {
    background-color: transparent;
    border: 1px solid shade(@theme_bg_color, 0.72);
    border-radius: 10px;
    color: @theme_fg_color;
    padding: 4px 12px;
    font-size: 13px;
}
#dusky-rec button:hover { background-color: alpha(@theme_fg_color, 0.1); }
"""

MAX_PACKET = 65536


def control_path() -> Path:
    rt = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(rt) / "dusky-stt" / "control.sock"


def send_command(payload: dict) -> dict | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC) as s:
            s.settimeout(5.0)
            s.connect(str(control_path()))
            s.sendmsg([json.dumps(payload).encode()])
            data, _, flags, _ = s.recvmsg(MAX_PACKET)
            if not data:
                return None
            return json.loads(data.decode())
    except (OSError, ValueError):
        return None


class LevelTap(threading.Thread):
    """Own mic tap via parec; calls back with smoothed 0..1 levels."""

    def __init__(self, on_level) -> None:
        super().__init__(name="dusky-level", daemon=True)
        self._on_level = on_level
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            proc = subprocess.Popen(
                ["parec", "--format=s16le", "--rate=16000", "--channels=1",
                 "--latency-msec=50"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL)
        except OSError:
            return
        assert proc.stdout is not None
        smooth = 0.0
        try:
            while not self._stop.is_set():
                raw = proc.stdout.read(2048 * 2)
                if len(raw) < 2048 * 2:
                    break
                acc = 0
                for off in range(0, len(raw), 2):
                    v = raw[off] | (raw[off + 1] << 8)
                    if v >= 32768:
                        v -= 65536
                    acc += v * v
                rms = (acc / 2048) ** 0.5
                peak = min(1.0, (rms / 6000.0) ** 0.7)
                smooth += (peak - smooth) * (0.5 if peak > smooth else 0.15)
                level = smooth
                GLib.idle_add(self._on_level, level)
        finally:
            try:
                proc.kill()
            except OSError:
                pass


class Indicator:
    def __init__(self, session: str = "") -> None:
        self.t0 = time.monotonic()
        self.session = session
        self.paused = False
        self.finalizing = False
        self._pulse_on = True
        self._idle_seen = 0

        css = Gtk.CssProvider()
        css.load_from_data(APP_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_name("dusky-rec")
        self.win.set_decorated(False)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.set_keep_above(True)
        self.win.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
        self.win.set_resizable(False)
        self.win.set_border_width(14)
        self.win.connect("destroy", self._quit)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        self.dot = Gtk.Label(label="●")
        self.dot.set_name("dot")
        row.pack_start(self.dot, False, False, 0)

        self.rec = Gtk.Label(label="REC")
        self.rec.set_name("rec-label")
        row.pack_start(self.rec, False, False, 0)

        self.clock = Gtk.Label(label="00:00")
        self.clock.set_name("time-label")
        row.pack_start(self.clock, False, False, 0)

        self.bar = Gtk.LevelBar.new()
        self.bar.set_min_value(0.0)
        self.bar.set_max_value(1.0)
        self.bar.set_value(0.0)
        self.bar.set_mode(Gtk.LevelBarMode.CONTINUOUS)
        self.bar.set_size_request(110, -1)
        row.pack_start(self.bar, False, False, 4)

        self.pause_btn = Gtk.Button(label="❚❚")
        self.pause_btn.set_tooltip_text("Pause / resume capture")
        self.pause_btn.connect("clicked", self._on_pause)
        row.pack_start(self.pause_btn, False, False, 0)

        stop_btn = Gtk.Button(label="■")
        stop_btn.set_tooltip_text("Stop and transcribe")
        stop_btn.connect("clicked", self._on_stop)
        row.pack_start(stop_btn, False, False, 0)

        self.win.add(row)
        self._tap = LevelTap(self._level)
        self._tap.start()
        GLib.timeout_add(500, self._tick)
        GLib.timeout_add(2000, self._watchdog)
        self.win.show_all()
        self._paint()

    def _level(self, value: float) -> bool:
        self.bar.set_value(max(0.0, min(1.0, value)))
        return False

    def _paint(self) -> None:
        # Pulse via widget opacity against the theme accent dot: no
        # hardcoded colors anywhere, full opacity when paused/finalizing.
        if self.finalizing:
            self.dot.set_opacity(1.0)
            self.rec.set_text("…")
            self.pause_btn.set_label("❚❚")
            self.pause_btn.set_sensitive(False)
        elif self.paused:
            self.dot.set_opacity(1.0)
            self.rec.set_text("PAUSED")
            self.pause_btn.set_label("▶")
            self.pause_btn.set_sensitive(True)
        else:
            self.dot.set_opacity(1.0 if self._pulse_on else 0.3)
            self.rec.set_text("REC")
            self.pause_btn.set_label("❚❚")
            self.pause_btn.set_sensitive(True)

    def _tick(self) -> bool:
        self._pulse_on = not self._pulse_on
        secs = int(time.monotonic() - self.t0)
        self.clock.set_text(f"{secs // 60:02d}:{secs % 60:02d}")
        self._paint()
        return True

    def _watchdog(self) -> bool:
        st = send_command({"command": "status"})
        if st is None:
            return self._note_idle()
        state = st.get("state", "idle")
        if state == "idle":
            return self._note_idle()
        if state == "finalizing":
            # GPU drain continues headless; show it so rapid re-taps are
            # understood instead of feeling swallowed.
            self._idle_seen = 0
            if not self.finalizing:
                self.finalizing = True
                self._paint()
            return True
        if state == "recording":
            self._idle_seen = 0
            if self.finalizing:
                self.finalizing = False
            owner = str(st.get("session") or "")
            if self.session and owner and not self.session.startswith(owner) and not owner.startswith(self.session):
                # A chained take replaced our session (its own pill spawns):
                # quit so two pills never linger.
                self._quit()
                return False
            paused = bool(st.get("paused", False))
            if paused != self.paused:
                self.paused = paused
                self._paint()
            return True
        return self._note_idle()

    def _note_idle(self) -> bool:
        self._idle_seen += 1
        if self._idle_seen >= 2:
            self._quit()
            return False
        return True

    def _on_pause(self, _btn) -> None:
        resp = send_command({"command": "pause"})
        if resp and resp.get("ok"):
            self.paused = resp.get("event") == "paused"
            self._idle_seen = 0
            self._paint()

    def _on_stop(self, _btn) -> None:
        send_command({"command": "stop"})
        GLib.timeout_add(400, self._quit_once)

    def _quit_once(self) -> bool:
        self._quit()
        return False

    def _quit(self, *_args) -> None:
        try:
            self._tap.stop()
        except Exception:
            pass
        Gtk.main_quit()

    def run(self) -> None:
        Gtk.main()


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="dusky_rec_indicator",
                                 description="Dusky STT on-screen recording indicator")
    ap.add_argument("--session", default="", help="Owning recording session id (quit if another takes over)")
    args = ap.parse_args(argv)
    if not control_path().exists():
        return 2
    Indicator(session=args.session).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

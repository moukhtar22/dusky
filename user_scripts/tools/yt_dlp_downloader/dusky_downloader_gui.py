#!/usr/bin/env python3
"""Responsive GTK frontend for the Dusky downloader CLI."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import gi

os.environ["GDK_BACKEND"] = "wayland"
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from dusky_yt_dlp import QUALITY_CAPS, TargetFormat


class DownloaderWindow(Gtk.Window):
    def __init__(self, initial_targets: list[str] | None = None) -> None:
        super().__init__(title="Dusky Downloader")
        self.set_default_size(860, 650)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_border_width(18)
        self.connect("delete-event", self._close)

        self.process: subprocess.Popen[str] | None = None
        self.process_lock = threading.Lock()
        self.messages: deque[str] = deque()
        self.messages_lock = threading.Lock()
        self.closing = False
        self.stopping = False
        self.completed = 0
        self.total = 0

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(root)
        heading = Gtk.Label(label="Dusky Downloader")
        heading.set_xalign(0)
        heading.get_style_context().add_class("title")
        root.pack_start(heading, False, False, 0)

        source_row = Gtk.Box(spacing=8)
        root.pack_start(source_row, False, False, 0)
        self.source_entry = Gtk.Entry()
        self.source_entry.set_placeholder_text("Paste a URL or batch file path, then press Enter")
        self.source_entry.connect("activate", self._add_source)
        source_row.pack_start(self.source_entry, True, True, 0)
        self.add_button = Gtk.Button(label="Add")
        self.add_button.connect("clicked", self._add_source)
        source_row.pack_start(self.add_button, False, False, 0)
        self.remove_button = Gtk.Button(label="Remove selected")
        self.remove_button.connect("clicked", self._remove_selected)
        source_row.pack_start(self.remove_button, False, False, 0)

        self.sources = Gtk.ListStore(str)
        self.source_view = Gtk.TreeView(model=self.sources)
        self.source_view.set_headers_visible(False)
        self.source_view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.source_view.append_column(Gtk.TreeViewColumn("Sources", Gtk.CellRendererText(), text=0))
        source_scroll = Gtk.ScrolledWindow()
        source_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        source_scroll.set_min_content_height(135)
        source_scroll.add(self.source_view)
        root.pack_start(source_scroll, True, True, 0)

        options = Gtk.Grid(column_spacing=12, row_spacing=8)
        root.pack_start(options, False, False, 0)
        self.format_box = Gtk.ComboBoxText()
        for mode in TargetFormat:
            self.format_box.append(mode.value, mode.value)
        self.format_box.set_active_id(TargetFormat.AUDIO_BEST.value)
        self.format_box.connect("changed", self._format_changed)
        self.quality_box = Gtk.ComboBoxText()
        self.quality_box.append("best", "best")
        for cap in QUALITY_CAPS:
            self.quality_box.append(str(cap), f"up to {cap}p")
        self.quality_box.set_active_id("best")
        self.quality_box.set_sensitive(False)
        self.workers = Gtk.SpinButton.new_with_range(1, 8, 1)
        self.workers.set_value(3)
        self.output_entry = Gtk.Entry()
        self.output_entry.set_placeholder_text("Automatic memory pool (/mnt/zram1 or /dev/shm)")
        self.playlist_entry = Gtk.Entry()
        self.playlist_entry.set_placeholder_text("all")
        self.skip_entry = Gtk.Entry()
        self.skip_entry.set_placeholder_text("none")
        self.cookie_entry = Gtk.Entry()
        self.cookie_entry.set_placeholder_text("Optional Netscape cookie file")
        self.browser_entry = Gtk.Entry()
        self.browser_entry.set_placeholder_text("Optional browser name")
        fields = [
            ("Format", self.format_box), ("Maximum height", self.quality_box),
            ("Concurrent jobs", self.workers), ("Memory output directory", self.output_entry),
            ("Playlist positions", self.playlist_entry), ("Skip queue positions", self.skip_entry),
            ("Cookie file", self.cookie_entry), ("Cookies from browser", self.browser_entry),
        ]
        for index, (label, widget) in enumerate(fields):
            row, column = divmod(index, 2)
            options.attach(Gtk.Label(label=label, xalign=0), column * 2, row, 1, 1)
            options.attach(widget, column * 2 + 1, row, 1, 1)
        options.set_column_homogeneous(False)

        action_row = Gtk.Box(spacing=8)
        root.pack_start(action_row, False, False, 0)
        self.start_button = Gtk.Button(label="Start download")
        self.start_button.get_style_context().add_class("suggested-action")
        self.start_button.connect("clicked", self._start)
        action_row.pack_start(self.start_button, False, False, 0)
        self.skip_button = Gtk.Button(label="Skip active jobs")
        self.skip_button.set_sensitive(False)
        self.skip_button.connect("clicked", self._skip_active)
        action_row.pack_start(self.skip_button, False, False, 0)
        self.cancel_button = Gtk.Button(label="Cancel")
        self.cancel_button.set_sensitive(False)
        self.cancel_button.connect("clicked", self._cancel)
        action_row.pack_start(self.cancel_button, False, False, 0)
        self.spinner = Gtk.Spinner()
        action_row.pack_start(self.spinner, False, False, 0)
        self.status = Gtk.Label(label="Ready")
        self.status.set_xalign(0)
        action_row.pack_start(self.status, True, True, 0)
        self.progress = Gtk.ProgressBar()
        root.pack_start(self.progress, False, False, 0)

        self.log = Gtk.TextView()
        self.log.set_editable(False)
        self.log.set_cursor_visible(False)
        self.log.set_monospace(True)
        self.log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(170)
        log_scroll.add(self.log)
        root.pack_start(log_scroll, True, True, 0)

        for target in initial_targets or []:
            self.sources.append([target])
        GLib.timeout_add(100, self._drain_messages)

    def _add_source(self, _button: object) -> None:
        for source in self.source_entry.get_text().splitlines():
            source = source.strip()
            if source:
                self.sources.append([source])
        self.source_entry.set_text("")

    def _remove_selected(self, _button: object) -> None:
        model, paths = self.source_view.get_selection().get_selected_rows()
        for path in reversed(paths):
            model.remove(model.get_iter(path))

    def _format_changed(self, _box: object) -> None:
        selected = self.format_box.get_active_id() or TargetFormat.AUDIO_BEST.value
        self.quality_box.set_sensitive(TargetFormat(selected).is_video)

    def _command(self) -> list[str]:
        command = [sys.executable, str(Path(__file__).with_name("dusky_yt_dlp.py")),
                   "--format", self.format_box.get_active_id() or TargetFormat.AUDIO_BEST.value,
                   "--concurrent", str(self.workers.get_value_as_int())]
        if self.quality_box.get_sensitive():
            command += ["--quality", self.quality_box.get_active_id() or "best"]
        for widget, flag in ((self.output_entry, "--output-dir"),
                             (self.playlist_entry, "--playlist-items"),
                             (self.skip_entry, "--skip-items"),
                             (self.cookie_entry, "--cookies"),
                             (self.browser_entry, "--cookies-from-browser")):
            value = widget.get_text().strip()
            if value:
                command.append(f"{flag}={value}")
        command += ["--", *(row[0] for row in self.sources)]
        return command

    def _start(self, _button: object) -> None:
        if self.process is not None:
            return
        if not len(self.sources):
            self.status.set_text("Add a URL or batch file first")
            return
        if self.cookie_entry.get_text().strip() and self.browser_entry.get_text().strip():
            self.status.set_text("Choose either a cookie file or browser cookies")
            return
        command = self._command()
        self.completed = self.total = 0
        self.progress.set_fraction(0)
        self.log.get_buffer().set_text("")
        self.stopping = False
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                bufsize=1, start_new_session=True,
                env=dict(os.environ, TERM="dumb", NO_COLOR="1", COLUMNS="160", DUSKY_GUI="1"),
            )
        except OSError as error:
            self.status.set_text(f"Unable to start: {error}")
            return
        with self.process_lock:
            self.process = process
        self.start_button.set_sensitive(False)
        self.skip_button.set_sensitive(False)
        self.cancel_button.set_sensitive(True)
        self.add_button.set_sensitive(False)
        self.remove_button.set_sensitive(False)
        self.source_entry.set_sensitive(False)
        self.spinner.start()
        self.status.set_text("Running")
        threading.Thread(target=self._read_process, args=(process,), daemon=True).start()

    def _read_process(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                with self.messages_lock:
                    self.messages.append(line)
            code = process.wait()
        except OSError as error:
            with self.messages_lock:
                self.messages.append(f"Output reader error: {error}\n")
            code = process.wait()
        finally:
            process.stdout.close()
        GLib.idle_add(self._finished, process, code)

    def _drain_messages(self) -> bool:
        lines: list[str] = []
        with self.messages_lock:
            for _ in range(min(200, len(self.messages))):
                line = self.messages.popleft()
                if line.startswith("DUSKY_EVENT|"):
                    try:
                        event = json.loads(line[len("DUSKY_EVENT|"):])
                    except ValueError:
                        continue
                    if event.get("event") == "queue":
                        self.total = event["total"]
                        self.skip_button.set_sensitive(True)
                        self.status.set_text(f"0 of {self.total} completed")
                    elif event.get("event") == "result":
                        self.completed = event["completed"]
                        self.total = event["total"]
                        if self.total:
                            self.progress.set_fraction(min(1, self.completed / self.total))
                        self.status.set_text(f"{self.completed} of {self.total}: {event['status']}")
                    continue
                lines.append(line)
        if lines:
            buffer = self.log.get_buffer()
            buffer.insert(buffer.get_end_iter(), "".join(lines))
            end = buffer.get_end_iter()
            self.log.scroll_to_iter(end, 0, False, 0, 0)
        return not self.closing or self.process is not None

    def _finished(self, process: subprocess.Popen[str], code: int) -> bool:
        self._drain_messages()
        with self.process_lock:
            if self.process is process:
                self.process = None
        self.spinner.stop()
        self.skip_button.set_sensitive(False)
        self.cancel_button.set_sensitive(False)
        self.start_button.set_sensitive(True)
        self.add_button.set_sensitive(True)
        self.remove_button.set_sensitive(True)
        self.source_entry.set_sensitive(True)
        if code == 0:
            self.progress.set_fraction(1)
            self.status.set_text("Finished")
        elif self.stopping or code in (130, 143, -signal.SIGTERM):
            self.status.set_text("Cancelled")
        else:
            self.status.set_text(f"Failed (exit {code}); see log")
        if self.closing:
            Gtk.main_quit()
        return False

    def _skip_active(self, _button: object) -> None:
        with self.process_lock:
            process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            os.kill(process.pid, signal.SIGINT)
            self.status.set_text("Skipping current jobs…")
        except ProcessLookupError:
            pass

    def _cancel(self, _button: object = None) -> None:
        with self.process_lock:
            process = self.process
        if process is None or process.poll() is not None:
            return
        self.stopping = True
        self.cancel_button.set_sensitive(False)
        self.status.set_text("Cancelling and cleaning up…")
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        GLib.timeout_add_seconds(6, self._escalate, process)

    def _escalate(self, process: subprocess.Popen[str]) -> bool:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return False

    def _close(self, _window: object, _event: object) -> bool:
        self.closing = True
        if self.process is not None:
            self._cancel()
            self.hide()
        else:
            Gtk.main_quit()
        return True


def main(initial_targets: list[str] | argparse.Namespace | None = None) -> int:
    available, _arguments = Gtk.init_check(None)
    if not available:
        print("Dusky GUI requires an active Wayland GTK session.", file=sys.stderr)
        return 2
    options = initial_targets if isinstance(initial_targets, argparse.Namespace) else None
    window = DownloaderWindow(options.target if options else initial_targets)
    if options:
        if options.format:
            window.format_box.set_active_id(options.format)
        if options.quality:
            window.quality_box.set_active_id(options.quality)
        if options.concurrent:
            window.workers.set_value(options.concurrent)
        for value, entry in ((options.output_dir, window.output_entry),
                             (options.playlist_items, window.playlist_entry),
                             (options.skip_items, window.skip_entry),
                             (options.cookies, window.cookie_entry),
                             (options.cookies_from_browser, window.browser_entry)):
            if value is not None:
                entry.set_text(str(value))
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

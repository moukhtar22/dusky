#!/usr/bin/env python3
"""
Dusky LLM Side Panel - Main Window & GApplication Orchestrator
Lite GTK3 Side Panel application for local Ollama LLM chat on Hyprland/Wayland.
Features Wayland focus-grab click-away dismiss, model picker dropdown,
GGUF auto-import, and systemd D-Bus service integration.
"""

import sys
import os

if not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
    sys.stderr.write("dusky-llm: error: WAYLAND_DISPLAY and DISPLAY are not set. Cannot launch GUI.\n")
    sys.exit(5)

import json
import signal
import ctypes
import threading
from pathlib import Path
from typing import Any, Optional

sys.dont_write_bytecode = True
signal.signal(signal.SIGINT, signal.SIG_DFL)

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gdk, Gio, GLib, GLibUnix, Gtk, Pango
except (ImportError, ValueError) as exc:
    raise SystemExit(f"Failed to load GTK3 PyGObject libraries: {exc}") from exc

from llm_backend import (
    APP_ID, HOME, LOG, load_or_create_config, save_config_value,
    check_ollama_alive, ensure_ollama_service, get_installed_models,
    auto_import_local_gguf, LLMStreamingWorker, gi_object_c_pointer,
    _reclaim_idle_memory
)
from llm_ui import apply_app_styles, add_css_class, remove_css_class, ChatBubble, LLMHeaderBar

WINDOW_CLASS: str = "dusky_llm_side_panal.py"
try:
    GLib.set_prgname(WINDOW_CLASS)
except Exception:
    pass


def _get_active_monitor_bounds() -> tuple[float, float]:
    """Get active monitor width and height using hyprctl or GTK fallback."""
    try:
        res = os.popen("hyprctl -j monitors 2>/dev/null").read()
        if res:
            monitors = json.loads(res)
            for m in monitors:
                if m.get("focused"):
                    scale = float(m.get("scale", 1.0))
                    return float(m["width"]) / scale, float(m["height"]) / scale
    except Exception:
        pass
    return 1920.0, 1080.0

class LLMSidePanelWindow(Gtk.ApplicationWindow):
    """Main GTK3 Side Panel Window."""

    def __init__(self, app: Gtk.Application, config: dict[str, Any]) -> None:
        super().__init__(application=app)
        self.app = app
        self.config = config
        self.panel_cfg = self.config.get("panel", {})
        self.llm_cfg = self.config.get("llm", {})

        self.messages_history: list[dict[str, str]] = []
        self.current_model: str = self.llm_cfg.get("default_model", "nanbeige:3b")
        self.active_bubble: Optional[ChatBubble] = None
        self._reposition_scheduled = False

        # Window properties
        self.set_title("Dusky AI Panel")
        self.set_wmclass(WINDOW_CLASS, WINDOW_CLASS)
        self.set_default_size(int(self.panel_cfg.get("width", 420)), -1)
        self.set_size_request(360, 400)
        self.set_resizable(False)
        self.set_decorated(False)
        add_css_class(self, "llm-window")

        # Event connections
        self.connect("delete-event", self._on_delete_event)
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("key-press-event", self._on_key_pressed)
        # LLM Worker
        self.worker = LLMStreamingWorker(
            on_chunk=self._on_stream_chunk,
            on_finish=self._on_stream_finish
        )

        # Main Layout Box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        add_css_class(main_box, "llm-main-box")
        self.add(main_box)

        # 1. Header Bar
        self.header_bar = LLMHeaderBar(
            on_model_changed=self._on_model_changed,
            on_clear=self.clear_history,
            on_close=self.hide
        )
        main_box.pack_start(self.header_bar, False, False, 0)

        # 2. Chat Feed Scrolled Area
        self.chat_scroll = Gtk.ScrolledWindow()
        add_css_class(self.chat_scroll, "chat-scroll")
        self.chat_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.chat_scroll.set_overlay_scrolling(True)

        self.feed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        add_css_class(self.feed_box, "chat-feed-box")
        self.chat_scroll.add(self.feed_box)
        main_box.pack_start(self.chat_scroll, True, True, 0)

        # 3. Input Bar Box
        input_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_css_class(input_container, "input-container")

        self.input_view = Gtk.TextView()
        add_css_class(self.input_view, "chat-entry")
        self.input_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.input_view.set_accepts_tab(False)
        self.input_buffer = self.input_view.get_buffer()
        self.input_view.connect("key-press-event", self._on_input_key_press)

        scrolled_input = Gtk.ScrolledWindow()
        scrolled_input.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_input.set_min_content_height(36)
        scrolled_input.set_max_content_height(100)
        scrolled_input.set_propagate_natural_height(True)
        scrolled_input.add(self.input_view)

        input_container.pack_start(scrolled_input, True, True, 0)

        # Action Button (Send / Stop)
        self.btn_send = Gtk.Button(label="Send")
        add_css_class(self.btn_send, "send-btn")
        self.btn_send.connect("clicked", self._on_send_clicked)
        input_container.pack_start(self.btn_send, False, False, 0)

        main_box.pack_start(input_container, False, False, 0)

        # Initial Welcome Message
        self.add_system_message("Dusky AI Panel ready. Type a prompt to begin!")
        self.refresh_models()

    def refresh_models(self) -> None:
        """Fetch available Ollama models and update header combo box."""
        def fetch_in_background() -> None:
            ensure_ollama_service()
            # Attempt auto-import of local GGUF if available
            auto_import_local_gguf()
            models = get_installed_models()

            def update_ui() -> bool:
                alive = check_ollama_alive()
                self.header_bar.set_status("connected" if alive else "offline")
                if models:
                    if self.current_model not in models:
                        self.current_model = models[0]
                    self.header_bar.set_models(models, self.current_model)
                else:
                    self.header_bar.set_models([], None)
                return GLib.SOURCE_REMOVE

            GLib.idle_add(update_ui)

        threading.Thread(target=fetch_in_background, daemon=True).start()

    def add_system_message(self, text: str) -> None:
        bubble = ChatBubble("system", text)
        self.feed_box.pack_start(bubble, False, False, 0)
        self.scroll_to_bottom()

    def clear_history(self) -> None:
        """Clear conversation history."""
        self.messages_history.clear()
        for child in self.feed_box.get_children():
            self.feed_box.remove(child)
        self.add_system_message("Chat history cleared.")

    def scroll_to_bottom(self) -> None:
        def do_scroll() -> bool:
            adj = self.chat_scroll.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return GLib.SOURCE_REMOVE
        GLib.idle_add(do_scroll)

    def _on_model_changed(self, model_name: str) -> None:
        self.current_model = model_name
        save_config_value(["llm", "default_model"], model_name)
        LOG.info(f"Switched model to: {model_name}")

    def _on_send_clicked(self, _btn: Gtk.Button) -> None:
        if self.worker.is_running():
            self.worker.cancel()
            self._set_generating_state(False)
            return

        start_iter, end_iter = self.input_buffer.get_bounds()
        text = self.input_buffer.get_text(start_iter, end_iter, True).strip()
        if not text:
            return

        self.input_buffer.set_text("")
        
        # Add user bubble
        user_bubble = ChatBubble("user", text)
        self.feed_box.pack_start(user_bubble, False, False, 0)
        self.messages_history.append({"role": "user", "content": text})

        # Add assistant bubble (empty initially)
        self.active_bubble = ChatBubble("assistant", "...")
        self.feed_box.pack_start(self.active_bubble, False, False, 0)
        self.scroll_to_bottom()

        # Prepare messages payload with system prompt
        sys_prompt = self.llm_cfg.get("system_prompt", "You are Dusky AI.")
        full_msgs = [{"role": "system", "content": sys_prompt}] + self.messages_history

        self._set_generating_state(True)
        self.worker.send_chat(
            model=self.current_model,
            messages=full_msgs,
            temperature=float(self.llm_cfg.get("temperature", 0.7))
        )

    def _set_generating_state(self, generating: bool) -> None:
        if generating:
            self.btn_send.set_label("Stop")
            add_css_class(self.btn_send, "stop-btn")
            remove_css_class(self.btn_send, "send-btn")
            self.header_bar.set_status("busy")
        else:
            self.btn_send.set_label("Send")
            add_css_class(self.btn_send, "send-btn")
            remove_css_class(self.btn_send, "stop-btn")
            self.header_bar.set_status("connected")

    def _on_stream_chunk(self, chunk: str) -> None:
        def update_ui() -> bool:
            if self.active_bubble:
                if self.active_bubble.raw_text == "...":
                    self.active_bubble.update_content(chunk)
                else:
                    self.active_bubble.append_chunk(chunk)
                self.scroll_to_bottom()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(update_ui)

    def _on_stream_finish(self, err_msg: Optional[str]) -> None:
        def finish_ui() -> bool:
            self._set_generating_state(False)
            if err_msg:
                if self.active_bubble:
                    self.active_bubble.update_content(f"⚠️ {err_msg}")
                else:
                    self.add_system_message(f"Error: {err_msg}")
            elif self.active_bubble:
                if self.active_bubble.raw_text == "...":
                    self.active_bubble.update_content("(No text returned)")
                self.messages_history.append({
                    "role": "assistant",
                    "content": self.active_bubble.raw_text
                })
            self.active_bubble = None
            return GLib.SOURCE_REMOVE
        GLib.idle_add(finish_ui)

    def _on_input_key_press(self, widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if not (event.state & Gdk.ModifierType.SHIFT_MASK):
                self._on_send_clicked(self.btn_send)
                return True
        return False

    def _on_map(self, *args: Any) -> None:
        self._reposition_to_left_side()

    def _on_unmap(self, *args: Any) -> None:
        _reclaim_idle_memory()

    def _on_delete_event(self, _w: Gtk.Widget, _e: Gdk.Event) -> bool:
        self.hide()
        return True

    def _on_key_pressed(self, _w: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def _reposition_to_left_side(self) -> None:
        mon_w, mon_h = _get_active_monitor_bounds()
        panel_w = int(self.panel_cfg.get("width", 420))
        panel_h = int(mon_h * 0.88)
        
        target_x = 16
        target_y = int((mon_h - panel_h) / 2)

        self.set_size_request(panel_w, panel_h)
        self.move(target_x, target_y)

class LLMSidePanelApp(Gtk.Application):
    """GTK Application Daemon for Dusky LLM Side Panel."""

    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window: Optional[LLMSidePanelWindow] = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda *_: self.quit() or GLib.SOURCE_REMOVE)
        self.hold()

        config_data = load_or_create_config()

        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", True)

        apply_app_styles()
        self.window = LLMSidePanelWindow(self, config_data)
        _reclaim_idle_memory()

    def do_activate(self) -> None:
        if self.window:
            if self.window.get_visible():
                self.window.hide()
            else:
                self.window.refresh_models()
                self.window.show_all()
                self.window.present()

    def do_shutdown(self) -> None:
        _reclaim_idle_memory()
        Gtk.Application.do_shutdown(self)

if __name__ == "__main__":
    app = LLMSidePanelApp()
    try:
        sys.exit(app.run(sys.argv))
    except KeyboardInterrupt:
        sys.exit(0)

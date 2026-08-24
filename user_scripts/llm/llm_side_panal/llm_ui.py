#!/usr/bin/env python3
"""
Dusky LLM Side Panel - UI Module
GTK3 custom styling, glassmorphism design system, header bar with model selector,
chat message bubbles, markdown text rendering, copy buttons, and input bar.
"""

import sys
import os
from typing import Any, Callable, Optional

sys.dont_write_bytecode = True

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango
except (ImportError, ValueError) as exc:
    raise SystemExit(f"Failed to load GTK3 PyGObject libraries: {exc}") from exc

# Modern Dark Glassmorphic CSS Styling
CSS_STYLES: str = """
/* Window & Container Structure */
.llm-window {
    background: rgba(18, 19, 25, 0.95);
    border-left: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: -10px 0px 30px rgba(0, 0, 0, 0.5);
    border-radius: 20px 0px 0px 20px;
}

.llm-main-box {
    padding: 14px;
    background: transparent;
}

/* Header Bar */
.llm-header {
    background: rgba(28, 30, 42, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 8px 12px;
    margin-bottom: 12px;
}

.llm-title {
    font-weight: 700;
    font-size: 14px;
    color: #e2e8f0;
}

.llm-badge-connected {
    background-color: #10b981;
    border-radius: 50%;
    min-width: 8px;
    min-height: 8px;
}

.llm-badge-busy {
    background-color: #f59e0b;
    border-radius: 50%;
    min-width: 8px;
    min-height: 8px;
}

.llm-badge-offline {
    background-color: #ef4444;
    border-radius: 50%;
    min-width: 8px;
    min-height: 8px;
}

/* Model Dropdown ComboBox */
.model-combo {
    background: rgba(38, 41, 58, 0.8);
    color: #f1f5f9;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
    padding: 2px 6px;
    font-size: 12px;
    font-weight: 600;
}

.model-combo cellview {
    color: #38bdf8;
    font-weight: 600;
}

.model-combo button {
    background: transparent;
    border: none;
    box-shadow: none;
}

/* Header Icon Buttons */
.hdr-btn {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    color: #94a3b8;
    padding: 4px 8px;
    min-width: 32px;
    min-height: 32px;
}

.hdr-btn:hover {
    background: rgba(255, 255, 255, 0.12);
    color: #f8fafc;
}

.hdr-btn-danger:hover {
    background: rgba(239, 68, 68, 0.2);
    color: #fca5a5;
    border-color: rgba(239, 68, 68, 0.4);
}

/* Chat Feed Scrolled Area */
.chat-scroll {
    background: transparent;
    border-radius: 12px;
}

.chat-feed-box {
    padding: 4px;
}

/* Chat Bubbles */
.msg-row {
    margin-bottom: 12px;
}

.msg-bubble {
    padding: 10px 14px;
    border-radius: 14px;
    font-size: 13px;
}

.msg-user {
    background: linear-gradient(135deg, #1e3a8a, #2563eb);
    color: #ffffff;
    border-radius: 14px 14px 2px 14px;
    margin-left: 40px;
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.25);
}

.msg-assistant {
    background: rgba(30, 33, 48, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.08);
    color: #e2e8f0;
    border-radius: 14px 14px 14px 2px;
    margin-right: 24px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}

.msg-system {
    background: rgba(255, 255, 255, 0.04);
    color: #94a3b8;
    border-radius: 8px;
    font-size: 11px;
    font-style: italic;
    padding: 6px 10px;
    margin: 4px 40px;
}

/* Code Snippet Container & Copy Button */
.code-block {
    background: #0f172a;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    padding: 8px;
    font-family: monospace;
    font-size: 12px;
    color: #38bdf8;
    margin: 6px 0;
}

.copy-btn {
    background: rgba(255, 255, 255, 0.08);
    border: none;
    border-radius: 6px;
    color: #cbd5e1;
    font-size: 10px;
    padding: 2px 8px;
}

.copy-btn:hover {
    background: rgba(56, 189, 248, 0.2);
    color: #38bdf8;
}

/* Input Bar Area */
.input-container {
    background: rgba(26, 28, 40, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 14px;
    padding: 8px 12px;
    margin-top: 10px;
}

.input-container:focus-within {
    border-color: #38bdf8;
    box-shadow: 0 0 10px rgba(56, 189, 248, 0.2);
}

.chat-entry {
    background: transparent;
    border: none;
    color: #f8fafc;
    font-size: 13px;
}

.send-btn {
    background: linear-gradient(135deg, #0284c7, #2563eb);
    color: #ffffff;
    border: none;
    border-radius: 10px;
    padding: 6px 14px;
    font-weight: 700;
}

.send-btn:hover {
    background: linear-gradient(135deg, #0369a1, #1d4ed8);
}

.stop-btn {
    background: rgba(239, 68, 68, 0.85);
    color: #ffffff;
    border: none;
    border-radius: 10px;
    padding: 6px 12px;
    font-weight: 700;
}

.stop-btn:hover {
    background: rgba(220, 38, 38, 1);
}
"""

def add_css_class(widget: Gtk.Widget, class_name: str) -> None:
    """Helper to add CSS class to Gtk.Widget."""
    ctx = widget.get_style_context()
    ctx.add_class(class_name)

def remove_css_class(widget: Gtk.Widget, class_name: str) -> None:
    """Helper to remove CSS class from Gtk.Widget."""
    ctx = widget.get_style_context()
    ctx.remove_class(class_name)

def apply_app_styles() -> None:
    """Load application CSS styling into GTK default screen."""
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS_STYLES.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

class ChatBubble(Gtk.Box):
    """Widget representing a single chat message (User or Assistant)."""

    def __init__(self, role: str, text: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        add_css_class(self, "msg-row")
        self.role = role
        self.raw_text = text

        # Inner container bubble
        self.bubble_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        add_css_class(self.bubble_box, "msg-bubble")
        
        if role == "user":
            add_css_class(self.bubble_box, "msg-user")
            self.set_halign(Gtk.Align.END)
        elif role == "assistant":
            add_css_class(self.bubble_box, "msg-assistant")
            self.set_halign(Gtk.Align.START)
        else:
            add_css_class(self.bubble_box, "msg-system")
            self.set_halign(Gtk.Align.CENTER)

        # Label content
        self.label = Gtk.Label()
        self.label.set_selectable(True)
        self.label.set_line_wrap(True)
        self.label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label.set_xalign(0.0)
        self.label.set_max_width_chars(45)

        self.update_content(text)
        self.bubble_box.pack_start(self.label, True, True, 0)

        # Copy button for Assistant messages
        if role == "assistant":
            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            btn_box.set_halign(Gtk.Align.END)
            self.copy_btn = Gtk.Button(label="📋 Copy")
            add_css_class(self.copy_btn, "copy-btn")
            self.copy_btn.connect("clicked", self._on_copy_clicked)
            btn_box.pack_start(self.copy_btn, False, False, 0)
            self.bubble_box.pack_start(btn_box, False, False, 0)

        self.pack_start(self.bubble_box, False, False, 0)
        self.show_all()

    def update_content(self, text: str) -> None:
        """Update text content dynamically during streaming."""
        self.raw_text = text
        clean_text = text.replace("<think>", "💭 Thinking:\n").replace("</think>", "\n\n")
        escaped_text = GLib.markup_escape_text(clean_text)
        
        # Simple markdown bold & inline code highlight support
        formatted = (
            escaped_text
            .replace("**", "<b>", 1)
            .replace("**", "</b>", 1)
            .replace("`", "<tt><b>", 1)
            .replace("`", "</b></tt>", 1)
        )
        
        try:
            self.label.set_markup(formatted)
        except Exception:
            self.label.set_text(clean_text)

    def append_chunk(self, chunk: str) -> None:
        """Append text token during stream response."""
        self.update_content(self.raw_text + chunk)

    def _on_copy_clicked(self, _btn: Gtk.Button) -> None:
        """Copy message text to GTK clipboard."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(self.raw_text, -1)
        if hasattr(self, "copy_btn"):
            self.copy_btn.set_label("✓ Copied")
            GLib.timeout_add(1500, lambda: self.copy_btn.set_label("📋 Copy") or GLib.SOURCE_REMOVE)

class LLMHeaderBar(Gtk.Box):
    """Top bar containing title, status indicator, model selector dropdown, clear & close buttons."""

    def __init__(
        self,
        on_model_changed: Callable[[str], None],
        on_clear: Callable[[], None],
        on_close: Callable[[], None]
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_css_class(self, "llm-header")

        self.on_model_changed = on_model_changed
        self.on_clear = on_clear
        self.on_close = on_close

        # Status badge dot
        self.status_dot = Gtk.Box()
        add_css_class(self.status_dot, "llm-badge-connected")
        self.status_dot.set_valign(Gtk.Align.CENTER)
        self.pack_start(self.status_dot, False, False, 4)

        # Title Label
        title_lbl = Gtk.Label(label="Dusky AI")
        add_css_class(title_lbl, "llm-title")
        self.pack_start(title_lbl, False, False, 4)

        # Model Selector Combo
        self.model_combo = Gtk.ComboBoxText()
        add_css_class(self.model_combo, "model-combo")
        self.model_combo.connect("changed", self._on_combo_changed)
        self.pack_start(self.model_combo, True, True, 4)

        # Clear Chat Button
        clear_btn = Gtk.Button()
        clear_btn.set_image(Gtk.Image.new_from_icon_name("edit-clear-symbolic", Gtk.IconSize.BUTTON))
        clear_btn.set_tooltip_text("Clear conversation history")
        add_css_class(clear_btn, "hdr-btn")
        clear_btn.connect("clicked", lambda _: self.on_clear())
        self.pack_start(clear_btn, False, False, 0)

        # Close/Dismiss Button
        close_btn = Gtk.Button()
        close_btn.set_image(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON))
        close_btn.set_tooltip_text("Dismiss panel (Escape)")
        add_css_class(close_btn, "hdr-btn")
        add_css_class(close_btn, "hdr-btn-danger")
        close_btn.connect("clicked", lambda _: self.on_close())
        self.pack_start(close_btn, False, False, 0)

    def set_models(self, models: list[str], active_model: Optional[str] = None) -> None:
        """Populate model dropdown items."""
        self.model_combo.handler_block_by_func(self._on_combo_changed)
        self.model_combo.remove_all()
        
        if not models:
            self.model_combo.append("none", "No models found")
            self.model_combo.set_active_id("none")
        else:
            active_idx = 0
            for idx, m in enumerate(models):
                self.model_combo.append(m, m)
                if active_model and m == active_model:
                    active_idx = idx
            self.model_combo.set_active(active_idx)
            
        self.model_combo.handler_unblock_by_func(self._on_combo_changed)

    def get_selected_model(self) -> Optional[str]:
        return self.model_combo.get_active_text()

    def set_status(self, status: str) -> None:
        """Set connection badge indicator state."""
        ctx = self.status_dot.get_style_context()
        ctx.remove_class("llm-badge-connected")
        ctx.remove_class("llm-badge-busy")
        ctx.remove_class("llm-badge-offline")

        if status == "connected":
            ctx.add_class("llm-badge-connected")
            self.status_dot.set_tooltip_text("Ollama service connected")
        elif status == "busy":
            ctx.add_class("llm-badge-busy")
            self.status_dot.set_tooltip_text("Generating response...")
        else:
            ctx.add_class("llm-badge-offline")
            self.status_dot.set_tooltip_text("Ollama offline")

    def _on_combo_changed(self, combo: Gtk.ComboBoxText) -> None:
        model = combo.get_active_text()
        if model and model != "No models found":
            self.on_model_changed(model)

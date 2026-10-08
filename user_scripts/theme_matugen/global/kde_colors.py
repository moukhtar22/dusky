#!/usr/bin/env python3
"""Publish complete KDE colors without replacing fonts, icons or widget styles.

KConfig watchers receive the native notification. Applications that cache a
pinned KColorScheme still need to reopen; a broadcast cannot clear their cache.
"""

import configparser
import os
from pathlib import Path
import re

from theme_files import atomic_write, merge_groups, publication_lock

COLOR_GROUPS = tuple(f"Colors:{name}" for name in
                     ("Button", "Complementary", "Header", "Selection", "Tooltip", "View", "Window"))
COLOR_KEYS = ("BackgroundAlternate", "BackgroundNormal", "DecorationFocus", "DecorationHover",
              "ForegroundActive", "ForegroundInactive", "ForegroundLink", "ForegroundNegative",
              "ForegroundNeutral", "ForegroundNormal", "ForegroundPositive", "ForegroundVisited")


def read_scheme(text):
    if not text.endswith("# dusky-kde-complete\n"):
        raise ValueError("Incomplete KDE color scheme")
    scheme = configparser.ConfigParser(interpolation=None)
    scheme.optionxform = str
    scheme.read_string(text)
    for group in (*COLOR_GROUPS, "ColorEffects:Disabled", "ColorEffects:Inactive", "WM"):
        values = scheme[group]
        required = (COLOR_KEYS if group in COLOR_GROUPS else
                    ("activeBackground", "activeBlend", "activeForeground", "inactiveBackground",
                     "inactiveBlend", "inactiveForeground") if group == "WM" else
                    ("ChangeSelectionColor", "Color", "ColorAmount", "ColorEffect", "ContrastAmount",
                     "ContrastEffect", "Enable", "IntensityAmount", "IntensityEffect"))
        if not set(required).issubset(values):
            raise ValueError(f"Incomplete KDE group: {group}")
        for key, value in values.items():
            if group in COLOR_GROUPS or group == "WM" or key == "Color":
                if not re.fullmatch(r"\d{1,3},\d{1,3},\d{1,3}", value) or any(int(v) > 255 for v in value.split(",")):
                    raise ValueError(f"Invalid KDE color: {group}/{key}")
    if scheme["General"]["ColorScheme"] != "Matugen" or scheme["UiSettings"]["ColorScheme"] != "Matugen":
        raise ValueError("Unexpected KDE scheme name")
    # Explicit allowlist: generated content never owns unrelated desktop settings.
    entries = {group: dict(scheme[group]) for group in
               (*COLOR_GROUPS, "ColorEffects:Disabled", "ColorEffects:Inactive", "WM")}
    entries.update({"General": {"ColorScheme": "Matugen"},
                    "UiSettings": {"ColorScheme": "Matugen"}})
    return entries


def notify(entries):
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return  # Offline installation: files are ready for the next session.
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    changes = {group: [key.encode() for key in values] for group, values in entries.items()}
    bus.emit_signal(None, "/kdeglobals", "org.kde.kconfig.notify", "ConfigChanged",
                    GLib.Variant("(a{saay})", (changes,)))
    # KDE's platform theme also listens to this palette notification.
    bus.emit_signal(None, "/KGlobalSettings", "org.kde.KGlobalSettings", "notifyChange",
                    GLib.Variant("(ii)", (0, 0)))
    bus.flush_sync(None)


def publish():
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    with publication_lock("kde"):
        text = (config / "matugen/generated/kdeglobals").read_text()
        entries = read_scheme(text)
        scheme_path = data / "color-schemes/Matugen.colors"
        globals_path = config / "kdeglobals"
        previous = globals_path.read_text() if globals_path.exists() else ""
        merged = merge_groups(previous, entries)
        changed = False
        for path, content in ((scheme_path, text), (globals_path, merged)):
            if path.is_symlink() or not path.exists() or path.read_text() != content:
                atomic_write(path, content)
                changed = True
        if changed:
            notify(entries)


if __name__ == "__main__":
    try:
        publish()
    except Exception as error:
        raise SystemExit(f"kde_colors: {error}") from error

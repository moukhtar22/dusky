#!/usr/bin/env python3
"""Publish Matugen GTK palettes without exposing truncated files to applications.

GTK3's user CSS is cached for the process lifetime. Two theme names importing
the same adw-gtk3 layout let GTK reload colors with a single settings change.
GTK4/libadwaita user CSS is also cached; its new palette applies on app restart.
"""

from __future__ import annotations

import os
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile

import gi
from theme_files import atomic_write, publication_lock, merge_groups


def validate(version: str, palette: str) -> None:
    if not palette.endswith("/* dusky-palette-complete */\n"):
        raise ValueError("Incomplete GTK palette; retaining the published colors")
    gi.require_version("Gtk", f"{version}.0")
    from gi.repository import Gtk

    provider = Gtk.CssProvider()
    errors = []
    provider.connect("parsing-error", lambda _p, _s, error: errors.append(str(error)))
    if version == "3":
        provider.load_from_data(palette.encode())
    else:
        provider.load_from_string(palette)
    if errors:
        raise ValueError("Invalid GTK palette: " + "; ".join(errors))


def shared_base(source: Path, data: Path) -> Path:
    """Cache an immutable base including assets where Flatpak can see it.

    Check file metadata on publication; copy only after a base-theme update.
    Publish complete directories so an app never imports a half-copied theme.
    """
    root = source.parent
    digest = hashlib.sha256(str(root.resolve()).encode())
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest.update(repr((str(path.relative_to(root)), stat.st_size,
                                stat.st_mtime_ns)).encode())
    cache = data / "themes/dusky-matugen-base"
    destination = cache / digest.hexdigest()[:20]
    if not destination.is_dir():
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".base-", dir=cache) as temporary:
            staged = Path(temporary) / "theme"
            shutil.copytree(root, staged)
            os.rename(staged, destination)
    return destination / source.name


def publish(version: str) -> None:
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    # Serialize publication and selection, including simultaneous Matugen hooks.
    with publication_lock("gtk"):
        palette = (config / "matugen/generated" / f"gtk-{version}.css").read_text()
        validate(version, palette)
        gtk = config / f"gtk-{version}.0"
        if version == "4":
            atomic_write(gtk / "gtk.css", palette)
            return

        from gi.repository import Gio

        # Discover the installed theme through GTK's documented data search paths.
        roots = [data, Path.home() / ".themes"]
        roots.extend(Path(p) for p in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"))
        bases = [(root if root.name == ".themes" else root / "themes") / "adw-gtk3-dark/gtk-3.0/gtk.css" for root in roots]
        base = next((p for p in bases if p.is_file()), None)
        if base is None:
            raise FileNotFoundError("adw-gtk3-dark is not installed")
        base = shared_base(base, data)

        settings = Gio.Settings.new("org.gnome.desktop.interface")
        names = ("dusky-matugen-a", "dusky-matugen-b")
        current = settings.get_string("gtk-theme")
        selected = names[1] if current == names[0] else names[0]
        stylesheet = f'@import url("{base.as_uri()}");\n\n' + palette
        for name in names:
            theme = data / "themes" / name / "gtk-3.0"
            atomic_write(theme / "gtk.css", stylesheet)
            atomic_write(theme / "gtk-dark.css", stylesheet)

        # Remove the managed cached overrides; all GTK3 colors now come from the
        # reloadable theme provider. Existing apps need one restart to drop them.
        atomic_write(gtk / "gtk.css", "/* Matugen colors live in the reloadable dusky-matugen GTK3 theme. */\n")
        ini = gtk / "settings.ini"
        content = ini.read_text() if ini.exists() else "[Settings]\n"
        content = merge_groups(content, {"Settings": {"gtk-theme-name": selected}})
        atomic_write(ini, content)
        if not settings.set_string("gtk-theme", selected):
            raise RuntimeError("Cannot update the GTK theme setting")
        Gio.Settings.sync()


if __name__ == "__main__":
    if sys.argv[1:] not in (["3"], ["4"]):
        raise SystemExit("Usage: gtk_colors.py 3|4")
    try:
        publish(sys.argv[1])
    except Exception as error:
        raise SystemExit(f"gtk_colors: {error}") from error

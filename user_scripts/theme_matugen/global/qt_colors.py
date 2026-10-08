#!/usr/bin/env python3
"""Validate and atomically publish Matugen's Qt palettes and Kvantum assets."""

import configparser
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

from theme_files import atomic_write, atomic_symlink, publication_lock


def publish(kind: str, bootstrap: bool = False) -> None:
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    generated = config / "matugen/generated"
    with publication_lock("qt"):
        if kind in ("qt5ct", "qt6ct"):
            source = generated / f"{kind}-colors.conf"
            if bootstrap and not source.exists():
                source = config / "matugen/generated_fresh" / source.name
            text = source.read_text()
            scheme = configparser.ConfigParser(interpolation=None)
            scheme.read_string(text)
            lines = ["[ColorScheme]"]
            for group in ("active_colors", "disabled_colors", "inactive_colors"):
                colors = [c.strip() for c in scheme["ColorScheme"][group].split(",")]
                # Installation seeds predating Accent contain a complete
                # 21-role palette. Qt defaults Accent to Highlight. Migrate
                # only during setup; wallpaper hooks still require 22 roles.
                if bootstrap and kind == "qt6ct" and len(colors) == 21:
                    colors.append(colors[12])
                # The shared template includes Qt6's Accent (role 21). Qt5 has
                # 21 roles, including PlaceholderText; omit only Accent there.
                if len(colors) not in ((21, 22) if kind == "qt5ct" else (22,)):
                    raise ValueError(f"Incomplete {kind} palette: {group}")
                if not all(re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", c) for c in colors):
                    raise ValueError(f"Invalid {kind} color: {group}")
                if kind == "qt5ct":
                    colors = colors[:21]
                lines.append(f"{group}=" + ", ".join(colors))
            text = "\n".join(lines) + "\n"
            directory = config / kind
            link = directory / "colors/matugen.conf"
            target = config / "matugen/published" / f"{kind}-colors.conf"
        else:
            suffix = "kvconfig" if kind == "kvantum_kvconfig" else "svg"
            text = (generated / f"kvantum-matugen.{suffix}").read_text()
            if suffix == "svg":
                if ET.fromstring(text).tag != "{http://www.w3.org/2000/svg}svg":
                    raise ValueError("Kvantum asset is not an SVG")
            else:
                if not text.endswith("# dusky-kvantum-complete\n"):
                    raise ValueError("Incomplete Kvantum configuration")
                scheme = configparser.ConfigParser(interpolation=None)
                scheme.read_string(text)
                for section in ("%General", "GeneralColors", "Window"):
                    if section not in scheme:
                        raise ValueError(f"Missing Kvantum section: {section}")
                for section in scheme.values():
                    for key, value in section.items():
                        if key.endswith(".color") and not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value):
                            raise ValueError(f"Invalid Kvantum color: {key}")
            target = config / "Kvantum/matugen" / f"matugen.{suffix}"

        changed = target.is_symlink() or not target.exists() or target.read_text() != text
        if changed:
            atomic_write(target, text)
        if kind in ("qt5ct", "qt6ct"):
            changed = atomic_symlink(link, target) or changed
        if not changed:
            return
        if kind in ("qt5ct", "qt6ct"):
            # qtct watches this directory, not colors/ or its symlink target.
            # A transient entry triggers its 3-second reload timer
            # without rewriting the user's main configuration.
            fd, notification = tempfile.mkstemp(prefix=".matugen-reload.", dir=directory)
            os.close(fd)
            Path(notification).unlink()


if __name__ == "__main__":
    kinds = ("qt5ct", "qt6ct", "kvantum_kvconfig", "kvantum_svg")
    bootstrap = len(sys.argv) == 3 and sys.argv[2] == "--bootstrap"
    if (len(sys.argv) != 2 and not bootstrap) or sys.argv[1] not in kinds:
        raise SystemExit("Usage: qt_colors.py " + "|".join(kinds) + " [--bootstrap]")
    if bootstrap and sys.argv[1] not in ("qt5ct", "qt6ct"):
        raise SystemExit("--bootstrap is only for Qt palette installation")
    try:
        publish(sys.argv[1], bootstrap)
    except Exception as error:
        raise SystemExit(f"qt_colors: {error}") from error

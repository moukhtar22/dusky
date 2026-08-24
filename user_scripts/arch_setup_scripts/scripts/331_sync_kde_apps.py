#!/usr/bin/env python3
"""
===============================================================================
DUSKY THEME: KDE FRAMEWORKS 6 APPLICATION COLOR SCHEME SYNCHRONIZER
===============================================================================
Ensures that all KDE Frameworks 6 applications (Dolphin, Kate, KWrite,
Gwenview, Okular, Ark, Spectacle, KCalc, Konsole, KatePart, Filelight, etc.)
have their [UiSettings], [General], and [KDE] color schemes pinned to 'Matugen'
so they dynamically reflect the current Material You palette with zero race
conditions, symlink safety, and atomic disk writes.
===============================================================================
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Final

KDE_APP_CONFIGS: Final[tuple[str, ...]] = (
    "dolphinrc",
    "katerc",
    "kwriterc",
    "kwrite_config",
    "katepartrc",
    "gwenviewrc",
    "okularrc",
    "arkrc",
    "spectaclerc",
    "kcalcrc",
    "konsolerc",
    "filelightrc",
    "plasma-systemmonitorrc",
    "elisarc",
    "kdenliverc",
    "kritarc",
    "ktorrentrc",
    "korganizerrc",
    "merkurorc",
    "kdeglobals",
)


def patch_ini_file_content(content: str, group_entries: dict[str, dict[str, str]]) -> str:
    """
    Robust single-pass INI updater that preserves structure, comments, and spacing.
    Safely injects or updates multiple groups and keys without duplicate headers.
    """
    lines = content.splitlines()
    out: list[str] = []

    current_group: str | None = None
    group_found = {g: False for g in group_entries}
    keys_updated: dict[str, set[str]] = {g: set() for g in group_entries}

    group_header_regex = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    key_val_regex = re.compile(r"^(\s*)([^=\r\n]+?)(\s*=\s*)(.*)$")

    for line in lines:
        match_header = group_header_regex.match(line)
        if match_header:
            if current_group in group_entries:
                for k, v in group_entries[current_group].items():
                    if k not in keys_updated[current_group]:
                        out.append(f"{k}={v}")
                        keys_updated[current_group].add(k)
                if out and out[-1].strip():
                    out.append("")

            current_group = match_header.group(1).strip()
            if current_group in group_entries:
                group_found[current_group] = True
            out.append(line)
            continue

        if current_group in group_entries:
            match_kv = key_val_regex.match(line)
            if match_kv:
                indent, key, equals, val = match_kv.groups()
                key_clean = key.strip()
                if key_clean in group_entries[current_group]:
                    new_val = group_entries[current_group][key_clean]
                    out.append(f"{indent}{key_clean}{equals}{new_val}")
                    keys_updated[current_group].add(key_clean)
                    continue

        out.append(line)

    if current_group in group_entries:
        for k, v in group_entries[current_group].items():
            if k not in keys_updated[current_group]:
                out.append(f"{k}={v}")
                keys_updated[current_group].add(k)

    for group, found in group_found.items():
        if not found:
            if out and out[-1].strip():
                out.append("")
            out.append(f"[{group}]")
            for k, v in group_entries[group].items():
                out.append(f"{k}={v}")
                keys_updated[group].add(k)

    # Clean multiple consecutive blank lines
    cleaned: list[str] = []
    prev_blank = False
    for l in out:
        is_blank = not l.strip()
        if is_blank and prev_blank:
            continue
        cleaned.append(l)
        prev_blank = is_blank

    result = "\n".join(cleaned).strip()
    return result + "\n" if result else ""


def atomic_write(path: Path, content: str) -> None:
    """Performs a crash-safe atomic write to disk preserving permissions."""
    target_path = path.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = target_path.parent

    orig_mode = target_path.stat().st_mode if target_path.exists() else 0o644

    fd, temp_path_str = tempfile.mkstemp(dir=temp_dir, prefix=f".{target_path.name}.tmp-")
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_path, orig_mode)
        os.replace(temp_path, target_path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def sync_kde_apps(
    scheme_name: str = "Matugen",
    config_directory: Path | None = None,
    quiet: bool = False,
    dry_run: bool = False,
) -> bool:
    """Synchronizes all target KDE application configuration files."""
    config_dir = config_directory or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    synced: list[str] = []

    base_patch = {
        "General": {"ColorScheme": scheme_name},
        "UiSettings": {"ColorScheme": scheme_name},
        "KDE": {"ColorScheme": scheme_name, "widgetStyle": "Fusion"},
    }

    app_specific_patches: dict[str, dict[str, dict[str, str]]] = {
        "katerc": {
            "KTextEditor Renderer": {"Color Theme": scheme_name, "Auto Color Theme Selection": "false"},
        },
        "kwriterc": {
            "KTextEditor Renderer": {"Color Theme": scheme_name, "Auto Color Theme Selection": "false"},
        },
        "katepartrc": {
            "KTextEditor Renderer": {"Color Theme": scheme_name, "Auto Color Theme Selection": "false"},
        },
        "konsolerc": {
            "Desktop Entry": {"DefaultProfile": f"{scheme_name}.profile"},
        },
        "gwenviewrc": {
            "View": {"BackgroundColorMode": "0"},
        },
        "kritarc": {
            "theme": {"theme": scheme_name, "color-scheme": scheme_name},
        },
    }

    for conf_name in KDE_APP_CONFIGS:
        conf_path = config_dir / conf_name

        # If the file is kdeglobals and it is a symlink to Matugen's generated output, skip rewriting
        if conf_name == "kdeglobals" and conf_path.is_symlink():
            continue

        # Merge base patch with any app-specific custom sections
        patch = {g: dict(kvs) for g, kvs in base_patch.items()}
        if conf_name in app_specific_patches:
            for group, kvs in app_specific_patches[conf_name].items():
                if group not in patch:
                    patch[group] = {}
                patch[group].update(kvs)

        original_content = conf_path.read_text(encoding="utf-8") if conf_path.is_file() else ""
        content = patch_ini_file_content(original_content, patch)

        if content != original_content:
            if not dry_run:
                atomic_write(conf_path, content)
            synced.append(conf_name)

    if not quiet:
        if synced:
            action = "Would sync" if dry_run else "Synced"
            print(f"[+] {action} '{scheme_name}' color scheme to KDE app configs: {', '.join(synced)}")
        else:
            print(f"[i] All KDE app configs already configured for '{scheme_name}'.")

    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize KDE Frameworks 6 application color schemes.")
    parser.add_argument("--scheme", default="Matugen", help="Color scheme name to pin (default: Matugen)")
    parser.add_argument("--dir", type=Path, default=None, help="Custom target configuration directory")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress standard output")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Simulate changes without writing to disk")
    args = parser.parse_args()

    success = sync_kde_apps(
        scheme_name=args.scheme,
        config_directory=args.dir,
        quiet=args.quiet,
        dry_run=args.dry_run,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

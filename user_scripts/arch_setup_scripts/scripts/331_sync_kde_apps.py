#!/usr/bin/env python3
"""Select KDE/editor colors using KConfig; preserve app layout and behavior.

This is an installation/manual selection helper, not a wallpaper hook.
Existing apps get a scheme preference; new KDE apps inherit kdeglobals.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys

KDE_APP_CONFIGS = (
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


def sync_kde_apps(scheme_name="Matugen", config_directory=None, quiet=False, dry_run=False):
    config = config_directory or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    changed = []
    for name in KDE_APP_CONFIGS:
        path = config / name
        if name != "kdeglobals" and not path.is_file():
            continue
        if name == "kdeglobals" and path.is_symlink() and path.resolve() == config / "matugen/generated/kdeglobals":
            # The wallpaper publisher migrates this legacy symlink. Never write
            # preferences into Matugen's transient generated output.
            continue
        if path.is_symlink():
            path = path.resolve()
        entries = {"UiSettings": {"ColorScheme": scheme_name}}
        if name in ("katerc", "kwriterc", "katepartrc"):
            entries["KTextEditor Renderer"] = {"Color Theme": scheme_name,
                                             "Auto Color Theme Selection": "false"}
        for group, values in entries.items():
            for key, value in values.items():
                args = ["--file", str(path), "--group", group, "--key", key]
                old = subprocess.run(["kreadconfig6", *args], check=True,
                                     capture_output=True, text=True).stdout.rstrip("\n")
                if old == value:
                    continue
                if not dry_run:
                    subprocess.run(["kwriteconfig6", *args, "--notify", value], check=True)
                if name not in changed:
                    changed.append(name)
    if not quiet:
        action = "Would update" if dry_run else "Updated"
        print(f"{action} colors: {', '.join(changed)}" if changed else "KDE color preferences already configured.")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheme", default="Matugen")
    parser.add_argument("--dir", type=Path)
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument("--dry-run", "-n", action="store_true")
    args = parser.parse_args()
    try:
        sync_kde_apps(args.scheme, args.dir, args.quiet, args.dry_run)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"KDE colors: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

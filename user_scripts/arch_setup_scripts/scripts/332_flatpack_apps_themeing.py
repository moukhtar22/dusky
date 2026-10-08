#!/usr/bin/env python3
"""Share Dusky GTK colors, GTK3 themes and cursors with Flatpak applications.

Run once as the desktop user during setup, or manually after installing Flatpak.
Global user overrides apply to installed and future apps, including system apps.
No downloads or running applications are needed. Restart open apps afterwards.
App-specific overrides remain in force; non-GTK apps choose their own colors.
"""

import argparse
import configparser
import shutil
import subprocess
import sys

THEME_DIRECTORIES = (
    "xdg-config/gtk-3.0",
    "xdg-config/gtk-4.0",
    "xdg-data/themes",
    "xdg-data/icons",
)


def configure(flatpak: str, dry_run: bool = False) -> list[str]:
    result = subprocess.run([flatpak, "override", "--user", "--show"],
                            check=True, capture_output=True, text=True)
    overrides = configparser.ConfigParser(interpolation=None)
    overrides.read_string(result.stdout)
    existing = overrides.get("Context", "filesystems", fallback="").split(";")
    # Preserve explicit writable access and unrelated user preferences. Exact
    # XDG mappings are needed: broader home access does not map app-private XDG.
    permitted = {entry.split(":", 1)[0] for entry in existing if not entry.startswith("!")}
    missing = [path for path in THEME_DIRECTORIES if path not in permitted]
    if missing and not dry_run:
        subprocess.run([flatpak, "override", "--user",
                        *(f"--filesystem={path}:ro" for path in missing)], check=True)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", "-n", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()
    flatpak = shutil.which("flatpak")
    if flatpak is None:
        if not args.quiet:
            print("Flatpak is not installed; skipped. Run this script after installing it.")
        return 0
    try:
        missing = configure(flatpak, args.dry_run)
    except (OSError, configparser.Error, subprocess.CalledProcessError) as error:
        print(f"Flatpak themes: {error}", file=sys.stderr)
        return 1
    if not args.quiet:
        if missing:
            action = "Would share" if args.dry_run else "Shared"
            print(f"{action} with Flatpak apps: {', '.join(missing)}")
            if not args.dry_run:
                print("Restart open Flatpak apps to load the theme. Future apps inherit this setup.")
        else:
            print("Flatpak theme access is already configured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

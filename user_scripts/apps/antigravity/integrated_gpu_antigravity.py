#!/usr/bin/env python3
"""Deploy the antigravity GPU-sandbox workaround.

Creates ~/.local/bin/antigravity (wrapper with --disable-gpu-sandbox)
and writes ~/.local/share/applications/antigravity.desktop.
"""

import pathlib
import stat
import sys

BIN_DIR = pathlib.Path.home() / ".local" / "bin"
APPS_DIR = pathlib.Path.home() / ".local" / "share" / "applications"
REAL_BIN = pathlib.Path("/opt/Antigravity/antigravity")
WRAPPER_PATH = BIN_DIR / "antigravity"
DESKTOP_PATH = APPS_DIR / "antigravity.desktop"


def get_wrapper_src(real_bin: pathlib.Path) -> str:
    return f"""#!/bin/bash
exec {real_bin} --disable-gpu-sandbox "$@"
"""


def get_desktop_src(wrapper_path: pathlib.Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Name=Antigravity\n"
        "Comment=Experience liftoff\n"
        "GenericName=Agentic Platform\n"
        f"Exec={wrapper_path} %U\n"
        "Icon=antigravity\n"
        "Type=Application\n"
        "Terminal=false\n"
        "StartupNotify=false\n"
        "StartupWMClass=Antigravity\n"
        "Categories=Development;\n"
    )


def deploy() -> None:
    if not REAL_BIN.exists():
        print(f"Warning: Real Antigravity binary not found at {REAL_BIN}. "
              "Please make sure Antigravity is installed.", file=sys.stderr)

    try:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        APPS_DIR.mkdir(parents=True, exist_ok=True)

        wrapper_src = get_wrapper_src(REAL_BIN)
        WRAPPER_PATH.write_text(wrapper_src)
        WRAPPER_PATH.chmod(WRAPPER_PATH.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        desktop_src = get_desktop_src(WRAPPER_PATH)
        DESKTOP_PATH.write_text(desktop_src)

        print(f"Created {WRAPPER_PATH}")
        print(f"Created {DESKTOP_PATH}")
        print("Restart your session or run 'update-desktop-database ~/.local/share/applications' to pick up the desktop entry.")
    except OSError as e:
        print(f"Error during deployment: {e}", file=sys.stderr)
        sys.exit(1)


def remove() -> None:
    try:
        for p in (WRAPPER_PATH, DESKTOP_PATH):
            if p.exists():
                p.unlink()
                print(f"Removed {p}")
    except OSError as e:
        print(f"Error during removal: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--remove":
        remove()
    else:
        deploy()

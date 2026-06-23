#!/usr/bin/env python3
# Create configuration directories for Matugen for theming
# -----------------------------------------------------------------------------
# Description:  Bootstrap configuration directories for Hyprland/UWSM environment
# Target:       Arch Linux / Python 3.14+
# Standards:    EAFP Idempotency, Strictly Typed, TTY-aware Logging, Atomic
# -----------------------------------------------------------------------------

import sys
from pathlib import Path

# 1. Visual Feedback (TTY-Aware ANSI Colors)
# Only emit colors if connected to a real terminal to prevent log pollution in UWSM/systemd
USE_COLORS = sys.stdout.isatty()
C_RESET = '\033[0m' if USE_COLORS else ''
C_GREEN = '\033[1;32m' if USE_COLORS else ''
C_BLUE = '\033[1;34m' if USE_COLORS else ''
C_RED = '\033[1;31m' if USE_COLORS else ''
C_GRAY = '\033[0;90m' if USE_COLORS else ''

# 2. Immutable Configuration
TARGET_DIRS: tuple[str, ...] = (
    "~/.config/gtk-3.0",
    "~/.config/gtk-4.0",
    "~/.config/Kvantum/matugen",
    "~/.config/btop/themes",
    "~/.config/yazi",
    "~/.config/zellij/themes",
    "~/.config/kitty",
    "~/.config/foot",
    "~/.config/opencode/themes",
    "~/.config/VSCodium/User",
    "~/.config/alacritty",
    "~/.config/zed/themes",
    "~/.config/zathura",
    "~/.cache/wal",
    "~/Documents/pensive/.obsidian/snippets",
    "~/.config/obs-studio/themes",
    "~/.config/vesktop/themes",
    "~/.config/BeeperTexts",
    "~/.config/AdwSteamGtk",
    "~/.config/cava/themes",
)

# 3. Utility Functions (Forced flush for real-time reporting)
def log_info(msg: str) -> None:
    print(f"{C_BLUE}[INFO]{C_RESET} {msg}", flush=True)

def log_success(msg: str) -> None:
    print(f"{C_GREEN}[OK]{C_RESET}   {msg}", flush=True)

def log_skip(msg: str) -> None:
    print(f"{C_GRAY}[SKIP]{C_RESET} {msg}", flush=True)

def log_err(msg: str) -> None:
    print(f"{C_RED}[ERR]{C_RESET}  {msg}", file=sys.stderr, flush=True)

# 4. Main Logic
def main() -> int:
    log_info("Initializing environment directories...")
    error_count = 0

    for dir_path_str in TARGET_DIRS:
        # Resolve tilde (~) dynamically
        target_path = Path(dir_path_str).expanduser()

        # EAFP Paradigm (Easier to Ask for Forgiveness than Permission)
        # We attempt creation immediately to avoid Time-Of-Check/Time-Of-Use race conditions.
        try:
            # exist_ok=False ensures we know exactly if *this* script created it
            target_path.mkdir(parents=True, exist_ok=False)
            log_success(f"Created: {target_path}")
            
        except FileExistsError:
            # The path already exists. Now we verify it is actually a directory.
            if target_path.is_dir():
                log_skip(f"Directory exists: {target_path}")
            else:
                log_err(f"Path exists but is NOT a directory (file collision): {target_path}")
                error_count += 1
                
        except PermissionError:
            log_err(f"Permission denied: {target_path}")
            error_count += 1
            
        except OSError as e:
            # Catches NotADirectoryError (if a parent in the path is a file) and other OS-level blocks
            log_err(f"OS error creating {target_path}: {e}")
            error_count += 1

    # 5. Final Status Reporting
    if error_count > 0:
        log_err(f"Initialization interrupted. Encountered {error_count} error(s).")
        return 1

    log_info("Directory initialization complete.")
    return 0

# 6. Entry Point
if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(flush=True)  # Clean newline after ^C
        log_err("Script interrupted by user.")
        sys.exit(130)

#!/usr/bin/env python3
#d: Create or reset the Matugen state config

import sys
import subprocess
import importlib
import textwrap
import shutil
from pathlib import Path

# --- Dependency Check & Auto-Install ---
try:
    from rich.console import Console
    from rich.theme import Theme
except ImportError:
    print("[WARN] The 'rich' library is not found. Attempting auto-installation...")
    
    # Bulletproof check: Ensure we are actually on an Arch-based system with pacman
    if not shutil.which("pacman"):
        print("[ERR]  'pacman' package manager not found. This script requires an Arch Linux environment.")
        sys.exit(1)
        
    try:
        # Surgically call pacman to install the required package non-interactively
        subprocess.check_call(["sudo", "pacman", "-S", "--noconfirm", "--needed", "python-rich"])
        
        # Invalidate Python's internal import caches to detect the new package at runtime
        importlib.invalidate_caches()
        
        # Retry the imports globally
        from rich.console import Console
        from rich.theme import Theme
        print("[OK]   'python-rich' auto-installed and imported successfully.")
    except subprocess.CalledProcessError as e:
        print(f"[ERR]  Pacman failed to auto-install 'python-rich'. (Exit Code: {e.returncode})")
        print("       Please check your network or sudo privileges and install manually: sudo pacman -S python-rich")
        sys.exit(1)
    except ImportError:
        print("[ERR]  'python-rich' was installed via pacman but failed to import in the current context.")
        print("       Please re-run the script.")
        sys.exit(1)

# --- Strict Mode & Configuration ---
# Custom theme mirroring the Orchestrator's ANSI color codes
custom_theme = Theme({
    "info": "bold blue",
    "success": "bold green",
    "warn": "bold yellow",
    "error": "bold red"
})
console = Console(theme=custom_theme)

# --- Helper Functions ---
def log_info(msg: str) -> None:
    console.print(f"[info][INFO][/info] {msg}")

def log_success(msg: str) -> None:
    console.print(f"[success][OK][/success]   {msg}")

def log_warn(msg: str) -> None:
    console.print(f"[warn][WARN][/warn] {msg}")

def log_error(msg: str) -> None:
    console.print(f"[error][ERR][/error]  {msg}")

def main() -> None:
    # ------------------------------------------------------------------------------
    # 1. Paths Definition
    # ------------------------------------------------------------------------------
    target_path = Path("~/.config/dusky/settings/dusky_theme/state.conf").expanduser()
    target_dir = target_path.parent

    # ------------------------------------------------------------------------------
    # 2. Configuration Block (Easily Editable)
    # ------------------------------------------------------------------------------
    # textwrap.dedent strips leading whitespace, allowing natural indentation in code
    state_content = textwrap.dedent("""\
# Dusky Theme State File
THEME_MODE="dark"
MATUGEN_TYPE="scheme-tonal-spot"
MATUGEN_CONTRAST="0"
SOURCE_COLOR_INDEX="0"
BASE16_BACKEND="disable"
AWWW_TRANS_TYPE="random"
AWWW_TRANS_DURATION="2"
AWWW_TRANS_FPS="60"
AWWW_TRANS_BEZIER=".54,0,.34,.99"
AWWW_TRANS_ANGLE="30"
AWWW_TRANS_POS="center"
LAST_APPLIED_HEX=#FF0000
    """)

    # ------------------------------------------------------------------------------
    # 3. Main Logic: Idempotent Directory & File Creation
    # ------------------------------------------------------------------------------
    log_info("Initializing Dusky Theme state configuration...")

    if not target_dir.exists():
        log_info(f"Creating config directory: {target_dir}")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            log_success(f"Directory created: {target_dir}")
        except PermissionError:
            log_error("Permission denied. This script modifies user configuration and should NOT be run as root.")
            sys.exit(1)
        except Exception as e:
            log_error(f"Failed to create directory: {e}")
            sys.exit(1)
    else:
        log_info(f"Directory exists: {target_dir} (verifying contents...)")

    if target_path.exists():
        log_warn(f"Target file already exists at '{target_path.name}'. Overwriting to ensure exact state...")
    else:
        log_info(f"Writing new file: {target_path.name}...")

    try:
        # write_text implicitly handles opening, truncating (overwriting), writing, and closing
        target_path.write_text(state_content, encoding="utf-8")
        log_success(f"Successfully wrote exact state to: {target_path.name}")
    except PermissionError:
        log_error(f"Permission denied when writing to {target_path}. Ensure you have write access.")
        sys.exit(1)
    except Exception as e:
        log_error(f"Failed to write file: {e}")
        sys.exit(1)

    # ------------------------------------------------------------------------------
    # 4. Completion
    # ------------------------------------------------------------------------------
    print()
    log_success("Setup complete!")
    log_info(f"Your configuration is securely placed in: {target_dir}")

if __name__ == "__main__":
    main()

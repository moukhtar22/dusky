#!/usr/bin/env python3
# deployes dusky overlay configs
"""
Initializes or validates the 'edit_here' user configuration overlay for Hyprland.
Ensures all template files exist, deploying from the defaults directory.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --- ANSI Color Codes ---
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[0;33m'
    BLUE = '\033[0;34m'
    RESET = '\033[0m'

class ColoredFormatter(logging.Formatter):
    """Custom logging formatter for ANSI colored outputs."""
    FORMATS = {
        logging.DEBUG: f"{Colors.BLUE}[DEBUG]{Colors.RESET} %(message)s",
        logging.INFO: f"{Colors.BLUE}[INFO]{Colors.RESET} %(message)s",
        logging.WARNING: f"{Colors.YELLOW}[WARN]{Colors.RESET} %(message)s",
        logging.ERROR: f"{Colors.RED}[ERR]{Colors.RESET}  %(message)s",
        logging.CRITICAL: f"{Colors.RED}[CRIT]{Colors.RESET} %(message)s",
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, self.FORMATS[logging.INFO])
        # Success level is custom handled via an extra dict property
        if getattr(record, 'success', False):
            log_fmt = f"{Colors.GREEN}[OK]{Colors.RESET}   %(message)s"
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def log_success(msg: str):
    """Helper for success messages formatted exactly like the bash script."""
    logger.info(msg, extra={'success': True})

def main() -> None:
    if os.geteuid() == 0:
        logger.error("This script must NOT be run as root.")
        logger.error(f"It modifies user configuration files in {Path.home()}.")
        sys.exit(1)

    HOME = Path.home()
    HYPR_DIR = HOME / ".config" / "hypr"
    EDIT_DIR = HYPR_DIR / "edit_here"
    EDIT_SOURCE_DIR = EDIT_DIR / "source"
    MAIN_CONF = HYPR_DIR / "hyprland.lua"
    NEW_CONF = EDIT_DIR / "hyprland.lua"
    DEFAULTS_DIR = HOME / "user_scripts" / "hypr" / "defaults" / "edit_here"

    APPS_DEFAULTS_REQUIRE = 'require("edit_here.source.default_apps")'
    OVERLAY_REQUIRE = 'require("edit_here.hyprland")'

    # Ensure base directories
    if not HYPR_DIR.exists():
        logger.info(f"Creating Hyprland config directory: {HYPR_DIR}")
        HYPR_DIR.mkdir(parents=True, exist_ok=True)
    
    if not MAIN_CONF.exists():
        logger.warning(f"Main Hyprland config not found at {MAIN_CONF}.")
        logger.warning("Creating empty file. You will need to populate it with your base config.")
        MAIN_CONF.touch()
        
    if not DEFAULTS_DIR.exists():
        logger.error(f"Defaults directory not found at {DEFAULTS_DIR}.")
        sys.exit(1)

    # Dynamically find default lua files
    default_files = sorted(p.name for p in DEFAULTS_DIR.glob("*.lua"))
    if not default_files:
        logger.error(f"No .lua files found in {DEFAULTS_DIR}")
        sys.exit(1)
        
    parser = argparse.ArgumentParser(description="Initialize or validate the 'edit_here' user config overlay for Hyprland.")
    parser.add_argument("--force", action="store_true", help="Backs up existing configs and regenerates templates.")
    
    # Add dynamic flags (support both --name and --name.lua)
    for f in default_files:
        base_name = f.removesuffix('.lua')
        dest_name = base_name.replace('-', '_')
        parser.add_argument(f"--{base_name}", f"--{f}", action="store_true", dest=dest_name, help=f"Dynamically target {f}")
        
    args, unknown = parser.parse_known_args()
    
    if unknown:
        logger.error(f"Unknown argument: {unknown[0]}")
        logger.info("Available file flags are:")
        for f in default_files:
            logger.info(f"  --{f.removesuffix('.lua')} (or --{f})")
        sys.exit(1)

    # Determine targets
    args_dict = vars(args)
    force_mode = args_dict.pop('force', False)
    
    target_files = [
        df for df in default_files
        if args_dict.get(df.removesuffix('.lua').replace('-', '_'))
    ]
    
    all_files_targeted = False
    if not target_files:
        target_files = list(default_files)
        all_files_targeted = True

    # Force Mode Backups
    if force_mode:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if all_files_targeted and EDIT_DIR.exists():
            backup_path = HYPR_DIR / f"edit_here.bak_{timestamp}"
            logger.warning(f"Force mode (All Files): Backing up '{EDIT_DIR}' to '{backup_path}'...")
            shutil.move(str(EDIT_DIR), str(backup_path))
            log_success("Backup complete. Proceeding with clean regeneration.")
        elif not all_files_targeted:
            logger.warning("Force mode (Targeted Files): Backing up specified files...")
            target_backup_dir = EDIT_SOURCE_DIR / "backups"
            target_backup_dir.mkdir(parents=True, exist_ok=True)
            for file in target_files:
                target_path = EDIT_SOURCE_DIR / file
                if target_path.exists():
                    backup_name = f"{file}.bak_{timestamp}"
                    shutil.move(str(target_path), str(target_backup_dir / backup_name))
                    log_success(f"  - Backed up: {file} -> backups/{backup_name}")

    logger.info("Initializing/Verifying Hyprland user configuration overlay...")
    
    if not EDIT_SOURCE_DIR.exists():
        logger.info(f"Creating directory: {EDIT_SOURCE_DIR}")
        EDIT_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    else:
        logger.info(f"Directory exists: {EDIT_SOURCE_DIR} (verifying contents...)")

    # Deploy targeted files
    for file in target_files:
        target_file = EDIT_SOURCE_DIR / file
        if target_file.exists():
            logger.info(f"  - Exists: {file}")
        else:
            default_file = DEFAULTS_DIR / file
            if default_file.exists():
                logger.warning(f"  - Missing: {file} -> Deploying from defaults...")
                shutil.copy2(default_file, target_file)
                log_success(f"    Deployed: {file}")
            else:
                logger.error(f"  - Default for {file} not found in {DEFAULTS_DIR}!")

    # Verify/Update loader file
    if NEW_CONF.exists():
        logger.info(f"Verifying loader file: {NEW_CONF}")
        content = NEW_CONF.read_text().splitlines()
        changed = False
        
        for file in target_files:
            if file == "default_apps.lua":
                continue
            module_name = file.removesuffix('.lua')
            req_str = f'require("edit_here.source.{module_name}")'
            
            # Check if commented out or present
            commented_idx = next((i for i, line in enumerate(content) if req_str in line and line.lstrip().startswith('--')), -1)
            uncommented_idx = next((i for i, line in enumerate(content) if req_str in line and not line.lstrip().startswith('--')), -1)
            
            if uncommented_idx == -1:
                if commented_idx != -1:
                    content[commented_idx] = content[commented_idx].replace('--', '', 1).lstrip()
                    log_success(f"  - Activated {file} in loader.")
                else:
                    content.append(req_str)
                    log_success(f"  - Appended {file} to loader.")
                changed = True

        if changed:
            NEW_CONF.write_text('\n'.join(content) + '\n')
            
    else:
        logger.warning(f"Loader file missing: {NEW_CONF} -> Creating...")
        header = (
            "-- ==============================================================================\n"
            "-- USER CONFIGURATION OVERLAY LOADER\n"
            "-- ==============================================================================\n"
            "-- This file is require()d at the bottom of hyprland.lua.\n"
            "-- It loads all your custom configuration files from 'source/'.\n"
            "-- Edit the specific files in 'source/' to apply your changes.\n"
            "--\n"
            "-- NOTE: 'default_apps.lua' is intentionally excluded here — it is require()d\n"
            "-- directly at the top of hyprland.lua so its globals are available first.\n"
            "-- ==============================================================================\n\n"
        )
        lines = [header]
        for file in default_files:
            if file == "default_apps.lua":
                continue
            module_name = file.removesuffix('.lua')
            if (EDIT_SOURCE_DIR / file).exists():
                lines.append(f'require("edit_here.source.{module_name}")\n')
            else:
                lines.append(f'-- require("edit_here.source.{module_name}") -- File missing/not deployed yet\n')
        NEW_CONF.write_text("".join(lines))
        log_success(f"Created loader: {NEW_CONF}")

    # Modify Main Config
    logger.info(f"Verifying main configuration at '{MAIN_CONF}'...")
    main_conf_content = MAIN_CONF.read_text()
    main_conf_lines = main_conf_content.splitlines()
    
    if APPS_DEFAULTS_REQUIRE in main_conf_content:
        log_success("Main config already contains default_apps require().")
    else:
        MAIN_CONF.write_text(f"{APPS_DEFAULTS_REQUIRE}\n" + "\n".join(main_conf_lines) + "\n")
        log_success(f"Prepended '{APPS_DEFAULTS_REQUIRE}' to the top of '{MAIN_CONF}'.")

    # Re-read content
    main_conf_content = MAIN_CONF.read_text()
    if OVERLAY_REQUIRE in main_conf_content:
        log_success("Main config already contains the overlay loader require().")
    else:
        MAIN_CONF.write_text(main_conf_content.rstrip('\n') + f"\n\n-- Source User Custom Config Overlay\n{OVERLAY_REQUIRE}\n")
        log_success(f"Appended '{OVERLAY_REQUIRE}' to '{MAIN_CONF}'.")

    # Hot-Reload
    if shutil.which("hyprctl"):
        try:
            subprocess.run(["hyprctl", "reload", "config-only"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            log_success("Forced hot-reload of Hyprland configuration (config-only).")
        except subprocess.CalledProcessError:
            pass

    print()
    log_success("Setup/Verification complete!")
    logger.info(f"Your custom configs are located in: {EDIT_DIR}")
    logger.info("To apply changes, save any .lua file (auto-reload) or run 'hyprctl reload'.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# desktop entries deployer with sergical edits for the hardcoded username
"""
==============================================================================
 DUSKY DESKTOP ENTRY SYNCHRONIZER (Enterprise Atomic Edition)
 Description: Idempotent, zero-trust, hash-based synchronizer.
              Features true atomic writes, strict XDG compliance, surrogate 
              escaping, C-optimized file digests, and power-loss safety mechanisms.
 Target:      Arch Linux / Python 3.14+
==============================================================================
"""

import os
import sys
import json
import hashlib
import re
from pathlib import Path

# ------------------------------------------------------------------------------
# 1. Strict Environment & XDG Configuration
# ------------------------------------------------------------------------------
USER = os.environ.get('USER')
if not USER:
    print("\033[31m✖ CRITICAL ERROR: USER environment variable is not set.\033[0m", file=sys.stderr)
    sys.exit(1)

HOME = Path.home()

def get_xdg_dir(env_var: str, default: Path) -> Path:
    """
    Safely retrieves XDG variables per the XDG Base Directory Specification.
    Strictly ignores empty strings and relative paths, falling back to defaults.
    """
    val = os.environ.get(env_var)
    if val:
        path = Path(val)
        if path.is_absolute():
            return path
    return default

XDG_CONFIG_HOME = get_xdg_dir('XDG_CONFIG_HOME', HOME / '.config')
XDG_DATA_HOME   = get_xdg_dir('XDG_DATA_HOME', HOME / '.local' / 'share')
XDG_STATE_HOME  = get_xdg_dir('XDG_STATE_HOME', HOME / '.local' / 'state')

SRC_DIR    = XDG_CONFIG_HOME / 'desktop_entries' / 'all'
DEST_DIR   = XDG_DATA_HOME / 'applications'
STATE_DIR  = XDG_STATE_HOME / 'dusky'
STATE_FILE = STATE_DIR / 'desktop_sync_state.json'

# Pre-compile regex engines for maximum execution speed
# Accounts for standard keys and localized keys (e.g. Exec[fr]=)
PATCH_KEYS_RE = re.compile(r'^(Exec|TryExec|Icon|Path)(?:\[[^\]]+\])?\s*=')

# Strictly targets standard Linux user home paths.
USER_PATH_RE = re.compile(r'/home/[^/]+/')

# TTY Color Support Check
if sys.stdout.isatty() and not os.environ.get('NO_COLOR'):
    C_RESET  = '\033[0m'
    C_BOLD   = '\033[1m'
    C_GREEN  = '\033[32m'
    C_BLUE   = '\033[34m'
    C_YELLOW = '\033[33m'
    C_RED    = '\033[31m'
    C_CYAN   = '\033[36m'
else:
    C_RESET = C_BOLD = C_GREEN = C_BLUE = C_YELLOW = C_RED = C_CYAN = ''

# ------------------------------------------------------------------------------
# 2. Advanced Helper Subsystems
# ------------------------------------------------------------------------------
def ensure_dir(path: Path) -> None:
    """Ensures a directory exists, safely averting FileExistsError on files and broken symlinks."""
    if path.is_symlink() or path.exists():
        if not path.is_dir():
            print(f"{C_RED}✖ CRITICAL ERROR: Target path occupies namespace but is not a directory: {path}{C_RESET}", file=sys.stderr)
            sys.exit(1)
    else:
        path.mkdir(parents=True, exist_ok=True)

def cleanup_stale_temp_files(directories: list[Path]) -> None:
    """
    Garbage collects orphaned .tmp files left by hard crashes or power losses.
    Validates process ID (PID) to explicitly prevent concurrency destruction bugs.
    """
    for d in directories:
        if not d.is_dir():
            continue
        for tmp_file in d.glob('*.tmp'):
            parts = tmp_file.name.split('.')
            if len(parts) >= 3 and parts[-2].isdigit():
                pid = int(parts[-2])
                try:
                    os.kill(pid, 0)
                    continue  # Process is alive, do not touch its active memory
                except ProcessLookupError:
                    pass      # Process is dead, inherently safe to clean
                except PermissionError:
                    continue  # Process belongs to root/other, assume it's executing
            
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass

def get_file_hash(filepath: Path) -> str | None:
    """Calculates SHA-256 using C-optimized digest. Returns None on read failure."""
    try:
        with filepath.open('rb') as f:
            return hashlib.file_digest(f, 'sha256').hexdigest()
    except OSError:
        return None

def patch_line(line: str) -> str:
    """Surgically substitutes username ONLY in valid keys."""
    if PATCH_KEYS_RE.match(line):
        return USER_PATH_RE.sub(lambda _: f'/home/{USER}/', line)
    return line

def write_atomic(dest: Path, content: str, mode: int | None = None, errors: str = 'strict') -> bool:
    """
    Executes a genuinely atomic write adhering to strict POSIX principles.
    Mitigates TOCTOU permission exploits, zero-byte file truncation, and parent inode power-loss.
    """
    temp_file = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    
    # Mitigate rare PID wrap-around collisions for our specific temp file
    temp_file.unlink(missing_ok=True)
    
    try:
        # Atomic file creation: Prevent TOCTOU permission leaks using O_CREAT | O_EXCL
        # Initialize heavily restricted (0o600) to shield sensitive data mid-write.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(temp_file, flags, 0o600)
        
        with open(fd, 'w', encoding='utf-8', errors=errors) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())  # Assures data exists safely on platter/flash before node rename
            
            # Retroactively apply definitive permissions once safe
            if mode is not None:
                os.fchmod(f.fileno(), mode)
            else:
                current_umask = os.umask(0)
                os.umask(current_umask)
                os.fchmod(f.fileno(), 0o666 & ~current_umask)

        temp_file.replace(dest)
        
        # POSIX Guarantee: Fsync the parent directory metadata to enforce node linkage
        try:
            dir_fd = os.open(dest.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
            
        return True
    except OSError as e:
        print(f"{C_RED}✖ Critical IO Error on {dest.name}: {e}{C_RESET}", file=sys.stderr)
        return False
    finally:
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            pass

def atomic_patch_and_copy(src: Path, dest: Path) -> bool:
    """Reads source, patches, and writes to destination safely."""
    try:
        lines = src.read_text(encoding='utf-8', errors='surrogateescape').splitlines(keepends=True)
        patched_content = "".join(patch_line(line) for line in lines)
        return write_atomic(dest, patched_content, mode=src.stat().st_mode, errors='surrogateescape')
    except OSError as e:
        print(f"{C_RED}✖ Read Error on {src.name}: {e}{C_RESET}", file=sys.stderr)
        return False

def load_state() -> dict:
    """Safely loads tracking JSON, gracefully resetting on ANY corruption."""
    if STATE_FILE.is_file():
        try:
            state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            if isinstance(state, dict):
                return state
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass  # Fallthrough to reset if corrupted, unreadable, or scrambled
    return {"tracked_files": {}}

# ------------------------------------------------------------------------------
# 3. Main Execution Engine
# ------------------------------------------------------------------------------
def main():
    quiet_mode = len(sys.argv) > 1 and sys.argv[1] == '--quiet'
    if quiet_mode:
        sys.stdout = open(os.devnull, 'w')

    print(f"{C_BOLD}{C_BLUE}Arch Linux Desktop Entry Synchronizer{C_RESET}")
    print(f"{C_BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print(f"{C_CYAN}Source Dir:{C_RESET} {SRC_DIR}")
    print(f"{C_CYAN}Target Dir:{C_RESET} {DEST_DIR}")
    print(f"{C_CYAN}Target User:{C_RESET} {C_BOLD}{USER}{C_RESET}\n")

    if SRC_DIR.is_symlink() or SRC_DIR.exists():
        if not SRC_DIR.is_dir():
            print(f"{C_RED}✖ CRITICAL ERROR: Source path occupies namespace but is not a directory: {SRC_DIR}{C_RESET}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"{C_YELLOW}⚠ Source directory missing: {SRC_DIR}{C_RESET}")
        print(f"{C_CYAN}Creating directory structure for future tracking...{C_RESET}")
        ensure_dir(SRC_DIR)
        return

    # Infrastructure Verification
    ensure_dir(DEST_DIR)
    ensure_dir(STATE_DIR)
    cleanup_stale_temp_files([DEST_DIR, STATE_DIR])

    state = load_state()
    old_tracked_files: dict[str, str] = state.get("tracked_files", {})
    new_tracked_files: dict[str, str] = {}

    updated_count = 0
    skipped_count = 0
    removed_count = 0

    # --- Phase 1: Engine Synchronization & Patching ---
    for src_file in SRC_DIR.iterdir():
        if not src_file.is_file() or src_file.suffix != '.desktop':
            continue
            
        filename = src_file.name
        dest_file = DEST_DIR / filename
        current_hash = get_file_hash(src_file)
        
        # Guard against hash collision loops on missing/unreadable files
        if current_hash is None:
            print(f"{C_RED}✖ Unreadable source file (Skipping):{C_RESET} {filename}", file=sys.stderr)
            if filename in old_tracked_files:
                new_tracked_files[filename] = old_tracked_files[filename]
            continue
        
        if old_tracked_files.get(filename) != current_hash or not dest_file.exists():
            if atomic_patch_and_copy(src_file, dest_file):
                new_tracked_files[filename] = current_hash
                print(f"{C_GREEN}✔ Synced & Patched:{C_RESET} {filename}")
                updated_count += 1
            else:
                # Retain tracking on failure to prevent Phase 2 Orphan deletion bug
                if filename in old_tracked_files:
                    new_tracked_files[filename] = old_tracked_files[filename]
        else:
            new_tracked_files[filename] = current_hash
            skipped_count += 1

    # --- Phase 2: Idempotent Garbage Collection (Orphans) ---
    orphans = set(old_tracked_files.keys()) - set(new_tracked_files.keys())
    
    for orphan in orphans:
        orphan_dest = DEST_DIR / orphan
        try:
            orphan_dest.unlink(missing_ok=True)
            print(f"{C_RED}✖ Pruned Orphan:{C_RESET}    {orphan}")
            removed_count += 1
        except OSError as e:
            print(f"{C_RED}✖ Critical Error removing {orphan}: {e}{C_RESET}", file=sys.stderr)

    # --- Phase 3: Commit State ---
    state["tracked_files"] = new_tracked_files
    write_atomic(STATE_FILE, json.dumps(state, indent=4), errors='strict')

    # --- Phase 4: Execution Summary Output ---
    print(f"\n{C_BOLD}{C_BLUE}Summary{C_RESET}")
    print(f"{C_BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print(f"  {C_GREEN}✔ Updated/Added:{C_RESET} {updated_count}")
    print(f"  {C_CYAN}• Skipped:{C_RESET}       {skipped_count}")
    if removed_count > 0:
        print(f"  {C_RED}✖ Pruned:{C_RESET}        {removed_count}")
    print()

if __name__ == '__main__':
    main()

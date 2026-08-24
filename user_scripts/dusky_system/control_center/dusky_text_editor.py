#!/usr/bin/env python3
"""
dusky_text_editor.py: Dynamic text editor launcher for the Dusky Control Center.
Parses default_apps.lua and wraps terminal editors in the default terminal emulator.
Executes via os.execvp to replace the process cleanly with dusky-run.
"""

import os
import re
import sys
from pathlib import Path

CONF_VARS = Path.home() / ".config/hypr/edit_here/source/default_apps.lua"
DEFAULT_EDITOR = "mousepad"
DEFAULT_TERMINAL = "kitty"
TERMINAL_EDITORS = {"nvim", "nano", "helix", "micro", "vi", "vim"}

def parse_config() -> tuple[str, str]:
    """Parses textEditor and terminal values from default_apps.lua."""
    editor = DEFAULT_EDITOR
    terminal = DEFAULT_TERMINAL
    
    if not CONF_VARS.is_file():
        return editor, terminal
        
    try:
        content = CONF_VARS.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("--"):
                continue
            
            editor_match = re.search(r'(?:local\s+)?textEditor\s*=\s*[\'"]([^\'"]+)[\'"]', stripped)
            if editor_match:
                val = editor_match.group(1).strip()
                if val:
                    editor = val

            terminal_match = re.search(r'(?:local\s+)?terminal\s*=\s*[\'"]([^\'"]+)[\'"]', stripped)
            if terminal_match:
                val = terminal_match.group(1).strip()
                if val:
                    terminal = val
    except Exception as e:
        sys.stderr.write(f"dusky-text-editor: error reading config: {e}\n")
        
    return editor, terminal

def main():
    editor, terminal = parse_config()
    args = sys.argv[1:]
    
    # Check if the chosen editor requires a terminal window
    is_term_editor = editor.lower() in TERMINAL_EDITORS
    
    # Base dusky-run command
    cmd_args = ["dusky-run"]
    
    if is_term_editor:
        # Launch terminal editor inside terminal wrapper
        term_lower = terminal.lower()
        if "kitty" in term_lower:
            cmd_args.extend([terminal, "--class", editor, editor])
        elif "foot" in term_lower:
            cmd_args.extend([terminal, "--app-id", editor, editor])
        elif "alacritty" in term_lower:
            cmd_args.extend([terminal, "--class", editor, "-e", editor])
        elif "wezterm" in term_lower:
            cmd_args.extend([terminal, "start", "--class", editor, "--", editor])
        else:
            # Fallback for generic terminal wrappers
            cmd_args.extend([terminal, "-e", editor])
    else:
        # Launch GUI editor directly
        cmd_args.append(editor)
        
    # Append the target file paths
    cmd_args.extend(args)
    
    try:
        # Replaces the current process with dusky-run cleanly (no Python overhead remains)
        os.execvp(cmd_args[0], cmd_args)
    except Exception as e:
        sys.stderr.write(f"dusky-text-editor: failed to execute {cmd_args[0]}: {e}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()

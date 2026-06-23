#!/usr/bin/env bash
# using pregenerated colors to get rid of hyprland errors
set -euo pipefail

# Define absolute paths using the HOME variable
FRESH_DIR="$HOME/.config/matugen/generated_fresh"
TARGET_DIR="$HOME/.config/matugen/generated"

# 1. Check if the source directory exists. 
# If it doesn't, the script exits cleanly without doing anything (ensuring idempotency).
if [[ -d "$FRESH_DIR" ]]; then
    
    # 2. If the target directory already exists, delete it and its contents.
    if [[ -d "$TARGET_DIR" ]]; then
        rm -rf "$TARGET_DIR"
    fi
    
    # 3. Atomically rename the fresh directory to the target name.
    mv "$FRESH_DIR" "$TARGET_DIR"
fi

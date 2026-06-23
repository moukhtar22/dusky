#!/usr/bin/env python3
"""
Hyprland Monitor Scale Adjuster (Lua Config Edition)
Optimized for 0ms latency, Wayland math constraints, and strict Lua syntax safety.
"""
import sys
import os
import subprocess
import json
import tempfile
import re
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

# --- Immutable Configuration ---
CONFIG_DIR = Path.home() / ".config/hypr/edit_here/source"
CONFIG_FILE = CONFIG_DIR / "monitors.lua"
NOTIFY_TAG = "hypr_scale_adjust"
MIN_LOGICAL_WIDTH = 640
MIN_LOGICAL_HEIGHT = 360

# Standard Wayland fractional/integer scaling steps
SCALE_STEPS = [
    0.5, 0.6, 0.75, 0.8, 0.9, 1.0, 1.0625, 1.1, 1.125, 1.15, 1.2, 1.25,
    1.33, 1.4, 1.5, 1.6, 1.67, 1.75, 1.8, 1.88, 2.0, 2.25, 2.4, 2.5,
    2.67, 2.8, 3.0
]

# --- Runtime State & Logging ---
DEBUG = os.environ.get("DEBUG") == "1"

def log_err(msg: str) -> None: sys.stderr.write(f"\033[0;31m[ERROR]\033[0m {msg}\n")
def log_warn(msg: str) -> None: sys.stderr.write(f"\033[0;33m[WARN]\033[0m {msg}\n")
def log_info(msg: str) -> None: sys.stderr.write(f"\033[0;32m[INFO]\033[0m {msg}\n")
def log_debug(msg: str) -> None:
    if DEBUG: sys.stderr.write(f"\033[0;34m[DEBUG]\033[0m {msg}\n")

def notify(title: str, body: str, urgency: str = "low") -> None:
    """Dispatches a notification safely, ignoring if the daemon is missing."""
    try:
        subprocess.run([
            "notify-send", 
            "-h", f"string:x-canonical-private-synchronous:{NOTIFY_TAG}",
            "-u", urgency, 
            "-t", "2000", 
            title, 
            body
        ], stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass

def get_active_monitor(target_override: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves full monitor state from Hyprland IPC."""
    try:
        res = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True, text=True, check=True)
        monitors = json.loads(res.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as e:
        log_err(f"Hyprland IPC Communication Failure: {e}")
        sys.exit(1)

    if not monitors:
        log_err("No active monitors found.")
        sys.exit(1)

    if target_override:
        target = next((m for m in monitors if m.get("name") == target_override), None)
        if not target:
            log_err(f"Target monitor '{target_override}' not found.")
            sys.exit(1)
    else:
        target = next((m for m in monitors if m.get("focused")), monitors[0])
        
    return target

def compute_next_scale(current: float, direction: str, phys_w: int, phys_h: int) -> Optional[float]:
    """
    Two-Tier Smart Math: Tries to find a mathematically perfect Wayland scale.
    If the monitor resolution prevents perfect division, falls back to the nearest standard fraction.
    """
    perfect_scales: List[float] = []
    fallback_scales: List[float] = []

    for s in SCALE_STEPS:
        lw, lh = phys_w / s, phys_h / s
        if lw < MIN_LOGICAL_WIDTH or lh < MIN_LOGICAL_HEIGHT:
            continue
            
        fallback_scales.append(s)
        
        # Strict validation: Only accept if it results in clean logical pixels
        if abs(lw - round(lw)) <= 0.01 and abs(lh - round(lh)) <= 0.01:
            perfect_scales.append(s)

    # If no perfect scales exist for this screen ratio, use standard fractions
    search_list = perfect_scales if perfect_scales else fallback_scales

    if not search_list:
        return 1.0

    if direction == "+":
        candidates = [s for s in search_list if s > current + 0.001]
        return min(candidates) if candidates else None
    else:
        candidates = [s for s in search_list if s < current - 0.001]
        return max(candidates) if candidates else None

def update_lua_config_atomically(monitor_data: Dict[str, Any], new_scale: float) -> None:
    """Safely injects scale parameters into the Lua configuration using POSIX atomics."""
    if not CONFIG_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text("-- USER CONFIGURATION: monitors.lua\n\n")

    real_path = CONFIG_FILE.resolve()
    with open(real_path, "r") as f:
        config_text = f.read()

    mon_name = monitor_data.get("name", "")
    mon_desc = monitor_data.get("description", "")
    found = False

    log_debug(f"Updating Lua config: {mon_name} -> {new_scale:g}")

    # Safely captures Lua tables avoiding greedy nested brace consumption
    block_pattern = re.compile(
        r'(^[ \t]*hl\.monitor\s*\(\s*\{(?:[^{}]|\{[^{}]*\})*\}\s*\))', 
        re.MULTILINE | re.DOTALL
    )

    def block_replacer(match: re.Match) -> str:
        nonlocal found
        block = match.group(1)
        
        output_match = re.search(r'output\s*=\s*["\'](.*?)["\']', block)
        if not output_match:
            return block
            
        out_val = output_match.group(1)
        is_target = False
        
        if out_val == mon_name:
            is_target = True
        elif out_val.startswith("desc:") and out_val[5:] in mon_desc:
            is_target = True

        if is_target:
            found = True
            if re.search(r'scale\s*=\s*([0-9.]+|"auto"|\'auto\')', block):
                block = re.sub(r'(scale\s*=\s*)([0-9.]+|"auto"|\'auto\')', rf'\g<1>{new_scale:g}', block)
            else:
                # Lua Syntax Guard: Ensures a preceding comma exists before adding the new scale field
                if re.search(r',\s*\}\s*\)$', block):
                    block = re.sub(r'(\s*\}\s*\))$', rf'    scale = {new_scale:g},\n\g<1>', block)
                else:
                    block = re.sub(r'(\s*\}\s*\))$', rf',\n    scale = {new_scale:g}\n\g<1>', block)
                
        return block

    new_text = block_pattern.sub(block_replacer, config_text)

    # Append fallback if no explicit rule targets this monitor
    if not found:
        log_info(f"Appending new explicit Lua rule for: {mon_name}")
        append_text = f"""
hl.monitor({{
    output   = "{mon_name}",
    mode     = "preferred",
    position = "auto",
    scale    = {new_scale:g},
}})
"""
        new_text += append_text

    # Strict POSIX Atomic Replace prevents tearing on live config reloads
    fd, temp_path = tempfile.mkstemp(dir=real_path.parent, prefix=".monitors.lua.tmp.")
    try:
        with os.fdopen(fd, 'w') as temp_file:
            temp_file.write(new_text)
            
        os.chmod(temp_path, real_path.stat().st_mode)
        os.replace(temp_path, real_path)
    except Exception as e:
        os.remove(temp_path)
        log_err(f"Atomic write failed: {e}")
        sys.exit(1)

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("+", "-"):
        sys.stderr.write(f"Usage: {sys.argv[0]} [+|-]\n")
        sys.exit(1)

    direction = sys.argv[1]
    target_override = os.environ.get("HYPR_SCALE_MONITOR")
    
    mon_data = get_active_monitor(target_override)
    mon_name = mon_data.get("name", "Unknown")
    phys_w = int(mon_data.get("width", 1920))
    phys_h = int(mon_data.get("height", 1080))
    current_scale = float(mon_data.get("scale", 1.0))
    
    new_scale = compute_next_scale(current_scale, direction, phys_w, phys_h)
    
    if new_scale is None:
        log_warn(f"Limit reached: {current_scale:g}")
        notify("Monitor Scale", f"Limit Reached: {current_scale:g}", "normal")
        return

    update_lua_config_atomically(mon_data, new_scale)
    
    log_info(f"Applying scale {new_scale:g} via hyprctl reload")
    # Subprocess failure here shouldn't crash the script if Hyprland is simply slow
    subprocess.run(["hyprctl", "reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    
    # State Verification: Polling to prevent async Wayland race conditions
    actual_scale = current_scale
    for _ in range(25):  
        time.sleep(0.1)
        polled_data = get_active_monitor(mon_name)
        polled_scale = float(polled_data.get("scale", 1.0))
        
        if abs(polled_scale - current_scale) > 0.000001:
            actual_scale = polled_scale
            break
        actual_scale = polled_scale
    
    # Clamp validation
    if abs(actual_scale - new_scale) > 0.000001:
        log_warn(f"Hyprland override detected: requested {new_scale:g}, active is {actual_scale:g}")
        update_lua_config_atomically(mon_data, actual_scale)
        notify("Scale Adjusted", f"Requested {new_scale:g}, got {actual_scale:g}")
    else:
        logic_w, logic_h = round(phys_w / new_scale), round(phys_h / new_scale)
        notify(f"Display Scale: {new_scale:g}", f"Monitor: {mon_name}\nLogical: {logic_w}x{logic_h}")

if __name__ == "__main__":
    main()

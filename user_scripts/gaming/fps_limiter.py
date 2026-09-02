#!/usr/bin/env python3
"""
Universal FPS Limiter for Arch Linux, Wayland, and Hyprland.
Integrates MangoHud, Gamescope, and DXVK for precise frame rate capping.
"""

import os
import sys
import shutil
import re


def print_help():
    print(f"Usage: {sys.argv[0]} <fps> <command> [args...]")
    print("Caps the framerate of any game or executable on Linux / Wayland.")
    print()
    print("Examples:")
    print(f"  {sys.argv[0]} 60 steam")
    print(f"  {sys.argv[0]} 144 lutris")
    print(f"  {sys.argv[0]} 60 ./game.x86_64")
    print(f"  {sys.argv[0]} 30 prime-run ./game.sh")
    print()
    print("Backend Priority:")
    print("  1. MangoHud (Universal Vulkan & OpenGL overlay / limiter)")
    print("  2. Gamescope (Wayland micro-compositor frame limiter: -r <fps>)")
    print("  3. DXVK_FRAME_RATE (DirectX 9/10/11 -> Vulkan translation fallback)")


def update_mangohud_config(existing_cfg: str, fps: int) -> str:
    """Updates or appends fps_limit in MANGOHUD_CONFIG while preserving other user settings."""
    if not existing_cfg:
        return f"fps_limit={fps},no_display"
    
    # If fps_limit is already defined, replace it
    if re.search(r"\bfps_limit=\d+", existing_cfg):
        cfg = re.sub(r"\bfps_limit=\d+", f"fps_limit={fps}", existing_cfg)
    else:
        cfg = f"{existing_cfg},fps_limit={fps}"
        
    if "no_display" not in cfg and "read_cfg" not in cfg:
        cfg = f"{cfg},no_display"
        
    return cfg


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print_help()
        sys.exit(0 if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help") else 1)

    if len(sys.argv) < 3:
        print(f"Error: Missing command to execute.\nUsage: {sys.argv[0]} <fps> <command> [args...]", file=sys.stderr)
        sys.exit(1)

    try:
        fps = int(sys.argv[1])
    except ValueError:
        print(f"Error: Invalid FPS '{sys.argv[1]}'. Must be a positive integer.", file=sys.stderr)
        sys.exit(1)

    if fps <= 0:
        print(f"Error: FPS must be greater than 0, got {fps}.", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[2:]
    binary = cmd[0]

    resolved = shutil.which(binary)
    if not resolved:
        if os.path.exists(binary):
            if not os.path.isfile(binary):
                print(f"Error: '{binary}' is not a valid file.", file=sys.stderr)
                sys.exit(1)
            if not os.access(binary, os.X_OK):
                print(f"Error: '{binary}' is not executable. Run: chmod +x '{binary}'", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Error: Command or executable '{binary}' not found.", file=sys.stderr)
            sys.exit(1)

    env = os.environ.copy()

    # Determine best frame limiting backend
    has_mangohud = shutil.which("mangohud") is not None
    has_gamescope = shutil.which("gamescope") is not None

    if has_mangohud:
        existing_cfg = env.get("MANGOHUD_CONFIG", "")
        env["MANGOHUD"] = "1"
        env["MANGOHUD_CONFIG"] = update_mangohud_config(existing_cfg, fps)
        cmd = ["mangohud"] + cmd
    elif has_gamescope:
        cmd = ["gamescope", "-r", str(fps), "--"] + cmd
    else:
        env["DXVK_FRAME_RATE"] = str(fps)
        print(
            f"[fps_limiter] Warning: MangoHud and Gamescope not found. Set DXVK_FRAME_RATE={fps} fallback.\n"
            "For universal OpenGL/Vulkan frame limiting, install: sudo pacman -S mangohud lib32-mangohud",
            file=sys.stderr
        )

    exec_path = shutil.which(cmd[0]) or cmd[0]
    try:
        os.execvpe(exec_path, cmd, env)
    except PermissionError as e:
        print(f"Error: Permission denied executing '{exec_path}': {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"Error: Failed to execute '{exec_path}': {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

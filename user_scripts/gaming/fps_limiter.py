#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess


def ensure_mangohud():
    if shutil.which("mangohud"):
        return True
    if not shutil.which("sudo"):
        return False
    try:
        subprocess.run(
            ["sudo", "pacman", "-S", "--noconfirm", "mangohud", "lib32-mangohud"],
            check=True
        )
        return shutil.which("mangohud") is not None
    except (subprocess.CalledProcessError, OSError):
        return False


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <fps> <command> [args...]")
        print(f"  e.g. {sys.argv[0]} 60 factorio")
        print(f"       {sys.argv[0]} 30 ./portal.2.sh")
        sys.exit(1)

    try:
        fps = int(sys.argv[1])
    except ValueError:
        print(f"Error: fps must be a number, got '{sys.argv[1]}'")
        sys.exit(1)

    if fps <= 0:
        print(f"Error: fps must be positive, got {fps}")
        sys.exit(1)

    cmd = sys.argv[2:]
    binary = cmd[0]

    resolved = shutil.which(binary)
    if not resolved:
        if os.path.exists(binary):
            if not os.path.isfile(binary):
                print(f"Error: '{binary}' is not a file")
                sys.exit(1)
            if not os.access(binary, os.X_OK):
                print(f"Error: '{binary}' is not executable")
                sys.exit(1)
        else:
            print(f"Error: '{binary}' not found")
            sys.exit(1)

    env = os.environ.copy()

    if shutil.which("mangohud"):
        pass
    elif ensure_mangohud():
        pass
    elif shutil.which("gamescope"):
        cmd = ["gamescope", "--fps-limit", str(fps), "--"] + cmd
    else:
        env["DXVK_FRAME_RATE"] = str(fps)
        print(f"Warning: neither mangohud nor gamescope found. DXVK_FRAME_RATE={fps} set (D3D/Vulkan games only).", file=sys.stderr)
        print("Install mangohud for universal FPS limiting: sudo pacman -S mangohud lib32-mangohud", file=sys.stderr)

    if shutil.which("mangohud"):
        env["MANGOHUD"] = "1"
        env["MANGOHUD_CONFIG"] = f"fps_limit={fps},no_display"
        cmd = ["mangohud"] + cmd

    try:
        os.execve(shutil.which(cmd[0]) or cmd[0], cmd, env)
    except PermissionError as e:
        print(f"Error: permission denied executing '{cmd[0]}': {e}")
        sys.exit(1)
    except OSError as e:
        print(f"Error: failed to execute '{binary}': {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

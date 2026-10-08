#!/usr/bin/env python3
"""Serialize service startup and write one complete command to its live FIFO."""

import errno
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

SERVICE = "dusky_visualizer.service"


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"toggle", "overlay"}:
        print("Usage: visualizer_ctl.py {toggle|overlay}", file=sys.stderr)
        return 2

    config_root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    runtime_root = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    fifo = config_root / "dusky/settings/way_layers/visualizer/visualizer.ctl"
    command = sys.argv[1]
    try:
        with (runtime_root / "dusky_visualizer-client.lock").open("a") as lock:
            # A second shortcut must not unlink or race the first startup's FIFO.
            fcntl.flock(lock, fcntl.LOCK_EX)
            active = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", SERVICE],
                check=False, timeout=10,
            ).returncode == 0
            if not active:
                subprocess.run(
                    ["systemctl", "--user", "reset-failed", SERVICE],
                    check=False, stderr=subprocess.DEVNULL, timeout=10,
                )
                subprocess.run(
                    ["systemctl", "--user", "start", SERVICE],
                    check=True, timeout=10,
                )
                if command == "toggle":
                    command = "enable"

            deadline = time.monotonic() + 5
            while True:
                try:
                    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                    try:
                        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
                            raise OSError(errno.EINVAL, f"Control path is not a FIFO: {fifo}")
                        # Below PIPE_BUF: each concurrent command is atomic.
                        os.write(fd, (command + "\n").encode("ascii"))
                    finally:
                        os.close(fd)
                    return 0
                except OSError as exc:
                    if exc.errno not in {errno.ENOENT, errno.ENXIO, errno.EPIPE, errno.EAGAIN}:
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"No control FIFO reader: {fifo}") from exc
                    time.sleep(0.05)
    except (OSError, TimeoutError, subprocess.SubprocessError) as exc:
        print(f"Visualizer control failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Restart a live pre-upgrade clipboard daemon before switching its storage."""

import os
from pathlib import Path
import subprocess
import sys
import time


SERVICE = "dusky_clipboard.service"
DAEMON = Path(__file__).resolve().parents[2] / "clipboard/dusky_clipboard_daemon.sh"


def run(*args):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=10, check=False)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def service_status():
    values = dict(line.split("=", 1) for line in run(
        "systemctl", "--user", "show", SERVICE,
        "--property=LoadState,ActiveState,MainPID").splitlines() if "=" in line)
    if values.get("LoadState") != "loaded":
        raise RuntimeError(f"{SERVICE} is not installed")
    active = values.get("ActiveState")
    pid = int(values.get("MainPID", "0"))
    if active in {"inactive", "failed"} and pid == 0:
        return "stopped"
    if active != "active" or pid <= 0:
        return "pending"

    try:
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        watchers = {}
        persist = 0
        for child in children:
            args = Path(f"/proc/{child}/cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            name = Path(os.fsdecode(args[0])).name if args and args[0] else ""
            if name == "wl-clip-persist":
                persist += 1
            elif name == "wl-paste" and b"--type" in args:
                kind = os.fsdecode(args[args.index(b"--type") + 1])
                watchers[kind] = args[-3:] == [b"--watch", os.fsencode(DAEMON), b"--store"]
    except (OSError, IndexError):
        return "pending"
    if set(watchers) != {"text", "image"} or persist != 1:
        return "pending"
    return "current" if all(watchers.values()) else "legacy"


def wait_status():
    deadline = time.monotonic() + 5
    while True:
        status = service_status()
        if status != "pending":
            return status
        if time.monotonic() >= deadline:
            raise RuntimeError("Clipboard service did not become ready")
        time.sleep(0.05)


def main():
    before = wait_status()
    if before != "legacy":
        return 0
    run("systemctl", "--user", "restart", SERVICE)
    if wait_status() != "current":
        raise RuntimeError("Clipboard service still has old watchers after restart")
    print("Updated the running clipboard service")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"Clipboard upgrade failed: {exc}", file=sys.stderr)
        sys.exit(1)

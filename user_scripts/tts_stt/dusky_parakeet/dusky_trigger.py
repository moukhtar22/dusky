#!/usr/bin/env python3
"""Authoritative SOCK_SEQPACKET control client for Dusky STT."""

import argparse
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
from typing import Any


MIN_PYTHON = (3, 14, 6)
MAX_PACKET = 64 * 1024
SERVICE = "dusky_stt.service"

if sys.version_info < MIN_PYTHON:
    raise SystemExit("Dusky STT requires CPython 3.14.6 or newer")
if sys.implementation.name != "cpython" or not sys._is_gil_enabled():
    raise SystemExit("Dusky STT requires the GIL-enabled CPython 3.14 ABI")

type JsonObject = dict[str, Any]


def runtime_directory() -> Path:
    raw = os.environ.get("XDG_RUNTIME_DIR")
    if not raw:
        raise RuntimeError("XDG_RUNTIME_DIR is required")
    base = Path(raw)
    metadata = os.lstat(base)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise RuntimeError("XDG_RUNTIME_DIR has an invalid owner or type")
    return base / "dusky-stt"


def control_path() -> Path:
    return runtime_directory() / "control.sock"


def run_systemctl(*arguments: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["systemctl", "--user", *arguments],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"systemctl {' '.join(arguments)} failed: {detail}")
    return completed


def socket_is_secure(path: Path) -> bool:
    try:
        parent = os.lstat(path.parent)
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISDIR(parent.st_mode)
        or stat.S_ISLNK(parent.st_mode)
        or parent.st_uid != os.getuid()
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise RuntimeError("control socket directory is not private")
    if not stat.S_ISSOCK(metadata.st_mode):
        raise RuntimeError(f"control path is not a socket: {path}")
    if metadata.st_uid != os.getuid():
        raise RuntimeError("control socket is owned by another user")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError("control socket must have mode 0600")
    return True


def wait_for_socket(timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    path = control_path()
    while time.monotonic() < deadline:
        if socket_is_secure(path):
            return
        if run_systemctl("is-failed", "--quiet", SERVICE).returncode == 0:
            break
        time.sleep(0.1)
    status = run_systemctl("status", SERVICE, "--no-pager", "--full")
    detail = status.stdout[-4000:] or status.stderr[-1000:]
    raise TimeoutError(f"Dusky control socket did not appear\n{detail}")


def ensure_service() -> None:
    if run_systemctl("is-active", "--quiet", SERVICE).returncode != 0:
        run_systemctl("start", SERVICE, check=True)
    wait_for_socket()


def request(payload: JsonObject, *, start_service: bool = True) -> JsonObject:
    if start_service:
        ensure_service()
    path = control_path()
    if not socket_is_secure(path):
        raise RuntimeError("Dusky control socket is unavailable")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_PACKET:
        raise ValueError("control request is too large")

    connection = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC)
    connection.settimeout(10)
    try:
        connection.connect(str(path))
        sent = connection.send(encoded)
        if sent != len(encoded):
            raise OSError(f"short control request send: {sent}/{len(encoded)}")
        packet, _ancillary, flags, _address = connection.recvmsg(MAX_PACKET)
        if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise RuntimeError("truncated control response")
    finally:
        connection.close()
    if not packet:
        raise RuntimeError("daemon closed the control connection without a response")
    response = json.loads(packet.decode("utf-8"))
    if not isinstance(response, dict):
        raise RuntimeError("daemon returned an invalid response")
    return response


def print_response(response: JsonObject, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(response, ensure_ascii=False, sort_keys=True))
        return 0 if response.get("ok") else 1
    if not response.get("ok"):
        print(f"Dusky STT: {response.get('error', 'request failed')}", file=sys.stderr)
        return 1
    for key in (
        "state",
        "daemon_pid",
        "daemon_rss_kib",
        "worker_pid",
        "worker_inflight",
        "backend",
        "model",
        "quantization",
        "file",
        "session_id",
    ):
        if key in response:
            print(f"{key}: {response[key]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Control Dusky STT")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--start", action="store_true", help="start recording")
    action.add_argument("--stop", action="store_true", help="stop and finalize recording")
    action.add_argument("--status", action="store_true", help="show daemon state")
    action.add_argument("--file", type=Path, help="transcribe a media file")
    action.add_argument("--restart", action="store_true", help="restart the user service")
    action.add_argument("--kill", action="store_true", help="stop the user service")
    action.add_argument("--logs", action="store_true", help="follow the service journal")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--realtime", action="store_true", help="type stable words with wtype")
    mode.add_argument("--push", action="store_true", help="capture and type once at the end")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable response")
    args = parser.parse_args()

    if args.logs:
        os.execvp(
            "journalctl",
            ["journalctl", "--user", "-u", SERVICE, "-n", "100", "-f", "-o", "short-precise"],
        )
    if args.restart:
        run_systemctl("restart", SERVICE, check=True)
        wait_for_socket()
        return print_response(request({"command": "status"}, start_service=False), as_json=args.json)
    if args.kill:
        run_systemctl("stop", SERVICE, check=True)
        if args.json:
            print('{"ok":true,"state":"stopped"}')
        else:
            print("Dusky STT service stopped")
        return 0
    if args.status:
        try:
            response = request({"command": "status"}, start_service=False)
        except (OSError, RuntimeError, TimeoutError):
            active = run_systemctl("is-active", SERVICE)
            state = active.stdout.strip() or "inactive"
            response = {"ok": active.returncode == 0, "state": state}
        return print_response(response, as_json=args.json)
    if args.file is not None:
        source = args.file.expanduser().resolve()
        if not source.is_file():
            print(f"File not found: {source}", file=sys.stderr)
            return 2
        return print_response(
            request({"command": "file", "path": str(source)}),
            as_json=args.json,
        )

    realtime = not args.push
    if args.start:
        command = "start"
    elif args.stop:
        command = "stop"
    else:
        command = "toggle"
    return print_response(
        request({"command": command, "realtime": realtime}),
        as_json=args.json,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Dusky STT control error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

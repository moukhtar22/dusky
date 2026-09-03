#!/usr/bin/env python3
"""Control client for Dusky STT (stdlib only).

Defaults to toggling realtime dictation with zero arguments (hotkey friendly).
Validates socket ownership/modes before connecting; never trusts permissions alone.
"""

import argparse
import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SERVICE = "dusky_stt.service"
MAX_PACKET = 65536
DEFAULT_TIMEOUT = 10.0
WAIT_DEADLINE_S = 4 * 3600  # --wait cap for very long files (Ctrl-C aborts client only)

type JsonObject = dict[str, Any]


def app_config_path() -> Path:
    return Path.home() / ".local" / "lib" / "dusky-stt" / "config.json"


def transcripts_dir() -> Path:
    try:
        cfg = json.loads(app_config_path().read_text(encoding="utf-8"))
        state = str(cfg.get("state_dir", "~/.local/state/dusky-stt"))
    except (OSError, ValueError):
        state = "~/.local/state/dusky-stt"
    return Path(state).expanduser() / "transcripts"


def newest_transcript(after: float | None = None) -> Path | None:
    d = transcripts_dir()
    try:
        cands = [p for p in d.glob("capture-*.txt") if p.is_file()]
    except OSError:
        return None
    if after is not None:
        cands = [p for p in cands if p.stat().st_mtime > after]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def control_path() -> Path:
    rt = os.environ.get("XDG_RUNTIME_DIR")
    if not rt:
        raise RuntimeError("XDG_RUNTIME_DIR is unset.")
    return Path(rt) / "dusky-stt" / "control.sock"


def is_socket_secure(p: Path) -> bool:
    try:
        d_st = p.parent.lstat()
        f_st = p.lstat()
    except OSError:
        return False
    return (d_st.st_uid == os.getuid() and stat.S_IMODE(d_st.st_mode) == 0o700
            and stat.S_ISSOCK(f_st.st_mode) and f_st.st_uid == os.getuid()
            and stat.S_IMODE(f_st.st_mode) == 0o600)


def ensure_service() -> None:
    if is_socket_secure(control_path()):
        return
    subprocess.run(["systemctl", "--user", "start", SERVICE], check=False)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if is_socket_secure(control_path()):
            return
        if subprocess.run(["systemctl", "--user", "is-failed", "--quiet", SERVICE], check=False).returncode == 0:
            break
        time.sleep(0.1)
    raise TimeoutError("Dusky STT socket did not appear; check `dusky_trigger --logs`.")


def send_command(payload: JsonObject, timeout: float = DEFAULT_TIMEOUT) -> JsonObject:
    ensure_service()
    p = control_path()
    if not is_socket_secure(p):
        raise RuntimeError(f"Control socket missing/insecure: {p}")
    blob = json.dumps(payload).encode()
    if len(blob) > MAX_PACKET:
        raise ValueError("Request too large for SEQPACKET")
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC) as s:
        s.settimeout(timeout)
        s.connect(str(p))
        s.sendmsg([blob])
        data, _, flags, _ = s.recvmsg(MAX_PACKET)
        if flags & getattr(socket, "MSG_TRUNC", 0x20):
            raise RuntimeError("Response truncated")
        if not data:
            raise RuntimeError("Daemon closed connection")
    return json.loads(data.decode())


def wait_for_transcript(baseline: float) -> JsonObject:
    """Poll until the daemon returns to idle, then print the transcript.

    Ctrl-C aborts only this client; the daemon keeps transcribing.
    In on-demand mode the service stops itself after the job: a dead
    socket then counts as done, and the transcript file is authoritative.
    """
    deadline = time.monotonic() + WAIT_DEADLINE_S
    missed = 0
    try:
        while time.monotonic() < deadline:
            try:
                st = send_command({"command": "status"}, timeout=DEFAULT_TIMEOUT)
                missed = 0
            except (OSError, ValueError, TimeoutError):
                st = None
                missed += 1
            if st is None:
                # Daemon unreachable: either still booting (keep waiting) or
                # self-stopped after finishing (transcript decides below).
                # A crash mid-job looks the same, so give up after ~30 s of
                # continuous silence with no transcript to show for it.
                if newest_transcript(after=baseline) is not None:
                    break
                if missed >= 15:
                    return {"ok": False, "error": "daemon unreachable for 30s (crashed mid-job?)"}
                time.sleep(2.0)
                continue
            if st.get("state", "idle") == "idle":
                break
            time.sleep(2.0)
        else:
            return {"ok": False, "error": f"timed out after {WAIT_DEADLINE_S // 3600}h waiting for transcription"}
    except KeyboardInterrupt:
        return {"ok": False, "error": "wait interrupted (transcription continues in background)"}
    path = newest_transcript(after=baseline)
    if path is None:
        return {"ok": False, "error": "transcription finished but no transcript file appeared"}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"cannot read {path}: {exc}"}
    print(text, end="" if text.endswith("\n") else "\n")
    return {"ok": True, "event": "transcribed", "path": str(path),
            "chars": len(text), "words": len(text.split())}


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="dusky_trigger",
        description="Control client for Dusky STT (Parakeet speech-to-text).",
        epilog="""USAGE
  dusky_trigger                         toggle realtime dictation (bind this to a hotkey)
  dusky_trigger --file ~/audio.m4a        transcribe an audio/video file
  dusky_trigger --file ~/ep.mp3 --wait    transcribe and print the transcript when done
  dusky_trigger --ACTION

ACTIONS
  (none) / --toggle   start if idle, else stop and finalize
                      (tap again mid-drain to chain a fresh take; --stop cancels)
  --start [--realtime|--push]   begin capture (realtime live-types as you speak)
  --stop              stop capture and finalize (waits for the last phrase)
  --pause             pause / resume capture (keeps the session)
  --status            daemon status
  --file PATH         transcribe any ffmpeg-readable file (2 h+ supported)
  --wait              with --file: block until idle, then print the transcript
  --unload            free GPU VRAM / RAM now (worker exits; respawns on demand)
  --restart           restart the service
  --kill              stop the service
  --logs              follow the daemon log

POWER MODES (follow the systemd unit)
  enabled   warm-resident: instant dictation, VRAM held, dGPU awake
  disabled  on-demand: hotkey still works, VRAM mid-job only, auto-offload after

HOTKEY EXAMPLES
  hyprland:  bind = SUPER, S, exec, dusky_trigger
  sway:      bindsym $mod+s exec dusky_trigger""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--start", action="store_true")
    g.add_argument("--stop", action="store_true")
    g.add_argument("--pause", action="store_true", help="Pause / resume the current capture")
    g.add_argument("--toggle", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--file", type=Path, default=None, help="Transcribe audio/video file")
    g.add_argument("--unload", action="store_true", help="Unload the ASR worker now (free VRAM/RAM)")
    g.add_argument("--restart", action="store_true")
    g.add_argument("--kill", action="store_true")
    g.add_argument("--logs", action="store_true")
    m = ap.add_mutually_exclusive_group()
    m.add_argument("--realtime", action="store_true", default=False)
    m.add_argument("--push", action="store_true", default=False)
    ap.add_argument("--wait", action="store_true",
                    help="With --file: wait for completion, then print the transcript to stdout")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = ap.parse_args()

    if args.wait and args.file is None:
        print("--wait needs --file", file=sys.stderr)
        return 2

    if args.logs:
        os.execvp("journalctl", ["journalctl", "--user", "-u", SERVICE, "-f", "-o", "short-precise"])
    if args.restart:
        subprocess.run(["systemctl", "--user", "restart", SERVICE], check=True)
        return 0
    if args.kill:
        subprocess.run(["systemctl", "--user", "stop", SERVICE], check=True)
        return 0

    mode = "push" if args.push else "realtime"
    if args.status:
        resp = send_command({"command": "status"}, timeout=args.timeout)
    elif args.start:
        resp = send_command({"command": "start", "mode": mode}, timeout=args.timeout)
    elif args.stop:
        resp = send_command({"command": "stop"}, timeout=max(args.timeout, 180.0))
    elif args.pause:
        resp = send_command({"command": "pause"}, timeout=args.timeout)
    elif args.file is not None:
        src = args.file.expanduser()
        if not src.is_file():
            print(f"File not found: {src}", file=sys.stderr)
            return 2
        baseline = time.time()
        resp = send_command({"command": "file", "path": str(src.resolve())}, timeout=max(args.timeout, 300.0))
        if args.wait and resp.get("ok"):
            resp = wait_for_transcript(baseline)
    elif args.unload:
        resp = send_command({"command": "unload"}, timeout=args.timeout)
    else:  # default: toggle (bare hotkey invocation)
        resp = send_command({"command": "toggle", "mode": mode}, timeout=max(args.timeout, 180.0))

    if args.wait and resp.get("ok") and "path" in resp:
        # Transcript body already went to stdout; keep it script-clean by
        # sending the metadata to stderr.
        print(f"transcribed: {resp['path']} ({resp.get('words', 0)} words)", file=sys.stderr)
        return 0
    if args.json:
        print(json.dumps(resp, indent=2))
    else:
        for k, v in resp.items():
            print(f"{k:15}: {v}")
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

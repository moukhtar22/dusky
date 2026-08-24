#!/usr/bin/env python3
"""
Dusky Notification Timestamp Tracking Daemon
Standalone background service that tracks exact arrival timestamps for Mako desktop notifications
and caches them atomically to $XDG_RUNTIME_DIR/dusky_notif_times.json.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Global exit signal flag
_RUNNING = True

def _signal_handler(signum: int, frame: Any) -> None:
    global _RUNNING
    _RUNNING = False

def get_cache_file() -> Path:
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    base_dir = Path(xdg_runtime) if xdg_runtime else Path(tempfile.gettempdir())
    return base_dir / "dusky_notif_times.json"

def atomic_write_json(path: Path, data: Any) -> None:
    """Safely write JSON data using atomic file replacement to prevent readers from seeing partial files."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        pass

def fetch_mako_notification_ids() -> set[int]:
    """Fetch active and history notification IDs from Mako without subprocess bloat."""
    ids = set()
    for cmd in (["makoctl", "list", "-j"], ["makoctl", "history", "-j"]):
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
            if res.returncode == 0 and res.stdout:
                parsed = json.loads(res.stdout)
                items = parsed
                if isinstance(parsed, dict) and "data" in parsed:
                    data = parsed["data"]
                    if data and isinstance(data, list):
                        items = data[0] if (data and isinstance(data[0], list)) else data
                elif isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], list):
                    items = parsed[0]

                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and "id" in item:
                            try: ids.add(int(item["id"]))
                            except (ValueError, TypeError): pass
        except Exception:
            pass
    return ids

def main() -> None:
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    cache_file = get_cache_file()
    cached_timestamps: dict[str, str] = {}

    # Load existing cache file on startup
    if cache_file.is_file():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        cached_timestamps[str(k)] = str(v)
        except Exception:
            pass

    while _RUNNING:
        try:
            current_ids = fetch_mako_notification_ids()
            if current_ids:
                now_str = datetime.now().strftime("%I:%M %p").lstrip("0")
                changed = False

                for nid in current_ids:
                    str_id = str(nid)
                    if str_id not in cached_timestamps:
                        cached_timestamps[str_id] = now_str
                        changed = True

                # Bound cache size to prevent memory or disk growth
                if len(cached_timestamps) > 500:
                    excess = len(cached_timestamps) - 500
                    for old_key in list(cached_timestamps.keys())[:excess]:
                        del cached_timestamps[old_key]
                    changed = True

                if changed:
                    atomic_write_json(cache_file, cached_timestamps)
        except Exception:
            pass

        # Interruptible sleep interval
        for _ in range(20):
            if not _RUNNING: break
            time.sleep(0.1)

if __name__ == "__main__":
    main()

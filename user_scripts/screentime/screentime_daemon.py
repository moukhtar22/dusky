#!/usr/bin/env python3
"""
===============================================================================
DUSKY SCREENTIME: BACKGROUND DAEMON (Python 3.14 Bleeding-Edge)
===============================================================================
Zero-fork, high-performance Wayland screentime tracking daemon.
Connects directly to Hyprland UNIX domain sockets to monitor active windows,
resolves applications via `DesktopResolver` (matching Rofi behavior), and
atomically persists daily metrics to `~/.local/share/dusky/screentime/screentime_data.json`.
"""

import json
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure local imports work regardless of working directory
SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from desktop_resolver import AppInfo, DesktopResolver
except ImportError:
    from python.desktop_resolver import AppInfo, DesktopResolver


DATA_DIR = Path("~/.local/share/dusky/screentime").expanduser()
DATA_FILE = DATA_DIR / "screentime_data.json"
CONFIG_DIR = Path("~/.config/dusky/settings/screentime").expanduser()
CONFIG_FILE = CONFIG_DIR / "screentime.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "save_interval_seconds": 5,
    "idle_threshold_seconds": 300,
    "ignore_classes": ["hyprlock", "swaylock", "gdm", "sddm"],
}

LOCKSCREEN_NAMES: set[str] = {
    "hyprlock",
    "swaylock",
    "swaylock-effects",
    "i3lock",
    "gtklock",
    "waylock",
}


class ScreentimeDaemon:
    def __init__(self) -> None:
        self.running: bool = False
        self.config: dict[str, Any] = DEFAULT_CONFIG.copy()
        self.data: dict[str, dict[str, Any]] = {}
        self.resolver: DesktopResolver = DesktopResolver()
        self.last_save_time: float = time.time()
        self.last_active_time: float = time.time()
        self.last_window_key: str = ""
        self.last_window_title: str = ""
        self.lock: threading.Lock = threading.Lock()
        self._cached_socket_path: Path | None = None
        self._socket_check_time: float = 0.0
        self._dbus_bus: Any | None = None

        self._ensure_directories()
        self._load_config()
        self._load_data()

    def _ensure_directories(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> None:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    user_conf = json.load(f)
                    for k, v in user_conf.items():
                        if k in self.config:
                            self.config[k] = v
            except Exception as e:
                print(f"[!] Error loading config: {e}", file=sys.stderr)
        else:
            self._save_config()

    def _save_config(self) -> None:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
        except Exception:
            pass

    def _load_data(self) -> None:
        if DATA_FILE.exists():
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"[!] Error loading data file: {e}", file=sys.stderr)
                self.data = {}
        else:
            self.data = {}

    def _save_data_atomic(self) -> None:
        with self.lock:
            try:
                temp_file = DATA_FILE.with_suffix(".json.tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=2)
                os.replace(temp_file, DATA_FILE)
                self.last_save_time = time.time()
            except Exception as e:
                print(f"[!] Error saving data: {e}", file=sys.stderr)

    def _discover_socket_path(self) -> Path | None:
        """
        Discover active Hyprland `.socket.sock` path dynamically.
        """
        now = time.time()
        if self._cached_socket_path and self._cached_socket_path.exists():
            return self._cached_socket_path

        # Only re-scan every 2.0 seconds to prevent unnecessary I/O
        if now - self._socket_check_time < 2.0:
            return None
        self._socket_check_time = now

        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")

        if xdg_runtime and sig:
            p = Path(xdg_runtime) / "hypr" / sig / ".socket.sock"
            if p.exists():
                self._cached_socket_path = p
                return p

        base_dirs: list[Path] = []
        if xdg_runtime:
            base_dirs.append(Path(xdg_runtime) / "hypr")
        base_dirs.append(Path("/tmp/hypr"))

        candidates: list[tuple[float, Path]] = []
        for bd in base_dirs:
            if bd.exists() and bd.is_dir():
                for sdir in bd.iterdir():
                    if sdir.is_dir():
                        sock = sdir / ".socket.sock"
                        if sock.exists():
                            try:
                                mtime = sock.stat().st_mtime
                                candidates.append((mtime, sock))
                            except OSError:
                                pass

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            self._cached_socket_path = candidates[0][1]
            return self._cached_socket_path

        return None

    def _hypr_query_socket(self, cmd: str) -> str | None:
        """
        Send a query to the Hyprland UNIX socket (`.socket.sock`) with zero subprocess forks.
        """
        socket_path = self._discover_socket_path()
        if not socket_path:
            return None

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(str(socket_path))
                s.sendall(cmd.encode("utf-8"))
                response = bytearray()
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
                return response.decode("utf-8", errors="ignore")
        except Exception:
            self._cached_socket_path = None
            return None

    def get_active_window(self) -> dict[str, Any] | None:
        """
        Retrieve active window metadata (`class`, `title`, `pid`) via socket.
        """
        raw = self._hypr_query_socket("j/activewindow")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("class"):
                return data
        except Exception:
            pass
        return None

    def is_dpms_off(self) -> bool:
        """
        Check if monitors are sleeping/off (`dpmsStatus` is false for all).
        """
        raw = self._hypr_query_socket("j/monitors")
        if not raw:
            return False
        try:
            monitors = json.loads(raw)
            if isinstance(monitors, list) and monitors:
                for m in monitors:
                    if m.get("dpmsStatus", True):
                        return False
                return True
        except Exception:
            pass
        return False

    def is_locked(self) -> bool:
        """
        Check if lockscreen process (`hyprlock` or `swaylock`) is active.
        Optimized procfs scanning.
        """
        try:
            with os.scandir("/proc") as it:
                for entry in it:
                    if entry.name.isdigit() and entry.is_dir():
                        try:
                            with open(f"/proc/{entry.name}/comm", "r", encoding="utf-8") as f:
                                comm = f.read().strip()
                                if comm in LOCKSCREEN_NAMES:
                                    return True
                        except OSError:
                            continue
        except Exception:
            pass
        return False

    def is_dbus_idle(self) -> bool:
        """
        Query systemd logind for session idle status with bus instance caching.
        """
        try:
            import dbus

            if self._dbus_bus is None:
                self._dbus_bus = dbus.SystemBus()

            logind = self._dbus_bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1")
            sessions = logind.ListSessions(dbus_interface="org.freedesktop.login1.Manager")
            for s_info in sessions:
                s_path = s_info[4]
                sess_obj = self._dbus_bus.get_object("org.freedesktop.login1", s_path)
                props = dbus.Interface(sess_obj, "org.freedesktop.DBus.Properties")
                idle_hint = props.Get("org.freedesktop.login1.Session", "IdleHint")
                if idle_hint:
                    return True
        except Exception:
            self._dbus_bus = None
        return False

    def _record_tick(self, win: dict[str, Any]) -> None:
        cls = win.get("class", "").strip()
        if not cls or cls.lower() in self.config.get("ignore_classes", []):
            return

        title = win.get("title", "").strip() or cls
        today = datetime.now().strftime("%Y-%m-%d")

        with self.lock:
            if today not in self.data:
                self.data[today] = {}

            info = self.resolver.resolve(cls, title)

            if cls not in self.data[today]:
                self.data[today][cls] = {
                    "name": info.name,
                    "category": info.category,
                    "icon": info.icon,
                    "duration": 0,
                    "first_seen": int(time.time()),
                    "last_active": int(time.time()),
                    "sessions": 1,
                    "titles": {},
                }
            else:
                rec = self.data[today][cls]
                rec["name"] = info.name
                rec["category"] = info.category
                rec["icon"] = info.icon
                rec["last_active"] = int(time.time())

            rec = self.data[today][cls]
            rec["duration"] += 1

            if title:
                if title not in rec["titles"] and len(rec["titles"]) >= 50:
                    title = "Other / Miscellaneous"
                rec["titles"][title] = rec["titles"].get(title, 0) + 1

            # Check for new focus session
            if cls != self.last_window_key:
                rec["sessions"] = rec.get("sessions", 0) + 1
                self.last_window_key = cls
                self.last_window_title = title
            elif title != self.last_window_title:
                self.last_window_title = title

    def run(self) -> None:
        self.running = True
        print("[*] Dusky Screentime Daemon started.")

        while self.running:
            start_t = time.time()

            if self.config.get("enabled", True):
                locked = self.is_locked()
                dpms_off = self.is_dpms_off()
                dbus_idle = self.is_dbus_idle()

                if not locked and not dpms_off and not dbus_idle:
                    win = self.get_active_window()
                    if win and win.get("class"):
                        current_title = win.get("title", "").strip()
                        current_class = win.get("class", "").strip()

                        # Update last active time when active window or title changes
                        if current_class != self.last_window_key or current_title != self.last_window_title:
                            self.last_active_time = time.time()

                        idle_thresh = self.config.get("idle_threshold_seconds", 300)
                        if time.time() - self.last_active_time <= idle_thresh:
                            self._record_tick(win)
                        else:
                            self.last_window_key = ""
                    else:
                        self.last_window_key = ""
                else:
                    self.last_window_key = ""

            # Periodic saving
            if time.time() - self.last_save_time >= self.config.get(
                "save_interval_seconds", 5
            ):
                self._save_data_atomic()

            # Maintain exactly 1.0 second loop interval
            elapsed = time.time() - start_t
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)

    def stop(self, *args: Any) -> None:
        print("[*] Stopping Dusky Screentime Daemon...")
        self.running = False
        self._save_data_atomic()
        sys.exit(0)


if __name__ == "__main__":
    daemon = ScreentimeDaemon()
    signal.signal(signal.SIGINT, daemon.stop)
    signal.signal(signal.SIGTERM, daemon.stop)
    daemon.run()

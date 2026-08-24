#!/usr/bin/env python3
# ==============================================================================
#  DUSKY MUSIC RECOGNITION (v2.0.0)
#  Bleeding-edge Shazam / SongRec audio identification engine with Rich UI,
#  bottom-center Mako notifications, and persistent state history.
# ==============================================================================

from __future__ import annotations

import argparse
import atexit
import csv
import dataclasses
import fcntl
import html
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import time
import tty
import urllib.error
import urllib.parse

import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --- Python Version Enforcement (Bleeding-Edge 3.12+) ---
if sys.version_info < (3, 12):
    sys.stderr.write("[FATAL] Python 3.12+ required for Dusky Music Recognition.\n")
    sys.exit(1)

# Rich library imports
try:
    from rich import box
    from rich.align import Align
    from rich.console import Console, Group
    from rich.live import Live
    from rich.markup import escape
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme

except ImportError:
    # Graceful fallback warning if rich is missing (pacman -S python-rich)
    sys.stderr.write("[ERROR] python-rich is required. Install via: sudo pacman -S python-rich\n")
    sys.exit(1)

VERSION = "2.0.0"


# ==============================================================================
#  MATUGEN THEME INTEGRATION (DUSKY DESIGN SYSTEM)
# ==============================================================================
@dataclass
class MatugenTheme:
    """Load dynamic color tokens from Matugen generated theme."""
    bg: str = "#11140f"
    fg: str = "#e1e4da"
    accent: str = "#a5d395"
    error: str = "#ffb4ab"
    warning: str = "#bbcbb2"
    success: str = "#a0cfd2"
    muted: str = "#43483f"

    @classmethod
    def load(cls) -> "MatugenTheme":
        inst = cls()
        theme_file = Path.home() / ".config/matugen/generated/dusky_tui.json"
        if theme_file.is_file():
            try:
                with open(theme_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    inst.bg = data.get("bg", inst.bg)
                    inst.fg = data.get("fg", inst.fg)
                    inst.accent = data.get("accent", inst.accent)
                    inst.error = data.get("error", inst.error)
                    inst.warning = data.get("warning", inst.warning)
                    inst.success = data.get("success", inst.success)
                    inst.muted = data.get("muted", inst.muted)
            except Exception:
                pass
        return inst

    def to_rich_theme(self) -> Theme:
        return Theme({
            "accent": f"bold {self.accent}",
            "accent_plain": self.accent,
            "fg": self.fg,
            "muted": self.muted,
            "warning": self.warning,
            "success": self.success,
            "error": self.error,
            "border": self.muted,
            "header": f"bold {self.accent}",
            "link": self.accent,
        })

    def get_fzf_color_opt(self) -> str:
        """Construct fzf --color option using exact Matugen tokens from terminal_clipboard.sh."""
        return (
            f"bg+:{self.muted},bg:{self.bg},spinner:{self.accent},fg:{self.fg},fg+:{self.fg},"
            f"header:{self.accent},info:{self.warning},pointer:{self.success},marker:{self.success},"
            f"prompt:{self.accent},hl:{self.accent},hl+:{self.accent},border:{self.muted},label:{self.accent}"
        )


GLOBAL_THEME = MatugenTheme.load()
console = Console(theme=GLOBAL_THEME.to_rich_theme(), highlight=False)




# Equalizer animation frames
EQ_FRAMES = [
    " ▂▃▅▆▇▆▅▃ ",
    "▂▃▅▆▇█▇▆▅▃",
    "▃▅▆▇█▇▆▅▃▂",
    "▅▆▇█▇▆▅▃▂ ",
    "▆▇█▇▆▅▃▂ ▂",
    "▇█▇▆▅▃▂ ▂▃",
    "█▇▆▅▃▂ ▂▃▅",
    "▇▆▅▃▂ ▂▃▅▆",
    "▆▅▃▂ ▂▃▅▆▇",
    "▅▃▂ ▂▃▅▆▇█",
    "▃▂ ▂▃▅▆▇█▇",
    "▂ ▂▃▅▆▇█▇▆",
]


# ==============================================================================
#  STATE DIRECTORY & CONFIGURATION
# ==============================================================================
def resolve_config_dir() -> Path:
    """Resolve base config path dynamically without hardcoding usernames."""
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home and os.path.isabs(config_home):
        base = Path(config_home)
    else:
        base = Path.home() / ".config"
    target = base / "dusky" / "settings" / "music_recognition"
    target.mkdir(parents=True, exist_ok=True)
    (target / "covers").mkdir(parents=True, exist_ok=True)
    return target


STATE_DIR: Path = resolve_config_dir()
HISTORY_FILE: Path = STATE_DIR / "history.json"
CONFIG_FILE: Path = STATE_DIR / "config.json"
COVERS_DIR: Path = STATE_DIR / "covers"
LOCK_FILE: Path = STATE_DIR / "music_recognition.lock"
LOG_FILE: Path = Path("/tmp/dusky_music_recognition.log")


@dataclass(slots=True)
class AppConfig:
    record_duration: int = 5
    timeout: int = 30
    notifications: bool = True
    auto_copy: bool = False
    default_source: str = "system"  # "system" or "mic"
    max_history: int = 500
    download_covers: bool = True

    @classmethod
    def load(cls) -> AppConfig:
        if not CONFIG_FILE.is_file():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()

    def save(self) -> None:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except Exception as e:
            log_error(f"Failed to save config: {e}")


# ==============================================================================
#  SONG METADATA MODEL
# ==============================================================================
@dataclass(slots=True)
class SongMetadata:
    id: str
    title: str
    artist: str
    album: str = ""
    release_year: str = ""
    genres: list[str] = field(default_factory=list)
    cover_url: str = ""
    local_cover_path: str = ""
    shazam_url: str = ""
    apple_music_url: str = ""
    spotify_url: str = ""
    youtube_search_url: str = ""
    lyrics: list[str] = field(default_factory=list)
    timestamp: str = ""
    epoch: float = 0.0
    source: str = "system"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SongMetadata:
        valid_keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in valid_keys})


# ==============================================================================
#  LOGGING & UTILITIES
# ==============================================================================
def log_info(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] INFO: {msg}\n")
    except Exception:
        pass


def log_error(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] ERROR: {msg}\n")
    except Exception:
        pass


def copy_to_clipboard(text: str) -> bool:
    """Copy text to Wayland clipboard using wl-copy."""
    if shutil.which("wl-copy"):
        try:
            subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            pass
    return False


# ==============================================================================
#  SINGLETON PROCESS LOCK
# ==============================================================================
class ProcessLock:
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd: int | None = None

    def acquire(self) -> bool:
        try:
            self._fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.write(self._fd, f"{os.getpid()}\n".encode("utf-8"))
            return True
        except (BlockingIOError, OSError):
            return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None


# ==============================================================================
#  PERSISTENT HISTORY STORE
# ==============================================================================
class HistoryStore:
    def __init__(self, history_path: Path = HISTORY_FILE, max_records: int = 500):
        self.history_path = history_path
        self.lock_path = history_path.with_suffix(".lock")
        self.max_records = max_records
        self.history_path.parent.mkdir(parents=True, exist_ok=True)


    def _atomic_modify(self, modifier: Callable[[list[SongMetadata]], list[SongMetadata]]) -> None:
        lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            items = []
            if self.history_path.is_file():
                try:
                    with open(self.history_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            items = [SongMetadata.from_dict(item) for item in data if isinstance(item, dict)]
                except Exception as e:
                    log_error(f"Error reading history in atomic modify: {e}")

            modified_items = modifier(items)

            tmp_file = self.history_path.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump([item.to_dict() for item in modified_items], f, indent=2, ensure_ascii=False)
            tmp_file.replace(self.history_path)
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            except Exception:
                pass

    def add(self, song: SongMetadata) -> None:
        def _modify(items: list[SongMetadata]) -> list[SongMetadata]:
            if items and items[0].title.lower() == song.title.lower() and items[0].artist.lower() == song.artist.lower():
                items[0] = song
            else:
                items.insert(0, song)
            if len(items) > self.max_records:
                items = items[: self.max_records]
            return items

        self._atomic_modify(_modify)

    def clear(self) -> None:
        self._atomic_modify(lambda _: [])

    def get_all(self, query: str | None = None, limit: int | None = None) -> list[SongMetadata]:
        lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_SH)
            items = []
            if self.history_path.is_file():
                try:
                    with open(self.history_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            items = [SongMetadata.from_dict(item) for item in data if isinstance(item, dict)]
                except Exception as e:
                    log_error(f"Failed to read history: {e}")
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            except Exception:
                pass

        if query:
            q = query.lower()
            items = [
                s for s in items
                if q in s.title.lower()
                or q in s.artist.lower()
                or q in s.album.lower()
                or any(q in g.lower() for g in s.genres)
                or any(q in lyr.lower() for lyr in s.lyrics)
            ]
        if limit and limit > 0:
            items = items[:limit]
        return items

    def get_latest(self) -> SongMetadata | None:
        items = self.get_all(limit=1)
        return items[0] if items else None

    def export(self, export_path: Path, fmt: str = "json") -> bool:
        items = self.get_all()
        try:
            export_path.parent.mkdir(parents=True, exist_ok=True)
            match fmt.lower():
                case "json":
                    with open(export_path, "w", encoding="utf-8") as f:
                        json.dump([item.to_dict() for item in items], f, indent=2, ensure_ascii=False)
                case "csv":
                    with open(export_path, "w", encoding="utf-8", newline="") as f:
                        writer = csv.writer(f)
                        writer.writerow(["Timestamp", "Title", "Artist", "Album", "Year", "Genres", "Shazam URL", "Spotify URL", "YouTube URL"])
                        for s in items:
                            writer.writerow([
                                s.timestamp, s.title, s.artist, s.album, s.release_year,
                                "; ".join(s.genres), s.shazam_url, s.spotify_url, s.youtube_search_url
                            ])
                case "md" | "markdown":
                    with open(export_path, "w", encoding="utf-8") as f:
                        f.write("# Music Recognition History\n\n")
                        f.write("| Date / Time | Title | Artist | Album | Links |\n")
                        f.write("| :--- | :--- | :--- | :--- | :--- |\n")
                        for s in items:
                            links = []
                            if s.shazam_url: links.append(f"[Shazam]({s.shazam_url})")
                            if s.spotify_url: links.append(f"[Spotify]({s.spotify_url})")
                            if s.youtube_search_url: links.append(f"[YouTube]({s.youtube_search_url})")
                            f.write(f"| {s.timestamp} | **{s.title}** | {s.artist} | {s.album} | {' • '.join(links)} |\n")
                case _:
                    return False
            return True
        except Exception as e:
            log_error(f"Failed to export history: {e}")
            return False



# ==============================================================================
#  DESKTOP NOTIFICATIONS (MAKO INTEGRATION)
# ==============================================================================
class Notifier:
    APP_NAME = "dusky-music-recognition"
    SYNC_HINT = "string:x-canonical-private-synchronous:dusky-music"

    @classmethod
    def notify_listening(cls, source_name: str = "System Audio") -> None:
        if not shutil.which("notify-send"):
            return
        cmd = [
            "notify-send",
            "-a", cls.APP_NAME,
            "-u", "low",
            "-t", "4000",
            "-h", cls.SYNC_HINT,
            "Music Recognition",
            f"Listening to {source_name}...",
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    @classmethod
    def notify_detected(cls, song: SongMetadata) -> None:
        if not shutil.which("notify-send"):
            return
        
        # Build subtitle with album and year
        details = html.escape(song.artist)
        if song.album:
            details += f" • {html.escape(song.album)}"
        if song.release_year:
            details += f" ({html.escape(song.release_year)})"

        cmd = [
            "notify-send",
            "-a", cls.APP_NAME,
            "-u", "normal",
            "-t", "8000",
            "-h", cls.SYNC_HINT,
        ]
        if song.local_cover_path and os.path.isfile(song.local_cover_path):
            cmd.extend(["-i", song.local_cover_path])

        cmd.extend([song.title, details])
        
        youtube_url = song.youtube_search_url or song.shazam_url
        spotify_url = song.spotify_url

        if youtube_url or spotify_url:
            if youtube_url:
                cmd.append("--action=default=Open on YouTube")
            if spotify_url:
                cmd.append("--action=spotify=Open on Spotify")

            helper_script = f"""
import subprocess
proc = subprocess.run({cmd!r}, capture_output=True, text=True)
action = proc.stdout.strip()
if action == 'default' and {bool(youtube_url)}:
    subprocess.Popen(['xdg-open', {youtube_url!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
elif action == 'spotify' and {bool(spotify_url)}:
    subprocess.Popen(['xdg-open', {spotify_url!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
"""
            try:
                subprocess.Popen(
                    [sys.executable, "-c", helper_script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True
                )
            except Exception as e:
                log_error(f"Notification listener spawn error: {e}")
        else:
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                log_error(f"Notification error: {e}")



    @classmethod
    def notify_no_match(cls, elapsed_secs: int) -> None:
        if not shutil.which("notify-send"):
            return
        cmd = [
            "notify-send",
            "-a", cls.APP_NAME,
            "-u", "low",
            "-t", "3500",
            "-h", cls.SYNC_HINT,
            "Music Recognition",
            f"No match found ({elapsed_secs}s elapsed)",
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass




# ==============================================================================
#  COVER ART DOWNLOADER
# ==============================================================================
def download_cover_art(url: str, song_id: str) -> str:
    """Download cover art image and save to covers directory."""
    if not url:
        return ""
    safe_id = re.sub(r"[^\w\-]", "_", song_id)
    target_path = COVERS_DIR / f"{safe_id}.jpg"
    if target_path.is_file() and target_path.stat().st_size > 500:
        return str(target_path)

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; Dusky-Music/2.0)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as response:
            if response.status == 200:
                with open(target_path, "wb") as f:
                    f.write(response.read())
                return str(target_path)
    except Exception as e:
        log_error(f"Failed to download cover art ({url}): {e}")
    return ""


# ==============================================================================
#  AUDIO CAPTURE ENGINE
# ==============================================================================
class AudioEngine:
    @staticmethod
    def get_default_monitor_source() -> str:
        """Fetch PipeWire default playback sink name."""
        # 1. Native WirePlumber metadata
        if shutil.which("pw-dump"):
            try:
                out = subprocess.check_output(["pw-dump", "Metadata"], text=True, stderr=subprocess.DEVNULL)
                for obj in json.loads(out):
                    if obj.get("props", {}).get("metadata.name") == "default":
                        for item in obj.get("metadata", []):
                            if item.get("key") in ("default.audio.sink", "default.configured.audio.sink"):
                                val = item.get("value")
                                name = val.get("name") if isinstance(val, dict) else val
                                if name and isinstance(name, str):
                                    return name
            except Exception:
                pass

        # 2. Native WirePlumber wpctl status
        if shutil.which("wpctl"):
            try:
                out = subprocess.check_output(["wpctl", "status"], text=True, stderr=subprocess.DEVNULL)
                in_sinks = False
                for line in out.splitlines():
                    if "Sinks:" in line:
                        in_sinks = True
                        continue
                    if in_sinks:
                        if line.strip().startswith(("├─", "└─", "Sources:", "Filters:", "Streams:")):
                            break
                        m = re.search(r"\*\s+(\d+)\.", line)
                        if m:
                            info = subprocess.check_output(["pw-cli", "info", m.group(1)], text=True, stderr=subprocess.DEVNULL)
                            name_m = re.search(r'node\.name = "([^"]+)"', info)
                            if name_m:
                                return name_m.group(1)
            except Exception:
                pass

        return "@DEFAULT_AUDIO_SINK@"

    @staticmethod
    def get_default_mic_source() -> str:
        """Fetch PipeWire default input microphone source name."""
        # 1. Native WirePlumber metadata
        if shutil.which("pw-dump"):
            try:
                out = subprocess.check_output(["pw-dump", "Metadata"], text=True, stderr=subprocess.DEVNULL)
                for obj in json.loads(out):
                    if obj.get("props", {}).get("metadata.name") == "default":
                        for item in obj.get("metadata", []):
                            if item.get("key") in ("default.audio.source", "default.configured.audio.source"):
                                val = item.get("value")
                                name = val.get("name") if isinstance(val, dict) else val
                                if name and isinstance(name, str):
                                    return name
            except Exception:
                pass

        # 2. Native WirePlumber wpctl status
        if shutil.which("wpctl"):
            try:
                out = subprocess.check_output(["wpctl", "status"], text=True, stderr=subprocess.DEVNULL)
                in_sources = False
                for line in out.splitlines():
                    if "Sources:" in line:
                        in_sources = True
                        continue
                    if in_sources:
                        if line.strip().startswith(("├─", "└─", "Filters:", "Streams:")):
                            break
                        m = re.search(r"\*\s+(\d+)\.", line)
                        if m:
                            info = subprocess.check_output(["pw-cli", "info", m.group(1)], text=True, stderr=subprocess.DEVNULL)
                            name_m = re.search(r'node\.name = "([^"]+)"', info)
                            if name_m:
                                return name_m.group(1)
            except Exception:
                pass

        return "@DEFAULT_AUDIO_SOURCE@"

    @classmethod
    def record_clip(
        cls,
        source: str,
        duration: int,
        out_path: Path,
        is_sink_monitor: bool = True,
        tick_callback: Callable[[int], None] | None = None
    ) -> bool:
        """Record audio clip using pw-record or ffmpeg with robust process cleanup."""
        if out_path.exists():
            try:
                out_path.unlink()
            except Exception:
                pass

        # Method 1: pw-record (Native PipeWire)
        if shutil.which("pw-record"):
            cmd = [
                "pw-record",
                "--target", source,
                "--rate", "44100",
                "--channels", "2",
            ]
            if is_sink_monitor:
                cmd.extend(["-P", "{ stream.capture.sink = true }"])
            cmd.append(str(out_path))

            proc = None
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for sec in range(1, duration + 1):
                    time.sleep(1)
                    if tick_callback:
                        tick_callback(sec)
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

                if out_path.is_file() and out_path.stat().st_size > 1024:
                    return True
            except Exception as e:
                log_error(f"pw-record failed: {e}")
            finally:
                if proc and proc.poll() is None:
                    try:
                        proc.kill()
                        proc.wait()
                    except Exception:
                        pass

        # Method 2: ffmpeg with Pulse input fallback
        if shutil.which("ffmpeg"):
            inp_source = f"{source}.monitor" if is_sink_monitor and not source.endswith(".monitor") else source
            cmd = [
                "ffmpeg", "-y",
                "-f", "pulse",
                "-i", inp_source,
                "-t", str(duration),
                "-acodec", "pcm_s16le",
                "-ar", "44100",
                "-ac", "2",
                "-loglevel", "error",
                str(out_path)
            ]
            proc = None
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for sec in range(1, duration + 1):
                    time.sleep(1)
                    if tick_callback:
                        tick_callback(sec)
                proc.wait(timeout=duration + 3)
                if out_path.is_file() and out_path.stat().st_size > 1024:
                    return True
            except Exception as e:
                log_error(f"ffmpeg recording failed: {e}")
            finally:
                if proc and proc.poll() is None:
                    try:
                        proc.kill()
                        proc.wait()
                    except Exception:
                        pass

        return False


# ==============================================================================
#  SHAZAM / SONGREC RECOGNITION ENGINE
# ==============================================================================
class RecognitionEngine:
    @staticmethod
    def verify_dependencies() -> list[str]:
        """Check for missing system binaries."""
        deps = ["songrec"]
        missing = [dep for dep in deps if not shutil.which(dep)]
        if not shutil.which("pw-record") and not shutil.which("ffmpeg"):
            missing.append("pw-record (or ffmpeg)")
        return missing

    @classmethod
    def parse_songrec_json(cls, raw_json: str, source_type: str) -> SongMetadata | None:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return None

        track = data.get("track")
        if not track or not isinstance(track, dict):
            return None

        title = track.get("title", "").strip()
        artist = track.get("subtitle", "").strip()
        if not title:
            return None

        song_id = str(track.get("key") or f"{title}_{artist}".lower())
        cover_url = (
            track.get("images", {}).get("coverarthq")
            or track.get("images", {}).get("coverart")
            or track.get("share", {}).get("image")
            or ""
        )
        shazam_url = track.get("url") or track.get("share", {}).get("href") or ""

        # Extract Album, Year, Genre from sections
        album = ""
        release_year = ""
        genres = []
        lyrics = []

        if "genres" in track and isinstance(track["genres"], dict):
            primary = track["genres"].get("primary")
            if primary:
                genres.append(primary)

        sections = track.get("sections", [])
        if isinstance(sections, list):
            for sec in sections:
                if not isinstance(sec, dict):
                    continue
                sec_type = sec.get("type", "")
                if sec_type == "SONG":
                    metadata_list = sec.get("metadata", [])
                    if isinstance(metadata_list, list):
                        for m in metadata_list:
                            if isinstance(m, dict):
                                t = m.get("title", "")
                                text_val = m.get("text", "")
                                if t == "Album":
                                    album = text_val
                                elif t == "Released":
                                    release_year = text_val
                elif sec_type == "LYRICS":
                    text_lines = sec.get("text", [])
                    if isinstance(text_lines, list):
                        lyrics = [str(line) for line in text_lines if line]

        # Extract Spotify and Apple Music links from hub
        spotify_url = ""
        apple_music_url = ""
        hub = track.get("hub", {})
        if isinstance(hub, dict):
            for opt in hub.get("options", []):
                if isinstance(opt, dict) and "OPEN IN SPOTIFY" in opt.get("caption", "").upper():
                    for action in opt.get("actions", []):
                        if isinstance(action, dict) and action.get("uri"):
                            spotify_url = action["uri"]
            for action in hub.get("actions", []):
                if isinstance(action, dict) and action.get("type") == "applemusicopen":
                    apple_music_url = action.get("uri", "")

        query_str = urllib.parse.quote(f"{title} {artist}")
        if spotify_url.startswith("spotify:track:"):
            track_id = spotify_url.split(":")[-1]
            spotify_url = f"https://open.spotify.com/track/{track_id}"
        elif not spotify_url:
            spotify_url = f"https://open.spotify.com/search/{query_str}"

        youtube_search_url = f"https://www.youtube.com/results?search_query={query_str}"

        now = datetime.now(timezone.utc)
        return SongMetadata(
            id=song_id,
            title=title,
            artist=artist,
            album=album,
            release_year=release_year,
            genres=genres,
            cover_url=cover_url,
            local_cover_path="",
            shazam_url=shazam_url,
            apple_music_url=apple_music_url,
            spotify_url=spotify_url,
            youtube_search_url=youtube_search_url,
            lyrics=lyrics,
            timestamp=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            epoch=now.timestamp(),
            source=source_type,
            raw=data
        )

    @classmethod
    def recognize_file(cls, audio_path: Path, source_type: str) -> SongMetadata | None:
        """Run songrec on the given audio file using audio-file-to-recognized-song."""
        if not shutil.which("songrec"):
            log_error("songrec binary not found.")
            return None

        cmd = ["songrec", "audio-file-to-recognized-song", str(audio_path)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if res.stdout and res.stdout.strip():
                return cls.parse_songrec_json(res.stdout, source_type)
        except subprocess.TimeoutExpired:
            log_error("songrec recognition timed out.")
        except Exception as e:
            log_error(f"songrec error: {e}")
        return None


# ==============================================================================
#  RICH UI COMPONENTS & PRESENTATION (MATUGEN THEMED)
# ==============================================================================
def render_song_card(song: SongMetadata, history_count: int = 0) -> Panel:
    """Render a compact, elegant Rich panel displaying song recognition details with Matugen theme."""
    theme = MatugenTheme.load()
    lines: list[Text] = []

    # Title
    lines.append(Text.from_markup(f" 󰎆 [bold fg]{escape(song.title)}[/bold fg]"))

    # Artist
    lines.append(Text.from_markup(f"    [muted]by[/muted] [accent]{escape(song.artist)}[/accent]"))

    # Metadata row (Album, Year, Genre)
    meta_parts: list[str] = []
    if song.album:
        meta_parts.append(f"[fg]{escape(song.album)}[/fg]")
    if song.release_year:
        meta_parts.append(f"[success]{escape(song.release_year)}[/success]")
    if song.genres:
        meta_parts.append(f"[warning]{escape(song.genres[0])}[/warning]")
    if meta_parts:
        lines.append(Text.from_markup("    " + " • ".join(meta_parts)))

    # Clickable Links row (Theme compliant - no hardcoded red)
    link_parts: list[str] = []
    if song.shazam_url:
        link_parts.append(f"[link={song.shazam_url}][accent]Shazam[/accent][/link]")
    if song.spotify_url:
        link_parts.append(f"[link={song.spotify_url}][success]Spotify[/success][/link]")
    if song.youtube_search_url:
        link_parts.append(f"[link={song.youtube_search_url}][warning]YouTube[/warning][/link]")
    if song.apple_music_url:
        link_parts.append(f"[link={song.apple_music_url}][accent]Apple Music[/accent][/link]")

    if link_parts:
        lines.append(Text.from_markup("    " + " • ".join(link_parts)))

    # Optional Lyrics Snippet (first 2 lines if available)
    if song.lyrics:
        lyrics_preview = song.lyrics[:2]
        lyrics_str = escape(" / ".join(lyrics_preview))
        lines.append(Text.from_markup(f"    [italic muted]“{lyrics_str}”[/italic muted]"))

    content = Text("\n").join(lines)
    footer = f"[muted]Saved to history • #{history_count}[/muted]" if history_count > 0 else "[muted]Saved to history[/muted]"

    return Panel(
        content,
        title="[bold accent]󰄬 Song Identified[/bold accent]",
        subtitle=footer,
        subtitle_align="right",
        border_style=theme.muted,
        box=box.ROUNDED,
        padding=(0, 1),
    )




def render_history_table(songs: list[SongMetadata], query: str | None = None) -> Table:
    """Render a clean Rich table of song recognition history using Matugen theme."""
    theme = MatugenTheme.load()
    table = Table(
        title=f"󰎆 Song Recognition History ({len(songs)} songs)" + (f" • Filter: '{query}'" if query else ""),
        title_style=f"bold {theme.accent}",
        box=box.ROUNDED,
        border_style=theme.muted,
        header_style=f"bold {theme.accent}",
        show_lines=False,
    )


    table.add_column("#", justify="right", style=theme.muted, width=4)
    table.add_column("Time", style=theme.muted, width=16)
    table.add_column("Title", style=f"bold {theme.fg}", min_width=16)
    table.add_column("Artist", style=theme.accent, min_width=14)
    table.add_column("Album", style=theme.fg, min_width=12)
    table.add_column("Links", style=theme.warning, min_width=14)

    for idx, s in enumerate(songs, start=1):
        links = []
        if s.shazam_url:
            links.append(f"[link={s.shazam_url}]Shazam[/link]")
        if s.spotify_url:
            links.append(f"[link={s.spotify_url}]Spotify[/link]")
        if s.youtube_search_url:
            links.append(f"[link={s.youtube_search_url}]YouTube[/link]")

        time_str = s.timestamp.split(" UTC")[0] if " UTC" in s.timestamp else s.timestamp
        table.add_row(
            str(idx),
            time_str,
            s.title,
            s.artist,
            s.album or "[muted]—[/muted]",
            " • ".join(links) if links else "[muted]—[/muted]"
        )

    return table






# ==============================================================================
#  INTERACTIVE HISTORY BROWSING & FZF INTEGRATION (MATUGEN THEMED)
# ==============================================================================

def read_single_key() -> str:
    """Read a single keypress without waiting for Enter."""
    if not sys.stdin.isatty():
        return ""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # Escape sequence
            r, _, _ = select.select([sys.stdin], [], [], 0.05)
            if r:
                ch += sys.stdin.read(2)
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def open_in_browser(url: str) -> None:
    """Open URL in default browser completely detached from the terminal process group."""
    if not url:
        return
    try:
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True
        )
    except Exception as e:
        log_error(f"Failed to open URL in browser ({url}): {e}")


def run_fzf_browser(history_store: HistoryStore) -> None:
    """Interactive fuzzy search browser using fzf perfectly themed with Matugen."""
    items = history_store.get_all()
    if not items:
        console.print("[warning]Recognition history is empty.[/warning]")
        return

    if not shutil.which("fzf"):
        console.print(render_history_table(items))
        return

    theme = MatugenTheme.load()
    fzf_colors = theme.get_fzf_color_opt()

    lines = []
    lookup: dict[str, SongMetadata] = {}
    for idx, s in enumerate(items, start=1):
        dt = s.timestamp.split(" UTC")[0] if " UTC" in s.timestamp else s.timestamp
        key = f"[{idx:03d}] {s.title} — {s.artist} ({s.album or 'Single'}) [{dt}]"
        lines.append(key)
        lookup[key] = s

    fzf_input = "\n".join(lines)
    header = "ENTER: YouTube | CTRL-S: Spotify | CTRL-Y: Copy | ESC: Exit"
    env = os.environ.copy()
    env["FZF_DEFAULT_OPTS"] = f"--color={fzf_colors} --pointer='▌' --marker='┃' --info=inline-right"

    try:
        proc = subprocess.run(
            [
                "fzf",
                "--ansi",
                f"--color={fzf_colors}",
                "--pointer=▌",
                "--marker=┃",
                "--highlight-line",
                "--header", header,
                "--prompt", "󰎆 History > ",
                "--border=rounded",
                "--border-label= 󰎆 Song History ",
                "--border-label-pos=top:center",
                "--layout=reverse",
                "--height=50%",
                "--expect=ctrl-y,ctrl-s",
            ],
            input=fzf_input.encode("utf-8"),
            capture_output=True,
            env=env
        )
        output_lines = proc.stdout.decode("utf-8").splitlines()
        if len(output_lines) >= 2:
            key_pressed = output_lines[0].strip()
            selected_key = output_lines[1].strip()
        elif len(output_lines) == 1:
            key_pressed = ""
            selected_key = output_lines[0].strip()
        else:
            return

        if selected_key and selected_key in lookup:
            selected_song = lookup[selected_key]
            if key_pressed == "ctrl-y":
                copy_to_clipboard(f"{selected_song.title} - {selected_song.artist}")
                sys.exit(0)
            elif key_pressed == "ctrl-s":
                spotify_target = selected_song.spotify_url or f"https://open.spotify.com/search/{urllib.parse.quote(selected_song.title + ' ' + selected_song.artist)}"
                open_in_browser(spotify_target)
                sys.exit(0)
            else:
                url = selected_song.youtube_search_url or selected_song.shazam_url or selected_song.spotify_url
                open_in_browser(url)
                sys.exit(0)
    except Exception as e:
        log_error(f"fzf error: {e}")


def handle_interactive_post_recognition(song: SongMetadata | None, history_store: HistoryStore) -> None:
    """Offer instant single-key actions right in the terminal window."""
    if not sys.stdin.isatty():
        return

    action_text = Text.from_markup(
        "\n [accent]Actions:[/accent] [bold fg][H][/bold fg] History  [bold fg][F][/bold fg] FZF Search  [bold fg][O][/bold fg] YouTube  [bold fg][S][/bold fg] Spotify  [bold fg][C][/bold fg] Copy  [bold fg][Q][/bold fg] Exit"
    )
    console.print(action_text)

    try:
        key = read_single_key().lower()
    except (KeyboardInterrupt, EOFError):
        sys.exit(0)

    if key in ("q", "\x1b", "\r", "\n", "\x03", "\x04", ""):
        sys.exit(0)
    elif key == "h":
        console.clear()
        items = history_store.get_all()
        console.print(render_history_table(items))
        console.print(Text.from_markup("\n [accent]Actions:[/accent] [bold fg][F][/bold fg] FZF Search  [bold fg][O][/bold fg] YouTube  [bold fg][S][/bold fg] Spotify  [bold fg][C][/bold fg] Copy Last  [bold fg][Q][/bold fg] Exit"))
        try:
            sub_key = read_single_key().lower()
        except (KeyboardInterrupt, EOFError):
            sys.exit(0)
        if sub_key == "f":
            console.clear()
            run_fzf_browser(history_store)
        elif sub_key == "o":
            latest = history_store.get_latest()
            if latest:
                url = latest.youtube_search_url or latest.shazam_url or latest.spotify_url
                open_in_browser(url)
            sys.exit(0)
        elif sub_key == "s":
            latest = history_store.get_latest()
            if latest:
                spotify_target = latest.spotify_url or f"https://open.spotify.com/search/{urllib.parse.quote(latest.title + ' ' + latest.artist)}"
                open_in_browser(spotify_target)
            sys.exit(0)
        elif sub_key == "c":
            latest = history_store.get_latest()
            if latest:
                copy_to_clipboard(f"{latest.title} - {latest.artist}")
            sys.exit(0)
        else:
            sys.exit(0)
    elif key == "f":
        console.clear()
        run_fzf_browser(history_store)
    elif key == "o":
        target = song or history_store.get_latest()
        if target:
            url = target.youtube_search_url or target.shazam_url or target.spotify_url
            open_in_browser(url)
        sys.exit(0)
    elif key == "s":
        target = song or history_store.get_latest()
        if target:
            spotify_target = target.spotify_url or f"https://open.spotify.com/search/{urllib.parse.quote(target.title + ' ' + target.artist)}"
            open_in_browser(spotify_target)
        sys.exit(0)
    elif key == "c":
        target = song or history_store.get_latest()
        if target:
            copy_to_clipboard(f"{target.title} - {target.artist}")
        sys.exit(0)






# ==============================================================================
#  MAIN WORKFLOW & RECOGNITION LOOP
# ==============================================================================
def run_recognition(
    source_type: str = "system",
    duration: int = 5,
    timeout: int = 30,
    notify: bool = True,
    auto_copy: bool = False,
    json_output: bool = False
) -> int:
    """Execute the core music recognition workflow."""
    theme = MatugenTheme.load()
    missing = RecognitionEngine.verify_dependencies()
    if missing:
        console.print(f"[error]Error:[/error] Missing required dependencies: {', '.join(missing)}")
        console.print("[warning]Install with:[/warning] sudo pacman -S --needed songrec ffmpeg libpulse")
        return 1

    # Select audio source
    is_sink_monitor = (source_type != "mic")
    if source_type == "mic":
        audio_source = AudioEngine.get_default_mic_source()
        source_label = "Microphone"
    else:
        audio_source = AudioEngine.get_default_monitor_source()
        source_label = "System Audio"

    if not audio_source:
        console.print(f"[error]Error:[/error] Could not detect active {source_label} source.")
        return 1

    log_info(f"Starting recognition session on '{audio_source}' (type={source_type}, duration={duration}s, timeout={timeout}s)")

    if notify and not json_output:
        Notifier.notify_listening(source_label)

    start_time = time.time()
    attempt = 0
    history_store = HistoryStore()
    found_song: SongMetadata | None = None

    with tempfile.TemporaryDirectory(prefix="dusky_songrec_") as tmp_dir:
        tmp_wav = Path(tmp_dir) / "recording.wav"

        if json_output:
            # Silent loop for JSON mode
            while time.time() - start_time < timeout:
                attempt += 1
                if AudioEngine.record_clip(audio_source, duration, tmp_wav, is_sink_monitor=is_sink_monitor):
                    song = RecognitionEngine.recognize_file(tmp_wav, source_type)
                    if song:
                        if song.cover_url:
                            song.local_cover_path = download_cover_art(song.cover_url, song.id)
                        history_store.add(song)
                        print(json.dumps(song.to_dict(), indent=2))
                        return 0
            print(json.dumps({"error": "No match found", "elapsed": int(time.time() - start_time)}))
            return 0

        # Interactive Rich Mode
        eq_idx = 0
        with Live(console=console, refresh_per_second=10) as live:
            while time.time() - start_time < timeout:
                attempt += 1
                elapsed = int(time.time() - start_time)

                def update_live_status(sec: int) -> None:
                    nonlocal eq_idx
                    eq_idx = (eq_idx + 1) % len(EQ_FRAMES)
                    wave = EQ_FRAMES[eq_idx]
                    status_text = Text.assemble(
                        ("󰐊 ", "error"),
                        ("Listening... ", "bold fg"),
                        (f"[{elapsed + sec:02d}s/{timeout}s] ", "muted"),
                        (f"{wave} ", "accent"),
                        (f"(Attempt {attempt})", "muted")
                    )
                    live.update(
                        Panel(
                            Align.center(status_text),
                            box=box.ROUNDED,
                            border_style=theme.muted,
                            padding=(0, 1)
                        )
                    )

                update_live_status(0)
                recorded = AudioEngine.record_clip(
                    audio_source, duration, tmp_wav, is_sink_monitor=is_sink_monitor, tick_callback=update_live_status
                )

                if recorded:
                    live.update(
                        Panel(
                            Align.center(Text("󰑐 Analyzing audio fingerprint with Shazam servers...", style="accent")),
                            box=box.ROUNDED,
                            border_style=theme.muted,
                            padding=(0, 1)
                        )
                    )
                    song = RecognitionEngine.recognize_file(tmp_wav, source_type)
                    if song:
                        found_song = song
                        break

    # Live context is completely finished
    if found_song:
        if found_song.cover_url:
            found_song.local_cover_path = download_cover_art(found_song.cover_url, found_song.id)

        history_store.add(found_song)
        total_count = len(history_store.get_all())

        if notify:
            Notifier.notify_detected(found_song)

        if auto_copy:
            copy_to_clipboard(f"{found_song.title} - {found_song.artist}")

        console.clear()
        console.print(render_song_card(found_song, total_count))
        log_info(f"Identified: {found_song.title} by {found_song.artist}")
        handle_interactive_post_recognition(found_song, history_store)
        return 0

    # If we reach here, timeout expired without match
    elapsed_total = int(time.time() - start_time)
    if notify:
        Notifier.notify_no_match(elapsed_total)

    console.clear()
    console.print(
        Panel(
            Align.center(
                Text.assemble(
                    ("󰅖 ", "error"),
                    ("No Match Found ", "bold fg"),
                    (f"(Elapsed: {elapsed_total}s, {attempt} attempts)\n", "muted"),
                    ("Ensure music is audible and playing clearly.", "italic warning")
                )
            ),
            box=box.ROUNDED,
            border_style=theme.muted,
            padding=(0, 1)
        )
    )
    handle_interactive_post_recognition(None, history_store)
    return 0





# ==============================================================================
#  CLI ARGUMENT PARSING & ENTRY POINT
# ==============================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="music_recognition",
        description="󰎆 Dusky Music Recognition - Shazam Audio Identification with Rich UI & Mako notifications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  music_recognition.py                     # Listen & recognize system audio (default)
  music_recognition.py history             # View song recognition history table
  music_recognition.py fzf                 # Interactive fuzzy search history browser
  music_recognition.py last                # Show details of last detected song
  music_recognition.py status              # Inspect dependencies & system health
  music_recognition.py clear               # Clear stored song history

Options:
  music_recognition.py -m                  # Recognize from microphone
  music_recognition.py -d 8 -t 45          # 8-second clips, 45-second timeout
  music_recognition.py -H                  # Show recognition history
  music_recognition.py -F                  # Launch interactive fzf history browser
  music_recognition.py -s "Daft Punk"      # Search recognition history
  music_recognition.py --export songs.csv  # Export history to CSV
  music_recognition.py --json              # Output raw JSON
        """
    )

    # Positional command (optional)
    parser.add_argument(
        "command",
        nargs="?",
        choices=["listen", "history", "log", "fzf", "last", "status", "clear", "export"],
        help="Optional subcommand (default: listen)"
    )

    # Recognition options
    rec_group = parser.add_argument_group("Recognition Options")
    rec_group.add_argument("-m", "--mic", action="store_true", help="Capture from microphone input instead of system audio")
    rec_group.add_argument("-d", "--duration", type=int, default=None, help="Duration of each audio sample in seconds (default: 5)")
    rec_group.add_argument("-t", "--timeout", type=int, default=None, help="Maximum total recognition timeout in seconds (default: 30)")
    rec_group.add_argument("-c", "--copy", action="store_true", help="Automatically copy 'Title - Artist' to clipboard")
    rec_group.add_argument("--no-notify", action="store_true", help="Disable desktop notification popup")
    rec_group.add_argument("--json", action="store_true", help="Output song metadata as JSON (silent mode)")

    # History options
    hist_group = parser.add_argument_group("History & State Management")
    hist_group.add_argument("-H", "--history", action="store_true", help="Display recognition history table")
    hist_group.add_argument("-F", "--fzf", action="store_true", help="Launch interactive fuzzy search history browser")
    hist_group.add_argument("-s", "--search", type=str, metavar="QUERY", help="Search history by title, artist, album, or lyrics")
    hist_group.add_argument("-l", "--last", action="store_true", help="Show full card of the last recognized song")
    hist_group.add_argument("--limit", type=int, default=50, help="Maximum history records to display (default: 50)")
    hist_group.add_argument("--clear-history", action="store_true", help="Clear all stored recognition history")
    hist_group.add_argument("--export", type=str, metavar="FILE", help="Export history to file (.json, .csv, .md)")

    # System & Info options
    sys_group = parser.add_argument_group("System & Configuration")
    sys_group.add_argument("--status", action="store_true", help="Inspect audio hardware, dependencies, and configuration")
    sys_group.add_argument("-v", "--version", action="version", version=f"%(prog)s {VERSION}")

    return parser


def main() -> None:
    # Handle termination signals cleanly
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(0))

    parser = build_parser()
    args = parser.parse_args()

    config = AppConfig.load()
    history = HistoryStore(max_records=config.max_history)

    # Route positional commands or flags
    cmd = args.command or ""

    # Subcommand: Status
    if args.status or cmd == "status":
        theme = MatugenTheme.load()
        table = Table(
            title="Dusky Music Recognition - System Status",
            title_style=f"bold {theme.accent}",
            box=box.ROUNDED,
            border_style=theme.muted,
            header_style=f"bold {theme.accent}"
        )
        table.add_column("Component", style=f"bold {theme.fg}")
        table.add_column("Status / Path", style=theme.accent)


        table.add_row("Version", VERSION)
        table.add_row("State Directory", str(STATE_DIR))
        table.add_row("History File", f"{HISTORY_FILE} ({len(history.get_all())} items)")
        table.add_row("Cover Art Cache", f"{COVERS_DIR} ({len(list(COVERS_DIR.glob('*.jpg')))} covers)")

        for dep in ["songrec", "pw-record", "ffmpeg", "notify-send", "wl-copy", "fzf", "mako"]:
            p = shutil.which(dep)
            status = f"[success]󰄬 {p}[/success]" if p else "[error]󰅖 Not Installed[/error]"
            table.add_row(f"Dependency: {dep}", status)

        sink = AudioEngine.get_default_monitor_source() or "None"
        mic = AudioEngine.get_default_mic_source() or "None"
        table.add_row("Default Sink Monitor", sink)
        table.add_row("Default Mic Source", mic)

        console.print(table)
        return

    # Subcommand: Clear History
    if args.clear_history or cmd == "clear":
        if console.input("[warning]Are you sure you want to clear song recognition history? (y/N): [/warning]").strip().lower() in ("y", "yes"):
            history.clear()
            console.print("[success]󰄬 Recognition history cleared.[/success]")
        else:
            console.print("[muted]Aborted.[/muted]")
        return

    # Subcommand: Export History
    if args.export or cmd == "export":
        target = args.export or str(STATE_DIR / "history_export.csv")
        export_path = Path(target).resolve()
        fmt = export_path.suffix.lstrip(".") or "json"
        if history.export(export_path, fmt):
            console.print(f"[success]󰄬 Successfully exported history to:[/success] {export_path}")
        else:
            console.print(f"[error]󰅖 Failed to export history to:[/error] {export_path}")
            sys.exit(1)
        return


    # Subcommand: FZF Interactive Browser
    if args.fzf or cmd == "fzf":
        run_fzf_browser(history)
        return

    # Subcommand: Last Recognized Song
    if args.last or cmd == "last":
        latest = history.get_latest()
        if latest:
            console.print(render_song_card(latest, len(history.get_all())))
        else:
            console.print("[warning]No songs in recognition history yet.[/warning]")
        return

    # Subcommand: Show / Search History Table
    if args.history or args.search or cmd in ("history", "log"):
        results = history.get_all(query=args.search, limit=args.limit)
        if results:
            console.print(render_history_table(results, query=args.search))
        else:
            msg = f"No songs matching '{args.search}'." if args.search else "Recognition history is empty."
            console.print(f"[warning]{msg}[/warning]")
        return


    # Singleton Execution Lock for Recognition
    lock = ProcessLock(LOCK_FILE)
    if not lock.acquire():
        if not args.json:
            console.print("[warning]A music recognition session is already currently running.[/warning]")
        sys.exit(0)


    try:
        source_type = "mic" if args.mic else config.default_source
        duration = args.duration or config.record_duration
        timeout = args.timeout or config.timeout
        notify = config.notifications if not args.no_notify else False
        auto_copy = args.copy or config.auto_copy

        exit_code = run_recognition(
            source_type=source_type,
            duration=duration,
            timeout=timeout,
            notify=notify,
            auto_copy=auto_copy,
            json_output=args.json
        )
        sys.exit(exit_code)
    finally:
        lock.release()


if __name__ == "__main__":
    main()

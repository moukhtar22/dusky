"""Always-on Dusky Keylogger daemon.

Wires the evdev KeyListener to SQLite via a dedicated writer thread:
the asyncio loop never issues a blocking sqlite3 call. Designed to run
under systemd Type=simple, stopping cleanly on SIGINT/SIGTERM.
"""

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path

from . import __version__
from .listener import KeyListener, KeyPress
from .storage import EventRow, EventWriter, KeyStore, row_from_press

logger = logging.getLogger(__name__)

DEFAULT_FLUSH_INTERVAL = 0.5
MAX_BUFFER = 256


def default_data_dir() -> Path:
    """Persistent DB directory (survives reboot). Never hardcodes username.

    Priority: env DUSKY_KEYLOGGER_DATA_DIR > config.json data_dir > default.
    Default is now ~/.config/dusky/settings/keylogger/data (per user request).
    Legacy ~/.local/share/dusky-keylogger is auto-migrated if present.
    The directory is auto-created on a fresh install if it doesn't already exist
    (see KeyStore.init_db / Daemon). Uses Path.home() expansion for ~.
    """
    override = os.environ.get("DUSKY_KEYLOGGER_DATA_DIR")
    if override:
        p = Path(override).expanduser()
        return p if p.is_absolute() else Path.home() / p
    # Try config file (new canonical, fallback old) for data_dir without recursion
    for cfg_path in (
        Path.home() / ".config" / "dusky" / "settings" / "keylogger" / "config.json",
        Path.home() / ".config" / "dusky-keylogger" / "config.json",
    ):
        if cfg_path.exists():
            try:
                import json as _json

                with cfg_path.open("r", encoding="utf-8") as fh:
                    data = _json.load(fh)
                if isinstance(data, dict) and data.get("data_dir"):
                    raw = str(data["data_dir"]).strip()
                    if raw:
                        p = Path(raw).expanduser()
                        return p if p.is_absolute() else Path.home() / p
            except Exception:
                pass
            break
    return Path.home() / ".config" / "dusky" / "settings" / "keylogger" / "data"


def get_data_dir(config: dict | None = None) -> Path:
    """Resolve persistent data dir from config/env (helper for TUI)."""
    if config is None:
        # Avoid recursion: directly check env + config file minimally
        return default_data_dir()
    # Config passed explicitly (from TUI)
    env = os.environ.get("DUSKY_KEYLOGGER_DATA_DIR")
    if env:
        p = Path(env).expanduser()
        return p if p.is_absolute() else Path.home() / p
    raw = str(config.get("data_dir", "") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else Path.home() / p
    return Path.home() / ".config" / "dusky" / "settings" / "keylogger" / "data"


def default_config_path() -> Path:
    """Canonical config path with intelligent fallback.

    New canonical (dusky ecosystem): ~/.config/dusky/settings/keylogger/config.json
    Legacy: ~/.config/dusky-keylogger/config.json (auto-migrated on first run).
    Env DUSKY_KEYLOGGER_CONFIG overrides both. Never hardcodes username (Path.home).
    The directory is auto-created on a fresh install if it doesn't already exist
    (see load_config).
    """
    env = os.environ.get("DUSKY_KEYLOGGER_CONFIG")
    if env:
        p = Path(env).expanduser()
        if not p.is_absolute():
            p = Path.home() / p
        return p
    new = Path.home() / ".config" / "dusky" / "settings" / "keylogger" / "config.json"
    old = Path.home() / ".config" / "dusky-keylogger" / "config.json"
    # Fresh install: neither exists -> return new (will be auto-created)
    # Migration: old exists but new doesn't -> return old for now (load_config will migrate)
    # Normal: new exists -> return new
    if new.exists():
        return new
    if old.exists() and not new.exists():
        return old
    return new


def _old_config_path() -> Path:
    return Path.home() / ".config" / "dusky-keylogger" / "config.json"


DEFAULT_CONFIG: dict = {
    "flush_interval": DEFAULT_FLUSH_INTERVAL,
    "log_level": "info",
    # Persistence: where SQLite DB lives (survives reboot, manual delete only).
    # Default is now ~/.config/dusky/settings/keylogger/data (per user request).
    # Override via env DUSKY_KEYLOGGER_DATA_DIR or config "data_dir".
    # Ephemeral: where transcripts go (cleared on reboot, e.g., /tmp).
    # Default is /tmp (per user request) — just these two places, nothing else.
    # Persistent counts/stats stay in DATA_DIR (not ephemeral); transcripts are ephemeral.
    "data_dir": "~/.config/dusky/settings/keylogger/data",
    "transcript_dir": "/tmp",
    "transcript_format": "text",  # "text" | "markdown"
    "persistent_enabled": True,  # master toggle for DB logging (persistent)
    "ephemeral_enabled": True,  # master toggle for transcript generation (ephemeral)
}


def load_config(path: Path | None = None) -> dict:
    cfg_path = path or default_config_path()
    config = dict(DEFAULT_CONFIG)
    try:
        if cfg_path.exists():
            # Tighten existing config dir/file perms (privacy) even on read path.
            try:
                os.chmod(cfg_path.parent, 0o700)
            except OSError:
                pass
            try:
                os.chmod(cfg_path, 0o600)
            except OSError:
                pass
            with cfg_path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
                if isinstance(loaded, dict):
                    config.update(loaded)
                else:
                    logger.warning("Config %s is not a JSON object -- using defaults", cfg_path)
        else:
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(cfg_path.parent, 0o700)
            except OSError:
                pass
            # Use O_EXCL to avoid clobbering concurrent daemon writes; ignore if exists.
            try:
                fd = os.open(cfg_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(DEFAULT_CONFIG, fh, indent=2)
                    fh.write("\n")
            except FileExistsError:
                pass
            try:
                os.chmod(cfg_path, 0o600)
            except OSError:
                pass
    except (OSError, ValueError) as exc:
        logger.warning("Could not read config %s: %s", cfg_path, exc)
    # Clamp flush_interval to sane range to avoid busy-spin or huge latency.
    try:
        fi = float(config.get("flush_interval", DEFAULT_FLUSH_INTERVAL))
        config["flush_interval"] = max(0.05, min(fi, 5.0))
    except Exception:
        config["flush_interval"] = DEFAULT_FLUSH_INTERVAL
    if str(config.get("log_level", "info")).lower() not in {"debug", "info", "warning", "error"}:
        config["log_level"] = "info"
    else:
        config["log_level"] = str(config.get("log_level", "info")).lower()

    # -- transcript_dir: env overrides config for flexibility (no hardcoding user) --
    env_transcript = os.environ.get("DUSKY_TRANSCRIPT_DIR")
    if env_transcript:
        config["transcript_dir"] = env_transcript
    raw_tdir = str(config.get("transcript_dir", "/tmp") or "/tmp").strip()
    # Expand ~ and $HOME without hardcoding username; keep /tmp as default even if
    # user passes empty. Normalize.
    try:
        tdir = Path(raw_tdir).expanduser()
        # If relative, make it relative to $HOME (intuitive for users editing config)
        if not tdir.is_absolute():
            tdir = (Path.home() / tdir)
        # Do NOT resolve strictly; /tmp may be symlink to /var/tmp etc.
        config["transcript_dir"] = str(tdir)
    except Exception:
        config["transcript_dir"] = "/tmp"

    fmt = str(config.get("transcript_format", "text")).lower().strip()
    if fmt not in {"text", "markdown", "md"}:
        fmt = "text"
    if fmt == "md":
        fmt = "markdown"
    config["transcript_format"] = fmt

    # Env format override (also flexible)
    env_fmt = os.environ.get("DUSKY_TRANSCRIPT_FORMAT")
    if env_fmt:
        ef = env_fmt.lower().strip()
        if ef in {"text", "markdown", "md"}:
            config["transcript_format"] = "markdown" if ef in {"markdown", "md"} else "text"

    # -- data_dir: persistent DB location (env overrides config) --
    env_data = os.environ.get("DUSKY_KEYLOGGER_DATA_DIR")
    if env_data:
        config["data_dir"] = env_data
    raw_data = str(config.get("data_dir", "~/.config/dusky/settings/keylogger/data") or "~/.config/dusky/settings/keylogger/data").strip()
    try:
        ddir = Path(raw_data).expanduser()
        if not ddir.is_absolute():
            ddir = Path.home() / ddir
        config["data_dir"] = str(ddir)
    except Exception:
        config["data_dir"] = str(Path.home() / ".config" / "dusky" / "settings" / "keylogger" / "data")

    # Persist auto-migrated keys (e.g., fresh fields after update) without
    # clobbering user values. Best-effort; failure is non-fatal.
    try:
        if cfg_path.exists():
            with cfg_path.open("r", encoding="utf-8") as fh:
                on_disk = json.load(fh)
            if isinstance(on_disk, dict):
                missing = {k: v for k, v in config.items() if k not in on_disk}
                # Only persist keys that are part of DEFAULT_CONFIG (avoid
                # writing env-overridden ephemeral values if env set)
                persisted_missing = {k: v for k, v in missing.items() if k in DEFAULT_CONFIG}
                # Don't persist env-override transcript_dir if it came from env
                if os.environ.get("DUSKY_TRANSCRIPT_DIR") and "transcript_dir" in persisted_missing:
                    persisted_missing.pop("transcript_dir", None)
                if os.environ.get("DUSKY_TRANSCRIPT_FORMAT") and "transcript_format" in persisted_missing:
                    persisted_missing.pop("transcript_format", None)
                if os.environ.get("DUSKY_KEYLOGGER_DATA_DIR") and "data_dir" in persisted_missing:
                    persisted_missing.pop("data_dir", None)
                if persisted_missing:
                    on_disk.update(persisted_missing)
                    tmp = cfg_path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(on_disk, indent=2) + "\n", encoding="utf-8")
                    tmp.chmod(0o600)
                    tmp.rename(cfg_path)
    except Exception:
        pass

    # Intelligent migration: old path ~/.config/dusky-keylogger/config.json ->
    # new canonical ~/.config/dusky/settings/keylogger/config.json
    # Auto-creates the new directory on a fresh install if it doesn't already exist.
    try:
        if path is None and not os.environ.get("DUSKY_KEYLOGGER_CONFIG"):
            new_path = Path.home() / ".config" / "dusky" / "settings" / "keylogger" / "config.json"
            old_path = _old_config_path()
            # If we just read from old and new doesn't exist, migrate
            if cfg_path == old_path and old_path.exists() and not new_path.exists():
                new_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(new_path.parent, 0o700)
                    # Also ensure intermediate dusky/settings dirs are 0700 for privacy
                    for parent in [new_path.parent, new_path.parent.parent, new_path.parent.parent.parent]:
                        try:
                            os.chmod(parent, 0o700)
                        except OSError:
                            pass
                except OSError:
                    pass
                # Copy with 0600
                try:
                    data = old_path.read_text(encoding="utf-8")
                    tmp = new_path.with_suffix(".tmp")
                    tmp.write_text(data, encoding="utf-8")
                    tmp.chmod(0o600)
                    tmp.rename(new_path)
                except OSError:
                    pass
                # Keep old for backward compat, but future default_config_path will now return new
                cfg_path = new_path
    except Exception:
        pass

    return config


def get_transcript_dir(config: dict | None = None) -> Path:
    """Resolve ephemeral transcript directory (config > env > /tmp).

    Never hardcodes a username; uses $HOME expansion if relative.
    Auto-creates on first use (caller should mkdir) but this helper just resolves.
    """
    cfg = config if config is not None else load_config()
    raw = str(cfg.get("transcript_dir", "/tmp") or "/tmp")
    # env already folded in load_config, but respect direct env if config was passed in
    env = os.environ.get("DUSKY_TRANSCRIPT_DIR")
    if env:
        raw = env
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path.home() / p
    return p


def get_transcript_format(config: dict | None = None) -> str:
    cfg = config if config is not None else load_config()
    fmt = str(cfg.get("transcript_format", "text")).lower()
    env = os.environ.get("DUSKY_TRANSCRIPT_FORMAT")
    if env:
        env = env.lower().strip()
        if env in {"text", "markdown", "md"}:
            return "markdown" if env in {"markdown", "md"} else "text"
    return "markdown" if fmt in {"markdown", "md"} else "text"


def sd_notify(message: str) -> None:
    """Send a systemd notification (READY / WATCHDOG / STOPPING / STATUS).

    No-op when not running under systemd (NOTIFY_SOCKET unset).
    """
    raw = os.environ.get("NOTIFY_SOCKET")
    if not raw:
        return
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
        try:
            addr = "\0" + raw[1:] if raw.startswith("@") else raw
            sock.connect(addr)
            sock.send(message.encode())
        finally:
            sock.close()
    except OSError:
        logger.debug("sd_notify(%r) failed", message, exc_info=True)


def _setup_logging(level: str, data_dir: Path) -> None:
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    lvl = level_map.get(str(level).lower(), logging.INFO)
    # Avoid duplicate handlers if run() is somehow invoked twice in same process (tests).
    root = logging.getLogger()
    # Remove stale dusky handlers that we previously added (idempotent setup).
    for h in list(root.handlers):
        if getattr(h, "_dusky", False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    # Use force=True on 3.8+ to reconfigure without duplicate StreamHandlers.
    logging.basicConfig(
        level=lvl,
        force=True,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    root.setLevel(lvl)
    # Mark the stream handler we just added so we can find it next time.
    for h in root.handlers:
        h._dusky = True  # type: ignore[attr-defined]
    try:
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(log_dir, 0o700)
            os.chmod(data_dir, 0o700)
        except OSError:
            pass
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            log_dir / "daemon.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler._dusky = True  # type: ignore[attr-defined]
        file_handler.setLevel(lvl)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-7s] %(message)s", "%Y-%m-%d %H:%M:%S"
            )
        )
        root.addHandler(file_handler)
    except OSError as exc:
        logger.warning("File logging disabled: %s", exc)


class Daemon:
    """Keystroke logging daemon. Event loop never blocks on SQLite."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        config: dict | None = None,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir else default_data_dir()
        self._config = config if config is not None else load_config()
        self._store = KeyStore(self._data_dir / "keys.db")
        self._writer = EventWriter(self._store)
        self._listener: KeyListener | None = None
        self._buffer: list[EventRow] = []
        # Clamp flush interval to avoid tight loops if config is malformed.
        try:
            fi = float(self._config.get("flush_interval", DEFAULT_FLUSH_INTERVAL))
        except Exception:
            fi = DEFAULT_FLUSH_INTERVAL
        self._flush_interval = max(0.05, min(fi, 5.0))
        self._stop = asyncio.Event()
        self._started_at = time.monotonic()

    def _handle_press(self, press: KeyPress) -> None:
        # Intelligent persistence toggle: if disabled, skip DB logging entirely
        # (ephemeral transcripts still possible via CLI if enabled, but DB stays quiet).
        if not self._config.get("persistent_enabled", True):
            return
        self._buffer.append(row_from_press(press))
        if len(self._buffer) >= MAX_BUFFER:
            self._kick_flush()

    def _kick_flush(self) -> None:
        if not self._buffer:
            return
        # Soft cap: if buffer grows beyond 20k (writer stuck ~80 flush cycles),
        # warn and keep newest events to avoid unbounded memory, but never silently
        # drop without logging. Normal steady state never hits this.
        if len(self._buffer) > 20000:
            logger.error(
                "Buffer grew to %d -- writer appears stuck; truncating oldest",
                len(self._buffer),
            )
            self._buffer = self._buffer[-20000:]
        rows, self._buffer = self._buffer, []
        if not self._writer.submit(rows):
            logger.error(
                "Writer queue saturated -- holding %d events in memory",
                len(rows),
            )
            # Preserve order: unsent rows go in front of any new arrivals.
            self._buffer = rows + self._buffer

    async def _flush_loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._flush_interval
                    )
                except TimeoutError:
                    self._kick_flush()
                    err = self._writer.last_error
                    if err is not None:
                        logger.error("Writer error: %s", err)
        except asyncio.CancelledError:
            raise

    async def _watchdog_loop(self, interval: float) -> None:
        """Ping systemd's watchdog at half WatchdogSec when WATCHDOG_USEC is set."""
        while not self._stop.is_set():
            sd_notify("WATCHDOG=1")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                continue

    async def run(self) -> None:
        _setup_logging(self._config.get("log_level", "info"), self._data_dir)
        self._store.init_db()
        self._writer.start()

        listener = KeyListener()
        self._listener = listener
        listener.on_key = self._handle_press
        await listener.start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self._stop.set)

        flush_task = asyncio.create_task(self._flush_loop(), name="dusky-flush")
        wd_usec = int(os.environ.get("WATCHDOG_USEC", "0"))
        wd_task: asyncio.Task | None = None
        if wd_usec > 0:
            wd_task = asyncio.create_task(
                self._watchdog_loop(wd_usec / 2_000_000), name="dusky-watchdog"
            )
        sd_notify(f"READY=1\nSTATUS=dusky v{__version__} listening\n")
        logger.info(
            "Dusky Keylogger v%s started (data: %s)",
            __version__,
            self._store.path,
        )
        try:
            await self._stop.wait()
        finally:
            sd_notify("STOPPING=1\nSTATUS=flushing\n")
            logger.info("Shutting down...")
            flush_task.cancel()
            if wd_task is not None:
                wd_task.cancel()
            await asyncio.gather(
                flush_task, *((wd_task,) if wd_task else ()), return_exceptions=True
            )
            await listener.stop()
            # Flush in-memory buffer to the writer queue first.
            self._kick_flush()
            # If queue was saturated, _kick_flush leaves rows in _buffer.
            # Close the writer first to drain whatever is queued; then
            # synchronously persist any leftovers that could not be queued.
            # This avoids two concurrent SQLite writers contending for the
            # WAL lock during shutdown.
            self._writer.close(timeout=8.0)
            if self._buffer:
                rows, self._buffer = self._buffer, []
                try:
                    self._store.insert_many(rows)
                    logger.info("Final synchronous flush: %d rows", len(rows))
                except Exception:
                    logger.exception("Final flush failed (%d rows)", len(rows))
            # Also flush any writer retry that was held due to transient SQLITE_BUSY.
            # EventWriter.close already attempts to flush _retry, but if the writer
            # thread hit an error and preserved _retry, try once more synchronously.
            if self._writer.last_error is not None:
                logger.error("Writer finished with error: %s", self._writer.last_error)
                retry = getattr(self._writer, "_retry", [])
                if retry:
                    try:
                        self._store.insert_many(list(retry))
                        logger.info("Flushed %d retry rows synchronously", len(retry))
                    except Exception:
                        logger.exception("Retry flush failed (%d rows)", len(retry))
            logger.info(
                "Shutdown complete: %d rows persisted, uptime %.1fs",
                self._writer.written,
                time.monotonic() - self._started_at,
            )

    async def stop(self) -> None:
        # Thread-safe: may be called from signal handler or another thread.
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(self._stop.set)
        else:
            self._stop.set()

    def stop_sync(self) -> None:
        """Synchronous stop for non-async callers / tests."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._stop.set()
            return
        if loop.is_running():
            loop.call_soon_threadsafe(self._stop.set)
        else:
            self._stop.set()

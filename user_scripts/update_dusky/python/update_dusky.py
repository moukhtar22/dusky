#!/usr/bin/env python3
# ==============================================================================
#  DUSKY UPDATER (v9.7.5)
# ==============================================================================
import sys

if sys.version_info < (3, 14, 7):
    sys.stdout.write("\033[1;31m[FATAL]\033[0m Dusky requires Python 3.14.7+ bleeding-edge architecture.\n")
    sys.exit(1)

import argparse
import asyncio
import atexit
import base64
import codecs
import errno
import fcntl
import functools
import hashlib
import importlib
import importlib.metadata as importlib_metadata
import importlib.util
import json
import math
import os
import pty
import pwd
import re
import select
import shlex
import shutil
import signal
import site
import sqlite3
import stat
import struct
import subprocess
import tempfile
import termios
import threading
import time
import tomllib
import uuid
from collections import deque
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

VERSION = "9.7.5"
SCRIPT_DIR: Path = Path(__file__).resolve().parent
SCRIPT_PATH: Path = Path(__file__).resolve()
PROFILES_DIR: Path = Path(
    os.environ.get("DUSKY_UPDATER_PROFILES_DIR", SCRIPT_DIR / "profiles")
).resolve()


def global_config_context_path() -> Path:
    """Return the configured settings pathname even when it is currently absent."""
    custom_path = os.environ.get("DUSKY_UPDATER_SETTINGS")
    if custom_path:
        return Path(custom_path).expanduser().resolve(strict=False)
    return (PROFILES_DIR / "settings" / "update_dusky.toml").resolve(strict=False)


def global_config_path() -> Path | None:
    # An explicitly configured path is authoritative. Never silently fall back
    # to a different settings file merely because that path is temporarily
    # missing; creation/deletion of the configured file must remain observable.
    p = global_config_context_path()
    return p if p.is_file() else None


CONFIG_WARNINGS: list[str] = []
CONFIG_FATAL_ERRORS: list[str] = []


def _config_warn(message: str) -> None:
    if message not in CONFIG_WARNINGS:
        CONFIG_WARNINGS.append(message)


def _config_fatal(message: str) -> None:
    _config_warn(message)
    if message not in CONFIG_FATAL_ERRORS:
        CONFIG_FATAL_ERRORS.append(message)


def load_global_config() -> dict[str, Any]:
    p = global_config_path()
    if p and p.is_file():
        try:
            with open(p, "rb") as f:
                data = tomllib.load(f)
            if isinstance(data, dict):
                return data
            _config_warn(f"settings root is not a TOML table: {p}")
        except (OSError, tomllib.TOMLDecodeError) as e:
            _config_fatal(f"failed to parse settings {p}: {e}")
    return {}


def _normalize_global_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize every import-time setting before any consumer sees it.

    Invalid optional values fall back explicitly and are recorded in
    CONFIG_WARNINGS. Ambiguous destructive-path values are not silently
    redirected: they are replaced only with the documented safe default and a
    warning which later disables synchronization until acknowledged by the
    normal preflight.
    """
    defaults: dict[str, dict[str, Any]] = {
        "ui": {
            "ascii_mode": False, "sidebar_width": 35, "max_log_lines": 6000,
            "theme_paths": [".config/matugen/generated/dusky_tui.json", ".config/matugen/generated_fresh/dusky_tui.json"],
        },
        "paths": {
            "documents_dir": "Documents", "namespace": "dusky-updater", "lock_file": "lock",
            "askpass_prefix": ".dusky_askpass_", "logs_subdir": "logs", "backups_subdir": "dusky_backups",
            "state_subdir": "state", "log_retention_days": 14, "backup_retention_days": 14,
        },
        "logging": {"enabled": True, "write_task_logs": True, "write_reports": True},
        "execution": {
            "disk_min_free_mb": 100, "disk_copy_reserve_mb": 64, "db_busy_timeout": 5000,
            "default_interpreter": "bash", "default_task_timeout": 14400.0,
            "validate_subscript_syntax": False,
            "interactive_task_timeout": 21600.0, "prompt_wait_timeout": 900.0,
            "max_defer_passes": 3, "log_max_bytes": 8 * 1024 * 1024, "log_max_line_bytes": 256 * 1024,
        },
        "git": {
            "branch": "main", "repo_url": "https://github.com/dusklinux/dusky",
            "fetch_timeout": 60, "fetch_max_attempts": 5, "clone_timeout": 120, "clone_max_attempts": 5,
            "command_timeout": 300, "backup_timeout": 900,
            "env_strip": ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_LITERAL_PATHSPECS", "GIT_ASKPASS", "SSH_ASKPASS"],
            "env_inject": {"GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes", "GIT_PAGER": "cat", "PAGER": "cat", "GIT_OPTIONAL_LOCKS": "0"},
        },
        "conditions": {
            "package_check_cmd": ["pacman", "-Qq"],
            "service_active_cmd": ["systemctl", "is-active", "--quiet"],
            "user_service_active_cmd": ["systemctl", "--user", "is-active", "--quiet"],
        },
        "notifications": {
            "desktop_enabled": True, "app_name": "Dusky Updater", "audio_enabled": True,
            "audio_players": ["pw-play", "paplay"],
            "fallback_sound": "/usr/share/sounds/freedesktop/stereo/bell.oga",
        },
        "sudo": {
            "heartbeat_interval": 60, "dropin_prefix": "99_dusky_",
            "sudoers_dir": "/etc/sudoers.d", "timestamp_timeout": 15,
        },
        "prompts": {"cooldown": 0.35, "allow_insecure_password_autofeed": True},
    }
    cfg: dict[str, Any] = {}
    for table, dflt in defaults.items():
        value = raw.get(table, {})
        if value is None:
            value = {}
        if not isinstance(value, dict):
            _config_warn(f"[{table}] must be a table; using defaults")
            value = {}
        cfg[table] = dict(value)
        for key, default in dflt.items():
            cfg[table].setdefault(key, default)
    # Preserve additional well-formed tables used elsewhere.
    for key, value in raw.items():
        if key not in cfg:
            cfg[key] = value

    def boolean(table: str, key: str, default: bool) -> None:
        v = cfg[table].get(key, default)
        if not isinstance(v, bool):
            _config_warn(f"{table}.{key} must be boolean; using {default!r}")
            v = default
        cfg[table][key] = v

    def string(table: str, key: str, default: str, *, nonempty: bool = True, no_nul: bool = True) -> None:
        v = cfg[table].get(key, default)
        bad = not isinstance(v, str) or (nonempty and not v.strip()) or (no_nul and isinstance(v, str) and "\x00" in v)
        if bad:
            _config_warn(f"{table}.{key} must be a valid string; using {default!r}")
            v = default
        cfg[table][key] = v

    def integer(table: str, key: str, default: int, lo: int, hi: int) -> None:
        v = cfg[table].get(key, default)
        if isinstance(v, bool):
            v = default
        try:
            x = int(v)
        except (TypeError, ValueError, OverflowError):
            x = default
        if x < lo or x > hi:
            x = default
        if x != v:
            _config_warn(f"{table}.{key} invalid; using {x}")
        cfg[table][key] = x

    def finite_number(table: str, key: str, default: float, lo: float, hi: float) -> None:
        v = cfg[table].get(key, default)
        try:
            x = float(v)
        except (TypeError, ValueError, OverflowError):
            x = default
        if not math.isfinite(x) or x < lo or x > hi:
            x = default
        if x != v:
            _config_warn(f"{table}.{key} invalid; using {x:g}")
        cfg[table][key] = x

    for key, default in (("ascii_mode", False),): boolean("ui", key, default)
    integer("ui", "sidebar_width", 35, 15, 80)
    integer("ui", "max_log_lines", 6000, 100, 200000)
    for key, default in (("enabled", True), ("write_task_logs", True), ("write_reports", True)):
        boolean("logging", key, default)
    critical_path_keys = {"documents_dir", "backups_subdir", "state_subdir"}
    raw_paths = raw.get("paths", {}) if isinstance(raw.get("paths", {}), dict) else {}
    for key, default in (("documents_dir", "Documents"), ("namespace", "dusky-updater"), ("lock_file", "lock"),
                         ("askpass_prefix", ".dusky_askpass_"), ("logs_subdir", "logs"),
                         ("backups_subdir", "dusky_backups"), ("state_subdir", "state")):
        original = raw_paths.get(key, default)
        string("paths", key, default)
        if key in critical_path_keys and (not isinstance(original, str) or not original.strip() or "\x00" in original):
            _config_fatal(f"paths.{key} is invalid; destructive synchronization is disabled until settings are fixed")
    integer("paths", "log_retention_days", 14, 0, 36500)
    integer("paths", "backup_retention_days", 14, 0, 36500)
    integer("execution", "disk_min_free_mb", 100, 0, 1_000_000)
    integer("execution", "disk_copy_reserve_mb", 64, 0, 1_000_000)
    integer("execution", "db_busy_timeout", 5000, 1, 600_000)
    integer("execution", "max_defer_passes", 3, 1, 100)
    integer("execution", "log_max_bytes", 8 * 1024 * 1024, 64 * 1024, 1024 * 1024 * 1024)
    integer("execution", "log_max_line_bytes", 256 * 1024, 4096, 16 * 1024 * 1024)
    string("execution", "default_interpreter", "bash")
    boolean("execution", "validate_subscript_syntax", False)
    finite_number("execution", "default_task_timeout", 14400.0, 1.0, 7 * 24 * 3600.0)
    finite_number("execution", "interactive_task_timeout", 21600.0, 1.0, 7 * 24 * 3600.0)
    finite_number("execution", "prompt_wait_timeout", 900.0, 1.0, 24 * 3600.0)
    raw_git = raw.get("git", {}) if isinstance(raw.get("git", {}), dict) else {}
    raw_branch = raw_git.get("branch", "main")
    raw_repo = raw_git.get("repo_url", "https://github.com/dusklinux/dusky")
    string("git", "branch", "main")
    string("git", "repo_url", "https://github.com/dusklinux/dusky")
    if not isinstance(raw_branch, str) or not raw_branch.strip() or "\x00" in raw_branch:
        _config_fatal("git.branch is invalid; synchronization target is ambiguous")
    if not isinstance(raw_repo, str) or not raw_repo.strip() or "\x00" in raw_repo:
        _config_fatal("git.repo_url is invalid; synchronization source is ambiguous")
    integer("git", "fetch_timeout", 60, 1, 86400)
    integer("git", "clone_timeout", 120, 1, 86400)
    integer("git", "fetch_max_attempts", 5, 1, 100)
    integer("git", "clone_max_attempts", 5, 1, 100)
    integer("git", "command_timeout", 300, 1, 86400)
    integer("git", "backup_timeout", 900, 1, 86400)
    for key in ("package_check_cmd", "service_active_cmd", "user_service_active_cmd"):
        v = cfg["conditions"].get(key, defaults["conditions"][key])
        if not isinstance(v, list) or not v or any(not isinstance(x, str) or not x or "\x00" in x for x in v):
            _config_warn(f"conditions.{key} must be a non-empty command string array; using defaults")
            v = list(defaults["conditions"][key])
        cfg["conditions"][key] = v
    boolean("notifications", "desktop_enabled", True)
    boolean("notifications", "audio_enabled", True)
    string("notifications", "app_name", "Dusky Updater")
    string("notifications", "fallback_sound", "/usr/share/sounds/freedesktop/stereo/bell.oga")
    players = cfg["notifications"].get("audio_players", defaults["notifications"]["audio_players"])
    if not isinstance(players, list) or any(not isinstance(x, str) or not x or "\x00" in x for x in players):
        _config_warn("notifications.audio_players must be a string array; using defaults")
        players = list(defaults["notifications"]["audio_players"])
    cfg["notifications"]["audio_players"] = players
    integer("sudo", "heartbeat_interval", 60, 5, 3600)
    integer("sudo", "timestamp_timeout", 15, 0, 1440)
    string("sudo", "dropin_prefix", "99_dusky_")
    string("sudo", "sudoers_dir", "/etc/sudoers.d")
    finite_number("prompts", "cooldown", 0.35, 0.0, 60.0)
    boolean("prompts", "allow_insecure_password_autofeed", True)

    theme_paths = cfg["ui"].get("theme_paths", defaults["ui"]["theme_paths"])
    if not isinstance(theme_paths, list) or any(not isinstance(x, str) or not x.strip() or "\x00" in x for x in theme_paths):
        _config_warn("ui.theme_paths must be a list of path strings; using defaults")
        cfg["ui"]["theme_paths"] = list(defaults["ui"]["theme_paths"])

    ext = cfg["execution"].get("extension_interpreters", {".py": "python3", ".sh": "bash", ".fish": "fish"})
    if not isinstance(ext, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in ext.items()):
        _config_warn("execution.extension_interpreters must map suffix strings to interpreter strings; using defaults")
        ext = {".py": "python3", ".sh": "bash", ".fish": "fish"}
    cfg["execution"]["extension_interpreters"] = ext

    for key in ("env_strip",):
        v = cfg["git"].get(key, defaults["git"][key])
        if not isinstance(v, list) or any(not isinstance(x, str) or not x for x in v):
            _config_warn(f"git.{key} must be a list of strings; using defaults")
            v = list(defaults["git"][key])
        cfg["git"][key] = v
    inject = cfg["git"].get("env_inject", defaults["git"]["env_inject"])
    if not isinstance(inject, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in inject.items()):
        _config_warn("git.env_inject must map strings to strings; using defaults")
        inject = dict(defaults["git"]["env_inject"])
    cfg["git"]["env_inject"] = inject

    palette_defaults = {"bg": "#1a110e", "fg": "#f1dfd9", "accent": "#ffb59b", "warning": "#e7bdaf", "success": "#d5c68e", "muted": "#53433e", "error": "#ffb4ab"}
    palette = cfg["ui"].get("default_palette", palette_defaults)
    if not isinstance(palette, dict):
        _config_warn("ui.default_palette must be a table; using defaults")
        palette = dict(palette_defaults)
    else:
        palette = dict(palette)
        for key, default in palette_defaults.items():
            value = palette.get(key, default)
            if not isinstance(value, str) or re.fullmatch(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value) is None:
                _config_warn(f"ui.default_palette.{key} invalid; using {default}")
                value = default
            palette[key] = value
    cfg["ui"]["default_palette"] = palette

    rules = cfg["prompts"].get("rules")
    if rules is not None and not isinstance(rules, list):
        _config_warn("prompts.rules must be an array of rule tables; ignoring invalid value")
        cfg["prompts"].pop("rules", None)
    return cfg


RAW_GLOBAL_CONFIG = load_global_config()
GLOBAL_CONFIG = _normalize_global_config(RAW_GLOBAL_CONFIG)


def _cfg_table(key: str) -> dict[str, Any]:
    v = GLOBAL_CONFIG.get(key, {})
    return v if isinstance(v, dict) else {}


ASCII_MODE = _cfg_table("ui").get("ascii_mode", False) if isinstance(_cfg_table("ui").get("ascii_mode", False), bool) else False
try:
    DISK_MIN_FREE_MB = int(_cfg_table("execution").get("disk_min_free_mb", 100))
except (TypeError, ValueError):
    DISK_MIN_FREE_MB = 100
try:
    DISK_COPY_RESERVE_MB = int(_cfg_table("execution").get("disk_copy_reserve_mb", 64))
except (TypeError, ValueError):
    DISK_COPY_RESERVE_MB = 64
_namespc = _cfg_table("paths").get("namespace", "dusky-updater")
NAMESPACE = _namespc if isinstance(_namespc, str) and _namespc else "dusky-updater"


# ==============================================================================
#  PATH RESOLUTION UTILITIES
# ==============================================================================
def user_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and os.geteuid() == 0:
        with suppress(Exception):
            return Path(pwd.getpwnam(sudo_user).pw_dir)
    return Path.home()


WORK_TREE: Path = Path(os.environ.get("DUSKY_WORK_TREE", user_home())).resolve()
GIT_DIR: Path = Path(os.environ.get("DUSKY_GIT_DIR", WORK_TREE / "dusky")).resolve()


def documents_root() -> Path:
    try:
        _paths_tbl = GLOBAL_CONFIG.get("paths", {})
        if not isinstance(_paths_tbl, dict):
            _paths_tbl = {}
        raw = _paths_tbl.get("documents_dir", "Documents")
        if not isinstance(raw, str):
            raw = "Documents"
    except Exception:
        raw = "Documents"
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return user_home() / p


def _documents_subdir(key: str, default: str) -> Path:
    try:
        _paths_tbl2 = GLOBAL_CONFIG.get("paths", {})
        if not isinstance(_paths_tbl2, dict):
            _paths_tbl2 = {}
        raw = _paths_tbl2.get(key, default)
        if not isinstance(raw, str):
            raw = default
    except Exception:
        raw = default
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return documents_root() / p


def logs_dir() -> Path:
    return _documents_subdir("logs_subdir", "logs")


def backups_dir() -> Path:
    return _documents_subdir("backups_subdir", "dusky_backups")


def runtime_dir(ensure: bool = True) -> Path:
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    ns = GLOBAL_CONFIG.get("paths", {}).get("namespace", "dusky-updater")
    if not isinstance(ns, str) or not ns:
        ns = "dusky-updater"
    p = (Path(xdg_runtime) if xdg_runtime else Path(f"/tmp/{ns}-{os.getuid()}")) / ns
    if ensure:
        ensure_secure_dir(p)
    return p


def _runtime_dir_path() -> Path:
    return runtime_dir(ensure=False)



def supervisor_state_dir() -> Path | None:
    raw = os.environ.get("DUSKY_SUPERVISOR_STATE_DIR")
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return None

def state_dir(ensure: bool = True) -> Path:
    p = _documents_subdir("state_subdir", "state")
    if ensure:
        ensure_secure_dir(p)
    return p


def _state_dir_path() -> Path:
    return state_dir(ensure=False)


def ensure_secure_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            path.chmod(0o700)
        return path.is_dir()
    except OSError:
        return False


def lock_path() -> Path:
    lock_file = GLOBAL_CONFIG.get("paths", {}).get("lock_file", "lock")
    return runtime_dir() / lock_file

def version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in re.split(r"[^0-9]+", value.strip()):
        if part:
            parts.append(int(part))
    return tuple(parts)


def check_runtime_versions() -> None:
    if sys.version_info < (3, 14, 7):
        sys.stderr.write("\033[1;31m[FATAL]\033[0m Python 3.14.7+ is required.\n")
        sys.exit(1)
    try:
        textual_version = importlib_metadata.version("textual")
        parsed = (version_tuple(textual_version) + (0, 0, 0))[:3]
        if parsed < (8, 2, 8):
            sys.stderr.write(
                f"\033[1;31m[FATAL]\033[0m Textual 8.2.8+ is required. Installed: {textual_version}\n"
            )
            sys.exit(1)
    except Exception:
        pass


# NOTE: check_runtime_versions() is intentionally NOT called at import time so
# that `import update_dusky` is side-effect free for isolated testing. It is
# invoked from main() before any TUI-dependent work.


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fsync_directory(path: Path) -> None:
    """Durably publish directory-entry changes on Linux filesystems."""
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_regular_file(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Atomic file publication with file+parent fsync; never follows dest links."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_directory(path.parent)
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def _fsync_tree(path: Path) -> bool:
    """Make an already-created recovery payload durable before destructive work."""
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            _fsync_directory(path.parent)
            return True
        if stat.S_ISREG(st.st_mode):
            _fsync_regular_file(path)
            _fsync_directory(path.parent)
            return True
        if not stat.S_ISDIR(st.st_mode):
            return False
        for root, dirs, files in os.walk(path, topdown=False, followlinks=False):
            root_p = Path(root)
            for name in files:
                f = root_p / name
                try:
                    fst = f.lstat()
                    if stat.S_ISREG(fst.st_mode):
                        _fsync_regular_file(f)
                except OSError:
                    return False
            for name in dirs:
                d = root_p / name
                try:
                    if not stat.S_ISLNK(d.lstat().st_mode):
                        _fsync_directory(d)
                except OSError:
                    return False
            _fsync_directory(root_p)
        _fsync_directory(path.parent)
        return True
    except OSError:
        return False


def file_checksum(path: Path) -> str:
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _file_digest_or_none(path: Path | None) -> str | None:
    # None marks a missing file distinctly from any content hash, so
    # creation and removal are always detected as changes.
    if path is None:
        return None
    try:
        if not Path(path).is_file():
            return None
    except OSError:
        return None
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


@functools.cache
def target_user_pw() -> pwd.struct_passwd:
    if os.geteuid() == 0:
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and sudo_user != "root":
            with suppress(KeyError):
                return pwd.getpwnam(sudo_user)
        return pwd.getpwuid(0)
    return pwd.getpwuid(os.getuid())


def askpass_dir() -> Path:
    p = runtime_dir() / "askpass"
    ensure_secure_dir(p)
    return p

def S(key: str) -> str:
    ASCII_SYMBOLS = {
        "logo": "DUSKY", "completed": "OK", "running": "RUN", "failed": "ERR",
        "skipped": "SKIP", "pending": "...", "sep": "|", "report": "REP",
        "timing": "TIME", "git": "GIT", "matrix": "MAT", "preflight": "SYS"
    }
    UNICODE_SYMBOLS = {
        "logo": "◈", "completed": "✓", "running": "◉", "failed": "✗",
        "skipped": "-", "pending": "○", "sep": "│", "report": "◆",
        "timing": "⚡", "git": "⎇", "matrix": "⬢", "preflight": "⚙"
    }
    return ASCII_SYMBOLS.get(key, key) if ASCII_MODE else UNICODE_SYMBOLS.get(key, key)


# ==============================================================================
#  REGEX & AUTO-RESPONDER CONSTANTS
# ==============================================================================
_INTERACTIVE_RE = re.compile(
    r"^\s*#\s*dusky_interactive\s*=\s*(?:true|1)\b",
    re.IGNORECASE,
)
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
ANSI_STRIP_REGEX = re.compile(
    r"\x1B(?:[@-Z\\_-]|\[[0-?]*[ -/]*[@-~]|\][^\x1b]*(?:\x07|\x1B\\))"
)
PCT_REGEX = re.compile(r"(?<!\d)(?:100(?:\.0+)?|\d{1,2}(?:\.\d+)?)%")
SPEED_ETA_REGEX = re.compile(
    r"Total\s*\(\s*\d+\s*/\s*\d+\s*\).*?(\d+(?:\.\d+)?\s*[KMG]?i?B/s)\s+([\d:]+)",
    re.IGNORECASE,
)
ALT_SPEED_ETA_REGEX = re.compile(
    r"(\d+(?:\.\d+)?\s*[KMG]?i?B/s)\s+([\d:]+)",
    re.IGNORECASE,
)
BRACKET_NEWLINE_RE = re.compile(r"[\r\n]+")
SINGLE_NEWLINE_RE = re.compile(r"[\r\n]")


def _build_prompt_rules() -> list[tuple[str, re.Pattern[str], str]]:
    # Authentication is routed through sudo askpass, never by matching generic
    # "Password:" output from arbitrary children. Generic confirmation matching
    # is intentionally permissive: the built-in generic_yes rule is
    # case-insensitive, so both default-Yes forms such as [Y/n] and default-No
    # forms such as [y/N] are answered Yes. The shipped settings preserve that
    # policy; users can narrow or remove the generic rule through prompts.rules.
    default_rules = [
        ("sudo_password", r"(?i)(\[sudo\] password for [^:]+:|^\s*Password:\s*$|sudo: a password is required|Password:\s*$)", "password"),
        ("pgp_import", r"(?i)(::\s*Import PGP key.*\?\s*\[Y/n\]|::\s*Append key\?.*\[Y/n\]|Import PGP key.*\?\s*\[Y/n\])", "yes"),
        ("pacman_proceed", r"(?i)::\s*(Proceed with (?:installation|download|upgrade)|Continue (?:installation|download|upgrade)).*\?\s*\[Y/n\]", "yes"),
        ("pacman_replace", r"(?i)::\s*Replace\s+.*\?\s*\[Y/n\]", "yes"),
        ("pacman_remove_conflict", r"(?i)::\s*Remove conflicting file.*\?\s*\[Y/n\]", "yes"),
        ("aur_proceed", r"(?i)(Proceed with installation\?|Continue building\?|Continue installing\?|::\s*Proceed with (?:installation|download|build).*\?\s*\[Y/n\])", "yes"),
        # Intentionally answer Yes to generic Y/n and y/N confirmation forms.
        # The inline (?i) makes their casing variants equivalent.
        ("generic_yes", r"(?i)\[Y/n\]|\(Y/n\)", "yes"),
    ]
    config_rules = _cfg_table("prompts").get("rules", None)
    rules = []
    items_to_parse = config_rules if config_rules is not None else default_rules
    for item in items_to_parse:
        try:
            if isinstance(item, dict):
                name, pattern, kind = item["name"], item["pattern"], item["kind"]
            else:
                name, pattern, kind = item
            if kind not in ("password", "yes", "no"):
                continue
            rules.append((name, re.compile(pattern, re.MULTILINE), kind))
        except (KeyError, TypeError, re.error) as e:
            _config_warn(f"invalid prompts.rules entry ignored: {e}")
            continue
    return rules


PROMPT_RULES: list[tuple[str, re.Pattern[str], str]] = _build_prompt_rules()


# ==============================================================================
#  ADVANCED PRIVILEGE ESCALATION ENGINE (SUDOENGINE)
# ==============================================================================
class SudoEngine:
    _password: str | None = None
    _askpass_path: Path | None = None
    _sudoers_path: Path | None = None
    _mode: str = "none"  # none | root | nopasswd | password
    _registered_atexit: bool = False
    _last_cancelled: bool = False

    _ENV_KEEP_DEFAULT = [
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "PATH",
            "TERM",
            "COLORTERM",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TZ",
            "XDG_RUNTIME_DIR",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "XDG_DATA_HOME",
            "XDG_SESSION_TYPE",
            "XDG_CURRENT_DESKTOP",
            "DBUS_SESSION_BUS_ADDRESS",
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XAUTHORITY",
            "SSH_AUTH_SOCK",
            "SSH_AGENT_PID",
            "SUDO_ASKPASS",
            "PYTHONUNBUFFERED",
            "PYTHONUTF8",
            "PYTHONDONTWRITEBYTECODE",
            "PAGER",
            "SYSTEMD_PAGER",
            "GIT_PAGER",
            "EDITOR",
            "VISUAL",
            "QT_QPA_PLATFORMTHEME",
            "GTK_THEME",
            "XCURSOR_THEME",
            "XCURSOR_SIZE",
            "MOZ_ENABLE_WAYLAND",
            "LIBVA_DRIVER_NAME",
            "VDPAU_DRIVER",
            "SDL_VIDEODRIVER",
            "ZDOTDIR",
            "HYPRLAND_INSTANCE_SIGNATURE",
            "QT_QPA_PLATFORM",
            "XDG_SESSION_ID",
            "XDG_SEAT",
        ]
    try:
        _sudo_tbl = GLOBAL_CONFIG.get("sudo", {})
        if not isinstance(_sudo_tbl, dict):
            ENV_KEEP = _ENV_KEEP_DEFAULT
        else:
            ENV_KEEP = _sudo_tbl.get("env_keep", _ENV_KEEP_DEFAULT)
            if not isinstance(ENV_KEEP, list):
                ENV_KEEP = _ENV_KEEP_DEFAULT
    except Exception:
        ENV_KEEP = _ENV_KEEP_DEFAULT

    @classmethod
    def mode_name(cls) -> str:
        return cls._mode

    @classmethod
    def _remove_stale_askpass_files(cls) -> None:
        prefix = GLOBAL_CONFIG.get("paths", {}).get("askpass_prefix", ".dusky_askpass_")
        now = time.time()
        for d in (askpass_dir(), runtime_dir()):
            with suppress(OSError):
                for p in d.glob(f"{prefix}*"):
                    if p != cls._askpass_path:
                        with suppress(OSError):
                            if now - p.stat().st_mtime >= 300:
                                p.unlink(missing_ok=True)

    @classmethod
    def cleanup(cls) -> None:
        if cls._sudoers_path is not None:
            env = os.environ.copy()
            if cls._askpass_path is not None:
                env["SUDO_ASKPASS"] = str(cls._askpass_path)

            for cmd in (
                ["sudo", "-n", "rm", "-f", str(cls._sudoers_path)],
                ["sudo", "-A", "rm", "-f", str(cls._sudoers_path)],
            ):
                try:
                    res = subprocess.run(
                        cmd,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                    if res.returncode == 0:
                        break
                except Exception:
                    pass

        if cls._askpass_path is not None:
            # Remove only our own helper; never purge another live process's file.
            with suppress(OSError):
                try:
                    st = cls._askpass_path.lstat()
                    if stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid():
                        cls._askpass_path.unlink(missing_ok=True)
                except OSError:
                    pass

        cls._askpass_path = None
        cls._sudoers_path = None
        # Always clear cached credentials and owned environment entries.
        cls._password = None
        cls._mode = "none"
        with suppress(Exception):
            os.environ.pop("SUDO_ASKPASS", None)
        with suppress(Exception):
            os.environ.pop("DUSKY_SUDO_PASSWORD", None)

    @classmethod
    def _write_askpass(cls, password: str) -> Path:
        encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
        interpreter = sys.executable or shutil.which("python3") or "/usr/bin/env python3"
        script = (
            f"#!{interpreter}\n"
            "import base64, sys\n"
            f"sys.stdout.write(base64.b64decode('{encoded}').decode('utf-8'))\n"
            "sys.stdout.write('\\n')\n"
        )

        try:
            _pp2 = GLOBAL_CONFIG.get("paths", {})
            prefix = _pp2.get("askpass_prefix", ".dusky_askpass_") if isinstance(_pp2, dict) else ".dusky_askpass_"
        except Exception:
            prefix = ".dusky_askpass_"
        if not isinstance(prefix, str) or not prefix:
            prefix = ".dusky_askpass_"
        fd, path = tempfile.mkstemp(prefix=prefix, dir=str(askpass_dir()))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(path, 0o700)
        return Path(path)

    @classmethod
    def _remove_stale_sudoers_files(cls, env: dict[str, str]) -> None:
        prefix = GLOBAL_CONFIG.get("sudo", {}).get("dropin_prefix", "99_dusky_")
        sudoers_dir = GLOBAL_CONFIG.get("sudo", {}).get("sudoers_dir", "/etc/sudoers.d")
        if not isinstance(prefix, str) or not prefix or "/" in prefix or "\x00" in prefix:
            return
        if not isinstance(sudoers_dir, str) or not sudoers_dir or "\x00" in sudoers_dir:
            return
        # Quote config-derived paths; prefix restricted to safe filename chars.
        if re.search(r"[^A-Za-z0-9._-]", prefix):
            return
        script = f"""
for f in {shlex.quote(sudoers_dir + '/' + prefix)}*; do
    [ -f "$f" ] || continue
    pid=$(sed -n 's/^# pid=\\([0-9]*\\).*/\\1/p' "$f" | head -n1)
    expected_st=$(sed -n 's/.*starttime=\\([0-9]*\\).*/\\1/p' "$f" | head -n1)
    if [ -n "$pid" ]; then
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$f"
        elif [ -n "$expected_st" ] && [ -f "/proc/$pid/stat" ]; then
            real_st=$(cat "/proc/$pid/stat" 2>/dev/null | sed -E 's/^.*\\) //' | awk '{{print $20}}')
            if [ "$real_st" != "$expected_st" ]; then
                rm -f "$f"
            fi
        elif [ -f "/proc/$pid/cmdline" ] && ! grep -q -e "dusky" -e "update_dusky" -e "orchestrator" -e "python" "/proc/$pid/cmdline" 2>/dev/null; then
            rm -f "$f"
        fi
    fi
done
"""
        with suppress(Exception):
            subprocess.run(
                ["sudo", "-A", "sh"],
                input=script,
                text=True,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )

    @classmethod
    def _write_sudoers_dropin(cls, env: dict[str, str]) -> None:
        try:
            username = pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            return
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", username):
            return
        safe_user = username
        prefix = GLOBAL_CONFIG.get("sudo", {}).get("dropin_prefix", "99_dusky_")
        sudoers_dir = GLOBAL_CONFIG.get("sudo", {}).get("sudoers_dir", "/etc/sudoers.d")
        if not isinstance(prefix, str) or re.search(r"[^A-Za-z0-9._-]", prefix):
            return
        if not isinstance(sudoers_dir, str) or not sudoers_dir.startswith("/") or "\x00" in sudoers_dir:
            return
        path = Path(f"{sudoers_dir}/{prefix}{safe_user}_{os.getpid()}")
        # Validate config-derived values before use.
        raw_keep = cls.ENV_KEEP if isinstance(cls.ENV_KEEP, list) else []
        keep_vars = [v for v in raw_keep if isinstance(v, str) and re.fullmatch(r"[A-Z0-9_]+", v)]
        if not keep_vars:
            return
        env_vars = " ".join(keep_vars)
        try:
            timeout_raw = GLOBAL_CONFIG.get("sudo", {}).get("timestamp_timeout", 15)
            timeout = int(timeout_raw)
            if not (0 <= timeout <= 1440):
                return
        except (TypeError, ValueError):
            return

        start_time = "0"
        with suppress(OSError, IndexError):
            stat_text = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="ascii", errors="ignore")
            idx = stat_text.rfind(")")
            if idx != -1:
                start_time = stat_text[idx + 1:].split()[19]

        content = (
            f"# pid={os.getpid()} starttime={start_time} ts={int(time.time())}\n"
            f"Defaults:{username} timestamp_type=global, timestamp_timeout={timeout}\n"
            f"Defaults:{username} env_keep += \"{env_vars} DUSKY_*\"\n"
        )

        # Stage outside the include directory, validate first, then atomically
        # install. Never leave a live invalid file untracked in sudoers_dir.
        staged: Path | None = None
        try:
            stage_dir = runtime_dir()
            fd, tmp = tempfile.mkstemp(prefix="sudoers_stage_", dir=str(stage_dir))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.chmod(tmp, 0o600)
                staged = Path(tmp)
                check = subprocess.run(
                    ["visudo", "-c", "-q", "-f", str(staged)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                # Fall back to sudo visudo only if local visudo is unavailable;
                # still validate before any install into sudoers_dir.
                if check.returncode != 0:
                    check = subprocess.run(
                        ["sudo", "-A", "visudo", "-c", "-q", "-f", str(staged)],
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=10,
                    )
                if check.returncode != 0:
                    return
                install_cmd = (
                    f"umask 077 && cat {shlex.quote(str(staged))} > {shlex.quote(str(path))}.tmp"
                    f" && chmod 0440 {shlex.quote(str(path))}.tmp"
                    f" && visudo -c -q -f {shlex.quote(str(path))}.tmp"
                    f" && mv -f {shlex.quote(str(path))}.tmp {shlex.quote(str(path))};"
                    f" rc=$?; rm -f {shlex.quote(str(path))}.tmp; exit $rc"
                )
                # Use staged validation result; install atomically via mv only
                # after the temp file itself passed visudo. The trailing
                # `exit $rc` reports the install truthfully: a failed install
                # never masquerades as success via cleanup status.
                try:
                    proc = subprocess.run(
                        ["sudo", "-A", "sh", "-c", install_cmd],
                        input="",
                        text=True,
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=10,
                    )
                except subprocess.TimeoutExpired:
                    # Track and clean the actual installed file even on
                    # timeout: mv may have succeeded before the wait expired.
                    with suppress(Exception):
                        subprocess.run(
                            ["sudo", "-A", "rm", "-f", str(path)],
                            env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                        )
                    return
                except Exception:
                    return
                if proc.returncode != 0:
                    with suppress(Exception):
                        subprocess.run(
                            ["sudo", "-A", "rm", "-f", str(path)],
                            env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                        )
                    return
                # Track immediately so cleanup can remove it reliably.
                cls._sudoers_path = path
            finally:
                with suppress(OSError):
                    if staged is not None:
                        staged.unlink(missing_ok=True)
        except Exception:
            with suppress(Exception):
                if staged is not None:
                    staged.unlink(missing_ok=True)
            return

    @classmethod
    def set_password(cls, password: str) -> tuple[bool, str]:
        cls.cleanup()
        cls._remove_stale_askpass_files()

        try:
            askpass = cls._write_askpass(password)
        except OSError as e:
            return False, f"Failed to create askpass helper: {e}"

        env = os.environ.copy()
        env["SUDO_ASKPASS"] = str(askpass)

        try:
            proc = subprocess.run(
                ["sudo", "-A", "-v"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                askpass.unlink(missing_ok=True)
            cls._password = None
            cls._mode = "none"
            with suppress(Exception):
                os.environ.pop("SUDO_ASKPASS", None)
            return False, "sudo authentication timed out"
        except OSError as e:
            with suppress(OSError):
                askpass.unlink(missing_ok=True)
            cls._password = None
            cls._mode = "none"
            with suppress(Exception):
                os.environ.pop("SUDO_ASKPASS", None)
            return False, str(e)

        if proc.returncode == 0:
            cls._password = password
            cls._askpass_path = askpass
            cls._mode = "password"
            os.environ["SUDO_ASKPASS"] = str(askpass)
            if not cls._registered_atexit:
                atexit.register(cls.cleanup)
                cls._registered_atexit = True
            cls._remove_stale_sudoers_files(env)
            cls._write_sudoers_dropin(env)
            return True, ""

        err = (proc.stderr or "").strip()
        with suppress(OSError):
            askpass.unlink(missing_ok=True)
        cls._password = None
        cls._mode = "none"
        with suppress(Exception):
            os.environ.pop("SUDO_ASKPASS", None)
        return False, err or "sudo authentication failed"

    @classmethod
    def detect_nopasswd(cls) -> bool:
        if os.geteuid() == 0:
            cls._mode = "root"
            return True

        if not shutil.which("sudo"):
            return False

        # Permission to run `true` without a password does NOT imply
        # unrestricted NOPASSWD: ALL. Only report passwordless for the exact
        # probe; callers must not treat this as blanket authorization.
        with suppress(Exception):
            proc = subprocess.run(
                ["sudo", "-k", "-n", "true"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            if proc.returncode == 0:
                cls._password = None
                cls._askpass_path = None
                cls._sudoers_path = None
                cls._mode = "nopasswd"
                return True

        return False

    @classmethod
    def refresh_sync(cls) -> bool:
        if os.geteuid() == 0:
            cls._mode = "root"
            return True

        if not shutil.which("sudo"):
            return False

        if cls._mode == "nopasswd":
            cmd = ["sudo", "-n", "-v"]
            env = os.environ.copy()
        elif cls._mode == "password" and cls._askpass_path is not None:
            cmd = ["sudo", "-A", "-v"]
            env = os.environ.copy()
            env["SUDO_ASKPASS"] = str(cls._askpass_path)
        else:
            return cls.detect_nopasswd()

        try:
            proc = subprocess.run(
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            return proc.returncode == 0
        except Exception:
            return False

    @classmethod
    def sudo_prefix(cls) -> list[str]:
        if cls._mode == "root":
            return []
        if cls._mode == "nopasswd":
            return ["sudo", "-n", "--"]
        if cls._mode == "password" and cls._askpass_path is not None:
            return ["sudo", "-A", "--"]
        return ["sudo", "--"]

    @classmethod
    def preflight(
        cls,
        cli_password: str | None = None,
        password_file: Path | None = None,
    ) -> bool:
        cls._last_cancelled = False
        if os.geteuid() == 0:
            cls._mode = "root"
            sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Running as root. No sudo escalation needed.\n")
            return True

        if not shutil.which("sudo"):
            sys.stderr.write("\033[1;31m[FATAL]\033[0m sudo is required but not installed.\n")
            return False

        # Reuse an already-adopted password helper (restart handoff) without
        # re-prompting and without password bytes in env/args. Validates
        # location/ownership/type/mode and proves the helper still works via
        # `sudo -A -v` before accepting.
        if cls._mode == "password" and cls._askpass_path is not None:
            try:
                cand = Path(cls._askpass_path)
                st = cand.lstat()
                valid = (
                    stat.S_ISREG(st.st_mode)
                    and st.st_uid == os.getuid()
                    and stat.S_IMODE(st.st_mode) == 0o700
                )
                if valid:
                    try:
                        rt = runtime_dir(ensure=False)
                        cand_res = cand.resolve()
                        rt_res = rt.resolve()
                        if rt_res not in list(cand_res.parents) and cand_res != rt_res:
                            valid = False
                    except Exception:
                        valid = False
                if valid:
                    env = os.environ.copy()
                    env["SUDO_ASKPASS"] = str(cand)
                    try:
                        proc = subprocess.run(
                            ["sudo", "-A", "-v"],
                            env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=15,
                        )
                        if proc.returncode == 0:
                            os.environ["SUDO_ASKPASS"] = str(cand)
                            sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Reusing adopted sudo credentials (no re-prompt).\n")
                            return True
                    except (subprocess.SubprocessError, OSError):
                        pass
                    # Adopted helper invalid/expired: fall through to normal
                    # auth (do not return False yet, allow password/nopasswd).
                    with suppress(OSError):
                        pass
            except OSError:
                pass

        sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Securing administrative privileges...\n")

        password: str | None = cli_password
        if password is None:
            env_pwd = os.environ.get("DUSKY_SUDO_PASSWORD")
            if env_pwd:
                password = env_pwd
        if password is None and password_file is not None:
            with suppress(OSError):
                text = password_file.read_text(encoding="utf-8", errors="ignore")
                if text:
                    password = text.splitlines()[0].rstrip("\r\n")

        if password is not None:
            ok, err = cls.set_password(password)
            if ok:
                os.environ.pop("DUSKY_SUDO_PASSWORD", None)
                sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Sudo credentials cached for this session.\n")
                return True
            sys.stderr.write(f"\033[1;31m[ERROR]\033[0m Provided sudo password failed: {err}\n")

        if cls.detect_nopasswd():
            sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Passwordless sudo detected.\n")
            return True

        if sys.stdin.isatty():
            import getpass

            target_user = pwd.getpwuid(os.getuid()).pw_name
            for attempt in range(1, 4):
                try:
                    password = getpass.getpass(f"[sudo] password for {target_user}: ")
                except (EOFError, KeyboardInterrupt):
                    cls._last_cancelled = True
                    sys.stderr.write("\n\033[1;33m[CANCELLED]\033[0m Sudo authentication cancelled.\n")
                    return False

                ok, err = cls.set_password(password)
                if ok:
                    sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Sudo credentials cached for this session.\n")
                    return True
                sys.stderr.write(f"\033[1;31m[ERROR]\033[0m Authentication failed ({attempt}/3): {err}\n")

        if sys.stdin.isatty():
            sys.stderr.write("\033[1;31m[FATAL]\033[0m Sudo authentication failed after 3 attempts. Aborting before running privileged tasks.\n")
        else:
            sys.stderr.write("\033[1;31m[FATAL]\033[0m Sudo password required for privileged tasks (no TTY available to prompt).\n")
            sys.stderr.write("Provide your password via --sudo-password or DUSKY_SUDO_PASSWORD environment variable.\n")
        return False

    @staticmethod
    async def maintain_heartbeat(error_callback=None) -> None:
        fail_count = 0
        interval = GLOBAL_CONFIG.get("sudo", {}).get("heartbeat_interval", 45)
        try:
            while True:
                await asyncio.sleep(interval)
                ok = await asyncio.to_thread(SudoEngine.refresh_sync)
                if ok:
                    fail_count = 0
                else:
                    fail_count += 1
                    if error_callback is not None and fail_count == 1:
                        error_callback("Sudo heartbeat failed. Admin credentials may need renewal.")
        except asyncio.CancelledError:
            pass


# ==============================================================================
#  PERSISTENT STATE & IDEMPOTENCY STORAGE
# ==============================================================================
def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(name)).strip("._") or "unnamed"
    # Disambiguate sanitization collisions ("a/b" vs "a_b"): suffix with a
    # short hash of the full name so distinct profiles never share a state DB.
    digest = hashlib.blake2b(str(name).encode("utf-8"), digest_size=4).hexdigest()
    return f"{cleaned}_{digest}"


PERSISTENCE_WARNINGS: list[str] = []

def _persistence_warn(msg: str) -> None:
    if msg not in PERSISTENCE_WARNINGS:
        PERSISTENCE_WARNINGS.append(msg)


class StateStore:
    DONE = {
        "completed",
        "skipped",
        "ignored",
        "manual",
        "completed_once",
    }

    def __init__(self, profile: 'ProfileConfig', read_only: bool = False):
        self._read_only = read_only
        fp = getattr(profile, "filepath", None)
        try:
            ident = str(Path(fp).expanduser().resolve()) if fp else profile.name
        except OSError:
            ident = profile.name
        self.path = state_dir(ensure=not read_only and not OPT_DRY_RUN) / f"{safe_filename(ident)}.db"
        busy_timeout = GLOBAL_CONFIG.get("execution", {}).get("db_busy_timeout", 5000)

        if OPT_DRY_RUN or read_only:
            if self.path.is_file():
                self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False, timeout=busy_timeout / 1000.0)
            else:
                self.conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._ensure_schema()
        else:
            self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=busy_timeout / 1000.0)
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        if getattr(self, "_read_only", False) or OPT_DRY_RUN:
            return
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                state_key TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                script TEXT,
                checksum TEXT,
                exit_code INTEGER,
                note TEXT,
                updated TEXT,
                duration REAL DEFAULT 0.0
            )
            """
        )
        cur = self.conn.execute("PRAGMA table_info(state);")
        columns = [row[1] for row in cur.fetchall()]
        if "duration" not in columns:
            self.conn.execute("ALTER TABLE state ADD COLUMN duration REAL DEFAULT 0.0;")
        self.conn.commit()

    def statuses(self) -> dict[str, str]:
        try:
            cur = self.conn.execute("SELECT state_key, status FROM state")
            return {str(k): str(v) for k, v in cur.fetchall()}
        except sqlite3.OperationalError:
            return {}

    def durations(self) -> dict[str, float]:
        try:
            cur = self.conn.execute("PRAGMA table_info(state);")
            if "duration" not in [row[1] for row in cur.fetchall()]:
                return {}
            cur = self.conn.execute("SELECT state_key, duration FROM state")
            return {str(k): float(v or 0.0) for k, v in cur.fetchall()}
        except sqlite3.OperationalError:
            return {}

    def mark(
        self,
        task: 'DuskyTask',
        status: str,
        exit_code: int | None = None,
        note: str = "",
        duration: float = 0.0,
    ) -> None:
        if getattr(self, "_read_only", False) or OPT_DRY_RUN:
            return
        try:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO state
                    (state_key, status, script, checksum, exit_code, note, updated, duration)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task.state_key, status, task.name, task.checksum, exit_code, note, now_iso(), duration),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            _persistence_warn(f"state write failed for {task.name}: {e}")

    def reset(self) -> None:
        with suppress(Exception):
            self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.path}{suffix}").unlink(missing_ok=True)

    def close(self) -> None:
        with suppress(Exception):
            self.conn.close()


class OnceStore:
    def __init__(self, read_only: bool = False) -> None:
        self._read_only = read_only
        self.path = state_dir(ensure=not read_only and not OPT_DRY_RUN) / "once.db"
        busy_timeout = GLOBAL_CONFIG.get("execution", {}).get("db_busy_timeout", 5000)

        if OPT_DRY_RUN or read_only:
            if self.path.is_file():
                self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False, timeout=busy_timeout / 1000.0)
            else:
                self.conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._ensure_schema()
        else:
            self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=busy_timeout / 1000.0)
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        if getattr(self, "_read_only", False) or OPT_DRY_RUN:
            return
        self.conn.execute(
            """
CREATE TABLE IF NOT EXISTS once_markers (
    marker_key TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    scope TEXT NOT NULL,
    mode TEXT NOT NULL,
    script_name TEXT NOT NULL,
    args_key TEXT NOT NULL,
    resolved_path TEXT,
    checksum TEXT,
    once_mode TEXT NOT NULL,
    exit_code INTEGER,
    run_id TEXT,
    version TEXT,
    created TEXT,
    updated TEXT
)
"""
        )
        with suppress(sqlite3.OperationalError):
            self.conn.execute("ALTER TABLE once_markers ADD COLUMN notified_checksum TEXT DEFAULT '';")

        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_once_script ON once_markers(script_name);"
        )
        self.conn.commit()
        self._migrate_keys()

    def _migrate_keys(self) -> None:
        if getattr(self, "_read_only", False) or OPT_DRY_RUN:
            return
        try:
            cur = self.conn.execute("SELECT marker_key, profile, scope, mode, script_name, args_key, resolved_path FROM once_markers")
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            return

        updates = []
        for row in rows:
            old_key, profile, scope, mode, script_name, args_key, resolved_path = row
            rel_path = ""
            if resolved_path:
                try:
                    rel_path = str(Path(resolved_path).relative_to(WORK_TREE))
                except ValueError:
                    rel_path = str(resolved_path)

            profile_part = "__global__" if scope == "global" else profile
            material = "|".join([
                "once", scope, profile_part, mode, script_name, rel_path, args_key
            ]).encode("utf-8")
            new_key = hashlib.blake2b(material, digest_size=16).hexdigest()

            if new_key != old_key:
                updates.append((new_key, old_key))

        if updates:
            for new_k, old_k in updates:
                with suppress(sqlite3.IntegrityError, sqlite3.OperationalError):
                    self.conn.execute("UPDATE once_markers SET marker_key = ? WHERE marker_key = ?", (new_k, old_k))
            self.conn.commit()

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def forget(self, script: str) -> int:
        script = script.strip()
        if not script:
            return 0

        try:
            esc = self._escape_like(script)
            cur = self.conn.execute(
                """
DELETE FROM once_markers
WHERE script_name = ?
   OR resolved_path = ?
   OR script_name LIKE ? ESCAPE '\\'
""",
                (script, script, f"%/{esc}"),
            )
            self.conn.commit()
            return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        except sqlite3.OperationalError:
            return 0

    @staticmethod
    def make_key(task: 'DuskyTask', profile_name: str) -> str:
        scope = task.once_scope if task.once_scope in ("profile", "global") else "profile"
        profile_part = "__global__" if scope == "global" else profile_name

        try:
            rel_path = str(task.resolved_path.relative_to(WORK_TREE)) if task.resolved_path else ""
        except ValueError:
            rel_path = str(task.resolved_path)

        material = "|".join(
            [
                "once",
                scope,
                profile_part,
                task.mode,
                task.name,
                rel_path,
                shlex.join(task.args),
            ]
        ).encode("utf-8")
        return hashlib.blake2b(material, digest_size=16).hexdigest()

    def check_marker_status(self, task: 'DuskyTask', profile_name: str) -> Literal["run", "skip", "notify_sealed", "error"]:
        if not task.once:
            return "run"

        key = self.make_key(task, profile_name)
        try:
            cur = self.conn.execute(
                "SELECT checksum, once_mode, notified_checksum FROM once_markers WHERE marker_key = ?",
                (key,),
            )
            row = cur.fetchone()
        except sqlite3.Error as e:
            _persistence_warn(f"once-state read failed for {task.name}: {e}")
            return "error"
        if row is None:
            return "run"

        stored_checksum, stored_mode, notified_checksum = row

        if task.once_mode == "forever" or stored_mode == "forever":
            return "skip"

        if task.once_mode == "sealed" or stored_mode == "sealed":
            if bool(task.checksum) and stored_checksum != task.checksum:
                if notified_checksum != task.checksum:
                    return "notify_sealed"
            return "skip"

        if bool(task.checksum) and stored_checksum == task.checksum:
            return "skip"

        return "run"

    def mark_sealed_notified(self, task: 'DuskyTask', profile_name: str) -> None:
        if getattr(self, "_read_only", False) or OPT_DRY_RUN:
            return
        key = self.make_key(task, profile_name)
        try:
            self.conn.execute(
                "UPDATE once_markers SET notified_checksum = ?, updated = ? WHERE marker_key = ?",
                (task.checksum, now_iso(), key)
            )
            self.conn.commit()
        except sqlite3.Error as e:
            _persistence_warn(f"once-state notification write failed for {task.name}: {e}")

    def mark_success(
        self,
        task: 'DuskyTask',
        profile_name: str,
        exit_code: int | None = None,
        run_id: str = "",
    ) -> None:
        if not task.once:
            return
        if getattr(self, "_read_only", False) or OPT_DRY_RUN:
            return

        key = self.make_key(task, profile_name)
        args_key = shlex.join(task.args)

        try:
            self.conn.execute(
                """
INSERT INTO once_markers (
    marker_key, profile, scope, mode, script_name, args_key,
    resolved_path, checksum, once_mode, exit_code, run_id,
    version, created, updated
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(marker_key) DO UPDATE SET
    profile=excluded.profile, scope=excluded.scope, mode=excluded.mode,
    script_name=excluded.script_name, args_key=excluded.args_key,
    resolved_path=excluded.resolved_path, checksum=excluded.checksum,
    once_mode=excluded.once_mode, exit_code=excluded.exit_code,
    run_id=excluded.run_id, version=excluded.version, updated=excluded.updated
""",
                (
                    key, profile_name, task.once_scope, task.mode, task.name,
                    args_key, str(task.resolved_path), task.checksum,
                    task.once_mode, exit_code, run_id, VERSION, now_iso(), now_iso(),
                ),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            _persistence_warn(f"once-state success write failed for {task.name}: {e}")

    def list_markers(self) -> list[dict[str, object]]:
        try:
            cur = self.conn.execute(
                "SELECT profile, scope, mode, script_name, args_key, resolved_path, checksum, once_mode, exit_code, run_id, updated FROM once_markers ORDER BY profile, script_name, args_key"
            )
        except sqlite3.OperationalError:
            return []
        rows: list[dict[str, object]] = []
        for row in cur.fetchall():
            rows.append(
                {"profile": row[0], "scope": row[1], "mode": row[2], "script_name": row[3], "args_key": row[4], "resolved_path": row[5], "checksum": row[6], "once_mode": row[7], "exit_code": row[8], "run_id": row[9], "updated": row[10]}
            )
        return rows

    def print_list(self) -> None:
        rows = self.list_markers()
        if not rows:
            print("No persistent once markers found.")
            return
        print(f"Persistent once markers ({len(rows)}):")
        for i, row in enumerate(rows, start=1):
            print(f"{i:3d}. [{row['mode']}] {row['script_name']}\n     profile:   {row['profile']}\n     scope:     {row['scope']}\n     args:      {row['args_key']}\n     path:      {row['resolved_path']}\n     mode:      {row['once_mode']}\n     checksum:  {row['checksum']}\n     exit_code: {row['exit_code']}\n     run_id:    {row['run_id']}\n     updated:   {row['updated']}\n")

    def close(self) -> None:
        with suppress(Exception):
            self.conn.close()

# ==============================================================================
#  CONDITION EVALUATOR & TASK RUN LOGGER
# ==============================================================================
class ConditionEvaluator:
    IMMUTABLE = {
        "wayland",
        "x11",
        "graphical",
        "ssh",
        "desktop",
        "battery",
        "btrfs",
        "vm",
        "baremetal",
        "gpu",
    }
    KNOWN = {
        "wayland", "x11", "graphical", "ssh", "desktop", "battery", "btrfs",
        "vm", "baremetal", "command", "cmd", "path", "missing", "file", "dir",
        "package", "pkg", "group", "gpu", "service_active", "service", "svc",
        "user_service_active", "user_service", "user_svc", "env",
        "always", "true", "yes", "never", "false", "no",
    }

    def __init__(self):
        self.cache: dict[str, bool] = {}

    def is_known(self, condition: str | None) -> bool:
        """Unknown conditions (typos) are never silently true via not:."""
        if not condition:
            return True
        cond = condition.strip()
        if "," in cond:
            return all(self.is_known(p) for p in cond.split(",") if p.strip())
        kind, _, value = cond.partition(":")
        kind = kind.strip().lower()
        value = value.strip()
        if kind == "not":
            return self.is_known(value) if value else False
        if kind in ("always", "true", "yes", "never", "false", "no"):
            return True
        return kind in self.KNOWN

    def _volatile(self, condition: str | None) -> bool:
        if not condition:
            return False
        cond = condition.strip()
        if "," in cond:
            # A compound condition is volatile if ANY of its AND'ed parts is
            # volatile (e.g. "gpu:nvidia,command:sddm" must re-check sddm each
            # pass so an earlier task can install it mid-run).
            return any(self._volatile(part) for part in cond.split(","))
        if cond.lower() in ("always", "true", "yes", "never", "false", "no"):
            return False

        kind, _, value = cond.partition(":")
        kind = kind.strip().lower()
        value = value.strip()

        if kind == "not":
            return self._volatile(value)
        return kind not in self.IMMUTABLE

    def check(self, condition: str | None) -> bool:
        if not condition:
            return True

        cond = condition.strip()
        if cond.lower() in ("always", "true", "yes"):
            return True
        if cond.lower() in ("never", "false", "no"):
            return False

        if self._volatile(cond):
            return self._eval(cond)

        if cond in self.cache:
            return self.cache[cond]

        result = self._eval(cond)
        self.cache[cond] = result
        return result

    def _eval(self, cond: str) -> bool:
        if "," in cond:
            # Commas are a strict AND separator between sub-conditions. Values
            # are comma-free (see documented DSL contract), so NO token merging.
            parts: list[str] = [p.strip() for p in cond.split(",") if p.strip()]
            if len(parts) > 1:
                return all(self.check(part) for part in parts)
            if parts:
                cond = parts[0]
            else:
                return True

        kind, _, value = cond.partition(":")
        kind = kind.strip().lower()
        value = value.strip()

        if kind == "not":
            # Unknown inner conditions must not become true via negation.
            if not value or not self.is_known(value):
                return False
            return not self.check(value)

        if kind == "wayland":
            return bool(os.environ.get("WAYLAND_DISPLAY"))
        if kind == "x11":
            return bool(os.environ.get("DISPLAY"))
        if kind == "graphical":
            return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
        if kind == "ssh":
            return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
        if kind == "desktop":
            session = os.environ.get("XDG_SESSION_TYPE", "").lower()
            if session in ("wayland", "x11", "mir"):
                return True
            return self.check("graphical") and not self.check("ssh")

        if kind == "battery":
            return self._has_battery()
        if kind == "btrfs":
            return self._root_is_btrfs()
        if kind == "vm":
            return self._is_vm()
        if kind == "baremetal":
            return not self._is_vm()

        if kind in ("command", "cmd"):
            return bool(shutil.which(value))
        if kind == "path":
            return Path(value).expanduser().exists()
        if kind == "missing":
            return not Path(value).expanduser().exists()
        if kind == "file":
            return Path(value).expanduser().is_file()
        if kind == "dir":
            return Path(value).expanduser().is_dir()

        if kind in ("package", "pkg"):
            return self._package_installed(value)
        if kind == "group":
            return self._user_in_group(value)
        if kind == "gpu":
            return self._gpu(value.lower())

        if kind in ("service_active", "service", "svc"):
            cmd = GLOBAL_CONFIG.get("conditions", {}).get(
                "service_active_cmd",
                ["systemctl", "is-active", "--quiet"],
            )
            return self._run(cmd + [value])
        if kind in ("user_service_active", "user_service", "user_svc"):
            cmd = GLOBAL_CONFIG.get("conditions", {}).get(
                "user_service_active_cmd",
                ["systemctl", "--user", "is-active", "--quiet"],
            )
            return self._run(cmd + [value])

        if kind == "env":
            return bool(os.environ.get(value))

        return False

    def _run(self, cmd: list[str]) -> bool:
        with suppress(Exception):
            return subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).returncode == 0
        return False

    def _output(self, cmd: list[str]) -> str:
        with suppress(Exception):
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        return ""

    def _has_battery(self) -> bool:
        base = Path("/sys/class/power_supply")
        if not base.exists():
            return False
        with suppress(OSError):
            for entry in base.iterdir():
                type_file = entry / "type"
                if type_file.exists():
                    if type_file.read_text(errors="ignore").strip() == "Battery":
                        return True
        return False

    def _root_is_btrfs(self) -> bool:
        with suppress(OSError):
            for line in Path("/proc/mounts").read_text(errors="ignore").splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "/" and parts[2] == "btrfs":
                    return True
        return False

    def _is_vm(self) -> bool:
        if shutil.which("systemd-detect-virt"):
            with suppress(Exception):
                proc = subprocess.run(
                    ["systemd-detect-virt", "--vm", "--quiet"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                return proc.returncode == 0

        dmi = Path("/sys/class/dmi/id/sys_vendor")
        if dmi.exists():
            with suppress(OSError):
                vendor = dmi.read_text(errors="ignore").lower()
                return any(x in vendor for x in ("qemu", "kvm", "vmware", "virtualbox", "bochs"))

        return False

    def _package_installed(self, name: str) -> bool:
        pkg_cmd = GLOBAL_CONFIG.get("conditions", {}).get(
            "package_check_cmd",
            ["pacman", "-Qq"],
        )
        if not pkg_cmd or not shutil.which(pkg_cmd[0]):
            return False
        return self._run(pkg_cmd + [name])

    def _user_in_group(self, group: str) -> bool:
        user = target_user_pw().pw_name
        groups = self._output(["id", "-nG", user])
        return group in groups.split()

    def _gpu(self, kind: str) -> bool:
        if kind == "nvidia" and Path("/sys/module/nvidia").exists():
            return True
        if kind == "intel" and (Path("/sys/module/i915").exists() or Path("/sys/module/xe").exists()):
            return True
        if kind == "amd" and (Path("/sys/module/amdgpu").exists() or Path("/sys/module/radeon").exists()):
            return True

        drm_path = Path("/sys/class/drm")
        if drm_path.exists():
            vendor_map = GLOBAL_CONFIG.get(
                "conditions",
                {},
            ).get(
                "gpu_vendor_map",
                {
                    "nvidia": "0x10de",
                    "intel": "0x8086",
                    "amd": "0x1002",
                    "vmware": "0x15ad",
                    "virtio": "0x1af4",
                },
            )
            target_vendor = vendor_map.get(kind)
            if target_vendor:
                with suppress(OSError):
                    for card in drm_path.glob("card[0-9]*"):
                        device_dir = card / "device"
                        if not device_dir.exists():
                            continue
                        driver_link = device_dir / "driver"
                        if driver_link.exists():
                            with suppress(OSError):
                                if driver_link.resolve().name == "simpledrm":
                                    continue
                        vendor_file = device_dir / "vendor"
                        if vendor_file.exists():
                            if vendor_file.read_text(encoding="utf-8").strip().lower() == target_vendor:
                                return True

        if kind == "nvidia":
            return self._lspci_vga("nvidia")
        if kind == "intel":
            return self._lspci_vga("intel")
        if kind == "amd":
            return (
                self._lspci_vga("amd")
                or self._lspci_vga("ati")
                or self._lspci_vga("radeon")
                or self._lspci_vga("advanced micro devices")
            )
        if kind in ("vmware", "virtio", "qemu"):
            return self._lspci_vga(kind)
        return False

    def _lspci_vga(self, needle: str) -> bool:
        if not shutil.which("lspci"):
            return False
        out = self._output(["lspci"])
        needle_lower = needle.lower()
        pattern = re.compile(rf"\b{re.escape(needle_lower)}\b", re.IGNORECASE)
        for line in out.splitlines():
            line_lower = line.lower()
            if any(ctrl in line_lower for ctrl in ("vga", "3d", "display")):
                if pattern.search(line_lower):
                    return True
        return False


class RunLogger:
    def __init__(self, profile: 'ProfileConfig', run_id: str):
        log_config = GLOBAL_CONFIG.get("logging", {})
        self.enabled = log_config.get("enabled", True)
        self.write_task_logs = log_config.get("write_task_logs", True)
        self.write_reports = log_config.get("write_reports", True)

        if OPT_DRY_RUN:
            self.enabled = False

        self.root: Path | None = None
        self.main_path: Path | None = None
        self._main = None
        self._task_handles: dict[int, Any] = {}
        self.run_id = run_id
        self.warning_source: list[dict[str, str]] | None = None
        self.git_summary_source: dict[str, Any] | None = None

        if not self.enabled:
            return

        try:
            self.root = logs_dir() / f"{run_id}_{safe_filename(profile.name)}_{run_id}"
            ensure_secure_dir(self.root)
            self.main_path = self.root / "dusky_update.log"
            self._main = open(self.main_path, "a", encoding="utf-8", errors="replace")
            self.system(f"Logging started for profile: {profile.name}")
            self.system(f"Run ID: {run_id}")
            self.system(f"Python: {sys.version.split()[0]} | User: {user_home().name} | Kernel: {os.uname().release}")
        except OSError as e:
            sys.stderr.write(f"[WARN] Cannot create task log directory under {logs_dir()}: {e}\n")

    def system(self, msg: str) -> None:
        if not self.enabled or self._main is None:
            return
        with suppress(OSError):
            self._main.write(f"[{now_ts()}] {msg}\n")
            self._main.flush()

    def task_log_path(self, task: 'DuskyTask', index: int) -> Path:
        if self.root is None:
            return Path("/dev/null")
        return self.root / f"{index:03d}_{safe_filename(task.name)}.log"

    def write_task(self, task: 'DuskyTask', index: int, text: str) -> None:
        # Buffered per-task log writes: one open handle per task, flushed on
        # close_task/close_all — never an open() per output line. Order is
        # preserved (single-threaded appends) and flushed on completion.
        if not self.enabled or not self.write_task_logs or self.root is None:
            return
        handle = self._task_handles.get(index)
        if handle is None:
            log_path = self.task_log_path(task, index)
            try:
                handle = open(log_path, "a", encoding="utf-8", errors="replace")
                self._task_handles[index] = handle
            except OSError:
                return
        try:
            if not text.endswith("\n"):
                text += "\n"
            handle.write(text)
        except OSError:
            with suppress(Exception):
                handle.close()
            self._task_handles.pop(index, None)

    def close_task(
        self,
        task: 'DuskyTask',
        index: int,
        status: str = "",
        exit_code: int | None = None,
        duration: float = 0.0,
    ) -> None:
        if not self.enabled or not self.write_task_logs or self.root is None:
            return
        outcome = getattr(task, "outcome", status) or status
        reason = getattr(task, "reason", "")
        attempts = getattr(task, "attempts", 0)
        handle = self._task_handles.pop(index, None)
        if handle is None:
            log_path = self.task_log_path(task, index)
            try:
                handle = open(log_path, "a", encoding="utf-8", errors="replace")
            except OSError:
                return
        with suppress(OSError):
            handle.write(f"\n[{now_ts()}] TASK END: {task.name}\n")
            handle.write(f"[{now_ts()}] STATUS: {status}\n")
            handle.write(f"[{now_ts()}] OUTCOME: {outcome}\n")
            if reason:
                handle.write(f"[{now_ts()}] REASON: {reason}\n")
            if exit_code is not None:
                handle.write(f"[{now_ts()}] EXIT CODE: {exit_code}\n")
            handle.write(f"[{now_ts()}] ATTEMPTS: {attempts}\n")
            handle.write(f"[{now_ts()}] DURATION: {duration:.2f}s\n")
            handle.flush()
            handle.close()

    def write_report(
        self,
        profile: 'ProfileConfig',
        tasks: list,
        statuses: dict[str, str] | None = None,
        counters: dict[str, int] | None = None,
    ) -> None:
        if not self.enabled or not self.write_reports or self.root is None:
            return

        cnt = counters or {}
        report = {
            "run_id": self.run_id,
            "generated": now_iso(),
            "profile": profile.name,
            "profile_file": str(profile.filepath),
            "version": VERSION,
            "python": sys.version,
            "user": target_user_pw().pw_name,
            "uid": target_user_pw().pw_uid,
            "home": str(user_home()),
            "counters": cnt,
            "git_summary": dict(self.git_summary_source or {}),
            "warnings": list(self.warning_source or []),
            "tasks": [],
        }

        lines = [
            "# Dusky Update Report",
            "",
            f"- Run ID: `{self.run_id}`",
            f"- Generated: `{now_iso()}`",
            f"- Profile: `{profile.name}`",
            f"- Version: `{VERSION}`",
            "",
            "## Counters",
            "",
        ]

        for k, v in sorted(cnt.items()):
            lines.append(f"- {k}: {v}")

        warnings = list(self.warning_source or [])
        if warnings:
            lines.extend(["", "## Warnings", ""])
            for w in warnings:
                detail = w.get("message", "")
                suffix = ""
                if w.get("task"):
                    suffix += f" task={w['task']}"
                if w.get("path"):
                    suffix += f" path={w['path']}"
                if w.get("recovery"):
                    suffix += f" recovery={w['recovery']}"
                lines.append(f"- {w.get('kind', 'warning')}: {detail}{suffix}")
        lines.extend(["", "## Git Summary", "", "```json", json.dumps(self.git_summary_source or {}, indent=2, default=str), "```", "", "## Tasks", ""])

        for task in tasks:
            st = getattr(task, "status", None)
            if not st and statuses:
                st = statuses.get(task.state_key, "pending")
            st = st or "pending"
            dur = getattr(task, "duration", 0.0)
            item = {
                "script": task.name,
                "mode": task.mode,
                "status": st,
                "outcome": getattr(task, "outcome", st),
                "reason": getattr(task, "reason", ""),
                "exit_code": getattr(task, "exit_code", None),
                "attempts": getattr(task, "attempts", 0),
                "path": str(task.resolved_path) if getattr(task, "resolved_path", None) else "",
                "args": task.args,
                "duration": round(dur, 2),
            }
            report["tasks"].append(item)
            dur_str = f" ({dur:.2f}s)" if dur > 0 else ""
            outcome_str = f"/{item['outcome']}" if item["outcome"] != st else ""
            reason_str = f" [{item['reason']}]" if item["reason"] else ""
            lines.append(f"- [{task.mode}] {task.name} -> {st}{outcome_str}{dur_str}{reason_str}")

        with suppress(OSError):
            (self.root / "report.json").write_text(
                json.dumps(report, indent=2, default=str),
                encoding="utf-8",
            )
            (self.root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def close(self) -> None:
        self.close_all()

    def close_all(self) -> None:
        if not self.enabled:
            return

        for handle in list(self._task_handles.values()):
            with suppress(OSError):
                handle.flush()
                handle.close()
        self._task_handles = {}
        if self._main is not None:
            with suppress(OSError):
                self.system("Logging stopped.")
                self._main.flush()
                self._main.close()
            self._main = None


# ==============================================================================
#  NOTIFICATIONS & POWER MANAGEMENT INHIBITOR
# ==============================================================================
class AudioNotifier:
    enabled = True
    _procs: list = []

    @classmethod
    @functools.cache
    def _get_player(cls) -> str | None:
        players = GLOBAL_CONFIG.get("notifications", {}).get("audio_players", ["pw-play", "paplay"])
        for bin_name in players:
            if p := shutil.which(bin_name):
                return p
        return None

    @classmethod
    def play(cls, sound_type: str = "alert") -> None:
        if not cls.enabled or not GLOBAL_CONFIG.get("notifications", {}).get("audio_enabled", True):
            return

        player = cls._get_player()
        if not player:
            return

        sound_map = GLOBAL_CONFIG.get(
            "notifications",
            {},
        ).get(
            "sound_map",
            {
                "alert": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
                "info": "/usr/share/sounds/freedesktop/stereo/dialog-information.oga",
                "complete": "/usr/share/sounds/freedesktop/stereo/complete.oga",
            },
        )
        target = Path(sound_map.get(sound_type, sound_map.get("alert", "")))
        if not target.exists():
            fallback_sound = GLOBAL_CONFIG.get(
                "notifications",
                {},
            ).get("fallback_sound", "/usr/share/sounds/freedesktop/stereo/bell.oga")
            fallback = Path(fallback_sound)
            if fallback.exists():
                target = fallback
            else:
                return

        cmd = (
            [player, "--media-role=event", str(target)]
            if player.endswith("pw-play")
            else [player, str(target)]
        )

        with suppress(OSError):
            proc = subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            cls._procs.append(proc)
            # Reap finished players; never accumulate zombies.
            alive = []
            for p in cls._procs:
                if p.poll() is None:
                    alive.append(p)
            cls._procs = alive[-8:]

    @classmethod
    def reap(cls) -> None:
        for p in cls._procs:
            with suppress(Exception):
                p.wait(timeout=1)
        cls._procs = []


class DesktopNotifier:
    enabled = True
    _procs: list = []

    @classmethod
    def notify(cls, title: str, body: str, urgency: str = "normal") -> None:
        if not cls.enabled or not GLOBAL_CONFIG.get("notifications", {}).get("desktop_enabled", True):
            return
        if not shutil.which("notify-send"):
            return
        app_name = GLOBAL_CONFIG.get("notifications", {}).get("app_name", "Dusky Updater")
        with suppress(OSError):
            proc = subprocess.Popen(
                [
                    "notify-send",
                    f"--app-name={app_name}",
                    f"--urgency={urgency}",
                    title,
                    body,
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            cls._procs.append(proc)
            alive = []
            for p in cls._procs:
                if p.poll() is None:
                    alive.append(p)
            cls._procs = alive[-8:]

    @classmethod
    def reap(cls) -> None:
        for p in cls._procs:
            with suppress(Exception):
                p.wait(timeout=1)
        cls._procs = []


class SleepInhibitor:
    def __init__(self, enabled: bool = True):
        self.proc = None
        if not enabled:
            return
        if not shutil.which("systemd-inhibit") or not shutil.which("sleep"):
            return

        with suppress(OSError):
            self.proc = subprocess.Popen(
                [
                    "systemd-inhibit",
                    "--what=idle:sleep",
                    "--who=Dusky Updater",
                    "--why=System update running",
                    "--mode=block",
                    "sleep",
                    "infinity",
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        with suppress(Exception):
            proc.terminate()
        with suppress(Exception):
            proc.wait(timeout=3)
        if proc.poll() is None:
            with suppress(Exception):
                proc.kill()
            with suppress(Exception):
                proc.wait(timeout=3)


# ==============================================================================
#  CLI ARGUMENT PARSING & CONFIGURATION
# ==============================================================================
OPT_DRY_RUN = False
OPT_SKIP_SYNC = False
OPT_SYNC_ONLY = False
OPT_POST_SELF_UPDATE = False
OPT_HANDOFF: Path | None = None
OPT_FORCE = False
OPT_STOP_ON_FAIL = False
OPT_ALLOW_DIVERGED_RESET = False
OPT_PROFILE_NAME = "01_update_default"


def show_help():
    help_text = f"""Dusky Updater v{VERSION} — Dotfile sync and setup tool for Arch Linux / Hyprland

Usage: update_dusky.py [OPTIONS]

Options:
  --help, -h               Show this help message and exit
  --version                Show version and exit
  --profile NAME           Specify custom profile TOML (default: 01_update_default)
  --dry-run                Preview actions without making changes
  --skip-sync              Skip git sync, only run the script sequence
  --sync-only              Pull updates but do not run scripts
  --force                  Skip confirmation prompts
  --stop-on-fail           Abort script execution on first hard failure
  --allow-diverged-reset   In non-interactive mode, allow reset on diverged or unrelated history
  --sudo-password PASS     Sudo password for privileged tasks (or DUSKY_SUDO_PASSWORD)
  --list                   List all active scripts in the update sequence
  --list-once              List persistent run-once markers and exit
  --forget-once SCRIPT...  Remove persistent run-once marker(s) and exit
  --doctor                 Run system diagnostics check and exit

Update sequence entry formats:
  U | script.sh --auto
  S | ignore-fail | script.sh --auto
  U | | script.sh --auto

Field 1:
  U = run as user
  S = run with sudo

Field 2:
  Optional comma/space separated flags. Supported: ignore-fail, interactive,
  no-interactive (force non-interactive), once, once:content, once:forever,
  once:sealed, once:global, if:CONDITION, timeout:S, retry:N, retry_delay:S

Logs are saved to:
  {logs_dir()}

Backups are saved to:
  {backups_dir()}
"""
    sys.stdout.write(help_text)
    sys.exit(0)


def show_version():
    sys.stdout.write(f"Dusky Updater v{VERSION}\n")
    sys.exit(0)


def run_doctor():
    sys.stdout.write("Dusky Updater Doctor\n=====================\n")
    sys.stdout.write(f"Version:        {VERSION}\n")
    sys.stdout.write(f"Python:         {sys.version.split()[0]}\n")
    sys.stdout.write(f"Executable:     {sys.executable}\n")
    sys.stdout.write(f"UID/EUID:       {os.getuid()}/{os.geteuid()}\n")
    sys.stdout.write(f"User:           {user_home().name}\n")
    sys.stdout.write(f"Home:           {user_home()}\n")
    sys.stdout.write(f"Logs dir:       {logs_dir()}\n")
    sys.stdout.write(f"Backups dir:    {backups_dir()}\n")
    sys.stdout.write(f"Runtime dir:    {_runtime_dir_path()} (not created by doctor)\n")
    sys.stdout.write(f"Profiles dir:   {PROFILES_DIR}\n")

    profiles = list_profiles()
    sys.stdout.write(f"Profiles found: {len(profiles)}\n")
    for p in profiles:
        sys.stdout.write(f"  - {p.name}\n")
    with suppress(Exception):
        if os.geteuid() == 0:
            sys.stdout.write("Sudo:           running as root (no escalation needed)\n")
        elif not shutil.which("sudo"):
            sys.stdout.write("Sudo:           sudo not installed\n")
        elif SudoEngine.detect_nopasswd():
            sys.stdout.write("Sudo:           passwordless for 'sudo -n true' (not necessarily NOPASSWD: ALL)\n")
        else:
            cached = subprocess.run(
                ["sudo", "-n", "-v"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).returncode == 0
            if cached:
                sys.stdout.write("Sudo:           password required (active cached session)\n")
            else:
                sys.stdout.write("Sudo:           password required\n")
    sys.exit(0)


def list_active_scripts(profile: 'ProfileConfig'):
    user_tasks = [t for t in profile.tasks if t.mode != 'GIT']
    sys.stdout.write(f"Active scripts in profile '{profile.name}':\n\n")
    for i, task in enumerate(user_tasks):
        display_mode = task.mode
        if task.ignore_fail:
            display_mode += ",ignore"
        cmd_str = f"{task.name} {' '.join(task.args)}".strip()
        sys.stdout.write(f"  {i+1:3d}) [{display_mode}] {cmd_str}\n")
    sys.stdout.write(f"\nTotal: {len(user_tasks)} active script(s)\n")
    sys.exit(0)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--help', '-h', action='store_true')
    parser.add_argument('--version', action='store_true')
    parser.add_argument('--doctor', action='store_true')
    parser.add_argument(
        '--profile',
        '-p',
        type=str,
        default=os.environ.get("DUSKY_UPDATER_PROFILE", "01_update_default"),
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--skip-sync', action='store_true')
    parser.add_argument('--sync-only', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--stop-on-fail', action='store_true')
    parser.add_argument('--allow-diverged-reset', action='store_true')
    parser.add_argument('--sudo-password', type=str, default=None)
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--list-once', action='store_true')
    parser.add_argument('--forget-once', nargs='+', metavar='SCRIPT', default=None)
    parser.add_argument('--post-self-update', action='store_true')
    parser.add_argument('--handoff', type=str, default=None)
    return parser


def parse_args():
    global OPT_DRY_RUN, OPT_SKIP_SYNC, OPT_SYNC_ONLY, OPT_FORCE
    global OPT_STOP_ON_FAIL, OPT_ALLOW_DIVERGED_RESET, OPT_PROFILE_NAME
    global OPT_POST_SELF_UPDATE, OPT_HANDOFF

    parser = _build_arg_parser()
    args, unknown = parser.parse_known_args()

    if unknown:
        sys.stderr.write(f"Error: Unknown option {unknown[0]}\n")
        parser.print_usage(sys.stderr)
        sys.exit(2)

    if args.help:
        show_help()
    if args.version:
        show_version()
    if args.doctor:
        run_doctor()

    _handoff = getattr(args, 'handoff', None)
    OPT_HANDOFF = Path(_handoff).expanduser() if _handoff else None
    OPT_PROFILE_NAME = args.profile
    OPT_DRY_RUN = args.dry_run
    OPT_SKIP_SYNC = args.skip_sync
    OPT_SYNC_ONLY = args.sync_only
    OPT_POST_SELF_UPDATE = args.post_self_update
    OPT_FORCE = args.force
    OPT_STOP_ON_FAIL = args.stop_on_fail
    OPT_ALLOW_DIVERGED_RESET = args.allow_diverged_reset

    if OPT_POST_SELF_UPDATE:
        OPT_SKIP_SYNC = True

    # Reject contradictory modes explicitly before any effects.
    if args.dry_run and args.forget_once:
        sys.stderr.write("Error: --dry-run cannot be combined with --forget-once (would mutate state).\n")
        sys.exit(2)
    if args.skip_sync and args.sync_only:
        sys.stderr.write("Error: --skip-sync cannot be combined with --sync-only.\n")
        sys.exit(2)
    if args.list and args.forget_once:
        sys.stderr.write("Error: --list cannot be combined with --forget-once.\n")
        sys.exit(2)
    if args.list_once and args.forget_once:
        sys.stderr.write("Error: --list-once cannot be combined with --forget-once.\n")
        sys.exit(2)

    return args


# ==============================================================================
#  PROFILE LOADING ENGINE
# ==============================================================================
def repair_missing_commas(text: str) -> tuple[str, int]:
    """Insert missing commas inside array / inline-table literals.

    A single omitted comma inside any [] or {} literal makes the WHOLE profile
    unparseable, bricking the updater on the broken file (users can no longer
    update until the file is hand-repaired). This tokenizer-based repairer
    inserts a comma wherever one value token is directly followed by another
    value token without a separator, so a forgotten comma can never again take
    the updater offline.

    Safety contract -- the repairer never changes the meaning of a file that
    parses afterwards, and leaves valid files byte-for-byte untouched:

      * Only invoked on a strict tomllib failure, and only applied when the
        repaired text re-parses cleanly with tomllib as the judge.
      * Strings, arrays and tables are unambiguous: a value token directly
        followed by another value token can only mean a missing comma.
      * Bare words are ambiguous (e.g. ``[1979-05-27 07:32:00]`` is a single
        space-separated datetime, not two values). Commas are inserted between
        words ONLY when the following word is unambiguously a number or a
        boolean (``true``/``false``/``inf``/``nan``), never when it could be a
        datetime fragment.
      * Table keys are recognized via ``=`` and never get commas.

    Returns ``(repaired_text, number_of_fixes)``.
    """
    _NUM_BOOL_RE = re.compile(
        r"[+-]?(?:\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?"
        r"|0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+)"
    )
    _BOOL_WORDS = {"true", "false", "inf", "+inf", "-inf", "nan", "+nan", "-nan"}
    _WORD_CHARS = "_.+-:"

    out: list[str] = []
    i = 0
    n = len(text)
    depth = 0
    pending_value = False
    pending_is_word = False
    value_end = -1
    fixes = 0

    def is_num_or_bool(word: str) -> bool:
        return word in _BOOL_WORDS or _NUM_BOOL_RE.fullmatch(word) is not None

    while i < n:
        c = text[i]

        if c == '#':
            j = text.find('\n', i)
            if j == -1:
                j = n
            out.append(text[i:j])
            i = j
            continue

        if depth and pending_value and (c in '"\'[{+-' or c.isalnum()) and not (c == '-' and i + 1 >= n):
            k = i
            while k < n and (text[k].isalnum() or text[k] in _WORD_CHARS):
                k += 1
            word_end = k
            while k < n and text[k] in ' \t':
                k += 1
            is_key = k < n and text[k] == '='
            insert = True
            if c.isalnum() and pending_is_word and not is_key and not is_num_or_bool(text[i:word_end]):
                insert = False
            if insert:
                out.insert(value_end, ',')
                pending_value = False
                pending_is_word = False
                fixes += 1

        if c in '"\'':
            quote = c
            str_start = i
            if text.startswith(quote * 3, i):
                i += 3
                while i < n and not text.startswith(quote * 3, i):
                    if text[i] == '\\':
                        i += 1
                    i += 1
                i = min(i + 3, n)
            else:
                i += 1
                while i < n and text[i] != quote:
                    if text[i] == '\\':
                        i += 1
                    i += 1
                i += 1
            out.append(text[str_start:i])
            if depth:
                pending_value = True
                pending_is_word = False
                value_end = len(out)
            continue

        if c == '=':
            pending_value = False
            pending_is_word = False
            out.append(c)
            i += 1
            continue

        if c in '[{':
            depth += 1
            pending_value = False
            pending_is_word = False
            out.append(c)
            i += 1
            continue

        if c in ']}':
            depth = max(0, depth - 1)
            out.append(c)
            i += 1
            if depth:
                pending_value = True
                pending_is_word = False
                value_end = len(out)
            else:
                pending_value = False
                pending_is_word = False
            continue

        if c == ',':
            pending_value = False
            pending_is_word = False
            out.append(c)
            i += 1
            continue

        if c in ' \t\r\n':
            out.append(c)
            i += 1
            continue

        word_start_idx = i
        while i < n and (text[i].isalnum() or text[i] in _WORD_CHARS):
            i += 1
        if i > word_start_idx:
            out.append(text[word_start_idx:i])
            k = i
            while k < n and text[k] in ' \t':
                k += 1
            if depth and (k >= n or text[k] != '='):
                pending_value = True
                pending_is_word = True
                value_end = len(out)
            else:
                pending_value = False
                pending_is_word = False
            continue

        out.append(c)
        i += 1

    return ''.join(out), fixes


@dataclass
class ProfileConfig:
    name: str
    description: str
    filepath: Path
    repo_url: str
    branch: str
    search_dirs: list[str]
    conflict_resolutions: dict[str, str]
    sequence: list[str]
    tasks: list['DuskyTask'] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    recovered_profile: bool = False


def list_profiles() -> list[Path]:
    if not PROFILES_DIR.is_dir():
        return []
    return sorted([p for p in PROFILES_DIR.glob("*.toml") if p.is_file()])


def load_profile(name_or_path: str) -> ProfileConfig:
    available = list_profiles()
    p: Path | None = None
    query = name_or_path.strip()

    candidate = Path(query).expanduser()
    if candidate.is_file():
        p = candidate
    elif (PROFILES_DIR / f"{query}.toml").is_file():
        p = PROFILES_DIR / f"{query}.toml"
    elif (PROFILES_DIR / query).is_file():
        p = PROFILES_DIR / query
    elif query.isdigit():
        idx = int(query) - 1
        if 0 <= idx < len(available):
            p = available[idx]

    if p is None and available:
        q_lower = query.lower()
        # Exact stem/filename match only; collect all exact hits for ambiguity check.
        exact_hits = [c for c in available if c.stem.lower() == q_lower or c.name.lower() == q_lower]
        if len(exact_hits) == 1:
            p = exact_hits[0]
        elif len(exact_hits) > 1:
            names = ", ".join(sorted(c.name for c in exact_hits))
            sys.stderr.write(f"[FATAL] Ambiguous profile '{name_or_path}': matches {names}\n")
            sys.exit(2)

    if p is None:
        if available:
            names = ", ".join(sorted(c.stem for c in available))
            sys.stderr.write(
                f"[FATAL] Unknown profile '{name_or_path}'. Available: {names}\n"
            )
            sys.exit(2)
        else:
            sys.stderr.write(f"[FATAL] Profile not found: {name_or_path}\n")
            sys.exit(1)

    try:
        text = p.read_text(encoding="utf-8")
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as raw_err:
            repaired, fixes = repair_missing_commas(text)
            if fixes == 0:
                sys.stderr.write(f"[FATAL] Failed to load profile '{p}': {raw_err}\n")
                sys.exit(1)
            try:
                data = tomllib.loads(repaired)
            except tomllib.TOMLDecodeError:
                sys.stderr.write(f"[FATAL] Failed to load profile '{p}': {raw_err}\n")
                sys.exit(1)
            # Never silently rewrite profile files; use in-memory repair only
            # and report the file location plus the TOML diagnostic.
            sys.stderr.write(
                f"[WARN] Profile '{p}' has {fixes} missing comma(s) ({raw_err}); "
                f"using in-memory repair. Fix them properly in git!\n"
            )

        prof_meta = data.get("profile", {})
        git_cfg = data.get("git", {})
        seq_cfg = data.get("sequence", {})
        if not isinstance(prof_meta, dict):
            sys.stderr.write(f"[FATAL] Profile '{p}': [profile] must be a table\n")
            sys.exit(2)
        if not isinstance(git_cfg, dict):
            sys.stderr.write(f"[FATAL] Profile '{p}': [git] must be a table\n")
            sys.exit(2)
        if not isinstance(seq_cfg, dict):
            sys.stderr.write(f"[FATAL] Profile '{p}': [sequence] must be a table\n")
            sys.exit(2)
        seq_tasks = seq_cfg.get("tasks", [])
        if not isinstance(seq_tasks, list) or any(not isinstance(t, str) for t in seq_tasks):
            sys.stderr.write(f"[FATAL] Profile '{p}': [sequence] tasks must be an array of strings\n")
            sys.exit(2)
        search_dirs = git_cfg.get("search_dirs", [
            "user_scripts/arch_setup_scripts/scripts",
            "user_scripts/arch_setup_scripts",
            "user_scripts/networking",
            "user_scripts/misc_extra",
            "user_scripts/update_dusky",
            "user_scripts/services",
        ])
        if not isinstance(search_dirs, list) or any(not isinstance(d, str) for d in search_dirs):
            sys.stderr.write(f"[FATAL] Profile '{p}': [git] search_dirs must be an array of strings\n")
            sys.exit(2)
        conflict_resolutions = data.get("conflict_resolutions", {})
        if not isinstance(conflict_resolutions, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in conflict_resolutions.items()):
            sys.stderr.write(f"[FATAL] Profile '{p}': [conflict_resolutions] must map names to paths\n")
            sys.exit(2)

        branch = git_cfg.get("branch", GLOBAL_CONFIG.get("git", {}).get("branch", "main"))
        if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or ".." in branch or branch.startswith(("/", "-", ".")) or any(s in branch for s in ("@{", "~", "^", ":", "?", "*", "[", "\\")):
            sys.stderr.write(f"[FATAL] Invalid git branch '{branch}' in profile '{p}'\n")
            sys.exit(2)

        repo_url = git_cfg.get("repo_url", GLOBAL_CONFIG.get("git", {}).get("repo_url", "https://github.com/dusklinux/dusky"))
        if not isinstance(repo_url, str) or not repo_url:
            sys.stderr.write(f"[FATAL] Profile '{p}': [git] repo_url must be a non-empty string\n")
            sys.exit(2)
        name = prof_meta.get("name", p.stem)
        if not isinstance(name, str) or not name:
            sys.stderr.write(f"[FATAL] Profile '{p}': [profile] name must be a non-empty string\n")
            sys.exit(2)
        description = prof_meta.get("description", "")
        if not isinstance(description, str):
            sys.stderr.write(f"[FATAL] Profile '{p}': [profile] description must be a string\n")
            sys.exit(2)

        return ProfileConfig(
            name=name,
            description=description,
            filepath=p.resolve(),
            repo_url=repo_url,
            branch=branch,
            search_dirs=search_dirs,
            conflict_resolutions=conflict_resolutions,
            sequence=seq_tasks,
        )
    except Exception as e:
        sys.stderr.write(f"[FATAL] Failed to load profile '{p}': {e}\n")
        sys.exit(1)


def setup_runtime_dir():
    global RUNTIME_DIR, LOCK_FILE
    RUNTIME_DIR = runtime_dir()
    LOCK_FILE = lock_path()


_LOCK_FD: int | None = None


def _cleanup_lock() -> None:
    global _LOCK_FD
    try:
        if _LOCK_FD is not None:
            with suppress(OSError):
                fcntl.flock(_LOCK_FD, fcntl.LOCK_UN)
            with suppress(OSError):
                os.close(_LOCK_FD)
            _LOCK_FD = None
    except OSError:
        pass


def acquire_lock() -> bool:
    global _LOCK_FD
    if _LOCK_FD is not None:
        return True
    if OPT_DRY_RUN:
        return True
    lp = lock_path()
    try:
        ensure_secure_dir(lp.parent)
        _LOCK_FD = os.open(str(lp), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Import only on contention: ordinary launches pay no recovery
            # startup cost. The supervisor also supports direct worker recovery.
            try:
                from update_dusky_supervisor import recover_lock_conflict
            except ImportError:
                sys.stderr.write("[INFO] Dusky Updater is already updating. Close the existing update window.\n")
                recovered = False
            else:
                recovered = recover_lock_conflict(_LOCK_FD)
            if not recovered:
                os.close(_LOCK_FD)
                _LOCK_FD = None
                raise SystemExit(0)
        os.ftruncate(_LOCK_FD, 0)
        os.write(_LOCK_FD, f"{os.getpid()}\n".encode("ascii"))
        atexit.register(_cleanup_lock)
        return True
    except OSError as e:
        sys.stderr.write(f"\033[1;31m[FATAL]\033[0m Could not establish process lock ({lp}): {e}\n")
        if _LOCK_FD is not None:
            with suppress(OSError):
                os.close(_LOCK_FD)
        _LOCK_FD = None
        return False


def release_lock() -> None:
    _cleanup_lock()


# ==============================================================================
#  STRUCTURAL PATTERN MATCHING & PARSING
# ==============================================================================
@dataclass(slots=True)
class DuskyTask:
    name: str
    mode: Literal['U', 'S', 'GIT']
    ignore_fail: bool
    interactive: bool
    args: list[str]
    interactive_override: bool | None = None
    status: Literal['pending', 'running', 'success', 'failed', 'skipped'] = 'pending'
    resolved_path: Path | None = None
    interpreter: list[str] | None = None
    path_state: str = "ok"  # "ok", "missing", "conflict"

    # Extended Orchestrator Subsystem Fields
    condition: str | None = None
    timeout: float | None = None
    retry: int = 0
    retry_delay: float = 1.0
    once: bool = False
    once_mode: str = "content"
    once_scope: str = "profile"
    checksum: str = ""
    state_key: str = ""
    duration: float = 0.0
    conflict_note: str = ""
    # Explicit terminal outcome shared by UI, state, reports, and exit status.
    outcome: str = "pending"
    reason: str = ""
    exit_code: int | None = None
    attempts: int = 0
    estimated_duration: float = 0.0


def _parse_manifest_strict(profile: ProfileConfig) -> list[DuskyTask]:
    tasks = [
        DuskyTask("Git Bare Repo Validation", 'GIT', False, False, []),
        DuskyTask("Fetch Upstream & Diff", 'GIT', False, False, []),
        DuskyTask("Forensic Collision Backup", 'GIT', False, False, []),
        DuskyTask("Snapshot", 'GIT', False, False, []),
        DuskyTask("Apply Bare Updates (Reset)", 'GIT', False, False, [])
    ]
    for i, t in enumerate(tasks):
        t.state_key = hashlib.blake2b(f"{t.mode}|{t.name}".encode("utf-8")).hexdigest()

    interactive_heuristics = {'reboot_post_lua_update.sh', 'tui_matugen.py', 'dusky_firefox_tui.sh'}

    for seq_index, entry in enumerate(profile.sequence):
        entry = entry.strip()
        if not entry or entry.startswith('#'):
            continue

        parts = [p.strip() for p in entry.split("|", 2)]

        if len(parts) == 1:
            mode, flags_raw, cmd_part = "U", "", parts[0]
        elif len(parts) == 2:
            mode, cmd_part = parts[0], parts[1]
            flags_raw = ""
        elif len(parts) == 3:
            mode, flags_raw, cmd_part = parts[0], parts[1], parts[2]
        else:
            continue

        mode = mode.strip().upper()
        if mode not in ("U", "S"):
            raise ValueError(f"Unknown mode '{mode}' in task: {entry}")

        try:
            cmd_tokens = shlex.split(cmd_part)
        except ValueError as e:
            raise ValueError(f"Malformed quoting in task '{entry}': {e}")
        if not cmd_tokens:
            raise ValueError(f"Empty command in task: {entry}")

        script_name, *args = cmd_tokens

        ignore_fail = False
        interactive = False
        interactive_override = None
        condition = None
        timeout = None
        retry = 0
        retry_delay = 1.0
        once = False
        once_mode = "content"
        once_scope = "profile"

        raw_flags = [tok.strip() for part in flags_raw.split(",") for tok in part.split() if tok.strip()]
        cond_evaluator = ConditionEvaluator()
        for f in raw_flags:
            key, has_val, val = f.partition(":")
            key_l = key.strip().lower()
            val_stripped = val.strip()
            val_l = val_stripped.lower()
            if not has_val:
                match key_l:
                    case "true" | "ignore" | "ignore-fail":
                        ignore_fail = True
                    case "interactive" | "tui" | "prompt" | "fullscreen" | "tty" | "suspend":
                        interactive = True
                        interactive_override = True
                    case "no-interactive" | "noninteractive" | "inline" | "embedded":
                        interactive = False
                        interactive_override = False
                    case "once" | "run_once" | "sticky":
                        once = True
                    case _:
                        # Bare unknown flags (including bare condition keywords
                        # like "wayland") — accept known bare conditions, reject typos.
                        if key_l in ("wayland", "x11", "graphical", "ssh", "desktop", "vm", "baremetal", "battery", "btrfs"):
                            condition = key_l if condition is None else f"{condition},{key_l}"
                        else:
                            raise ValueError(f"Unknown flag '{f}' in task: {entry}")
            else:
                match key_l, val_l:
                    case ("once", "content" | "hash"):
                        once, once_mode = True, "content"
                    case ("once", "forever" | "exact" | "permanent"):
                        once, once_mode = True, "forever"
                    case ("once", "sealed" | "locked"):
                        once, once_mode = True, "sealed"
                    case ("once", "profile" | "local"):
                        once, once_scope = True, "profile"
                    case ("once", "global" | "machine"):
                        once, once_scope = True, "global"
                    case ("if", _):
                        # Value case must be preserved: env vars, paths and
                        # systemd units are case-sensitive. Repeated if: flags are AND.
                        if not cond_evaluator.is_known(val_stripped):
                            raise ValueError(f"Unknown condition '{val_stripped}' in task: {entry}")
                        condition = val_stripped if condition is None else f"{condition},{val_stripped}"
                    case ("timeout", _):
                        try:
                            v = float(val_stripped)
                        except ValueError:
                            raise ValueError(f"Invalid timeout '{val_stripped}' in task: {entry}")
                        if not math.isfinite(v) or v <= 0:
                            raise ValueError(f"Invalid timeout '{val_stripped}' in task: {entry}")
                        timeout = v
                    case ("retry", _):
                        try:
                            v = int(val_stripped)
                        except ValueError:
                            raise ValueError(f"Invalid retry '{val_stripped}' in task: {entry}")
                        if v < 0 or v > 100:
                            raise ValueError(f"Invalid retry '{val_stripped}' in task: {entry}")
                        retry = v
                    case ("retry_delay", _):
                        try:
                            v = float(val_stripped)
                        except ValueError:
                            raise ValueError(f"Invalid retry_delay '{val_stripped}' in task: {entry}")
                        if not math.isfinite(v) or v < 0:
                            raise ValueError(f"Invalid retry_delay '{val_stripped}' in task: {entry}")
                        retry_delay = v
                    case _:
                        raise ValueError(f"Unknown flag '{f}' in task: {entry}")

        if not interactive and script_name in interactive_heuristics:
            interactive = True

        task = DuskyTask(
            name=script_name, mode=mode,  # type: ignore
            ignore_fail=ignore_fail, interactive=interactive,
            interactive_override=interactive_override,
            condition=condition, timeout=timeout, retry=retry,
            retry_delay=retry_delay, once=once,
            once_mode=once_mode, once_scope=once_scope, args=args
        )
        # Unambiguous identity: include sequence occurrence so duplicate lines
        # do not collapse state keys.
        task.state_key = hashlib.blake2b(
            f"{mode}|{script_name}|{shlex.join(args)}|{seq_index}".encode("utf-8")
        ).hexdigest()
        tasks.append(task)
    return tasks


def parse_manifest(profile: ProfileConfig) -> list[DuskyTask]:
    """Parse task entries independently so one malformed task cannot brick sync.

    The five Git pseudo-tasks are always present. Invalid user task entries are
    omitted with indexed diagnostics retained on the profile for final warning
    aggregation. This deliberately does not guess malformed task semantics.
    """
    base = ProfileConfig(
        name=profile.name, description=profile.description, filepath=profile.filepath,
        repo_url=profile.repo_url, branch=profile.branch,
        search_dirs=list(profile.search_dirs), conflict_resolutions=dict(profile.conflict_resolutions),
        sequence=[], diagnostics=profile.diagnostics, recovered_profile=profile.recovered_profile,
    )
    tasks = _parse_manifest_strict(base)
    for seq_index, entry in enumerate(profile.sequence):
        if not isinstance(entry, str):
            profile.diagnostics.append(f"task[{seq_index}]: entry is not a string; skipped")
            continue
        stripped = entry.strip()
        if not stripped or stripped.startswith("#"):
            continue
        one = ProfileConfig(
            name=profile.name, description=profile.description, filepath=profile.filepath,
            repo_url=profile.repo_url, branch=profile.branch,
            search_dirs=list(profile.search_dirs), conflict_resolutions=dict(profile.conflict_resolutions),
            sequence=[entry], diagnostics=profile.diagnostics, recovered_profile=profile.recovered_profile,
        )
        try:
            parsed = _parse_manifest_strict(one)
        except (ValueError, TypeError, OverflowError) as e:
            profile.diagnostics.append(f"task[{seq_index}]: {e}; skipped")
            continue
        if len(parsed) <= 5:
            continue
        task = parsed[5]
        task.state_key = hashlib.blake2b(
            f"{task.mode}|{task.name}|{shlex.join(task.args)}|{seq_index}".encode("utf-8")
        ).hexdigest()
        tasks.append(task)
    return tasks


# ==============================================================================
#  GLOBAL SETTINGS VALIDATION (before auth/bootstrap/mutation)
# ==============================================================================
def validate_global_config(cfg: dict | None = None) -> list[str]:
    """Return only settings errors that make safe operation ambiguous.

    Optional/type errors are normalized and remain structured run warnings;
    they must not brick startup merely because a cosmetic/tuning field is bad.
    """
    if cfg is None:
        return list(CONFIG_FATAL_ERRORS)
    if not isinstance(cfg, dict):
        return ["global settings root is not a table"]
    w0, f0 = len(CONFIG_WARNINGS), len(CONFIG_FATAL_ERRORS)
    _normalize_global_config(cfg)
    errors = CONFIG_FATAL_ERRORS[f0:]
    del CONFIG_WARNINGS[w0:]
    del CONFIG_FATAL_ERRORS[f0:]
    return errors

def bootstrap_dependencies() -> bool:
    """Ensure UI dependencies are available without mutating the system.

    No auto-installation: if textual/rich are missing, emit an explicit
    actionable error (exact pacman command) and exit before any package,
    sudo, or state mutation. Import-time ``_HAS_UI`` is therefore never
    stale (we never pretend an install succeeded then fail because UI
    classes were not reloaded). Info commands and dry-run never install.
    """
    if any(flag in sys.argv for flag in {"-h", "--help", "--version", "--doctor", "--list", "--list-once", "--forget-once"}):
        return False
    # Dry-run must never install packages or prompt for sudo.
    if "--dry-run" in sys.argv:
        missing = [
            pkg for mod, pkg in [("textual", "python-textual"), ("rich", "python-rich")]
            if importlib.util.find_spec(mod) is None
        ]
        if missing:
            sys.stdout.write(
                "\033[1;33m[DUSKY BOOTSTRAP]\033[0m Dry-run: missing UI packages "
                f"({', '.join(missing)}) would be installed via: sudo pacman -S --noconfirm "
                f"{' '.join(missing)} — refusing to install in dry-run.\n"
            )
        return False

    missing = [
        pkg for mod, pkg in [("textual", "python-textual"), ("rich", "python-rich")]
        if importlib.util.find_spec(mod) is None
    ]
    if missing:
        sys.stderr.write(
            "\033[1;31m[FATAL]\033[0m Missing UI dependencies: "
            f"{', '.join(missing)}.\n"
            f"Install them explicitly before running the updater: "
            f"sudo pacman -S --noconfirm {' '.join(missing)}\n"
            "No packages were installed automatically; re-run after installing.\n"
        )
        sys.exit(1)
    return False


def _early_info_dispatch():  # type: ignore[no-untyped-def]
    """Parse CLI once and serve informational subcommands without the TUI stack.

    Single parse entry: unknown options and contradictory modes are rejected
    BEFORE bootstrap_dependencies() gets a chance to touch the system, and
    info flags never hard-fail on a missing textual/rich (bootstrap
    deliberately skips dependency installation for them). Returns parsed args
    for the main flow so argv is never parsed twice with diverging results.
    """
    args = parse_args()

    cfg_errors = validate_global_config()
    if cfg_errors:
        sys.stderr.write("[FATAL] Invalid global settings:\n")
        for err in cfg_errors:
            sys.stderr.write(f"  - {err}\n")
        sys.exit(2)

    if not (args.help or args.version or args.doctor or args.list
            or args.list_once or args.forget_once):
        return args

    if args.help:
        show_help()
    if args.version:
        show_version()
    if args.doctor:
        run_doctor()

    if args.list_once:
        # Read-only informational command: never migrate or create DB.
        # A consistent snapshot is required; malformed/unreadable state fails
        # explicitly (nonzero) rather than reporting an empty list that would
        # hide markers. True absent DB still reports empty (exit 0).
        try:
            store = OnceStore(read_only=True)
        except (OSError, sqlite3.DatabaseError) as e:
            sys.stderr.write(f"[FATAL] Cannot obtain consistent once-marker snapshot: {e}\n")
            sys.exit(1)
        try:
            store.print_list()
        finally:
            store.close()
        sys.exit(0)

    if args.forget_once:
        setup_runtime_dir()
        if not acquire_lock():
            sys.exit(1)
        store = OnceStore()
        try:
            for script in args.forget_once:
                removed = store.forget(script)
                sys.stdout.write(f"Forgot {removed} marker(s): {script}\n")
        finally:
            store.close()
        sys.exit(0)

    if args.list:
        profile = load_profile(args.profile)
        try:
            profile.tasks = parse_manifest(profile)
        except ValueError as e:
            sys.stderr.write(f"[FATAL] Invalid task manifest: {e}\n")
            sys.exit(2)
        list_active_scripts(profile)
        sys.exit(0)
    return args


# Module import must be side-effect free for isolated testing. Executable
# startup (argv dispatch, dependency installation, UI startup) happens only
# inside main() / __main__. Dependency-free help/version/list/doctor remain
# usable without the TUI stack.
SUDO_ALREADY_ACQUIRED = False
_HAS_UI = False

try:
    from rich.markup import escape
    from rich.syntax import Syntax
    from rich.text import Text
    from textual import events, on
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical
    from textual.reactive import reactive
    from textual.screen import ModalScreen
    from textual.widgets import (
        Button,
        ContentSwitcher,
        Input,
        Label,
        ListItem,
        ListView,
        OptionList,
        ProgressBar,
        RichLog,
        Static,
    )
    from textual.widgets.option_list import Option
    _HAS_UI = True
except ImportError:
    # UI stack optional at import time; main() enforces it for full runs
    # after bootstrap has had a chance to install missing packages.
    _HAS_UI = False

    def escape(text: object) -> str:  # type: ignore[no-redef]
        return str(text).replace("[", "\\[")


def _require_ui() -> None:
    if not _HAS_UI:
        sys.stdout.write("\033[1;31m[FATAL]\033[0m UI libraries (textual/rich) are required for this mode. Install python-textual python-rich.\n")
        sys.exit(1)


# ==============================================================================
#  STORAGE, LOGGING & LOCKING UTILITIES
# ==============================================================================
ACTIVE_LOG_BASE_DIR = None
ACTIVE_BACKUP_BASE_DIR = None
RUNTIME_DIR = None
LOCK_FILE = None
LOCK_FD = None
LOG_FILE = None
RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

CLR_RED = "\033[1;31m"
CLR_GRN = "\033[1;32m"
CLR_YLW = "\033[1;33m"
CLR_BLU = "\033[1;34m"
CLR_CYN = "\033[1;36m"
CLR_RST = "\033[0m"


def strip_ansi(text: str) -> str:
    return ANSI_STRIP_REGEX.sub('', text)


def log(level: str, msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = f"[{level}]"

    if level == "INFO":
        prefix = f"{CLR_BLU}[INFO ]{CLR_RST}"
    elif level == "OK":
        prefix = f"{CLR_GRN}[OK   ]{CLR_RST}"
    elif level == "WARN":
        prefix = f"{CLR_YLW}[WARN ]{CLR_RST}"
    elif level == "ERROR":
        prefix = f"{CLR_RED}[ERROR]{CLR_RST}"
    elif level == "SECTION":
        prefix = f"\n{CLR_CYN}═══════{CLR_RST}"

    app_instance = globals().get('app')
    if app_instance is not None and getattr(app_instance, '_running', False):
        with suppress(Exception):
            app_instance.log_main(f"{prefix} {msg}" if level not in ("RAW", "SECTION") else (f"{prefix} {msg}\n" if level == "SECTION" else msg))
    else:
        if level == "SECTION":
            sys.stdout.write(f"{prefix} {msg}\n")
        elif level == "RAW":
            sys.stdout.write(f"{msg}\n")
        else:
            sys.stdout.write(f"{prefix} {msg}\n")
        sys.stdout.flush()

        if LOG_FILE and GLOBAL_CONFIG.get("logging", {}).get("enabled", True):
            with suppress(OSError):
                stripped = strip_ansi(msg)
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(f"[{timestamp}] [{level:<7s}] {stripped}\n")


def desktop_notify(summary: str, body: str, urgency: str = "normal") -> None:
    if OPT_DRY_RUN:
        return
    DesktopNotifier.notify(summary, body, urgency)


def auto_prune() -> None:
    log_days = GLOBAL_CONFIG.get("paths", {}).get("log_retention_days", 14)
    backup_days = GLOBAL_CONFIG.get("paths", {}).get("backup_retention_days", 14)
    now_sec = time.time()

    if log_days > 0:
        cutoff = now_sec - (log_days * 86400)
        l_dir = logs_dir()
        if l_dir.is_dir():
            with suppress(Exception):
                for f in l_dir.glob("dusky_update_*.log"):
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        with suppress(OSError):
                            f.unlink()
            # Per-run orchestrator directories ({stamp}_{profile}_{run_id})
            # created by RunLogger. The strict double-timestamp shape only
            # matches updater-generated dirs, never arbitrary user folders.
            run_dir_re = re.compile(r"^\d{8}_\d{6}_.+_\d{8}_\d{6}$")
            with suppress(Exception):
                for d in l_dir.iterdir():
                    if d.is_dir() and not d.is_symlink() and run_dir_re.fullmatch(d.name):
                        if d.stat().st_mtime < cutoff:
                            shutil.rmtree(d, ignore_errors=True)

    if backup_days > 0:
        cutoff = now_sec - (backup_days * 86400)
        b_dir = backups_dir()
        if b_dir.is_dir():
            # Only auto-expire completed temporary artifacts. Unresolved
            # recovery data (collisions, manual merges, history, full snapshots,
            # staged/quarantined) is never expired automatically; it requires
            # explicit user action. A backup is eligible only if it carries
            # .meta/STATUS=completed.
            prune_prefixes = ("your_changes_",)
            with suppress(Exception):
                for d in b_dir.iterdir():
                    if not (d.is_dir() and not d.is_symlink()):
                        continue
                    if not any(d.name.startswith(p) for p in prune_prefixes):
                        continue
                    try:
                        status_f = d / ".meta" / "STATUS"
                        if not status_f.is_file():
                            continue
                        if status_f.read_text(encoding="utf-8", errors="ignore").strip() != "completed":
                            continue
                    except OSError:
                        continue
                    try:
                        if d.stat().st_mtime < cutoff:
                            with suppress(OSError):
                                shutil.rmtree(d, ignore_errors=True)
                    except OSError:
                        continue


def make_private_dir_under(base: Path, folder_name: str) -> Path | None:
    if not ensure_secure_dir(base):
        return None
    candidate = base / folder_name
    try:
        candidate.mkdir(mode=0o700)
        candidate.chmod(0o700)
        _fsync_directory(candidate)
        _fsync_directory(base)
        return candidate
    except FileExistsError:
        for i in range(2, 100):
            candidate = base / f"{folder_name}_{i}"
            try:
                candidate.mkdir(mode=0o700)
                candidate.chmod(0o700)
                _fsync_directory(candidate)
                _fsync_directory(base)
                return candidate
            except FileExistsError:
                continue
            except OSError:
                break
    except OSError:
        pass
    return None


def make_private_file_under(base: Path, prefix: str, suffix: str = ".log") -> Path | None:
    if not ensure_secure_dir(base):
        return None
    try:
        fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=base)
        os.close(fd)
        p = Path(path)
        p.chmod(0o600)
        return p
    except Exception:
        return None


def setup_storage_roots():
    global ACTIVE_LOG_BASE_DIR, ACTIVE_BACKUP_BASE_DIR
    l_dir = logs_dir()
    b_dir = backups_dir()
    s_dir = _documents_subdir("state_subdir", "state")

    # Prevent destructive overlap: logs/backups/state must be distinct and
    # must not coincide with WORK_TREE, GIT_DIR, or filesystem root.
    try:
        l_res = l_dir.resolve()
        b_res = b_dir.resolve()
        s_res = s_dir.resolve()
        wt_res = WORK_TREE.resolve()
        gd_res = GIT_DIR.resolve()
    except OSError:
        sys.stderr.write("Error: Cannot resolve storage roots.\n")
        sys.exit(1)
    if l_res == b_res or l_res == s_res or b_res == s_res:
        sys.stderr.write(
            f"Error: log/backup/state directories must be distinct: {l_dir} {b_dir} {s_dir}\n"
        )
        sys.exit(1)
    for cand, label in ((l_res, "logs"), (b_res, "backups"), (s_res, "state")):
        if cand == Path("/") or cand == wt_res or cand == gd_res:
            sys.stderr.write(f"Error: {label} directory overlaps protected root: {cand}\n")
            sys.exit(1)
    # Enforce invalid overlaps while allowing normal storage inside WORK_TREE
    # (the default ~/Documents/... lives under WORK_TREE=~):
    # - no storage root inside another storage root (prune/cleanup isolation);
    # - no storage root inside GIT_DIR (would corrupt the bare repo);
    # - GIT_DIR inside a storage root (backups swallowing the repo).
    for (a_res, a_label), (b_res, b_label) in (
        ((l_res, "logs"), (b_res, "backups")),
        ((l_res, "logs"), (s_res, "state")),
        ((b_res, "backups"), (s_res, "state")),
    ):
        if a_res in b_res.parents or b_res in a_res.parents:
            sys.stderr.write(
                f"Error: {a_label} directory ({a_res}) must not nest inside {b_label} ({b_res})\n"
            )
            sys.exit(1)
    for cand, label in ((l_res, "logs"), (b_res, "backups"), (s_res, "state")):
        if cand in gd_res.parents or gd_res in cand.parents:
            # cand == gd_res already rejected above; remaining cases are
            # nesting in either direction.
            sys.stderr.write(f"Error: {label} directory ({cand}) overlaps GIT_DIR ({gd_res})\n")
            sys.exit(1)

    if OPT_DRY_RUN:
        # Dry-run validates path relationships only; it must not create storage.
        ACTIVE_LOG_BASE_DIR = l_dir
        ACTIVE_BACKUP_BASE_DIR = b_dir
        return

    if GLOBAL_CONFIG["logging"]["enabled"]:
        if ensure_secure_dir(l_dir):
            ACTIVE_LOG_BASE_DIR = l_dir
        else:
            ACTIVE_LOG_BASE_DIR = l_dir
            _config_warn(f"optional log directory unavailable: {l_dir}")
    else:
        ACTIVE_LOG_BASE_DIR = l_dir

    # Recovery storage is mandatory for any real sync because destructive Git
    # replacement must never proceed without a durable backup destination.
    if ensure_secure_dir(b_dir):
        ACTIVE_BACKUP_BASE_DIR = b_dir
    else:
        sys.stderr.write(f"Error: Cannot create mandatory backup directory: {b_dir}\n")
        sys.exit(1)

    if not ensure_secure_dir(s_dir):
        _persistence_warn(f"state directory unavailable: {s_dir}")


async def wait_for_process(proc: asyncio.subprocess.Process, timeout: float | None = None) -> int:
    """Await a child via asyncio only; never steal reaping or report unknown as success."""
    try:
        if timeout is not None and timeout > 0:
            async with asyncio.timeout(timeout):
                return await proc.wait()
        return await proc.wait()
    except TimeoutError:
        raise
    except asyncio.CancelledError:
        # Let the caller terminate/reap; preserve cancellation, never map to 0.
        raise
    except Exception:
        # Unknown failure is not success; surface as distinct non-zero.
        if proc.returncode is not None:
            return proc.returncode
        return 127


def _pg_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except OSError:
        return False


_pg_has_owned_member = _pg_alive


async def _terminate_process_group(proc: asyncio.subprocess.Process, grace: float = 3.0) -> int | None:
    pid = proc.pid
    if pid is None:
        return proc.returncode
    pgid = pid
    try:
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pgid, signal.SIGTERM)
        try:
            async with asyncio.timeout(grace):
                while True:
                    if proc.returncode is None:
                        with suppress(TimeoutError, asyncio.TimeoutError):
                            await asyncio.wait_for(proc.wait(), timeout=0.1)
                    if not _pg_alive(pgid):
                        break
                    await asyncio.sleep(0.1)
        except (TimeoutError, asyncio.TimeoutError):
            pass

        if _pg_alive(pgid):
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pgid, signal.SIGKILL)
            try:
                async with asyncio.timeout(2.0):
                    while True:
                        if proc.returncode is None:
                            with suppress(TimeoutError, asyncio.TimeoutError):
                                await asyncio.wait_for(proc.wait(), timeout=0.1)
                        if not _pg_alive(pgid):
                            break
                        await asyncio.sleep(0.1)
            except (TimeoutError, asyncio.TimeoutError):
                pass

        if proc.returncode is None:
            with suppress(Exception):
                async with asyncio.timeout(0.5):
                    await proc.wait()
    except Exception:
        pass
    return proc.returncode

def _write_all_nonblocking(fd: int, data: bytes) -> bool:
    """Write all bytes to a nonblocking fd; True only when fully written.

    Bounded synchronous fallback: waits briefly for writable readiness via
    select instead of busy-spinning forever, handles zero-progress writes,
    and gives up (False) rather than freezing the event loop. Async callers
    must prefer :func:`_awrite_all_nonblocking`, which uses true async
    readiness with cancellation and preserves partial writes/order.
    """
    view = memoryview(data)
    # Bounded attempts: ~2s total, no infinite spin on a full pipe/PTY.
    for _ in range(200):
        if not view:
            return True
        try:
            n = os.write(fd, view)
        except BlockingIOError:
            try:
                _, w, _ = select.select([], [fd], [], 0.01)
                if not w:
                    continue
                continue
            except (OSError, ValueError):
                return False
        except OSError:
            return False
        if n == 0:
            # No progress: wait briefly for readiness instead of spinning.
            try:
                _, w, _ = select.select([], [fd], [], 0.01)
            except (OSError, ValueError):
                return False
            continue
        view = view[n:]
    return not view


async def _wait_fd_writable(fd: int) -> None:
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[None] = loop.create_future()

    def _ready() -> None:
        if not fut.done():
            fut.set_result(None)

    try:
        loop.add_writer(fd, _ready)
    except (OSError, ValueError):
        # FD closed/invalid: let the caller observe OSError on write.
        return
    try:
        await fut
    finally:
        with suppress(Exception):
            loop.remove_writer(fd)


async def _awrite_all_nonblocking(fd: int, data: bytes) -> bool:
    """Buffered async write preserving partial writes/order.

    Uses event-loop writable readiness (no busy polling, no loop blocking),
    handles zero-progress writes, supports cancellation, and never drops
    trailing bytes (returns False only on closed/failing FD).
    """
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except BlockingIOError:
            try:
                await _wait_fd_writable(fd)
            except asyncio.CancelledError:
                raise
            except Exception:
                return False
            continue
        except OSError:
            return False
        if n == 0:
            try:
                await _wait_fd_writable(fd)
            except asyncio.CancelledError:
                raise
            except Exception:
                return False
            continue
        view = view[n:]
    return True


def check_disk_space(path: Path) -> bool:
    try:
        usage = shutil.disk_usage(path)
        available_mb = usage.free // (1024 * 1024)
        if available_mb < DISK_MIN_FREE_MB:
            log("ERROR", f"Low disk space: {available_mb}MB available at {path} (need {DISK_MIN_FREE_MB}MB)")
            return False
        return True
    except Exception:
        return False


def get_available_bytes(path: Path) -> int:
    try:
        usage = shutil.disk_usage(path)
        return usage.free
    except Exception:
        return 0


def path_copy_size_bytes(path: Path) -> int:
    if not (path.exists() or path.is_symlink()):
        return 0
    if path.is_symlink():
        with suppress(OSError):
            return path.lstat().st_size
        return 0
    if path.is_dir():
        size = 0
        with suppress(Exception):
            for root, dirs, files in os.walk(path):
                size += Path(root).stat().st_size
                for f in files:
                    fp = Path(root) / f
                    if not fp.is_symlink():
                        size += fp.stat().st_size
                    else:
                        size += fp.lstat().st_size
        return size
    else:
        with suppress(OSError):
            return path.stat().st_size
        return 0


def backup_required_bytes(paths: list[Path]) -> int | None:
    total = 0
    for p in paths:
        try:
            total += path_copy_size_bytes(p)
        except OSError:
            return None
    return total

def ensure_free_space_for_bytes(target_path: Path, required_bytes: int, context: str = "operation") -> bool:
    if required_bytes <= 0:
        return True
    available_bytes = get_available_bytes(target_path)
    reserve_bytes = DISK_COPY_RESERVE_MB * 1024 * 1024
    if available_bytes < required_bytes + reserve_bytes:
        required_mb = (required_bytes + reserve_bytes + 1048575) // 1048576
        available_mb = (available_bytes + 1048575) // 1048576
        log("ERROR", f"Insufficient free space for {context}: {available_mb}MB available, need at least {required_mb}MB")
        return False
    return True


def setup_logging():
    global LOG_FILE
    LOG_FILE = None
    if OPT_DRY_RUN or not GLOBAL_CONFIG["logging"]["enabled"]:
        return
    LOG_FILE = make_private_file_under(ACTIVE_LOG_BASE_DIR, f"dusky_update_{RUN_TIMESTAMP}_", ".log")
    if not LOG_FILE:
        _config_warn(f"optional main log unavailable under {ACTIVE_LOG_BASE_DIR}")
        return
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(f" DUSKY UPDATE LOG — {RUN_TIMESTAMP}\n")
            f.write(f" Kernel: {os.uname().release} | User: {user_home().name} | Python: {sys.version.split()[0]}\n")
            f.write("=" * 80 + "\n")
    except OSError as e:
        _config_warn(f"optional main log write failed: {e}")
        LOG_FILE = None


# ==============================================================================
#  THEME COMPILER (MATUGEN JSON)
# ==============================================================================
def compile_theme() -> dict[str, str]:
    defaults = {
        "bg": "#1a110e", "fg": "#f1dfd9", "accent": "#ffb59b",
        "error": "#ffb4ab", "warning": "#e7bdaf", "success": "#d5c68e", "muted": "#53433e"
    }
    # Validate configured default_palette before merging: partial/invalid
    # palettes fall back per-key so checked values never enter CSS and missing
    # keys cannot KeyError. Safe against non-dict ui tables (no traceback).
    try:
        _ui_tbl = GLOBAL_CONFIG.get("ui", {})
        if not isinstance(_ui_tbl, dict):
            _ui_tbl = {}
        cfg_palette = _ui_tbl.get("default_palette", None)
    except Exception:
        cfg_palette = None
    if isinstance(cfg_palette, dict):
        for k, v in cfg_palette.items():
            if k in defaults and isinstance(v, str) and _HEX_COLOR_RE.match(v.strip()):
                defaults[k] = v.strip()
    theme: dict[str, str] = dict(defaults)

    try:
        _ui_tbl2 = GLOBAL_CONFIG.get("ui", {})
        if not isinstance(_ui_tbl2, dict):
            _ui_tbl2 = {}
        search_paths = _ui_tbl2.get("theme_paths", [
            ".config/matugen/generated/dusky_tui.json",
            ".config/matugen/generated_fresh/dusky_tui.json",
        ])
        if not isinstance(search_paths, list):
            search_paths = []
    except Exception:
        search_paths = []

    for raw in search_paths:
        theme_path = Path(raw).expanduser()
        if not theme_path.is_absolute():
            theme_path = user_home() / theme_path

        if theme_path.is_file():
            try:
                data = json.loads(theme_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for k, v in data.items():
                        if k in theme and isinstance(v, str) and _HEX_COLOR_RE.match(v.strip()):
                            theme[k] = v.strip()
                    break
            except (json.JSONDecodeError, OSError):
                pass
    return theme


THEME = compile_theme()


def get_rgb_color(hex_str: str, default: tuple[int, int, int] = (255, 181, 155)) -> tuple[int, int, int]:
    try:
        clean_hex = hex_str.lstrip('#')
        if len(clean_hex) >= 6:
            return int(clean_hex[0:2], 16), int(clean_hex[2:4], 16), int(clean_hex[4:6], 16)
        elif len(clean_hex) == 3:
            return int(clean_hex[0]*2, 16), int(clean_hex[1]*2, 16), int(clean_hex[2]*2, 16)
    except (ValueError, IndexError, Exception):
        pass
    return default


try:
    _ui_sw = GLOBAL_CONFIG.get("ui", {})
    if not isinstance(_ui_sw, dict):
        sidebar_w = 35
    else:
        sidebar_w = int(_ui_sw.get("sidebar_width", 35))
except (TypeError, ValueError, AttributeError):
    sidebar_w = 35
sidebar_w = max(15, min(80, sidebar_w))
log_w = 100 - sidebar_w

DUSKY_CSS = f"""
Screen, ListView, RichLog, ScrollBar, #sidebar {{ 
    background: {THEME['bg']}; 
    color: {THEME['fg']}; 
    scrollbar-color: {THEME['accent']}80;
    scrollbar-color-hover: {THEME['accent']};
    scrollbar-color-active: {THEME['accent']};
    scrollbar-background: transparent;
    scrollbar-background-hover: transparent;
    scrollbar-background-active: transparent;
    scrollbar-size-horizontal: 0;
}}
#sidebar {{
    width: {sidebar_w}%; 
    border-right: solid {THEME['muted']}4d; 
    background: {THEME['bg']};
    height: 100%;
    scrollbar-size-vertical: 1;
}}
#log_container {{ 
    width: {log_w}%; padding: 0; 
    background: {THEME['bg']}; 
    height: 100%;
}}
ContentSwitcher {{ height: 1fr; width: 100%; }}
RichLog {{
    height: 1fr; background: transparent; color: {THEME['fg']};
    border: none; padding: 0;
    scrollbar-size-vertical: 1;
    overflow-x: hidden;
}}
ListView {{ background: transparent; overflow-x: hidden; height: 100%; scrollbar-size-vertical: 1; }}
ListView:focus {{ background-tint: transparent 0%; }}
ListItem {{ 
    padding: 0 1; 
    border-left: tall transparent;
    background: transparent;
}}
ListView > ListItem.-highlight {{ 
    background: {THEME['muted']}; 
    border-left: tall {THEME['accent']};
}}
ListView:focus > ListItem.-highlight {{ 
    background: {THEME['muted']}; 
    border-left: tall {THEME['accent']};
}}
#top_header {{
    height: 1;
    dock: top;
    background: {THEME['bg']};
    color: {THEME['accent']};
    text-style: bold;
    padding: 0 1;
}}
#header_title {{
    width: 100%;
    text-align: center;
}}
ProgressBar {{ dock: bottom; margin: 0; height: 1; }}
ProgressBar > .progress--bar {{ color: {THEME['accent']}; }}
ProgressBar > .progress--remaining {{ background: {THEME['muted']}33; }}
CompletionDialog, TaskSearchScreen, LogSearchScreen, ConfirmQuitScreen, HelpScreen {{
    align: center middle;
    background: rgba(0, 0, 0, 0.88);
    width: 100%;
    height: 100%;
}}
#completion-dialog {{
    width: 60; height: auto; max-height: 60%;
    background: {THEME['bg']}; padding: 1 2;
}}
#completion-dialog.-success {{ border: solid {THEME['success']}; }}
#completion-dialog.-warning {{ border: solid {THEME['warning']}; }}
#completion-dialog.-danger {{ border: solid {THEME['error']}; }}
#completion-message {{ color: {THEME['fg']}; margin-bottom: 1; }}
#modal-title {{
    color: {THEME['accent']}; margin-bottom: 1; text-style: bold;
    border-bottom: solid {THEME['muted']};
    content-align: center middle; width: 100%;
}}
.modal-btn-container {{
    width: 100%; height: auto; align: center middle;
    margin-top: 1; background: transparent;
}}
.modal-close-btn {{
    background: {THEME['accent']}; color: {THEME['bg']}; text-style: bold;
    padding: 0 2; width: auto; height: 1; margin: 0 1;
}}
.modal-close-btn:hover {{ background: {THEME['fg']}; color: {THEME['bg']}; }}
.modal-cancel-btn {{
    background: {THEME['muted']}; color: {THEME['fg']}; text-style: bold;
    padding: 0 2; width: auto; height: 1; margin: 0 1;
}}
.modal-cancel-btn:hover {{ background: {THEME['accent']}; color: {THEME['bg']}; }}
#search_dialog, #log_search_dialog {{
    width: 86;
    height: 75%;
    background: {THEME['bg']};
    border: solid {THEME['accent']};
    padding: 1 2;
}}
#search_list, #log_search_list {{
    height: 1fr;
    border: none;
    background: {THEME['bg']};
    color: {THEME['fg']};
}}
#search_input, #log_search_input {{
    margin-bottom: 1;
}}
#search_title, #log_search_title {{
    color: {THEME['accent']};
    text-style: bold;
    margin-bottom: 1;
}}
#confirm_dialog {{
    width: 56;
    height: auto;
    background: {THEME['bg']};
    border: heavy {THEME['warning']};
    padding: 1 2;
}}
#confirm_title {{
    color: {THEME['error']};
    text-style: bold;
    margin-bottom: 1;
}}
#confirm_text {{
    color: {THEME['fg']};
    margin-bottom: 1;
}}
#help_dialog {{
    width: 80;
    height: auto;
    max-height: 80%;
    background: {THEME['bg']};
    border: heavy {THEME['accent']};
    padding: 1 2;
}}
"""

# ==============================================================================
#  MANIFEST & PATH CONSTANTS
# ==============================================================================
# WORK_TREE and GIT_DIR are configured in PATH RESOLUTION UTILITIES (with env overrides)


def is_script_interactive(script_path: Path) -> bool:
    if not script_path.exists() or not script_path.is_file():
        return False
    try:
        with open(script_path, 'r', errors='ignore') as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                line_clean = line.strip().replace(" ", "").lower()
                if "#dusky_interactive=true" in line_clean or "#dusky_interactive=1" in line_clean:
                    return True
    except Exception:
        pass
    return False


def resolve_and_validate_manifest(
    profile: ProfileConfig,
    tasks: list[DuskyTask],
    interactive: bool = True,
) -> bool:
    log("INFO", "Performing pre-flight validation and conflict resolution...")

    needs_python = False
    all_valid = True

    for index, task in enumerate(tasks):
        if task.mode == 'GIT':
            continue

        task.conflict_note = ""

        script = task.name
        matches: list[Path] = []

        # Persist explicit resolutions across post-sync re-resolution: if this
        # task already resolved (e.g. via an interactive choice pre-sync) and
        # that path is still readable, keep it instead of silently switching
        # to the first match.
        if task.resolved_path is not None and task.path_state == "ok":
            try:
                rp = task.resolved_path
                if rp.is_file() and os.access(rp, os.R_OK):
                    if "/" in script:
                        exp = Path(script)
                        if not exp.is_absolute():
                            exp = WORK_TREE / exp
                        if rp == exp or (rp.is_absolute() and exp.is_absolute() and rp.resolve() == exp.resolve()):
                            matches = [rp]
                    elif rp.name == script:
                        matches = [rp]
            except OSError:
                pass

        if not matches:
            if "/" in script:
                explicit_path = Path(script)
                if not explicit_path.is_absolute():
                    explicit_path = WORK_TREE / explicit_path
                if explicit_path.is_file() and os.access(explicit_path, os.R_OK):
                    matches.append(explicit_path)
            else:
                # Deduplicate equivalent search dirs (a, a/, ./a) and matches
                # by resolved inode so the same file is never a "duplicate".
                seen_dirs: set[str] = set()
                seen_inodes: set[tuple[int, int]] = set()
                for d in profile.search_dirs:
                    try:
                        norm = os.path.normpath(d)
                    except Exception:
                        continue
                    if norm in seen_dirs:
                        continue
                    seen_dirs.add(norm)
                    dir_path = WORK_TREE / norm
                    candidate = dir_path / script
                    try:
                        if candidate.is_file() and os.access(candidate, os.R_OK):
                            try:
                                st = candidate.stat()
                                key = (st.st_dev, st.st_ino)
                            except OSError:
                                key = None
                            if key is not None:
                                if key in seen_inodes:
                                    continue
                                seen_inodes.add(key)
                            if candidate not in matches:
                                matches.append(candidate)
                    except OSError:
                        continue

        def _finalize(task: DuskyTask, resolved: Path | None, state: str, checksum: str) -> None:
            # Unambiguous identity: sequence position + resolved path + args.
            task.resolved_path = resolved if resolved is not None else Path(script)
            task.path_state = state
            task.checksum = checksum
            task.state_key = hashlib.blake2b(
                f"{task.mode}|{task.name}|{shlex.join(task.args)}|{index}|{task.resolved_path}".encode("utf-8")
            ).hexdigest()

        if len(matches) == 0:
            _finalize(task, None, "missing", "")
            log("WARN", f"Required script not found or unreadable: {script}")
            continue
        elif len(matches) == 1:
            script_path = matches[0]
        else:
            predefined = profile.conflict_resolutions.get(script)
            if predefined:
                explicit_pre = Path(predefined)
                if not explicit_pre.is_absolute():
                    explicit_pre = WORK_TREE / explicit_pre
                if explicit_pre.is_file() and os.access(explicit_pre, os.R_OK):
                    script_path = explicit_pre
                    log("INFO", f"Resolved duplicate '{script}' using conflict resolution -> {script_path}")
                else:
                    log("WARN", f"Predefined resolution for '{script}' is missing or unreadable: {explicit_pre}")
                    _finalize(task, None, "missing", "")
                    continue
            else:
                hashes = {m: file_checksum(m) for m in matches}
                # Checksum failure ("") is an error, never "identical".
                if any(h == "" for h in hashes.values()):
                    bad = [str(m) for m, h in hashes.items() if h == ""]
                    log("ERROR", f"Cannot checksum duplicates for '{script}' ({', '.join(bad)}); treating as conflict.")
                    _finalize(task, None, "missing", "")
                    continue
                unique_hashes = set(hashes.values())
                if len(unique_hashes) == 1:
                    # Identical duplicates resolve silently without prompting.
                    script_path = matches[0]
                    task.conflict_note = f"Identical duplicates found for {script} (locations: {', '.join(str(m) for m in matches)})"
                    log("INFO", f"Resolved {script} silently (all duplicates identical byte-for-byte).")
                else:
                    script_path = matches[0]
                    log("WARN", f"Content conflict for {script}. Found differing versions:")
                    for j, m in enumerate(matches):
                        log("WARN", f"  {j+1}) {m} (Checksum: {hashes[m]})")
                    if OPT_DRY_RUN or OPT_FORCE or not interactive or not sys.stdin.isatty():
                        log("WARN", "Non-interactive/force mode: automatically picking the first match.")
                    else:
                        sys.stdout.write(f"\n{CLR_YLW}[CONFLICT DETECTED]{CLR_RST} Which version of {script} should be executed?\n")
                        choice = ""
                        while True:
                            try:
                                choice = input(f"Enter 1-{len(matches)}: ").strip()
                            except (KeyboardInterrupt, EOFError):
                                log("ERROR", "Input interrupted. Aborting.")
                                sys.exit(1)
                            if choice.isdigit() and 1 <= int(choice) <= len(matches):
                                script_path = matches[int(choice) - 1]
                                log("OK", f"Selected: {script_path}")
                                break
                            print(f"Invalid choice. Please enter a number between 1 and {len(matches)}.")

        _finalize(task, script_path, "ok", file_checksum(script_path))

        if is_script_interactive(script_path):
            task.interactive = True

        if task.interactive_override is not None:
            task.interactive = task.interactive_override

        first_line = ""
        with suppress(OSError):
            with open(script_path, "r", encoding="utf-8", errors="replace") as f:
                first_line = f.readline().rstrip('\r\n')

        has_py_ext = script_path.suffix == ".py"
        has_sh_ext = script_path.suffix == ".sh"
        has_py_shebang = False
        has_bash_shebang = False
        extracted_interpreter = []

        shebang_match = re.match(r'^#!\s*(.+)', first_line)
        if shebang_match:
            shebang_cmd = shebang_match.group(1).strip()
            extracted_interpreter = shebang_cmd.split()
            if any("python" in token for token in extracted_interpreter):
                has_py_shebang = True
            elif extracted_interpreter:
                base_interp = os.path.basename(extracted_interpreter[0])
                if base_interp in ("bash", "sh", "zsh", "dash", "ksh"):
                    has_bash_shebang = True

        resolved_interpreter = []

        if (has_py_ext and has_bash_shebang) or (has_sh_ext and has_py_shebang):
            if OPT_DRY_RUN or OPT_FORCE or not interactive or not sys.stdin.isatty():
                log("WARN", f"Interpreter conflict for '{script}': File extension and Shebang disagree. Auto-picking Shebang.")
                resolved_interpreter = extracted_interpreter
                if has_py_shebang:
                    needs_python = True
            else:
                sys.stdout.write(f"\n{CLR_YLW}[INTERPRETER CONFLICT]{CLR_RST} Script {script} has conflicting indicators.\n")
                sys.stdout.write("  1) Run with Bash\n")
                sys.stdout.write("  2) Run with Python\n")

                int_choice = ""
                while True:
                    try:
                        int_choice = input("Select interpreter (1-2): ").strip()
                    except (KeyboardInterrupt, EOFError):
                        log("ERROR", "Input interrupted. Aborting.")
                        sys.exit(1)
                    if int_choice == "1":
                        resolved_interpreter = ["bash"]
                        break
                    elif int_choice == "2":
                        resolved_interpreter = ["python3"]
                        needs_python = True
                        break
                    else:
                        print("Invalid choice.")
        else:
            suffix = script_path.suffix.lower()
            ext_map = GLOBAL_CONFIG.get("execution", {}).get(
                "extension_interpreters",
                {
                    ".py": sys.executable,
                    ".sh": shutil.which("bash") or "bash",
                    ".fish": shutil.which("fish") or "fish",
                },
            )
            default_interp = GLOBAL_CONFIG.get("execution", {}).get("default_interpreter", "bash")

            if extracted_interpreter:
                resolved_interpreter = extracted_interpreter
            elif suffix in ext_map:
                resolved_interpreter = [ext_map[suffix]]
            elif has_py_ext or has_py_shebang:
                needs_python = True
                resolved_interpreter = extracted_interpreter or [sys.executable]
            else:
                resolved_interpreter = [shutil.which(default_interp) or default_interp]

        task.interpreter = resolved_interpreter

    if needs_python and shutil.which("python3") is None and shutil.which("python") is None:
        if OPT_DRY_RUN:
            log("WARN", "[DRY-RUN] Python dependency detected but not installed.")
        else:
            log("WARN", "Python dependency detected, but 'python' binary is not installed.")
            log("INFO", "Installing Python via pacman...")

            if not SudoEngine.refresh_sync():
                if not SudoEngine.preflight():
                    log("ERROR", "Sudo authentication required to install Python dependency.")
                    return False

            try:
                subprocess.run(SudoEngine.sudo_prefix() + ["pacman", "-S", "python", "--noconfirm", "--needed"], check=True)
                log("OK", "Python installed successfully.")
            except subprocess.CalledProcessError:
                log("ERROR", "Failed to install Python. Aborting update sequence.")
                return False

    if not all_valid:
        log("ERROR", "Preflight validation failed: one or more required scripts failed syntax checks.")
        return False

    log("OK", "Preflight validation complete.")
    return True


# ==============================================================================
#  GIT ASYNCHRONOUS ENGINE
# ==============================================================================
def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    strip_keys = GLOBAL_CONFIG.get("git", {}).get(
        "env_strip",
        ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_LITERAL_PATHSPECS", "GIT_ASKPASS", "SSH_ASKPASS"],
    )
    for k in strip_keys:
        env.pop(k, None)

    inject = GLOBAL_CONFIG.get("git", {}).get(
        "env_inject",
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": "ssh" if "SSH_AUTH_SOCK" in os.environ else "ssh -o BatchMode=yes",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_OPTIONAL_LOCKS": "0",
        },
    )
    env.update(inject)
    return env


HANDOFF_TTL_SEC = 3600


def restart_handoff_path(run_id: str) -> Path:
    return runtime_dir() / f"handoff_{run_id}.json"


def _write_restart_handoff(path: Path, payload: dict) -> bool:
    """Publish a single-use exec handoff durably before inheriting control."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            str(path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            _fsync_directory(path.parent)
        except BaseException:
            with suppress(OSError):
                os.close(fd)
            with suppress(OSError):
                path.unlink(missing_ok=True)
                _fsync_directory(path.parent)
            raise
        return True
    except OSError:
        return False


def validate_restart_handoff(path: Path | None, profile: 'ProfileConfig') -> dict | None:
    """Validate every field consumed after exec without binding to new config.

    Repository/branch/profile *values* may legitimately change in the candidate,
    so they are shape-checked rather than compared with the newly loaded profile.
    The handoff is instead bound to the inherited work-tree/git-dir, run-id file
    name, owner, runtime directory, TTL, and inherited lock identity.
    """
    _ = profile
    try:
        if path is None:
            return None
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            return None
        if stat.S_IMODE(st.st_mode) & 0o077:
            return None
        runtime = _runtime_dir_path().resolve()
        if path.parent.resolve() != runtime:
            return None
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict) or payload.get("schema") != 3:
            return None
        if payload.get("git_dir") != str(GIT_DIR) or payload.get("work_tree") != str(WORK_TREE):
            return None

        def nonempty(name: str) -> str | None:
            value = payload.get(name)
            return value if isinstance(value, str) and value and "\x00" not in value else None

        run_id = nonempty("run_id")
        if run_id is None or path.name != f"handoff_{run_id}.json":
            return None
        for key in ("repo_url", "branch", "profile_filepath", "profile_name"):
            if nonempty(key) is None:
                return None
        created = payload.get("created_epoch")
        if isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(float(created)):
            return None
        age = time.time() - float(created)
        if age > HANDOFF_TTL_SEC or age < -60:
            return None

        if "run_start_mono" in payload or "phase_durations" in payload:
            start = payload.get("run_start_mono")
            durations = payload.get("phase_durations")
            if (isinstance(start, bool) or not isinstance(start, (int, float))
                    or not math.isfinite(start) or not 0 <= start <= time.monotonic()):
                return None
            if not isinstance(durations, dict):
                return None
            for key in ("phase1_git", "phase1_5_resolve", "phase2_exec"):
                value = durations.get(key)
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or value < 0):
                    return None

        tasks = payload.get("git_tasks")
        if not isinstance(tasks, list) or len(tasks) != 5:
            return None
        valid_status = {"success", "failed", "skipped"}
        for item in tasks:
            if not isinstance(item, dict) or item.get("status") not in valid_status:
                return None
            code = item.get("exit_code")
            if code is not None and (isinstance(code, bool) or not isinstance(code, int)):
                return None

        summary = payload.get("git_summary")
        warnings = payload.get("warnings", [])
        if not isinstance(summary, dict) or not isinstance(warnings, list):
            return None
        for key in ("branch", "before_head", "after_head", "commits", "diff",
                    "collision_backup", "local_mods_backup", "status"):
            value = summary.get(key, "")
            if not isinstance(value, str) or "\x00" in value:
                return None
        commit_list = summary.get("commit_list", [])
        if not isinstance(commit_list, list) or any(not isinstance(x, str) or "\x00" in x for x in commit_list):
            return None
        for key in ("files_changed",):
            value = summary.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
        for key in ("collisions", "local_mods"):
            value = summary.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                return None
        restored = summary.get("local_mods_restored")
        if restored is not None and not isinstance(restored, bool):
            return None
        if not isinstance(summary.get("unrelated_histories", False), bool):
            return None
        for warning in warnings:
            if not isinstance(warning, dict):
                return None
            for key in ("kind", "message", "task", "path", "recovery"):
                value = warning.get(key, "")
                if not isinstance(value, str) or "\x00" in value:
                    return None

        lock = payload.get("lock")
        if lock is not None:
            if not isinstance(lock, dict):
                return None
            for key in ("fd", "ino", "dev"):
                value = lock.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return None
            lock_path_value = lock.get("path")
            if not isinstance(lock_path_value, str) or "\x00" in lock_path_value:
                return None

        sudo = payload.get("sudo")
        if not isinstance(sudo, dict) or sudo.get("mode") not in {"none", "password", "nopasswd"}:
            return None
        for key in ("askpass_path", "sudoers_path"):
            value = sudo.get(key, "")
            if not isinstance(value, str) or "\x00" in value:
                return None

        generation = payload.get("restart_generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or not (1 <= generation <= 3):
            return None
        return payload
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def _append_manifest_line(path: Path, record: dict) -> bool:
    """Append one recovery record and durably publish it before mutation."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", errors="surrogateescape") as mf:
            mf.write(json.dumps(record, ensure_ascii=False) + "\n")
            mf.flush()
            os.fsync(mf.fileno())
        _fsync_directory(path.parent)
        return True
    except OSError:
        return False


def _sync_copy_file(src_p: Path, dest_p: Path) -> bool:
    """Copy one recovery payload and fsync data + containing directory."""
    try:
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        try:
            st_d = dest_p.lstat()
            if stat.S_ISLNK(st_d.st_mode):
                dest_p.unlink()
            elif stat.S_ISDIR(st_d.st_mode):
                if not src_p.is_symlink() and src_p.is_file():
                    return False
        except FileNotFoundError:
            pass
        if src_p.is_symlink():
            target = os.readlink(src_p)
            with suppress(OSError):
                dest_p.unlink(missing_ok=True)
            os.symlink(target, dest_p)
            _fsync_directory(dest_p.parent)
        else:
            shutil.copy2(src_p, dest_p, follow_symlinks=False)
            _fsync_regular_file(dest_p)
            _fsync_directory(dest_p.parent)
        return True
    except OSError:
        return False


# ==============================================================================
#  SELF-HEALING GATES (never let a broken script silence the updater)
# ==============================================================================
def _validate_script_syntax(path: Path) -> tuple[bool, str]:
    """Quick syntax gate for managed Python (.py) and shell (.sh) files.

    No __pycache__ is produced (in-memory compile for Python). Shell checking
    uses the file's actual shebang interpreter with extglob enabled, and handles
    scripts with embedded data tables gracefully. Templates are ignored.
    """
    original = path
    suffix = original.suffix.lower()
    try:
        st = original.lstat()
    except OSError:
        return False, "file missing"
    if stat.S_ISLNK(st.st_mode):
        try:
            path = original.resolve(strict=True)
            st = path.stat()
        except (OSError, RuntimeError) as e:
            return False, f"broken/unresolvable symlink: {e}"
    if not stat.S_ISREG(st.st_mode):
        return False, "not a regular file"
    name_lower = original.name.lower()
    if ".template" in name_lower or name_lower.endswith(".template"):
        return True, ""
    if suffix == ".py":
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            return False, f"cannot read: {e}"
        try:
            compile(text, str(path), "exec", dont_inherit=True)
            return True, ""
        except SyntaxError as e:
            return False, f"python syntax failed: {e}"
        except (ValueError, OSError) as e:
            return False, f"python syntax failed: {e}"
    if suffix == ".sh":
        interp = "bash"
        try:
            with open(path, "rb") as f:
                first = f.readline(4096)
            if first.startswith(b"#!"):
                line = first[2:].decode("utf-8", errors="ignore").strip().split()
                if line:
                    base = Path(line[0]).name
                    if base in ("bash", "sh", "dash", "ksh", "zsh"):
                        interp = base
                    elif base == "env" and len(line) > 1:
                        cand = Path(line[1]).name
                        if cand in ("bash", "sh", "dash", "ksh", "zsh"):
                            interp = cand
        except OSError:
            pass
        cmd = [interp]
        if interp == "bash":
            cmd.extend(["-O", "extglob"])
        cmd.extend(["-n", str(path)])
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
                check=True,
            )
            return True, ""
        except (subprocess.SubprocessError, OSError) as e:
            # Whole-file validation only. A syntactically valid prefix before
            # an exit/data marker is not evidence that the actual candidate is
            # safe to execute.
            return False, f"{interp} syntax failed: {e}"
    return True, ""



@dataclass(slots=True)
class ScriptGateResult:
    fatal: bool = False
    outcomes: dict[str, dict[str, str]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return not self.fatal



class _PTYStreamReaderProtocol(asyncio.StreamReaderProtocol):
    """Translate Linux terminal hangup to EOF before StreamReader sees it.

    Doing this at the protocol boundary preserves bytes already buffered when
    the slave closes; StreamReader otherwise raises its stored exception before
    returning that final output.
    """

    def __init__(self, reader: asyncio.StreamReader, on_close):
        super().__init__(reader)
        self._on_close = on_close

    def connection_lost(self, exc: Exception | None) -> None:
        # The transport closes its FD after this callback. Disable input first
        # so a delayed response cannot write to that FD after it is reused.
        self._on_close()
        if isinstance(exc, OSError) and exc.errno == errno.EIO:
            exc = None
        super().connection_lost(exc)


class GitEngine:
    def __init__(self, app: Any, profile: ProfileConfig):
        self.app = app
        self.profile = profile
        self.log = app.log_main  # type: ignore
        # All emitted paths and literal pathspecs are work-tree-root relative,
        # regardless of the directory from which the updater was launched.
        self.git_cmd_base = ['git', '-C', str(WORK_TREE), f'--git-dir={GIT_DIR}', f'--work-tree={WORK_TREE}']
        self._last_collision_count = 0
        self._last_collision_dir = ""
        backups_dir().mkdir(parents=True, exist_ok=True)
        self._sync_txn_path = backups_dir() / ".dusky_sync_transaction.json"

    _SYNC_TXN_STATES = {
        "prepared", "collisions-durable", "snapshot-durable", "applying",
        "reset-applied", "completed", "completed-recovery-retained",
        "aborted-before-apply", "recovery-blocked",
    }

    def _load_sync_txn(self) -> dict[str, Any] | None:
        try:
            st = self._sync_txn_path.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                return None
            payload = json.loads(self._sync_txn_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema") not in {1, 2}:
                return None
            if payload.get("state") not in self._SYNC_TXN_STATES:
                return None
            if payload.get("schema") == 2:
                if payload.get("work_tree") != str(WORK_TREE) or payload.get("git_dir") != str(GIT_DIR):
                    return None
            return payload
        except (OSError, ValueError, TypeError):
            return None

    def _write_sync_txn(self, state: str, **extra: Any) -> None:
        """Durably publish a cumulative destructive-sync checkpoint.

        Every state after ``prepared`` carries forward recovery locations and
        capture metadata.  This is intentional: a journal that forgets its
        payload pointers at ``applying`` cannot recover after a power loss.
        """
        if state not in self._SYNC_TXN_STATES:
            raise ValueError(f"unknown sync transaction state: {state}")
        if state == "prepared":
            payload: dict[str, Any] = {}
        else:
            payload = self._load_sync_txn() or {}
        payload.update({
            "schema": 2,
            "state": state,
            "updated": now_iso(),
            "updated_epoch": time.time(),
            "run_id": str(getattr(self.app, "run_id", RUN_TIMESTAMP)),
            "work_tree": str(WORK_TREE),
            "git_dir": str(GIT_DIR),
            "old_head": getattr(self, "_sync_old_head", payload.get("old_head", "")),
            "target_oid": getattr(self, "_sync_target_oid", payload.get("target_oid", "")),
            "collision_backup": self._last_collision_dir or str(payload.get("collision_backup", "")),
        })
        payload.update(extra)
        parent = self._sync_txn_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".sync-txn-", dir=str(parent))
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._sync_txn_path)
            _fsync_directory(parent)
        finally:
            with suppress(OSError):
                tmp_path.unlink(missing_ok=True)

    def _finish_sync_txn(self, state: str = "completed", **extra: Any) -> None:
        self._write_sync_txn(state, **extra)

    async def _run(self, *args: str, check: bool = True, task_idx: int = -1) -> tuple[int, str, str]:
        rc, out, err = await self._run_raw(*args, timeout_sec=float(GLOBAL_CONFIG["git"]["command_timeout"]))
        if task_idx != -1 and err:
            self.app.log_task(escape(err), task_idx)
        if rc != 0 and check:
            cmd = self.git_cmd_base + list(args)
            msg = f"[bold {THEME['error']}]Git Architecture Error ({rc}):[/] {escape(err)}"
            self.log(msg)
            if task_idx != -1:
                self.app.log_task(msg, task_idx)
            raise subprocess.CalledProcessError(rc, cmd, output=out, stderr=err)
        return rc, out, err

    async def _run_raw(self, *args: str, timeout_sec: int | float | None = None) -> tuple[int, str, str]:
        cmd = self.git_cmd_base + list(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_git_env(), start_new_session=True
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return 1, "", str(e)
        try:
            effective_timeout = float(timeout_sec) if timeout_sec and timeout_sec > 0 else float(GLOBAL_CONFIG["git"]["command_timeout"])
            if effective_timeout > 0:
                try:
                    async with asyncio.timeout(effective_timeout):
                        stdout, stderr = await proc.communicate()
                except TimeoutError:
                    await _terminate_process_group(proc)
                    return 124, "", "timeout"
            else:
                stdout, stderr = await proc.communicate()

            return (proc.returncode,
                    stdout.decode('utf-8', errors='surrogateescape') if '-z' in args else stdout.decode('utf-8', errors='surrogateescape').strip(),
                    stderr.decode('utf-8', errors='surrogateescape').strip())
        except asyncio.CancelledError:
            await _terminate_process_group(proc)
            raise
        except Exception as e:
            return 1, "", str(e)

    async def _run_raw_bytes(self, *args: str, timeout_sec: int = 30) -> tuple[int, bytes]:
        """Like _run_raw, but returns the exact stdout bytes (no strip/decode),
        so restored files stay byte-identical to their git blob."""
        cmd = self.git_cmd_base + list(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_git_env(), start_new_session=True
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return 1, b""
        try:
            try:
                async with asyncio.timeout(timeout_sec):
                    stdout, _ = await proc.communicate()
            except TimeoutError:
                await _terminate_process_group(proc)
                return 124, b""
            return proc.returncode, stdout
        except asyncio.CancelledError:
            await _terminate_process_group(proc)
            raise
        except Exception:
            return 1, b""

    def _tlog(self, msg: str, idx: int, also_main: bool = False):
        self.app.log_task(msg, idx)  # type: ignore
        if also_main:
            self.log(msg)

    @staticmethod
    def _literal_pathspec(rel: str) -> str:
        # `--` alone does not disable pathspec magic; :(literal) matches the
        # exact path including glob chars, spaces, and non-UTF8 (surrogateescape).
        return f":(top,literal){rel}"

    async def _reject_protected_incoming(self, commit_oid: str, idx: int) -> bool:
        """Reject incoming trees that contain paths overlapping updater repository or backup directories."""
        protected: list[str] = []
        for d in tuple(x for x in (GIT_DIR, backups_dir(), logs_dir(), state_dir(), askpass_dir(), runtime_dir(), supervisor_state_dir()) if x is not None):
            try:
                rel = d.resolve().relative_to(WORK_TREE.resolve())
                protected.append(str(rel))
            except (ValueError, RuntimeError):
                pass
        if not protected:
            return True
        rc, ls_tree, err = await self._run_raw('ls-tree', '-r', '-z', '--name-only', commit_oid)
        if rc != 0:
            self._tlog(f"[bold {THEME['error']}]Failed to check tree {escape(commit_oid)}: {escape(err)}[/]", idx, True)
            return False
        for p in ls_tree.split('\0'):
            if not p:
                continue
            for prot in protected:
                if p == prot or p.startswith(prot + "/") or prot.startswith(p + "/"):
                    self._tlog(f"[bold {THEME['error']}]Incoming path '{escape(p)}' conflicts with/contains protected storage '{escape(prot)}'[/]", idx, True)
                    return False
        return True

    def _quarantine_path(self, backup_dir: Path, category: str, rel_path: str, src: Path) -> Path | None:
        """Move a conflicting directory into recovery storage under backup_dir so neither side is lost."""
        try:
            dest = backup_dir / category / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() or dest.is_symlink():
                dest = dest.with_name(f"{dest.name}_{int(time.time())}")
            shutil.move(str(src), str(dest))
            if not _fsync_tree(dest):
                return None
            with suppress(OSError):
                _fsync_directory(src.parent)
            return dest
        except Exception:
            return None


    async def _gate_incoming_scripts(self, changed_paths: list[str], idx: int, local_head: str = "", target_oid: str = "") -> ScriptGateResult:
        """Validate exact post-sync script bytes and block execution structurally.

        Invalid scripts remain at the upstream/local content selected by Git; the
        gate never rewrites them to an older blob and never relies on chmod as a
        safety boundary. Execution is prevented through app.blocked_scripts.
        """
        result = ScriptGateResult()
        # Shell scripts can legitimately parse later sections only after
        # changing parser options or stop before appended data. Let their real
        # interpreter decide by default; updater activation is checked separately.
        if not GLOBAL_CONFIG["execution"]["validate_subscript_syntax"]:
            return result
        tree_ref = target_oid or "HEAD"
        try:
            rc_ls, ls_out, ls_err = await self._run_raw('ls-tree', '-r', '-z', tree_ref, '--')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._tlog(f"[bold {THEME['error']}]Gate tree lookup failed: {escape(str(e))}[/]", idx, True)
            result.fatal = True
            return result
        if rc_ls != 0:
            self._tlog(f"[bold {THEME['error']}]Gate tree lookup failed (rc={rc_ls}): {escape(ls_err)}[/]", idx, True)
            result.fatal = True
            return result

        modes: dict[str, str] = {}
        for rec in ls_out.split('\0'):
            if not rec or '\t' not in rec:
                continue
            meta, rel = rec.split('\t', 1)
            toks = meta.split()
            if len(toks) >= 3:
                modes[rel] = toks[0]

        blocked = getattr(self.app, "blocked_scripts", None)
        if not isinstance(blocked, dict):
            blocked = {}
            setattr(self.app, "blocked_scripts", blocked)
        all_outcomes = getattr(self.app, "script_gate_outcomes", None)
        if not isinstance(all_outcomes, dict):
            all_outcomes = {}
            setattr(self.app, "script_gate_outcomes", all_outcomes)

        for rel in sorted(set(changed_paths)):
            if not rel.endswith((".py", ".sh")):
                continue
            mode = modes.get(rel)
            if mode is None:
                result.outcomes[rel] = {"state": "deleted", "reason": "absent from target tree"}
                blocked.pop(rel, None)
                continue
            if mode not in ("100644", "100755", "120000"):
                reason = f"unsupported git mode {mode}"
                result.outcomes[rel] = {"state": "blocked", "reason": reason}
                blocked[rel] = reason
                self._tlog(f"[bold {THEME['warning']}]Blocked script {escape(rel)}: {escape(reason)}[/]", idx, True)
                continue

            target = WORK_TREE / rel
            ok, why = await asyncio.to_thread(_validate_script_syntax, target)
            if ok:
                result.outcomes[rel] = {"state": "valid", "reason": ""}
                blocked.pop(rel, None)
                continue

            result.outcomes[rel] = {"state": "blocked", "reason": why}
            blocked[rel] = why
            self._tlog(
                f"[bold {THEME['warning']}]BLOCKED TASK SCRIPT:[/] {escape(rel)}\n"
                f"    Exact candidate failed syntax validation: {escape(why)}\n"
                "    File content was not rewritten; any task resolving to this path will be skipped.",
                idx, True,
            )
            if hasattr(self.app, "add_warning"):
                self.app.add_warning("blocked-script", f"{rel}: {why}", path=rel)

        all_outcomes.update(result.outcomes)
        return result

    async def _unstage_managed_paths(self) -> bool:
        """
        Safeguards internal directories (backups, logs, state) from git tracking hazards.
        If a user ran 'git add .', these untracked files enter the index. A subsequent
        'git reset --hard' would physically delete them (data loss), and diff-index would
        capture them as user modifications (backup loop).

        By syncing the index to HEAD for these paths, new backup files become purely untracked,
        which secures them against deletion and capture, while leaving any explicitly
        tracked files (like a .keep file) completely intact.

        Fail-closed: returns False on any git failure so callers stop before reset.
        """
        paths = []
        for d in tuple(x for x in (backups_dir(), logs_dir(), state_dir(), askpass_dir(), runtime_dir(), supervisor_state_dir()) if x is not None):
            try:
                rel = d.relative_to(WORK_TREE)
                paths.append(str(rel))
            except ValueError:
                pass

        if not paths:
            return True

        rc, raw_local, _ = await self._run_raw('rev-parse', '--verify', '-q', 'HEAD')

        if rc == 0 and raw_local.strip():
            rc2, _, err2 = await self._run_raw('reset', '-q', 'HEAD', '--', *(self._literal_pathspec(p) for p in paths))
            if rc2 != 0:
                self._tlog(f"[bold {THEME['error']}]Failed to unstage managed paths: {escape(err2)}[/]", -1, True)
                return False
            return True
        else:
            # Unborn repo: no tracked files exist yet, so rm --cached is strictly safe.
            # If HEAD is unborn (rc!=0 with empty output) this is expected; any
            # other rev-parse failure must fail closed.
            if rc != 0 and raw_local.strip():
                return False
            rc2, _, err2 = await self._run_raw('rm', '--cached', '-r', '--ignore-unmatch', '--quiet', '--', *(self._literal_pathspec(p) for p in paths))
            if rc2 != 0:
                self._tlog(f"[bold {THEME['error']}]Failed to unstage managed paths (unborn): {escape(err2)}[/]", -1, True)
                return False
            return True

    def _detect_git_lock_state(self) -> str:
        for lock_name in ('index.lock', 'config.lock', 'packed-refs.lock',
                          'shallow.lock', 'HEAD.lock', 'ORIG_HEAD.lock', 'FETCH_HEAD.lock'):
            if (GIT_DIR / lock_name).exists():
                return lock_name
        refs_dir = GIT_DIR / "refs"
        if refs_dir.is_dir():
            with suppress(Exception):
                for root, dirs, files in os.walk(refs_dir):
                    for f in files:
                        if f.endswith('.lock'):
                            return str((Path(root) / f).relative_to(GIT_DIR))
        return 'none'

    async def _run_git_dir_only(self, *args: str, timeout_sec: int | float | None = None) -> tuple[int, str, str]:
        # Identity probes (bare check, git-dir resolution) must NOT pass
        # --work-tree: git reports a bare repo as non-bare when a worktree
        # override is present. Worktree operations keep using _run_raw.
        cmd = ['git', f'--git-dir={GIT_DIR}'] + list(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_git_env(), start_new_session=True
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return 1, "", str(e)
        try:
            effective_timeout = float(timeout_sec) if timeout_sec and timeout_sec > 0 else float(GLOBAL_CONFIG["git"]["command_timeout"])
            if effective_timeout > 0:
                try:
                    async with asyncio.timeout(effective_timeout):
                        stdout, stderr = await proc.communicate()
                except TimeoutError:
                    await _terminate_process_group(proc)
                    return 124, "", "timeout"
            else:
                stdout, stderr = await proc.communicate()
            return (proc.returncode,
                    stdout.decode('utf-8', errors='surrogateescape').strip(),
                    stderr.decode('utf-8', errors='surrogateescape').strip())
        except asyncio.CancelledError:
            await _terminate_process_group(proc)
            raise
        except Exception as e:
            return 1, "", str(e)

    async def _get_repo_state(self, task_idx: int) -> str:
        if GIT_DIR.is_symlink():
            self._tlog(f"[bold {THEME['error']}]GIT_DIR must not be a symlink: {GIT_DIR}[/]", task_idx, True)
            return 'invalid'
        if not GIT_DIR.exists():
            # Validate path relationships even in the absent case: the parent
            # must be a safe, owned directory (not a file/symlink), otherwise
            # refuse the clone target instead of creating into it.
            parent = GIT_DIR.parent
            try:
                st_p = parent.lstat()
                if stat.S_ISLNK(st_p.st_mode):
                    self._tlog(f"[bold {THEME['error']}]GIT_DIR parent must not be a symlink: {parent}[/]", task_idx, True)
                    return 'invalid'
                if not stat.S_ISDIR(st_p.st_mode):
                    self._tlog(f"[bold {THEME['error']}]GIT_DIR parent is not a directory: {parent}[/]", task_idx, True)
                    return 'invalid'
                if st_p.st_uid != os.getuid():
                    self._tlog(f"[bold {THEME['error']}]GIT_DIR parent not owned by current user: {parent}[/]", task_idx, True)
                    return 'invalid'
            except FileNotFoundError:
                # Parent chain absent: walk up to the first existing ancestor.
                cur = parent
                while True:
                    try:
                        st_a = cur.lstat()
                        break
                    except FileNotFoundError:
                        cur = cur.parent
                        if str(cur) in ("", "/", "."):
                            try:
                                st_a = cur.lstat()
                            except OSError:
                                self._tlog(f"[bold {THEME['error']}]Cannot validate GIT_DIR parent chain: {parent}[/]", task_idx, True)
                                return 'invalid'
                            break
                        continue
                    except OSError:
                        self._tlog(f"[bold {THEME['error']}]Cannot validate GIT_DIR parent chain: {parent}[/]", task_idx, True)
                        return 'invalid'
                try:
                    if stat.S_ISLNK(st_a.st_mode) or not stat.S_ISDIR(st_a.st_mode) or st_a.st_uid != os.getuid():
                        self._tlog(f"[bold {THEME['error']}]GIT_DIR ancestor unsafe: {cur}[/]", task_idx, True)
                        return 'invalid'
                except OSError:
                    return 'invalid'
            except OSError:
                self._tlog(f"[bold {THEME['error']}]Cannot validate GIT_DIR parent: {parent}[/]", task_idx, True)
                return 'invalid'
            return 'absent'
        if not GIT_DIR.is_dir():
            self._tlog(f"[bold {THEME['error']}]GIT_DIR path exists but is not a directory: {GIT_DIR}[/]", task_idx, True)
            return 'invalid'
        if GIT_DIR.stat().st_uid != os.getuid():
            self._tlog(f"[bold {THEME['error']}]GIT_DIR is not owned by current user: {GIT_DIR}[/]", task_idx, True)
            return 'invalid'
        if not WORK_TREE.is_dir() or not os.access(WORK_TREE, os.W_OK):
            self._tlog(f"[bold {THEME['error']}]Work tree is not writable: {WORK_TREE}[/]", task_idx, True)
            return 'invalid'

        lock_name = self._detect_git_lock_state()
        if lock_name != 'none':
            lock_path = GIT_DIR / lock_name
            self._tlog(f"[bold {THEME['warning']}]Git lock detected: {lock_path}[/]", task_idx, True)
            try:
                st = lock_path.lstat()
                owner = st.st_uid
                mtime = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))
            except OSError as e:
                self._tlog(f"[bold {THEME['error']}]Cannot stat git lock {lock_path}: {e}. Refusing.[/]", task_idx, True)
                return 'invalid'
            # Never auto-unlink based on age or incomplete /proc visibility.
            # Refuse with diagnostics unless ownership/liveness is conclusively
            # established — and even then, refuse: only the lock owner (git
            # itself or the user) may clear it manually.
            self._tlog(
                f"[bold {THEME['error']}]Refusing: git lock {lock_name} present "
                f"(uid={owner}, mtime={mtime}). Resolve manually (finish/abort the git "
                f"operation or remove the lock yourself after verifying no git process is running).[/]",
                task_idx, True,
            )
            return 'invalid'

        rc, git_dir_out, _ = await self._run_git_dir_only('rev-parse', '--git-dir')
        if rc != 0:
            self._tlog(f"[bold {THEME['error']}]Repository metadata invalid or corrupted: {GIT_DIR}[/]", task_idx, True)
            return 'invalid'
        # Validate bare-repository identity without the worktree override:
        # --git-dir output resolves to GIT_DIR and the repo reports bare.
        try:
            reported = Path(git_dir_out.strip()).expanduser()
            if not reported.is_absolute():
                reported = (WORK_TREE / reported).resolve()
            else:
                reported = reported.resolve()
        except OSError:
            reported = None
        try:
            expected = GIT_DIR.resolve()
        except OSError:
            expected = GIT_DIR
        if reported is None or reported != expected:
            self._tlog(f"[bold {THEME['error']}]GIT_DIR mismatch: configured {expected}, git reports {reported}[/]", task_idx, True)
            return 'invalid'
        rc_bare, bare_out, bare_err = await self._run_git_dir_only('rev-parse', '--is-bare-repository')
        if rc_bare != 0 or bare_out.strip() != 'true':
            self._tlog(f"[bold {THEME['error']}]Repository is not a bare repository: {GIT_DIR} ({bare_err})[/]", task_idx, True)
            return 'invalid'
        return 'valid'

    def _detect_git_operation_state(self) -> str:
        if (GIT_DIR / 'rebase-merge').is_dir() or (GIT_DIR / 'rebase-apply').is_dir():
            return 'rebase'
        if (GIT_DIR / 'MERGE_HEAD').is_file():
            return 'merge'
        if (GIT_DIR / 'CHERRY_PICK_HEAD').is_file():
            return 'cherry-pick'
        if (GIT_DIR / 'REVERT_HEAD').is_file():
            return 'revert'
        if (GIT_DIR / 'BISECT_LOG').is_file():
            return 'bisect'
        return 'none'


    async def _ensure_repo_defaults(self):
        rc, val, _ = await self._run_raw('config', '--get', 'status.showUntrackedFiles')
        if val.strip() != 'no':
            await self._run_raw('config', 'status.showUntrackedFiles', 'no')

    def _canon_url(self, url: str) -> str:
        url = url.rstrip('/').removesuffix('.git')
        for prefix, replacement in [
            ('git@github.com:', 'github.com/'),
            ('ssh://git@github.com/', 'github.com/'),
            ('https://github.com/', 'github.com/'),
            ('http://github.com/', 'github.com/'),
        ]:
            if url.startswith(prefix):
                return replacement + url[len(prefix):]
        return url

    async def _get_fetch_source(self) -> str:
        want = self._canon_url(self.profile.repo_url)
        for remote in ('origin', 'dusky-upstream'):
            rc, url, _ = await self._run_raw('remote', 'get-url', remote)
            if rc == 0 and self._canon_url(url.strip()) == want:
                return remote
        return self.profile.repo_url

    async def _fetch_with_retry(self, source: str, tracking_ref: str, task_idx: int) -> bool:
        FETCH_TIMEOUT = GLOBAL_CONFIG.get("git", {}).get("fetch_timeout", 60)
        MAX_ATTEMPTS = GLOBAL_CONFIG.get("git", {}).get("fetch_max_attempts", 5)
        wait = 2
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._tlog(f"[dim]Fetch attempt {attempt}/{MAX_ATTEMPTS}...[/dim]", task_idx)
            cmd = ['git', f'--git-dir={GIT_DIR}', f'--work-tree={WORK_TREE}',
                   'fetch', '--no-write-fetch-head', source, f'+refs/heads/{self.profile.branch}:{tracking_ref}']
            try:
                proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=_git_env(), start_new_session=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._tlog(f"[bold {THEME['error']}]Fetch spawn failed: {escape(str(e))}[/]", task_idx, True)
                rc = 1
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(wait)
                    wait = min(wait * 2, 60)
                continue
            try:
                try:
                    async with asyncio.timeout(FETCH_TIMEOUT):
                        stdout, _ = await proc.communicate()
                    output = stdout.decode('utf-8', errors='surrogateescape').strip()
                    if output:
                        self._tlog(f"[dim]{escape(output)}[/dim]", task_idx)
                    rc = proc.returncode
                except TimeoutError:
                    await _terminate_process_group(proc)
                    rc = 124
            except asyncio.CancelledError:
                await _terminate_process_group(proc)
                raise
            if rc == 0:
                return True
            if attempt < MAX_ATTEMPTS:
                reason = "timed out" if rc == 124 else f"rc={rc}"
                self._tlog(f"[bold {THEME['warning']}]Fetch {attempt}/{MAX_ATTEMPTS} {reason}. Retrying in {wait}s...[/]", task_idx, True)
                await asyncio.sleep(wait)
                wait = min(wait * 2, 60)
        return False

    async def _clone_with_retry(self, task_idx: int) -> bool:
        CLONE_TIMEOUT = GLOBAL_CONFIG.get("git", {}).get("clone_timeout", 120)
        MAX_ATTEMPTS = GLOBAL_CONFIG.get("git", {}).get("clone_max_attempts", 5)
        wait = 2
        # Never clone directly into live GIT_DIR and never rmtree it on
        # failure: it may have appeared independently. Clone into an
        # exclusively owned temp dir and publish only after success.
        parent = GIT_DIR.parent
        with suppress(OSError):
            parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._tlog(f"[dim]Clone attempt {attempt}/{MAX_ATTEMPTS}...[/dim]", task_idx)
            tmpdir: Path | None = None
            try:
                tmpdir = Path(tempfile.mkdtemp(prefix=f".{GIT_DIR.name}.clone.", dir=str(parent)))
                with suppress(OSError):
                    tmpdir.chmod(0o700)
                dest = tmpdir / "repo.git"
                cmd = ['git', 'clone', '--bare', '--branch', self.profile.branch, self.profile.repo_url, str(dest)]
                try:
                    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=_git_env(), start_new_session=True)
                    async with asyncio.timeout(CLONE_TIMEOUT):
                        stdout, _ = await proc.communicate()
                    output = stdout.decode('utf-8', errors='surrogateescape').strip()
                    if output:
                        self._tlog(f"[dim]{escape(output)}[/dim]", task_idx)
                    rc = proc.returncode
                except TimeoutError:
                    await _terminate_process_group(proc)
                    rc = 124
                except asyncio.CancelledError:
                    # Reap before deleting: never remove staging while the
                    # clone child may still be writing there.
                    await _terminate_process_group(proc)
                    with suppress(OSError):
                        shutil.rmtree(str(tmpdir), ignore_errors=True)
                    raise
                except Exception as e:
                    # Never leak our staging dir on failure; never touch live GIT_DIR.
                    with suppress(OSError):
                        shutil.rmtree(str(tmpdir), ignore_errors=True)
                    self._tlog(f"[bold {THEME['error']}]Clone attempt failed: {escape(str(e))}[/]", task_idx, True)
                    rc = 1
            except asyncio.CancelledError:
                with suppress(OSError):
                    if tmpdir is not None:
                        shutil.rmtree(str(tmpdir), ignore_errors=True)
                raise
            except OSError as e:
                self._tlog(f"[bold {THEME['error']}]Cannot create clone staging dir: {escape(str(e))}[/]", task_idx, True)
                return False
            if rc == 0:
                try:
                    # Publish atomically only if GIT_DIR is still absent.
                    if GIT_DIR.exists() or GIT_DIR.is_symlink():
                        self._tlog(f"[bold {THEME['error']}]GIT_DIR appeared during clone; refusing to overwrite: {GIT_DIR}[/]", task_idx, True)
                        with suppress(OSError):
                            shutil.rmtree(str(tmpdir), ignore_errors=True)
                        return False
                    os.rename(str(dest), str(GIT_DIR))
                    with suppress(OSError):
                        shutil.rmtree(str(tmpdir), ignore_errors=True)
                    await self._run_raw('config', 'remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*')
                    return True
                except OSError as e:
                    self._tlog(f"[bold {THEME['error']}]Failed to publish cloned repo: {escape(str(e))}[/]", task_idx, True)
                    with suppress(OSError):
                        shutil.rmtree(str(tmpdir), ignore_errors=True)
                    return False
            # Failure: remove only our own staging dir, never live GIT_DIR.
            with suppress(OSError):
                if tmpdir is not None:
                    shutil.rmtree(str(tmpdir), ignore_errors=True)
            if attempt < MAX_ATTEMPTS:
                reason = "timed out" if rc == 124 else f"rc={rc}"
                self._tlog(f"[bold {THEME['warning']}]Clone {attempt}/{MAX_ATTEMPTS} {reason}. Retrying in {wait}s...[/]", task_idx, True)
                await asyncio.sleep(wait)
                wait = min(wait * 2, 60)
        return False

    async def _collect_dir_collision_roots(self, root_rel: str, tracked_exact: dict,
                                            tracked_descendants: dict, out_dict: dict):
        stack = [root_rel]
        while stack:
            rel = stack.pop()
            abs_path = WORK_TREE / rel
            if not (abs_path.exists() or abs_path.is_symlink()):
                continue
            if abs_path.is_symlink() or not abs_path.is_dir():
                if rel not in tracked_exact:
                    out_dict[rel] = 1
                continue
            if rel in tracked_exact:
                out_dict[rel] = 1
                continue
            try:
                children = [p.name for p in abs_path.iterdir()]
            except OSError:
                children = []
            if rel in tracked_descendants:
                if not children:
                    out_dict[rel] = 1
                else:
                    for child in children:
                        stack.append(f"{rel}/{child}")
            else:
                out_dict[rel] = 1

    async def _backup_worktree_collisions(self, ref: str, honor_tracked: bool, task_idx: int) -> bool:
        rc, ls_tree, err = await self._run_raw('ls-tree', '-r', '-z', '--name-only', ref)
        if rc != 0:
            self._tlog(f"[bold {THEME['error']}]Failed to list incoming tree {escape(ref)}: {escape(err)}[/]", task_idx, True)
            return False
        incoming = [f for f in ls_tree.split('\0') if f]

        tracked_exact: dict = {}
        tracked_descendants: dict = {}
        if honor_tracked:
            rc2, ls_files, err2 = await self._run_raw('ls-files', '-z')
            if rc2 != 0:
                self._tlog(f"[bold {THEME['error']}]Failed to list tracked files: {escape(err2)}[/]", task_idx, True)
                return False
            rc_head, head_files, head_err = await self._run_raw('ls-tree', '-r', '-z', '--name-only', 'HEAD')
            if rc_head != 0:
                self._tlog(f"[bold {THEME['error']}]Failed to list HEAD paths: {escape(head_err)}[/]", task_idx, True)
                return False
            # A staged deletion does not make recreated local content an
            # unrelated collision. Leave it for the local-change snapshot so
            # unchanged upstream content preserves the recreated work-tree file.
            for f in (ls_files + '\0' + head_files).split('\0'):
                if not f:
                    continue
                tracked_exact[f] = 1
                parts = f.split('/')
                for i in range(1, len(parts)):
                    tracked_descendants['/'.join(parts[:i])] = 1

        collision_candidates: dict = {}
        for tgt in incoming:
            abs_path = WORK_TREE / tgt
            if abs_path.exists() or abs_path.is_symlink():
                if abs_path.is_dir() and not abs_path.is_symlink():
                    if honor_tracked and tgt in tracked_descendants:
                        await self._collect_dir_collision_roots(tgt, tracked_exact, tracked_descendants, collision_candidates)
                    else:
                        collision_candidates[tgt] = 1
                elif not honor_tracked or tgt not in tracked_exact:
                    collision_candidates[tgt] = 1
            ancestor = ""
            remaining = tgt
            while '/' in remaining:
                part, remaining = remaining.split('/', 1)
                ancestor = f"{ancestor}/{part}" if ancestor else part
                abs_anc = WORK_TREE / ancestor
                if abs_anc.exists() or abs_anc.is_symlink():
                    if abs_anc.is_symlink() or not abs_anc.is_dir():
                        if not honor_tracked or ancestor not in tracked_exact:
                            collision_candidates[ancestor] = 1
                        break

        collision_roots: dict = {}
        for coll in collision_candidates:
            skip = any(
                '/'.join(coll.split('/')[:i]) in collision_candidates
                for i in range(1, len(coll.split('/')))
            )
            if not skip:
                collision_roots[coll] = 1

        if not collision_roots:
            self._last_collision_count = 0
            self._last_collision_dir = ""
            self._tlog(f"[bold {THEME['success']}]No structural filesystem conflicts detected.[/]", task_idx)
            return True

        candidate_paths = [WORK_TREE / r for r in collision_roots]
        required_bytes = backup_required_bytes(candidate_paths)
        if required_bytes is None:
            self._tlog(f"[bold {THEME['error']}]Cannot size collision data (I/O error); aborting backup.[/]", task_idx, True)
            return False
        backup_base = backups_dir()
        if not check_disk_space(backup_base):
            return False
        if not ensure_free_space_for_bytes(backup_base, required_bytes, "collision backup"):
            return False

        backup_dir = make_private_dir_under(backup_base, f"moved_aside_{RUN_TIMESTAMP}")
        if not backup_dir:
            self._tlog(f"[bold {THEME['error']}]Failed to create collision backup directory[/]", task_idx, True)
            return False

        self._last_collision_count = len(collision_roots)
        self._last_collision_dir = str(backup_dir)

        # Separate payload from metadata: payload under payload/, metadata
        # under .meta/ so a tracked INFO.txt/MOVED_PATHS.txt can never collide.
        meta_dir = backup_dir / ".meta"
        payload_root = backup_dir / "payload"
        try:
            meta_dir.mkdir(parents=True, exist_ok=True)
            meta_dir.chmod(0o700)
            payload_root.mkdir(parents=True, exist_ok=True)
            payload_root.chmod(0o700)
            _fsync_directory(meta_dir)
            _fsync_directory(payload_root)
            _fsync_directory(backup_dir)
            _atomic_write_text(
                meta_dir / "INFO.txt",
                f"Dusky work-tree collision backup\nCreated: {RUN_TIMESTAMP}\nRef: {ref}\nWork tree: {WORK_TREE}\n",
            )
            _atomic_write_text(meta_dir / "STATUS", "pending-collision\n")
        except OSError as e:
            self._tlog(f"[bold {THEME['error']}]Cannot durably initialize collision recovery metadata: {escape(str(e))}[/]", task_idx, True)
            return False

        moved_log = meta_dir / "MOVED_PATHS.txt"
        journal_log = meta_dir / "JOURNAL.txt"
        try:
            _atomic_write_text(moved_log, "")
            _atomic_write_text(journal_log, "")
        except OSError as e:
            self._tlog(f"[bold {THEME['error']}]Cannot durably initialize collision journals: {escape(str(e))}[/]", task_idx, True)
            return False

        self._tlog(f"[bold {THEME['warning']}]{len(collision_roots)} work-tree collision(s) found. Backing up...[/]", task_idx, True)
        for coll_rel in collision_roots:
            coll_src = WORK_TREE / coll_rel
            if not (coll_src.exists() or coll_src.is_symlink()):
                continue
            coll_dest = payload_root / coll_rel
            coll_dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Record journal before move for rollback/diagnostics.
                if not _append_manifest_line(journal_log, {"src": coll_rel, "dest": f"payload/{coll_rel}", "type": "move"}):
                    self._tlog(f"[bold {THEME['error']}]Failed to journal collision {escape(coll_rel)}[/]", task_idx, True)
                    return False
                shutil.move(str(coll_src), str(coll_dest))
                if not _fsync_tree(coll_dest):
                    self._tlog(f"[bold {THEME['error']}]Collision payload could not be made durable: {escape(coll_rel)}[/]", task_idx, True)
                    return False
                with suppress(OSError):
                    _fsync_directory(coll_src.parent)
                self._tlog(f"[dim]  → Backed up collision: {escape(coll_rel)}[/dim]", task_idx)
                if not _append_manifest_line(moved_log, {"path": coll_rel}):
                    self._tlog(f"[bold {THEME['error']}]Failed to record moved path {escape(coll_rel)}[/]", task_idx, True)
                    return False
            except Exception as e:
                self._tlog(f"[bold {THEME['error']}]Failed to move collision {escape(coll_rel)}: {escape(str(e))}[/]", task_idx, True)
                return False

        if not await asyncio.to_thread(_fsync_tree, backup_dir):
            self._tlog(f"[bold {THEME['error']}]Collision recovery tree could not be made fully durable; aborting.[/]", task_idx, True)
            return False
        self._tlog(f"[bold {THEME['success']}]Collisions backed up → {backup_dir}[/]", task_idx, True)
        return True

    @staticmethod
    def _parse_raw_diff(raw: str) -> list[tuple[str, str, str, str]]:
        rows: list[tuple[str, str, str, str]] = []
        recs = raw.split('\0')
        i = 0
        while i + 1 < len(recs):
            meta = recs[i].lstrip(':')
            path = recs[i + 1]
            i += 2
            if not meta or not path:
                continue
            parts = meta.split()
            if len(parts) < 5:
                continue
            oldmode, _newmode, oldoid, _newoid, status = parts[:5]
            rows.append((path, status.rstrip('0123456789'), oldmode, oldoid))
        return rows

    async def _capture_tracked_changes(self) -> tuple[list, dict, dict, dict]:
        """Capture HEAD→index and index→worktree independently, then union.

        ``self._actual_staged_paths`` records only real HEAD/index deltas so
        ordinary unstaged edits do not create false pending-staged recovery.
        """
        with suppress(Exception):
            await self._run_raw('update-index', '-q', '--refresh')
        rc_i, raw_i, err_i = await self._run_raw('diff-index', '--cached', '--raw', '--no-renames', '-z', 'HEAD', '--')
        if rc_i != 0:
            raise RuntimeError(f"diff-index --cached HEAD failed (rc={rc_i}): {err_i}")
        rc_w, raw_w, err_w = await self._run_raw('diff-files', '--raw', '--no-renames', '-z', '--')
        if rc_w != 0:
            raise RuntimeError(f"diff-files failed (rc={rc_w}): {err_w}")

        index_rows = self._parse_raw_diff(raw_i)
        work_rows = self._parse_raw_diff(raw_w)
        self._actual_staged_paths = {r[0] for r in index_rows}
        union: list[str] = []
        status_map: dict[str, str] = {}
        old_mode_map: dict[str, str] = {}
        old_oid_map: dict[str, str] = {}
        for path, status, mode, oid in [*index_rows, *work_rows]:
            if path not in status_map:
                union.append(path)
            status_map[path] = status
            if mode and mode.strip('0'):
                old_mode_map.setdefault(path, mode)
            if oid and oid.strip('0'):
                old_oid_map.setdefault(path, oid)
        # HEAD is the old upstream baseline for EVERY path. In particular,
        # staged additions have no old blob, and a staged deletion can coexist
        # with a recreated (now untracked) local file that must be backed up.
        signatures: dict[str, tuple] = {}
        for path in union:
            rc_h, rec, err_h = await self._run_raw('ls-tree', '-z', 'HEAD', '--', self._literal_pathspec(path))
            if rc_h != 0:
                raise RuntimeError(f"Cannot capture HEAD metadata for {path}: {err_h}")
            old_mode_map[path], old_oid_map[path] = "", ""
            if rec:
                if '\t' not in rec:
                    raise RuntimeError(f"Malformed HEAD metadata for {path}")
                toks = rec.split('\t', 1)[0].split()
                if len(toks) < 3:
                    raise RuntimeError(f"Malformed HEAD metadata for {path}")
                old_mode_map[path], old_oid_map[path] = toks[0], toks[2]
            src = WORK_TREE / path
            try:
                st = src.lstat()
            except FileNotFoundError:
                status_map[path] = "D"
                signatures[path] = ("missing",)
                continue
            status_map[path] = "M" if old_oid_map[path] else "A"
            if stat.S_ISLNK(st.st_mode):
                content = os.readlink(src)
            elif stat.S_ISREG(st.st_mode):
                content = await asyncio.to_thread(file_checksum, src)
                if not content:
                    raise RuntimeError(f"Cannot read local content for {path}")
            else:
                content = None
            signatures[path] = (st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns, content)
        rc_index, index_bytes = await self._run_raw_bytes('ls-files', '--stage', '-z')
        rc_head, head, err_head = await self._run_raw('rev-parse', '--verify', 'HEAD')
        if rc_index != 0 or rc_head != 0:
            raise RuntimeError(f"Cannot fingerprint index/HEAD before reset: {err_head}")
        # Status/path equality alone misses edits to an already-modified file
        # and changes to the new side of a staged blob.
        self._capture_signature = (head, index_bytes, signatures)
        return union, status_map, old_mode_map, old_oid_map

    async def _backup_user_modifications(self, change_paths: list, change_status: dict, task_idx: int,
                                         change_old_mode: dict | None = None, change_old_oid: dict | None = None) -> Path | None:
        if not change_paths:
            return None
        change_old_mode = change_old_mode or {}
        change_old_oid = change_old_oid or {}

        backup_base = backups_dir()
        candidate_paths = [WORK_TREE / p for p in change_paths if change_status.get(p) != 'D']
        required_bytes = backup_required_bytes(candidate_paths)
        if required_bytes is None:
            self._tlog(f"[bold {THEME['error']}]Cannot size modified data (I/O error); aborting backup.[/]", task_idx, True)
            return None
        if not check_disk_space(backup_base):
            return None
        if not ensure_free_space_for_bytes(backup_base, required_bytes, "modified-files backup"):
            return None

        backup_dir = make_private_dir_under(backup_base, f"your_changes_{RUN_TIMESTAMP}")
        if not backup_dir:
            self._tlog(f"[bold {THEME['error']}]Failed to create user-mods backup dir[/]", task_idx, True)
            return None

        meta_dir = backup_dir / ".meta"
        payload_root = backup_dir / "payload"
        staged_root = meta_dir / "staged"
        manifest = meta_dir / "MANIFEST.txt"
        try:
            meta_dir.mkdir(parents=True, exist_ok=True)
            meta_dir.chmod(0o700)
            payload_root.mkdir(parents=True, exist_ok=True)
            payload_root.chmod(0o700)
            staged_root.mkdir(parents=True, exist_ok=True)
            staged_root.chmod(0o700)
            _fsync_directory(staged_root)
            _fsync_directory(meta_dir)
            _fsync_directory(payload_root)
            _fsync_directory(backup_dir)
            _atomic_write_text(manifest, "")
            _atomic_write_text(meta_dir / "STATUS", "pending\n")
            _atomic_write_text(
                meta_dir / "INFO.txt",
                f"Dusky user-mods backup\nCreated: {RUN_TIMESTAMP}\nWork tree: {WORK_TREE}\n"
                f"Staging is NOT restored automatically; staged blobs are preserved under .meta/staged/ for recovery.\n",
            )
            capture_ctx = {
                "old_head": getattr(self, "_sync_old_head", ""),
                "target_oid": getattr(self, "_sync_target_oid", ""),
                "created": now_iso(),
                "created_epoch": time.time(),
            }
            _atomic_json_write(meta_dir / "CAPTURE.json", capture_ctx)
        except Exception as e:
            self._tlog(f"[bold {THEME['error']}]Cannot durably initialize local-change recovery metadata: {escape(str(e))}[/]", task_idx, True)
            return None

        for path in change_paths:
            st = change_status.get(path, "?")
            src = WORK_TREE / path
            # Capture staged/index content independently of the worktree diff,
            # including staged-only changes (worktree reverted to HEAD) and
            # files missing from the worktree. Fail closed on incomplete capture.
            if path in getattr(self, "_actual_staged_paths", set()):
                staged_ok = await self._capture_staged_blob(path, staged_root, manifest, task_idx)
                if not staged_ok:
                    self._tlog(f"[bold {THEME['error']}]Staged capture failed for: {escape(path)}[/]", task_idx, True)
                    return None
            if st == 'D' or not (src.exists() or src.is_symlink()):
                if not _append_manifest_line(manifest, {"status": st, "has_copy": 0, "path": path, "old_mode": change_old_mode.get(path, ""), "old_oid": change_old_oid.get(path, "")}):
                    self._tlog(f"[bold {THEME['error']}]Failed to write manifest for: {escape(path)}[/]", task_idx, True)
                    return None
                continue
            dest = payload_root / path
            ok = await asyncio.to_thread(_sync_copy_file, src, dest)
            if not ok:
                self._tlog(f"[bold {THEME['error']}]Backup failed for: {escape(path)}[/]", task_idx, True)
                return None
            if not _append_manifest_line(manifest, {"status": st, "has_copy": 1, "path": path, "old_mode": change_old_mode.get(path, ""), "old_oid": change_old_oid.get(path, "")}):
                self._tlog(f"[bold {THEME['error']}]Failed to write manifest for: {escape(path)}[/]", task_idx, True)
                return None

        if not await asyncio.to_thread(_fsync_tree, backup_dir):
            self._tlog(f"[bold {THEME['error']}]Local-change recovery tree could not be made fully durable; aborting.[/]", task_idx, True)
            return None
        self._tlog(f"[bold {THEME['success']}]Backed up {len(change_paths)} tracked change(s) → {backup_dir}[/]", task_idx, True)
        return backup_dir

    async def _capture_staged_blob(self, path: str, staged_root: Path, manifest: Path, task_idx: int) -> bool:
        """Preserve the index version of `path`, independent of worktree state.

        Returns True when staging is fully accounted for (blob saved, or
        conclusively absent from the index). Returns False on any transport
        or write failure so callers abort before reset.
        """
        # Staged mode/oid first, so deletions and modes are recorded too.
        rc_ls, ls_out, ls_err = await self._run_raw('ls-files', '-s', '-z', '--', self._literal_pathspec(path))
        if rc_ls != 0:
            self._tlog(f"[bold {THEME['error']}]Failed to read staged state for {escape(path)}: {escape(ls_err)}[/]", task_idx, True)
            return False
        staged_entry = ""
        staged_stage = ""
        for rec in ls_out.split('\0'):
            if not rec:
                continue
            # ls-files -s -z: "<mode> <oid> <stage>\t<path>\0"
            if '\t' not in rec or rec.split('\t', 1)[1] != path:
                continue
            meta = rec.split('\t', 1)[0]
            toks = meta.strip().split()
            stage = toks[2] if len(toks) > 2 else ""
            if stage == "0":
                staged_entry = meta
                staged_stage = stage
                break
            if not staged_entry:
                staged_entry = meta
                staged_stage = stage
        if not staged_entry:
            # Not in the index: staged deletion or never staged. Record it.
            if not _append_manifest_line(manifest, {"staged": "absent", "path": path}):
                return False
            return True
        toks = staged_entry.strip().split()
        staged_mode = toks[0] if toks else ""
        staged_oid = toks[1] if len(toks) > 1 else ""
        if not staged_oid or len(staged_oid) < 40:
            self._tlog(f"[bold {THEME['error']}]Invalid staged oid for {escape(path)}: {escape(staged_oid)}[/]", task_idx, True)
            return False
        rc_b, blob = await self._run_raw_bytes("cat-file", "blob", staged_oid, timeout_sec=30)
        if rc_b != 0:
            self._tlog(f"[bold {THEME['error']}]Failed to read staged blob for {escape(path)}[/]", task_idx, True)
            return False
        try:
            sdest = staged_root / path
            sdest.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive create: never overwrite silently, never follow symlinks.
            fd = os.open(str(sdest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(blob)
                    f.flush()
                    os.fsync(f.fileno())
                _fsync_directory(sdest.parent)
            except BaseException:
                with suppress(OSError):
                    os.close(fd)
                raise
            if not _append_manifest_line(staged_root.parent / "STAGED_MODES.txt",
                                           {"mode": staged_mode, "oid": staged_oid, "stage": staged_stage, "path": path}):
                return False
            if not _append_manifest_line(manifest, {"staged": 1, "mode": staged_mode, "stage": staged_stage, "path": path}):
                return False
        except OSError as e:
            self._tlog(f"[bold {THEME['error']}]Failed to store staged blob for {escape(path)}: {escape(str(e))}[/]", task_idx, True)
            return False
        return True

    async def _backup_full_tracked_tree(self, task_idx: int) -> Path | None:
        backup_base = backups_dir()
        rc, ls_files, err = await self._run_raw('ls-files', '-z')
        if rc != 0:
            self._tlog(f"[bold {THEME['error']}]Failed to list tracked files for full snapshot: {escape(err)}[/]", task_idx, True)
            return None
        tracked = [f for f in ls_files.split('\0') if f]
        required_bytes = backup_required_bytes([WORK_TREE / p for p in tracked])
        if required_bytes is None:
            self._tlog(f"[bold {THEME['error']}]Cannot size tracked tree (I/O error); aborting backup.[/]", task_idx, True)
            return None
        if not check_disk_space(backup_base):
            return None
        if not ensure_free_space_for_bytes(backup_base, required_bytes, "full tracked-tree backup"):
            return None

        backup_dir = make_private_dir_under(backup_base, f"full_snapshot_{RUN_TIMESTAMP}")
        if not backup_dir:
            self._tlog(f"[bold {THEME['error']}]Failed to create full tracked-tree backup dir[/]", task_idx, True)
            return None

        meta_dir = backup_dir / ".meta"
        payload_root = backup_dir / "payload"
        try:
            meta_dir.mkdir(parents=True, exist_ok=True)
            meta_dir.chmod(0o700)
            payload_root.mkdir(parents=True, exist_ok=True)
            payload_root.chmod(0o700)
            _fsync_directory(meta_dir)
            _fsync_directory(payload_root)
            _fsync_directory(backup_dir)
            _, head, _ = await self._run_raw('rev-parse', 'HEAD')
            _atomic_write_text(meta_dir / "INFO.txt", f"Dusky full tracked-tree backup\nCreated: {RUN_TIMESTAMP}\nHEAD: {head.strip()}\n")
            _atomic_write_text(meta_dir / "STATUS", "pending-full-snapshot\n")
        except OSError as e:
            self._tlog(f"[bold {THEME['error']}]Cannot durably initialize full-snapshot metadata: {escape(str(e))}[/]", task_idx, True)
            return None

        def _sync_copy_tree(tracked_files, work_tree, b_dir):
            success_count = 0
            failed: list[str] = []
            for p in tracked_files:
                src = work_tree / p
                dest = b_dir / p
                if not (src.exists() or src.is_symlink()):
                    # Tracked but absent in worktree (e.g. staged deletion):
                    # record as expected-missing, not as copied.
                    continue
                if _sync_copy_file(src, dest):
                    success_count += 1
                else:
                    failed.append(p)
            return success_count, failed

        copied, failed = await asyncio.to_thread(_sync_copy_tree, tracked, WORK_TREE, payload_root)
        # Record expected entries and verify; abort reset on any omission.
        try:
            _atomic_write_text(meta_dir / "EXPECTED.txt", json.dumps(tracked, ensure_ascii=False) + "\n")
            if failed:
                _atomic_write_text(meta_dir / "FAILED.txt", json.dumps(failed, ensure_ascii=False) + "\n")
        except OSError:
            self._tlog(f"[bold {THEME['error']}]Failed to write snapshot manifest; aborting reset.[/]", task_idx, True)
            return None

        if failed:
            self._tlog(f"[bold {THEME['error']}]Full snapshot incomplete: {len(failed)} file(s) failed; aborting reset. See {meta_dir}/FAILED.txt[/]", task_idx, True)
            return None

        if not await asyncio.to_thread(_fsync_tree, backup_dir):
            self._tlog(f"[bold {THEME['error']}]Full tracked-tree recovery payload could not be made durable; aborting.[/]", task_idx, True)
            return None
        self._tlog(f"[bold {THEME['success']}]Full tracked-tree backup: {backup_dir} ({copied} file(s))[/]", task_idx, True)
        return backup_dir

    async def _backup_git_history(self, task_idx: int) -> Path | None:
        backup_base = backups_dir()
        required_bytes = backup_required_bytes([GIT_DIR])
        if required_bytes is None:
            self._tlog(f"[bold {THEME['error']}]Cannot size git history (I/O error); aborting backup.[/]", task_idx, True)
            return None
        if not check_disk_space(backup_base):
            return None
        if not ensure_free_space_for_bytes(backup_base, required_bytes, "Git history backup"):
            return None

        backup_root = make_private_dir_under(backup_base, f"repo_history_{RUN_TIMESTAMP}")
        if not backup_root:
            self._tlog(f"[bold {THEME['error']}]Failed to create Git history backup dir[/]", task_idx, True)
            return None

        backup_repo = backup_root / "repo.git"
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                'cp', '-a', '--reflink=auto', str(GIT_DIR), str(backup_repo),
                start_new_session=True, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(float(GLOBAL_CONFIG["git"]["backup_timeout"])):
                    _out, err = await proc.communicate()
            except TimeoutError:
                await _terminate_process_group(proc)
                self._tlog(f"[bold {THEME['error']}]Git history backup timed out[/]", task_idx, True)
                return None
            if proc.returncode != 0:
                self._tlog(f"[bold {THEME['error']}]Failed to copy Git history: {escape(err.decode('utf-8', errors='replace'))}[/]", task_idx, True)
                return None
        except asyncio.CancelledError:
            if proc is not None:
                with suppress(Exception):
                    await _terminate_process_group(proc)
            raise
        except Exception as e:
            if proc is not None:
                with suppress(Exception):
                    await _terminate_process_group(proc)
            self._tlog(f"[bold {THEME['error']}]Exception copying git dir: {escape(str(e))}[/]", task_idx, True)
            return None

        # A successful cp exit only proves bytes reached the page cache. Flush
        # every copied regular file/directory before treating this mandatory
        # destructive-sync backup as durable.
        if not await asyncio.to_thread(_fsync_tree, backup_repo):
            self._tlog(
                f"[bold {THEME['error']}]Git history backup could not be made durable; aborting destructive sync.[/]",
                task_idx, True,
            )
            return None
        try:
            _atomic_write_text(
                backup_root / "INFO.txt",
                f"Dusky Git history backup\nCreated: {RUN_TIMESTAMP}\nSource: {GIT_DIR}\n",
            )
        except OSError as e:
            self._tlog(
                f"[bold {THEME['error']}]Git history backup metadata durability failed: {escape(str(e))}[/]",
                task_idx, True,
            )
            return None

        self._tlog(f"[bold {THEME['success']}]Git history preserved → {backup_root}[/]", task_idx, True)
        return backup_root

    def _rollback_collision_backup(self, backup_dir: Path) -> tuple[int, int]:
        restored = retained = 0
        meta = backup_dir / ".meta"
        paths: list[str] = []
        seen: set[str] = set()
        # JOURNAL is written+fsynced before each move, so it is authoritative
        # for the crash window between rename/copy and MOVED_PATHS publication.
        for source_name in ("JOURNAL.txt", "MOVED_PATHS.txt"):
            source = meta / source_name
            if not source.is_file():
                continue
            try:
                lines = source.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                rel = rec.get("src") if source_name == "JOURNAL.txt" else rec.get("path")
                if isinstance(rel, str) and rel and rel not in seen:
                    seen.add(rel)
                    paths.append(rel)

        for rel in reversed(paths):
            src = backup_dir / "payload" / rel
            dst = WORK_TREE / rel
            if not (src.exists() or src.is_symlink()):
                continue
            if dst.exists() or dst.is_symlink():
                retained += 1
                continue
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                if not _fsync_tree(dst):
                    retained += 1
                    continue
                with suppress(OSError):
                    _fsync_directory(src.parent)
                restored += 1
            except OSError:
                retained += 1
        # Whether restored or intentionally retained, this collision-move
        # transaction is resolved. Retained payload remains available for
        # manual recovery and is never auto-pruned.
        try:
            state = "completed-recovery-retained" if retained else "completed"
            _atomic_write_text(meta / "STATUS", state + "\n")
        except OSError:
            retained += 1
        return restored, retained

    async def _recover_pending_sync_artifacts(self, task_idx: int) -> None:
        """Recover/resolve the previous durable sync transaction before syncing.

        ``applying`` is deliberately fail-closed: a crash while ``git reset`` or
        checkout was in flight can leave HEAD/worktree/index partially updated.
        Without proof that the destructive command returned successfully we do
        not guess.  All recovery payloads are retained and a new sync is blocked.
        ``reset-applied`` is the explicit safe checkpoint from which local-state
        restoration can be resumed automatically.
        """
        base = backups_dir()
        if not base.is_dir():
            return

        txn = await asyncio.to_thread(self._load_sync_txn)
        txn_state = str(txn.get("state", "")) if txn else ""
        terminal_states = {"completed", "completed-recovery-retained", "aborted-before-apply"}
        if txn and txn_state == "recovery-blocked":
            recovery = str(txn.get("local_backup") or txn.get("collision_backup") or base)
            raise RuntimeError(
                f"previous sync is recovery-blocked; inspect retained recovery data at {recovery} before another sync"
            )
        if txn and txn_state not in terminal_states:
            self._sync_old_head = str(txn.get("old_head", ""))
            self._sync_target_oid = str(txn.get("target_oid", ""))
            self._last_collision_dir = str(txn.get("collision_backup", ""))

        retained_any = False

        # Resolve interrupted collision moves. JOURNAL.txt makes the crash
        # window after a move but before MOVED_PATHS publication recoverable.
        for d in sorted(base.glob("moved_aside_*")):
            try:
                status = (d / ".meta" / "STATUS").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if status != "pending-collision":
                continue
            restored, retained = await asyncio.to_thread(self._rollback_collision_backup, d)
            retained_any = retained_any or retained > 0
            if restored or retained:
                self._tlog(
                    f"[bold {THEME['warning']}]Recovered interrupted collision transaction: "
                    f"restored={restored}, retained={retained}, backup={escape(str(d))}[/]",
                    task_idx, True,
                )
                if hasattr(self.app, "add_warning"):
                    self.app.add_warning(
                        "recovery",
                        f"interrupted collision transaction: restored={restored}, retained={retained}",
                        recovery=str(d),
                    )

        rc_h, head_out, _ = await self._run_raw('rev-parse', '--verify', '-q', 'HEAD')
        head = head_out.strip() if rc_h == 0 else ""

        # A pre-application checkpoint proves reset/checkout was never started.
        # Local worktree/index state therefore remains authoritative; keep its
        # captured backup as recovery evidence but do not copy it over newer
        # editor changes.
        if txn and txn_state in {"prepared", "collisions-durable", "snapshot-durable"}:
            local_raw = txn.get("local_backup", "")
            if isinstance(local_raw, str) and local_raw:
                local_dir = Path(local_raw)
                if local_dir.is_dir():
                    try:
                        status_file = local_dir / ".meta" / "STATUS"
                        current = status_file.read_text(encoding="utf-8").strip() if status_file.is_file() else ""
                        if current == "pending":
                            _atomic_write_text(status_file, "completed-capture-retained\n")
                            retained_any = True
                    except OSError:
                        retained_any = True
            full_raw = txn.get("full_tracked_backup", "")
            if isinstance(full_raw, str) and full_raw:
                full_dir = Path(full_raw)
                try:
                    status_file = full_dir / ".meta" / "STATUS"
                    if status_file.is_file() and status_file.read_text(encoding="utf-8").strip() == "pending-full-snapshot":
                        _atomic_write_text(status_file, "completed-recovery-retained\n")
                        retained_any = True
                except OSError:
                    retained_any = True
            await asyncio.to_thread(
                self._finish_sync_txn,
                "aborted-before-apply",
                recovered_from=txn_state,
                recovery_retained=retained_any,
            )
            self._tlog(
                f"[bold {THEME['warning']}]Recovered interrupted pre-apply sync ({escape(txn_state)}); destructive application had not begun.[/]",
                task_idx, True,
            )
            if hasattr(self.app, "add_warning"):
                self.app.add_warning("recovery", f"interrupted sync recovered before application ({txn_state})")
            return

        # Once reset/checkout has been launched, only the explicit post-command
        # checkpoint proves the worktree/index update completed.  Guessing from
        # HEAD alone is unsafe because reset can update HEAD before all files.
        if txn and txn_state == "applying":
            recovery = str(txn.get("local_backup") or txn.get("collision_backup") or txn.get("full_tracked_backup") or base)
            await asyncio.to_thread(
                self._finish_sync_txn,
                "recovery-blocked",
                recovered_from="applying",
                observed_head=head,
                recovery=str(recovery),
            )
            if hasattr(self.app, "add_warning"):
                self.app.add_warning(
                    "recovery",
                    "sync was interrupted while destructive Git application was in flight; automatic overwrite is unsafe",
                    recovery=recovery,
                )
            raise RuntimeError(
                f"sync was interrupted during destructive Git application; automatic recovery is intentionally blocked. "
                f"Recovery data: {recovery}"
            )

        def load_change_manifest(d: Path) -> tuple[list[str], dict[str, str], dict[str, str], dict[str, str]] | None:
            paths: list[str] = []
            status_map: dict[str, str] = {}
            mode_map: dict[str, str] = {}
            oid_map: dict[str, str] = {}
            try:
                lines = (d / ".meta" / "MANIFEST.txt").read_text(
                    encoding="utf-8", errors="surrogateescape"
                ).splitlines()
                for line in lines:
                    rec = json.loads(line)
                    if not isinstance(rec, dict) or "status" not in rec:
                        continue
                    rel = rec.get("path")
                    if not isinstance(rel, str) or not rel or rel in status_map:
                        continue
                    paths.append(rel)
                    status_map[rel] = str(rec.get("status", "?"))
                    mode_map[rel] = str(rec.get("old_mode", ""))
                    oid_map[rel] = str(rec.get("old_oid", ""))
            except (OSError, ValueError, TypeError):
                return None
            return paths, status_map, mode_map, oid_map

        processed_local: set[Path] = set()
        if txn and txn_state == "reset-applied":
            target = str(txn.get("target_oid", ""))
            if not target or head != target:
                recovery = str(txn.get("local_backup") or txn.get("collision_backup") or base)
                await asyncio.to_thread(
                    self._finish_sync_txn,
                    "recovery-blocked",
                    recovered_from="reset-applied",
                    observed_head=head,
                    recovery=str(recovery),
                )
                raise RuntimeError(
                    f"transaction says reset completed to {target or '<missing>'}, but HEAD is {head or '<missing>'}; "
                    f"recovery retained at {recovery}"
                )

            local_raw = txn.get("local_backup", "")
            captured = txn.get("captured_paths", [])
            if isinstance(local_raw, str) and local_raw:
                local_dir = Path(local_raw)
                if not local_dir.is_dir():
                    if isinstance(captured, list) and captured:
                        await asyncio.to_thread(
                            self._finish_sync_txn,
                            "recovery-blocked",
                            recovered_from="reset-applied",
                            observed_head=head,
                            recovery=local_raw,
                        )
                        raise RuntimeError(f"required local-change recovery bundle is missing: {local_raw}")
                else:
                    processed_local.add(local_dir)
                    try:
                        local_status = (local_dir / ".meta" / "STATUS").read_text(encoding="utf-8").strip()
                    except OSError:
                        local_status = ""
                    if local_status in {"pending", "pending-restore"}:
                        manifest_data = load_change_manifest(local_dir)
                        if manifest_data is None:
                            await asyncio.to_thread(
                                self._finish_sync_txn,
                                "recovery-blocked",
                                recovery=str(local_dir),
                                reason="manifest unreadable",
                            )
                            raise RuntimeError(f"local-change recovery manifest is unreadable: {local_dir}")
                        paths, status_map, mode_map, oid_map = manifest_data
                        ok = True
                        if paths:
                            ok = await self._restore_user_modifications(
                                local_dir, paths, status_map, mode_map, oid_map, task_idx
                            )
                        if not ok:
                            retained_any = True
                        self._tlog(
                            f"[bold {THEME['warning']}]Resumed interrupted local-change restoration from {escape(str(local_dir))}: "
                            f"{'complete' if ok else 'recovery retained'}[/]",
                            task_idx, True,
                        )
                        if hasattr(self.app, "add_warning"):
                            self.app.add_warning(
                                "recovery", "resumed interrupted local-change restoration", recovery=str(local_dir)
                            )
                    elif local_status not in {"completed", "completed-capture-retained"}:
                        retained_any = True
                        if hasattr(self.app, "add_warning"):
                            self.app.add_warning(
                                "recovery",
                                f"local-change recovery still requires manual attention ({local_status or 'unknown'})",
                                recovery=str(local_dir),
                            )

            full_raw = txn.get("full_tracked_backup", "")
            if isinstance(full_raw, str) and full_raw:
                full_dir = Path(full_raw)
                if full_dir.is_dir():
                    try:
                        status_file = full_dir / ".meta" / "STATUS"
                        if status_file.is_file() and status_file.read_text(encoding="utf-8").strip() == "pending-full-snapshot":
                            _atomic_write_text(status_file, "completed-recovery-retained\n")
                            retained_any = True
                            if hasattr(self.app, "add_warning"):
                                self.app.add_warning(
                                    "recovery", "full tracked-tree snapshot retained after interrupted reset", recovery=str(full_dir)
                                )
                    except OSError:
                        retained_any = True

            await asyncio.to_thread(
                self._finish_sync_txn,
                "completed-recovery-retained" if retained_any else "completed",
                recovered_from="reset-applied",
                restore_ok=not retained_any,
            )

        # Backward-compatible recovery for standalone artifacts from an older
        # run/journal. Only resume when HEAD proves the recorded target landed.
        for d in sorted(base.glob("your_changes_*")):
            if d in processed_local:
                continue
            try:
                status = (d / ".meta" / "STATUS").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if status in {"pending-staged", "pending-quarantine", "pending-upstream-conflict"}:
                if hasattr(self.app, "add_warning"):
                    self.app.add_warning(
                        "recovery", f"manual recovery retained ({status})", recovery=str(d)
                    )
                continue
            if status not in {"pending", "pending-restore"}:
                continue
            try:
                ctx = json.loads((d / ".meta" / "CAPTURE.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(ctx, dict):
                continue
            target = str(ctx.get("target_oid", ""))
            if not target or head != target:
                continue
            manifest_data = load_change_manifest(d)
            if manifest_data is None:
                continue
            paths, status_map, mode_map, oid_map = manifest_data
            if paths:
                ok = await self._restore_user_modifications(d, paths, status_map, mode_map, oid_map, task_idx)
                self._tlog(
                    f"[bold {THEME['warning']}]Resumed interrupted local-change restoration from {escape(str(d))}: "
                    f"{'complete' if ok else 'recovery retained'}[/]",
                    task_idx, True,
                )
                if hasattr(self.app, "add_warning"):
                    self.app.add_warning("recovery", "resumed interrupted local-change restoration", recovery=str(d))

        # Full snapshots are intentionally manual recovery artifacts. Mark an
        # interrupted capture as retained/resolved so it is visible but does not
        # masquerade as an active transaction on every launch.
        for d in sorted(base.glob("full_snapshot_*")):
            try:
                status_file = d / ".meta" / "STATUS"
                if status_file.read_text(encoding="utf-8").strip() != "pending-full-snapshot":
                    continue
                _atomic_write_text(status_file, "completed-recovery-retained\n")
                if hasattr(self.app, "add_warning"):
                    self.app.add_warning("recovery", "full tracked-tree recovery snapshot retained", recovery=str(d))
            except OSError:
                continue

    async def _get_head_path_meta(self, path: str) -> tuple[str, str]:
        # Fail-closed: a git error must not masquerade as "absent upstream
        # path" (which would cause restore/merge to overwrite upstream data).
        rc, record, err = await self._run_raw('ls-tree', '-z', 'HEAD', '--', self._literal_pathspec(path))
        if rc != 0:
            raise RuntimeError(f"ls-tree HEAD -- {path} failed (rc={rc}): {err}")
        if not record.strip():
            return ('', '')
        try:
            meta_part = record.split('\t')[0]
            parts = meta_part.strip().split()
            if len(parts) >= 3:
                return (parts[0], parts[2])
        except Exception:
            pass
        return ('', '')


    async def _restore_user_modifications(self, backup_dir: Path, change_paths: list,
                                           change_status: dict, change_old_mode: dict,
                                           change_old_oid: dict, task_idx: int) -> bool:
        if not (backup_dir and backup_dir.is_dir() and change_paths):
            return True

        merge_dir: Path | None = None
        restore_count = merge_count = deletion_count = 0
        quarantined: list[str] = []
        upstream_overrode_deletions: list[str] = []
        all_ok = True

        for path in change_paths:
            status = change_status.get(path, "?")
            old_oid = change_old_oid.get(path, "")
            old_mode = change_old_mode.get(path, "")
            # New layout stores payload under payload/; accept legacy root for back-compat.
            cand_new = backup_dir / "payload" / path
            cand_old = backup_dir / path
            try:
                backup_src = cand_new if (cand_new.exists() or cand_new.is_symlink()) else cand_old
            except OSError:
                backup_src = cand_old
            target = WORK_TREE / path

            try:
                new_mode, new_oid = await self._get_head_path_meta(path)
            except RuntimeError as e:
                self._tlog(f"[bold {THEME['error']}]Cannot determine upstream state for {escape(path)}: {escape(str(e))} — preserving backup[/]", task_idx, True)
                all_ok = False
                continue
            old_oid_valid = bool(old_oid and old_oid.strip("0"))

            same_oid = (new_oid.lower() == old_oid.lower()) if (new_oid and old_oid) else False
            same_mode = (new_mode.lstrip('0') == old_mode.lstrip('0')) if (new_mode and old_mode) else False
            same_meta = same_oid and same_mode

            if status == 'D':
                if not new_oid:
                    action = "delete-preserved"
                elif old_oid_valid and same_meta:
                    action = "delete-safe"
                else:
                    action = "delete-upstream-won"
            else:
                has_copy = backup_src.exists() or backup_src.is_symlink()
                if not has_copy:
                    self._tlog(f"[bold {THEME['error']}]Missing backup source for {escape(path)} — preserving backup[/]", task_idx, True)
                    all_ok = False
                    continue
                if old_oid_valid:
                    # A tracked path removed upstream is an upstream change,
                    # not a safe restoration target. Preserve the user's copy
                    # in recovery while leaving the active path deleted.
                    safe = same_meta
                else:
                    # Genuine local addition absent from both upstream trees.
                    safe = not new_oid
                action = "restore" if safe else "merge"

            if action == "delete-preserved":
                deletion_count += 1

            elif action == "delete-safe":
                # Never recursively discard: only unlink files/symlinks in
                # place; unexpected directories are quarantined into recovery
                # storage so their contents survive.
                try:
                    try:
                        st_t = target.lstat()
                    except FileNotFoundError:
                        deletion_count += 1
                        self._tlog(f"[dim]  → Re-applied tracked deletion: {escape(path)}[/dim]", task_idx)
                        continue
                    if stat.S_ISDIR(st_t.st_mode) and not stat.S_ISLNK(st_t.st_mode):
                        q = await asyncio.to_thread(self._quarantine_path, backup_dir, "deleted_dirs", path, target)
                        if q is None:
                            self._tlog(f"[bold {THEME['error']}]Failed to preserve unexpected directory {escape(path)}; leaving in place[/]", task_idx, True)
                            all_ok = False
                            continue
                        quarantined.append(f"{path} -> {q}")
                        deletion_count += 1
                        self._tlog(f"[dim]  → Re-applied tracked deletion (dir preserved at {escape(str(q))}): {escape(path)}[/dim]", task_idx)
                    else:
                        target.unlink()
                        deletion_count += 1
                        self._tlog(f"[dim]  → Re-applied tracked deletion: {escape(path)}[/dim]", task_idx)
                except OSError as e:
                    self._tlog(f"[bold {THEME['error']}]Failed to re-apply deletion {escape(path)}: {escape(str(e))}[/]", task_idx, True)
                    all_ok = False

            elif action == "delete-upstream-won":
                # Upstream changed/reintroduced the path, so upstream wins at
                # the active pathname.  The user's prior state was a deletion;
                # retain that fact in recovery metadata instead of claiming a
                # successful byte restoration and deleting the backup.
                deletion_count += 1
                upstream_overrode_deletions.append(path)
                all_ok = False
                self._tlog(
                    f"[dim]  → Upstream version accepted for {escape(path)}; your prior local deletion is retained in recovery metadata[/dim]",
                    task_idx,
                )

            elif action == "merge":
                if not merge_dir:
                    backup_base = backups_dir()
                    merge_dir = make_private_dir_under(backup_base, f"manual_merge_{RUN_TIMESTAMP}")
                    if not merge_dir:
                        self._tlog(f"[bold {THEME['error']}]Failed to create merge dir[/]", task_idx, True)
                        all_ok = False
                        continue

                mdest = merge_dir / path
                mdest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    ok = await asyncio.to_thread(_sync_copy_file, backup_src, mdest)
                    if ok:
                        merge_count += 1
                        # Upstream won at the active path, but the user's
                        # displaced version still requires manual recovery.
                        # This is not a fully restored transaction.
                        all_ok = False
                        self._tlog(f"[dim]  → Upstream changed: {escape(path)} (your version saved for merge)[/dim]", task_idx)
                    else:
                        self._tlog(f"[bold {THEME['error']}]Failed to save merge copy: {escape(path)}[/]", task_idx, True)
                        all_ok = False
                except Exception as e:
                    self._tlog(f"[bold {THEME['error']}]Exception saving merge copy {escape(path)}: {escape(str(e))}[/]", task_idx, True)
                    all_ok = False

            elif action == "restore":
                parent = (WORK_TREE / path).parent
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    self._tlog(f"[bold {THEME['error']}]Cannot create parent dirs for {escape(path)}: {e}[/]", task_idx, True)
                    all_ok = False
                    continue
                try:
                    # Exclusive temp file in the parent dir (O_EXCL):
                    if backup_src.is_symlink():
                        # Recreate symlinks as symlinks; never write through.
                        link_target = os.readlink(backup_src)
                        fd, tmp_name = tempfile.mkstemp(prefix=".restore.", dir=str(parent))
                        os.close(fd)
                        tmp_file = Path(tmp_name)
                        try:
                            tmp_file.unlink()
                            os.symlink(link_target, tmp_file)
                            try:
                                st_now = target.lstat()
                                if stat.S_ISDIR(st_now.st_mode) and not stat.S_ISLNK(st_now.st_mode):
                                    q = await asyncio.to_thread(self._quarantine_path, backup_dir, "type_conflicts", path, target)
                                    if q is None:
                                        raise OSError("cannot preserve conflicting directory")
                                    quarantined.append(f"{path} -> {q}")
                            except FileNotFoundError:
                                pass
                            os.replace(str(tmp_file), str(target))
                            _fsync_directory(target.parent)
                        except BaseException:
                            with suppress(OSError):
                                tmp_file.unlink(missing_ok=True)
                            raise
                    else:
                        # Regular-file restore: preserve the backed-up file's
                        # intended permissions before atomic replacement.
                        # mkstemp creates 0600; without an explicit chmod the
                        # restored file would lose executable bits (0755->0600).
                        try:
                            st_src = backup_src.lstat()
                            if stat.S_ISDIR(st_src.st_mode):
                                raise OSError("backup payload is a directory; refusing file restore")
                            src_mode = stat.S_IMODE(st_src.st_mode)
                        except OSError as e:
                            # Fall back to the recorded git mode when the
                            # payload cannot be statted; never guess 0600.
                            if old_mode in ("100755", "100775"):
                                src_mode = 0o755
                            elif old_mode in ("100644", "100664", ""):
                                # Empty old_mode means untracked/new; keep
                                # payload default only when lstat succeeded.
                                # Here lstat failed, so fail closed.
                                raise OSError(f"cannot stat backup payload: {e}")
                            else:
                                src_mode = 0o644
                        fd, tmp_name = tempfile.mkstemp(prefix=".restore.", dir=str(parent))
                        tmp_file = Path(tmp_name)
                        try:
                            with os.fdopen(fd, "wb") as f:
                                try:
                                    data = backup_src.read_bytes()
                                except OSError as e:
                                    raise OSError(f"cannot read payload: {e}")
                                f.write(data)
                                f.flush()
                                os.fsync(f.fileno())
                            # Preserve permissions and supported metadata before
                            # replacement. copystat copies mode+times without
                            # following symlinks; chmod ensures the executable
                            # bit even if copystat is partial.
                            try:
                                shutil.copystat(backup_src, tmp_name, follow_symlinks=False)
                            except OSError:
                                pass
                            try:
                                os.chmod(tmp_name, src_mode)
                            except OSError as e:
                                raise OSError(f"cannot preserve permissions: {e}")
                            # mkstemp is exclusive; now atomically replace. The
                            # original is never displaced first, so a failed
                            # replace leaves both old destination and payload
                            # recoverable.
                            try:
                                st_now = target.lstat()
                                if stat.S_ISDIR(st_now.st_mode) and not stat.S_ISLNK(st_now.st_mode):
                                    # Type transition: preserve the directory tree.
                                    q = await asyncio.to_thread(self._quarantine_path, backup_dir, "type_conflicts", path, target)
                                    if q is None:
                                        raise OSError("cannot preserve conflicting directory")
                                    quarantined.append(f"{path} -> {q}")
                            except FileNotFoundError:
                                pass
                            os.replace(str(tmp_file), str(target))
                            _fsync_directory(target.parent)
                            # Verify content/type/mode after replacement before
                            # this path counts as restored.
                            try:
                                st_t = target.lstat()
                                if stat.S_ISDIR(st_t.st_mode) or stat.S_ISLNK(st_t.st_mode):
                                    raise OSError("restored target has unexpected type")
                                if stat.S_IMODE(st_t.st_mode) != src_mode:
                                    raise OSError(
                                        f"restored mode {oct(stat.S_IMODE(st_t.st_mode))} != expected {oct(src_mode)}"
                                    )
                                if target.read_bytes() != data:
                                    raise OSError("restored content mismatch")
                            except OSError as e:
                                raise OSError(f"post-restore verification failed: {e}")
                        except BaseException:
                            with suppress(OSError):
                                tmp_file.unlink(missing_ok=True)
                            raise
                    restore_count += 1
                    self._tlog(f"[dim]  → Restored: {escape(path)}[/dim]", task_idx)
                except asyncio.CancelledError:
                    raise
                except OSError as e:
                    self._tlog(f"[bold {THEME['error']}]Restore failed for: {escape(path)} ({escape(str(e))})[/]", task_idx, True)
                    all_ok = False
                except Exception as e:
                    self._tlog(f"[bold {THEME['error']}]Exception restoring {escape(path)}: {escape(str(e))}[/]", task_idx, True)
                    all_ok = False

        if restore_count:
            self._tlog(f"[bold {THEME['success']}]Auto-restored {restore_count} file(s)[/]", task_idx, True)
        if merge_count:
            self._tlog(f"[bold {THEME['warning']}]{merge_count} file(s) need manual merge (upstream also changed them)[/]", task_idx, True)
            if merge_dir:
                self._tlog(f"[dim]  Review in: {merge_dir}[/dim]", task_idx)
        if deletion_count:
            self._tlog(f"[bold {THEME['warning']}]{deletion_count} tracked deletion(s) handled[/]", task_idx, True)

        # Retain recovery manifests with actual paths; only mark completed when
        # restoration is fully verified. Staged recovery artifacts are never
        # auto-restored, so a backup containing unrestored staging is never
        # deleted nor marked fully resolved.
        # Policy: staging is preserved, never auto-restored. Byte equality of
        # a staged blob with WORK_TREE does NOT prove the index
        # mode/type/deletion is preserved (index holds mode+oid+stage
        # separately), so the staged manifest/index metadata decides. Any
        # recorded staged entry — blob, mode change, or staged deletion —
        # keeps the backup as pending-staged.
        staged_left: list[str] = []
        try:
            staged_root = backup_dir / ".meta" / "staged"
            staged_set: set[str] = set()
            if staged_root.is_dir():
                for f in sorted(staged_root.rglob("*")):
                    try:
                        if not (f.is_file() and not f.is_symlink()):
                            continue
                        rel = str(f.relative_to(staged_root))
                        staged_set.add(rel)
                    except OSError:
                        continue
            # Manifest records staged blobs AND staged deletions (absent has
            # no blob file). STAGED_MODES records index mode/oid/stage.
            for meta_name in ("MANIFEST.txt", "STAGED_MODES.txt"):
                try:
                    meta_file = backup_dir / ".meta" / meta_name
                    if not meta_file.is_file():
                        continue
                    text = meta_file.read_text(encoding="utf-8", errors="surrogateescape")
                except OSError:
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    p = rec.get("path")
                    if not isinstance(p, str) or not p:
                        continue
                    if meta_name == "MANIFEST.txt":
                        # Staged capture writes {"staged": 1, ...} or
                        # {"staged": "absent", ...}; non-staged lines have
                        # {"status": ..., "has_copy": ...} without "staged".
                        if "staged" in rec:
                            staged_set.add(p)
                    else:
                        # STAGED_MODES.txt lines always describe index state.
                        staged_set.add(p)
            staged_left = sorted(staged_set)
        except OSError:
            pass
        if quarantined:
            all_ok = False
            self._tlog(
                f"[bold {THEME['warning']}]Quarantined {len(quarantined)} path(s) into {backup_dir}/.meta/ (never discarded). Review: {escape(', '.join(quarantined[:10]))}[/]",
                task_idx, True,
            )
        if staged_left:
            all_ok = False
            self._tlog(
                f"[bold {THEME['warning']}]Staged recovery preserved ({len(staged_left)} file(s)) under {backup_dir}/.meta/staged/ — "
                f"staging is NOT auto-restored. Review: {escape(', '.join(staged_left[:10]))}[/]",
                task_idx, True,
            )
        if upstream_overrode_deletions:
            self._tlog(
                f"[bold {THEME['warning']}]Upstream overrode {len(upstream_overrode_deletions)} local deletion(s); deletion intent retained in recovery metadata.[/]",
                task_idx, True,
            )
        try:
            meta_dir = backup_dir / ".meta"
            meta_dir.mkdir(parents=True, exist_ok=True)
            result_text = (
                f"all_ok={all_ok}\nrestored={restore_count}\nmerged={merge_count}\ndeleted={deletion_count}\n"
                f"staged_left={len(staged_left)}\nupstream_overrode_local_deletions={len(upstream_overrode_deletions)}\nbackup={backup_dir}\n"
                + "".join(f"quarantined={q}\n" for q in quarantined)
                + "".join(f"upstream_overrode_deletion={q}\n" for q in upstream_overrode_deletions)
            )
            _atomic_write_text(meta_dir / "RESTORE_RESULT.txt", result_text)
            if all_ok:
                _atomic_write_text(meta_dir / "STATUS", "completed\n")
            else:
                if quarantined:
                    status_val = "pending-quarantine"
                elif staged_left:
                    status_val = "pending-staged"
                elif upstream_overrode_deletions:
                    status_val = "pending-upstream-conflict"
                else:
                    status_val = "pending-restore"
                _atomic_write_text(meta_dir / "STATUS", status_val + "\n")
        except OSError:
            all_ok = False

        # Keep the completed payload until retention pruning. Deleting it here
        # leaves a crash window before execute_phase commits the transaction:
        # recovery then sees reset-applied but cannot find its required backup.

        return all_ok

    async def execute_phase(self) -> bool:
        UPSTREAM_TRACKING_REF = f'refs/dusky-updater/upstream/{self.profile.branch}'

        your_changes_backup: Path | None = None
        local_head = ""
        change_paths: list = []
        change_status: dict = {}
        change_old_mode: dict = {}
        change_old_oid: dict = {}
        meta: dict = {
            "status": "unknown",
            "branch": self.profile.branch,
            "commits": "",
            "commit_list": [],
            "files_changed": 0,
            "diff": "",
            "before_head": "",
            "after_head": "",
            "unrelated_histories": False,
            "collisions": None,
            "collision_backup": "",
            "local_mods": None,
            "local_mods_backup": "",
            "local_mods_restored": None,
        }
        restore_ok: bool | None = None

        try:
            # Candidate-independent rollback is owned by update_dusky_supervisor.py.
            # Never attempt in-process self-heal here: this process cannot
            # recover from a candidate that fails before Python reaches main().

            # Task 0: Bare Repo Validation
            idx = 0
            self.app.update_task_state(idx, "running")  # type: ignore
            self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Bare Repository Validation\n", idx)

            # Recovery is always resolved before inspecting/fetching a new
            # repository state, including interrupted clone/unborn branches.
            await self._recover_pending_sync_artifacts(idx)
            repo_state = await self._get_repo_state(idx)

            if repo_state == 'absent':
                self._tlog(f"[bold {THEME['warning']}]Bare repository missing. Cloning from upstream...[/]", idx, True)
                if not await self._clone_with_retry(idx):
                    raise RuntimeError("Clone sequence failed.")
                await self._ensure_repo_defaults()

                self._tlog("[dim]Checking out files into work-tree...[/dim]", idx)
                rc_head, head_oid, head_err = await self._run_raw('rev-parse', '--verify', 'HEAD^{commit}')
                if rc_head != 0 or not head_oid.strip():
                    raise RuntimeError(f"Cannot resolve cloned HEAD: {head_err}")
                self._sync_old_head = ""
                self._sync_target_oid = head_oid.strip()
                await asyncio.to_thread(self._write_sync_txn, "prepared", branch=self.profile.branch, initialization="clone")
                if not await self._reject_protected_incoming(head_oid.strip(), idx):
                    raise RuntimeError("Cloned tree overlaps protected storage; refusing checkout.")
                if not await self._backup_worktree_collisions('HEAD', honor_tracked=False, task_idx=idx):
                    raise RuntimeError("Collision backup failed during initial checkout.")
                await asyncio.to_thread(self._write_sync_txn, "collisions-durable")
                await asyncio.to_thread(self._write_sync_txn, "applying")

                rc, _, err = await self._run_raw('checkout')
                if rc != 0:
                    self._tlog(f"[bold {THEME['error']}]Checkout failed: {escape(err)}[/]", idx, True)
                    raise RuntimeError("Work-tree checkout failed.")
                await asyncio.to_thread(self._write_sync_txn, "reset-applied", initialization="clone")

                # Validate clone content too; it previously returned before the gate.
                rc_ls, ls_clone, ls_err = await self._run_raw('ls-tree', '-r', '-z', '--name-only', 'HEAD')
                if rc_ls != 0:
                    raise RuntimeError(f"Cannot list cloned tree for gate: {ls_err}")
                clone_files = [f for f in ls_clone.split('\0') if f]
                if clone_files:
                    if not await self._gate_incoming_scripts(clone_files, idx, "", head_oid.strip()):
                        raise RuntimeError("Cloned script gate failed; quarantined invalid scripts.")

                meta.update(status="cloned")
                self.app.git_summary.update(meta)
                self._tlog(f"[bold {THEME['success']}]Repository cloned and checked out successfully.[/]", idx, True)
                self.app.update_task_state(idx, "success")  # type: ignore
                for i in range(1, 5):
                    self.app.update_task_state(i, "skipped")  # type: ignore
                await asyncio.to_thread(self._finish_sync_txn, "completed", initialization="clone")
                return True

            elif repo_state == 'invalid':
                raise RuntimeError("Repository is in an invalid or unsafe state.")

            self._tlog(f"[bold {THEME['success']}]Bare repository integrity verified.[/]", idx)
            self.app.update_task_state(idx, "success")  # type: ignore
            if self.app.run_logger:
                self.app._safe_close_task_log(self.app.tasks[idx], idx, "completed", 0, 0.0)

            # Task 1: Fetch Upstream & Diff
            idx = 1
            self.app.update_task_state(idx, "running")  # type: ignore
            self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Fetch Upstream & Diff\n", idx)

            # Check for in-progress merge/rebase/etc BEFORE touching the index.
            op = self._detect_git_operation_state()
            if op != 'none':
                self._tlog(f"[bold {THEME['error']}]Git {op} is in progress. Resolve it manually first.[/]", idx, True)
                raise RuntimeError(f"Git {op} in progress.")

            # Managed-path index cleanup is deferred until after local/index
            # state has been captured durably; doing it here would destroy
            # staged evidence before backup.

            fetch_source = await self._get_fetch_source()
            self._tlog(f"[dim]Fetching from {escape(fetch_source)}...[/dim]", idx)

            if not await self._fetch_with_retry(fetch_source, UPSTREAM_TRACKING_REF, idx):
                raise RuntimeError("Fetch failed after all retry attempts.")

            rc, raw_local, _ = await self._run_raw('rev-parse', '--verify', '-q', 'HEAD')
            local_head = raw_local.strip() if rc == 0 else ""

            rc, raw_remote, _ = await self._run_raw('rev-parse', '--verify', '-q', UPSTREAM_TRACKING_REF)
            remote_head = raw_remote.strip() if rc == 0 else ""

            if not remote_head:
                self._tlog(f"[bold {THEME['error']}]Cannot determine upstream HEAD for branch {self.profile.branch}.[/]", idx, True)
                raise RuntimeError("No upstream HEAD available.")

            # Snapshot the target commit OID and use it throughout the
            # transaction rather than the mutable tracking ref.
            rc_oid, oid_out, oid_err = await self._run_raw('rev-parse', '--verify', f'{UPSTREAM_TRACKING_REF}^{{commit}}')
            target_oid = oid_out.strip() if rc_oid == 0 else ""
            if not target_oid or len(target_oid) < 40:
                self._tlog(f"[bold {THEME['error']}]Cannot resolve upstream OID: {escape(oid_err)}[/]", idx, True)
                raise RuntimeError("Cannot snapshot upstream OID.")
            rc_type, type_out, _ = await self._run_raw('cat-file', '-t', target_oid)
            if rc_type != 0 or type_out.strip() != 'commit':
                raise RuntimeError(f"Upstream OID is not a commit: {target_oid}")
            remote_head = target_oid
            UPSTREAM_OID = target_oid
            self._sync_old_head = local_head
            self._sync_target_oid = UPSTREAM_OID
            await asyncio.to_thread(self._write_sync_txn, "prepared", branch=self.profile.branch)

            if not local_head:
                self._tlog(f"[bold {THEME['warning']}]Local repository has no commits yet. Initializing from upstream...[/]", idx, True)
                rc1, _, _ = await self._run_raw('symbolic-ref', 'HEAD', f'refs/heads/{self.profile.branch}')
                if rc1 != 0:
                    raise RuntimeError("Failed to point HEAD at branch.")
                if not await self._reject_protected_incoming(UPSTREAM_OID, idx):
                    raise RuntimeError("Incoming tree overlaps protected storage; refusing reset.")
                if not await self._backup_worktree_collisions(UPSTREAM_OID, honor_tracked=False, task_idx=idx):
                    raise RuntimeError("Collision backup failed during unborn init.")
                await asyncio.to_thread(self._write_sync_txn, "collisions-durable", initialization="unborn")
                await asyncio.to_thread(self._write_sync_txn, "applying", initialization="unborn")
                rc2, _, err2 = await self._run_raw('reset', '--hard', UPSTREAM_OID)
                if rc2 != 0:
                    self._tlog(f"[bold {THEME['error']}]Failed to init unborn repo: {escape(err2)}[/]", idx, True)
                    raise RuntimeError("Reset of unborn repo failed.")
                await asyncio.to_thread(self._write_sync_txn, "reset-applied", initialization="unborn")
                await self._ensure_repo_defaults()
                rc_ls2, ls_unborn, ls_err2 = await self._run_raw('ls-tree', '-r', '-z', '--name-only', UPSTREAM_OID)
                if rc_ls2 != 0:
                    raise RuntimeError(f"Cannot list unborn tree for gate: {ls_err2}")
                unborn_files = [f for f in ls_unborn.split('\0') if f]
                if unborn_files:
                    if not await self._gate_incoming_scripts(unborn_files, idx, "", UPSTREAM_OID):
                        raise RuntimeError("Unborn-branch script gate failed; quarantined invalid scripts.")
                self._tlog(f"[bold {THEME['success']}]Repository synchronized (initial bootstrap).[/]", idx, True)
                self.app.update_task_state(idx, "success")  # type: ignore
                for i in range(2, 5):
                    self.app.update_task_state(i, "skipped")  # type: ignore
                await asyncio.to_thread(self._finish_sync_txn, "completed", initialization="unborn")
                return True

            if local_head == remote_head:
                op_eq = self._detect_git_operation_state()
                if op_eq != 'none':
                    raise RuntimeError(f"Git {op_eq} in progress.")
                change_paths, change_status, change_old_mode, change_old_oid = await self._capture_tracked_changes()

                rc_ls_eq, ls_tree_eq, ls_err_eq = await self._run_raw('ls-tree', '-r', '-z', '--name-only', remote_head)
                if rc_ls_eq != 0:
                    raise RuntimeError(f"Cannot list tree for script validation: {ls_err_eq}")
                tracked_scripts_eq = [f for f in ls_tree_eq.split('\0') if f and f.endswith(('.py', '.sh'))]

                if not change_paths:
                    if tracked_scripts_eq:
                        if not await self._gate_incoming_scripts(tracked_scripts_eq, idx, "", remote_head):
                            raise RuntimeError("Script gate failed: invalid syntax in repository scripts.")
                    meta.update(status="up_to_date", before_head=local_head, after_head=remote_head)
                    self.app.git_summary.update(meta)
                    self._tlog(f"[bold {THEME['success']}]Repository synchronization perfect. Origin matched.[/]", idx, True)
                    await self._ensure_repo_defaults()
                    self.app.update_task_state(idx, "success")  # type: ignore
                    if self.app.run_logger:
                        self.app._safe_close_task_log(self.app.tasks[idx], idx, "completed", 0, 0.0)
                    for i in range(2, 5):
                        self.app.update_task_state(i, "skipped")  # type: ignore
                        if self.app.run_logger:
                            self.app._safe_close_task_log(self.app.tasks[i], i, "skipped", 0, 0.0)
                    await asyncio.to_thread(self._finish_sync_txn, "completed", equal_head=True)
                    return True
                meta.update(status="up_to_date_with_mods", before_head=local_head, after_head=remote_head, local_mods=len(change_paths))
                self.app.git_summary.update(meta)
                self._tlog(f"[bold {THEME['accent']}]Origin matched; preserving {len(change_paths)} local/index change(s) in place (no reset).[/]", idx, True)
                if tracked_scripts_eq:
                    await self._gate_incoming_scripts(tracked_scripts_eq, idx, "", remote_head)
                await self._ensure_repo_defaults()
                self.app.update_task_state(idx, "success")
                for i in range(2, 5):
                    self.app.update_task_state(i, "skipped")
                await asyncio.to_thread(self._finish_sync_txn, "completed", equal_head=True, local_changes=len(change_paths))
                return True

            rc, commit_count_raw, _ = await self._run_raw('rev-list', '--count', f'{local_head}..{remote_head}')
            commit_count = commit_count_raw.strip() or "?"
            rc_diff, changed_raw, changed_err = await self._run_raw('diff', '--name-only', '-z', f'{local_head}..{remote_head}')
            if rc_diff != 0:
                raise RuntimeError(f"diff --name-only failed (rc={rc_diff}): {changed_err}")
            # NUL-delimited, byte-preserving via surrogateescape; do not split on newlines.
            changed_files = [f for f in changed_raw.split('\0') if f]
            self._tlog(
                f"\n[bold {THEME['accent']}]Upstream changes:[/]\n"
                f"    Commits behind:  {commit_count}\n"
                f"    Files changed:   {len(changed_files)}",
                idx
            )
            commit_list = []
            rc_log, log_out, _ = await self._run_raw('log', '--oneline', '--no-decorate', '-10', f'{local_head}..{remote_head}')
            if rc_log == 0 and log_out:
                commit_list = [line.strip() for line in log_out.split('\n') if line.strip()]
                self._tlog("    Recent commits:", idx)
                for line in commit_list[:10]:
                    self._tlog(f"      {escape(line)}", idx)

            rc, diff_out, _ = await self._run_raw('diff', '--no-color', '--no-ext-diff', f'{local_head}..{remote_head}')
            if diff_out.strip():
                self._tlog(f"\n[bold {THEME['warning']}]Differential Divergence Detected:[/]\n", idx)
                self.app.log_task(Syntax(diff_out, "diff", theme="monokai", background_color="default", word_wrap=True), idx)  # type: ignore
                self.app.git_diff_text = diff_out  # type: ignore
                meta.update(
                    status="updated",
                    commits=commit_count,
                    commit_list=commit_list,
                    files_changed=len(changed_files),
                    diff=diff_out,
                    before_head=local_head,
                    after_head=remote_head,
                )
            else:
                meta.update(
                    status="updated",
                    commits=commit_count,
                    commit_list=commit_list,
                    files_changed=len(changed_files),
                    before_head=local_head,
                    after_head=remote_head,
                )

            mb_rc, base_commit, _ = await self._run_raw('merge-base', local_head, remote_head)
            base_commit = base_commit.strip()

            if mb_rc == 1 or (mb_rc == 0 and not base_commit):
                self._tlog(f"[bold {THEME['warning']}]Local repository does not share history with upstream (unrelated histories).[/]", idx, True)
                if not OPT_ALLOW_DIVERGED_RESET:
                    self._tlog(f"[bold {THEME['error']}]Aborting: non-interactive mode and unrelated history. Use --allow-diverged-reset to override.[/]", idx, True)
                    raise RuntimeError("Unrelated upstream history. Aborting.")
                if not await self._backup_git_history(idx):
                    raise RuntimeError("Git history backup failed.")

                self.app.update_task_state(idx, "success")  # type: ignore

                # Task 2: Forensic Collision Backup
                idx = 2
                self.app.update_task_state(idx, "running")  # type: ignore
                self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Forensic Collision Backup\n", idx)

                if not await self._reject_protected_incoming(UPSTREAM_OID, idx):
                    raise RuntimeError("Incoming tree overlaps protected storage; refusing reset.")
                if not await self._backup_worktree_collisions(UPSTREAM_OID, honor_tracked=True, task_idx=idx):
                    raise RuntimeError("Collision backup failed.")
                meta.update(
                    collisions=self._last_collision_count,
                    collision_backup=self._last_collision_dir,
                )
                await asyncio.to_thread(self._write_sync_txn, "collisions-durable")
                self.app.update_task_state(idx, "success")  # type: ignore

                # Task 3: Snapshot
                idx = 3
                self.app.update_task_state(idx, "running")  # type: ignore
                self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Snapshot\n", idx)

                full_snapshot_dir = await self._backup_full_tracked_tree(idx)
                if not full_snapshot_dir:
                    raise RuntimeError("Full tracked-tree backup failed.")

                op_u = self._detect_git_operation_state()
                if op_u != 'none':
                    raise RuntimeError(f"Git {op_u} in progress.")
                change_paths, change_status, change_old_mode, change_old_oid = await self._capture_tracked_changes()
                if change_paths:
                    captured_signature = self._capture_signature
                    your_changes_backup = await self._backup_user_modifications(change_paths, change_status, idx, change_old_mode, change_old_oid)
                    if your_changes_backup is None:
                        raise RuntimeError("User modifications backup failed.")
                else:
                    captured_signature = self._capture_signature
                    self._tlog(f"[bold {THEME['success']}]No local tracked modifications found. Snapshot skipped.[/]", idx)
                meta.update(
                    full_tracked_backup=str(full_snapshot_dir),
                    local_mods=len(change_paths),
                    local_mods_backup=str(your_changes_backup) if your_changes_backup else "",
                )
                await asyncio.to_thread(
                    self._write_sync_txn, "snapshot-durable",
                    local_backup=str(your_changes_backup) if your_changes_backup else "",
                    full_tracked_backup=str(full_snapshot_dir),
                    captured_paths=change_paths, captured_status=change_status,
                    captured_old_mode=change_old_mode, captured_old_oid=change_old_oid,
                    captured_staged_paths=sorted(getattr(self, "_actual_staged_paths", set())),
                )

                # Revalidate immediately before destructive application. If
                # an editor or another Git process changed captured inputs,
                # stop rather than overwriting state that was never backed up.
                fresh_paths, fresh_status, fresh_modes, fresh_oids = await self._capture_tracked_changes()
                if self._capture_signature != captured_signature or (fresh_paths, fresh_status, fresh_modes, fresh_oids) != (change_paths, change_status, change_old_mode, change_old_oid):
                    raise RuntimeError("Local/index state changed after snapshot; refusing reset. Re-run to recapture.")
                # Now that staged/index state is durable, internal updater
                # storage may be removed from the index before reset.
                if not await self._unstage_managed_paths():
                    raise RuntimeError("Failed to unstage managed paths after capture.")
                self.app.update_task_state(idx, "success")  # type: ignore

                # Task 4: Apply Bare Updates (Reset)
                idx = 4
                self.app.update_task_state(idx, "running")  # type: ignore
                self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Apply Bare Updates (Reset)\n", idx)

                await asyncio.to_thread(self._write_sync_txn, "applying")
                rc_reset, _, err_reset = await self._run_raw('reset', '--hard', UPSTREAM_OID)
                if rc_reset != 0:
                    self._tlog(f"[bold {THEME['error']}]Reset failed: {escape(err_reset)}[/]", idx, True)
                    raise RuntimeError(f"Reset failed (rc={rc_reset}).")
                await asyncio.to_thread(self._write_sync_txn, "reset-applied")

                rc_ls_unr, ls_unr, _ = await self._run_raw('ls-tree', '-r', '-z', '--name-only', UPSTREAM_OID)
                tracked_scripts_unr = [f for f in ls_unr.split('\0') if f and f.endswith(('.py', '.sh'))]
                gate_targets = sorted(set(changed_files) | set(tracked_scripts_unr))

                gate_ok = await self._gate_incoming_scripts(gate_targets, idx, "", UPSTREAM_OID)
                if not gate_ok:
                    raise RuntimeError("Incoming script gate failed; quarantined invalid scripts.")

                if your_changes_backup and change_paths:
                    self._tlog(f"[bold {THEME['accent']}]Restoring your tracked modifications...[/]", idx)
                    restore_ok = await self._restore_user_modifications(
                        your_changes_backup, change_paths, change_status, change_old_mode, change_old_oid, idx
                    )
                    if not restore_ok:
                        self._tlog(f"[bold {THEME['warning']}]Some files could not be restored. Backup preserved at: {your_changes_backup}[/]", idx, True)
                    gate_restored = await self._gate_incoming_scripts(change_paths, idx, "", UPSTREAM_OID)
                    if not gate_restored:
                        raise RuntimeError("Restored modifications script gate failed; invalid syntax in local changes.")

                await self._ensure_repo_defaults()
                meta.update(
                    status="unrelated_reset",
                    unrelated_histories=True,
                    after_head=remote_head,
                    local_mods_restored=restore_ok,
                )
                self.app.git_summary.update(meta)
                self.app.update_task_state(idx, "success")  # type: ignore
                await asyncio.to_thread(self._finish_sync_txn, "completed", restore_ok=restore_ok, unrelated=True)
                return True

            elif mb_rc != 0:
                raise RuntimeError(f"merge-base failed (rc={mb_rc}).")

            if base_commit == local_head:
                self._tlog(f"[bold {THEME['accent']}]Fast-forward sync detected.[/]", idx)
            else:
                self._tlog(f"[bold {THEME['warning']}]Local history diverged from upstream.[/]", idx, True)
                if not OPT_ALLOW_DIVERGED_RESET:
                    self._tlog(f"[bold {THEME['error']}]Aborting: non-interactive mode and diverged history. Use --allow-diverged-reset to override.[/]", idx, True)
                    raise RuntimeError("Diverged history detected. Aborting.")
                if not await self._backup_git_history(idx):
                    raise RuntimeError("Git history backup failed.")

            self.app.update_task_state(idx, "success")  # type: ignore
            if self.app.run_logger:
                self.app._safe_close_task_log(self.app.tasks[idx], idx, "completed", 0, 0.0)

            # Task 2: Forensic Collision Backup
            idx = 2
            self.app.update_task_state(idx, "running")  # type: ignore
            self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Forensic Collision Backup\n", idx)

            if not await self._reject_protected_incoming(UPSTREAM_OID, idx):
                raise RuntimeError("Incoming tree overlaps protected storage; refusing reset.")
            if not await self._backup_worktree_collisions(UPSTREAM_OID, honor_tracked=True, task_idx=idx):
                raise RuntimeError("Collision backup failed.")
            meta.update(
                collisions=self._last_collision_count,
                collision_backup=self._last_collision_dir,
            )
            await asyncio.to_thread(self._write_sync_txn, "collisions-durable")
            self.app.update_task_state(idx, "success")  # type: ignore
            if self.app.run_logger:
                self.app._safe_close_task_log(self.app.tasks[idx], idx, "completed", 0, 0.0)

            # Task 3: Snapshot
            idx = 3
            self.app.update_task_state(idx, "running")  # type: ignore
            self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Snapshot\n", idx)

            op3 = self._detect_git_operation_state()
            if op3 != 'none':
                raise RuntimeError(f"Git {op3} in progress.")
            change_paths, change_status, change_old_mode, change_old_oid = await self._capture_tracked_changes()
            captured_signature = self._capture_signature
            if change_paths:
                your_changes_backup = await self._backup_user_modifications(change_paths, change_status, idx, change_old_mode, change_old_oid)
                if your_changes_backup is None:
                    raise RuntimeError("User modifications backup failed.")
            else:
                self._tlog(f"[bold {THEME['success']}]No local tracked modifications found. Snapshot skipped.[/]", idx)
            meta.update(
                local_mods=len(change_paths),
                local_mods_backup=str(your_changes_backup) if your_changes_backup else "",
            )
            await asyncio.to_thread(
                self._write_sync_txn, "snapshot-durable",
                local_backup=str(your_changes_backup) if your_changes_backup else "",
                captured_paths=change_paths, captured_status=change_status,
                captured_old_mode=change_old_mode, captured_old_oid=change_old_oid,
                captured_staged_paths=sorted(getattr(self, "_actual_staged_paths", set())),
            )

            fresh_paths, fresh_status, fresh_modes, fresh_oids = await self._capture_tracked_changes()
            if self._capture_signature != captured_signature or (fresh_paths, fresh_status, fresh_modes, fresh_oids) != (change_paths, change_status, change_old_mode, change_old_oid):
                raise RuntimeError("Local/index state changed after snapshot; refusing reset. Re-run to recapture.")
            if not await self._unstage_managed_paths():
                raise RuntimeError("Failed to unstage managed paths after capture.")
            self.app.update_task_state(idx, "success")  # type: ignore
            if self.app.run_logger:
                self.app._safe_close_task_log(self.app.tasks[idx], idx, "completed", 0, 0.0)

            # Task 4: Apply Reset
            idx = 4
            self.app.update_task_state(idx, "running")  # type: ignore
            self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Apply Bare Updates (Reset)\n", idx)

            await asyncio.to_thread(self._write_sync_txn, "applying")
            rc_reset, _, err_reset = await self._run_raw('reset', '--hard', UPSTREAM_OID)
            if rc_reset != 0:
                self._tlog(f"[bold {THEME['error']}]Reset failed: {escape(err_reset)}[/]", idx, True)
                raise RuntimeError(f"Reset failed (rc={rc_reset}).")

            await asyncio.to_thread(self._write_sync_txn, "reset-applied")
            self._tlog(f"[bold {THEME['success']}]Bare Repository reset applied and synchronized.[/]", idx, True)

            rc_ls_t4, ls_t4, _ = await self._run_raw('ls-tree', '-r', '-z', '--name-only', UPSTREAM_OID)
            tracked_scripts_t4 = [f for f in ls_t4.split('\0') if f and f.endswith(('.py', '.sh'))]
            gate_targets = sorted(set(changed_files) | set(tracked_scripts_t4))

            gate_ok2 = await self._gate_incoming_scripts(gate_targets, idx, local_head if local_head != UPSTREAM_OID else "", UPSTREAM_OID)
            if not gate_ok2:
                raise RuntimeError("Incoming script gate failed; quarantined invalid scripts.")

            if your_changes_backup and change_paths:
                self._tlog(f"[bold {THEME['accent']}]Restoring your tracked modifications...[/]", idx)
                restore_ok = await self._restore_user_modifications(
                    your_changes_backup, change_paths, change_status, change_old_mode, change_old_oid, idx
                )
                if not restore_ok:
                    self._tlog(f"[bold {THEME['warning']}]Some files could not be restored. Backup preserved at: {your_changes_backup}[/]", idx, True)
                gate_restored = await self._gate_incoming_scripts(change_paths, idx, "", UPSTREAM_OID)
                if not gate_restored:
                    raise RuntimeError("Restored modifications script gate failed; invalid syntax in local changes.")

            await self._ensure_repo_defaults()
            final_st = meta.get("status")
            if final_st not in ("up_to_date_with_mods", "unrelated_reset"):
                final_st = "updated" if (local_head and remote_head and local_head != remote_head) else "up_to_date_with_mods"

            meta.update(
                status=final_st,
                after_head=remote_head,
                local_mods_restored=restore_ok,
            )
            self.app.git_summary.update(meta)
            self.app.update_task_state(idx, "success")  # type: ignore
            if self.app.run_logger:
                self.app._safe_close_task_log(self.app.tasks[idx], idx, "completed", 0, 0.0)
            await asyncio.to_thread(self._finish_sync_txn, "completed", restore_ok=restore_ok)
            return True

        except asyncio.CancelledError:
            with suppress(Exception):
                await self._recover_pending_sync_artifacts(0)
            raise
        except Exception as e:
            err_msg = f"[bold {THEME['error']}][FATAL][/] Git Sync Failure: {escape(str(e))}"
            self.log(err_msg)
            if hasattr(self.app, "add_warning"):
                self.app.add_warning("sync", str(e))
            with suppress(Exception):
                await self._recover_pending_sync_artifacts(0)
            for i in range(5):
                st = self.app.tasks[i].status  # type: ignore
                if st == "running":
                    self.app.update_task_state(i, "failed")  # type: ignore
                elif st == "pending":
                    self.app.update_task_state(i, "skipped")  # type: ignore
            return False


if _HAS_UI:
    # UI component definitions load only when rich/textual are present.
    # Dependency-free CLI (help/version/doctor/list) never touches this block.
    # ==============================================================================
    #  TEXTUAL UI COMPONENTS
    # ==============================================================================
    class MainLogItem(ListItem):
        def compose(self) -> ComposeResult:
            yield Label(f" [bold {THEME['accent']}]CORE[/] Dusky Execution Engine", classes="list-item-label")


    class ReportLogItem(ListItem):
        def compose(self) -> ComposeResult:
            yield Label(f" [bold {THEME['success']}]◆ REPORT[/] Final Run Overview", classes="list-item-label")


    class TaskItem(ListItem):
        status = reactive("pending")

        def __init__(self, task: DuskyTask, index: int):
            super().__init__()
            self.dusky_task = task
            self.task_index = index

        def compose(self) -> ComposeResult:
            yield Label(id=f"lbl-{self.task_index}")

        def on_mount(self) -> None:
            self._update_label()

        def watch_status(self, old_status: str, new_status: str) -> None:
            self._update_label()

        def _update_label(self) -> None:
            if not self.is_mounted:
                self.call_after_refresh(self._update_label)
                return

            if self.dusky_task.mode == 'GIT':
                mode_text = "GIT"
            elif self.dusky_task.mode == 'S':
                mode_text = "SUDO"
            else:
                mode_text = "USER"

            cmd_str = f"{self.dusky_task.name} {' '.join(self.dusky_task.args)}".strip()
            cmd_str = escape(cmd_str)

            suffix = ""
            if getattr(self.dusky_task, "duration", 0) > 0:
                secs = self.dusky_task.duration
                if secs < 60:
                    suffix = f" [dim {THEME['warning']}]({secs:.1f}s)[/]"
                else:
                    m = int(secs) // 60
                    s = int(secs) % 60
                    suffix = f" [dim {THEME['warning']}]({m}m{s:02d}s)[/]"

            symbols = GLOBAL_CONFIG.get("ui", {}).get(
                "ascii_symbols" if ASCII_MODE else "unicode_symbols",
                {"pending": "○", "running": "◉", "success": "✓", "failed": "✗", "skipped": "-"}
            )

            icon_map = {
                'pending': f"[dim {THEME['muted']}]{symbols.get('pending', '○')}[/]",
                'running': f"[bold {THEME['accent']} blink]{symbols.get('running', '◉')}[/]",
                'success': f"[bold {THEME['success']}]{symbols.get('completed', '✓')}[/]",
                'failed':  f"[bold {THEME['error']}]{symbols.get('failed', '✗')}[/]",
                'skipped': f"[dim {THEME['warning']}]{symbols.get('skipped', '-')}[/]"
            }
            icon = icon_map.get(self.status, "?")

            color_map = {
                'running': f"bold {THEME['fg']}", 'pending': f"dim {THEME['muted']}",
                'success': f"bold {THEME['success']}", 'failed': f"bold {THEME['error']}",
                'skipped': f"dim {THEME['warning']}"
            }
            color = color_map.get(self.status, "white")

            with suppress(Exception):
                self.query_one(Label).update(f" {icon}  [{color}]{cmd_str}[/]{suffix}  [{color}]{mode_text}[/]")


    class TaskSearchScreen(ModalScreen[int | None]):
        BINDINGS = [
            Binding("escape", "dismiss_modal", "Dismiss", priority=True),
            Binding("ctrl+n", "cursor_down", "Down", priority=True),
            Binding("ctrl+p", "cursor_up", "Up", priority=True),
        ]

        def on_key(self, event: events.Key) -> None:
            if event.key.lower() == "escape":
                self.dismiss(None)
                event.stop()

        @on(events.Click)
        def on_background_click(self, event: events.Click) -> None:
            if event.control is self:
                self.dismiss(None)

        def __init__(self, tasks: list[DuskyTask]):
            super().__init__()
            self.tasks = tasks
            self.results: list[int] = []

        def compose(self) -> ComposeResult:
            with Container(id="search_dialog"):
                yield Static(f"{S('logo')} Fuzzy Task Search", id="search_title")
                yield Input(placeholder="Search tasks...", id="search_input")
                yield OptionList(id="search_list")

        def on_mount(self) -> None:
            self.query_one("#search_input", Input).focus()
            self._update_results("")

        def on_input_changed(self, event: Input.Changed) -> None:
            self._update_results(event.value)

        def _update_results(self, query: str) -> None:
            ol = self.query_one(OptionList)
            ol.clear_options()
            self.results.clear()

            query_lower = query.lower().strip()
            query_no_space = query_lower.replace(" ", "")
            try:
                limit = int(GLOBAL_CONFIG.get("ui", {}).get("search_result_limit", 200))
            except (TypeError, ValueError):
                limit = 200
            limit = max(10, min(1000, limit))

            if not query_lower:
                scored = [(0, i, t) for i, t in enumerate(self.tasks[:limit])]
            else:
                scored_results: list[tuple[int, int, DuskyTask]] = []
                for idx, item in enumerate(self.tasks):
                    target = item.name.lower()
                    args_text = " ".join(item.args).lower()
                    haystack = f"{target} {args_text}"
                    score = 0

                    if query_lower == target:
                        score += 100
                    elif target.startswith(query_lower):
                        score += 50
                    elif query_lower in target:
                        score += 30
                    elif query_lower in haystack:
                        score += 18

                    if query_no_space and query_no_space in target.replace(" ", "").replace("-", "").replace("_", ""):
                        score += 20

                    s_idx = q_idx = 0
                    match_positions: list[int] = []
                    while s_idx < len(target) and q_idx < len(query_no_space):
                        if target[s_idx] == query_no_space[q_idx]:
                            match_positions.append(s_idx)
                            q_idx += 1
                        s_idx += 1

                    if q_idx == len(query_no_space) and query_no_space:
                        if len(match_positions) > 1:
                            spread = (match_positions[-1] - match_positions[0]) - (len(match_positions) - 1)
                            score += max(0, 15 - spread)
                        else:
                            score += 15
                        score += 5

                    if score > 0:
                        scored_results.append((score, idx, item))

                scored_results.sort(key=lambda x: (-x[0], x[1]))
                scored = scored_results

            options: list[Option] = []
            for _, idx, item in scored[:limit]:
                txt = Text()
                txt.append(f"{idx:03d} ")
                if item.mode == 'GIT':
                    txt.append(" [GIT] ", style="bold cyan")
                elif item.mode == 'S':
                    txt.append(" [SUDO] ", style="bold red")
                else:
                    txt.append(" [USER] ", style="bold green")

                txt.append(item.name, style="bold white")
                if item.args:
                    txt.append(" " + shlex.join(item.args), style="dim")
                options.append(Option(txt, id=str(idx)))
                self.results.append(idx)

            ol.add_options(options)

        @on(OptionList.OptionSelected)
        def on_selected(self, event: OptionList.OptionSelected) -> None:
            if event.option and event.option.id is not None:
                self.dismiss(int(event.option.id))
            elif event.option_index is not None and event.option_index < len(self.results):
                self.dismiss(self.results[event.option_index])

        @on(Input.Submitted)
        def on_input_submitted(self, event: Input.Submitted) -> None:
            event.stop()
            ol = self.query_one(OptionList)
            if ol.highlighted is not None and ol.highlighted < len(self.results):
                self.dismiss(self.results[ol.highlighted])
            elif self.results:
                self.dismiss(self.results[0])

        def action_cursor_down(self) -> None:
            self.query_one(OptionList).action_cursor_down()

        def action_cursor_up(self) -> None:
            self.query_one(OptionList).action_cursor_up()

        def action_dismiss_modal(self) -> None:
            self.dismiss(None)


    class LogSearchScreen(ModalScreen[None]):
        BINDINGS = [
            Binding("escape", "dismiss_modal", "Dismiss", priority=True),
            Binding("ctrl+n", "cursor_down", "Down", priority=True),
            Binding("ctrl+p", "cursor_up", "Up", priority=True),
        ]

        def on_key(self, event: events.Key) -> None:
            if event.key.lower() == "escape":
                self.dismiss(None)
                event.stop()

        def action_cursor_down(self) -> None:
            self.query_one("#log_search_list", OptionList).action_cursor_down()

        def action_cursor_up(self) -> None:
            self.query_one("#log_search_list", OptionList).action_cursor_up()

        @on(events.Click)
        def on_background_click(self, event: events.Click) -> None:
            if event.control is self:
                self.dismiss(None)

        def __init__(self, title: str, lines: list[str]):
            super().__init__()
            self.title = title
            self.lines = lines

        def compose(self) -> ComposeResult:
            with Container(id="log_search_dialog"):
                yield Static(f"{S('logo')} Log Search: {self.title}", id="log_search_title")
                yield Input(placeholder="Search log...", id="log_search_input")
                yield OptionList(id="log_search_list")

        def on_mount(self) -> None:
            self.query_one("#log_search_input", Input).focus()
            self._update("")

        def on_input_changed(self, event: Input.Changed) -> None:
            self._update(event.value)

        def _update(self, query: str) -> None:
            ol = self.query_one("#log_search_list", OptionList)
            ol.clear_options()

            q = query.strip().lower()
            if not q:
                return

            # Stop scanning after the result cap instead of allocating every
            # matching Option first.
            cap = 300
            count = 0
            for i, line in enumerate(self.lines):
                if count >= cap:
                    break
                clean = ANSI_STRIP_REGEX.sub("", line)
                if q in clean.lower():
                    txt = Text()
                    txt.append(f"{i + 1:5d}  ", style="dim")
                    txt.append(clean.strip())
                    ol.add_options([Option(txt)])
                    count += 1

        def action_dismiss_modal(self) -> None:
            self.dismiss(None)


    class ConfirmQuitScreen(ModalScreen[str]):
        BINDINGS = [
            Binding("escape", "cancel", "Cancel", priority=True),
            Binding("y,a,q,enter", "confirm_abort", "Abort", priority=True),
            Binding("n,c", "cancel", "Cancel", priority=True),
        ]

        def compose(self) -> ComposeResult:
            with Container(id="confirm_dialog"):
                yield Static(f"{S('failed')}  ABORT DUSKY UPDATER?", id="confirm_title")
                yield Static("Are you sure you want to terminate the active update process?", id="confirm_text")
                with Horizontal(classes="modal-btn-container"):
                    yield Label(" Cancel [N] ", classes="modal-cancel-btn", id="btn_cancel")
                    yield Label(" Abort [Y] ", classes="modal-close-btn", id="btn_abort")

        @on(events.Click, "#btn_abort")
        def on_abort_click(self) -> None:
            self.dismiss("abort")

        @on(events.Click, "#btn_cancel")
        def on_cancel_click(self) -> None:
            self.dismiss("cancel")

        def on_key(self, event: events.Key) -> None:
            key = event.key.lower()
            if key in ("a", "y", "enter", "space", "q"):
                self.dismiss("abort")
            elif key in ("c", "n", "escape"):
                self.dismiss("cancel")

        def action_confirm_abort(self) -> None:
            self.dismiss("abort")

        def action_cancel(self) -> None:
            self.dismiss("cancel")


    class HelpScreen(ModalScreen[None]):
        BINDINGS = [
            Binding("escape", "dismiss", "Dismiss", priority=True),
            Binding("f1", "dismiss", "Dismiss", priority=True),
            Binding("question_mark", "dismiss", "Dismiss", priority=True),
            Binding("q", "dismiss", "Dismiss", priority=True),
        ]

        def compose(self) -> ComposeResult:
            with Container(id="help_dialog"):
                yield Static(f"{S('logo')} Dusky Updater Keybindings & Help", id="modal-title")

                text = Text()
                text.append("Global Navigation & Shortcuts\n", style=f"bold {THEME['accent']}")
                text.append("  F1 / ?         Open / close (toggle) this Help screen\n")
                text.append("  Ctrl+F         Fuzzy search tasks\n")
                text.append("  Ctrl+L         Search current execution log\n")
                text.append("  F              Cycle filter (all/pending/running/success/failed/skipped)\n")
                text.append("  Ctrl+G         Toggle follow-running-task mode (manual navigation turns it off)\n")
                text.append("  Ctrl+O         Toggle child-input mode during PTY work (all keys to child; Ctrl+O exits)\n")
                text.append("  q / Ctrl+Q / Ctrl+Z   Quit / Abort confirmation dialog\n")
                text.append("  Ctrl+C         Reaches the running child first during PTY work (use Q to quit)\n\n")

                text.append("Pane Resizing & Layout\n", style=f"bold {THEME['accent']}")
                text.append("  Alt+Right / Alt+L / ]  Expand sidebar width\n")
                text.append("  Alt+Left / Alt+H / [   Shrink sidebar width\n")
                text.append("  Mouse Drag     Click and drag split border left or right\n\n")

                text.append("List & Log Navigation\n", style=f"bold {THEME['accent']}")
                text.append("  j / k                  Navigate scripts in left sidebar\n")
                text.append("  Up / Down              Scroll active log line-by-line in right pane\n")
                text.append("  PageUp / PageDown      Scroll active log page-by-page in right pane\n")
                text.append("  Home / End             Scroll active log to top / bottom in right pane\n")
                text.append("  Tab / Shift+Tab        Toggle focus between sidebar and log pane\n")
                text.append("  Enter                  Select task and view task log\n")
                text.append("  y / a                  Confirm / Abort in modal dialogs\n")
                text.append("  n / c / Esc            Cancel in modal dialogs\n")

                yield Static(text)

                with Horizontal(classes="modal-btn-container"):
                    yield Label(" Close [F1/?] ", classes="modal-close-btn", id="btn_close")

        def on_key(self, event: events.Key) -> None:
            key = event.key.lower()
            if key in ("escape", "f1", "question_mark", "q", "enter", "space", "?") or event.character in ("?", "q"):
                self.dismiss(None)
                event.stop()

        @on(events.Click, "#btn_close")
        def on_close_click(self) -> None:
            self.dismiss(None)

        @on(events.Click)
        def on_background_click(self, event: events.Click) -> None:
            if event.control is self:
                self.dismiss(None)

        def action_dismiss(self) -> None:
            self.dismiss(None)


    class CompletionDialog(ModalScreen[bool]):
        """Final dialog shown when the pipeline finishes: review logs or quit."""

        BINDINGS = [
            Binding("escape", "dismiss_stay", "View Logs", priority=True),
            Binding("enter,space", "dismiss_stay", "View Logs", priority=True),
            Binding("v", "dismiss_stay", "View Logs", priority=True),
            Binding("q", "dismiss_quit", "Quit", priority=True),
        ]

        def __init__(self, title: str = "dusky updated", message: str = "", level: str = "success") -> None:
            super().__init__()
            self.title_text = title
            self.message = message
            self.level = level

        def compose(self) -> ComposeResult:
            with Vertical(id="completion-dialog", classes=f"-{self.level}"):
                yield Label(self.title_text, id="modal-title")
                yield Static(self.message, id="completion-message", markup=False)
                with Horizontal(classes="modal-btn-container"):
                    yield Label(" View Logs ", classes="modal-close-btn", id="btn-view")
                    yield Label(" Quit ", classes="modal-cancel-btn", id="btn-quit")

        def on_key(self, event: events.Key) -> None:
            key = event.key.lower()
            if key in ("escape", "enter", "space", "v"):
                self.dismiss(False)
                event.stop()
            elif key == "q":
                self.dismiss(True)
                event.stop()

        def action_dismiss_stay(self) -> None:
            self.dismiss(False)

        def action_dismiss_quit(self) -> None:
            self.dismiss(True)

        @on(events.Click, "#btn-view")
        def on_view_click(self) -> None:
            self.dismiss(False)

        @on(events.Click, "#btn-quit")
        def on_quit_click(self) -> None:
            self.dismiss(True)

        @on(events.Click)
        def on_background_click(self, event: events.Click) -> None:
            if event.control is self:
                self.dismiss(False)


    # ==============================================================================
    #  MAIN APPLICATION ENGINE
    # ==============================================================================
    # ==============================================================================
    #  MAIN APPLICATION ENGINE
    # ==============================================================================
    class FocusableRichLog(RichLog):
        can_focus = True


    class DuskyApp(App):
        CSS = DUSKY_CSS
        # Reserved emergency shortcuts: ONLY Ctrl+O (leave child-input mode)
        # and Ctrl+Q (emergency abort) are priority bindings that run before
        # on_key. All other keys are non-priority so child-input mode can
        # intercept them in on_key and forward bytes to the child (Escape,
        # Ctrl+F/L, function keys, Tab, navigation, printable). This fixes the
        # previous bug where priority Escape opened ConfirmQuitScreen instead
        # of writing \x1b to the child.
        BINDINGS = [
            Binding("ctrl+q", "request_quit", "Quit", priority=True),
            Binding("ctrl+o", "toggle_child_input", "Child Input", priority=True),
            Binding("ctrl+f", "open_search", "Search Tasks"),
            Binding("ctrl+l", "search_log", "Search Log"),
            Binding("q", "request_quit", "Quit"),
            Binding("escape", "request_quit", "Quit"),
            Binding("ctrl+z", "request_quit", "Quit"),
            Binding("ctrl+g", "toggle_follow", "Follow"),
            Binding("f1", "help", "Help"),
            Binding("question_mark", "help", "Help"),
            Binding("f", "cycle_filter", "Filter"),
            Binding("alt+left", "shrink_left_pane", "Shrink Sidebar"),
            Binding("alt+right", "expand_left_pane", "Expand Sidebar"),
            Binding("alt+h", "shrink_left_pane", "Shrink Sidebar"),
            Binding("alt+l", "expand_left_pane", "Expand Sidebar"),
            Binding("ctrl+left", "shrink_left_pane", "Shrink Sidebar"),
            Binding("ctrl+right", "expand_left_pane", "Expand Sidebar"),
            Binding("bracketleft", "shrink_left_pane", "Shrink Sidebar"),
            Binding("bracketright", "expand_left_pane", "Expand Sidebar"),
            Binding("j", "tree_down", "Tree Down"),
            Binding("k", "tree_up", "Tree Up"),
            Binding("up", "scroll_preview_up", "Scroll Log Up"),
            Binding("down", "scroll_preview_down", "Scroll Log Down"),
            Binding("pageup", "scroll_preview_page_up", "Page Up"),
            Binding("pagedown", "scroll_preview_page_down", "Page Down"),
            Binding("home", "scroll_preview_home", "Home"),
            Binding("end", "scroll_preview_end", "End"),
            Binding("tab", "toggle_focus", "Switch Focus"),
            Binding("shift+tab", "toggle_focus", "Switch Focus"),
        ]

        def __init__(self, profile: ProfileConfig, tasks: list[DuskyTask], has_sudo: bool):
            super().__init__()
            self.profile = profile
            self.tasks = tasks
            self.has_sudo = has_sudo
            self.abort_flag = False
            self.git_diff_text = ""
            self.current_pty_master: int | None = None
            self.active_child_pid: int | None = None
            self.active_child_group: bool = False
            # Shared-group interactive child (same session/process group as
            # the updater by design): cancellation must signal it by PID,
            # never via killpg(), which would hit our own group.
            self._interactive_proc: subprocess.Popen | None = None
            self._interactive_cancel: threading.Event | None = None
            self._prompt_buffer: str = ""
            self._prompt_counts: dict[str, int] = {}
            self._prompt_last: dict[str, float] = {}
            self._prompt_retry_task: asyncio.Task | None = None
            self._prompt_wait_task: asyncio.Task | None = None
            # Buffered async PTY input: single ordered byte-bounded buffer +
            # one writer task preserves order/partial writes without
            # busy-polling or per-keystroke tasks. Explicit bound + overload
            # policy (drop incoming with warning, never reorder).
            self._pty_write_queue: asyncio.Queue[bytes] | None = None  # legacy, unused; kept for compat
            self._pty_writer_task: asyncio.Task | None = None
            self._pty_write_fd: int | None = None
            self._pty_write_pending: deque[bytes] = deque()
            self._pty_write_bytes: int = 0
            self._pty_write_max_bytes: int = 65536
            self._pty_write_event: asyncio.Event | None = None
            self._pty_write_dropped: int = 0
            self.state_store: StateStore | None = None
            self.sleep_inhibitor: SleepInhibitor | None = None
            self.progress = None
            self.heartbeat_task: asyncio.Task | None = None
            self.condition_evaluator = ConditionEvaluator()
            self.run_id: str = RUN_TIMESTAMP
            self.run_logger: RunLogger | None = None
            self.once_store: OnceStore | None = None
            self.missing_scripts: list[str] = []
            self.run_warnings: list[dict[str, str]] = []
            self.blocked_scripts: dict[str, str] = {}
            self.script_gate_outcomes: dict[str, dict[str, str]] = {}
            for _w in CONFIG_WARNINGS:
                self.run_warnings.append({"kind": "configuration", "message": _w, "task": "", "path": "", "recovery": ""})
            for _w in self.profile.diagnostics:
                self.run_warnings.append({"kind": "profile", "message": _w, "task": "", "path": str(self.profile.filepath), "recovery": ""})
            try:
                self.sidebar_width: int = max(15, min(80, int(GLOBAL_CONFIG.get("ui", {}).get("sidebar_width", 35))))
            except (TypeError, ValueError):
                self.sidebar_width = 35
            self.filter_mode: str = "all"
            self._log_lines: dict[int | str, deque[str]] = {}
            self._is_dragging_pane: bool = False
            # Explicit follow/child-input modes with visible state. Follow key:
            # manual ListView navigation disables auto-follow; ctrl+g resumes.
            # Child input: ctrl+o toggles; ONLY Ctrl+O/Ctrl+Q are reserved
            # (Escape goes to the child as \x1b).
            self.follow_mode: bool = True
            self.child_input_mode: bool = False
            self._finalized: set[int] = set()
            self._task_widgets: list = []
            self.exit_code: int = 0
            self.pipeline_finished: bool = False
            self._restart_handoff: Path | None = None
            self._previous_signal_handlers: dict[int, Any] = {}
            self.run_start_mono: float = time.monotonic()
            self.phase_durations: dict[str, float] = {
                "phase1_git": 0.0,
                "phase1_5_resolve": 0.0,
                "phase2_exec": 0.0,
            }
            self.git_summary: dict[str, Any] = {
                "branch": self.profile.branch,
                "before_head": "",
                "after_head": "",
                "commits": "0",
                "commit_list": [],
                "files_changed": 0,
                "collisions": 0,
                "collision_backup": "",
                "local_mods": 0,
                "local_mods_backup": "",
                "local_mods_restored": None,
                "unrelated_histories": False,
                "status": "skipped" if OPT_SKIP_SYNC else "unknown",
            }

        def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
            # Dynamically disable unrelated bindings in child-input mode.
            # Reserved: toggle_child_input (Ctrl+O) and request_quit via Ctrl+Q
            # emergency. Escape/Ctrl+F/L/etc. are non-priority and are stopped
            # in on_key (forwarded to the child), so they never reach actions.
            # Modal screens and Input focus are preserved.
            try:
                from textual.screen import ModalScreen as _Modal
                if isinstance(getattr(self, "screen", None), _Modal):
                    return True
            except Exception:
                pass
            try:
                from textual.widgets import Input as _Input
                if isinstance(getattr(self, "focused", None), _Input):
                    return True
            except Exception:
                pass
            if getattr(self, "child_input_mode", False) and getattr(self, "current_pty_master", None) is not None:
                if action in ("toggle_child_input", "request_quit"):
                    # Ctrl+O exits mode, Ctrl+Q emergency aborts (handled in
                    # on_key, but also allow binding path for Ctrl+Q).
                    return True
                # All other app bindings disabled in child-input mode.
                return False
            return True

        def compose(self) -> ComposeResult:
            with Horizontal(id="top_header"):
                yield Static(f"{S('logo')} DUSKY UPDATER", id="header_title", markup=False)

            with Horizontal():
                with Vertical(id="sidebar"):
                    yield ListView(id="task_list")

                with Vertical(id="log_container"):
                    max_lines = GLOBAL_CONFIG.get("ui", {}).get("max_log_lines", 6000)
                    with ContentSwitcher(initial="log-main", id="log_switcher"):
                        yield FocusableRichLog(id="log-main", markup=True, wrap=True, auto_scroll=True, max_lines=max_lines)
                        yield FocusableRichLog(id="log-report", markup=True, wrap=True, auto_scroll=False, max_lines=max_lines)
                        for i in range(len(self.tasks)):
                            yield FocusableRichLog(id=f"log-task-{i}", markup=True, wrap=True, auto_scroll=True, max_lines=max_lines)

            yield ProgressBar(total=len(self.tasks), id="main_progress", show_eta=False)

        async def on_mount(self) -> None:
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGTERM, signal.SIGHUP):
                self._previous_signal_handlers[signum] = signal.getsignal(signum)
                loop.add_signal_handler(signum, self._terminate, signum)
            self.progress = self.query_one("#main_progress", ProgressBar)

            self.sleep_inhibitor = SleepInhibitor(enabled=True)
            try:
                self.state_store = StateStore(self.profile)
            except Exception as e:
                self.state_store = None
                self.add_warning("persistence", f"StateStore unavailable: {e}")
            try:
                self.run_logger = RunLogger(self.profile, self.run_id)
                self.run_logger.warning_source = self.run_warnings
                self.run_logger.git_summary_source = self.git_summary
            except Exception as e:
                self.run_logger = None
                self.add_warning("logging", f"RunLogger unavailable: {e}")
            try:
                self.once_store = OnceStore()
            except Exception as e:
                self.once_store = None
                self.add_warning("persistence", f"OnceStore unavailable: {e}")

            stored_durations = self.state_store.durations() if self.state_store else {}
            for t in self.tasks:
                if t.state_key in stored_durations and stored_durations[t.state_key] > 0:
                    t.estimated_duration = stored_durations[t.state_key]

            list_view = self.query_one("#task_list", ListView)
            list_view.append(MainLogItem())
            for i, task in enumerate(self.tasks):
                list_view.append(TaskItem(task, i))
            list_view.append(ReportLogItem())
            with suppress(Exception):
                self._task_widgets = list(list_view.query(TaskItem).nodes)
            self._update_header_state()

            self.log_main(f"[bold {THEME['accent']}]======================================================[/]")
            self.log_main(f"[bold {THEME['fg']}] DUSKY UPDATER — {datetime.now().strftime('%H:%M:%S')}[/]")
            self.log_main(f"[bold {THEME['accent']}] Profile: {self.profile.name}[/]")
            self.log_main(f"[bold {THEME['accent']}]======================================================[/]")

            if self.has_sudo:
                self.heartbeat_task = asyncio.create_task(
                    SudoEngine.maintain_heartbeat(
                        error_callback=lambda msg: self.log_main(f"[bold {THEME['warning']}][WARN] {msg}[/]")
                    )
                )

            # This is the candidate health boundary used by the independent
            # supervisor. Reaching it proves imports/config/profile parsing,
            # dependency/bootstrap checks, Textual app construction+mount,
            # storage/log initialization, and UI queries all succeeded. It is
            # still before Git mutation or any user child task.
            await asyncio.to_thread(_supervisor_health_checkpoint, self.profile)
            if not OPT_DRY_RUN:
                # Retention pruning is destructive housekeeping. Run it only
                # after the candidate has crossed the independent startup-health
                # boundary so a broken replacement cannot delete old recovery/log
                # data before it is known to start successfully.
                try:
                    await asyncio.to_thread(auto_prune)
                except Exception as e:
                    self.add_warning("logging", f"optional retention pruning failed: {e}")
            self.run_worker(self.execute_pipeline(), exclusive=True, thread=False)

        def _terminate(self, signum: int) -> None:
            self.exit_code = 128 + signum
            self.log_main(f"Termination requested: {signal.Signals(signum).name}")
            if self._restart_handoff is not None:
                with suppress(OSError):
                    self._restart_handoff.unlink(missing_ok=True)
                self._restart_handoff = None
            self.action_quit()

        async def on_unmount(self) -> None:
            # Textual cancels workers before unmount but does not await their
            # cleanup. Keep databases/logs open until the pipeline has finished.
            with suppress(Exception):
                await self.workers.wait_for_complete()
            loop = asyncio.get_running_loop()
            for signum, previous in self._previous_signal_handlers.items():
                loop.remove_signal_handler(signum)
                signal.signal(signum, previous)
            self._previous_signal_handlers.clear()
            if self.heartbeat_task and not self.heartbeat_task.done():
                self.heartbeat_task.cancel()
            if self.sleep_inhibitor:
                self.sleep_inhibitor.close()
            with suppress(Exception):
                AudioNotifier.reap()
            with suppress(Exception):
                DesktopNotifier.reap()
            if self.state_store:
                self.state_store.close()
            if self.run_logger:
                self.run_logger.close()
            if self.once_store:
                self.once_store.close()

        def _canonical_plain(self, message: Any) -> str:
            # Canonical plain text for search/log buffers, separate from Rich
            # renderables: never store str(Syntax) markup or Rich tags.
            try:
                if hasattr(message, "plain"):
                    return str(message.plain)
                if hasattr(message, "code"):
                    return str(message.code)
            except Exception:
                pass
            text = strip_ansi(str(message))
            # Strip residual Rich markup tags without corrupting literal
            # bracket text: only remove well-formed style tags.
            try:
                text = re.sub(r"\[(?:/?(?:bold|dim|italic|underline|blink|reverse|strike)(?:\s+[^\]]*)?|/?[a-zA-Z_][a-zA-Z0-9_#]*(?:\s+[^\]]*)?)\]", "", text)
            except Exception:
                pass
            return text

        def add_warning(self, kind: str, message: str, *, task: str = "", path: str = "", recovery: str = "") -> None:
            rec = {"kind": str(kind), "message": str(message), "task": str(task), "path": str(path), "recovery": str(recovery)}
            if rec not in self.run_warnings:
                self.run_warnings.append(rec)

        @staticmethod
        async def _store_call(func, *args, **kwargs):
            # Cancelling to_thread() cannot stop its running database operation.
            # Retain and join it before cancellation can close the connection.
            operation = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                with suppress(Exception):
                    await operation
                raise

        async def _safe_state_mark(
            self, task: DuskyTask, status: str, *, exit_code: int | None = None,
            note: str = "", duration: float = 0.0,
        ) -> bool:
            if self.state_store is None or OPT_DRY_RUN:
                return True
            try:
                await self._store_call(
                    self.state_store.mark, task, status, exit_code=exit_code,
                    note=note, duration=duration,
                )
                return True
            except Exception as e:
                self.add_warning(
                    "persistence", f"state write failed after task outcome was determined: {e}",
                    task=task.name,
                )
                return False

        async def _safe_once_check(self, task: DuskyTask) -> Literal["run", "skip", "notify_sealed", "error"]:
            if self.once_store is None:
                return "error"
            try:
                return await self._store_call(self.once_store.check_marker_status, task, self.profile.name)
            except Exception as e:
                self.add_warning(
                    "persistence", f"once-state read failed; task skipped to avoid an unsafe rerun: {e}",
                    task=task.name,
                )
                return "error"

        async def _safe_once_sealed_notified(self, task: DuskyTask) -> bool:
            if self.once_store is None or OPT_DRY_RUN:
                return True
            try:
                await self._store_call(self.once_store.mark_sealed_notified, task, self.profile.name)
                return True
            except Exception as e:
                self.add_warning("persistence", f"once sealed-notification write failed: {e}", task=task.name)
                return False

        async def _safe_once_mark_success(self, task: DuskyTask, *, exit_code: int) -> bool:
            if self.once_store is None or OPT_DRY_RUN:
                return True
            try:
                await self._store_call(
                    self.once_store.mark_success, task, self.profile.name,
                    exit_code=exit_code, run_id=getattr(self, "run_id", ""),
                )
                return True
            except Exception as e:
                # The child already succeeded. Preserve that truth and retain a
                # warning; a persistence failure must never rewrite execution
                # history as a child-process failure.
                self.add_warning("persistence", f"once-state success write failed: {e}", task=task.name)
                return False

        def _safe_close_task_log(self, task: DuskyTask, index: int, status: str, exit_code: int, duration: float) -> None:
            if self.run_logger is None:
                return
            try:
                self.run_logger.close_task(task, index, status, exit_code, duration)
            except Exception as e:
                self.add_warning("logging", f"task log finalization failed: {e}", task=task.name)

        def _append_log_line(self, key: int | str, line: str) -> None:
            max_lines = int(GLOBAL_CONFIG["ui"]["max_log_lines"])
            max_bytes = int(GLOBAL_CONFIG["execution"]["log_max_bytes"])
            max_line_bytes = int(GLOBAL_CONFIG["execution"]["log_max_line_bytes"])
            encoded = line.encode("utf-8", errors="replace")
            if len(encoded) > max_line_bytes:
                encoded = encoded[:max_line_bytes]
                line = encoded.decode("utf-8", errors="ignore") + " …[truncated]"
            dq = self._log_lines.setdefault(key, deque())
            self._log_bytes = getattr(self, "_log_bytes", {})
            while len(dq) >= max_lines and dq:
                old = dq.popleft()
                self._log_bytes[key] = max(0, self._log_bytes.get(key, 0) - len(old.encode("utf-8", errors="replace")))
            dq.append(line)
            self._log_bytes[key] = self._log_bytes.get(key, 0) + len(line.encode("utf-8", errors="replace"))
            while self._log_bytes.get(key, 0) > max_bytes and len(dq) > 1:
                old = dq.popleft()
                self._log_bytes[key] = max(0, self._log_bytes[key] - len(old.encode("utf-8", errors="replace")))

        def log_main(self, message: Any) -> None:
            with suppress(Exception):
                self.query_one("#log-main", RichLog).write(message)
            plain = self._canonical_plain(message)
            for line in plain.splitlines() or [""]:
                self._append_log_line("main", line)
            if self.run_logger and self.run_logger.enabled:
                try:
                    for line in plain.splitlines():
                        if line.strip():
                            self.run_logger.system(line)
                except OSError as e:
                    self.add_warning("logging", f"main run log write failed: {e}")
                    self.run_logger.enabled = False
            if LOG_FILE and GLOBAL_CONFIG["logging"]["enabled"]:
                timestamp = datetime.now().strftime("%H:%M:%S")
                try:
                    with open(LOG_FILE, "a", encoding="utf-8") as f:
                        for line in plain.splitlines():
                            f.write(f"[{timestamp}] [MAIN   ] {line}\n")
                except OSError as e:
                    self.add_warning("logging", f"legacy log write failed: {e}", path=str(LOG_FILE))

        def log_task(self, message: Any, index: int) -> None:
            with suppress(Exception):
                self.query_one(f"#log-task-{index}", RichLog).write(message)
            plain = self._canonical_plain(message)
            for line in plain.splitlines() or [""]:
                self._append_log_line(index, line)
            if self.run_logger and self.run_logger.enabled and 0 <= index < len(self.tasks):
                try:
                    self.run_logger.write_task(self.tasks[index], index, plain)
                except OSError as e:
                    self.add_warning("logging", f"task log write failed: {e}", task=self.tasks[index].name)

        def _restore_handoff(self, handoff: Path | None) -> bool:
            # Single-use bound handoff from the pre-restart process. Applies
            # the EXACT git task outcomes (never blanket-success). Missing or
            # invalid handoff returns False so the caller runs a normal sync
            # instead of leaving tasks pending.
            payload = validate_restart_handoff(handoff, self.profile)
            if handoff is not None:
                with suppress(OSError):
                    handoff.unlink(missing_ok=True)
            if payload is None:
                return False
            if "run_start_mono" in payload:
                self.run_start_mono = payload["run_start_mono"]
                self.phase_durations.update(payload["phase_durations"])
            self.git_summary.update(payload["git_summary"])
            for warning in payload.get("warnings", []):
                if isinstance(warning, dict):
                    rec = {k: str(warning.get(k, "")) for k in ("kind", "message", "task", "path", "recovery")}
                    if rec not in self.run_warnings:
                        self.run_warnings.append(rec)
            os.environ["DUSKY_RESTART_GENERATION"] = str(payload.get("restart_generation", 0))
            outcome_to_close = {"success": "completed", "skipped": "skipped", "failed": "failed"}
            outcome_to_code = {"success": 0, "skipped": 0, "failed": 1}
            for i, saved in enumerate(payload["git_tasks"]):
                status = saved.get("status", "skipped")
                code = saved.get("exit_code", outcome_to_code.get(status, 1))
                self.update_task_state(i, status)
                self._safe_close_task_log(
                    self.tasks[i], i, outcome_to_close.get(status, "skipped"), code, 0.0
                )
            summary = payload["git_summary"]
            diff = summary.get("diff") or ""
            commits = summary.get("commits", "?")
            files_changed = summary.get("files_changed", "?")
            branch = summary.get("branch") or self.profile.branch
            before = summary.get("before_head") or ""
            after = summary.get("after_head") or ""
            unrelated = bool(summary.get("unrelated_histories"))

            def short(sha: str) -> str:
                return sha[:10] if sha else "?"

            self.log_task(f"\n[bold {THEME['accent']}]Git Bare Repo Validation[/]", 0)
            self.log_task(f"[dim]Branch: {branch}[/dim]", 0)
            if before:
                self.log_task(f"[dim]HEAD before update: {short(before)}[/dim]", 0)
            if unrelated:
                self.log_task("[dim]Local history did not share ancestry with upstream — full recovery performed.[/dim]", 0)

            self.log_task(f"\n[bold {THEME['accent']}]Fetch Upstream & Diff[/]", 1)
            if diff.strip():
                self.log_task(f"[dim]Commits behind: {commits}  |  Files changed: {files_changed}[/dim]", 1)
                self.log_task(f"[dim]{'-' * 46}[/dim]", 1)
                self.log_task(Syntax(diff, "diff", theme="monokai", background_color="default", word_wrap=True), 1)
            else:
                self.log_task("[dim]No textual diff captured.[/dim]", 1)

            self.log_task(f"\n[bold {THEME['accent']}]Forensic Collision Backup[/]", 2)
            if unrelated:
                note = f"Collision backup performed during diverged-history recovery ({summary.get('collisions', 0)} collision(s))."
            elif summary.get("collisions") is None:
                note = "No collision-backup details recorded."
            elif summary.get("collisions") == 0:
                note = "No work-tree collisions detected."
            else:
                note = f"{summary.get('collisions')} work-tree collision(s) backed up."
                if summary.get("collision_backup"):
                    note += f" Backup: {summary.get('collision_backup')}"
            self.log_task(f"[dim]{note}[/dim]", 2)

            self.log_task(f"\n[bold {THEME['accent']}]Snapshot[/]", 3)
            if unrelated:
                note = "Full tracked-tree backup performed during diverged-history recovery."
                if summary.get("local_mods"):
                    note += f" {summary.get('local_mods')} local tracked modification(s) backed up for restore."
                    if summary.get("local_mods_backup"):
                        note += f" Backup: {summary.get('local_mods_backup')}"
            elif summary.get("local_mods") is None:
                note = "No snapshot details recorded."
            elif summary.get("local_mods") == 0:
                note = "No local tracked modifications found. Snapshot skipped."
            else:
                note = f"{summary.get('local_mods')} local tracked modification(s) backed up."
                if summary.get("local_mods_backup"):
                    note += f" Backup: {summary.get('local_mods_backup')}"
            self.log_task(f"[dim]{note}[/dim]", 3)

            self.log_task(f"\n[bold {THEME['accent']}]Apply Bare Updates (Reset)[/]", 4)
            if unrelated:
                note = "Reset applied during diverged-history recovery."
            elif before or after:
                note = f"Reset applied: {short(before)} -> {short(after)}."
            else:
                note = "Reset applied and synchronized."
            if summary.get("local_mods_restored") is True:
                note += " Local modifications restored."
            elif summary.get("local_mods_restored") is False:
                note += " Some local modifications could not be restored (backup preserved)."
            elif summary.get("local_mods") == 0:
                note += " No local modifications to restore."
            self.log_task(f"[dim]{note}[/dim]", 4)

            self.log_main(f"\n[bold {THEME['accent']}]Update applied. Select a GIT task on the left to review what changed.[/]")
            self.log_main(f"[dim]Restart handoff applied (run {escape(str(payload.get('run_id', '')))}).[/]")
            return True

        def update_task_state(self, index: int, new_status: str) -> None:
            task = self.tasks[index]
            if task.mode == "GIT":
                task.outcome = "completed" if new_status == "success" else new_status
                if new_status in ("success", "skipped", "failed"):
                    task.exit_code = 1 if new_status == "failed" else 0
            old_status = task.status
            terminal = ("success", "failed", "skipped")
            # Idempotent progress: repeated terminal transitions advance once.
            if old_status == new_status:
                return
            if old_status in terminal and new_status in terminal:
                task.status = new_status
                with suppress(Exception):
                    if index < len(self._task_widgets):
                        self._task_widgets[index].status = new_status
                self._apply_filter()
                return
            task.status = new_status  # type: ignore
            with suppress(Exception):
                if index < len(self._task_widgets):
                    self._task_widgets[index].status = new_status
                else:
                    list_view = self.query_one("#task_list", ListView)
                    task_nodes = list_view.query(TaskItem).nodes
                    if index < len(task_nodes):
                        task_nodes[index].status = new_status

            if new_status == "running":
                if getattr(self, "follow_mode", True):
                    with suppress(Exception):
                        list_view = self.query_one("#task_list", ListView)
                        target_pos = index + 1
                        list_view.index = target_pos
                        switcher = self.query_one("#log_switcher", ContentSwitcher)
                        switcher.current = f"log-task-{index}"

            if new_status in terminal and index not in self._finalized:
                self._finalized.add(index)
                if self.progress is not None:
                    with suppress(Exception):
                        self.progress.advance(1)
            self._apply_filter()

        def _apply_filter(self) -> None:
            # Reapply the task filter on status changes so navigation and
            # search always reflect visible results.
            with suppress(Exception):
                list_view = self.query_one("#task_list", ListView)
                for item in list_view.query(TaskItem):
                    if self.filter_mode == "all":
                        item.display = True
                    else:
                        item.display = (item.status == self.filter_mode)

        def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
            item = event.item
            if item is None:
                return
            with suppress(Exception):
                switcher = self.query_one("#log_switcher", ContentSwitcher)
                if isinstance(item, MainLogItem):
                    switcher.current = "log-main"
                elif isinstance(item, ReportLogItem):
                    switcher.current = "log-report"
                elif isinstance(item, TaskItem):
                    switcher.current = f"log-task-{item.task_index}"

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            self.follow_mode = False
            self._update_header_state()

        def _queue_pty_write(self, data: bytes) -> None:
            """Enqueue child-input bytes preserving order with a byte bound.

            Single ordered producer/buffer: sync callers append to
            ``_pty_write_pending`` (FIFO) with an explicit byte bound
            (``_pty_write_max_bytes``, default 64 KiB). Overload policy is
            explicit: when the bound would be exceeded, the incoming chunk is
            dropped with a warning and a dropped-byte counter (order of
            accepted bytes is never rearranged). No per-keystroke tasks are
            created, so burst input cannot relocate unbounded backlog into
            tasks. The single writer task drains in FIFO order via async
            readiness, preserving partial writes. When no writer
            infrastructure exists (no active PTY), a synchronous bounded
            write is used directly with no independent async writers (so no
            interleaving); when no running loop exists, the same sync path
            applies.
            """
            fd = getattr(self, "current_pty_master", None)
            if fd is None or not data:
                return
            # Lazily ensure ordered buffer exists (covers headless probes
            # that only set legacy _pty_write_queue).
            pending = getattr(self, "_pty_write_pending", None)
            if pending is None:
                try:
                    self._pty_write_pending = deque()
                    self._pty_write_bytes = 0
                    pending = self._pty_write_pending
                except Exception:
                    with suppress(OSError):
                        _write_all_nonblocking(fd, data)
                    return
            event = getattr(self, "_pty_write_event", None)
            # No active writer (no event): synchronous ordered fallback, no tasks.
            if event is None:
                with suppress(OSError):
                    _write_all_nonblocking(fd, data)
                return
            max_bytes = int(getattr(self, "_pty_write_max_bytes", 65536) or 65536)
            cur = int(getattr(self, "_pty_write_bytes", 0) or 0)
            if cur + len(data) > max_bytes:
                try:
                    self._pty_write_dropped = int(getattr(self, "_pty_write_dropped", 0) or 0) + len(data)
                except Exception:
                    pass
                try:
                    logm = getattr(self, "log_main", None)
                    if callable(logm):
                        logm(f"[WARN] PTY input dropped ({len(data)} bytes, buffer {cur}/{max_bytes}): backpressure limit; order preserved.")
                except Exception:
                    pass
                return
            try:
                pending.append(data)
                self._pty_write_bytes = cur + len(data)
            except Exception:
                return
            try:
                event.set()
            except Exception:
                pass

        async def _pty_writer_loop(self, fd: int, queue: asyncio.Queue[bytes] | None = None) -> None:
            # Single ordered consumer: drains _pty_write_pending FIFO in order
            # via async readiness. `queue` is legacy/ignored (kept for compat).
            # Handles child exit (False -> clear pending, exit), cancellation,
            # and fd reuse (pending cleared on shutdown by caller).
            while True:
                event = getattr(self, "_pty_write_event", None)
                pending = getattr(self, "_pty_write_pending", None)
                if event is None or pending is None:
                    return
                if not pending:
                    try:
                        # Wait for new input with cancellation support.
                        await event.wait()
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        break
                    try:
                        event.clear()
                    except Exception:
                        pass
                    continue
                try:
                    data = pending.popleft()
                except IndexError:
                    continue
                try:
                    cur = int(getattr(self, "_pty_write_bytes", 0) or 0)
                    self._pty_write_bytes = max(0, cur - len(data))
                except Exception:
                    pass
                try:
                    ok = await _awrite_all_nonblocking(fd, data)
                    if not ok:
                        # Child exited/closed: drop remaining buffered input
                        # (cannot succeed) and exit so shutdown never spins.
                        try:
                            pending.clear()
                            self._pty_write_bytes = 0
                        except Exception:
                            pass
                        break
                except asyncio.CancelledError:
                    # Put back ordering? Cancellation aborts pending write;
                    # re-queue at front to preserve order for shutdown drain?
                    # Simpler: drop current chunk (already debited), clear
                    # nothing else, exit. Caller clears on reuse.
                    break
                except Exception:
                    break

        async def _retry_prompt_after(self, delay: float) -> None:
            try:
                await asyncio.sleep(max(0.0, delay))
                # Allow processing to schedule the next cooldown immediately.
                self._prompt_retry_task = None
                self._maybe_respond_prompt("")
            except asyncio.CancelledError:
                raise
            finally:
                if self._prompt_retry_task is asyncio.current_task():
                    self._prompt_retry_task = None

        def _schedule_prompt_retry(self, delay: float) -> None:
            task = self._prompt_retry_task
            if task is None or task.done():
                self._prompt_retry_task = asyncio.create_task(self._retry_prompt_after(delay))

        async def _prompt_wait_notice(self, delay: float, snapshot: str, task_index: int) -> None:
            try:
                await asyncio.sleep(max(1.0, delay))
                if self.current_pty_master is None:
                    return
                tail = self._prompt_buffer.strip()
                if not tail or not tail.endswith(snapshot):
                    return
                task_name = self.tasks[task_index].name if 0 <= task_index < len(self.tasks) else ""
                task_timeout = (
                    self.tasks[task_index].timeout
                    if 0 <= task_index < len(self.tasks) else None
                )
                self.add_warning(
                    "prompt",
                    f"interactive prompt remained unresolved for {delay:.0f}s; "
                    f"task execution remains bounded by {task_timeout:.0f}s"
                    if task_timeout else
                    f"interactive prompt remained unresolved for {delay:.0f}s",
                    task=task_name,
                )
                self.log_main(
                    f"[bold {THEME['warning']}][WARN][/] Unresolved prompt still waiting after {delay:.0f}s; "
                    "manual input may be required. The task-level timeout remains in force."
                )
            except asyncio.CancelledError:
                raise
            finally:
                self._prompt_wait_task = None

        def _cancel_prompt_wait_notice(self) -> None:
            task = self._prompt_wait_task
            if task is not None and not task.done():
                task.cancel()
            self._prompt_wait_task = None

        def _schedule_prompt_wait_notice(self, tail: str) -> None:
            task = self._prompt_wait_task
            if task is not None and not task.done():
                return
            delay = float(GLOBAL_CONFIG["execution"]["prompt_wait_timeout"])
            snapshot = tail[-300:]
            idx = int(getattr(self, "_prompt_task_index", 0) or 0)
            self._prompt_wait_task = asyncio.create_task(
                self._prompt_wait_notice(delay, snapshot, idx)
            )

        def _maybe_respond_prompt(self, text: str) -> None:
            if self.current_pty_master is None:
                return
            # Keep the prompt buffer ANSI-free so regex match offsets map
            # exactly to the consumed buffer. This avoids accidentally
            # discarding part of a following prompt when escape sequences
            # precede a match.
            clean_text = ANSI_STRIP_REGEX.sub("", text)
            self._prompt_buffer = (self._prompt_buffer + clean_text)[-8192:]
            cooldown = float(GLOBAL_CONFIG["prompts"]["cooldown"])
            for _ in range(8):
                tail = self._prompt_buffer
                chosen = None
                for name, pattern, kind in PROMPT_RULES:
                    m = pattern.search(tail)
                    if m is not None:
                        chosen = (name, m, kind)
                        break
                if chosen is None:
                    break
                name, m, kind = chosen
                count = self._prompt_counts.get(name, 0)
                max_count = 5 if name == "sudo_password" else 500
                if count >= max_count:
                    # Consume this already-exhausted prompt so it cannot starve
                    # later prompts in the same read.
                    self._prompt_buffer = self._prompt_buffer[min(len(self._prompt_buffer), m.end()):]
                    continue
                now = time.monotonic()
                remaining = cooldown - (now - self._prompt_last.get(name, 0.0))
                if remaining > 0:
                    self._schedule_prompt_retry(remaining)
                    break
                matched = tail[m.start():m.end()].strip().splitlines()
                shown = (matched[-1] if matched else f"[{name}]")[-200:]
                response: bytes | None = None
                if kind == "password":
                    allow = bool(GLOBAL_CONFIG["prompts"]["allow_insecure_password_autofeed"])
                    if allow and SudoEngine._password:
                        response = SudoEngine._password.encode("utf-8") + b"\r"
                        self.log_task("[dim]Auto-answered password prompt (autofeed enabled)[/dim]", getattr(self, '_prompt_task_index', 0))
                    else:
                        self.add_warning("prompt", "password prompt could not be auto-answered", task=getattr(self.tasks[getattr(self, '_prompt_task_index', 0)], 'name', ''))
                elif kind == "yes":
                    response = b"y\r"
                elif kind == "no":
                    response = b"n\r"
                if response is not None:
                    self._cancel_prompt_wait_notice()
                    self.log_task(f"[dim]Auto-answered prompt [{escape(name)}]: {escape(shown)}[/]", getattr(self, '_prompt_task_index', 0))
                    self._queue_pty_write(response)
                self._prompt_counts[name] = count + 1
                self._prompt_last[name] = now
                # Consume only the matched prompt; preserve any following prompt
                # already received in the same PTY read.
                self._prompt_buffer = self._prompt_buffer[min(len(self._prompt_buffer), m.end()):]

            tail = self._prompt_buffer.strip()
            if tail and tail[-1:] in ("?", ":", "$", "#", ">", "]"):
                last_line = tail.splitlines()[-1][-300:]
                redacted = re.sub(r"(?i)(password|passwd|passphrase|secret|token)[^\n]*", r"\1: [redacted]", last_line)
                self.log_main(f"[WARN] Unresolved interactive prompt (no auto-answer rule yet): {redacted[-200:]}")
                self._schedule_prompt_wait_notice(tail)
            else:
                self._cancel_prompt_wait_notice()

        @staticmethod
        def _set_pty_size(fd: int) -> None:
            try:
                size = os.get_terminal_size()
                sidebar_percent = GLOBAL_CONFIG.get("ui", {}).get("sidebar_width", 35)
                actual_cols = max(10, int(size.columns * (1 - (sidebar_percent / 100))) - 2)
                winsize = struct.pack("HHHH", size.lines, actual_cols, 0, 0)
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
            except (OSError, ValueError):
                fallback_cols = GLOBAL_CONFIG.get("ui", {}).get("fallback_pty_columns", 120)
                fallback_lines = GLOBAL_CONFIG.get("ui", {}).get("fallback_pty_lines", 40)
                with suppress(OSError):
                    winsize = struct.pack("HHHH", fallback_lines, fallback_cols, 0, 0)
                    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

        async def execute_pty_command(self, cmd: list[str], timeout: float = 0.0, task_index: int = 0) -> tuple[bool, int | None]:
            try:
                master_fd, slave_fd = pty.openpty()
            except OSError as e:
                self.log_main(f"[FATAL] PTY allocation failed: {e}")
                return False, None

            self.current_pty_master = master_fd
            self._prompt_task_index = task_index
            # Reset prompt buffers/counts per command attempt.
            self._prompt_buffer = ""
            self._prompt_counts = {}
            self._prompt_last = {}
            # Buffered input + single writer task: ordered byte-bounded FIFO,
            # one task per PTY (no per-keystroke tasks). Prevent prior-command
            # input leaking into the next command by resetting the buffer.
            try:
                self._pty_write_pending = deque()
                self._pty_write_bytes = 0
                self._pty_write_dropped = 0
                self._pty_write_event = asyncio.Event()
                self._pty_write_queue = None  # legacy, unused
                self._pty_write_fd = master_fd
                # Writer starts only after the child has spawned successfully;
                # this prevents leaked writer tasks on spawn/setup failure.
                self._pty_writer_task = None
            except RuntimeError:
                self._pty_write_pending = deque()
                self._pty_write_bytes = 0
                self._pty_write_event = None
                self._pty_write_queue = None
                self._pty_writer_task = None
                self._pty_write_fd = None

            transport: asyncio.Transport | None = None
            proc: asyncio.subprocess.Process | None = None
            file_obj = None
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            line_buffer = ""
            read_task: asyncio.Task | None = None

            async def _shutdown(code: int | None) -> tuple[bool, int | None]:
                # Centralized child ownership and shutdown: terminate, bounded
                # grace, kill when necessary, await reaping, cancel/await the
                # reader AND single writer task, then close resources. True
                # status is preserved; unknown (None) is never reported as
                # success. Always shuts down the group even when the leader
                # already exited, so forked descendants (including
                # TERM-resistant children) do not leak. All writer-related
                # state is tracked/cancelled before the fd is closed/reused,
                # and pending input is cleared so no bytes leak into the next
                # command (including on reused fd numbers).
                nonlocal read_task, transport, file_obj, master_fd, slave_fd
                if proc is not None:
                    with suppress(Exception):
                        await _terminate_process_group(proc)
                if read_task is not None and not read_task.done():
                    read_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        async with asyncio.timeout(2.0):
                            await read_task
                # Bounded flush of ordered pending input, then cancel writer.
                # No dropped tails on live FDs; bounded so shutdown never hangs.
                wtask = getattr(self, "_pty_writer_task", None)
                if wtask is not None and not wtask.done():
                    try:
                        async with asyncio.timeout(2.0):
                            # Wait until pending drains (writer clears event).
                            while True:
                                pend = getattr(self, "_pty_write_pending", None)
                                if pend is None or len(pend) == 0:
                                    break
                                await asyncio.sleep(0.02)
                    except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                    except Exception:
                        pass
                    wtask.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        async with asyncio.timeout(2.0):
                            await wtask
                # Clear all writer state before fd close/reuse (prevents leak
                # into next command, even if the OS reuses the fd number).
                try:
                    pend = getattr(self, "_pty_write_pending", None)
                    if pend is not None:
                        pend.clear()
                except Exception:
                    pass
                self._pty_write_bytes = 0
                self._pty_write_event = None
                self._pty_write_queue = None
                self._pty_writer_task = None
                self._pty_write_fd = None
                retry_task = getattr(self, "_prompt_retry_task", None)
                if retry_task is not None and not retry_task.done():
                    retry_task.cancel()
                self._prompt_retry_task = None
                self._cancel_prompt_wait_notice()
                if transport is not None:
                    with suppress(Exception):
                        transport.close()
                elif file_obj is not None:
                    with suppress(Exception):
                        file_obj.close()
                elif master_fd != -1:
                    with suppress(OSError):
                        os.close(master_fd)
                if slave_fd != -1:
                    with suppress(OSError):
                        os.close(slave_fd)
                self.current_pty_master = None
                self.active_child_pid = None
                self.active_child_group = False
                master_fd = -1
                slave_fd = -1
                if code is None:
                    return False, None
                return code == 0, code

            try:
                os.set_blocking(master_fd, False)
                self._set_pty_size(slave_fd)
                spawn_cmd = cmd
                spawn_kwargs: dict[str, Any] = {"cwd": str(WORK_TREE)}
                setsid_bin = shutil.which("setsid")
                if setsid_bin:
                    # util-linux setsid --ctty makes the PTY slave (stdin) the
                    # controlling terminal without unsafe preexec_fn hooks.
                    spawn_cmd = [setsid_bin, "--ctty", "--wait", "--", *cmd]
                else:
                    spawn_kwargs["start_new_session"] = True
                    self.add_warning("pty", "setsid --ctty unavailable; /dev/tty access is unsupported for inline PTY tasks")
                proc = await asyncio.create_subprocess_exec(
                    *spawn_cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                    close_fds=True, **spawn_kwargs
                )
                self._pty_writer_task = asyncio.create_task(self._pty_writer_loop(master_fd))
            except asyncio.CancelledError:
                await _shutdown(None)
                raise
            except Exception as e:
                self.log_main(f"[FATAL] PTY spawn failed: {e}")
                await _shutdown(None)
                return False, None

            with suppress(OSError):
                os.close(slave_fd)
            slave_fd = -1

            self.active_child_pid = proc.pid
            self.active_child_group = True

            try:
                loop = asyncio.get_running_loop()
                reader = asyncio.StreamReader(limit=1024 * 1024)
                pty_writer = self._pty_writer_task

                def terminal_closed() -> None:
                    if self._pty_writer_task is not pty_writer:
                        return  # Delayed close callback from a previous task.
                    self.current_pty_master = None
                    writer = self._pty_writer_task
                    if writer is not None and not writer.done():
                        writer.cancel()
                    retry = getattr(self, "_prompt_retry_task", None)
                    if retry is not None and not retry.done():
                        retry.cancel()
                    self._cancel_prompt_wait_notice()
                    self._pty_write_pending.clear()
                    self._pty_write_bytes = 0

                protocol = _PTYStreamReaderProtocol(reader, terminal_closed)

                file_obj = os.fdopen(master_fd, "rb", buffering=0)
                master_fd = -1

                transport, _ = await loop.connect_read_pipe(lambda: protocol, file_obj)

                async def consume_output() -> None:
                    nonlocal line_buffer

                    while True:
                        try:
                            chunk = await reader.read(4096)
                        except asyncio.CancelledError:
                            raise
                        except OSError as e:
                            # Linux PTY masters report EIO when the last slave
                            # closes. This is EOF, not the child's exit status.
                            # In particular, never kill a successful child just
                            # because its terminal closed before wait() completed.
                            if e.errno != errno.EIO:
                                raise
                            chunk = b""

                        if not chunk:
                            # Flush the incremental decoder at EOF so a final
                            # partial sequence is not lost.
                            try:
                                tail = decoder.decode(b"", final=True)
                            except Exception:
                                tail = ""
                            if tail:
                                line_buffer += tail
                            if line_buffer:
                                for line in BRACKET_NEWLINE_RE.split(line_buffer):
                                    if line:
                                        self.log_task(Text.from_ansi(line), task_index)
                                line_buffer = ""
                            break

                        try:
                            text = decoder.decode(chunk)
                        except Exception:
                            text = chunk.decode("utf-8", errors="replace")

                        if text:
                            self._maybe_respond_prompt(text)

                        line_buffer += text

                        if len(line_buffer) > 32768:
                            last_nl = line_buffer.rfind('\n', 0, 32768)
                            cut_idx = last_nl + 1 if last_nl != -1 else 32768
                            self.log_task(Text.from_ansi(line_buffer[:cut_idx]), task_index)
                            line_buffer = line_buffer[cut_idx:]

                        while True:
                            m = SINGLE_NEWLINE_RE.search(line_buffer)
                            if not m:
                                break
                            idx = m.start()
                            line = line_buffer[:idx]
                            line_buffer = line_buffer[idx + 1:]
                            if line:
                                clean = line.strip("\r\n")
                                stripped = ANSI_STRIP_REGEX.sub("", clean) if "\x1b" in clean else clean

                                pct = speed = eta = None
                                if "%" in stripped:
                                    if match := PCT_REGEX.search(stripped):
                                        pct = match.group(0)
                                if "b/s" in stripped.lower():
                                    if match := SPEED_ETA_REGEX.search(stripped):
                                        speed, eta = match.group(1), match.group(2)
                                    elif match := ALT_SPEED_ETA_REGEX.search(stripped):
                                        speed, eta = match.group(1), match.group(2)

                                if pct or speed:
                                    telemetry_str = f" [dim {THEME['accent']}]({pct or ''} {speed or ''})[/]"
                                    with suppress(Exception):
                                        lbl = self.query_one(f"#lbl-{task_index}", Label)
                                        lbl.update(f"{task_index+1}. {self.tasks[task_index].name}{telemetry_str}")

                                self.log_task(Text.from_ansi(line), task_index)

                async def read_loop() -> None:
                    try:
                        await consume_output()
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        self.add_warning("pty", f"output reader failed: {e}", task=self.tasks[task_index].name)
                        # Also cover decoding/rendering/prompt errors: never
                        # leave a child blocked behind an abandoned reader.
                        # Keep cleanup owned and awaited, not a detached task
                        # that could later signal a reused process group.
                        await _terminate_process_group(proc)
                        raise

                read_task = asyncio.create_task(read_loop())

                try:
                    code = await wait_for_process(proc, timeout=timeout if timeout > 0 else None)
                    try:
                        async with asyncio.timeout(2.0):
                            await read_task
                    except (TimeoutError, asyncio.TimeoutError):
                        read_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await read_task
                    return await _shutdown(code)

                except (TimeoutError, asyncio.TimeoutError):
                    await _terminate_process_group(proc)
                    read_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        try:
                            async with asyncio.timeout(2.0):
                                await read_task
                        except (TimeoutError, asyncio.TimeoutError):
                            pass
                    final = proc.returncode
                    await _shutdown(final)
                    return False, 124

                except asyncio.CancelledError:
                    await _shutdown(None)
                    raise

            except asyncio.CancelledError:
                await _shutdown(None)
                raise
            except Exception as e:
                self.log_main(f"[FATAL] PTY execution failed: {e}")
                await _shutdown(None)
                return False, None

        @contextmanager
        def _suspend_ui(self):
            # The async child wait leaves Textual timers alive. Prevent them
            # from writing to the stopped driver's bounded output queue.
            error: BaseException | None = None
            with self.batch_update():
                with self.suspend():
                    tty_fd: int | None = None
                    tty_attrs = None
                    try:
                        # Capture cooked mode AFTER suspension, before a child
                        # TUI can change it (or leave raw mode after a crash).
                        try:
                            tty_fd = os.open("/dev/tty", os.O_RDWR | os.O_CLOEXEC)
                            tty_attrs = termios.tcgetattr(tty_fd)
                        except (OSError, termios.error):
                            pass
                        yield
                    except BaseException as exc:
                        # App.suspend() resumes only on normal context exit.
                        # Defer even cancellation until its driver is restored.
                        error = exc
                    finally:
                        if tty_fd is not None:
                            try:
                                if tty_attrs is not None:
                                    termios.tcsetattr(tty_fd, termios.TCSANOW, tty_attrs)
                            except (OSError, termios.error) as exc:
                                self.log_main(f"[WARN] Terminal mode restoration failed: {exc}")
                            finally:
                                with suppress(OSError):
                                    os.close(tty_fd)
                # A nested TUI replaces the alternate screen. Invalidate the
                # whole screen, including regions unchanged in our widget tree.
                self.screen.refresh(repaint=True, layout=True)
            if error is not None:
                raise error.with_traceback(error.__traceback__)

        @staticmethod
        def _interactive_tree_pids(root_pid: int) -> list[int]:
            """Return root PID plus all live descendant PIDs (BFS over /proc).

            Pure snapshot, no signaling. Used instead of process-group
            signaling because the interactive child intentionally shares the
            updater's session/process group, where killpg() is unsafe.
            """
            try:
                children: dict[int, list[int]] = {}
                for entry in os.listdir("/proc"):
                    if not entry.isdigit():
                        continue
                    pid = int(entry)
                    try:
                        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as f:
                            ppid = int(f.read().rsplit(")", 1)[1].split()[1])
                    except (OSError, ValueError, IndexError):
                        continue
                    children.setdefault(ppid, []).append(pid)
                seen = {root_pid}
                order = [root_pid]
                queue = [root_pid]
                while queue:
                    current = queue.pop(0)
                    for child in children.get(current, []):
                        if child not in seen:
                            seen.add(child)
                            order.append(child)
                            queue.append(child)
                return order
            except Exception:
                return [root_pid]

        @staticmethod
        def _interactive_proc_start(pid: int) -> str:
            """Process start time (stat field 22) or '' when unreadable."""
            try:
                with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as f:
                    return f.read().rsplit(")", 1)[1].split()[19]
            except (OSError, ValueError, IndexError):
                return ""

        @staticmethod
        def _interactive_proc_state(pid: int) -> str:
            """Single-letter process state or '' when unreadable."""
            try:
                with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as f:
                    return f.read().rsplit(")", 1)[1].split()[0]
            except (OSError, ValueError, IndexError):
                return ""

        @classmethod
        def _interactive_terminate_tree(cls, root_pid: int) -> None:
            """TERM, then KILL, a PID tree by identity; never killpg().

            Each PID is verified against its recorded start time before every
            signal so recycled PIDs are never touched. Zombie (Z) entries are
            treated as terminated. Best-effort: all errors suppressed.
            """
            try:
                members = {pid: cls._interactive_proc_start(pid) for pid in cls._interactive_tree_pids(root_pid)}
            except Exception:
                return

            def _signal_all(sig: int) -> None:
                for pid, start in members.items():
                    if not start:
                        continue
                    try:
                        if cls._interactive_proc_start(pid) != start:
                            continue
                        os.kill(pid, sig)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass

            def _all_gone() -> bool:
                for pid, start in members.items():
                    if not start:
                        continue
                    try:
                        current = cls._interactive_proc_start(pid)
                    except Exception:
                        continue
                    if current and current == start and cls._interactive_proc_state(pid) != "Z":
                        return False
                return True

            _signal_all(signal.SIGTERM)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if _all_gone():
                    break
                time.sleep(0.1)
            if not _all_gone():
                _signal_all(signal.SIGKILL)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if _all_gone():
                        break
                    time.sleep(0.1)

        @classmethod
        def _interactive_wait_worker(
            cls,
            proc: subprocess.Popen,
            timeout_s: float | None,
            cancel_event: threading.Event,
        ) -> tuple[int | None, str]:
            """Blocking wait in a helper thread; the asyncio loop stays live.

            Returns (rc, disposition) with disposition "done", "timeout", or
            "cancelled". Timeout/cancellation terminate the PID tree (never
            killpg) and reap the direct child before returning.
            """
            deadline = time.monotonic() + timeout_s if timeout_s and timeout_s > 0 else None
            while True:
                try:
                    return proc.wait(timeout=0.2), "done"
                except subprocess.TimeoutExpired:
                    pass
                except Exception:
                    return (proc.returncode if proc.returncode is not None else 1), "done"
                if cancel_event.is_set():
                    with suppress(Exception):
                        cls._interactive_terminate_tree(proc.pid)
                    with suppress(Exception):
                        proc.wait(timeout=5.0)
                    return None, "cancelled"
                if deadline is not None and time.monotonic() >= deadline:
                    with suppress(Exception):
                        cls._interactive_terminate_tree(proc.pid)
                    with suppress(Exception):
                        proc.wait(timeout=5.0)
                    return 124, "timeout"

        def _request_interactive_cancel(self) -> None:
            """Let the wait worker capture descendants before signaling them."""
            event = self._interactive_cancel
            if event is not None:
                event.set()

        async def _execute_task_inner(self, index: int) -> str:
            task = self.tasks[index]

            if task.condition:
                condition_met = await asyncio.to_thread(self.condition_evaluator.check, task.condition)
                if not condition_met:
                    self.log_main(f"[dim]Condition '{task.condition}' false; deferring: {escape(task.name)}[/dim]")
                    task.outcome = "deferred"
                    task.reason = f"condition false: {task.condition}"
                    return "deferred"

            if task.once:
                once_status = await self._safe_once_check(task)
                if once_status == "error":
                    self.add_warning("persistence", "once-state unavailable; once task skipped to avoid unsafe rerun", task=task.name)
                    await self._finalize_task(index, "skipped", "once-state unavailable", 1)
                    return "skipped"
                if once_status == "notify_sealed":
                    msg = f"[bold {THEME['warning']}][WARN][/] Run-once:sealed script modified since last run; not re-run: {escape(task.name)}"
                    self.log_main(msg)
                    self.log_task(msg, index)
                    desktop_notify("Dusky Update", f"Sealed script modified: {task.name}", urgency="normal")
                    if not OPT_DRY_RUN:
                        await self._safe_once_sealed_notified(task)
                    self.update_task_state(index, "skipped")
                    task.outcome = "skipped"
                    task.reason = "once:sealed modified since last run"
                    await self._safe_state_mark(task, "skipped", note="Run-once:sealed modified")
                    return "skipped"
                elif once_status == "skip":
                    self.log_main(f"[dim]Run-once marker valid. Skipping: {escape(task.name)}[/dim]")
                    self.update_task_state(index, "skipped")
                    task.outcome = "skipped"
                    task.reason = "once marker valid"
                    await self._safe_state_mark(task, "skipped", note="Run-once marker valid")
                    return "skipped"

            self.update_task_state(index, "running")

            cmd_str = f"{task.name} {' '.join(task.args)}".strip()
            self.log_main(f"\n[bold {THEME['warning']}]>[/] Executing Process: [bold {THEME['fg']}]{escape(cmd_str)}[/]")
            self.log_task(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] {escape(cmd_str)}\n", index)

            if not (task.resolved_path and task.resolved_path.is_file()):
                self.missing_scripts.append(task.name)
                err = f"[bold {THEME['warning']}][WARN][/] Script missing or conflicting in preflight: {escape(task.name)}"
                self.log_main(err)
                self.log_task(err, index)
                self.update_task_state(index, "skipped")
                await self._safe_state_mark(task, "skipped", note="Script missing or unresolvable")
                self._safe_close_task_log(task, index, "skipped", 1, 0.0)
                task.outcome = "skipped"
                task.reason = "script missing or unresolvable"
                task.exit_code = 1
                task.attempts = 0
                return "skipped"

            resolved_path = task.resolved_path
            try:
                rel_resolved = str(resolved_path.relative_to(WORK_TREE))
            except ValueError:
                rel_resolved = str(resolved_path)
            if GLOBAL_CONFIG["execution"]["validate_subscript_syntax"] and rel_resolved in self.blocked_scripts:
                reason = self.blocked_scripts[rel_resolved]
                self.add_warning("blocked-script", reason, task=task.name, path=rel_resolved)
                await self._finalize_task(index, "skipped", f"blocked script: {reason}", 1)
                return "skipped"
            if task.mode == 'S' and not self.has_sudo:
                reason = "sudo credentials unavailable; privileged task skipped"
                self.add_warning("sudo", reason, task=task.name)
                await self._finalize_task(index, "skipped", reason, 1)
                return "skipped"
            if task.timeout is None or not math.isfinite(float(task.timeout)) or float(task.timeout) <= 0:
                if task.timeout is not None:
                    self.add_warning(
                        "configuration", "non-positive/non-finite task timeout replaced with bounded default",
                        task=task.name,
                    )
                task.timeout = float(
                    GLOBAL_CONFIG["execution"][
                        "interactive_task_timeout" if task.interactive else "default_task_timeout"
                    ]
                )

            interpreter = task.interpreter or []
            exec_cmd = interpreter + [str(resolved_path)] + task.args
            if not interpreter:
                exec_cmd = [str(resolved_path)] + task.args
            if task.mode == 'S':
                exec_cmd = SudoEngine.sudo_prefix() + exec_cmd

            start_t = time.monotonic()
            try:
                if OPT_DRY_RUN:
                    self.log_main(f"[dim][DRY-RUN] Would execute: {escape(' '.join(exec_cmd))}[/dim]")
                    self.log_task(f"[dim][DRY-RUN] Execution bypassed.[/dim]", index)
                    rc = 0
                    task.attempts = 1
                    await asyncio.sleep(0.05)
                elif task.interactive:
                    self.log_main(f"[dim]Suspending UI abstraction... Passing raw terminal control...[/]")
                    self.log_task(f"[dim]Interactive flag detected. Console control delegated to user.[/]", index)

                    # Ordinary synchronous POSIX terminal inheritance under
                    # public App.suspend(): the child keeps stdin/stdout/stderr
                    # and the updater's existing session/process group
                    # unchanged (no new group, no stop-before-exec barrier, no
                    # foreground juggling). Only the blocking wait runs off the
                    # asyncio thread so heartbeat/timeout/cancellation stay
                    # live. Cleanup signals by PID/tree identity, never killpg,
                    # because the group is shared with the updater itself.
                    max_attempts = (task.retry + 1) if task.retry > 0 else 1
                    rc = 1
                    task.attempts = 0
                    for attempt in range(1, max_attempts + 1):
                        if self.abort_flag:
                            break
                        task.attempts = attempt
                        with self._suspend_ui():
                            proc: subprocess.Popen | None = None
                            cancel_event = threading.Event()
                            try:
                                proc = subprocess.Popen(
                                    exec_cmd,
                                    cwd=str(WORK_TREE),
                                    stdin=None,
                                    stdout=None,
                                    stderr=None,
                                    close_fds=True,
                                    restore_signals=True,
                                    start_new_session=False,
                                )
                            except OSError as e:
                                self.log_task(f"[bold {THEME['error']}]Interactive spawn failed: {escape(str(e))}[/]", index)
                                rc = 1
                                break
                            self.active_child_pid = proc.pid
                            self.active_child_group = False
                            self._interactive_proc = proc
                            self._interactive_cancel = cancel_event
                            try:
                                timeout_s = float(task.timeout) if task.timeout else None
                                worker_task = asyncio.create_task(
                                    asyncio.to_thread(
                                        self._interactive_wait_worker,
                                        proc,
                                        timeout_s,
                                        cancel_event,
                                    ),
                                    name=f"interactive-wait-{proc.pid}",
                                )
                                try:
                                    rc, disposition = await asyncio.shield(worker_task)
                                except asyncio.CancelledError:
                                    cancel_event.set()
                                    with suppress(Exception):
                                        rc, disposition = await worker_task
                                    raise
                                if disposition == "cancelled":
                                    raise asyncio.CancelledError
                                if disposition == "timeout":
                                    rc = 124
                                    self.log_task(f"[bold {THEME['warning']}]Interactive task timed out.[/]", index)
                            except asyncio.CancelledError:
                                raise
                            except Exception as e:
                                self.log_task(f"[bold {THEME['error']}]Interactive execution failed: {escape(str(e))}[/]", index)
                                rc = 1
                            finally:
                                self.active_child_pid = None
                                self.active_child_group = False
                                self._interactive_proc = None
                                self._interactive_cancel = None
                        if rc == 0 or self.abort_flag:
                            break
                        if attempt < max_attempts:
                            reason = "Timeout (124)" if rc == 124 else f"Code {rc}"
                            self.log_task(f"[bold {THEME['warning']}]Attempt {attempt} failed ({reason}). Retrying in {task.retry_delay}s...[/]", index)
                            await asyncio.sleep(task.retry_delay)

                    self.log_task(f"\n[bold {THEME['success']}]Terminal control returned. Exit Code: {rc}[/]", index)

                else:
                    max_attempts = (task.retry + 1) if task.retry > 0 else 1
                    task.attempts = 0
                    for attempt in range(1, max_attempts + 1):
                        task.attempts = attempt
                        success, rc = await self.execute_pty_command(
                            exec_cmd,
                            timeout=task.timeout if task.timeout else 0.0,
                            task_index=index
                        )
                        if rc is None:
                            rc = 1
                            break

                        if rc == 0 or self.abort_flag:
                            break

                        if attempt < max_attempts:
                            reason = "Timeout (124)" if rc == 124 else f"Code {rc}"
                            self.log_task(f"[bold {THEME['warning']}]Attempt {attempt} failed ({reason}). Retrying in {task.retry_delay}s...[/]", index)
                            await asyncio.sleep(task.retry_delay)

                duration = time.monotonic() - start_t
                task.duration = duration

                task.exit_code = rc
                if rc == 0:
                    task.outcome = "completed"
                    task.reason = "dry-run" if OPT_DRY_RUN else ""
                    self.update_task_state(index, "success")
                    await self._safe_state_mark(task, "completed", exit_code=0, duration=duration)
                    if task.once:
                        await self._safe_once_mark_success(task, exit_code=0)
                    self._safe_close_task_log(task, index, "completed", 0, duration)
                    self.log_main(f"[bold {THEME['success']}][OK][/] Process Complete ({duration:.2f}s).")
                    self.log_task(f"\n[bold {THEME['success']}]>>> EXECUTION SUCCESSFUL ({duration:.2f}s)[/]", index)
                    return "completed"
                else:
                    if task.ignore_fail and not OPT_STOP_ON_FAIL:
                        task.outcome = "skipped"
                        task.reason = f"ignored failure (exit {rc})"
                        self.update_task_state(index, "skipped")
                        await self._safe_state_mark(task, "skipped", exit_code=rc, duration=duration)
                        self._safe_close_task_log(task, index, "skipped", rc, duration)
                        self.log_main(f"[bold {THEME['warning']}][WARN][/] Process failure (Code {rc}) suppressed by manifest.")
                        self.log_task(f"\n[bold {THEME['warning']}]>>> EXECUTION FAILED / SUPPRESSED (Code {rc})[/]", index)
                        return "skipped"
                    else:
                        task.outcome = "failed"
                        task.reason = "aborted" if self.abort_flag and OPT_STOP_ON_FAIL else f"exit {rc}"
                        self.update_task_state(index, "failed")
                        await self._safe_state_mark(task, "failed", exit_code=rc, duration=duration)
                        self._safe_close_task_log(task, index, "failed", rc, duration)
                        if OPT_STOP_ON_FAIL:
                            self.log_main(f"[bold {THEME['error']}][FATAL][/] Process aborted execution sequence (Code {rc}).")
                            self.log_task(f"\n[bold {THEME['error']}]>>> FATAL EXECUTION FAILURE (Code {rc})[/]", index)
                            self.abort_flag = True
                        else:
                            self.log_main(f"[bold {THEME['error']}][ERROR][/] Process execution failed (Code {rc}). Continuing sequence...")
                            self.log_task(f"\n[bold {THEME['error']}]>>> EXECUTION FAILED (Code {rc})[/]", index)
                        return "failed"

            except asyncio.CancelledError:
                task.duration = time.monotonic() - start_t
                raise
            except Exception as e:
                duration = time.monotonic() - start_t
                task.duration = duration
                err_msg = f"[bold {THEME['error']}][ERROR][/] Internal Exception: {escape(str(e))}"
                self.log_main(err_msg)
                self.log_task(err_msg, index)
                task.outcome = "failed"
                task.reason = f"internal exception: {e}"
                task.exit_code = 1
                self.update_task_state(index, "failed")
                await self._safe_state_mark(task, "failed", exit_code=1, note=str(e), duration=duration)
                self._safe_close_task_log(task, index, "failed", 1, duration)
                if OPT_STOP_ON_FAIL:
                    self.abort_flag = True
                return "failed"
            finally:
                await asyncio.sleep(0.01)

        def _render_final_overview_block(
            self,
            verdict: str,
            success_count: int,
            fail_count: int,
            skipped_count: int,
            missing_count: int,
            total_duration: float,
        ) -> str:
            sep = S("sep")

            # Honor the caller's verdict; derive display from actual outcomes.
            _verdict_map = {
                "SYNC COMPLETE": (THEME['success'], "SYNC COMPLETE"),
                "SYNC SIMULATED": (THEME['success'], "SYNC SIMULATED"),
                "COMPLETED": (THEME['success'], "SUCCESS"),
                "SYSTEM HALTED": (THEME['error'], "ABORTED"),
                "RESOLUTION FAILED": (THEME['error'], "ABORTED"),
                "ABORTED": (THEME['error'], "ABORTED"),
            }
            if self.abort_flag:
                v_color = THEME['error']
                v_title = "ABORTED"
            elif OPT_DRY_RUN:
                v_color = THEME['success']
                v_title = "DRY-RUN"
            elif verdict in _verdict_map:
                v_color, v_title = _verdict_map[verdict]
                if (missing_count > 0 or fail_count > 0 or bool(self.run_warnings)) and v_title in ("SUCCESS", "SYNC COMPLETE"):
                    v_color = THEME['warning']
                    v_title = "WARNINGS"
            elif missing_count > 0 or fail_count > 0 or self.run_warnings:
                v_color = THEME['warning']
                v_title = "WARNINGS"
            else:
                v_color = THEME['success']
                v_title = "SUCCESS"

            p1_t = self.phase_durations.get("phase1_git", 0.0)
            p15_t = self.phase_durations.get("phase1_5_resolve", 0.0)
            p2_t = self.phase_durations.get("phase2_exec", 0.0)

            profile_tasks = self.tasks[5:]
            exec_tasks = [t for t in profile_tasks if t.status == "success" and getattr(t, "duration", 0) > 0]
            exec_tasks.sort(key=lambda t: t.duration, reverse=True)
            top_slowest = [f"{t.name} ({t.duration:.1f}s)" for t in exec_tasks[:3]]
            slowest_str = ", ".join(top_slowest) if top_slowest else "None"

            g = getattr(self, "git_summary", {})
            git_st = g.get("status", "skipped" if OPT_SKIP_SYNC else "unknown")
            branch = escape(str(g.get("branch") or self.profile.branch))
            before_sha = str(g.get("before_head") or "")[:8]
            after_sha = str(g.get("after_head") or "")[:8]
            commits_behind = str(g.get("commits") or "0")
            commit_list = g.get("commit_list") or []
            files_c = g.get("files_changed") or 0
            col_c = g.get("collisions") or 0
            col_dir = g.get("collision_backup") or ""
            mod_c = g.get("local_mods") or 0
            mod_restored = g.get("local_mods_restored")
            mod_backup = g.get("local_mods_backup") or ""
            full_backup = g.get("full_tracked_backup") or ""

            if OPT_DRY_RUN:
                git_headline = "Bypassed (Dry-run mode)"
            elif OPT_SKIP_SYNC and not OPT_POST_SELF_UPDATE:
                git_headline = "Bypassed via --skip-sync flag"
            elif git_st == "updated":
                arrow = "->" if ASCII_MODE else "➔"
                sha_str = f" ({before_sha} {arrow} {after_sha})" if (before_sha and after_sha) else ""
                extra = " [dim](Self-updated)[/dim]" if OPT_POST_SELF_UPDATE else ""
                git_headline = f"Pulled {commits_behind} commit(s), {files_c} file(s) changed{sha_str}{extra}"
            elif git_st == "up_to_date":
                extra = " [dim](Self-updated)[/dim]" if OPT_POST_SELF_UPDATE else ""
                git_headline = f"Up to date at commit [dim]{after_sha or before_sha or 'HEAD'}[/dim]{extra}"
            elif git_st == "up_to_date_with_mods":
                git_headline = f"Up to date ({mod_c} local modification(s) preserved)"
            elif git_st == "unrelated_reset":
                git_headline = f"Full ancestry recovery reset to [dim]{after_sha}[/dim]"
            elif git_st == "cloned":
                git_headline = "Bare repo cloned and checked out"
            elif git_st in ("unknown", "", None):
                git_headline = "Unknown / not completed (see errors above)"
            else:
                git_headline = f"Unknown git state '{escape(str(git_st))}' (see errors above)"

            modes_in_profile = sorted(list({t.mode for t in profile_tasks})) or ["USER", "SUDO"]
            matrix = {m: {"success": 0, "failed": 0, "skipped": 0, "missing": 0} for m in modes_in_profile}
            for task in profile_tasks:
                m = task.mode
                if m not in matrix:
                    matrix[m] = {"success": 0, "failed": 0, "skipped": 0, "missing": 0}
                if task.path_state == "missing":
                    matrix[m]["missing"] += 1
                elif task.status == "success":
                    matrix[m]["success"] += 1
                elif task.status == "failed":
                    matrix[m]["failed"] += 1
                elif task.status == "skipped":
                    matrix[m]["skipped"] += 1
                else:
                    matrix[m]["skipped"] += 1

            tot_all = len(profile_tasks)
            tot_succ = sum(matrix[m]["success"] for m in matrix)
            tot_fail = sum(matrix[m]["failed"] for m in matrix)
            tot_skip = sum(matrix[m]["skipped"] for m in matrix)
            tot_miss = sum(matrix[m]["missing"] for m in matrix)

            rule = "=" * 80 if ASCII_MODE else "═" * 80
            report_sym = S("report")
            lines = [
                f"{rule}",
                f" {report_sym} FINAL OVERVIEW {sep} [bold {THEME['fg']}]{escape(self.profile.name)}[/] {sep} Verdict: [bold {v_color}]{v_title}[/]",
                f"{rule}",
                f"",
                f" {S('timing')} TIMING & PERFORMANCE",
                f"   Total Pipeline Duration : [bold {THEME['fg']}]{total_duration:.2f}s[/]",
                f"   • Phase 1 (Git Reconciliation) : {p1_t:.2f}s",
                f"   • Phase 1.5 (Post-Sync Resolve) : {p15_t:.2f}s",
                f"   • Phase 2 (Script Execution)    : {p2_t:.2f}s",
                f"   • Top Bottlenecks               : {slowest_str}",
                f"",
                f" {S('git')} GIT SYNCHRONIZATION",
                f"   Branch           : [bold {THEME['fg']}]{branch}[/]",
                f"   Sync Status      : {git_headline}",
            ]

            if commit_list:
                lines.append("   Recent Commits   :")
                for item in commit_list[:6]:
                    lines.append(f"     - {escape(item)}")

            if col_c > 0:
                lines.append(f"   Work-tree Backup : [bold {THEME['warning']}]{col_c} collision(s) moved aside[/] ({escape(col_dir)})")
            if mod_c > 0:
                if mod_restored is True:
                    st_text = "restored"
                elif mod_restored is False:
                    st_text = "ATTENTION REQUIRED (merge or restore failure; backup preserved)"
                else:
                    st_text = "backed up"
                loc_extra = f" ({escape(mod_backup)})" if mod_backup else ""
                lines.append(f"   Local Tracked    : {mod_c} file(s) ({st_text}){loc_extra}")
            if full_backup:
                lines.append(f"   Full Tree Backup : {escape(full_backup)}")

            if ASCII_MODE:
                box_top = "   +----------+----------+----------+----------+----------+----------+"
                box_head = "   | MODE     | SUCCESS  | FAILED   | SKIPPED  | MISSING  | TOTAL    |"
                box_mid = "   +----------+----------+----------+----------+----------+----------+"
                box_bot = "   +----------+----------+----------+----------+----------+----------+"
                row_fmt = "   | {mode:<8s} |    {s:2d}    |    {f:2d}    |    {sk:2d}    |    {mi:2d}    |    {to:2d}    |"
            else:
                box_top = "   ┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐"
                box_head = "   │ MODE     │ SUCCESS  │ FAILED   │ SKIPPED  │ MISSING  │ TOTAL    │"
                box_mid = "   ├──────────┼──────────┼──────────┼──────────┼──────────┼──────────┤"
                box_bot = "   └──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘"
                row_fmt = "   │ {mode:<8s} │    {s:2d}    │    {f:2d}    │    {sk:2d}    │    {mi:2d}    │    {to:2d}    │"
            lines.extend([
                f"",
                f" {S('matrix')} SCRIPT EXECUTION MATRIX",
                box_top,
                box_head,
                box_mid,
            ])

            for mode_name in sorted(matrix.keys()):
                r = matrix[mode_name]
                tot_row = sum(r.values())
                if ASCII_MODE:
                    lines.append(
                        f"   | {mode_name:<8s} |    {r['success']:2d}    |    {r['failed']:2d}    |    {r['skipped']:2d}    |    {r['missing']:2d}    |    {tot_row:2d}    |"
                    )
                else:
                    lines.append(
                        f"   │ {mode_name:<8s} │    [bold {THEME['success']}]{r['success']:2d}[/]    │    [bold {THEME['error']}]{r['failed']:2d}[/]    │    [dim {THEME['warning']}]{r['skipped']:2d}[/]    │    [dim]{r['missing']:2d}[/]    │    {tot_row:2d}    │"
                    )

            lines.extend([
                box_mid,
                row_fmt.format(mode="TOTAL", s=tot_succ, f=tot_fail, sk=tot_skip, mi=tot_miss, to=tot_all) if ASCII_MODE else
                f"   │ TOTAL    │    [bold {THEME['success']}]{tot_succ:2d}[/]    │    [bold {THEME['error']}]{tot_fail:2d}[/]    │    [dim {THEME['warning']}]{tot_skip:2d}[/]    │    [dim]{tot_miss:2d}[/]    │    {tot_all:2d}    │",
                box_bot,
                f"",
            ])

            failed_tasks = [t for t in profile_tasks if t.status == "failed"]
            skipped_tasks = [t for t in profile_tasks if t.status == "skipped"]
            fail_sym = S("failed")

            if failed_tasks:
                hard_failed = [t for t in failed_tasks if not t.ignore_fail]
                soft_failed = [t for t in failed_tasks if t.ignore_fail]

                if hard_failed:
                    lines.append(f" [bold {THEME['error']}]{fail_sym} HARD FAILED SCRIPTS ({len(hard_failed)}):[/]")
                    for t in hard_failed:
                        status_note = "(Required - Pipeline Aborted)" if self.abort_flag else "(Required - Execution Continued)"
                        lines.append(f"   • [{t.mode}] {escape(t.name)} [bold {THEME['error']}]{status_note}[/]")

                if soft_failed:
                    lines.append(f" [bold {THEME['warning']}]! SOFT FAILED SCRIPTS ({len(soft_failed)}):[/]")
                    for t in soft_failed:
                        lines.append(f"   • [{t.mode}] {escape(t.name)} [dim {THEME['warning']}](Ignored / Allowed to Fail)[/dim]")

                failed_dirs = sorted(list({str(t.resolved_path.parent) for t in failed_tasks if getattr(t, "resolved_path", None)}))
                bullet = "-" if ASCII_MODE else "└─"
                if failed_dirs:
                    lines.append(f"   [dim]Debug locations:[/dim]")
                    for d in failed_dirs:
                        lines.append(f"     {bullet} [dim]{escape(d)}[/dim]")
            else:
                lines.append(f" [dim]{fail_sym} FAILED SCRIPTS   : None[/dim]")

            if skipped_tasks:
                lines.append(f" [bold {THEME['warning']}]- SKIPPED SCRIPTS ({len(skipped_tasks)}):[/]")
                for t in skipped_tasks:
                    reason = t.reason or ("condition false" if t.condition else ("once marker valid" if t.once else ("missing script" if t.path_state == "missing" else "ignored failure")))
                    lines.append(f"   • [{t.mode}] {escape(t.name)} [dim]({escape(reason)})[/dim]")
            else:
                lines.append(f" [dim]- SKIPPED SCRIPTS  : None[/dim]")

            if self.missing_scripts:
                lines.append(f" [bold {THEME['warning']}]? MISSING SCRIPTS ({len(self.missing_scripts)}):[/]")
                for s in self.missing_scripts:
                    lines.append(f"   • {escape(s)}")
            else:
                lines.append(f" [dim]? MISSING SCRIPTS  : None[/dim]")

            lines.extend([
                f"",
                f" {S('preflight')} SYSTEM & PREFLIGHT",
                f"   • Sudo Mode    : {SudoEngine.mode_name()}",
                f"   • User / Home  : {target_user_pw().pw_name} ({user_home()})",
                f"   • Log File     : {LOG_FILE if LOG_FILE else 'Disabled'}",
                f"{rule}",
            ])

            if self.run_warnings:
                lines.extend(["", f" {S('report')} WARNINGS ({len(self.run_warnings)})"] )
                for w in self.run_warnings:
                    detail = w.get("message", "")
                    where = w.get("task") or w.get("path") or ""
                    recovery = w.get("recovery") or ""
                    suffix = f" [{where}]" if where else ""
                    if recovery:
                        suffix += f" recovery={recovery}"
                    lines.append(f"   - {w.get('kind', 'warning')}: {escape(detail)}{escape(suffix)}")

            return "\n".join(lines)

        async def _execute_task(self, index: int) -> str:
            """Isolate the complete lifecycle of one user task.

            Resolution, condition evaluation, execution, and persistence are
            recoverable per-task boundaries. Cancellation is deliberately not
            converted into failure.
            """
            try:
                return await self._execute_task_inner(index)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                task = self.tasks[index]
                self.add_warning("task", f"recoverable lifecycle exception: {e}", task=task.name)
                with suppress(Exception):
                    self.log_main(f"[bold {THEME['error']}][ERROR][/] {escape(task.name)}: {escape(str(e))}")
                with suppress(Exception):
                    await self._finalize_task(index, "failed", f"task lifecycle exception: {e}", 1)
                return "failed"

        async def _finalize_task(self, index: int, status: str, reason: str, exit_code: int | None = None) -> None:
            # Finalize every unexecuted task with an accurate abort reason so
            # no tail is ever left pending: stop-on-fail aborts, sync-only,
            # resolution failure, cancellation, and once skips alike.
            task = self.tasks[index]
            task.outcome = status if status in ("success", "failed", "skipped") else "skipped"
            task.reason = reason
            if exit_code is not None:
                task.exit_code = exit_code
            self.update_task_state(index, task.outcome)
            if index >= 5:
                await self._safe_state_mark(
                    task, task.outcome, exit_code=task.exit_code, note=reason, duration=task.duration
                )
            code = task.exit_code if task.exit_code is not None else (0 if task.outcome != "failed" else 1)
            self._safe_close_task_log(
                task, index, "completed" if task.outcome == "success" else task.outcome, code, task.duration
            )

        def _outcome_counts(self) -> dict[str, int]:
            counts = {"success": 0, "failed": 0, "skipped": 0, "missing": 0}
            for task in self.tasks[5:]:
                if task.path_state == "missing":
                    counts["missing"] += 1
                elif task.status == "success":
                    counts["success"] += 1
                elif task.status == "failed":
                    counts["failed"] += 1
                else:
                    counts["skipped"] += 1
            return counts

        def _git_counts(self) -> dict[str, int]:
            counts = {"success": 0, "failed": 0, "skipped": 0}
            for task in self.tasks[:5]:
                if task.status == "success":
                    counts["success"] += 1
                elif task.status == "failed":
                    counts["failed"] += 1
                else:
                    counts["skipped"] += 1
            return counts

        async def _stop_periodic(self) -> None:
            # Release heartbeat/inhibitor when execution ends (success, failure,
            # or abort) while allowing report browsing afterwards. Helper
            # subprocesses are reaped; stores close later in on_unmount after
            # workers using them have finished.
            hb = getattr(self, "heartbeat_task", None)
            if hb is not None and not hb.done():
                hb.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    async with asyncio.timeout(3.0):
                        await hb
            self.heartbeat_task = None
            inh = getattr(self, "sleep_inhibitor", None)
            if inh is not None:
                with suppress(Exception):
                    inh.close()
                self.sleep_inhibitor = None
            with suppress(Exception):
                AudioNotifier.reap()
            with suppress(Exception):
                DesktopNotifier.reap()

        async def _execute_pipeline_inner(self) -> None:
            p1_start = time.monotonic()
            # Creation/removal-sensitive snapshots: None means missing, so a
            # created or removed file always counts as changed.
            self._self_hash_before = _file_digest_or_none(SCRIPT_PATH)
            prof_fp = getattr(self.profile, "filepath", None) if getattr(self, "profile", None) else None
            self._profile_hash_before = _file_digest_or_none(prof_fp) if prof_fp else None
            cfg_p = global_config_context_path()
            self._config_hash_before = _file_digest_or_none(cfg_p)

            handoff_ok = False
            if OPT_POST_SELF_UPDATE:
                self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 1: Git Architecture Reconciliation (Self-Updated) ═══[/]\n")
                handoff_ok = self._restore_handoff(Path(OPT_HANDOFF) if OPT_HANDOFF else None)
                if not handoff_ok:
                    self.log_main(f"[bold {THEME['warning']}][WARN][/] Restart handoff missing or invalid; running a normal sync instead of trusting skipped tasks.")

            if OPT_POST_SELF_UPDATE and handoff_ok:
                # Valid handoff already applied EXACT saved outcomes above;
                # never execute the generic skip branch (which would overwrite
                # all five with skipped) nor re-run sync. Preserve exact.
                self.log_main("[dim]Preserving exact git outcomes from restart handoff; skipping re-sync.[/dim]")
                self.phase_durations["phase1_git"] += time.monotonic() - p1_start
            elif not OPT_SKIP_SYNC or (OPT_POST_SELF_UPDATE and not handoff_ok):
                if OPT_DRY_RUN:
                    self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 1: Git Architecture Reconciliation (DRY-RUN) ═══[/]\n")
                    self.log_main("[dim]Git synchronization bypassed during dry-run.[/dim]")
                    for index in range(5):
                        self.update_task_state(index, "skipped")
                else:
                    self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 1: Git Architecture Reconciliation ═══[/]\n")
                    git_engine = GitEngine(self, self.profile)
                    if not await git_engine.execute_phase():
                        self.abort_flag = True
                        self.phase_durations["phase1_git"] = time.monotonic() - p1_start
                        self.log_main(f"\n[bold {THEME['error']} blink]SYSTEM HALTED. GIT INTEGRITY VIOLATION.[/]")
                        for index in range(5, len(self.tasks)):
                            await self._finalize_task(index, "skipped", "git sync halted; sequence not executed", 1)
                        if self.run_logger:
                            self.run_logger.write_report(
                                self.profile,
                                self.tasks,
                                {t.state_key: t.status for t in self.tasks},
                                {"success": 0, "failed": 1, "missing": 0, "skipped": len(self.tasks) - 5},
                            )
                        report_block = self._render_final_overview_block(
                            verdict="SYSTEM HALTED",
                            success_count=0,
                            fail_count=1,
                            skipped_count=len(self.tasks) - 5,
                            missing_count=0,
                            total_duration=time.monotonic() - self.run_start_mono,
                        )
                        with suppress(Exception):
                            rw = self.query_one("#log-report", RichLog)
                            rw.clear()
                            rw.write(report_block)
                            self.query_one("#task_list", ListView).index = len(self.tasks) + 1
                            self.query_one("#log_switcher", ContentSwitcher).current = "log-report"
                        self.exit_code = 1
                        await self._stop_periodic()
                        self._show_completion_dialog(
                            "UPDATE HALTED",
                            "Git integrity check failed. The update was stopped to protect your system.\n\nChoose how to continue:",
                            "danger",
                        )
                        return
                    # The independent supervisor publishes a bundle as known-good
                    # only after the restarted candidate reaches its health checkpoint.
                self.phase_durations["phase1_git"] = time.monotonic() - p1_start
            else:
                self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 1: Git Architecture Reconciliation (SKIPPED) ═══[/]\n")
                for index in range(5):
                    self.update_task_state(index, "skipped")
                self.phase_durations["phase1_git"] = 0.0

            # Validate/restart a changed updater bundle before honoring
            # --sync-only completion. A successful synchronization that changed
            # the worker/profile/settings is not complete until the candidate
            # reaches the supervisor-backed startup health checkpoint.
            reexec_outcome = self._maybe_reexec_after_sync()
            if reexec_outcome == "restart":
                return

            if OPT_SYNC_ONLY and reexec_outcome != "invalid":
                msg = "SYNC SIMULATED." if OPT_DRY_RUN else "SYNC COMPLETE."
                self.log_main(f"\n[bold {THEME['success']}]{msg} (--sync-only specified)[/]")
                for index in range(5, len(self.tasks)):
                    await self._finalize_task(index, "skipped", "sync-only run; sequence not executed", 0)
                git_counts = self._git_counts()
                user_counts = self._outcome_counts()
                if self.run_logger:
                    self.run_logger.write_report(
                        self.profile,
                        self.tasks,
                        {t.state_key: t.status for t in self.tasks},
                        {"success": git_counts["success"], "failed": git_counts["failed"],
                         "missing": user_counts["missing"], "skipped": git_counts["skipped"] + user_counts["skipped"]},
                    )
                report_block = self._render_final_overview_block(
                    verdict="SYNC COMPLETE" if not OPT_DRY_RUN else "SYNC SIMULATED",
                    success_count=git_counts["success"],
                    fail_count=git_counts["failed"],
                    skipped_count=git_counts["skipped"] + user_counts["skipped"],
                    missing_count=user_counts["missing"],
                    total_duration=time.monotonic() - self.run_start_mono,
                )
                self.exit_code = 0 if git_counts["failed"] == 0 else 1
                await self._stop_periodic()
                with suppress(Exception):
                    rw = self.query_one("#log-report", RichLog)
                    rw.clear()
                    rw.write(report_block)
                    self.query_one("#task_list", ListView).index = len(self.tasks) + 1
                    self.query_one("#log_switcher", ContentSwitcher).current = "log-report"
                self._show_completion_dialog(
                    "SYNC COMPLETE" if not OPT_DRY_RUN else "SYNC SIMULATED",
                    "Dotfile synchronization finished.\n\nChoose how to continue:",
                    "success",
                )
                return

            if reexec_outcome == "invalid":
                # Invalid updated code/profile/settings: halt, never execute
                # stale plan. Finalize all user tails as failed, preserve
                # diagnostic/recovery data (git_summary, run_logger report),
                # nonzero exit, report browsing preserved via dialog.
                self.abort_flag = True
                self.phase_durations["phase1_5_resolve"] = 0.0
                self.phase_durations["phase2_exec"] = 0.0
                for index in range(5, len(self.tasks)):
                    try:
                        if self.tasks[index].status in ("pending", "running"):
                            await self._finalize_task(index, "failed", "updated profile/settings invalid; stale plan halted", 1)
                        elif self.tasks[index].status not in ("success", "failed", "skipped"):
                            await self._finalize_task(index, "failed", "updated profile/settings invalid; stale plan halted", 1)
                    except Exception:
                        pass
                # Preserve git/diagnostic data: write report with actual counts.
                try:
                    if self.run_logger:
                        counts = self._outcome_counts()
                        # _outcome_counts covers user tasks; git counts separate for report.
                        git_counts = self._git_counts()
                        self.run_logger.write_report(
                            self.profile,
                            self.tasks,
                            {t.state_key: t.status for t in self.tasks},
                            {"success": counts["success"], "failed": counts["failed"] + 1,
                             "missing": counts["missing"], "skipped": counts["skipped"]},
                        )
                except Exception:
                    pass
                try:
                    counts = self._outcome_counts()
                    report_block = self._render_final_overview_block(
                        verdict="ABORTED",
                        success_count=counts["success"],
                        fail_count=counts["failed"] + 1,
                        skipped_count=counts["skipped"],
                        missing_count=counts["missing"],
                        total_duration=time.monotonic() - self.run_start_mono,
                    )
                except Exception:
                    report_block = "Update halted: invalid updated profile/settings."
                with suppress(Exception):
                    rw = self.query_one("#log-report", RichLog)
                    rw.clear()
                    rw.write(report_block)
                    self.query_one("#task_list", ListView).index = len(self.tasks) + 1
                    self.query_one("#log_switcher", ContentSwitcher).current = "log-report"
                try:
                    self._log_lines["report"] = deque([self._canonical_plain(report_block)], maxlen=6000)
                except Exception:
                    pass
                self.exit_code = 1
                await self._stop_periodic()
                self._show_completion_dialog(
                    "UPDATE HALTED",
                    "Updated profile/settings failed validation after sync. Stale plan was NOT executed.\n\nChoose how to continue:",
                    "danger",
                )
                return

            p15_start = time.monotonic()
            self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 1.5: Post-Sync Script Resolution ═══[/]\n")
            resolve_ok = await asyncio.to_thread(resolve_and_validate_manifest, self.profile, self.tasks, False)
            if not resolve_ok:
                self.abort_flag = True
                self.phase_durations["phase1_5_resolve"] = time.monotonic() - p15_start
                self.log_main(f"[bold {THEME['error']}][FATAL][/] Post-sync script resolution failed. Cannot proceed.")
                for index in range(5, len(self.tasks)):
                    if self.tasks[index].status in ("pending", "running"):
                        await self._finalize_task(index, "skipped", "post-sync resolution failed", 1)
                if self.run_logger:
                    self.run_logger.write_report(
                        self.profile,
                        self.tasks,
                        {t.state_key: t.status for t in self.tasks},
                        {"success": 0, "failed": 1, "missing": len(self.missing_scripts), "skipped": len(self.tasks) - 5},
                    )
                report_block = self._render_final_overview_block(
                    verdict="RESOLUTION FAILED",
                    success_count=0,
                    fail_count=1,
                    skipped_count=len(self.tasks) - 5,
                    missing_count=len(self.missing_scripts),
                    total_duration=time.monotonic() - self.run_start_mono,
                )
                with suppress(Exception):
                    rw = self.query_one("#log-report", RichLog)
                    rw.clear()
                    rw.write(report_block)
                    self.query_one("#task_list", ListView).index = len(self.tasks) + 1
                    self.query_one("#log_switcher", ContentSwitcher).current = "log-report"
                self.exit_code = 1
                await self._stop_periodic()
                self._show_completion_dialog(
                    "UPDATE HALTED",
                    "Post-sync script resolution failed. The update was stopped to protect your system.\n\nChoose how to continue:",
                    "danger",
                )
                return
            self.phase_durations["phase1_5_resolve"] = time.monotonic() - p15_start

            try:
                            p2_start = time.monotonic()
                            self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 2: Configuration Pipeline Execution ═══[/]\n")

                            success_count, fail_count = 0, 0
                            deferred_indices: list[int] = []

                            for index in range(5, len(self.tasks)):
                                if self.abort_flag:
                                    await self._finalize_task(index, "skipped", "aborted before execution", 130)
                                    continue

                                outcome = await self._execute_task(index)
                                if outcome == "deferred":
                                    deferred_indices.append(index)
                                elif outcome == "completed":
                                    success_count += 1
                                elif outcome == "failed":
                                    fail_count += 1

                            if deferred_indices:
                                max_defer_passes = max(1, int(GLOBAL_CONFIG.get("execution", {}).get("max_defer_passes", 3)))
                                remaining: list[int] = list(deferred_indices)
                                for pass_no in range(1, max_defer_passes + 1):
                                    if self.abort_flag:
                                        break
                                    progressed = False
                                    next_remaining: list[int] = []
                                    for pos, index in enumerate(remaining):
                                        if self.abort_flag:
                                            # Current and rest are the unvisited tail.
                                            next_remaining.extend(remaining[pos:])
                                            break
                                        outcome = await self._execute_task(index)
                                        if outcome == "deferred":
                                            next_remaining.append(index)
                                        else:
                                            progressed = True
                                            if outcome == "completed":
                                                success_count += 1
                                            elif outcome == "failed":
                                                fail_count += 1
                                        if self.abort_flag:
                                            # Stop-on-fail tripped mid-pass: indices after the
                                            # current one were never visited.
                                            unvisited = remaining[pos + 1:]
                                            next_remaining.extend(u for u in unvisited if u not in next_remaining)
                                            break
                                    remaining = next_remaining
                                    if not remaining or self.abort_flag:
                                        break
                                    if not progressed:
                                        break

                                for index in remaining:
                                    task = self.tasks[index]
                                    if self.abort_flag:
                                        await self._finalize_task(index, "skipped", "aborted during deferred passes", 130)
                                    else:
                                        await self._finalize_task(index, "skipped", f"condition never met: {task.condition}", 0)
                                        self.log_main(f"[dim]Condition '{task.condition}' never satisfied; skipping: {escape(task.name)}[/dim]")

                            self.phase_durations["phase2_exec"] = time.monotonic() - p2_start
                            total_duration = time.monotonic() - self.run_start_mono
                            end_counts = self._outcome_counts()
                            skipped_count = end_counts["skipped"]
                            missing_count = end_counts["missing"]
                            success_count = end_counts["success"]
                            fail_count = end_counts["failed"]

                            if self.run_logger:
                                self.run_logger.write_report(
                                    self.profile,
                                    self.tasks,
                                    {t.state_key: t.status for t in self.tasks},
                                    {"success": success_count, "failed": fail_count, "missing": missing_count, "skipped": skipped_count},
                                )

                            # Generate & Write Final Report Block
                            report_block = self._render_final_overview_block(
                                verdict="ABORTED" if self.abort_flag else ("DRY-RUN" if OPT_DRY_RUN else "COMPLETED"),
                                success_count=success_count,
                                fail_count=fail_count,
                                skipped_count=skipped_count,
                                missing_count=missing_count,
                                total_duration=total_duration,
                            )

                            with suppress(Exception):
                                rw = self.query_one("#log-report", RichLog)
                                rw.clear()
                                rw.write(report_block)

                            self._log_lines["report"] = deque([self._canonical_plain(report_block)], maxlen=6000)
                            self.log_main(f"\n{report_block}\n")

                            # Auto-switch sidebar highlight to Report item in the background
                            report_idx = len(self.tasks) + 1
                            with suppress(Exception):
                                list_view = self.query_one("#task_list", ListView)
                                list_view.index = report_idx
                                self.query_one("#log_switcher", ContentSwitcher).current = "log-report"

                            if self.abort_flag:
                                desktop_notify("Dusky Update", f"{fail_count} required script(s) failed", urgency="critical")
                                AudioNotifier.play("alert")
                            elif OPT_DRY_RUN:
                                desktop_notify("Dusky Update", "Dry-run completed successfully", urgency="normal")
                                AudioNotifier.play("info")
                            elif self.missing_scripts or fail_count > 0 or self.run_warnings:
                                details = []
                                if fail_count > 0:
                                    details.append(f"{fail_count} script(s) failed")
                                if self.missing_scripts:
                                    details.append(f"{missing_count} script(s) missing")
                                if self.run_warnings:
                                    details.append(f"{len(self.run_warnings)} warning(s)")
                                desktop_notify("Dusky Update", ", ".join(details), urgency="normal")
                                AudioNotifier.play("info")
                            else:
                                desktop_notify("Dusky updated", "", urgency="normal")
                                AudioNotifier.play("complete")

                            self.log_main("\n[dim]Press 'Q' (or Ctrl+Q) to quit; Ctrl+C reaches the running child first.[/dim]")

                            summary_lines = [
                                f"Successful: {success_count}",
                                f"Failed: {fail_count}",
                            ]
                            if self.missing_scripts:
                                summary_lines.append(f"Missing: {missing_count}")

                            if self.abort_flag:
                                dialog_title, dialog_level = "UPDATE ABORTED", "danger"
                            elif OPT_DRY_RUN:
                                dialog_title, dialog_level = "DRY-RUN COMPLETE", "success"
                            elif self.missing_scripts or fail_count > 0 or self.run_warnings:
                                dialog_title, dialog_level = "dusky updated with warnings", "warning"
                            else:
                                dialog_title, dialog_level = "dusky updated", "success"

                            self.exit_code = 0 if (fail_count == 0 and not self.abort_flag) else 1
                            await self._stop_periodic()
                            self._show_completion_dialog(
                                dialog_title,
                                "\n".join(summary_lines) + "\n\nChoose how to continue:",
                                dialog_level,
                            )

            except asyncio.CancelledError:
                # Cancellation (quit/restart): terminate the active child
                # group (TERM then KILL, bounded), finalize every
                # non-terminal task, write the report, and close out quietly
                # so the app can shut down. Covers Git, resolution,
                # interactive, and normal execution; shutdown stays bounded
                # and stores/logs close later in on_unmount after workers.
                pid = getattr(self, "active_child_pid", None)
                if pid is not None and not getattr(self, "active_child_group", False) and getattr(self, "_interactive_proc", None) is not None:
                    # Shared-group interactive child shares our process group:
                    # signal by PID only, never killpg (that would hit us too).
                    self._request_interactive_cancel()
                elif pid is not None:
                    try:
                        if _pg_has_owned_member(pid):
                            with suppress(ProcessLookupError, PermissionError, OSError):
                                os.killpg(pid, signal.SIGTERM)
                            try:
                                async with asyncio.timeout(3.0):
                                    while _pg_has_owned_member(pid):
                                        await asyncio.sleep(0.1)
                            except (TimeoutError, asyncio.TimeoutError):
                                pass
                            if _pg_has_owned_member(pid):
                                with suppress(ProcessLookupError, PermissionError, OSError):
                                    os.killpg(pid, signal.SIGKILL)
                    except Exception:
                        pass
                for index in range(5, len(self.tasks)):
                    if self.tasks[index].status in ("pending", "running"):
                        await self._finalize_task(index, "skipped", "cancelled", 130)
                self.abort_flag = True
                if self.exit_code == 0:
                    self.exit_code = 130
                await self._stop_periodic()
                if self.run_logger:
                    counts = self._outcome_counts()
                    self.run_logger.write_report(
                        self.profile,
                        self.tasks,
                        {t.state_key: t.status for t in self.tasks},
                        {"success": counts["success"], "failed": counts["failed"],
                         "missing": counts["missing"], "skipped": counts["skipped"]},
                    )
                return
            except Exception as e:
                # Unexpected exception: finalize consistently, never leave
                # tails pending or report success. Missing/failed work is
                # surfaced as WARNINGS/ABORTED, not silent success.
                try:
                    self.log_main(f"[bold {THEME['error']}][FATAL][/] Unexpected pipeline error: {escape(str(e))}")
                except Exception:
                    pass
                for index in range(5, len(self.tasks)):
                    try:
                        if self.tasks[index].status in ("pending", "running"):
                            await self._finalize_task(index, "failed", f"unexpected error: {e}", 1)
                    except Exception:
                        pass
                self.abort_flag = True
                self.exit_code = 1
                try:
                    await self._stop_periodic()
                except Exception:
                    pass
                try:
                    if self.run_logger:
                        counts = self._outcome_counts()
                        self.run_logger.write_report(
                            self.profile,
                            self.tasks,
                            {t.state_key: t.status for t in self.tasks},
                            {"success": counts["success"], "failed": counts["failed"] + 1,
                             "missing": counts["missing"], "skipped": counts["skipped"]},
                        )
                except Exception:
                    pass
                try:
                    with suppress(Exception):
                        desktop_notify("Dusky Update", f"Pipeline error: {e}", urgency="critical")
                    with suppress(Exception):
                        AudioNotifier.play("alert")
                except Exception:
                    pass
                return

        async def execute_pipeline(self) -> None:
            # Whole-pipeline finalization: covers Git, post-sync resolution,
            # and reexec/invalid paths (not just phase 2). Inner already
            # finalizes its own early returns; this outer layer catches
            # unexpected exceptions/cancellation from Git/resolution/reexec
            # so no tail is left pending and no success is reported silently.
            # Report browsing is preserved (report written, dialog shown where
            # possible); the actual final result (exit_code) is preserved on
            # quit (130 for mid-run cancellation, otherwise inner's code).
            try:
                await self._execute_pipeline_inner()
            except asyncio.CancelledError:
                # Cancellation during Git/resolution/reexec (phase 2 has its
                # own handler; this covers the rest). Bounded child shutdown,
                # finalize all pending (including Git tasks 0-4 when still
                # pending), write report, exit 130. Stores close in on_unmount.
                pid = getattr(self, "active_child_pid", None)
                if pid is not None and not getattr(self, "active_child_group", False) and getattr(self, "_interactive_proc", None) is not None:
                    # Shared-group interactive child shares our process group:
                    # signal by PID only, never killpg (that would hit us too).
                    self._request_interactive_cancel()
                elif pid is not None:
                    try:
                        if _pg_has_owned_member(pid):
                            with suppress(ProcessLookupError, PermissionError, OSError):
                                os.killpg(pid, signal.SIGTERM)
                            try:
                                async with asyncio.timeout(3.0):
                                    while _pg_has_owned_member(pid):
                                        await asyncio.sleep(0.1)
                            except (TimeoutError, asyncio.TimeoutError):
                                pass
                            if _pg_has_owned_member(pid):
                                with suppress(ProcessLookupError, PermissionError, OSError):
                                    os.killpg(pid, signal.SIGKILL)
                    except Exception:
                        pass
                # Finalize Git tasks (0-4) when still pending/running, then user tails.
                for index in range(0, len(self.tasks)):
                    try:
                        if getattr(self.tasks[index], "status", "pending") in ("pending", "running"):
                            if index < 5:
                                # Git phase never ran to completion: mark skipped/cancelled.
                                await self._finalize_task(index, "skipped", "cancelled during sync", 130)
                            else:
                                await self._finalize_task(index, "skipped", "cancelled", 130)
                    except Exception:
                        pass
                # Preserve inner's final result when already set to non-zero?
                # Mid-run cancellation is 130; if inner already set 1 (e.g.,
                # invalid halt) and cancellation races, keep 130 as the quit
                # signal but never reset to 0.
                try:
                    if getattr(self, "exit_code", 0) == 0:
                        self.exit_code = 130
                except Exception:
                    self.exit_code = 130
                self.abort_flag = True
                try:
                    await self._stop_periodic()
                except Exception:
                    pass
                try:
                    if self.run_logger:
                        counts = self._outcome_counts()
                        git_counts = self._git_counts()
                        self.run_logger.write_report(
                            self.profile,
                            self.tasks,
                            {t.state_key: t.status for t in self.tasks},
                            {"success": counts["success"] + git_counts["success"],
                             "failed": counts["failed"] + git_counts["failed"],
                             "missing": counts["missing"], "skipped": counts["skipped"] + git_counts["skipped"]},
                        )
                except Exception:
                    pass
                return
            except Exception as e:
                try:
                    self.log_main(f"[bold {THEME['error']}][FATAL][/] Unexpected pipeline error (outer): {escape(str(e))}")
                except Exception:
                    pass
                for index in range(0, len(self.tasks)):
                    try:
                        if getattr(self.tasks[index], "status", "pending") in ("pending", "running"):
                            await self._finalize_task(index, "failed", f"unexpected error: {e}", 1)
                    except Exception:
                        pass
                self.abort_flag = True
                # Preserve existing nonzero exit; never downgrade to success.
                try:
                    if getattr(self, "exit_code", 0) == 0:
                        self.exit_code = 1
                except Exception:
                    self.exit_code = 1
                try:
                    await self._stop_periodic()
                except Exception:
                    pass
                try:
                    if self.run_logger:
                        counts = self._outcome_counts()
                        self.run_logger.write_report(
                            self.profile,
                            self.tasks,
                            {t.state_key: t.status for t in self.tasks},
                            {"success": counts["success"], "failed": counts["failed"] + 1,
                             "missing": counts["missing"], "skipped": counts["skipped"]},
                        )
                except Exception:
                    pass
                try:
                    with suppress(Exception):
                        desktop_notify("Dusky Update", f"Pipeline error: {e}", urgency="critical")
                    with suppress(Exception):
                        AudioNotifier.play("alert")
                except Exception:
                    pass
                return
            finally:
                if self._restart_handoff is None:
                    await self._finish_update_session()
                self.pipeline_finished = True
                completion = getattr(self, "_completion_dialog", None)
                if completion is not None and self.is_running:
                    title, message, level = completion
                    self.push_screen(
                        CompletionDialog(title=title, message=message, level=level),
                        self._on_completion_reply,
                    )

        async def _finish_update_session(self) -> None:
            """End mutation ownership while keeping the completed report visible."""
            await self._stop_periodic()
            # Join cleanup before a new updater can create/use shared state.
            await asyncio.to_thread(SudoEngine.cleanup)
            for store in (self.state_store, self.once_store):
                if store is not None:
                    store.close()
            self.state_store = None
            self.once_store = None
            # The supervisor may still need rollback/publication ownership.
            # Its acknowledgement proves that work is complete before unlock.
            await asyncio.to_thread(_supervisor_finish_checkpoint)
            release_lock()

        def action_open_search(self) -> None:
            if isinstance(self.screen, ModalScreen):
                return

            def on_search_selected(task_idx: int | None) -> None:
                if task_idx is None:
                    return
                list_view = self.query_one("#task_list", ListView)
                target_pos = task_idx + 1
                if 0 <= target_pos < len(list_view.children):
                    list_view.index = target_pos

            self.push_screen(TaskSearchScreen(self.tasks), on_search_selected)

        def action_search_log(self) -> None:
            if isinstance(self.screen, ModalScreen):
                return

            list_view = self.query_one("#task_list", ListView)
            current_idx = list_view.index
            key: int | str = "main"
            title = "Main Core Log"

            if current_idx is not None and current_idx > 0:
                if current_idx == len(self.tasks) + 1:
                    key = "report"
                    title = "Final Run Overview Report"
                elif (current_idx - 1) < len(self.tasks):
                    task_idx = current_idx - 1
                    key = task_idx
                    title = self.tasks[task_idx].name

            lines = list(self._log_lines.get(key, deque()))
            self.push_screen(LogSearchScreen(title, lines))

        def _update_header_state(self) -> None:
            # Visible active filter/child-input state in the header.
            with suppress(Exception):
                title = self.query_one("#header_title", Static)
                bits = []
                if self.filter_mode != "all":
                    bits.append(f"filter:{self.filter_mode}")
                if self.child_input_mode:
                    bits.append("child-input")
                suffix = f" [{' '.join(bits)}]" if bits else ""
                title.update(f"{S('logo')} DUSKY UPDATER{suffix}")

        def action_cycle_filter(self) -> None:
            if isinstance(self.screen, ModalScreen):
                return

            filters = ["all", "pending", "running", "success", "failed", "skipped"]
            idx = filters.index(self.filter_mode) if self.filter_mode in filters else 0
            self.filter_mode = filters[(idx + 1) % len(filters)]
            self._apply_filter()
            self._update_header_state()

            self.log_main(f"[dim]Task filter set to: [bold]{self.filter_mode}[/bold][/dim]")

        def action_toggle_follow(self) -> None:
            self.follow_mode = not self.follow_mode
            self._update_header_state()
            self.log_main(f"[dim]Follow mode {'on' if self.follow_mode else 'off'}.[/dim]")

        def action_toggle_child_input(self) -> None:
            if getattr(self, "current_pty_master", None) is None:
                self.log_main("[dim]No active child session; child-input mode unchanged.[/dim]")
                return
            self.child_input_mode = not self.child_input_mode
            self._update_header_state()
            state = "on (all keys go to child; Ctrl+O exits)" if self.child_input_mode else "off"
            self.log_main(f"[dim]Child-input mode {state}.[/dim]")

        def _set_pane_widths(self, width_pct: int) -> None:
            min_w = GLOBAL_CONFIG.get("ui", {}).get("min_left_pane_width", 15)
            max_w = GLOBAL_CONFIG.get("ui", {}).get("max_left_pane_width", 80)
            try:
                min_w = int(min_w)
                max_w = int(max_w)
            except (TypeError, ValueError):
                min_w, max_w = 15, 80
            self.sidebar_width = max(min_w, min(max_w, width_pct))
            with suppress(Exception):
                self.query_one("#sidebar").styles.width = f"{self.sidebar_width}%"
                self.query_one("#log_container").styles.width = f"{100 - self.sidebar_width}%"
            # Resize PTYs on sidebar changes as well as terminal resizes.
            if getattr(self, "current_pty_master", None) is not None:
                with suppress(OSError, ValueError):
                    size = os.get_terminal_size()
                    actual_cols = max(20, int(size.columns * (1 - (self.sidebar_width / 100))) - 2)
                    winsize = struct.pack("HHHH", size.lines, actual_cols, 0, 0)
                    fcntl.ioctl(self.current_pty_master, termios.TIOCSWINSZ, winsize)
        def _update_pane_width_from_mouse(self, mouse_screen_x: int) -> None:
            with suppress(Exception):
                screen_w = self.size.width
                if screen_w > 0:
                    pct = int(mouse_screen_x * 100 / screen_w)
                    self._set_pane_widths(pct)

        def action_shrink_left_pane(self) -> None:
            self._set_pane_widths(self.sidebar_width - 4)

        def action_expand_left_pane(self) -> None:
            self._set_pane_widths(self.sidebar_width + 4)

        def _get_active_visible_log(self) -> RichLog | None:
            with suppress(Exception):
                switcher = self.query_one("#log_switcher", ContentSwitcher)
                if switcher.current:
                    return self.query_one(f"#{switcher.current}", RichLog)
            with suppress(Exception):
                return self.query_one("#log-main", RichLog)
            return None

        def action_tree_down(self) -> None:
            self.follow_mode = False
            self._update_header_state()
            with suppress(Exception):
                self.query_one("#task_list", ListView).action_cursor_down()

        def action_tree_up(self) -> None:
            self.follow_mode = False
            self._update_header_state()
            with suppress(Exception):
                self.query_one("#task_list", ListView).action_cursor_up()

        def action_scroll_preview_up(self) -> None:
            with suppress(Exception):
                if log_w := self._get_active_visible_log():
                    log_w.scroll_up(animate=False)

        def action_scroll_preview_down(self) -> None:
            with suppress(Exception):
                if log_w := self._get_active_visible_log():
                    log_w.scroll_down(animate=False)

        def action_scroll_preview_page_up(self) -> None:
            with suppress(Exception):
                if log_w := self._get_active_visible_log():
                    log_w.scroll_page_up(animate=False)

        def action_scroll_preview_page_down(self) -> None:
            with suppress(Exception):
                if log_w := self._get_active_visible_log():
                    log_w.scroll_page_down(animate=False)

        def action_scroll_preview_home(self) -> None:
            with suppress(Exception):
                if log_w := self._get_active_visible_log():
                    log_w.scroll_home(animate=False)

        def action_scroll_preview_end(self) -> None:
            with suppress(Exception):
                if log_w := self._get_active_visible_log():
                    log_w.scroll_end(animate=False)

        def action_toggle_focus(self) -> None:
            with suppress(Exception):
                task_list = self.query_one("#task_list", ListView)
                log_w = self._get_active_visible_log()
                if self.focused == log_w:
                    task_list.focus()
                else:
                    if log_w:
                        log_w.focus()

        def on_mouse_down(self, event: events.MouseDown) -> None:
            if isinstance(self.screen, ModalScreen):
                return
            with suppress(Exception):
                sidebar = self.query_one("#sidebar")
                sidebar_x = sidebar.region.x + sidebar.region.width
                if abs(event.screen_x - sidebar_x) <= 4:
                    self._is_dragging_pane = True
                    self._update_pane_width_from_mouse(event.screen_x)

        def on_mouse_move(self, event: events.MouseMove) -> None:
            if getattr(self, "_is_dragging_pane", False):
                if event.button == 0:
                    self._is_dragging_pane = False
                else:
                    self._update_pane_width_from_mouse(event.screen_x)

        def on_mouse_up(self, event: events.MouseUp) -> None:
            self._is_dragging_pane = False

        def action_request_quit(self) -> None:
            if isinstance(self.screen, HelpScreen):
                self.screen.dismiss(None)
                return

            if isinstance(self.screen, (TaskSearchScreen, LogSearchScreen)):
                self.screen.dismiss(None)
                return

            if isinstance(self.screen, ConfirmQuitScreen):
                self.screen.dismiss("abort")
                return

            if isinstance(self.screen, CompletionDialog):
                self.screen.dismiss(True)
                return

            if getattr(self, "pipeline_finished", False):
                self.action_quit()
                return

            def on_quit_decision(result: str | None) -> None:
                if result == "abort":
                    self.log_main("[FATAL] User requested sequence termination.")
                    self.action_quit()

            self.push_screen(ConfirmQuitScreen(), on_quit_decision)

        def action_help(self) -> None:
            if isinstance(self.screen, HelpScreen):
                self.screen.dismiss(None)
                return
            if isinstance(self.screen, ModalScreen):
                return
            self.push_screen(HelpScreen())

        def on_resize(self, event: events.Resize) -> None:
            if getattr(self, "current_pty_master", None) is not None:
                with suppress(OSError, ValueError):
                    sidebar_percent = self.sidebar_width
                    actual_cols = max(20, int(event.size.width * (1 - (sidebar_percent / 100))) - 2)
                    winsize = struct.pack("HHHH", event.size.height, actual_cols, 0, 0)
                    fcntl.ioctl(self.current_pty_master, termios.TIOCSWINSZ, winsize)

        def on_key(self, event: events.Key) -> None:
            if isinstance(self.screen, ModalScreen):
                return

            # Reserved escape first: Ctrl+O always leaves child-input mode.
            if event.key == "ctrl+o":
                if getattr(self, "child_input_mode", False):
                    self.child_input_mode = False
                    self._update_header_state()
                    self.log_main("[dim]Child-input mode off.[/dim]")
                    event.stop()
                return

            # Never hijack text entry: focused Inputs receive keys first
            # (bindings above are non-priority for text keys).
            try:
                from textual.widgets import Input as _Input
                if isinstance(self.focused, _Input):
                    return
            except Exception:
                pass

            if getattr(self, "current_pty_master", None) is not None:
                if getattr(self, "child_input_mode", False):
                    # Explicit child-input mode: everything except the
                    # reserved Ctrl+O/Ctrl+Q goes to the child.
                    if event.key == "ctrl+q":
                        self.log_main("[FATAL] Emergency abort requested from PTY session.")
                        self.action_quit()
                        event.stop()
                        return
                    data = self._pty_key_bytes(event)
                    if data:
                        self._cancel_prompt_wait_notice()
                        self._queue_pty_write(data)
                        event.stop()
                    return
                if event.key == "ctrl+f":
                    self.action_open_search()
                    event.stop()
                    return

                if event.key == "ctrl+l":
                    self.action_search_log()
                    event.stop()
                    return

                if event.key == "ctrl+q":
                    self.log_main("[FATAL] Emergency abort requested from PTY session.")
                    self.action_quit()
                    event.stop()
                    return

                if event.key in (
                    "pageup", "pagedown", "home", "end", "up", "down",
                    "j", "k", "f1", "question_mark", "f", "tab", "shift+tab",
                    "alt+left", "alt+right", "alt+h", "alt+l",
                    "ctrl+left", "ctrl+right", "bracketleft", "bracketright",
                ):
                    return

                data = self._pty_key_bytes(event)
                if data:
                    self._cancel_prompt_wait_notice()
                    self._queue_pty_write(data)
                    event.stop()

        def _pty_key_bytes(self, event: events.Key) -> bytes:
            key = event.key
            if event.is_printable and event.character:
                return event.character.encode("utf-8")

            simple = {
                "enter": b"\r", "escape": b"\x1b", "tab": b"\t", "shift+tab": b"\x1b[Z",
                "backspace": b"\x7f", "delete": b"\x1b[3~", "home": b"\x1b[H", "end": b"\x1b[F",
                "pageup": b"\x1b[5~", "pagedown": b"\x1b[6~", "up": b"\x1b[A", "down": b"\x1b[B",
                "right": b"\x1b[C", "left": b"\x1b[D", "insert": b"\x1b[2~",
                "f1": b"\x1bOP", "f2": b"\x1bOQ", "f3": b"\x1bOR", "f4": b"\x1bOS",
                "f5": b"\x1b[15~", "f6": b"\x1b[17~", "f7": b"\x1b[18~", "f8": b"\x1b[19~",
                "f9": b"\x1b[20~", "f10": b"\x1b[21~", "f11": b"\x1b[23~", "f12": b"\x1b[24~",
            }

            if key in simple:
                return simple[key]

            if key.startswith("ctrl+"):
                rest = key[5:]
                if rest in ("space", "@"): return b"\x00"
                if rest == "[": return b"\x1b"
                if rest == "\\": return b"\x1c"
                if rest == "]": return b"\x1d"
                if rest == "^": return b"\x1e"
                if rest == "_": return b"\x1f"
                if len(rest) == 1 and rest.isalpha():
                    return bytes([ord(rest.lower()) - 96])

            return b""

        def action_quit(self) -> None:
            self.abort_flag = True
            # Worker cancellation owns child cleanup and reaping. Signaling
            # here could race task finalization or orphan interactive children.
            self.exit()

        def _show_completion_dialog(self, title: str, message: str, level: str) -> None:
            # Present it only after the worker/supervisor have finished cleanup
            # and released mutation ownership in execute_pipeline's finally.
            self._completion_dialog = (title, message, level)

        def _maybe_reexec_after_sync(self) -> str:
            # Explicit outcomes: "unchanged" (no restart needed, proceed),
            # "restart" (handoff staged, caller must return for exec),
            # "invalid" (updated files failed validation or restart cannot be
            # staged; caller must HALT, finalize all tasks, nonzero exit —
            # never proceed with the stale plan).
            # Validates incoming files BEFORE restarting; invalid code/profile/
            # settings (including deletion and invalid types) halt task
            # execution. No password bytes cross exec. No private driver calls.
            if OPT_DRY_RUN:
                return "unchanged"
            generation = int(os.environ.get("DUSKY_RESTART_GENERATION", "0") or 0)
            before = getattr(self, "_self_hash_before", None)
            after = _file_digest_or_none(SCRIPT_PATH)

            prof_filepath = getattr(self.profile, "filepath", None) if getattr(self, "profile", None) else None
            prof_before = getattr(self, "_profile_hash_before", None)
            prof_after = _file_digest_or_none(prof_filepath) if prof_filepath else None

            cfg_p = global_config_context_path()
            cfg_before = getattr(self, "_config_hash_before", None)
            cfg_after = _file_digest_or_none(cfg_p)

            script_changed = before != after
            profile_changed = prof_before != prof_after
            config_changed = cfg_before != cfg_after

            if not script_changed and not profile_changed and not config_changed:
                return "unchanged"
            if generation >= 3:
                self.add_warning("restart", "self-update restart limit reached; refusing another candidate restart")
                return "invalid"
            # Validate the updated files BEFORE restarting: broken/deleted/
            # mistyped profile or settings must halt, not exec into stale plan.
            # Uses strict profile/manifest validation; invalid task strings,
            # deletion (None digest vs content), and invalid TOML types all
            # halt. Caller finalizes tails and exits nonzero.
            try:
                if script_changed:
                    # Deletion counts as invalid (missing file cannot restart).
                    if after is None:
                        raise ValueError("new updater missing after sync (deleted)")
                    ok_new, why_new = _validate_script_syntax(SCRIPT_PATH)
                    if not ok_new:
                        raise ValueError(f"new updater failed syntax gate: {why_new}")
                    compile(SCRIPT_PATH.read_text(encoding="utf-8"), str(SCRIPT_PATH), "exec", dont_inherit=True)
                if profile_changed:
                    # Deletion or missing filepath is invalid (not unchanged).
                    if prof_filepath is None or prof_after is None:
                        raise ValueError("updated profile missing after sync (deleted)")
                    # Strict validation: load via load_profile (tables,
                    # branch/repo/search-dirs types) then parse_manifest
                    # (modes/flags/conditions/timeouts). Any ValueError or
                    # SystemExit (unknown/ambiguous/deleted profile) halts.
                    try:
                        fresh_prof = load_profile(str(prof_filepath))
                        parse_manifest(fresh_prof)
                    except SystemExit as e:
                        raise ValueError(f"new profile failed to load (exit {e.code})")
                    except ValueError as e:
                        raise ValueError(f"new profile manifest invalid: {e}")
                if config_changed:
                    # Deletion is invalid (missing settings cannot restart).
                    if cfg_after is None:
                        raise ValueError("updated settings missing after sync (deleted)")
                    try:
                        fresh_cfg = tomllib.loads(Path(cfg_p).read_bytes().decode("utf-8"))
                    except (OSError, tomllib.TOMLDecodeError) as e:
                        raise ValueError(f"new settings unreadable: {e}")
                    cfg_errs = validate_global_config(fresh_cfg)
                    if cfg_errs:
                        raise ValueError("; ".join(cfg_errs[:3]))
            except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
                self.log_main(f"[bold {THEME['error']}][updater][/] Restart aborted: updated files failed validation ({escape(str(e))}); halting (stale plan will NOT execute).")
                return "invalid"
            if _supervisor_rejected_candidate_matches(SCRIPT_PATH, prof_filepath, cfg_p):
                self.add_warning(
                    "restart",
                    "the independent supervisor previously rejected this exact bundle; refusing to restart it again during the rejection window",
                )
                self.log_main(
                    f"[bold {THEME['error']}][updater][/] Restart aborted: this exact candidate bundle was previously rejected by the supervisor."
                )
                return "invalid"
            if script_changed:
                self.log_main("[updater] Script updated during sync — restarting with the new version.")
            elif profile_changed:
                self.log_main("[updater] Profile updated during sync — restarting to apply new tasks.")
            else:
                self.log_main("[updater] Settings updated during sync — restarting to apply new configuration.")
            git_tasks = [
                {"status": self.tasks[i].status if i < len(self.tasks) else "skipped",
                 "exit_code": getattr(self.tasks[i], "exit_code", 0) if i < len(self.tasks) else 0}
                for i in range(5)
            ]
            sudo_mode = SudoEngine.mode_name()
            askpass = str(SudoEngine._askpass_path) if SudoEngine._askpass_path else ""
            sudoers = str(SudoEngine._sudoers_path) if getattr(SudoEngine, "_sudoers_path", None) else ""
            previous_generation = int(os.environ.get("DUSKY_RESTART_GENERATION", "0") or 0)
            payload = {
                "schema": 3,
                "run_id": getattr(self, "run_id", RUN_TIMESTAMP),
                "git_dir": str(GIT_DIR),
                "work_tree": str(WORK_TREE),
                "repo_url": self.profile.repo_url,
                "branch": self.profile.branch,
                "profile_filepath": str(prof_filepath or ""),
                "profile_name": self.profile.name,
                "git_tasks": git_tasks,
                "git_summary": self.git_summary if isinstance(getattr(self, "git_summary", None), dict) else {},
                "warnings": list(self.run_warnings),
                "run_start_mono": self.run_start_mono,
                "phase_durations": dict(self.phase_durations),
                "restart_generation": previous_generation + 1,
                "sudo": {"mode": sudo_mode if sudo_mode in ("password", "nopasswd") else "none",
                         "askpass_path": askpass,
                         "sudoers_path": sudoers},
                "created": now_iso(),
                "created_epoch": time.time(),
            }
            handoff = restart_handoff_path(payload["run_id"])
            # A stale handoff from our own run (same run_id) may linger after
            # an aborted restart; replace only our own file, never another's.
            with suppress(OSError):
                st_h = handoff.lstat()
                if stat.S_ISREG(st_h.st_mode) and st_h.st_uid == os.getuid():
                    handoff.unlink()
            # Keep exclusive ownership across exec: clear CLOEXEC so the lock
            # fd survives; the child re-validates and adopts it. Never release
            # here — that would open a second-instance window.
            global _LOCK_FD
            try:
                if _LOCK_FD is None:
                    self.log_main(f"[bold {THEME['error']}][updater][/] Restart aborted: no lock held; halting (stale plan will NOT execute).")
                    return "invalid"
                os.set_inheritable(_LOCK_FD, True)
                st = os.fstat(_LOCK_FD)
                try:
                    lock_file_str = str(lock_path())
                except SystemExit:
                    lock_file_str = ""
                payload["lock"] = {"fd": _LOCK_FD, "ino": st.st_ino, "dev": st.st_dev,
                                   "path": lock_file_str}
            except OSError as e:
                self.log_main(f"[bold {THEME['error']}][updater][/] Restart aborted: cannot preserve lock ({escape(str(e))}); halting.")
                return "invalid"
            if not _write_restart_handoff(handoff, payload):
                self.log_main(f"[bold {THEME['error']}][updater][/] Restart aborted: cannot stage handoff file; halting.")
                return "invalid"
            self._restart_handoff = handoff
            self.exit()
            return "restart"

        def _on_completion_reply(self, quit_now: bool | None) -> None:
            self.pipeline_finished = True
            if quit_now:
                self.exit()
            else:
                report_idx = len(self.tasks) + 1
                with suppress(Exception):
                    list_view = self.query_one("#task_list", ListView)
                    list_view.index = report_idx
                    self.query_one("#log_switcher", ContentSwitcher).current = "log-report"



def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_p = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_p, path)
        dfd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        with suppress(OSError):
            tmp_p.unlink(missing_ok=True)
        raise


def _supervisor_rejected_candidate_matches(
    script: Path, profile: Path | None, settings: Path | None,
) -> bool:
    """Return True when the supervisor recently rejected this exact bundle.

    The independent supervisor records the candidate identity before rollback.
    Rejection is deliberately bounded: an identical bundle may be retried after
    the record expires, while any changed script/profile/settings digest is
    eligible immediately. Direct-Python launches have no supervisor state and
    are unaffected.
    """
    state_raw = os.environ.get("DUSKY_SUPERVISOR_STATE_DIR")
    if not state_raw:
        return False
    rejected = Path(state_raw) / "rejected_candidate.json"
    try:
        st = rejected.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            return False
        data = json.loads(rejected.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != 1:
            return False
        expires = float(data.get("expires_epoch", 0.0))
        if not math.isfinite(expires) or time.time() > expires:
            return False
        files = data.get("files")
        if not isinstance(files, list) or not files:
            return False
        def supervisor_digest(path: Path) -> str:
            try:
                st_path = path.lstat()
                if not stat.S_ISREG(st_path.st_mode):
                    return ""
            except OSError:
                return ""
            return file_checksum(path)

        current: dict[str, tuple[str, str]] = {
            "script": (str(script), supervisor_digest(script)),
        }
        if profile is not None:
            current["profile"] = (str(profile), supervisor_digest(profile))
        if settings is not None:
            current["settings"] = (str(settings), supervisor_digest(settings))
        seen: set[str] = set()
        for rec in files:
            if not isinstance(rec, dict):
                return False
            kind = rec.get("kind")
            installed = rec.get("installed")
            digest_value = rec.get("digest")
            if kind not in current or kind in seen:
                return False
            if (installed, digest_value) != current[kind]:
                return False
            seen.add(kind)
        # A settings-free bundle is valid; otherwise every currently supervised
        # member must be represented in the rejected identity.
        return seen == set(current)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _supervisor_health_checkpoint(profile: ProfileConfig) -> None:
    """Publish a startup-health checkpoint after the mounted application is viable.

    The caller invokes this after configuration/profile parsing, dependency
    loading, Textual application construction/mount, and state/log setup, but
    before Git mutation or child-task execution. Direct ``python update_dusky.py``
    remains supported, but cannot provide
    candidate-independent rollback because the candidate is its own recovery
    process. The supervisor sets DUSKY_SUPERVISOR_CONTROL_DIR.
    """
    if OPT_DRY_RUN:
        return
    control_raw = os.environ.get("DUSKY_SUPERVISOR_CONTROL_DIR")
    if not control_raw:
        return
    fatal_cfg = [w for w in CONFIG_WARNINGS if "failed to parse settings" in w or "settings root" in w]
    if fatal_cfg:
        raise RuntimeError("supervisor health rejected malformed settings: " + "; ".join(fatal_cfg))
    control = Path(control_raw)
    launch_id = uuid.uuid4().hex
    settings = global_config_context_path()
    payload = {
        "schema": 1,
        "launch_id": launch_id,
        "pid": os.getpid(),
        "script": str(SCRIPT_PATH),
        "script_digest": _file_digest_or_none(SCRIPT_PATH),
        "profile": str(profile.filepath),
        "profile_digest": _file_digest_or_none(profile.filepath),
        "settings": str(settings),
        "settings_digest": _file_digest_or_none(settings),
        "work_tree": str(WORK_TREE),
        "git_dir": str(GIT_DIR),
        "timestamp": time.time(),
    }
    health = control / "health.json"
    ack = control / f"ack_{launch_id}"
    _atomic_json_write(health, payload)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if ack.is_file():
            with suppress(OSError):
                ack.unlink(missing_ok=True)
            os.environ["DUSKY_SUPERVISOR_LAUNCH_ID"] = launch_id
            return
        time.sleep(0.05)
    raise RuntimeError("supervisor did not acknowledge durable known-good snapshot")


def _supervisor_finish_checkpoint() -> None:
    control_raw = os.environ.get("DUSKY_SUPERVISOR_CONTROL_DIR")
    launch_id = os.environ.get("DUSKY_SUPERVISOR_LAUNCH_ID")
    if (OPT_DRY_RUN or not control_raw or not launch_id
            or os.environ.get("DUSKY_SUPERVISOR_FINISH_PROTOCOL") != "1"):
        # A worker can update itself under the already-running previous
        # supervisor. Only request completion acknowledgement when supported.
        return
    control = Path(control_raw)
    _atomic_json_write(control / "finished.json", {
        "schema": 1, "launch_id": launch_id, "pid": os.getpid(),
    })
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if (control / f"finished_ack_{launch_id}").is_file():
            return
        time.sleep(0.05)
    # Keep ownership on failure; normal process exit still closes the fd.
    raise RuntimeError("supervisor did not acknowledge update completion")


def _close_inherited_lock_fd(raw_fd: str | None) -> None:
    """Close an exec-inherited lock descriptor when its handoff is rejected."""
    if not raw_fd:
        return
    try:
        fd = int(raw_fd)
        if fd >= 0 and fd != globals().get("_LOCK_FD"):
            os.close(fd)
    except (TypeError, ValueError, OSError):
        pass


def _adopt_inherited_lock(info: dict) -> bool:
    global _LOCK_FD
    if not isinstance(info, dict):
        return False
    try:
        old_fd = int(info.get("fd", -1))
        if old_fd < 0:
            return False
        st = os.fstat(old_fd)
        if int(info.get("ino", -1)) != st.st_ino or int(info.get("dev", -1)) != st.st_dev:
            return False
        fcntl.flock(old_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous_path = str(info.get("path", ""))
        effective = lock_path()
        if previous_path and Path(previous_path) != effective:
            # Hold the inherited lock until the new configured lock is
            # acquired, then hand ownership over with no unlocked window.
            fd2 = os.open(str(effective), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.ftruncate(fd2, 0)
                os.write(fd2, f"{os.getpid()}\n".encode())
            except BaseException:
                os.close(fd2)
                raise
            _LOCK_FD = fd2
            with suppress(OSError):
                os.close(old_fd)
        else:
            _LOCK_FD = old_fd
        os.set_inheritable(_LOCK_FD, False)
        atexit.register(_cleanup_lock)
        return True
    except (TypeError, ValueError, OSError):
        return False


def _adopt_current_process_lock() -> bool:
    """Recover an exec-inherited lock independently of handoff schema changes.

    Match the live descriptor, current lock inode, and our unchanged exec PID.
    Never remove a lock file or release another process's lock.
    """
    try:
        effective = lock_path()
        target = effective.stat()
        for name in os.listdir("/proc/self/fd"):
            if not name.isdecimal() or int(name) < 3:
                continue
            fd = int(name)
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    continue
                if (st.st_dev, st.st_ino) != (target.st_dev, target.st_ino):
                    continue
                if os.pread(fd, 64, 0).strip() != str(os.getpid()).encode("ascii"):
                    continue
                if _adopt_inherited_lock({
                    "fd": fd, "ino": st.st_ino, "dev": st.st_dev,
                    "path": str(effective),
                }):
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def _adopt_handoff_sudo(payload: dict | Path | None) -> bool:
    if not isinstance(payload, dict):
        return False
    sudo = payload.get("sudo", {})
    if not isinstance(sudo, dict):
        return False
    mode = sudo.get("mode")
    if mode == "password":
        ap = sudo.get("askpass_path", "")
        try:
            cand = Path(ap)
            st = cand.lstat()
            rt = runtime_dir(ensure=False).resolve()
            valid = stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700
            valid = valid and (rt == cand.resolve() or rt in cand.resolve().parents)
        except (OSError, RuntimeError):
            valid = False
        if valid:
            SudoEngine._askpass_path = cand
            SudoEngine._mode = "password"
            os.environ["SUDO_ASKPASS"] = str(cand)
            # Rehydrate the configured prompt-autofeed secret from the owned
            # askpass helper instead of losing functionality across exec.
            try:
                got = subprocess.run([str(cand)], capture_output=True, text=True, timeout=3, check=True)
                SudoEngine._password = got.stdout.rstrip("\r\n")
            except Exception:
                SudoEngine._password = None
            sp = sudo.get("sudoers_path", "")
            if isinstance(sp, str) and sp:
                SudoEngine._sudoers_path = Path(sp)
            if not SudoEngine._registered_atexit:
                atexit.register(SudoEngine.cleanup)
                SudoEngine._registered_atexit = True
            return SudoEngine.refresh_sync()
    elif mode == "nopasswd":
        return SudoEngine.detect_nopasswd()
    return False

if __name__ == "__main__":
    try:
        args = _early_info_dispatch()

        inherited_lock: dict | None = None
        _early_handoff: dict | None = None
        adopted_lock = False
        adopted_sudo = False
        inherited_lock_fd_env = os.environ.pop("DUSKY_INHERITED_LOCK_FD", None)
        if not OPT_DRY_RUN:
            setup_runtime_dir()
            if OPT_POST_SELF_UPDATE and OPT_HANDOFF is not None:
                try:
                    _early_handoff = validate_restart_handoff(OPT_HANDOFF, load_profile(OPT_PROFILE_NAME))
                except SystemExit:
                    raise
                except Exception:
                    _early_handoff = None
                if _early_handoff is not None and isinstance(_early_handoff.get("lock"), dict):
                    inherited_lock = _early_handoff["lock"]
            if inherited_lock is not None and _adopt_inherited_lock(inherited_lock):
                adopted_lock = True
            elif OPT_POST_SELF_UPDATE and _adopt_current_process_lock():
                adopted_lock = True
            else:
                # A malformed/stale handoff must not leave the old exec-inherited
                # flock descriptor open, otherwise reacquiring the same lock via
                # a new open file description self-conflicts on Linux.
                _close_inherited_lock_fd(inherited_lock_fd_env)
                if not acquire_lock():
                    sys.exit(1)
            if OPT_POST_SELF_UPDATE and _early_handoff is not None:
                adopted_sudo = _adopt_handoff_sudo(_early_handoff)

        SUDO_ALREADY_ACQUIRED = bootstrap_dependencies()

        profile = load_profile(OPT_PROFILE_NAME)
        try:
            tasks = parse_manifest(profile)
        except ValueError as e:
            sys.stderr.write(f"[FATAL] Invalid task manifest: {e}\n")
            sys.exit(2)
        profile.tasks = tasks
        has_sudo = bool(SUDO_ALREADY_ACQUIRED or adopted_sudo)

        if args.list:
            list_active_scripts(profile)
            sys.exit(0)

        if OPT_DRY_RUN:
            has_sudo = False
        elif not OPT_SYNC_ONLY:
            if not has_sudo and any(t.mode == 'S' for t in tasks):
                if not SudoEngine.preflight(cli_password=getattr(args, 'sudo_password', None)):
                    if SudoEngine._last_cancelled:
                        sys.exit(130)
                    sys.stderr.write("\033[1;33m[WARN]\033[0m Sudo unavailable; privileged tasks will be skipped while user tasks continue.\n")
                    has_sudo = False
                else:
                    has_sudo = True

        setup_storage_roots()
        setup_logging()

        if not OPT_SYNC_ONLY:
            if not resolve_and_validate_manifest(profile, tasks):
                sys.stderr.write(
                    "\033[1;31m[FATAL]\033[0m Pre-flight validation failed. "
                    "Resolve the above errors and re-run.\n"
                )
                sys.exit(1)

        check_runtime_versions()
        _require_ui()
        app = DuskyApp(profile, tasks, has_sudo)
        app.run()

        handoff = getattr(app, "_restart_handoff", None)
        if handoff is not None:
            cleaned_args: list[str] = []
            skip_next = False
            for a in sys.argv[1:]:
                if skip_next:
                    skip_next = False
                    continue
                if a in ("--post-self-update",):
                    continue
                if a == "--handoff":
                    skip_next = True
                    continue
                if a.startswith("--handoff="):
                    continue
                cleaned_args.append(a)
            cleaned_args += ["--handoff", str(handoff)]
            try:
                if _LOCK_FD is not None:
                    os.environ["DUSKY_INHERITED_LOCK_FD"] = str(_LOCK_FD)
                os.execv(sys.executable, [sys.executable, str(SCRIPT_PATH), "--post-self-update", *cleaned_args])
            except OSError as e:
                os.environ.pop("DUSKY_INHERITED_LOCK_FD", None)
                sys.stderr.write(f"\033[1;31m[FATAL]\033[0m Restart exec failed ({e}); update already applied, re-run manually.\n")
                with suppress(OSError):
                    Path(handoff).unlink(missing_ok=True)
                sys.exit(1)
            sys.exit(1)
        sys.exit(getattr(app, "exit_code", 0))

    except BrokenPipeError:
        with suppress(Exception):
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        sys.exit(0)
    except KeyboardInterrupt:
        sys.stdout.write("\n\033[1;33m[WARN]\033[0m User interrupt detected. Terminating.\n")
        sys.exit(130)

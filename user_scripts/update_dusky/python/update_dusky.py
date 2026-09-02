#!/usr/bin/env python3
# ==============================================================================
#  DUSKY UPDATER (v9.6.0)
# ==============================================================================
import sys

if sys.version_info < (3, 14):
    sys.stdout.write("\033[1;31m[FATAL]\033[0m Dusky requires Python 3.14+ bleeding-edge architecture.\n")
    sys.exit(1)

import argparse
import asyncio
import atexit
import base64
import codecs
import fcntl
import functools
import hashlib
import importlib
import importlib.metadata as importlib_metadata
import importlib.util
import json
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

VERSION = "9.6.0"
SCRIPT_DIR: Path = Path(__file__).resolve().parent
SCRIPT_PATH: Path = Path(__file__).resolve()
PROFILES_DIR: Path = Path(
    os.environ.get("DUSKY_UPDATER_PROFILES_DIR", SCRIPT_DIR / "profiles")
).resolve()


def global_config_path() -> Path | None:
    custom_path = os.environ.get("DUSKY_UPDATER_SETTINGS")
    if custom_path:
        p = Path(custom_path).expanduser()
        if p.is_file():
            return p
    config_path = PROFILES_DIR / "settings" / "update_dusky.toml"
    if config_path.is_file():
        return config_path
    return None


def load_global_config() -> dict:
    p = global_config_path()
    if p and p.is_file():
        try:
            with open(p, "rb") as f:
                return tomllib.load(f)
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to parse config ({p}): {e}\n")
    return {}


GLOBAL_CONFIG = load_global_config()

ASCII_MODE = GLOBAL_CONFIG.get("ui", {}).get("ascii_mode", False)
DISK_MIN_FREE_MB = GLOBAL_CONFIG.get("execution", {}).get("disk_min_free_mb", 100)
DISK_COPY_RESERVE_MB = GLOBAL_CONFIG.get("execution", {}).get("disk_copy_reserve_mb", 64)
NAMESPACE = GLOBAL_CONFIG.get("paths", {}).get("namespace", "dusky-updater")


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
    raw = GLOBAL_CONFIG.get("paths", {}).get("documents_dir", "Documents")
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return user_home() / p


def _documents_subdir(key: str, default: str) -> Path:
    raw = GLOBAL_CONFIG.get("paths", {}).get(key, default)
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return documents_root() / p


def logs_dir() -> Path:
    return _documents_subdir("logs_subdir", "logs")


def backups_dir() -> Path:
    return _documents_subdir("backups_subdir", "dusky_backups")


def runtime_dir() -> Path:
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        candidate = Path(xdg_runtime) / NAMESPACE
        if ensure_secure_dir(candidate):
            return candidate
    candidate = Path(f"/tmp/{NAMESPACE}-{os.getuid()}")
    if not ensure_secure_dir(candidate):
        sys.stderr.write(f"\033[1;31m[FATAL]\033[0m Cannot secure runtime directory: {candidate}\n")
        sys.exit(1)
    return candidate


def state_dir() -> Path:
    p = _documents_subdir("state_subdir", "state")
    ensure_secure_dir(p)
    return p


def ensure_secure_dir(path: Path) -> bool:
    if path.is_symlink():
        return False
    if path.exists() and not path.is_dir():
        return False
    try:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    except OSError:
        return False

    try:
        st = path.stat()
        is_owner = st.st_uid == os.getuid()
        is_writable = os.access(path, os.W_OK)
        return is_owner and is_writable and not path.is_symlink()
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
    if sys.version_info < (3, 14):
        sys.stderr.write("\033[1;31m[FATAL]\033[0m Python 3.14+ is required.\n")
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


check_runtime_versions()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def file_checksum(path: Path) -> str:
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


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
    p.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        p.chmod(0o700)
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
    default_rules = [
        ("sudo_password", r"(?i)(\[sudo\] password for [^:]+:|^\s*Password:\s*$|sudo: a password is required|Password:\s*$)", "password"),
        ("pgp_import", r"(?i)(::\s*Import PGP key.*\?\s*\[Y/n\]|::\s*Append key\?.*\[Y/n\]|Import PGP key.*\?\s*\[Y/n\])", "yes"),
        ("pacman_proceed", r"(?i)::\s*(Proceed with (?:installation|download|upgrade)|Continue (?:installation|download|upgrade)).*\?\s*\[Y/n\]", "yes"),
        ("pacman_replace", r"(?i)::\s*Replace\s+.*\?\s*\[Y/n\]", "yes"),
        ("pacman_remove_conflict", r"(?i)::\s*Remove conflicting file.*\?\s*\[Y/n\]", "yes"),
        ("aur_proceed", r"(?i)(Proceed with installation\?|Continue building\?|Continue installing\?|::\s*Proceed with (?:installation|download|build).*\?\s*\[Y/n\])", "yes"),
        ("generic_yes", r"(?i)\[Y/n\]|\(Y/n\)|\[y/N\]|\(y/N\)", "yes"),
    ]
    config_rules = GLOBAL_CONFIG.get("prompts", {}).get("rules", None)
    rules = []
    items_to_parse = config_rules if config_rules is not None else default_rules
    for item in items_to_parse:
        if isinstance(item, dict):
            name, pattern, kind = item["name"], item["pattern"], item["kind"]
        else:
            name, pattern, kind = item
        rules.append((name, re.compile(pattern, re.MULTILINE), kind))
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

    ENV_KEEP = GLOBAL_CONFIG.get(
        "sudo",
        {},
    ).get(
        "env_keep",
        [
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
        ],
    )

    @classmethod
    def mode_name(cls) -> str:
        return cls._mode

    @classmethod
    def _remove_stale_askpass_files(cls) -> None:
        prefix = GLOBAL_CONFIG.get("paths", {}).get("askpass_prefix", ".dusky_askpass_")
        with suppress(OSError):
            for p in askpass_dir().glob(f"{prefix}*"):
                with suppress(OSError):
                    p.unlink(missing_ok=True)
            for p in runtime_dir().glob(f"{prefix}*"):
                with suppress(OSError):
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
            with suppress(OSError):
                cls._askpass_path.unlink(missing_ok=True)

        cls._askpass_path = None
        cls._sudoers_path = None

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

        prefix = GLOBAL_CONFIG.get("paths", {}).get("askpass_prefix", ".dusky_askpass_")
        fd, path = tempfile.mkstemp(prefix=prefix, dir=str(askpass_dir()))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(path, 0o700)
        return Path(path)

    @classmethod
    def _remove_stale_sudoers_files(cls, env: dict[str, str]) -> None:
        prefix = GLOBAL_CONFIG.get("sudo", {}).get("dropin_prefix", "99_dusky_")
        sudoers_dir = GLOBAL_CONFIG.get("sudo", {}).get("sudoers_dir", "/etc/sudoers.d")
        script = f"""
for f in {sudoers_dir}/{prefix}*; do
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
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )

    @classmethod
    def _write_sudoers_dropin(cls, env: dict[str, str]) -> None:
        username = pwd.getpwuid(os.getuid()).pw_name
        safe_user = re.sub(r"[^A-Za-z0-9._-]", "_", username)
        prefix = GLOBAL_CONFIG.get("sudo", {}).get("dropin_prefix", "99_dusky_")
        sudoers_dir = GLOBAL_CONFIG.get("sudo", {}).get("sudoers_dir", "/etc/sudoers.d")
        path = Path(f"{sudoers_dir}/{prefix}{safe_user}_{os.getpid()}")
        env_vars = " ".join(cls.ENV_KEEP)

        start_time = "0"
        with suppress(OSError, IndexError):
            stat_text = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="ascii", errors="ignore")
            idx = stat_text.rfind(")")
            if idx != -1:
                start_time = stat_text[idx + 1:].split()[19]

        timeout = GLOBAL_CONFIG.get("sudo", {}).get("timestamp_timeout", 15)
        content = (
            f"# pid={os.getpid()} starttime={start_time} ts={int(time.time())}\n"
            f"Defaults:{username} timestamp_type=global, timestamp_timeout={timeout}\n"
            f"Defaults:{username} env_keep += \"{env_vars} DUSKY_*\"\n"
        )

        shell_cmd = (
            f"mkdir -p {shlex.quote(str(sudoers_dir))} && "
            f"umask 077 && cat > {shlex.quote(str(path))} && "
            f"chmod 0440 {shlex.quote(str(path))}"
        )

        try:
            proc = subprocess.run(
                ["sudo", "-A", "sh", "-c", shell_cmd],
                input=content,
                text=True,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            if proc.returncode != 0:
                return

            check = subprocess.run(
                ["sudo", "-A", "visudo", "-c", "-f", str(path)],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )

            if check.returncode == 0:
                cls._sudoers_path = path
            else:
                with suppress(Exception):
                    subprocess.run(
                        ["sudo", "-A", "rm", "-f", str(path)],
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
        except Exception:
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
            return False, "sudo authentication timed out"
        except OSError as e:
            with suppress(OSError):
                askpass.unlink(missing_ok=True)
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
        return False, err or "sudo authentication failed"

    @classmethod
    def detect_nopasswd(cls) -> bool:
        if os.geteuid() == 0:
            cls._mode = "root"
            return True

        if not shutil.which("sudo"):
            return False

        with suppress(Exception):
            proc = subprocess.run(
                ["sudo", "-n", "-v"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
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
        if os.geteuid() == 0:
            cls._mode = "root"
            sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Running as root. No sudo escalation needed.\n")
            return True

        if not shutil.which("sudo"):
            sys.stderr.write("\033[1;31m[FATAL]\033[0m sudo is required but not installed.\n")
            return False

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
                    sys.stderr.write("\n\033[1;31m[FATAL]\033[0m Sudo authentication cancelled.\n")
                    return False

                ok, err = cls.set_password(password)
                if ok:
                    sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Sudo credentials cached for this session.\n")
                    return True
                sys.stderr.write(f"\033[1;31m[ERROR]\033[0m Authentication failed ({attempt}/3): {err}\n")

        sys.stderr.write("\033[1;31m[FATAL]\033[0m Sudo authentication failed. Aborting.\n")
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
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(name)).strip("._")
    return cleaned or "unnamed"


class StateStore:
    DONE = {
        "completed",
        "skipped",
        "ignored",
        "manual",
        "completed_once",
    }

    def __init__(self, profile: 'ProfileConfig'):
        self.path = state_dir() / f"{safe_filename(profile.name)}.db"
        busy_timeout = GLOBAL_CONFIG.get("execution", {}).get("db_busy_timeout", 5000)

        if OPT_DRY_RUN:
            if not self.path.exists():
                self.conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._ensure_schema()
            else:
                self.conn = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, check_same_thread=False)
        else:
            self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=busy_timeout / 1000.0)
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self._ensure_schema()

    def _ensure_schema(self) -> None:
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
        self.conn.execute(
            """
            INSERT OR REPLACE INTO state
                (state_key, status, script, checksum, exit_code, note, updated, duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.state_key,
                status,
                task.name,
                task.checksum,
                exit_code,
                note,
                now_iso(),
                duration,
            ),
        )
        self.conn.commit()

    def reset(self) -> None:
        with suppress(Exception):
            self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.path}{suffix}").unlink(missing_ok=True)

    def close(self) -> None:
        with suppress(Exception):
            self.conn.close()


class OnceStore:
    def __init__(self) -> None:
        self.path = state_dir() / "once.db"
        busy_timeout = GLOBAL_CONFIG.get("execution", {}).get("db_busy_timeout", 5000)

        if OPT_DRY_RUN:
            if not self.path.exists():
                self.conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._ensure_schema()
            else:
                self.conn = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, check_same_thread=False)
        else:
            self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=busy_timeout / 1000.0)
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self._ensure_schema()

    def _ensure_schema(self) -> None:
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

    def forget(self, script: str) -> int:
        script = script.strip()
        if not script:
            return 0

        try:
            cur = self.conn.execute(
                """
DELETE FROM once_markers
WHERE script_name = ?
   OR resolved_path = ?
   OR script_name LIKE ?
""",
                (script, script, f"%/{script}"),
            )
            self.conn.commit()
            return cur.rowcount
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

    def check_marker_status(self, task: 'DuskyTask', profile_name: str) -> Literal["run", "skip", "notify_sealed"]:
        if not task.once:
            return "run"

        key = self.make_key(task, profile_name)
        try:
            cur = self.conn.execute(
                "SELECT checksum, once_mode, notified_checksum FROM once_markers WHERE marker_key = ?",
                (key,),
            )
            row = cur.fetchone()
        except sqlite3.OperationalError:
            return "run"
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
        key = self.make_key(task, profile_name)
        self.conn.execute(
            "UPDATE once_markers SET notified_checksum = ?, checksum = ?, updated = ? WHERE marker_key = ?",
            (task.checksum, task.checksum, now_iso(), key)
        )
        self.conn.commit()

    def mark_success(
        self,
        task: 'DuskyTask',
        profile_name: str,
        exit_code: int | None = None,
        run_id: str = "",
    ) -> None:
        if not task.once:
            return

        key = self.make_key(task, profile_name)
        args_key = shlex.join(task.args)

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

    def list_markers(self) -> list[dict[str, object]]:
        cur = self.conn.execute(
            "SELECT profile, scope, mode, script_name, args_key, resolved_path, checksum, once_mode, exit_code, run_id, updated FROM once_markers ORDER BY profile, script_name, args_key"
        )
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
        "group",
        "env",
    }

    def __init__(self):
        self.cache: dict[str, bool] = {}

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
        self.run_id = run_id

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
        if not self.enabled or not self.write_task_logs or self.root is None:
            return
        log_path = self.task_log_path(task, index)
        with suppress(OSError):
            with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                if not text.endswith("\n"):
                    text += "\n"
                f.write(text)

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
        log_path = self.task_log_path(task, index)
        with suppress(OSError):
            with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"\n[{now_ts()}] TASK END: {task.name}\n")
                f.write(f"[{now_ts()}] STATUS: {status}\n")
                if exit_code is not None:
                    f.write(f"[{now_ts()}] EXIT CODE: {exit_code}\n")
                f.write(f"[{now_ts()}] DURATION: {duration:.2f}s\n")

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

        lines.extend(["", "## Tasks", ""])

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
                "path": str(task.resolved_path) if getattr(task, "resolved_path", None) else "",
                "args": task.args,
                "duration": round(dur, 2),
            }
            report["tasks"].append(item)
            dur_str = f" ({dur:.2f}s)" if dur > 0 else ""
            lines.append(f"- [{task.mode}] {task.name} -> {st}{dur_str}")

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
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )


class DesktopNotifier:
    enabled = True

    @classmethod
    def notify(cls, title: str, body: str, urgency: str = "normal") -> None:
        if not cls.enabled or not GLOBAL_CONFIG.get("notifications", {}).get("desktop_enabled", True):
            return
        if not shutil.which("notify-send"):
            return
        app_name = GLOBAL_CONFIG.get("notifications", {}).get("app_name", "Dusky Updater")
        with suppress(OSError):
            subprocess.Popen(
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
        if self.proc is None:
            return
        with suppress(Exception):
            self.proc.terminate()
            self.proc.wait(timeout=3)
        with suppress(Exception):
            self.proc.kill()
        self.proc = None


# ==============================================================================
#  CLI ARGUMENT PARSING & CONFIGURATION
# ==============================================================================
OPT_DRY_RUN = False
OPT_SKIP_SYNC = False
OPT_SYNC_ONLY = False
OPT_POST_SELF_UPDATE = False
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
    sys.stdout.write(f"Runtime dir:    {runtime_dir()}\n")
    sys.stdout.write(f"Profiles dir:   {PROFILES_DIR}\n")

    profiles = list_profiles()
    sys.stdout.write(f"Profiles found: {len(profiles)}\n")
    for p in profiles:
        sys.stdout.write(f"  - {p.name}\n")
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
    return parser


def parse_args():
    global OPT_DRY_RUN, OPT_SKIP_SYNC, OPT_SYNC_ONLY, OPT_FORCE
    global OPT_STOP_ON_FAIL, OPT_ALLOW_DIVERGED_RESET, OPT_PROFILE_NAME
    global OPT_POST_SELF_UPDATE

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
            if depth and text[k] != '=':
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
        for cand in available:
            if cand.stem.lower() == q_lower or cand.name.lower() == q_lower:
                p = cand
                break
        if p is None:
            for cand in available:
                if q_lower in cand.stem.lower() or cand.stem.lower().startswith(q_lower):
                    p = cand
                    break

    if p is None:
        if available:
            p = available[0]
            sys.stderr.write(
                f"[WARN] Profile '{name_or_path}' not found; "
                f"falling back to '{p.stem}' ({p})\n"
            )
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
            try:
                p.write_text(repaired, encoding="utf-8")
                sys.stderr.write(
                    f"[WARN] Inserted {fixes} missing comma(s) in '{p}' -- "
                    f"auto-repaired. Fix them properly in git!\n"
                )
            except OSError as write_err:
                sys.stderr.write(
                    f"[WARN] Inserted {fixes} missing comma(s) in '{p}' (in-memory "
                    f"repair only, could not save: {write_err}). Fix them in git!\n"
                )

        prof_meta = data.get("profile", {})
        git_cfg = data.get("git", {})
        seq_cfg = data.get("sequence", {})

        return ProfileConfig(
            name=prof_meta.get("name", p.stem),
            description=prof_meta.get("description", ""),
            filepath=p,
            repo_url=git_cfg.get("repo_url", GLOBAL_CONFIG.get("git", {}).get("repo_url", "https://github.com/dusklinux/dusky")),
            branch=git_cfg.get("branch", GLOBAL_CONFIG.get("git", {}).get("branch", "main")),
            search_dirs=git_cfg.get("search_dirs", [
                "user_scripts/arch_setup_scripts/scripts",
                "user_scripts/arch_setup_scripts",
                "user_scripts/networking",
                "user_scripts/misc_extra",
                "user_scripts/update_dusky",
                "user_scripts/services",
            ]),
            conflict_resolutions=data.get("conflict_resolutions", {}),
            sequence=seq_cfg.get("tasks", []),
        )
    except Exception as e:
        sys.stderr.write(f"[FATAL] Failed to load profile '{p}': {e}\n")
        sys.exit(1)


def setup_runtime_dir():
    global RUNTIME_DIR, LOCK_FILE
    RUNTIME_DIR = runtime_dir()
    LOCK_FILE = lock_path()


_LOCK_FD: int | None = None


def get_lock_holders() -> str:
    lp = lock_path()
    if not lp.exists():
        return ""

    try:
        real_lock = lp.resolve()
    except Exception:
        return ""

    holders: list[str] = []
    proc_dir = Path("/proc")
    if not proc_dir.exists():
        return ""

    try:
        pids = [d for d in proc_dir.iterdir() if d.name.isdigit()]
    except PermissionError:
        return ""

    my_pid = str(os.getpid())

    for pid_dir in pids:
        if pid_dir.name == my_pid:
            continue

        fd_dir = pid_dir / "fd"
        try:
            if not fd_dir.exists():
                continue
            for fd_link in fd_dir.iterdir():
                try:
                    if os.readlink(fd_link) == str(real_lock):
                        cmdline_path = pid_dir / "cmdline"
                        cmd = ""
                        with suppress(PermissionError, OSError):
                            if cmdline_path.exists():
                                cmd = cmdline_path.read_text(errors="replace").replace("\x00", " ").strip()
                        if not cmd:
                            cmd = f"[pid {pid_dir.name}]"
                        holders.append(f"  - PID {pid_dir.name}: {cmd}")
                        break
                except (PermissionError, FileNotFoundError, OSError):
                    continue
        except (PermissionError, OSError):
            continue

    return "\n".join(holders)


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
    if OPT_DRY_RUN:
        return True
    lp = lock_path()
    try:
        lp.parent.mkdir(parents=True, exist_ok=True)
        cloexec = getattr(os, "O_CLOEXEC", 0)
        _LOCK_FD = os.open(str(lp), os.O_RDWR | os.O_CREAT | cloexec, 0o600)
        try:
            fcntl.flock(_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(_LOCK_FD, 0)
            os.write(_LOCK_FD, f"{os.getpid()}\n".encode("ascii"))
            atexit.register(_cleanup_lock)
            return True
        except (BlockingIOError, OSError):
            holders = get_lock_holders()
            msg = f"Another instance of Dusky Updater is currently active on {lp}."
            if holders:
                msg += f"\nActive lock holder(s):\n{holders}"
            sys.stderr.write(f"\033[1;31m[FATAL]\033[0m {msg}\n")
            os.close(_LOCK_FD)
            _LOCK_FD = None
            return False
    except OSError as e:
        sys.stderr.write(f"\033[1;33m[WARN]\033[0m Could not establish process lock ({lp}): {e}\n")
        return True


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


def parse_manifest(profile: ProfileConfig) -> list[DuskyTask]:
    tasks = [
        DuskyTask("Git Bare Repo Validation", 'GIT', False, False, []),
        DuskyTask("Fetch Upstream & Diff", 'GIT', False, False, []),
        DuskyTask("Forensic Collision Backup", 'GIT', False, False, []),
        DuskyTask("Atomic Snapshot (CoW)", 'GIT', False, False, []),
        DuskyTask("Apply Bare Updates (Reset)", 'GIT', False, False, [])
    ]
    for i, t in enumerate(tasks):
        t.state_key = hashlib.blake2b(f"{t.mode}|{t.name}".encode("utf-8")).hexdigest()

    interactive_heuristics = {'reboot_post_lua_update.sh', 'tui_matugen.py', 'dusky_firefox_tui.sh'}

    for entry in profile.sequence:
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

        cmd_tokens: list[str] | None = None
        with suppress(Exception):
            cmd_tokens = shlex.split(cmd_part)
        if not cmd_tokens:
            cmd_tokens = cmd_part.split()

        if not cmd_tokens:
            continue

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
                        pass
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
                        # systemd units are case-sensitive.
                        condition = val_stripped if condition is None else f"{condition},{val_stripped}"
                    case ("timeout", _):
                        with suppress(ValueError):
                            timeout = float(val_stripped)
                    case ("retry", _):
                        with suppress(ValueError):
                            retry = max(0, int(val_stripped))
                    case ("retry_delay", _):
                        with suppress(ValueError):
                            retry_delay = max(0.0, float(val_stripped))
                    case _:
                        pass

        if not interactive and script_name in interactive_heuristics:
            interactive = True

        tasks.append(DuskyTask(
            name=script_name, mode=mode,  # type: ignore
            ignore_fail=ignore_fail, interactive=interactive,
            interactive_override=interactive_override,
            condition=condition, timeout=timeout, retry=retry,
            retry_delay=retry_delay, once=once,
            once_mode=once_mode, once_scope=once_scope, args=args
        ))
    return tasks


# ==============================================================================
#  PRE-FLIGHT BOOTSTRAP & DEPENDENCY RESOLUTION
# ==============================================================================
def bootstrap_dependencies() -> bool:
    if any(flag in sys.argv for flag in {"-h", "--help", "--version", "--doctor", "--list", "--list-once", "--forget-once"}):
        return False

    missing = [
        pkg for mod, pkg in [("textual", "python-textual"), ("rich", "python-rich")]
        if importlib.util.find_spec(mod) is None
    ]
    if missing:
        sys.stdout.write(f"\033[1;33m[DUSKY BOOTSTRAP]\033[0m Resolving dependencies: {', '.join(missing)}\n")
        if not SudoEngine.preflight():
            sys.exit(1)
        try:
            cmd = SudoEngine.sudo_prefix() + ['pacman', '-S', '--noconfirm'] + missing
            subprocess.run(cmd, check=True)
            importlib.invalidate_caches()
            importlib.reload(site)
        except subprocess.CalledProcessError:
            sys.stdout.write("\033[1;31m[FATAL]\033[0m Dependency resolution failed.\n")
            sys.exit(1)
        return True
    return False


def _early_info_dispatch() -> None:
    """Validate CLI arguments and serve informational subcommands without
    requiring the TUI stack.

    Runs unconditionally so unknown options are rejected BEFORE
    bootstrap_dependencies() gets a chance to touch the system, and so the
    info flags never hard-fail on a missing textual/rich (bootstrap
    deliberately skips dependency installation for them)."""
    parser = _build_arg_parser()
    args, unknown = parser.parse_known_args()
    if unknown:
        sys.stderr.write(f"Error: Unknown option {unknown[0]}\n")
        parser.print_usage(sys.stderr)
        sys.exit(2)

    if not (args.help or args.version or args.doctor or args.list
            or args.list_once or args.forget_once):
        return

    if args.help:
        show_help()
    if args.version:
        show_version()
    if args.doctor:
        run_doctor()

    if args.list_once:
        store = OnceStore()
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
        profile.tasks = parse_manifest(profile)
        list_active_scripts(profile)


_early_info_dispatch()
SUDO_ALREADY_ACQUIRED = bootstrap_dependencies()

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
except ImportError:
    sys.stdout.write("\033[1;31m[FATAL]\033[0m UI library import failed post-resolution. Ensure Arch mirrors are synced.\n")
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
            backup_prefixes = (
                "full_snapshot_", "your_changes_", "moved_aside_",
                "manual_merge_", "repo_history_",
                # Legacy prefixes included to ensure older backups are still cleaned up
                "pre_reset_", "user_mods_", "untracked_collisions_",
                "needs_merge_"
            )
            with suppress(Exception):
                for d in b_dir.iterdir():
                    if d.is_dir() and any(d.name.startswith(p) for p in backup_prefixes):
                        if d.stat().st_mtime < cutoff:
                            with suppress(OSError):
                                shutil.rmtree(d, ignore_errors=True)


def make_private_dir_under(base: Path, folder_name: str) -> Path | None:
    if not ensure_secure_dir(base):
        return None
    candidate = base / folder_name
    try:
        candidate.mkdir(mode=0o700)
        candidate.chmod(0o700)
        return candidate
    except FileExistsError:
        for i in range(2, 100):
            candidate = base / f"{folder_name}_{i}"
            try:
                candidate.mkdir(mode=0o700)
                candidate.chmod(0o700)
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

    if ensure_secure_dir(l_dir):
        ACTIVE_LOG_BASE_DIR = l_dir
    else:
        sys.stderr.write(f"Error: Cannot create log directory: {l_dir}\n")
        sys.exit(1)

    if ensure_secure_dir(b_dir):
        ACTIVE_BACKUP_BASE_DIR = b_dir
    else:
        sys.stderr.write(f"Error: Cannot create backup directory: {b_dir}\n")
        sys.exit(1)


async def wait_for_process(proc: asyncio.subprocess.Process, timeout: float | None = None) -> int:
    """Waits for a subprocess to exit cleanly without hanging on SIGCHLD delivery."""
    start_t = time.monotonic()

    with suppress(Exception):
        if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
            signal.signal(signal.SIGCHLD, signal.SIG_DFL)

    while True:
        if proc.returncode is not None:
            return proc.returncode

        with suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=0.15)
            if proc.returncode is not None:
                return proc.returncode

        if proc.pid is not None:
            try:
                pid, status = os.waitpid(proc.pid, os.WNOHANG)
                if pid == proc.pid:
                    if os.WIFEXITED(status):
                        rc = os.WEXITSTATUS(status)
                    elif os.WIFSIGNALED(status):
                        rc = -os.WTERMSIG(status)
                    else:
                        rc = 0
                    if hasattr(proc, "_transport") and proc._transport:
                        with suppress(Exception):
                            if hasattr(proc._transport, "_process_exited"):
                                proc._transport._process_exited(rc)
                            else:
                                proc._transport._returncode = rc  # type: ignore
                    return rc
            except (ChildProcessError, OSError):
                if proc.returncode is not None:
                    return proc.returncode
                return 0

        if timeout is not None and timeout > 0:
            if (time.monotonic() - start_t) >= timeout:
                raise TimeoutError()

        await asyncio.sleep(0.05)


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
    if not GLOBAL_CONFIG.get("logging", {}).get("enabled", True):
        return
    LOG_FILE = make_private_file_under(ACTIVE_LOG_BASE_DIR, f"dusky_update_{RUN_TIMESTAMP}_", ".log")
    if not LOG_FILE:
        sys.stderr.write("Error: Cannot create log file\n")
        sys.exit(1)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("================================================================================\n")
            f.write(f" DUSKY UPDATE LOG — {RUN_TIMESTAMP}\n")
            f.write(f" Kernel: {os.uname().release} | User: {user_home().name} | Python: {sys.version.split()[0]}\n")
            f.write("================================================================================\n")
    except Exception as e:
        sys.stderr.write(f"Error: Cannot write to log file: {e}\n")
        sys.exit(1)


# ==============================================================================
#  THEME COMPILER (MATUGEN JSON)
# ==============================================================================
def compile_theme() -> dict[str, str]:
    default_palette = GLOBAL_CONFIG.get("ui", {}).get("default_palette", {
        "bg": "#1a110e", "fg": "#f1dfd9", "accent": "#ffb59b",
        "error": "#ffb4ab", "warning": "#e7bdaf", "success": "#d5c68e", "muted": "#53433e"
    })
    theme: dict[str, str] = dict(default_palette)

    search_paths = GLOBAL_CONFIG.get("ui", {}).get("theme_paths", [
        ".config/matugen/generated/dusky_tui.json",
        ".config/matugen/generated_fresh/dusky_tui.json",
    ])

    for raw in search_paths:
        theme_path = Path(raw).expanduser()
        if not theme_path.is_absolute():
            theme_path = user_home() / theme_path

        if theme_path.is_file():
            try:
                data = json.loads(theme_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    theme.update({str(k): str(v) for k, v in data.items()})
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


sidebar_w = GLOBAL_CONFIG.get("ui", {}).get("sidebar_width", 35)
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
    border: none; padding: 0 0 0 1;
    scrollbar-size-vertical: 1;
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

    for index, task in enumerate(tasks):
        if task.mode == 'GIT':
            continue

        task.conflict_note = ""

        script = task.name
        matches = []

        if "/" in script:
            explicit_path = Path(script)
            if not explicit_path.is_absolute():
                explicit_path = WORK_TREE / explicit_path
            if explicit_path.is_file() and os.access(explicit_path, os.R_OK):
                matches.append(explicit_path)
        else:
            for d in profile.search_dirs:
                dir_path = WORK_TREE / d
                candidate = dir_path / script
                if candidate.is_file() and os.access(candidate, os.R_OK):
                    matches.append(candidate)

        if len(matches) == 0:
            task.resolved_path = Path(script)
            task.path_state = "missing"
            task.checksum = ""
            task.state_key = hashlib.blake2b(f"{task.mode}|{task.name}|{shlex.join(task.args)}".encode("utf-8")).hexdigest()
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
                    task.resolved_path = Path(script)
                    task.path_state = "missing"
                    task.checksum = ""
                    task.state_key = hashlib.blake2b(f"{task.mode}|{task.name}|{shlex.join(task.args)}".encode("utf-8")).hexdigest()
                    continue
            else:
                hashes = {m: file_checksum(m) for m in matches}
                unique_hashes = set(hashes.values())
                if len(unique_hashes) == 1:
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

        task.resolved_path = script_path
        task.path_state = "ok"
        task.checksum = file_checksum(script_path)
        task.state_key = hashlib.blake2b(f"{task.mode}|{task.name}|{shlex.join(task.args)}".encode("utf-8")).hexdigest()

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


def last_git_diff_path() -> Path:
    return runtime_dir() / "last_git_diff.json"


def persist_last_git_diff(payload: dict) -> None:
    try:
        last_git_diff_path().write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def remove_last_git_diff() -> None:
    try:
        last_git_diff_path().unlink(missing_ok=True)
    except Exception:
        pass


def _sync_copy_file(src_p: Path, dest_p: Path) -> bool:
    try:
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        if src_p.is_symlink():
            target = os.readlink(src_p)
            if dest_p.is_symlink() or dest_p.exists():
                dest_p.unlink(missing_ok=True)
            os.symlink(target, dest_p)
        else:
            shutil.copy2(src_p, dest_p, follow_symlinks=False)
        return True
    except OSError:
        return False


# ==============================================================================
#  SELF-HEALING GATES (never let a broken script silence the updater)
# ==============================================================================
def _validate_script_syntax(path: Path) -> tuple[bool, str]:
    """Quick syntax gate for managed Python (.py) and shell (.sh) files."""
    if not path.exists():
        return False, "file missing"
    suffix = path.suffix.lower()
    if suffix == ".py":
        try:
            subprocess.run(
                [sys.executable, "-m", "py_compile", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
                check=True,
            )
            return True, ""
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"python syntax failed: {e}"
    if suffix == ".sh":
        try:
            subprocess.run(
                ["bash", "-n", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
                check=True,
            )
            return True, ""
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"bash syntax failed: {e}"
    return True, ""


def last_good_dir() -> Path:
    d = backups_dir() / "last_good"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def store_last_good_self() -> None:
    """Keep a pristine copy of the updater every time a sync run completes."""
    try:
        candidate = last_good_dir()
        self_copy = Path(__file__)
        if not _validate_script_syntax(self_copy)[0]:
            return
        _sync_copy_file(self_copy, candidate / "update_dusky.py")
        _sync_copy_file(self_copy, candidate / "update_dusky.py.latest")
    except Exception:
        log("WARN", "Could not store last-good updater copy.")


def restore_last_good_self() -> str:
    """Repair the running updater if it was corrupted by a bad sync.

    Returns a human-readable summary of what was (or wasn't) done."""
    self_copy = Path(__file__)
    ok, why = _validate_script_syntax(self_copy)
    if ok:
        return ""
    saved = last_good_dir() / "update_dusky.py"
    if saved.exists() and _validate_script_syntax(saved)[0]:
        _sync_copy_file(saved, self_copy)
        return f"Healed: restored {self_copy.name} from last-good copy."
    return (f"Cannot self-heal: {self_copy.name} is invalid ({why}) and no "
            f"last-good copy exists at {saved}.")


class GitEngine:
    def __init__(self, app: App, profile: ProfileConfig):
        self.app = app
        self.profile = profile
        self.log = app.log_main  # type: ignore
        self.git_cmd_base = ['git', f'--git-dir={GIT_DIR}', f'--work-tree={WORK_TREE}']
        self._last_collision_count = 0
        self._last_collision_dir = ""
        backups_dir().mkdir(parents=True, exist_ok=True)

    async def _run(self, *args: str, check: bool = True, task_idx: int = -1) -> tuple[int, str, str]:
        cmd = self.git_cmd_base + list(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_git_env()
        )
        stdout, stderr = await proc.communicate()
        out, err = stdout.decode('utf-8', errors='replace').strip(), stderr.decode('utf-8', errors='replace').strip()

        if task_idx != -1 and err:
            self.app.log_task(escape(err), task_idx)  # type: ignore

        if proc.returncode != 0 and check:
            msg = f"[bold {THEME['error']}]Git Architecture Error ({proc.returncode}):[/] {escape(err)}"
            self.log(msg)
            if task_idx != -1:
                self.app.log_task(msg, task_idx)  # type: ignore
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=out, stderr=err)
        return proc.returncode, out, err

    async def _run_raw(self, *args: str, timeout_sec: int = 0) -> tuple[int, str, str]:
        cmd = self.git_cmd_base + list(args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=_git_env(), start_new_session=True
            )
            if timeout_sec > 0:
                try:
                    async with asyncio.timeout(timeout_sec):
                        stdout, stderr = await proc.communicate()
                except TimeoutError:
                    with suppress(ProcessLookupError, PermissionError, OSError):
                        os.killpg(proc.pid, signal.SIGKILL)
                        await proc.wait()
                    return 124, "", "timeout"
            else:
                stdout, stderr = await proc.communicate()

            return (proc.returncode,
                    stdout.decode('utf-8', errors='replace').strip(),
                    stderr.decode('utf-8', errors='replace').strip())
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
            try:
                async with asyncio.timeout(timeout_sec):
                    stdout, _ = await proc.communicate()
            except TimeoutError:
                with suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()
                return 124, b""
            return proc.returncode, stdout
        except Exception:
            return 1, b""

    def _tlog(self, msg: str, idx: int, also_main: bool = False):
        self.app.log_task(msg, idx)  # type: ignore
        if also_main:
            self.log(msg)

    async def _gate_incoming_scripts(self, changed_paths: list[str], idx: int, local_head: str = "") -> None:
        """Self-healing gate: validate every .py / .sh the sync just landed.

        A broken script that reaches the worktree would fail on the next user
        run with no explanation and no recovery path. Instead we restore the
        file from the previous local commit whenever the incoming version is
        syntactically invalid, so the machine always keeps a runnable copy.
        """
        for rel in sorted(set(changed_paths)):
            if not rel.endswith((".py", ".sh")):
                continue
            target = Path(WORK_TREE) / rel
            ok, why = await asyncio.to_thread(_validate_script_syntax, target)
            if ok:
                continue
            if not local_head:
                self._tlog(
                    f"\n[bold {THEME['error']}]WARNING:[/] broken incoming script {rel} (no previous local HEAD)\n"
                    f"    Reason: {why} — left in place, review it manually.",
                    idx,
                    True,
                )
                continue
            rc_old, old_bytes = await self._run_raw_bytes("show", f"{local_head}:{rel}", timeout_sec=30)
            if rc_old == 0 and old_bytes:
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(old_bytes)
                    self._tlog(
                        f"\n[bold {THEME['warning']}]Invalid incoming update blocked:[/] {rel}\n"
                        f"    Restored your last working version from your previous local commit.\n    Reason: {why}",
                        idx,
                        True,
                    )
                    continue
                except OSError as e:
                    self._tlog(f"[bold {THEME['error']}]Restore failed for {rel}: {e}[/]", idx, True)
            self._tlog(
                f"\n[bold {THEME['error']}]WARNING:[/] broken incoming script {rel} (no valid previous HEAD)\n"
                f"    Reason: {why} — left in place, review it manually.",
                idx,
                True,
            )

    async def _unstage_managed_paths(self) -> None:
        """
        Safeguards internal directories (backups, logs, state) from git tracking hazards.
        If a user ran 'git add .', these untracked files enter the index. A subsequent
        'git reset --hard' would physically delete them (data loss), and diff-index would
        capture them as user modifications (backup loop).

        By syncing the index to HEAD for these paths, new backup files become purely untracked,
        which secures them against deletion and capture, while leaving any explicitly
        tracked files (like a .keep file) completely intact.
        """
        paths = []
        for d in (backups_dir(), logs_dir(), state_dir(), askpass_dir(), runtime_dir()):
            try:
                rel = d.relative_to(WORK_TREE)
                paths.append(str(rel))
            except ValueError:
                pass

        if not paths:
            return

        rc, raw_local, _ = await self._run_raw('rev-parse', '--verify', '-q', 'HEAD')

        if rc == 0 and raw_local.strip():
            await self._run_raw('reset', '-q', 'HEAD', '--', *paths)
        else:
            # Unborn repo: no tracked files exist yet, so rm --cached is strictly safe
            await self._run_raw('rm', '--cached', '-r', '--ignore-unmatch', '--quiet', '--', *paths)

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

    async def _get_repo_state(self, task_idx: int) -> str:
        if GIT_DIR.is_symlink():
            self._tlog(f"[bold {THEME['error']}]GIT_DIR must not be a symlink: {GIT_DIR}[/]", task_idx, True)
            return 'invalid'
        if not GIT_DIR.exists():
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
        while lock_name != 'none':
            lock_path = GIT_DIR / lock_name
            self._tlog(f"[bold {THEME['warning']}]Git lock detected: {lock_path}[/]", task_idx, True)
            try:
                lock_age = int(time.time() - lock_path.stat().st_mtime)
            except OSError:
                lock_age = 0

            lock_open = False
            with suppress(OSError):
                lock_real = str(lock_path)
                lock_real = str(lock_path.resolve())
                for pid_dir in os.listdir("/proc"):
                    if not pid_dir.isdigit():
                        continue
                    fd_dir = f"/proc/{pid_dir}/fd"
                    with suppress(OSError):
                        for fd_name in os.listdir(fd_dir):
                            with suppress(OSError):
                                if os.readlink(os.path.join(fd_dir, fd_name)) == lock_real:
                                    lock_open = True
                                    break
                    if lock_open:
                        break

            if lock_open:
                self._tlog(f"[bold {THEME['error']}]Lock file is held by a live process. Refusing to remove.[/]", task_idx, True)
                return 'invalid'

            if lock_age <= 60:
                self._tlog(f"[bold {THEME['error']}]Lock file is too recent ({lock_age}s). Refusing to auto-remove.[/]", task_idx, True)
                return 'invalid'

            try:
                lock_path.unlink()
                self._tlog(f"[bold {THEME['success']}]Stale lock cleared ({lock_age}s old): {lock_name}[/]", task_idx, True)
            except Exception:
                self._tlog(f"[bold {THEME['error']}]Failed to remove stale lock: {lock_path}[/]", task_idx, True)
                return 'invalid'

            new_lock = self._detect_git_lock_state()
            if new_lock == lock_name:
                self._tlog(f"[bold {THEME['error']}]Lock persists after removal attempt: {lock_path}[/]", task_idx, True)
                return 'invalid'
            lock_name = new_lock

        rc, _, _ = await self._run_raw('rev-parse', '--git-dir')
        if rc != 0:
            self._tlog(f"[bold {THEME['error']}]Repository metadata invalid or corrupted: {GIT_DIR}[/]", task_idx, True)
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
                async with asyncio.timeout(FETCH_TIMEOUT):
                    stdout, _ = await proc.communicate()
                output = stdout.decode('utf-8', errors='replace').strip()
                if output:
                    self._tlog(f"[dim]{escape(output)}[/dim]", task_idx)
                rc = proc.returncode
            except TimeoutError:
                with suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()
                rc = 124
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
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._tlog(f"[dim]Clone attempt {attempt}/{MAX_ATTEMPTS}...[/dim]", task_idx)
            cmd = ['git', 'clone', '--bare', '--branch', self.profile.branch, self.profile.repo_url, str(GIT_DIR)]
            try:
                proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=_git_env(), start_new_session=True)
                async with asyncio.timeout(CLONE_TIMEOUT):
                    stdout, _ = await proc.communicate()
                output = stdout.decode('utf-8', errors='replace').strip()
                if output:
                    self._tlog(f"[dim]{escape(output)}[/dim]", task_idx)
                rc = proc.returncode
            except TimeoutError:
                with suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()
                rc = 124
            if rc == 0:
                await self._run_raw('config', 'remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*')
                return True
            if GIT_DIR.exists():
                shutil.rmtree(str(GIT_DIR), ignore_errors=True)
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
        rc, ls_tree, _ = await self._run_raw('ls-tree', '-r', '-z', '--name-only', ref)
        incoming = [f for f in ls_tree.split('\0') if f]

        tracked_exact: dict = {}
        tracked_descendants: dict = {}
        if honor_tracked:
            _, ls_files, _ = await self._run_raw('ls-files', '-z')
            for f in ls_files.split('\0'):
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

        required_bytes = sum(
            path_copy_size_bytes(WORK_TREE / r)
            for r in collision_roots
            if (WORK_TREE / r).exists() or (WORK_TREE / r).is_symlink()
        )
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

        with suppress(Exception):
            (backup_dir / "INFO.txt").write_text(
                f"Dusky work-tree collision backup\nCreated: {RUN_TIMESTAMP}\nRef: {ref}\nWork tree: {WORK_TREE}\n"
            )
            (backup_dir / "INFO.txt").chmod(0o600)

        moved_log = backup_dir / "MOVED_PATHS.txt"
        with suppress(Exception):
            moved_log.write_text("")
            moved_log.chmod(0o600)

        self._tlog(f"[bold {THEME['warning']}]{len(collision_roots)} work-tree collision(s) found. Backing up...[/]", task_idx, True)
        for coll_rel in collision_roots:
            coll_src = WORK_TREE / coll_rel
            if not (coll_src.exists() or coll_src.is_symlink()):
                continue
            coll_dest = backup_dir / coll_rel
            coll_dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(coll_src), str(coll_dest))
                self._tlog(f"[dim]  → Backed up collision: {escape(coll_rel)}[/dim]", task_idx)
                with suppress(Exception):
                    with open(moved_log, "a") as mf:
                        mf.write(f"{coll_rel}\n")
            except Exception as e:
                self._tlog(f"[bold {THEME['error']}]Failed to move collision {escape(coll_rel)}: {escape(str(e))}[/]", task_idx, True)
                return False

        self._tlog(f"[bold {THEME['success']}]Collisions backed up → {backup_dir}[/]", task_idx, True)
        return True

    async def _capture_tracked_changes(self) -> tuple[list, dict, dict, dict]:
        await self._run_raw('update-index', '-q', '--refresh')
        rc, raw, _ = await self._run_raw('diff-index', '--raw', '--no-renames', '-z', 'HEAD', '--')

        paths: list = []
        status_map: dict = {}
        old_mode_map: dict = {}
        old_oid_map: dict = {}

        if rc != 0 or not raw.strip():
            return paths, status_map, old_mode_map, old_oid_map

        records = raw.split('\0')
        i = 0
        while i + 1 < len(records):
            meta = records[i].lstrip(':')
            path = records[i + 1]
            i += 2
            if not meta or not path:
                continue
            parts = meta.split()
            if len(parts) < 5:
                continue
            oldmode, _, oldoid, _, status = parts[0], parts[1], parts[2], parts[3], parts[4]
            status = status.rstrip('0123456789')
            paths.append(path)
            status_map[path] = status
            old_mode_map[path] = oldmode
            old_oid_map[path] = oldoid

        return paths, status_map, old_mode_map, old_oid_map

    async def _backup_user_modifications(self, change_paths: list, change_status: dict, task_idx: int) -> Path | None:
        if not change_paths:
            return None

        backup_base = backups_dir()
        required_bytes = sum(
            path_copy_size_bytes(WORK_TREE / p)
            for p in change_paths
            if change_status.get(p) != 'D' and ((WORK_TREE / p).exists() or (WORK_TREE / p).is_symlink())
        )
        if not check_disk_space(backup_base):
            return None
        if not ensure_free_space_for_bytes(backup_base, required_bytes, "modified-files backup"):
            return None

        backup_dir = make_private_dir_under(backup_base, f"your_changes_{RUN_TIMESTAMP}")
        if not backup_dir:
            self._tlog(f"[bold {THEME['error']}]Failed to create user-mods backup dir[/]", task_idx, True)
            return None

        manifest = backup_dir / "MANIFEST.txt"
        try:
            manifest.write_text("")
            manifest.chmod(0o600)
        except Exception:
            return None

        for path in change_paths:
            st = change_status.get(path, "?")
            src = WORK_TREE / path
            if st == 'D' or not (src.exists() or src.is_symlink()):
                with suppress(Exception):
                    with open(manifest, "a") as mf:
                        mf.write(f"status={st} has_copy=0 path={path}\n")
                continue
            dest = backup_dir / path
            ok = await asyncio.to_thread(_sync_copy_file, src, dest)
            if not ok:
                self._tlog(f"[bold {THEME['error']}]Backup failed for: {escape(path)}[/]", task_idx, True)
                return None
            with open(manifest, "a") as mf:
                mf.write(f"status={st} has_copy=1 path={path}\n")

        self._tlog(f"[bold {THEME['success']}]Backed up {len(change_paths)} tracked change(s) → {backup_dir}[/]", task_idx, True)
        return backup_dir

    async def _backup_full_tracked_tree(self, task_idx: int) -> Path | None:
        backup_base = backups_dir()
        _, ls_files, _ = await self._run_raw('ls-files', '-z')
        tracked = [f for f in ls_files.split('\0') if f]
        required_bytes = sum(
            path_copy_size_bytes(WORK_TREE / p)
            for p in tracked
            if (WORK_TREE / p).exists() or (WORK_TREE / p).is_symlink()
        )
        if not check_disk_space(backup_base):
            return None
        if not ensure_free_space_for_bytes(backup_base, required_bytes, "full tracked-tree backup"):
            return None

        backup_dir = make_private_dir_under(backup_base, f"full_snapshot_{RUN_TIMESTAMP}")
        if not backup_dir:
            self._tlog(f"[bold {THEME['error']}]Failed to create full tracked-tree backup dir[/]", task_idx, True)
            return None

        with suppress(Exception):
            _, head, _ = await self._run_raw('rev-parse', 'HEAD')
            info = backup_dir / "INFO.txt"
            info.write_text(f"Dusky full tracked-tree backup\nCreated: {RUN_TIMESTAMP}\nHEAD: {head.strip()}\n")
            info.chmod(0o600)

        def _sync_copy_tree(tracked_files, work_tree, b_dir):
            success_count = 0
            for p in tracked_files:
                s = work_tree / p
                d = b_dir / p
                if not (s.exists() or s.is_symlink()):
                    continue
                d.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if s.is_symlink():
                        target = os.readlink(s)
                        if d.is_symlink() or d.exists():
                            d.unlink(missing_ok=True)
                        os.symlink(target, d)
                    else:
                        shutil.copy2(s, d, follow_symlinks=False)
                    success_count += 1
                except OSError:
                    pass
            return success_count

        copied = await asyncio.to_thread(_sync_copy_tree, tracked, WORK_TREE, backup_dir)

        self._tlog(f"[bold {THEME['success']}]Full tracked-tree backup: {backup_dir} ({copied} file(s))[/]", task_idx, True)
        return backup_dir

    async def _backup_git_history(self, task_idx: int) -> Path | None:
        backup_base = backups_dir()
        required_bytes = path_copy_size_bytes(GIT_DIR)
        if not check_disk_space(backup_base):
            return None
        if not ensure_free_space_for_bytes(backup_base, required_bytes, "Git history backup"):
            return None

        backup_root = make_private_dir_under(backup_base, f"repo_history_{RUN_TIMESTAMP}")
        if not backup_root:
            self._tlog(f"[bold {THEME['error']}]Failed to create Git history backup dir[/]", task_idx, True)
            return None

        backup_repo = backup_root / "repo.git"
        try:
            proc = await asyncio.create_subprocess_exec('cp', '-a', '--reflink=auto', str(GIT_DIR), str(backup_repo))
            await proc.wait()
            if proc.returncode != 0:
                self._tlog(f"[bold {THEME['error']}]Failed to copy Git history[/]", task_idx, True)
                return None
        except Exception as e:
            self._tlog(f"[bold {THEME['error']}]Exception copying git dir: {escape(str(e))}[/]", task_idx, True)
            return None

        with suppress(Exception):
            info = backup_root / "INFO.txt"
            info.write_text(f"Dusky Git history backup\nCreated: {RUN_TIMESTAMP}\nSource: {GIT_DIR}\n")
            info.chmod(0o600)

        self._tlog(f"[bold {THEME['success']}]Git history preserved → {backup_root}[/]", task_idx, True)
        return backup_root

    async def _get_head_path_meta(self, path: str) -> tuple[str, str]:
        rc, record, _ = await self._run_raw('ls-tree', '-z', 'HEAD', '--', path)
        if rc != 0 or not record.strip():
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
        all_ok = True

        for path in change_paths:
            status = change_status.get(path, "?")
            old_oid = change_old_oid.get(path, "")
            old_mode = change_old_mode.get(path, "")
            backup_src = backup_dir / path
            target = WORK_TREE / path

            new_mode, new_oid = await self._get_head_path_meta(path)
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
                    action = "delete-restored"
            else:
                has_copy = backup_src.exists() or backup_src.is_symlink()
                if not has_copy:
                    continue
                if old_oid_valid:
                    safe = same_meta or not new_oid
                else:
                    safe = not new_oid
                action = "restore" if safe else "merge"

            if action == "delete-preserved":
                deletion_count += 1

            elif action == "delete-safe":
                try:
                    if target.exists() or target.is_symlink():
                        if target.is_dir() and not target.is_symlink():
                            shutil.rmtree(str(target))
                        else:
                            target.unlink()
                    deletion_count += 1
                    self._tlog(f"[dim]  → Re-applied tracked deletion: {escape(path)}[/dim]", task_idx)
                except Exception as e:
                    self._tlog(f"[bold {THEME['error']}]Failed to re-apply deletion {escape(path)}: {escape(str(e))}[/]", task_idx, True)
                    all_ok = False

            elif action == "delete-restored":
                restore_count += 1
                self._tlog(f"[dim]  → Restored: {escape(path)} (accepting upstream's new version over local deletion)[/dim]", task_idx)

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
                        self._tlog(f"[dim]  → Upstream changed: {escape(path)} (your version saved for merge)[/dim]", task_idx)
                    else:
                        self._tlog(f"[bold {THEME['error']}]Failed to save merge copy: {escape(path)}[/]", task_idx, True)
                        all_ok = False
                except Exception as e:
                    self._tlog(f"[bold {THEME['error']}]Exception saving merge copy {escape(path)}: {escape(str(e))}[/]", task_idx, True)
                    all_ok = False

            elif action == "restore":
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with tempfile.TemporaryDirectory(prefix=f".{target.name}.dtmp.", dir=target.parent) as tmpdir:
                        tmp_file = Path(tmpdir) / target.name
                        ok = await asyncio.to_thread(_sync_copy_file, backup_src, tmp_file)
                        if ok:
                            if target.exists() or target.is_symlink():
                                displaced = Path(tmpdir) / (".old_" + target.name)
                                shutil.move(str(target), str(displaced))
                            shutil.move(str(tmp_file), str(target))
                            restore_count += 1
                            self._tlog(f"[dim]  → Restored: {escape(path)}[/dim]", task_idx)
                        else:
                            self._tlog(f"[bold {THEME['error']}]Restore failed for: {escape(path)}[/]", task_idx, True)
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

        if all_ok:
            shutil.rmtree(str(backup_dir), ignore_errors=True)

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
            # Self-heal gate: if a previous sync corrupted the updater itself,
            # repair from the last-good copy before doing anything else.
            self_heal_note = restore_last_good_self()
            if self_heal_note:
                self.log(f"[bold {THEME['warning']}][SELF-HEAL][/] {self_heal_note}")

            # Task 0: Bare Repo Validation
            idx = 0
            self.app.update_task_state(idx, "running")  # type: ignore
            self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Bare Repository Validation\n", idx)

            repo_state = await self._get_repo_state(idx)

            if repo_state == 'absent':
                self._tlog(f"[bold {THEME['warning']}]Bare repository missing. Cloning from upstream...[/]", idx, True)
                if not await self._clone_with_retry(idx):
                    raise RuntimeError("Clone sequence failed.")
                await self._ensure_repo_defaults()

                self._tlog("[dim]Checking out files into work-tree...[/dim]", idx)
                if not await self._backup_worktree_collisions('HEAD', honor_tracked=False, task_idx=idx):
                    raise RuntimeError("Collision backup failed during initial checkout.")

                rc, _, err = await self._run_raw('checkout')
                if rc != 0:
                    self._tlog(f"[bold {THEME['error']}]Checkout failed: {escape(err)}[/]", idx, True)
                    raise RuntimeError("Work-tree checkout failed.")

                meta.update(status="cloned")
                self.app.git_summary = meta
                self._tlog(f"[bold {THEME['success']}]Repository cloned and checked out successfully.[/]", idx, True)
                self.app.update_task_state(idx, "success")  # type: ignore
                for i in range(1, 5):
                    self.app.update_task_state(i, "skipped")  # type: ignore
                return True

            elif repo_state == 'invalid':
                raise RuntimeError("Repository is in an invalid or unsafe state.")

            self._tlog(f"[bold {THEME['success']}]Bare repository integrity verified.[/]", idx)
            self.app.update_task_state(idx, "success")  # type: ignore
            if self.app.run_logger:
                self.app.run_logger.close_task(self.app.tasks[idx], idx, "completed", 0, 0.0)

            # Task 1: Fetch Upstream & Diff
            idx = 1
            self.app.update_task_state(idx, "running")  # type: ignore
            self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Fetch Upstream & Diff\n", idx)

            # Purge internal managed paths from the index to prevent backup loops and data loss
            await self._unstage_managed_paths()

            op = self._detect_git_operation_state()
            if op != 'none':
                self._tlog(f"[bold {THEME['error']}]Git {op} is in progress. Resolve it manually first.[/]", idx, True)
                raise RuntimeError(f"Git {op} in progress.")

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

            if not local_head:
                self._tlog(f"[bold {THEME['warning']}]Local repository has no commits yet. Initializing from upstream...[/]", idx, True)
                rc1, _, _ = await self._run_raw('symbolic-ref', 'HEAD', f'refs/heads/{self.profile.branch}')
                if rc1 != 0:
                    raise RuntimeError("Failed to point HEAD at branch.")
                if not await self._backup_worktree_collisions(UPSTREAM_TRACKING_REF, honor_tracked=False, task_idx=idx):
                    raise RuntimeError("Collision backup failed during unborn init.")
                rc2, _, err2 = await self._run_raw('reset', '--hard', UPSTREAM_TRACKING_REF)
                if rc2 != 0:
                    self._tlog(f"[bold {THEME['error']}]Failed to init unborn repo: {escape(err2)}[/]", idx, True)
                    raise RuntimeError("Reset of unborn repo failed.")
                await self._ensure_repo_defaults()
                self._tlog(f"[bold {THEME['success']}]Repository synchronized (initial bootstrap).[/]", idx, True)
                self.app.update_task_state(idx, "success")  # type: ignore
                for i in range(2, 5):
                    self.app.update_task_state(i, "skipped")  # type: ignore
                return True

            if local_head == remote_head:
                await self._unstage_managed_paths()
                change_paths, change_status, change_old_mode, change_old_oid = await self._capture_tracked_changes()
                if not change_paths:
                    meta.update(status="up_to_date", before_head=local_head, after_head=remote_head)
                    self.app.git_summary = meta
                    self._tlog(f"[bold {THEME['success']}]Repository synchronization perfect. Origin matched.[/]", idx, True)
                    await self._ensure_repo_defaults()
                    self.app.update_task_state(idx, "success")  # type: ignore
                    if self.app.run_logger:
                        self.app.run_logger.close_task(self.app.tasks[idx], idx, "completed", 0, 0.0)
                    for i in range(2, 5):
                        self.app.update_task_state(i, "skipped")  # type: ignore
                        if self.app.run_logger:
                            self.app.run_logger.close_task(self.app.tasks[i], i, "skipped", 0, 0.0)
                    return True
                meta.update(status="up_to_date_with_mods", before_head=local_head, after_head=remote_head, local_mods=len(change_paths))
                self.app.git_summary = meta
                self._tlog(f"[bold {THEME['accent']}]Origin matched, but work-tree has {len(change_paths)} tracked change(s). Processing...[/]", idx, True)

            rc, commit_count_raw, _ = await self._run_raw('rev-list', '--count', f'{local_head}..{remote_head}')
            commit_count = commit_count_raw.strip() or "?"
            rc, changed_raw, _ = await self._run_raw('diff', '--name-only', f'{local_head}..{remote_head}')
            changed_files = [f for f in changed_raw.split('\n') if f.strip()]
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

                if not await self._backup_worktree_collisions(UPSTREAM_TRACKING_REF, honor_tracked=True, task_idx=idx):
                    raise RuntimeError("Collision backup failed.")
                meta.update(
                    collisions=self._last_collision_count,
                    collision_backup=self._last_collision_dir,
                )
                self.app.update_task_state(idx, "success")  # type: ignore

                # Task 3: Atomic Snapshot (CoW)
                idx = 3
                self.app.update_task_state(idx, "running")  # type: ignore
                self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Atomic Snapshot (CoW)\n", idx)

                full_snapshot_dir = await self._backup_full_tracked_tree(idx)
                if not full_snapshot_dir:
                    raise RuntimeError("Full tracked-tree backup failed.")

                await self._unstage_managed_paths()
                change_paths, change_status, change_old_mode, change_old_oid = await self._capture_tracked_changes()
                if change_paths:
                    your_changes_backup = await self._backup_user_modifications(change_paths, change_status, idx)
                    if your_changes_backup is None:
                        raise RuntimeError("User modifications backup failed.")
                else:
                    self._tlog(f"[bold {THEME['success']}]No local tracked modifications found. Snapshot skipped.[/]", idx)
                meta.update(
                    full_tracked_backup=str(full_snapshot_dir),
                    local_mods=len(change_paths),
                    local_mods_backup=str(your_changes_backup) if your_changes_backup else "",
                )

                self.app.update_task_state(idx, "success")  # type: ignore

                # Task 4: Apply Bare Updates (Reset)
                idx = 4
                self.app.update_task_state(idx, "running")  # type: ignore
                self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Apply Bare Updates (Reset)\n", idx)

                rc_reset, _, err_reset = await self._run_raw('reset', '--hard', UPSTREAM_TRACKING_REF)
                if rc_reset != 0:
                    self._tlog(f"[bold {THEME['error']}]Reset failed: {escape(err_reset)}[/]", idx, True)
                    raise RuntimeError(f"Reset failed (rc={rc_reset}).")

                self._tlog(f"[bold {THEME['success']}]Bare Repository reset applied and synchronized.[/]", idx, True)

                await self._gate_incoming_scripts(changed_files, idx, local_head)

                if your_changes_backup and change_paths:
                    self._tlog(f"[bold {THEME['accent']}]Restoring your tracked modifications...[/]", idx)
                    restore_ok = await self._restore_user_modifications(
                        your_changes_backup, change_paths, change_status, change_old_mode, change_old_oid, idx
                    )
                    if not restore_ok:
                        self._tlog(f"[bold {THEME['warning']}]Some files could not be restored. Backup preserved at: {your_changes_backup}[/]", idx, True)

                await self._ensure_repo_defaults()
                meta.update(
                    status="unrelated_reset",
                    unrelated_histories=True,
                    after_head=remote_head,
                    local_mods_restored=restore_ok,
                )
                self.app.git_summary = meta
                if meta.get("diff") or meta.get("commits"):
                    persist_last_git_diff(meta)
                self.app.update_task_state(idx, "success")  # type: ignore
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
                self.app.run_logger.close_task(self.app.tasks[idx], idx, "completed", 0, 0.0)

            # Task 2: Forensic Collision Backup
            idx = 2
            self.app.update_task_state(idx, "running")  # type: ignore
            self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Forensic Collision Backup\n", idx)

            if not await self._backup_worktree_collisions(UPSTREAM_TRACKING_REF, honor_tracked=True, task_idx=idx):
                raise RuntimeError("Collision backup failed.")
            meta.update(
                collisions=self._last_collision_count,
                collision_backup=self._last_collision_dir,
            )
            self.app.update_task_state(idx, "success")  # type: ignore
            if self.app.run_logger:
                self.app.run_logger.close_task(self.app.tasks[idx], idx, "completed", 0, 0.0)

            # Task 3: Atomic Snapshot (CoW)
            idx = 3
            self.app.update_task_state(idx, "running")  # type: ignore
            self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Atomic Snapshot (CoW)\n", idx)

            await self._unstage_managed_paths()
            change_paths, change_status, change_old_mode, change_old_oid = await self._capture_tracked_changes()
            if change_paths:
                your_changes_backup = await self._backup_user_modifications(change_paths, change_status, idx)
                if your_changes_backup is None:
                    raise RuntimeError("User modifications backup failed.")
            else:
                self._tlog(f"[bold {THEME['success']}]No local tracked modifications found. Snapshot skipped.[/]", idx)
            meta.update(
                local_mods=len(change_paths),
                local_mods_backup=str(your_changes_backup) if your_changes_backup else "",
            )

            self.app.update_task_state(idx, "success")  # type: ignore
            if self.app.run_logger:
                self.app.run_logger.close_task(self.app.tasks[idx], idx, "completed", 0, 0.0)

# Task 4: Apply Reset
            idx = 4
            self.app.update_task_state(idx, "running")  # type: ignore
            self._tlog(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] Apply Bare Updates (Reset)\n", idx)

            rc_reset, _, err_reset = await self._run_raw('reset', '--hard', UPSTREAM_TRACKING_REF)
            if rc_reset != 0:
                self._tlog(f"[bold {THEME['error']}]Reset failed: {escape(err_reset)}[/]", idx, True)
                raise RuntimeError(f"Reset failed (rc={rc_reset}).")

            self._tlog(f"[bold {THEME['success']}]Bare Repository reset applied and synchronized.[/]", idx, True)

            await self._gate_incoming_scripts(changed_files, idx, local_head)

            if your_changes_backup and change_paths:
                self._tlog(f"[bold {THEME['accent']}]Restoring your tracked modifications...[/]", idx)
                restore_ok = await self._restore_user_modifications(
                    your_changes_backup, change_paths, change_status, change_old_mode, change_old_oid, idx
                )
                if not restore_ok:
                    self._tlog(f"[bold {THEME['warning']}]Some files could not be restored. Backup preserved at: {your_changes_backup}[/]", idx, True)

            await self._ensure_repo_defaults()
            final_st = meta.get("status")
            if final_st not in ("up_to_date_with_mods", "unrelated_reset"):
                final_st = "updated" if (local_head and remote_head and local_head != remote_head) else "up_to_date_with_mods"

            meta.update(
                status=final_st,
                after_head=remote_head,
                local_mods_restored=restore_ok,
            )
            self.app.git_summary = meta
            if meta.get("diff") or meta.get("commits"):
                persist_last_git_diff(meta)
            self.app.update_task_state(idx, "success")  # type: ignore
            if self.app.run_logger:
                self.app.run_logger.close_task(self.app.tasks[idx], idx, "completed", 0, 0.0)
            return True

        except Exception as e:
            err_msg = f"[bold {THEME['error']}][FATAL][/] Git Sync Failure: {escape(str(e))}"
            self.log(err_msg)
            for i in range(5):
                st = self.app.tasks[i].status  # type: ignore
                if st == "running":
                    self.app.update_task_state(i, "failed")  # type: ignore
                elif st == "pending":
                    self.app.update_task_state(i, "skipped")  # type: ignore
            return False


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
        limit = GLOBAL_CONFIG.get("ui", {}).get("search_result_limit", 200)

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

        options: list[Option] = []
        for i, line in enumerate(self.lines):
            clean = ANSI_STRIP_REGEX.sub("", line)
            if q in clean.lower():
                txt = Text()
                txt.append(f"{i + 1:5d}  ", style="dim")
                txt.append(clean.strip())
                options.append(Option(txt))

        ol.add_options(options[:300])

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class ConfirmQuitScreen(ModalScreen[str]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("y,a,enter", "confirm_abort", "Abort", priority=True),
        Binding("n,c,q", "cancel", "Cancel", priority=True),
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
        if key in ("a", "y", "enter", "space"):
            self.dismiss("abort")
        elif key in ("c", "n", "escape", "q"):
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
            text.append("  q / Ctrl+Q / Ctrl+Z   Quit / Abort confirmation dialog\n\n")

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
    BINDINGS = [
        Binding("ctrl+f", "open_search", "Search Tasks", priority=True),
        Binding("ctrl+l", "search_log", "Search Log", priority=True),
        Binding("ctrl+q", "request_quit", "Quit", priority=True),
        Binding("q", "request_quit", "Quit", priority=True),
        Binding("escape", "request_quit", "Quit", priority=True),
        Binding("ctrl+z", "request_quit", "Quit", priority=True),
        Binding("f1", "help", "Help", priority=True),
        Binding("question_mark", "help", "Help"),
        Binding("f", "cycle_filter", "Filter"),
        Binding("alt+left", "shrink_left_pane", "Shrink Sidebar", priority=True),
        Binding("alt+right", "expand_left_pane", "Expand Sidebar", priority=True),
        Binding("alt+h", "shrink_left_pane", "Shrink Sidebar", priority=True),
        Binding("alt+l", "expand_left_pane", "Expand Sidebar", priority=True),
        Binding("ctrl+left", "shrink_left_pane", "Shrink Sidebar", priority=True),
        Binding("ctrl+right", "expand_left_pane", "Expand Sidebar", priority=True),
        Binding("bracketleft", "shrink_left_pane", "Shrink Sidebar"),
        Binding("bracketright", "expand_left_pane", "Expand Sidebar"),
        Binding("j", "tree_down", "Tree Down", priority=True),
        Binding("k", "tree_up", "Tree Up", priority=True),
        Binding("up", "scroll_preview_up", "Scroll Log Up", priority=True),
        Binding("down", "scroll_preview_down", "Scroll Log Down", priority=True),
        Binding("pageup", "scroll_preview_page_up", "Page Up", priority=True),
        Binding("pagedown", "scroll_preview_page_down", "Page Down", priority=True),
        Binding("home", "scroll_preview_home", "Home", priority=True),
        Binding("end", "scroll_preview_end", "End", priority=True),
        Binding("tab", "toggle_focus", "Switch Focus", priority=True),
        Binding("shift+tab", "toggle_focus", "Switch Focus", priority=True),
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
        self._prompt_buffer: str = ""
        self._prompt_counts: dict[str, int] = {}
        self._prompt_last: dict[str, float] = {}
        self.state_store: StateStore | None = None
        self.sleep_inhibitor: SleepInhibitor | None = None
        self.heartbeat_task: asyncio.Task | None = None
        self.condition_evaluator = ConditionEvaluator()
        self.run_id: str = RUN_TIMESTAMP
        self.run_logger: RunLogger | None = None
        self.once_store: OnceStore | None = None
        self.missing_scripts: list[str] = []
        self.sidebar_width: int = GLOBAL_CONFIG.get("ui", {}).get("sidebar_width", 35)
        self.filter_mode: str = "all"
        self._log_lines: dict[int | str, deque[str]] = {}
        self._is_dragging_pane: bool = False
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
        self.progress = self.query_one("#main_progress", ProgressBar)

        self.sleep_inhibitor = SleepInhibitor(enabled=True)
        self.state_store = StateStore(self.profile)
        self.run_logger = RunLogger(self.profile, self.run_id)
        self.once_store = OnceStore()

        stored_durations = self.state_store.durations()
        for t in self.tasks:
            if t.state_key in stored_durations and stored_durations[t.state_key] > 0:
                t.duration = stored_durations[t.state_key]

        list_view = self.query_one("#task_list", ListView)
        list_view.append(MainLogItem())
        for i, task in enumerate(self.tasks):
            list_view.append(TaskItem(task, i))
        list_view.append(ReportLogItem())

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

        self.run_worker(self.execute_pipeline(), exclusive=True, thread=False)

    def on_unmount(self) -> None:
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
        if self.sleep_inhibitor:
            self.sleep_inhibitor.close()
        if self.state_store:
            self.state_store.close()
        if self.run_logger:
            self.run_logger.close()
        if self.once_store:
            self.once_store.close()

    def log_main(self, message: Any) -> None:
        self.query_one("#log-main", RichLog).write(message)
        if "main" not in self._log_lines:
            max_lines = GLOBAL_CONFIG.get("ui", {}).get("max_log_lines", 6000)
            self._log_lines["main"] = deque(maxlen=max_lines)
        self._log_lines["main"].append(str(message))

        if hasattr(message, "plain"):
            plain = message.plain
        elif hasattr(message, "code"):
            plain = message.code
        else:
            plain = strip_ansi(str(message))

        if self.run_logger and self.run_logger.enabled:
            for line in plain.splitlines():
                if line.strip():
                    self.run_logger.system(line)

        if LOG_FILE and GLOBAL_CONFIG.get("logging", {}).get("enabled", True):
            timestamp = datetime.now().strftime("%H:%M:%S")
            with suppress(OSError):
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    for line in plain.splitlines():
                        f.write(f"[{timestamp}] [MAIN   ] {line}\n")

    def log_task(self, message: Any, index: int) -> None:
        with suppress(Exception):
            self.query_one(f"#log-task-{index}", RichLog).write(message)
        if index not in self._log_lines:
            max_lines = GLOBAL_CONFIG.get("ui", {}).get("max_log_lines", 6000)
            self._log_lines[index] = deque(maxlen=max_lines)
        self._log_lines[index].append(str(message))

        if hasattr(message, "plain"):
            plain = message.plain
        elif hasattr(message, "code"):
            plain = message.code
        else:
            plain = strip_ansi(str(message))

        if self.run_logger and self.run_logger.enabled and 0 <= index < len(self.tasks):
            task = self.tasks[index]
            self.run_logger.write_task(task, index, plain)

    def _restore_last_git_diff(self) -> None:
        payload = None
        path = last_git_diff_path()
        try:
            if path.is_file():
                raw = path.read_text(encoding="utf-8")
                payload = json.loads(raw)
        except Exception:
            payload = None
        remove_last_git_diff()
        if not isinstance(payload, dict):
            return
        self.git_summary.update(payload)
        for i in range(5):
            self.update_task_state(i, "success")
            if self.run_logger:
                self.run_logger.close_task(self.tasks[i], i, "completed", 0, 0.0)
        diff = payload.get("diff") or ""
        commits = payload.get("commits", "?")
        files_changed = payload.get("files_changed", "?")
        branch = payload.get("branch") or self.profile.branch
        before = payload.get("before_head") or ""
        after = payload.get("after_head") or ""
        unrelated = bool(payload.get("unrelated_histories"))

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
            note = f"Collision backup performed during diverged-history recovery ({payload.get('collisions', 0)} collision(s))."
        elif payload.get("collisions") is None:
            note = "No collision-backup details recorded."
        elif payload.get("collisions") == 0:
            note = "No work-tree collisions detected."
        else:
            note = f"{payload.get('collisions')} work-tree collision(s) backed up."
            if payload.get("collision_backup"):
                note += f" Backup: {payload['collision_backup']}"
        self.log_task(f"[dim]{note}[/dim]", 2)

        self.log_task(f"\n[bold {THEME['accent']}]Atomic Snapshot (CoW)[/]", 3)
        if unrelated:
            note = "Full tracked-tree backup performed during diverged-history recovery."
            if payload.get("local_mods"):
                note += f" {payload['local_mods']} local tracked modification(s) backed up for restore."
                if payload.get("local_mods_backup"):
                    note += f" Backup: {payload['local_mods_backup']}"
        elif payload.get("local_mods") is None:
            note = "No snapshot details recorded."
        elif payload.get("local_mods") == 0:
            note = "No local tracked modifications found. Snapshot skipped."
        else:
            note = f"{payload.get('local_mods')} local tracked modification(s) backed up."
            if payload.get("local_mods_backup"):
                note += f" Backup: {payload['local_mods_backup']}"
        self.log_task(f"[dim]{note}[/dim]", 3)

        self.log_task(f"\n[bold {THEME['accent']}]Apply Bare Updates (Reset)[/]", 4)
        if unrelated:
            note = "Reset applied during diverged-history recovery."
        elif before or after:
            note = f"Reset applied: {short(before)} -> {short(after)}."
        else:
            note = "Reset applied and synchronized."
        if payload.get("local_mods_restored") is True:
            note += " Local modifications restored."
        elif payload.get("local_mods_restored") is False:
            note += " Some local modifications could not be restored (backup preserved)."
        elif payload.get("local_mods") == 0:
            note += " No local modifications to restore."
        self.log_task(f"[dim]{note}[/dim]", 4)

        self.log_main(f"\n[bold {THEME['accent']}]Update applied. Select a GIT task on the left to review what changed.[/]")

    def update_task_state(self, index: int, new_status: str) -> None:
        self.tasks[index].status = new_status  # type: ignore
        list_view = self.query_one("#task_list", ListView)

        with suppress(Exception):
            task_nodes = list_view.query(TaskItem).nodes
            if index < len(task_nodes):
                task_nodes[index].status = new_status

        if new_status == "running":
            target_pos = index + 1
            if list_view.index is None or list_view.index <= target_pos:
                list_view.index = target_pos

        if new_status in ("success", "failed", "skipped"):
            self.progress.advance(1)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if item is None:
            return
        switcher = self.query_one("#log_switcher", ContentSwitcher)
        if isinstance(item, MainLogItem):
            switcher.current = "log-main"
        elif isinstance(item, ReportLogItem):
            switcher.current = "log-report"
        elif isinstance(item, TaskItem):
            switcher.current = f"log-task-{item.task_index}"

    def _maybe_respond_prompt(self, text: str) -> None:
        if not hasattr(self, 'current_pty_master') or self.current_pty_master is None:
            return

        self._prompt_buffer = (getattr(self, "_prompt_buffer", "") + text)[-4096:]
        tail = ANSI_STRIP_REGEX.sub("", self._prompt_buffer)

        for name, pattern, kind in PROMPT_RULES:
            if not pattern.search(tail):
                continue

            if not hasattr(self, '_prompt_counts'):
                self._prompt_counts = {}
                self._prompt_last = {}

            count = self._prompt_counts.get(name, 0)
            max_count = 5 if name == "sudo_password" else 500
            if count >= max_count:
                continue

            now = time.monotonic()
            last = self._prompt_last.get(name, 0.0)
            cooldown = GLOBAL_CONFIG.get("prompts", {}).get("cooldown", 0.35)
            if now - last < cooldown:
                continue

            response: bytes | None = None

            if kind == "password":
                if SudoEngine._password:
                    response = SudoEngine._password.encode("utf-8") + b"\r"
                else:
                    self.log_main("[FATAL] Sudo password prompt detected, but no cached password is available.")
                    continue
            elif kind == "yes":
                response = b"y\r"
            else:
                response = b"\r"

            with suppress(BlockingIOError, OSError):
                os.write(self.current_pty_master, response)

            self._prompt_counts[name] = count + 1
            self._prompt_last[name] = now
            self._prompt_buffer = ""
            break

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
        os.set_blocking(master_fd, False)
        self._set_pty_size(slave_fd)

        transport: asyncio.Transport | None = None
        proc: asyncio.subprocess.Process | None = None
        file_obj = None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        line_buffer = ""

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
                cwd=str(WORK_TREE)
            )

            with suppress(OSError):
                os.close(slave_fd)
            slave_fd = -1

            self.active_child_pid = proc.pid
            self.active_child_group = True

            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader(limit=1024 * 1024)
            protocol = asyncio.StreamReaderProtocol(reader)

            file_obj = os.fdopen(master_fd, "rb", buffering=0)
            master_fd = -1

            transport, _ = await loop.connect_read_pipe(lambda: protocol, file_obj)

            async def read_loop() -> None:
                nonlocal line_buffer

                while True:
                    try:
                        chunk = await reader.read(4096)
                    except Exception:
                        chunk = b""

                    if not chunk:
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

            read_task = asyncio.create_task(read_loop())

            try:
                code = await wait_for_process(proc, timeout=timeout if timeout > 0 else None)
                try:
                    async with asyncio.timeout(2.0):
                        await asyncio.shield(read_task)
                except (TimeoutError, asyncio.TimeoutError):
                    read_task.cancel()
                return code == 0, code

            except (TimeoutError, asyncio.TimeoutError):
                with suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(proc.pid, signal.SIGKILL)
                read_task.cancel()
                with suppress(Exception):
                    await wait_for_process(proc, timeout=2.0)
                return False, 124

        finally:
            self.current_pty_master = None
            self.active_child_pid = None
            self.active_child_group = False

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

    @contextmanager
    def _suspend_ui(self):
        suspend = getattr(self, "suspend", None)
        if callable(suspend):
            with suspend():
                yield
        else:
            driver = getattr(self, "driver", None)
            if driver is not None and hasattr(driver, "stop_application_mode"):
                with suppress(Exception):
                    driver.stop_application_mode()
            try:
                yield
            finally:
                if driver is not None and hasattr(driver, "start_application_mode"):
                    with suppress(Exception):
                        driver.start_application_mode()

    async def _execute_task(self, index: int) -> str:
        task = self.tasks[index]

        if task.condition:
            condition_met = await asyncio.to_thread(self.condition_evaluator.check, task.condition)
            if not condition_met:
                self.log_main(f"[dim]Condition '{task.condition}' false; deferring: {escape(task.name)}[/dim]")
                return "deferred"

        if task.once and self.once_store:
            once_status = await asyncio.to_thread(self.once_store.check_marker_status, task, self.profile.name)
            if once_status == "notify_sealed":
                msg = f"[bold {THEME['warning']}][WARN][/] Run-once:sealed script modified since last run; not re-run: {escape(task.name)}"
                self.log_main(msg)
                self.log_task(msg, index)
                desktop_notify("Dusky Update", f"Sealed script modified: {task.name}", urgency="normal")
                if not OPT_DRY_RUN:
                    await asyncio.to_thread(self.once_store.mark_sealed_notified, task, self.profile.name)
                self.update_task_state(index, "skipped")
                if self.state_store and not OPT_DRY_RUN:
                    await asyncio.to_thread(self.state_store.mark, task, "skipped", note="Run-once:sealed modified")
                return "skipped"
            elif once_status == "skip":
                self.log_main(f"[dim]Run-once marker valid. Skipping: {escape(task.name)}[/dim]")
                self.update_task_state(index, "skipped")
                if self.state_store and not OPT_DRY_RUN:
                    await asyncio.to_thread(self.state_store.mark, task, "skipped", note="Run-once marker valid")
                return "skipped"

        self.update_task_state(index, "running")

        cmd_str = f"{task.name} {' '.join(task.args)}".strip()
        self.log_main(f"\n[bold {THEME['warning']}]>[/] Executing Process: [bold {THEME['fg']}]{escape(cmd_str)}[/]")
        self.log_task(f"[bold {THEME['accent']}]>>> PROCESS INITIATED:[/] {escape(cmd_str)}\n", index)

        if not (task.resolved_path and task.path_state == "ok" and task.resolved_path.is_file()):
            self.missing_scripts.append(task.name)
            err = f"[bold {THEME['warning']}][WARN][/] Script missing or conflicting in preflight: {escape(task.name)}"
            self.log_main(err)
            self.log_task(err, index)
            self.update_task_state(index, "skipped")
            if self.state_store and not OPT_DRY_RUN:
                await asyncio.to_thread(self.state_store.mark, task, "skipped", note="Script missing or unresolvable")
            if self.run_logger:
                self.run_logger.close_task(task, index, "skipped", 1, 0.0)
            return "skipped"

        resolved_path = task.resolved_path

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
                await asyncio.sleep(0.05)
            elif task.interactive:
                self.log_main(f"[dim]Suspending UI abstraction... Passing raw PTY control...[/]")
                self.log_task(f"[dim]Interactive flag detected. Console control delegated to user.[/]", index)

                with self._suspend_ui():
                    try:
                        res = subprocess.run(exec_cmd, cwd=str(WORK_TREE))
                        rc = res.returncode
                    except KeyboardInterrupt:
                        rc = 130
                
                start_time = time.time()
                while time.time() - start_time < 0.2:
                    await asyncio.sleep(0.01)

                self.log_task(f"\n[bold {THEME['success']}]PTY control returned. Exit Code: {rc}[/]", index)

            else:
                max_attempts = (task.retry + 1) if task.retry > 0 else 1
                for attempt in range(1, max_attempts + 1):
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

            if rc == 0:
                self.update_task_state(index, "success")
                if self.state_store and not OPT_DRY_RUN:
                    await asyncio.to_thread(self.state_store.mark, task, "completed", exit_code=0, duration=duration)
                if task.once and self.once_store and not OPT_DRY_RUN:
                    await asyncio.to_thread(self.once_store.mark_success, task, self.profile.name, exit_code=0, run_id=getattr(self, "run_id", ""))
                if self.run_logger:
                    self.run_logger.close_task(task, index, "completed", 0, duration)
                self.log_main(f"[bold {THEME['success']}][OK][/] Process Complete ({duration:.2f}s).")
                self.log_task(f"\n[bold {THEME['success']}]>>> EXECUTION SUCCESSFUL ({duration:.2f}s)[/]", index)
                return "completed"
            else:
                if task.ignore_fail and not OPT_STOP_ON_FAIL:
                    self.update_task_state(index, "skipped")
                    if self.state_store and not OPT_DRY_RUN:
                        await asyncio.to_thread(self.state_store.mark, task, "skipped", exit_code=rc, duration=duration)
                    if self.run_logger:
                        self.run_logger.close_task(task, index, "skipped", rc, duration)
                    self.log_main(f"[bold {THEME['warning']}][WARN][/] Process failure (Code {rc}) suppressed by manifest.")
                    self.log_task(f"\n[bold {THEME['warning']}]>>> EXECUTION FAILED / SUPPRESSED (Code {rc})[/]", index)
                    return "skipped"
                else:
                    self.update_task_state(index, "failed")
                    if self.state_store and not OPT_DRY_RUN:
                        await asyncio.to_thread(self.state_store.mark, task, "failed", exit_code=rc, duration=duration)
                    if self.run_logger:
                        self.run_logger.close_task(task, index, "failed", rc, duration)
                    if OPT_STOP_ON_FAIL:
                        self.log_main(f"[bold {THEME['error']}][FATAL][/] Process aborted execution sequence (Code {rc}).")
                        self.log_task(f"\n[bold {THEME['error']}]>>> FATAL EXECUTION FAILURE (Code {rc})[/]", index)
                        self.abort_flag = True
                    else:
                        self.log_main(f"[bold {THEME['error']}][ERROR][/] Process execution failed (Code {rc}). Continuing sequence...")
                        self.log_task(f"\n[bold {THEME['error']}]>>> EXECUTION FAILED (Code {rc})[/]", index)
                    return "failed"

        except Exception as e:
            duration = time.monotonic() - start_t
            err_msg = f"[bold {THEME['error']}][ERROR][/] Internal Exception: {escape(str(e))}"
            self.log_main(err_msg)
            self.log_task(err_msg, index)
            self.update_task_state(index, "failed")
            if self.state_store and not OPT_DRY_RUN:
                await asyncio.to_thread(self.state_store.mark, task, "failed", exit_code=1, note=str(e), duration=duration)
            if self.run_logger:
                self.run_logger.close_task(task, index, "failed", 1, duration)
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

        if self.abort_flag:
            v_color = THEME['error']
            v_title = "ABORTED"
        elif OPT_DRY_RUN:
            v_color = THEME['success']
            v_title = "DRY-RUN"
        elif missing_count > 0 or fail_count > 0:
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
            sha_str = f" ({before_sha} ➔ {after_sha})" if (before_sha and after_sha) else ""
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
        else:
            git_headline = f"Up to date at commit [dim]{after_sha or before_sha or 'HEAD'}[/dim]"

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

        lines = [
            f"════════════════════════════════════════════════════════════════════════════════",
            f" ◆ FINAL OVERVIEW {sep} [bold {THEME['fg']}]{escape(self.profile.name)}[/] {sep} Verdict: [bold {v_color}]{v_title}[/]",
            f"════════════════════════════════════════════════════════════════════════════════",
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
            st_text = "restored" if mod_restored else ("merge required" if mod_restored is False else "backed up")
            loc_extra = f" ({escape(mod_backup)})" if mod_backup else ""
            lines.append(f"   Local Tracked    : {mod_c} file(s) ({st_text}){loc_extra}")
        if full_backup:
            lines.append(f"   Full Tree Backup : {escape(full_backup)}")

        lines.extend([
            f"",
            f" {S('matrix')} SCRIPT EXECUTION MATRIX",
            f"   ┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐",
            f"   │ MODE     │ SUCCESS  │ FAILED   │ SKIPPED  │ MISSING  │ TOTAL    │",
            f"   ├──────────┼──────────┼──────────┼──────────┼──────────┼──────────┤",
        ])

        for mode_name in sorted(matrix.keys()):
            r = matrix[mode_name]
            tot_row = sum(r.values())
            lines.append(
                f"   │ {mode_name:<8s} │    [bold {THEME['success']}]{r['success']:2d}[/]    │    [bold {THEME['error']}]{r['failed']:2d}[/]    │    [dim {THEME['warning']}]{r['skipped']:2d}[/]    │    [dim]{r['missing']:2d}[/]    │    {tot_row:2d}    │"
            )

        lines.extend([
            f"   ├──────────┼──────────┼──────────┼──────────┼──────────┼──────────┤",
            f"   │ TOTAL    │    [bold {THEME['success']}]{tot_succ:2d}[/]    │    [bold {THEME['error']}]{tot_fail:2d}[/]    │    [dim {THEME['warning']}]{tot_skip:2d}[/]    │    [dim]{tot_miss:2d}[/]    │    {tot_all:2d}    │",
            f"   └──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘",
            f"",
        ])

        failed_tasks = [t for t in profile_tasks if t.status == "failed"]
        skipped_tasks = [t for t in profile_tasks if t.status == "skipped"]

        if failed_tasks:
            hard_failed = [t for t in failed_tasks if not t.ignore_fail]
            soft_failed = [t for t in failed_tasks if t.ignore_fail]

            if hard_failed:
                lines.append(f" [bold {THEME['error']}]✗ HARD FAILED SCRIPTS ({len(hard_failed)}):[/]")
                for t in hard_failed:
                    status_note = "(Required - Pipeline Aborted)" if self.abort_flag else "(Required - Execution Continued)"
                    lines.append(f"   • [{t.mode}] {escape(t.name)} [bold {THEME['error']}]{status_note}[/]")

            if soft_failed:
                lines.append(f" [bold {THEME['warning']}]⚠ SOFT FAILED SCRIPTS ({len(soft_failed)}):[/]")
                for t in soft_failed:
                    lines.append(f"   • [{t.mode}] {escape(t.name)} [dim {THEME['warning']}](Ignored / Allowed to Fail)[/dim]")

            failed_dirs = sorted(list({str(t.resolved_path.parent) for t in failed_tasks if getattr(t, "resolved_path", None)}))
            if failed_dirs:
                lines.append(f"   [dim]Debug locations:[/dim]")
                for d in failed_dirs:
                    lines.append(f"     └─ [dim]{escape(d)}[/dim]")
        else:
            lines.append(f" [dim]✗ FAILED SCRIPTS   : None[/dim]")

        if skipped_tasks:
            lines.append(f" [bold {THEME['warning']}]- SKIPPED SCRIPTS ({len(skipped_tasks)}):[/]")
            for t in skipped_tasks[:12]:
                reason = "condition false" if t.condition else ("once marker valid" if t.once else ("missing script" if t.path_state == "missing" else "ignored failure"))
                lines.append(f"   • [{t.mode}] {escape(t.name)} [dim]({reason})[/dim]")
            if len(skipped_tasks) > 12:
                lines.append(f"   • ... and {len(skipped_tasks) - 12} more skipped script(s).")
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
            f"════════════════════════════════════════════════════════════════════════════════",
        ])

        return "\n".join(lines)

    async def execute_pipeline(self) -> None:
        p1_start = time.monotonic()
        self._self_hash_before = file_checksum(SCRIPT_PATH)
        self._profile_hash_before = (
            file_checksum(self.profile.filepath)
            if getattr(self, "profile", None) and getattr(self.profile, "filepath", None)
            else ""
        )
        cfg_p = global_config_path()
        self._config_hash_before = file_checksum(cfg_p) if cfg_p else ""

        if not OPT_SKIP_SYNC:
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
                        self.update_task_state(index, "skipped")
                        if self.run_logger:
                            self.run_logger.close_task(self.tasks[index], index, "skipped", 0, 0.0)
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
                    self._show_completion_dialog(
                        "UPDATE HALTED",
                        "Git integrity check failed. The update was stopped to protect your system.\n\nChoose how to continue:",
                        "danger",
                    )
                    return
                store_last_good_self()
            self.phase_durations["phase1_git"] = time.monotonic() - p1_start
        else:
            if OPT_POST_SELF_UPDATE:
                self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 1: Git Architecture Reconciliation (Self-Updated) ═══[/]\n")
                self._restore_last_git_diff()
            else:
                self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 1: Git Architecture Reconciliation (SKIPPED) ═══[/]\n")
                for index in range(5):
                    self.update_task_state(index, "skipped")
            self.phase_durations["phase1_git"] = 0.0

        if OPT_SYNC_ONLY:
            msg = "SYNC SIMULATED." if OPT_DRY_RUN else "SYNC COMPLETE."
            self.log_main(f"\n[bold {THEME['success']}]{msg} (--sync-only specified)[/]")
            if self.run_logger:
                self.run_logger.write_report(
                    self.profile,
                    self.tasks,
                    {t.state_key: t.status for t in self.tasks},
                    {"success": 5, "failed": 0, "missing": 0, "skipped": len(self.tasks) - 5},
                )
            report_block = self._render_final_overview_block(
                verdict="SYNC COMPLETE" if not OPT_DRY_RUN else "SYNC SIMULATED",
                success_count=5,
                fail_count=0,
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
            self._show_completion_dialog(
                "SYNC COMPLETE" if not OPT_DRY_RUN else "SYNC SIMULATED",
                "Dotfile synchronization finished.\n\nChoose how to continue:",
                "success",
            )
            return

        if self._maybe_reexec_after_sync():
            return

        p15_start = time.monotonic()
        self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 1.5: Post-Sync Script Resolution ═══[/]\n")
        if not resolve_and_validate_manifest(self.profile, self.tasks, interactive=False):
            self.abort_flag = True
            self.phase_durations["phase1_5_resolve"] = time.monotonic() - p15_start
            self.log_main(f"[bold {THEME['error']}][FATAL][/] Post-sync script resolution failed. Cannot proceed.")
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
            self._show_completion_dialog(
                "UPDATE HALTED",
                "Post-sync script resolution failed. The update was stopped to protect your system.\n\nChoose how to continue:",
                "danger",
            )
            return
        self.phase_durations["phase1_5_resolve"] = time.monotonic() - p15_start

        p2_start = time.monotonic()
        self.log_main(f"\n[bold {THEME['accent']}]═══ Phase 2: Configuration Pipeline Execution ═══[/]\n")

        success_count, fail_count = 0, 0
        deferred_indices: list[int] = []

        for index in range(5, len(self.tasks)):
            if self.abort_flag:
                self.update_task_state(index, "skipped")
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
            pending: list[int] = deferred_indices
            leftover: list[int] = deferred_indices
            for pass_no in range(1, max_defer_passes + 1):
                if self.abort_flag:
                    break
                progressed = False
                next_pending: list[int] = []
                for index in pending:
                    outcome = await self._execute_task(index)
                    if outcome == "deferred":
                        next_pending.append(index)
                    else:
                        progressed = True
                        if outcome == "completed":
                            success_count += 1
                        elif outcome == "failed":
                            fail_count += 1
                    if self.abort_flag:
                        break
                leftover = next_pending
                if not next_pending or self.abort_flag:
                    break
                if not progressed:
                    break
                pending = next_pending

            for index in leftover:
                task = self.tasks[index]
                self.update_task_state(index, "skipped")
                if self.state_store and not OPT_DRY_RUN:
                    await asyncio.to_thread(self.state_store.mark, task, "skipped", note=f"Condition never met: {task.condition}")
                if self.run_logger:
                    self.run_logger.close_task(task, index, "skipped", 0, 0.0)
                self.log_main(f"[dim]Condition '{task.condition}' never satisfied; skipping: {escape(task.name)}[/dim]")

        self.phase_durations["phase2_exec"] = time.monotonic() - p2_start
        total_duration = time.monotonic() - self.run_start_mono
        skipped_count = sum(1 for t in self.tasks[5:] if t.status == "skipped")
        missing_count = len(self.missing_scripts)

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

        self._log_lines["report"] = deque([strip_ansi(report_block)], maxlen=6000)
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
        elif self.missing_scripts or fail_count > 0:
            details = []
            if fail_count > 0:
                details.append(f"{fail_count} script(s) failed")
            if self.missing_scripts:
                details.append(f"{missing_count} script(s) missing")
            desktop_notify("Dusky Update", ", ".join(details), urgency="normal")
            AudioNotifier.play("info")
        else:
            desktop_notify("Dusky updated", "", urgency="normal")
            AudioNotifier.play("complete")

        self.log_main("\n[dim]Press 'Ctrl+C' or 'Q' to terminate abstraction shell.[/dim]")

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
        elif self.missing_scripts or fail_count > 0:
            dialog_title, dialog_level = "dusky updated", "warning"
        else:
            dialog_title, dialog_level = "dusky updated", "success"

        self._show_completion_dialog(
            dialog_title,
            "\n".join(summary_lines) + "\n\nChoose how to continue:",
            dialog_level,
        )

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

    def action_cycle_filter(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return

        filters = ["all", "pending", "running", "success", "failed", "skipped"]
        idx = filters.index(self.filter_mode) if self.filter_mode in filters else 0
        self.filter_mode = filters[(idx + 1) % len(filters)]

        list_view = self.query_one("#task_list", ListView)
        for item in list_view.query(TaskItem):
            if self.filter_mode == "all":
                item.display = True
            else:
                item.display = (item.status == self.filter_mode)

        self.log_main(f"[dim]Task filter set to: [bold]{self.filter_mode}[/bold][/dim]")

    def _set_pane_widths(self, width_pct: int) -> None:
        min_w = GLOBAL_CONFIG.get("ui", {}).get("min_left_pane_width", 15)
        max_w = GLOBAL_CONFIG.get("ui", {}).get("max_left_pane_width", 80)
        self.sidebar_width = max(min_w, min(max_w, width_pct))
        with suppress(Exception):
            self.query_one("#sidebar").styles.width = f"{self.sidebar_width}%"
            self.query_one("#log_container").styles.width = f"{100 - self.sidebar_width}%"

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
        with suppress(Exception):
            self.query_one("#task_list", ListView).action_cursor_down()

    def action_tree_up(self) -> None:
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
            self.screen.dismiss("cancel")
            return

        if isinstance(self.screen, CompletionDialog):
            self.screen.dismiss(False)
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
                actual_cols = max(10, int(event.size.width * (1 - (sidebar_percent / 100))) - 2)
                winsize = struct.pack("HHHH", event.size.height, actual_cols, 0, 0)
                fcntl.ioctl(self.current_pty_master, termios.TIOCSWINSZ, winsize)

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.screen, ModalScreen):
            return

        if getattr(self, "current_pty_master", None) is not None:
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
                with suppress(BlockingIOError, OSError):
                    os.write(self.current_pty_master, data)
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
        self.exit()

    def _show_completion_dialog(self, title: str, message: str, level: str) -> None:
        self.push_screen(
            CompletionDialog(title=title, message=message, level=level),
            self._on_completion_reply,
        )

    def _maybe_reexec_after_sync(self) -> bool:
        if OPT_POST_SELF_UPDATE or OPT_DRY_RUN or OPT_SYNC_ONLY:
            return False
        before = getattr(self, "_self_hash_before", "")
        after = file_checksum(SCRIPT_PATH)

        prof_before = getattr(self, "_profile_hash_before", "")
        prof_filepath = getattr(self.profile, "filepath", None) if getattr(self, "profile", None) else None
        prof_after = file_checksum(prof_filepath) if prof_filepath else ""

        cfg_before = getattr(self, "_config_hash_before", "")
        cfg_p = global_config_path()
        cfg_after = file_checksum(cfg_p) if cfg_p else ""

        script_changed = bool(before and after and after != before)
        profile_changed = bool(prof_before and prof_after and prof_after != prof_before)
        config_changed = bool(cfg_before and cfg_after and cfg_after != cfg_before)

        if not script_changed and not profile_changed and not config_changed:
            remove_last_git_diff()
            return False
        try:
            sys.stdout.flush()
        except Exception:
            pass
        if script_changed:
            sys.stderr.write(
                "\033[1;33m[updater]\033[0m Script updated during sync — restarting with the new version.\n"
            )
        elif profile_changed:
            sys.stderr.write(
                "\033[1;33m[updater]\033[0m Profile updated during sync — restarting to apply new tasks.\n"
            )
        else:
            sys.stderr.write(
                "\033[1;33m[updater]\033[0m Settings updated during sync — restarting to apply new configuration.\n"
            )
        try:
            sys.stderr.flush()
        except Exception:
            pass
        release_lock()
        cleaned_args = [a for a in sys.argv[1:] if a != "--post-self-update"]
        os.execv(sys.executable, [sys.executable, str(SCRIPT_PATH), "--post-self-update", *cleaned_args])
        return True

    def _on_completion_reply(self, quit_now: bool | None) -> None:
        if quit_now:
            self.exit()
        else:
            report_idx = len(self.tasks) + 1
            with suppress(Exception):
                list_view = self.query_one("#task_list", ListView)
                list_view.index = report_idx
                self.query_one("#log_switcher", ContentSwitcher).current = "log-report"


if __name__ == "__main__":
    try:
        args = parse_args()

        profile = load_profile(OPT_PROFILE_NAME)
        tasks = parse_manifest(profile)
        profile.tasks = tasks
        has_sudo = SUDO_ALREADY_ACQUIRED

        if args.list:
            list_active_scripts(profile)

        if not OPT_SYNC_ONLY and not OPT_DRY_RUN:
            if not has_sudo and any(t.mode == 'S' for t in tasks):
                if not SudoEngine.preflight(cli_password=getattr(args, 'sudo_password', None)):
                    sys.exit(1)
                has_sudo = True

        setup_storage_roots()
        setup_runtime_dir()
        if not acquire_lock():
            sys.exit(1)

        setup_logging()

        if not OPT_DRY_RUN:
            auto_prune()

        if not OPT_SYNC_ONLY:
            if not resolve_and_validate_manifest(profile, tasks):
                sys.stderr.write(
                    "\033[1;31m[FATAL]\033[0m Pre-flight validation failed. "
                    "Resolve the above errors and re-run.\n"
                )
                sys.exit(1)

        app = DuskyApp(profile, tasks, has_sudo)
        app.run()

    except BrokenPipeError:
        with suppress(Exception):
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        sys.exit(0)
    except KeyboardInterrupt:
        sys.stdout.write("\n\033[1;33m[WARN]\033[0m User interrupt detected. Terminating.\n")
        sys.exit(130)

#!/usr/bin/env python3
"""Hyprland cursor-size adjuster — bleeding-edge, Lua-config native.

Increases / decreases the compositor cursor size and keeps every layer
in sync (compositor, GTK/gsettings, session env, persisted Lua config):

  1. ``hyprctl setcursor <theme> <size>``  (live Wayland / XWayland cursor).
     Sizes snap to the theme's real bitmaps (parsed from the XCursor TOC),
     so every step lands on a renderable size; vector themes step freely.
     Note (compositor behavior, upstream wontfix): the staged size renders
     once the cursor *image* changes (arrow -> beam -> hand, ...). Bare
     motion does not refresh it; over real content it lands within a hover
     or two, and the OSD below confirms each press instantly.
  2. ``gsettings`` ``org.gnome.desktop.interface cursor-size`` + ``cursor-theme``
     (GTK apps; ``dconf`` fallback when schemas are missing, e.g. NixOS)
  3. ``dbus-update-activation-environment --systemd`` (newly launched apps)
  4. ``~/.config/hypr/edit_here/source/environment_variables.lua``
     (survives reboot / ``hyprctl reload`` — atomic POSIX replace)
  5. ``$XDG_CACHE_HOME/hypr-cursor-size`` state file (IPC has no cursor getter)

Target stack: Hyprland >= 0.56 (Lua ``hl.env`` config provider),
hyprcursor >= 0.1.x, Linux kernel >= 7.2, Python >= 3.12.

Nothing is hardcoded: theme, size, paths, user and hardware are all
detected dynamically via env vars, gsettings, Hyprland IPC and the
XDG base-directory spec, so the script works unmodified on other machines.

Usage:
    cursor_size.py + | -                 # grow / shrink by --step
    cursor_size.py up | down              # aliases
    cursor_size.py --set 32               # jump to an absolute size
    cursor_size.py --reset                # back to the configured default
    cursor_size.py --get                  # print current size, change nothing

Keybinds (in ~/.config/hypr/source/keybinds.lua, right after the
SUPER+minus/plus zoom binds, plus SHIFT+scroll variants):

    hl.bind("SUPER + SHIFT + equal",
        hl.dsp.exec_cmd(dusky_scripts .. "hypr/cursor_size.py +"),
        { description = "Cursor Size Up", repeating = true })
    hl.bind("SUPER + SHIFT + minus",
        hl.dsp.exec_cmd(dusky_scripts .. "hypr/cursor_size.py -"),
        { description = "Cursor Size Down", repeating = true })
    hl.bind("SUPER + SHIFT + mouse_up",
        hl.dsp.exec_cmd(dusky_scripts .. "hypr/cursor_size.py +"),
        { description = "Cursor Size Up (Scroll)" })
    hl.bind("SUPER + SHIFT + mouse_down",
        hl.dsp.exec_cmd(dusky_scripts .. "hypr/cursor_size.py -"),
        { description = "Cursor Size Down (Scroll)" })

Note: on a US layout ``equal`` is the ``=``/``+`` key, so
``SUPER+SHIFT+equal`` *is* SUPER+Shift+Plus, mirroring SUPER+equal (Zoom In).
Plain SUPER+scroll stays on workspace cycling, hence SHIFT for scroll too.
"""

import argparse
import fcntl
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "theme_matugen/global"))
from theme_files import publication_lock, merge_groups

# ---------------------------------------------------------------------------
# Dynamic, relocatable locations — XDG spec, no hardcoded $HOME / usernames.
# ---------------------------------------------------------------------------

def _home() -> Path:
    return Path.home()


def _config_home() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    return _home() / ".config"


def _cache_home() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg)
    return _home() / ".cache"


CONFIG_HOME = _config_home()
CACHE_HOME = _cache_home()

USER_ENV_LUA = CONFIG_HOME / "hypr" / "edit_here" / "source" / "environment_variables.lua"
BASE_ENV_LUA = CONFIG_HOME / "hypr" / "source" / "environment_variables.lua"
STATE_FILE = CACHE_HOME / "hypr-cursor-size"
LOCK_FILE = Path(os.environ.get("XDG_RUNTIME_DIR") or CACHE_HOME) / "dusky-cursor.lock"
CURSOR_CONF = CONFIG_HOME / "dusky" / "settings" / "cursor.conf"

# Sane universal bounds (freedesktop/XCursor convention, not machine-specific).
# All overridable via flags or CURSOR_SIZE_* env vars.
DEFAULT_STEP = 2
DEFAULT_MIN = 8
DEFAULT_MAX = 96
DEFAULT_FALLBACK_SIZE = 18  # house default when nothing else is detectable (freedesktop uses 24)

GSETTINGS_SCHEMA = "org.gnome.desktop.interface"
NOTIFY_TAG = "hypr_cursor_size"

DEBUG = os.environ.get("DEBUG") == "1"
QUIET = False


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _color(code: str, msg: str) -> str:
    if os.environ.get("NO_COLOR") or not sys.stderr.isatty():
        return msg
    return f"\033[{code}m{msg}\033[0m"


def log_err(msg: str) -> None:
    if not QUIET:
        sys.stderr.write(f"{_color('0;31m', '[ERROR]')} {msg}\n")


def log_warn(msg: str) -> None:
    if not QUIET:
        sys.stderr.write(f"{_color('0;33m', '[WARN]')} {msg}\n")


def log_info(msg: str) -> None:
    if not QUIET:
        sys.stderr.write(f"{_color('0;32m', '[INFO]')} {msg}\n")


def log_debug(msg: str) -> None:
    if DEBUG and not QUIET:
        sys.stderr.write(f"{_color('0;34m', '[DEBUG]')} {msg}\n")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess[str]:
    """Run cmd (list form — never shell) with short timeout, never raising."""
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 10)
    kw.setdefault("check", False)
    try:
        return subprocess.run(cmd, **kw)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timed out")


def atomic_write(path: Path, data: str) -> None:
    """POSIX atomic write: temp file in same dir + os.replace, mode preserved."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o644
    try:
        if path.exists():
            mode = path.stat().st_mode & 0o777
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def notify(title: str, body: str, urgency: str = "low") -> None:
    """Best-effort desktop + compositor notification. Never raises."""
    if QUIET:
        return
    # 1. Freedesktop OSD (synchronous tag so repeats replace each other).
    if have("notify-send"):
        r = run([
            "notify-send",
            "-h", f"string:x-canonical-private-synchronous:{NOTIFY_TAG}",
            "-u", urgency, "-t", "1800", title, body,
        ])
        log_debug(f"notify-send -> rc={r.returncode}")
    # 2. Native Hyprland OSD: notify <icon> <time_ms> <color> <message...>
    if have("hyprctl"):
        # icon 5 = Ok, color 0 = default
        r = run(["hyprctl", "notify", "5", "1800", "0", f"{title}: {body}"])
        log_debug(f"hyprctl notify -> rc={r.returncode} {r.stderr.strip()[:120]}")


# ---------------------------------------------------------------------------
# Dynamic detection — theme
# ---------------------------------------------------------------------------

def _split_code_comment(line: str) -> tuple[str, str]:
    """Split a Lua line into (code, comment) at the first -- outside strings."""
    in_str: str | None = None
    i = 0
    while i < len(line) - 1:
        c = line[i]
        if in_str is not None:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c in ("'", '"'):
                in_str = c
            elif c == "-" and line[i + 1] == "-":
                return line[:i], line[i:]
        i += 1
    return line, ""


def _strip_lua_comments(text: str) -> str:
    """Drop Lua ``--`` line comments.

    The stock override template contains commented-out
    ``hl.env("XCURSOR_SIZE", "24")`` examples that must never be treated
    as live configuration.
    """
    return "\n".join(_split_code_comment(line)[0] for line in text.splitlines())


def _theme_from_gsettings() -> str | None:
    if not have("gsettings"):
        return None
    r = run(["gsettings", "get", GSETTINGS_SCHEMA, "cursor-theme"])
    if r.returncode != 0:
        log_debug(f"gsettings cursor-theme failed: {r.stderr.strip()[:150]}")
        return None
    theme = r.stdout.strip().strip("'\"")
    return theme or None


def _theme_from_lua_configs() -> str | None:
    """Parse hl.env("HYPRCURSOR_THEME"/"XCURSOR_THEME", ...) from Lua configs."""
    pat = re.compile(
        r"""(?:hl\.env|hl_env)\s*\(\s*["'](?:HYPRCURSOR_THEME|XCURSOR_THEME)["']\s*,\s*["']([^"']+)["']\s*\)"""
    )
    for path in (USER_ENV_LUA, BASE_ENV_LUA):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = pat.search(_strip_lua_comments(text))
        if m:
            log_debug(f"theme {m.group(1)!r} from {path}")
            return m.group(1)
    return None


def detect_theme(explicit: str | None) -> str:
    if explicit:
        return explicit
    # gsettings first: Hyprland keeps it live via cursor:sync_gsettings_theme,
    # so it reflects setcursor/dusky_cursor switches immediately. Process env
    # goes stale after any theme switch (login-time values linger in old
    # shells) and would otherwise resurrect the previous theme on persist.
    theme = _theme_from_gsettings()
    if theme:
        log_debug(f"theme {theme!r} from gsettings")
        return theme
    for var in ("HYPRCURSOR_THEME", "XCURSOR_THEME"):
        val = os.environ.get(var, "").strip()
        if val:
            log_debug(f"theme {val!r} from ${var}")
            return val
    theme = _theme_from_lua_configs()
    if theme:
        return theme
    log_err("Could not detect cursor theme from gsettings, "
            "$HYPRCURSOR_THEME / $XCURSOR_THEME or Hyprland Lua config. "
            "Pass --theme <name>.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Dynamic detection — size
# ---------------------------------------------------------------------------

_SIZE_CALL_PAT = re.compile(
    r"""(?:hl\.env|hl_env)\s*\(\s*["'](?:HYPRCURSOR_SIZE|XCURSOR_SIZE)["']\s*,\s*["']?(\d+)["']?\s*\)"""
)


def _size_from_gsettings() -> int | None:
    if not have("gsettings"):
        return None
    r = run(["gsettings", "get", GSETTINGS_SCHEMA, "cursor-size"])
    if r.returncode != 0:
        log_debug(f"gsettings cursor-size failed: {r.stderr.strip()[:150]}")
        return None
    m = re.search(r"\d+", r.stdout)
    if m:
        return int(m.group(0))
    return None


def _size_from_env() -> int | None:
    for var in ("HYPRCURSOR_SIZE", "XCURSOR_SIZE"):
        val = os.environ.get(var, "").strip()
        if re.fullmatch(r"\d+", val):
            log_debug(f"size {val} from ${var}")
            return int(val)
    return None


def _size_from_state() -> int | None:
    try:
        text = STATE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def _size_from_lua_configs() -> int | None:
    for path in (USER_ENV_LUA, BASE_ENV_LUA):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _SIZE_CALL_PAT.search(_strip_lua_comments(text))
        if m:
            log_debug(f"size {m.group(1)} from {path}")
            return int(m.group(1))
    return None


def detect_current_size() -> int:
    """First hit wins: gsettings > env > state file > Lua config > fallback.

    No IPC getter for cursor size exists, so the script keeps these layers
    in sync on every run; any one of them is therefore a valid read, and a
    later run converges divergent layers instead of warning about them.
    """
    for source in (_size_from_gsettings, _size_from_env,
                   _size_from_state, _size_from_lua_configs):
        size = source()
        if size:
            log_debug(f"current size {size} (via {source.__name__})")
            return size
    log_warn(f"No cursor size detectable; using freedesktop default "
             f"{DEFAULT_FALLBACK_SIZE}.")
    return DEFAULT_FALLBACK_SIZE


def get_default_size() -> int:
    """Reset target: shipped base-config default (never the user override).

    The user override file is owned by this script (it rewrites the size on
    every run), so reading it back as a "default" would be circular and make
    --reset a no-op. The base file is the real configured default.
    """
    try:
        text = BASE_ENV_LUA.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_FALLBACK_SIZE
    m = _SIZE_CALL_PAT.search(_strip_lua_comments(text))
    if m:
        return int(m.group(1))
    return DEFAULT_FALLBACK_SIZE


def theme_icon_dir(theme: str) -> Path | None:
    """Locate an installed cursor theme dir (user dirs first)."""
    home = _home()
    for base in (home / ".local" / "share" / "icons",
                 home / ".icons",
                 Path("/usr/local/share/icons"),
                 Path("/usr/share/icons")):
        cand = base / theme
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    return None


def theme_native_sizes(theme: str) -> list[int]:
    """Discrete bitmap sizes shipped by an XCursor theme (parsed from the
    left_ptr TOC — dynamic per theme, nothing hardcoded).

    Returns [] for vector (hyprcursor) themes or when undetectable, meaning
    free stepping is fine.
    """
    d = theme_icon_dir(theme)
    if d is None:
        return []
    if (d / "manifest.hl").is_file() or (d / "hyprcursors").is_dir():
        return []  # vector theme: arbitrary sizes render crisply
    try:
        raw = (d / "cursors" / "left_ptr").read_bytes()
    except OSError:
        return []
    try:
        magic, _blen, _ver, ntoc = struct.unpack("<IIII", raw[:16])
        if magic != 0x72756358 or ntoc > 256:  # "Xcur" LE
            return []
        sizes = set()
        for i in range(ntoc):
            typ, subtype, _pos = struct.unpack("<III", raw[16 + i * 12:28 + i * 12])
            if typ == 0xFFFD0002:  # image TOC entry; subtype = nominal size
                sizes.add(subtype)
        return sorted(s for s in sizes if 0 < s < 1000)
    except (struct.error, IndexError):
        return []


def snap_to_native(target: int, direction: int, native: list[int]) -> int:
    """Snap a computed size onto the theme's real bitmap sizes.

    Direction-aware so every keypress lands somewhere new: growing rounds
    up to the next available size, shrinking rounds down, absolute --set
    takes the nearest. Empty list (vector/unknown theme) passes through.
    """
    if not native:
        return target
    if direction > 0:
        bigger = [s for s in native if s >= target]
        return min(bigger) if bigger else native[-1]
    if direction < 0:
        smaller = [s for s in native if s <= target]
        return max(smaller) if smaller else native[0]
    return min(native, key=lambda s: (abs(s - target), s))


# ---------------------------------------------------------------------------
# Apply layers
# ---------------------------------------------------------------------------

def apply_compositor(theme: str, size: int) -> bool:
    """Live update via the canonical IPC: `hyprctl setcursor <theme> <size>`."""
    if not have("hyprctl"):
        log_err("hyprctl not found in PATH; cannot update compositor cursor.")
        return False
    r = run(["hyprctl", "setcursor", theme, str(size)])
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        log_err(f"hyprctl setcursor failed (rc={r.returncode}): {out[:300]}")
        return False
    log_info(f"Compositor cursor -> {theme!r} @ {size}px")
    log_debug(f"setcursor output: {out[:200]}")
    return True


def apply_gsettings(theme: str, size: int) -> bool:
    """Sync GTK layer. dconf fallback when gsettings schemas are absent."""
    ok = True
    if have("gsettings"):
        r1 = run(["gsettings", "set", GSETTINGS_SCHEMA, "cursor-size", str(size)])
        r2 = run(["gsettings", "set", GSETTINGS_SCHEMA, "cursor-theme", theme])
        if r1.returncode != 0:
            log_warn(f"gsettings cursor-size failed: {r1.stderr.strip()[:200]}")
            ok = False
        if r2.returncode != 0:
            log_warn(f"gsettings cursor-theme failed: {r2.stderr.strip()[:200]}")
            ok = False
        if "No schemas" in (r1.stderr + r2.stderr):
            ok = apply_dconf(theme, size) and ok
    elif have("dconf"):
        ok = apply_dconf(theme, size)
    else:
        log_debug("neither gsettings nor dconf available; skipping GTK sync")
    return ok


def apply_dconf(theme: str, size: int) -> bool:
    r1 = run(["dconf", "write", "/org/gnome/desktop/interface/cursor-size",
              str(size)])
    r2 = run(["dconf", "write", "/org/gnome/desktop/interface/cursor-theme",
              f"'{theme}'"])
    if r1.returncode != 0 or r2.returncode != 0:
        log_warn("dconf cursor sync failed "
                 f"(size rc={r1.returncode}, theme rc={r2.returncode})")
        return False
    return True


def apply_dbus_env(size: int) -> None:
    """Publish new sizes to the systemd user / D-Bus activation environment so
    newly launched apps inherit them without a relogin. Best-effort."""
    if not have("dbus-update-activation-environment"):
        log_debug("dbus-update-activation-environment missing; skipping")
        return
    r = run(["dbus-update-activation-environment", "--systemd",
             f"XCURSOR_SIZE={size}", f"HYPRCURSOR_SIZE={size}"])
    if r.returncode != 0:
        log_debug(f"dbus-update-activation-environment rc={r.returncode}: "
                  f"{r.stderr.strip()[:150]}")


ENV_KEYS = ("XCURSOR_SIZE", "HYPRCURSOR_SIZE", "XCURSOR_THEME", "HYPRCURSOR_THEME")


def apply_gtk_settings(theme: str, size: int) -> bool:
    """Sync GTK settings.ini cursor keys (gtk-3.0/gtk-4.0). Best-effort.

    Kept in sync with dusky_cursor.update_gtk_settings so keypress steps
    never leave stale values behind for GTK apps.
    """
    with publication_lock("gtk"):
        return _apply_gtk_settings(theme, size)


def _apply_gtk_settings(theme: str, size: int) -> bool:
    ok = True
    values = {"gtk-cursor-theme-name": theme, "gtk-cursor-theme-size": str(size)}
    for name in ("gtk-3.0", "gtk-4.0"):
        path = CONFIG_HOME / name / "settings.ini"
        try:
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            current = ""
        text = merge_groups(current, {"Settings": values})
        if text == current:
            continue
        try:
            atomic_write(path, text)
        except OSError as e:
            log_warn(f"Cannot write {path}: {e}")
            ok = False
    return ok


def persist_lua_env(theme: str, size: int) -> bool:
    """Atomically upsert cursor vars in the user Lua override file.

    Handles both `hl.env(...)` and local-aliased `hl_env(...)` spellings,
    any quoting/whitespace, and a missing file. Appended lines always use
    the globally valid `hl.env` form.
    """
    try:
        original = USER_ENV_LUA.read_text(encoding="utf-8") if USER_ENV_LUA.exists() else ""
    except OSError as e:
        log_err(f"Cannot read {USER_ENV_LUA}: {e}")
        return False

    values = {"XCURSOR_SIZE": str(size), "HYPRCURSOR_SIZE": str(size),
              "XCURSOR_THEME": theme, "HYPRCURSOR_THEME": theme}
    # Replace only in live code: split each line into code/comment so the
    # commented template examples in the stock file are never touched.
    # Patterns hoisted: same four regexes for every line.
    pats = {
        key: re.compile(
            r"""(?:hl\.env|hl_env)\s*\(\s*["']""" + re.escape(key) + r"""["']\s*,\s*["']?[^"'\n\)]*["']?\s*\)"""
        )
        for key in ENV_KEYS
    }
    lines = original.splitlines()
    total_replaced = 0
    for idx, line in enumerate(lines):
        code, comment = _split_code_comment(line)
        for key in ENV_KEYS:
            code, n = pats[key].subn(f'hl.env("{key}", "{values[key]}")', code)
            total_replaced += n
        lines[idx] = code + comment
    text = "\n".join(lines)
    if original.endswith("\n") and text:
        text += "\n"
    log_debug(f"lua persist: replaced {total_replaced} occurrence(s) in code")

    live_code = _strip_lua_comments(text)
    missing = [k for k in ENV_KEYS
               if not re.search(rf"""["']{re.escape(k)}["']""", live_code)]
    if missing:
        if text and not text.endswith("\n"):
            text += "\n"
        if not original.strip():
            text = ('-- USER CONFIGURATION: environment_variables.lua (cursor size '
                    'managed by cursor_size.py)\n')
        for key in ENV_KEYS:
            if key in missing:
                text += f'hl.env("{key}", "{values[key]}")\n'
        log_info(f"Appended {missing} to {USER_ENV_LUA}")
    elif text != original:
        log_info(f"Updated cursor vars in {USER_ENV_LUA}")

    if text == original:
        return True
    try:
        atomic_write(USER_ENV_LUA, text)
        return True
    except OSError as e:
        log_err(f"Atomic write to {USER_ENV_LUA} failed: {e}")
        return False


def write_state(size: int) -> bool:
    try:
        atomic_write(STATE_FILE, f"{size}\n")
        # Keep ~/.config/dusky/settings/cursor.conf in sync if it exists
        if CURSOR_CONF.is_file():
            try:
                conf_text = CURSOR_CONF.read_text(encoding="utf-8")
                if re.search(r"^\s*SIZE\s*=", conf_text, re.MULTILINE):
                    new_conf = re.sub(r"^(\s*SIZE\s*=).*$", rf"\g<1>{size}", conf_text, flags=re.MULTILINE)
                else:
                    new_conf = conf_text.rstrip("\n") + f"\nSIZE={size}\n"
                if new_conf != conf_text:
                    atomic_write(CURSOR_CONF, new_conf)
            except OSError:
                pass
        return True
    except OSError as e:
        log_warn(f"Cannot write state file {STATE_FILE}: {e}")
        return False


def confirm_size(expected: int) -> bool:
    """One-shot confirming read: gsettings set is synchronous, so a single
    re-read is enough to catch a real failure (e.g. read-only dconf db)."""
    if not have("gsettings"):
        return True
    got = _size_from_gsettings()
    if got == expected:
        return True
    log_warn(f"Verification: gsettings reports {got}, expected {expected}")
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Grow/shrink the Hyprland cursor (SUPER+SHIFT+minus/plus "
                    "companion to the SUPER+minus/plus zoom binds).",
        epilog="Sizes clamp to [MIN, MAX]. Theme auto-detected unless --theme.",
    )
    p.add_argument("direction", nargs="?", default=None,
                   choices=["+", "-", "up", "down", "increase", "decrease"],
                   help="'+/up/increase grows, -/down/decrease shrinks'")
    p.add_argument("--set", type=int, metavar="N", default=None,
                   help="jump to size N (snapped to the theme's real bitmaps)")
    p.add_argument("--reset", action="store_true",
                   help="restore the configured default size")
    p.add_argument("--get", action="store_true",
                   help="print current size and exit (no changes)")
    p.add_argument("--step", type=int, default=None,
                   help=f"increment in px (default {DEFAULT_STEP}, or $CURSOR_SIZE_STEP)")
    p.add_argument("--min", type=int, default=None, dest="min_size",
                   help=f"lower clamp (default {DEFAULT_MIN}, or $CURSOR_SIZE_MIN)")
    p.add_argument("--max", type=int, default=None, dest="max_size",
                   help=f"upper clamp (default {DEFAULT_MAX}, or $CURSOR_SIZE_MAX)")
    p.add_argument("--theme", default=None,
                   help="cursor theme (default: auto-detect, or $CURSOR_THEME)")
    p.add_argument("--no-notify", action="store_true", help="suppress OSD")
    p.add_argument("--no-persist", action="store_true",
                   help="do not touch the Lua config (live + gsettings only)")
    p.add_argument("--dry-run", action="store_true",
                   help="compute and print the new size without applying")
    p.add_argument("--quiet", "-q", action="store_true", help="minimal output")
    p.add_argument("--verbose", "-v", action="store_true", help="debug output")
    return p.parse_args(argv)


def resolve_bounds(args: argparse.Namespace) -> tuple[int, int, int]:
    def env_int(name: str, fallback: int) -> int:
        try:
            return int(os.environ.get(name, "").strip() or fallback)
        except ValueError:
            return fallback

    step = args.step if args.step is not None else env_int("CURSOR_SIZE_STEP", DEFAULT_STEP)
    lo = args.min_size if args.min_size is not None else env_int("CURSOR_SIZE_MIN", DEFAULT_MIN)
    hi = args.max_size if args.max_size is not None else env_int("CURSOR_SIZE_MAX", DEFAULT_MAX)
    step = max(1, step)
    if lo > hi:
        lo, hi = hi, lo
    return step, lo, hi


def main(argv: list[str] | None = None) -> int:
    global QUIET, DEBUG
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.quiet:
        QUIET = True
    if args.verbose:
        DEBUG = True

    if args.get and (args.direction or args.set is not None or args.reset):
        log_err("--get is exclusive (no direction/--set/--reset alongside).")
        return 1
    if sum([args.set is not None, args.reset, args.direction is not None]) > 1:
        log_err("Give exactly one of: direction, --set N, --reset, --get.")
        return 1
    if not (args.get or args.direction or args.set is not None or args.reset):
        log_err("Nothing to do. Usage: cursor_size.py [+|-|up|down] [--set N] [--reset] [--get]")
        return 1

    theme = detect_theme(args.theme or os.environ.get("CURSOR_THEME") or None)
    current = detect_current_size()
    step, lo, hi = resolve_bounds(args)
    native = theme_native_sizes(theme)
    if native:
        log_debug(f"theme {theme!r} native bitmap sizes: {native}")

    def snap(raw: int, sign: int) -> int:
        return max(lo, min(hi, snap_to_native(raw, sign, native)))

    if args.get:
        print(current)
        return 0

    if args.reset:
        target = snap(get_default_size(), 0)
        reason = "reset"
    elif args.set is not None:
        target = snap(args.set, 0)
        reason = "set"
    else:
        direction = args.direction or ""
        if direction in ("+", "up", "increase"):
            sign, reason = 1, "increase"
        else:
            sign, reason = -1, "decrease"
        target = snap(current + step * sign, sign)

    if target == current:
        edge = "maximum" if target >= hi else "minimum" if target <= lo else "current"
        msg = f"Cursor already at {edge}: {current}px ({theme})"
        log_warn(msg)
        if not args.no_notify:
            notify("Cursor size", msg, "normal")
        return 0

    log_info(f"Cursor {reason}: {current}px -> {target}px ({theme})")
    if args.dry_run:
        print(target)
        return 0

    # Serialize key repeats and color builds, which also publish cursor size
    # and the same persistent environment/preferences.
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_fh = LOCK_FILE.open("w")
    except OSError as e:
        log_err(f"Cannot open lock file {LOCK_FILE}: {e}")
        return 1

    try:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
        except OSError as e:
            log_err(f"Cannot acquire cursor-size lock: {e}")
            return 1
        try:
            # Re-read under lock: a racing repeat may have moved us already.
            raced = detect_current_size()
            if raced != current and args.set is None and not args.reset:
                direction = args.direction or ""
                sign = 1 if direction in ("+", "up", "increase") else -1
                recomputed = snap(raced + step * sign, sign)
                log_debug(f"race: {current} -> {raced}, recomputed target {recomputed}")
                current, target = raced, recomputed
                if target == current:
                    log_warn(f"Limit reached under lock at {current}px")
                    return 0

            ok_compositor = apply_compositor(theme, target)
            apply_gsettings(theme, target)
            apply_dbus_env(target)
            ok_persist = True
            if not args.no_persist:
                ok_persist = persist_lua_env(theme, target)
                write_state(target)
                apply_gtk_settings(theme, target)
            verified = confirm_size(target)

            if not args.no_notify:
                if ok_compositor and verified:
                    notify("Cursor size", f"{theme} — {target}px")
                else:
                    notify("Cursor size", f"{theme} — {target}px (partial apply, check log)",
                           "critical")

            if not ok_compositor:
                return 1
            if not args.no_persist and not ok_persist:
                return 1
            return 0
        finally:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            lock_fh.close()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())

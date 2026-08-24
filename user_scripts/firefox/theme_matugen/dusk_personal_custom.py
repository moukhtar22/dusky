#!/usr/bin/env python3
"""
Dusk Personal Custom Setup (Arch Linux / Python 3.14+ / Firefox 140+)
========================================================================
Personal Firefox chrome customization that hides the built-in sidebar for
*your* profiles only — deliberately separate from the shared Dusky Sites setup
(dusky_sites_setup.py) so other people who use your dotfiles are unaffected.
dusky_sites_setup.py is never modified or touched by this script.

───────────────────────────────────────────────────────────────────────────
FEATURES — every element this script can hide or alter
───────────────────────────────────────────────────────────────────────────
Each entry in the FEATURES list below is a toggleable item with:
  * key        — CLI flag name (--<key> / --no-<key>) and config key
  * title      — short display name
  * desc       — what it does (shown in prompts and --list)
  * selectors  — CSS selectors hidden when enabled (None = no CSS)
  * pref       — user.js pref written when enabled (None = no pref)
  * default    — initial state until you answer the questionnaire

You can also append plain CSS to EXTRA_CSS_CONTENT (written verbatim into
every profile's chrome/dusk_personal_custom.css) for rules that don't fit
the feature list.

───────────────────────────────────────────────────────────────────────────
INTERACTIVE / CONFIG
───────────────────────────────────────────────────────────────────────────
  * First run from a terminal asks you y/n for every feature and saves your
    answers to ~/.config/dusk_personal_custom/config.json.
  * Later runs are non-interactive and reuse that config (idempotent).
  * `--configure` re-opens the questionnaire at any time.
  * `--<key>` / `--no-<key>` (e.g. `--no-launcher-rail`) override the config
    for that single run only — they are never persisted.
  * `--yes` / `--no-prompt` forces non-interactive mode (for automation);
    when stdin is not a terminal the script never prompts anyway.
  * `--list` prints the current effective feature states without installing.

───────────────────────────────────────────────────────────────────────────
What it does per profile:
  * Writes chrome/dusk_personal_custom.css (generated from the enabled
    features + EXTRA_CSS_CONTENT, verbatim)
  * Adds its own @import line to chrome/userChrome.css
  * Ensures the enabled prefs in user.js (and removes lines for features
    you switched OFF, so disabling truly un-does the change)

Works hand-in-hand with dusky_sites_setup.py:
  * This script manages ONLY dusk_personal_custom.css and its own @import line.
    It never touches dusky_menu.css, dusky's imports, or dusky's config.
  * The shared toolkit.legacyUserProfileCustomizations.stylesheets pref is
    only removed on uninstall while Dusky's chrome files are still referenced,
    so uninstalling this script can never break Dusky Sites.

Design guarantees:
  * Idempotent — safe to run any number of times; imports and prefs never
    duplicate, and already-correct files are left byte-for-byte untouched.
  * Atomic writes — every write goes through a temp file + fsync + same-directory
    rename, so a crash mid-write can never leave a truncated file behind.
  * Symlink-safe — writes *through* userChrome.css / user.js symlinks
    (dotfiles-friendly) instead of clobbering them.
  * Robust profile discovery — a deduplicated union of profiles.ini entries and
    a prefs.js/user.js directory scan, so orphan and never-yet-launched profiles
    are still covered. Non-profile dirs (Crash Reports, etc.) are skipped.

Usage:
  dusk_personal_custom.py              # install/ensure with saved config (asks on first run)
  dusk_personal_custom.py --configure  # re-run the interactive questionnaire, then apply
  dusk_personal_custom.py --list       # show effective feature states
  dusk_personal_custom.py --no-launcher-rail   # one-shot override for a single run
  dusk_personal_custom.py --uninstall  # remove only what this script deployed
  dusk_personal_custom.py --yes        # never prompt (uninstall confirm / questionnaire)
  dusk_personal_custom.py --help
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════
# FEATURES — EDIT THIS BLOCK
# ══════════════════════════════════════════════════════════════════════════
# Add, remove, or reorder entries freely; re-run the script to apply.
#
# Selector notes (verified against the Firefox 153 chrome DOM):
#   * Since the Firefox 136+ sidebar rewrite, `sidebar-main` is a custom
#     ELEMENT, not an ID — the old `#sidebar-main { display:none }` rules
#     match nothing on current Firefox. Use the element name.
#   * #sidebar-box is deliberately never hidden: native-sidebar extensions
#     (Sidebery and similar) render their vertical tabs/panels inside it.
#   * `sidebar-panel-header` renders inside the sidebar's panel <browser>
#     (a child document), which userChrome.css cannot style — that rule is
#     inert insurance; the wrappers below do the real hiding.
#   * Firefox's built-in AI chatbot sidebar (genai/chat.html — its #header and
#     #summarize-btn-container) ALSO renders in the sidebar <browser>
#     sub-document, which userChrome.css cannot style — do not add those
#     selectors here; disable the whole AI chatbot instead (pref
#     browser.ai.control.sidebarChatbot).
FEATURES = [
    {
        "key": "sidebar_container",
        "title": "Sidebar container",
        "desc": "hide the new-sidebar wrapper (#sidebar-container)",
        "selectors": ["#sidebar-container"],
        "pref": None,
        "default": True,
    },
    {
        "key": "launcher_rail",
        "title": "Launcher rail",
        "desc": "hide the launcher rail element (sidebar-main) — the icon column",
        "selectors": ["sidebar-main"],
        "pref": None,
        "default": True,
    },
    {
        "key": "panel_header",
        "title": "Panel header",
        "desc": "hide the panel header (#sidebar-header, sidebar-panel-header)",
        "selectors": ["#sidebar-header", "sidebar-panel-header"],
        "pref": None,
        "default": True,
    },
    {
        "key": "launcher_splitter",
        "title": "Launcher splitter",
        "desc": "hide the launcher drag handle (#sidebar-launcher-splitter)",
        "selectors": ["#sidebar-launcher-splitter"],
        "pref": None,
        "default": True,
    },
    {
        "key": "ai_window",
        "title": "AI chatbot window",
        "desc": "hide the AI chatbot window sidebar (#ai-window-box, #ai-window-splitter)",
        "selectors": ["#ai-window-box", "#ai-window-splitter"],
        "pref": None,
        "default": True,
    },
    {
        "key": "titlebar_buttons",
        "title": "Titlebar window buttons",
        "desc": "hide the window minimize/maximize/close buttons (.titlebar-buttonbox-container) — WM shortcuts or Alt+F4 still close the window",
        "selectors": [".titlebar-buttonbox-container"],
        "pref": None,
        "default": True,
    },
    {
        "key": "fxa_button",
        "title": "Firefox Account button",
        "desc": "hide the Firefox Account toolbar button (#fxa-toolbar-menu-button)",
        "selectors": ["#fxa-toolbar-menu-button"],
        "pref": None,
        "default": True,
    },
    {
        "key": "sidebar_splitter",
        "title": "Sidebar resize handle",
        "desc": "also hide #sidebar-splitter — the drag handle that resizes an open panel (Sidebery's width then isn't drag-adjustable)",
        "selectors": ["#sidebar-splitter"],
        "pref": None,
        "default": False,
    },
    {
        "key": "horizontal_tabs",
        "title": "Horizontal tabs",
        "desc": "disable Firefox's built-in vertical tabs so the tab strip returns to the top (sidebar.verticalTabs=false)",
        "selectors": None,
        "pref": ("sidebar.verticalTabs", "false"),
        "default": False,
    },
    {
        "key": "stylesheets",
        "title": "userChrome loading",
        "desc": "keep toolkit.legacyUserProfileCustomizations.stylesheets=true — REQUIRED for all of the above (also for Dusky's chrome CSS)",
        "selectors": None,
        "pref": ("toolkit.legacyUserProfileCustomizations.stylesheets", "true"),
        "default": True,
    },
]


# ── Optional extra CSS, appended verbatim (plain CSS, no escaping needed) ──
EXTRA_CSS_CONTENT = r""""""


# ── Reserved CLI words feature keys must not collide with ─────────────────
_RESERVED_ARGS = {
    "--help", "-h", "--uninstall", "--purge", "--configure", "--interactive", "-i",
    "--yes", "--no-prompt", "--list", "--status",
}


def _feature_flag(key: str) -> str:
    """snake_case key -> kebab-case CLI flag suffix (ai_window -> ai-window)."""
    return key.replace("_", "-")


def _validate_features() -> None:
    seen: set[str] = set()
    for feat in FEATURES:
        key = feat["key"]
        if key in seen:
            raise ValueError(f"duplicate feature key {key!r}")
        seen.add(key)
        flag = f"--{_feature_flag(key)}"
        no_flag = f"--no-{_feature_flag(key)}"
        if flag in _RESERVED_ARGS or no_flag in _RESERVED_ARGS:
            raise ValueError(f"feature key {key!r} collides with a reserved CLI flag")
        if not (feat.get("selectors") or feat.get("pref")):
            raise ValueError(f"feature {key!r} has neither selectors nor pref")


_validate_features()


# ── Terminal styling ──────────────────────────────────────────────────────
C_CYAN = "\033[0;36m"
C_GREEN = "\033[0;32m"
C_BLUE = "\033[0;34m"
C_YELLOW = "\033[1;33m"
C_RED = "\033[0;31m"
C_RESET = "\033[0m"


def print_step(msg: str) -> None:
    print(f"{C_BLUE}==>{C_RESET} {msg}")


def print_success(msg: str) -> None:
    print(f"{C_GREEN}✓{C_RESET} {msg}")


def print_warn(msg: str) -> None:
    print(f"{C_YELLOW}[!] {msg}")


def print_error(msg: str) -> None:
    print(f"{C_RED}[!] Error:{C_RESET} {msg}")
    sys.exit(1)


# ── Identity (namespaced so it can never collide with Dusky's files) ──────
CSS_FILE_NAME = "dusk_personal_custom.css"
IMPORT_LINE = f'@import url("{CSS_FILE_NAME}");'

# The stylesheets pref is SHARED with dusky_sites_setup.py — it is only
# removed while Dusky's chrome files are still referenced, so this script can
# never silently break Dusky's theming (neither on uninstall nor when the
# `stylesheets` feature is switched off).
SHARED_PREF = ("toolkit.legacyUserProfileCustomizations.stylesheets", "true")

# Prefs we always remove on uninstall (never shared with Dusky). Derived from
# FEATURES so editing a feature's pref can never drift from uninstall behavior.
_UNSHARED_PREFS = [f["pref"] for f in FEATURES if f["pref"] and f["pref"] != SHARED_PREF]


# ── Feature state: defaults ← config file ← CLI overrides ─────────────────
def feature_keys() -> list[str]:
    return [feat["key"] for feat in FEATURES]


def default_enabled() -> dict[str, bool]:
    return {feat["key"]: bool(feat["default"]) for feat in FEATURES}


def _xdg_config_home(home: Path) -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if raw:
        try:
            p = Path(raw).expanduser()
            if p.is_absolute():
                return p
        except (OSError, RuntimeError):
            pass  # unresolvable value — fall back to the XDG default below
    return home / ".config"


def config_path(home: Path) -> Path:
    return _xdg_config_home(home) / "dusk_personal_custom" / "config.json"


def _as_bool(value) -> bool:
    """Robust bool coercion for config values (handles hand-edited strings)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def load_config(home: Path) -> dict[str, bool]:
    """Load saved feature states. Corrupt/missing config → {} (caller falls back to defaults)."""
    path = config_path(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("features") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    keys = set(feature_keys())
    return {k: _as_bool(v) for k, v in raw.items() if k in keys}


def save_config(home: Path, enabled: dict[str, bool]) -> Path:
    path = config_path(home)
    payload = {"features": {feat["key"]: bool(enabled[feat["key"]]) for feat in FEATURES}}
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")
    return path


def resolve_enabled(home: Path, overrides: dict[str, bool] | None = None) -> dict[str, bool]:
    """Effective feature states: defaults ← saved config ← CLI overrides."""
    enabled = default_enabled()
    enabled.update(load_config(home))
    if overrides:
        enabled.update(overrides)
    return enabled


# ── Interactive questionnaire ─────────────────────────────────────────────
def ask_yes_no(prompt: str, default: bool) -> bool | None:
    """One y/n question. Returns the answer, or None if the user aborted (q / EOF)."""
    suffix = "Y/n" if default else "y/N"
    try:
        resp = input(f"{prompt} [{suffix}] ").strip().lower()
    except EOFError:
        return None
    if resp in ("q", "quit"):
        return None
    if resp in ("y", "yes"):
        return True
    if resp in ("n", "no"):
        return False
    return default


def run_questionnaire(current: dict[str, bool]) -> dict[str, bool] | None:
    """Ask about every feature. Returns the new states, or None if aborted."""
    print(f"\n{C_CYAN}Dusk Personal Custom — feature configuration{C_RESET}\n")
    print("Answer y/n for each element (Enter keeps the default). Type 'q' to abort.\n")
    enabled = dict(current)
    for feat in FEATURES:
        answer = ask_yes_no(f"{feat['title']}: {feat['desc']}", enabled[feat["key"]])
        if answer is None:
            return None
        enabled[feat["key"]] = answer
    if not enabled["stylesheets"]:
        print_warn(
            "'userChrome loading' is OFF — userChrome.css will not load, so the sidebar "
            "hider (and Dusky's chrome CSS) will stop working."
        )
    return enabled


# ── CSS generation from the enabled features ──────────────────────────────
def build_css(enabled: dict[str, bool]) -> str:
    """Generate dusk_personal_custom.css content for the given feature states."""
    lines = [
        "/* Auto-generated by Dusk Personal Custom — see the FEATURES list in",
        "   dusk_personal_custom.py. Re-run the script or use `--configure` to change. */",
        "",
    ]
    for feat in FEATURES:
        if not feat["selectors"]:
            continue
        lines.append(f"/* [{feat['key']}] {feat['title']} */")
        if enabled[feat["key"]]:
            lines.append(f"{', '.join(feat['selectors'])} {{")
            lines.append("  display: none !important;")
            lines.append("}")
        else:
            lines.append("/* disabled */")
        lines.append("")
    extra = EXTRA_CSS_CONTENT.strip()
    if extra:
        lines.append("/* [extra] custom rules from EXTRA_CSS_CONTENT */")
        lines.append(extra)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ── Prefs for the current feature states ──────────────────────────────────
def prefs_for_enabled(enabled: dict[str, bool]) -> list[tuple[str, str]]:
    return [feat["pref"] for feat in FEATURES if feat["pref"] and enabled[feat["key"]]]


def prefs_for_disabled(enabled: dict[str, bool]) -> list[tuple[str, str]]:
    """Prefs this script would write but the feature is OFF — remove them so
    disabling truly reverses the change."""
    return [feat["pref"] for feat in FEATURES if feat["pref"] and not enabled[feat["key"]]]


# ── Atomic, symlink-aware writes ──────────────────────────────────────────
def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically (unique temp + fsync + same-dir rename + dir fsync).

    If ``path`` is a symlink (typical for dotfiles repos), write *through* it
    to the resolved target so the symlink is preserved, never replaced.
    Preserves the original file's permission bits, uses a random-named temp
    file created with O_EXCL so concurrent runs can never collide, fsyncs the
    data *and* the parent directory before the rename (so the new directory
    entry survives a crash), and cleans the temp file up on failure — a crash
    at any point leaves either the old file or the complete new file, never a
    truncated one and never a dangling temp.
    """
    try:
        target = path.resolve() if path.is_symlink() else path
    except (OSError, RuntimeError):
        target = path  # symlink loop / unresolvable — write the path itself
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = None
    try:
        if target.is_file():
            mode = target.stat().st_mode
    except OSError:
        pass
    tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}.{secrets.token_hex(6)}")
    data = text.encode("utf-8")
    created = False
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        created = True
        try:
            view = memoryview(data)
            written = 0
            while written < len(view):
                try:
                    written += os.write(fd, view[written:])
                except InterruptedError:
                    continue
            os.fsync(fd)
        finally:
            os.close(fd)
        if mode is not None:
            os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, target)
        # Persist the rename on Linux filesystems (ext4/btrfs/zfs).
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except OSError:
        # Only remove a temp file we actually created (O_EXCL collision with a
        # pre-existing stale file is ~impossible, but never delete one we didn't).
        if created:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _remove_file(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            return True
    except OSError as e:
        print_warn(f"Could not remove {path}: {e}")
    return False


# ── user.js prefs ─────────────────────────────────────────────────────────
def ensure_prefs(user_js: Path, ensure: list[tuple[str, str]], remove: list[tuple[str, str]]) -> bool:
    """Write the ``ensure`` prefs, drop the exact ``remove`` lines. Returns True if changed."""
    try:
        content = user_js.read_text(encoding="utf-8", errors="replace") if user_js.is_file() else ""
    except OSError as e:
        print_warn(f"Could not read {user_js}: {e}")
        return False
    original = content

    for pref_name, pref_val in ensure:
        pref_re = re.compile(rf'user_pref\(\s*"{re.escape(pref_name)}"\s*,\s*[^)]+\)\s*;')
        pref_line = f'user_pref("{pref_name}", {pref_val});'
        if pref_re.search(content):
            content = pref_re.sub(pref_line, content)
        elif pref_line not in content:
            content = content.rstrip() + f"\n{pref_line}\n"

    if remove:
        exact_lines = {f'user_pref("{name}", {val});' for name, val in remove}
        content = "".join(
            ln for ln in content.splitlines(keepends=True) if ln.strip() not in exact_lines
        )

    if content != original:
        try:
            atomic_write_text(user_js, content)
            return True
        except OSError as e:
            print_warn(f"Could not write {user_js}: {e}")
            return False
    return False


def restore_pref_line(user_js: Path, prefs: list[tuple[str, str]]) -> bool:
    """Remove pref lines from ``prefs``. Returns True if any removed.

    Matches are regex-based but anchored to the exact pref name AND value this
    script would write, tolerant of surrounding whitespace and trailing
    comments — so disabling a feature / uninstalling removes the pref even if
    the line was hand-edited with different spacing, while a line holding a
    *different* value for the same pref (e.g. the user set ``true`` by hand)
    is deliberately left alone. Pref names are case-sensitive in Firefox, so
    matching is case-sensitive too.
    """
    if not (user_js.is_file() or user_js.is_symlink()):
        return False
    try:
        content = user_js.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print_warn(f"Could not read {user_js}: {e}")
        return False
    patterns = [
        re.compile(rf'^\s*user_pref\(\s*"{re.escape(name)}"\s*,\s*{re.escape(val)}\s*\)\s*;.*$')
        for name, val in prefs
    ]
    lines = content.splitlines(keepends=True)
    kept = [ln for ln in lines if not any(p.match(ln) for p in patterns)]
    if len(kept) == len(lines):
        return False
    try:
        atomic_write_text(user_js, "".join(kept))
        print_success(f"Removed pref line from {user_js}")
        return True
    except OSError as e:
        print_warn(f"Could not write {user_js}: {e}")
        return False


# ── Profile discovery (mirrors dusky_sites_setup.py, hardened) ────────────
def iter_firefox_profiles(base_dir: Path):
    """Yield profile directories: profiles.ini entries ∪ prefs.js/user.js scan (deduped).

    Using a union (rather than "ini only when present") means orphan profiles —
    real profile directories that exist but are not listed in profiles.ini —
    are still covered, while never-yet-launched profiles are caught by accepting
    ``user.js`` as well as ``prefs.js`` (Firefox writes prefs.js on first run,
    while user.js can already exist). Results are resolved, deduplicated, and
    sorted for deterministic order. Non-profile dirs (Crash Reports, Pending
    Pings, Profile Groups, etc.) contain neither file and are never yielded.

    profiles.ini is parsed section-aware (no state bleed between sections):
    ``[ProfileN]`` sections contribute their ``Path`` (honouring ``IsRelative``),
    and ``[Install...]`` sections' ``Default`` entries (Firefox 67+ dedicated
    profiles) are honoured too. Comments, blank lines, surrounding quotes and
    inline ``;``/``#`` comments are tolerated. A never-yet-launched profile
    listed in profiles.ini is still yielded if its directory exists.
    """
    found: set[Path] = set()

    def emit(profile: Path) -> None:
        try:
            resolved = profile.resolve()
        except OSError:
            resolved = profile
        if resolved not in found:
            found.add(resolved)

    ini = base_dir / "profiles.ini"
    if ini.is_file():
        try:
            text = ini.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""

        sections: list[tuple[str, dict[str, str]]] = []
        cur_sec = ""
        cur_kv: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and "]" in line:
                if cur_sec:
                    sections.append((cur_sec, cur_kv))
                cur_sec = line[1:line.index("]")].strip().lower()
                cur_kv = {}
            elif "=" in line and cur_sec:
                k, v = line.split("=", 1)
                v = re.split(r"\s+[;#]", v)[0].strip().strip('\"').strip("'")
                cur_kv[k.strip().lower()] = v
        if cur_sec:
            sections.append((cur_sec, cur_kv))

        def consider(sec_name: str, kv: dict[str, str]) -> None:
            if sec_name.startswith("profile"):
                rel = kv.get("path")
                is_relative = kv.get("isrelative", "1") != "0"
            elif sec_name.startswith("install"):
                rel = kv.get("default")
                is_relative = rel is not None and not rel.startswith("/")
            else:
                return
            if not rel:
                return
            p = Path(rel)
            profile = (base_dir / p) if is_relative else p
            try:
                if profile.is_dir():
                    emit(profile)
            except OSError:
                if profile.is_dir():
                    emit(profile)

        for sec_name, kv in sections:
            consider(sec_name, kv)

    try:
        for profile in base_dir.iterdir():
            if profile.is_dir() and (
                (profile / "prefs.js").is_file() or (profile / "user.js").is_file()
            ):
                emit(profile)
    except OSError:
        pass

    yield from sorted(found, key=str)


def _profile_base_dirs(home: Path) -> list[Path]:
    """Firefox-family profile roots: native, Flatpak, and Snap variants."""
    return [
        home / ".mozilla" / "firefox",
        home / ".config" / "mozilla" / "firefox",
        home / ".librewolf",
        home / ".config" / "librewolf",
        home / ".zen",
        home / ".config" / "zen",
        home / ".waterfox",
        home / ".floorp",
        home / ".firedragon",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
        home / ".var" / "app" / "io.gitlab.librewolf-community" / ".librewolf",
        home / ".var" / "app" / "app.zen_browser.zen" / ".zen",
        home / ".var" / "app" / "one.ablaze.floorp" / ".floorp",
        home / ".var" / "app" / "net.waterfox.waterfox" / ".waterfox",
        home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
    ]


_BROWSER_PROC_PATTERNS = {
    "firefox": re.compile(r"(?:^|/)(?:firefox(?:-bin|-esr|-nightly)?|org\.mozilla\.firefox)(?:\s|$)"),
    "librewolf": re.compile(r"(?:^|/)(?:librewolf(?:-bin)?|io\.gitlab\.librewolf)(?:\s|$)"),
    "zen": re.compile(r"(?:^|/)(?:zen(?:-bin|-browser)?|app\.zen_browser\.zen)(?:\s|$)"),
    "waterfox": re.compile(r"(?:^|/)(?:waterfox(?:-bin|-g)?|net\.waterfox\.waterfox)(?:\s|$)"),
    "floorp": re.compile(r"(?:^|/)(?:floorp(?:-bin)?|one\.ablaze\.floorp)(?:\s|$)"),
    "firedragon": re.compile(r"(?:^|/)(?:firedragon(?:-bin)?|org\.garudalinux\.firedragon)(?:\s|$)"),
}


def _browser_processes_running() -> list[str]:
    """Return names of detected running Firefox-family browsers (informational).

    Inspects /proc directly instead of `pgrep` — zero external dependencies
    (so it also works in minimal chroots/containers where pgrep is missing) and
    it catches binary variants (`zen-bin`, `librewolf-bin`, `firefox-esr`) and
    Flatpak app IDs that a `pgrep -x` exact-comm match can never see. Only
    processes owned by the current user are considered; returns [] if /proc is
    unavailable.
    """
    current_uid = os.getuid()
    detected: set[str] = set()
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return []
    for pid in pids:
        pid_dir = f"/proc/{pid}"
        try:
            if os.stat(pid_dir).st_uid != current_uid:
                continue
        except OSError:
            continue
        comm = ""
        cmdline = ""
        try:
            with open(f"{pid_dir}/comm", "r", encoding="utf-8", errors="replace") as f:
                comm = f.read().strip()
        except OSError:
            pass
        try:
            with open(f"{pid_dir}/cmdline", "rb") as f:
                cmdline = " ".join(
                    p.decode("utf-8", errors="replace")
                    for p in f.read().split(b"\x00") if p
                )
        except OSError:
            pass
        for browser, pattern in _BROWSER_PROC_PATTERNS.items():
            if browser not in detected and (pattern.search(comm) or pattern.search(cmdline)):
                detected.add(browser)
    return sorted(detected)


# ── Per-profile install / restore ─────────────────────────────────────────
def setup_profile(profile: Path, css_content: str, ensure_prefs_list: list, remove_prefs_list: list) -> bool:
    """Install CSS + import + prefs into one profile. Returns True if anything changed."""
    chrome_dir = profile / "chrome"
    try:
        chrome_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print_warn(f"Could not create {chrome_dir}: {e}")

    # Never remove the shared stylesheets pref while Dusky's chrome files are
    # still referenced — same guarantee the uninstall path honours.
    remove_unshared = [p for p in remove_prefs_list if p != SHARED_PREF]
    remove_shared = [p for p in remove_prefs_list if p == SHARED_PREF]
    prefs_to_remove = list(remove_unshared)
    if remove_shared:
        if _dusky_chrome_present(chrome_dir):
            print_warn(
                f"{profile}: keeping the shared stylesheets pref — Dusky's chrome files are still in use."
            )
        else:
            prefs_to_remove.extend(remove_shared)

    changed = False
    if ensure_prefs(profile / "user.js", ensure_prefs_list, prefs_to_remove):
        changed = True

    # CSS file — write only if missing or different (idempotent; picks up edits
    # to FEATURES / EXTRA_CSS_CONTENT).
    css_path = chrome_dir / CSS_FILE_NAME
    try:
        if css_path.is_file() and css_path.read_text(encoding="utf-8", errors="replace") == css_content:
            pass
        else:
            atomic_write_text(css_path, css_content)
            changed = True
    except OSError as e:
        print_warn(f"Could not write {css_path}: {e}")

    # @import line — add only if not already present (idempotent; whitespace-insensitive).
    # W3C CSS: @import must follow any leading @charset/@layer statements and must
    # precede @namespace or any other rule, or Gecko silently drops it. So scan
    # past leading blank lines / comments (incl. multi-line) / @charset / @layer /
    # existing @imports, then insert our import at the first rule boundary.
    uc_path = chrome_dir / "userChrome.css"
    try:
        existing = uc_path.read_text(encoding="utf-8", errors="replace") if uc_path.is_file() else ""
        already_imported = any(_is_our_import(ln) for ln in existing.splitlines())
        if already_imported:
            pass
        else:
            lines = existing.splitlines(keepends=True)
            insert_at = 0
            in_comment = False
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if in_comment:
                    if "*/" in stripped:
                        in_comment = False
                        rest = stripped.partition("*/")[2].strip()
                        if rest:  # rule content after the comment close
                            break
                    insert_at = idx + 1
                    continue
                if not stripped:
                    insert_at = idx + 1
                    continue
                if stripped.startswith("/*"):
                    if "*/" in stripped:
                        rest = stripped.partition("*/")[2].strip()
                        if rest:  # comment + rule on the same line -> boundary
                            break
                        insert_at = idx + 1
                    else:
                        in_comment = True
                        insert_at = idx + 1
                    continue
                lower = stripped.lower()
                if lower.startswith("@charset"):
                    insert_at = idx + 1
                    continue
                if lower.startswith("@layer"):
                    # Only the statement form (`@layer name;`) may precede @import;
                    # a block form (`@layer name {`) is a hard boundary.
                    if "{" in stripped:
                        break
                    insert_at = idx + 1
                    continue
                if lower.startswith("@import"):
                    insert_at = idx + 1
                    continue
                break  # first @namespace or style rule
            # Files without a trailing newline would glue the import onto the
            # previous line; make sure it sits on its own line.
            if insert_at > 0 and lines[insert_at - 1] and not lines[insert_at - 1].endswith("\n"):
                lines[insert_at - 1] += "\n"
            lines.insert(insert_at, f"{IMPORT_LINE}\n")
            atomic_write_text(uc_path, "".join(lines))
            changed = True
    except OSError as e:
        print_warn(f"Could not write {uc_path}: {e}")

    return changed


_OUR_IMPORT_RE = re.compile(
    r"@import\s+url\(\s*[\"']?" + re.escape(CSS_FILE_NAME) + r"[\"']?\s*\)"
)


def _is_our_import(line: str) -> bool:
    """Robust check: is this an @import line that references our CSS file?

    Matches ``@import url("dusk_personal_custom.css");`` allowing optional
    whitespace and single/double quotes around the filename, plus trailing
    comments. Anchors the filename inside ``url(...)`` so unrelated imports
    (e.g. ``dusk_personal_custom_old.css``) can never false-positive — both
    when avoiding duplicate imports and when removing our import on uninstall.
    """
    return _OUR_IMPORT_RE.search(line) is not None


def _dusky_chrome_present(chrome_dir: Path) -> bool:
    """True if Dusky's chrome files are still in use — keeps the shared pref alive.

    Checks both menu (userChrome) and content (userContent) theming, matching the
    real-world files (dusky_menu.css / dusky_content.css), so uninstalling this
    script can never silently break Dusky's web-content theming either.
    """
    try:
        if (chrome_dir / "dusky_menu.css").is_file():
            return True
        if (chrome_dir / "dusky_content.css").is_file():
            return True
        for uc_name in ("userChrome.css", "userContent.css"):
            uc = chrome_dir / uc_name
            if uc.is_file():
                text = uc.read_text(encoding="utf-8", errors="replace")
                if "dusky_menu.css" in text or "dusky_content.css" in text:
                    return True
    except OSError:
        pass
    return False


def restore_profile(profile: Path) -> bool:
    """Remove this script's deployed artifacts from one profile. Returns True if any removed."""
    chrome_dir = profile / "chrome"
    removed = False
    if chrome_dir.is_dir():
        css_path = chrome_dir / CSS_FILE_NAME
        if _remove_file(css_path):
            print_success(f"Removed {css_path}")
            removed = True

        uc_path = chrome_dir / "userChrome.css"
        if uc_path.is_file() or uc_path.is_symlink():
            try:
                lines = uc_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as e:
                print_warn(f"Could not read {uc_path}: {e}")
                return removed
            kept = [ln for ln in lines if not _is_our_import(ln)]
            if len(kept) != len(lines):
                while kept and kept[0].strip() == "":
                    kept.pop(0)
                new_text = "\n".join(kept).rstrip() + "\n"
                if new_text.strip():
                    try:
                        atomic_write_text(uc_path, new_text)
                        print_success(f"Removed import from {uc_path}")
                    except OSError as e:
                        print_warn(f"Could not write {uc_path}: {e}")
                else:
                    # File held nothing but our import. For a symlink (dotfiles),
                    # write the empty state through to the resolved target so the
                    # symlink survives and no stale @import is left behind; for a
                    # plain file, just delete it.
                    if uc_path.is_symlink():
                        try:
                            atomic_write_text(uc_path, "")
                            print_success(f"Emptied {uc_path} (symlink target)")
                        except OSError as e:
                            print_warn(f"Could not empty {uc_path}: {e}")
                    else:
                        _remove_file(uc_path)
                        print_success(f"Removed empty {uc_path}")
                removed = True

        # Unshared pref lines (sidebar.verticalTabs=false) are never gated on
        # Dusky — exact-line match makes this a safe no-op if never written.
        if restore_pref_line(profile / "user.js", _UNSHARED_PREFS):
            removed = True

        # Shared pref: only remove if Dusky isn't still using userChrome.
        if not _dusky_chrome_present(chrome_dir):
            if restore_pref_line(profile / "user.js", [SHARED_PREF]):
                removed = True
    return removed


# ── Install / uninstall drivers ───────────────────────────────────────────
def run_install(home: Path, enabled: dict[str, bool]) -> None:
    print(f"\n{C_CYAN}Dusk Personal Custom Setup (Arch Linux / Python 3.14+){C_RESET}\n")

    print_step("Feature configuration:")
    for feat in FEATURES:
        mark = f"{C_GREEN}✓{C_RESET}" if enabled[feat["key"]] else f"{C_RED}✗{C_RESET}"
        print(f"  {mark} {feat['key']:<20} {feat['title']}")
    if not enabled["stylesheets"]:
        print_warn(
            "'userChrome loading' is OFF — userChrome.css will not load, so the sidebar "
            "hider (and Dusky's chrome CSS) will stop working."
        )

    css_content = build_css(enabled)
    ensure = prefs_for_enabled(enabled)
    remove = prefs_for_disabled(enabled)

    running = _browser_processes_running()
    if running:
        print_warn(f"Detected running browser(s): {', '.join(running)}")
        print_warn("Changes will take effect at the next browser restart.")

    print_step("Scanning Firefox-family profiles...")
    touched = 0
    total = 0
    for base_dir in _profile_base_dirs(home):
        if not base_dir.is_dir():
            continue
        for profile in iter_firefox_profiles(base_dir):
            total += 1
            if setup_profile(profile, css_content, ensure, remove):
                touched += 1
            print_success(f"Ensured personal custom in {profile}")

    if total == 0:
        print_warn("No Firefox-family profiles found. Nothing to do.")
        return
    if touched == 0:
        print_success(f"All {total} profile(s) already up to date — no changes needed.")
    else:
        print_success(f"Updated {touched}/{total} profile(s).")

    print(f"\n{C_GREEN}[+] Setup complete. Restart your browser to apply the CSS.{C_RESET}\n")


def run_uninstall(home: Path) -> None:
    print(f"\n{C_CYAN}[-] Dusk Personal Custom Uninstaller{C_RESET}\n")

    running = _browser_processes_running()
    if running:
        print_warn(f"Detected running browser(s): {', '.join(running)}")
        print_warn("It is strongly recommended to close them before continuing.")

    print_step("Removing deployed personal custom artifacts...")
    removed_any = 0
    for base_dir in _profile_base_dirs(home):
        if not base_dir.is_dir():
            continue
        for profile in iter_firefox_profiles(base_dir):
            if restore_profile(profile):
                removed_any += 1

    if removed_any == 0:
        print_warn("No personal custom artifacts found to remove.")
    else:
        print_success(f"Cleaned {removed_any} profile(s).")

    print(f"\n{C_GREEN}[+] Uninstall complete. Dusky Sites artifacts were left untouched.{C_RESET}\n")


# ── CLI ───────────────────────────────────────────────────────────────────
def _print_list(enabled: dict[str, bool], home: Path) -> None:
    print(f"\n{C_CYAN}Dusk Personal Custom — current features{C_RESET}\n")
    print(f"{'Feature key':<20} {'State':<7} What it does")
    print("-" * 78)
    for feat in FEATURES:
        state = f"{C_GREEN}on{C_RESET}" if enabled[feat["key"]] else f"{C_RED}off{C_RESET}"
        print(f"{feat['key']:<20} {state:<18} {feat['title']}: {feat['desc']}")
    cfg = config_path(home)
    print(f"\nConfig file: {cfg} ({'exists' if cfg.is_file() else 'not created yet — defaults shown'})")
    print("Change: run `--configure`, or pass `--<key>` / `--no-<key>` for one run.\n")


def _print_help() -> None:
    print(__doc__)
    print("Options:")
    print("  --configure, -i, --interactive   Re-run the interactive questionnaire, then apply.")
    print("  --list, --status                 Show effective feature states without installing.")
    print("  --yes, --no-prompt               Never prompt (skips questionnaire / uninstall confirm).")
    print("  --uninstall, --purge             Remove only what this script deployed.")
    print("  Feature flags (one-shot, not persisted):")
    for feat in FEATURES:
        flag = _feature_flag(feat["key"])
        print(f"    --{flag:<20} enable  |  --no-{flag:<17} disable  ({feat['title']})")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    home = Path.home()

    if any(a in ("--help", "-h") for a in args):
        _print_help()
        return

    # Reject unknown flags on every path (reliability: typos fail loudly instead
    # of silently doing nothing — including on --uninstall).
    known = set(_RESERVED_ARGS)
    for feat in FEATURES:
        flag = _feature_flag(feat["key"])
        known.add(f"--{flag}")
        known.add(f"--no-{flag}")
    unknown = [a for a in args if a not in known]
    if unknown:
        print_error(f"Unknown option(s): {', '.join(unknown)} — run with --help to see valid flags.")

    if any(a in ("--uninstall", "--purge") for a in args):
        auto_yes = any(a in ("--yes", "--no-prompt") for a in args)
        if not auto_yes:
            try:
                resp = input(
                    "Are you sure you want to uninstall Dusk Personal Custom (sidebar hider)? [y/N] "
                ).strip().lower()
            except EOFError:
                resp = "n"
            if resp not in ("y", "yes"):
                print("Aborted.")
                return
        run_uninstall(home)
        return

    want_configure = any(a in ("--configure", "--interactive", "-i") for a in args)
    want_list = any(a in ("--list", "--status") for a in args)
    no_prompt = any(a in ("--yes", "--no-prompt") for a in args)

    # One-shot CLI overrides (never persisted).
    overrides: dict[str, bool] = {}
    for feat in FEATURES:
        key = feat["key"]
        flag = _feature_flag(key)
        if f"--{flag}" in args:
            overrides[key] = True
        elif f"--no-{flag}" in args:
            overrides[key] = False

    enabled = resolve_enabled(home, overrides)
    config_exists = config_path(home).is_file()

    if want_list:
        _print_list(enabled, home)
        return

    if want_configure or (not config_exists and not no_prompt and not overrides and sys.stdin.isatty()):
        answered = run_questionnaire(enabled)
        if answered is None:
            print("Aborted.")
            return
        enabled = answered
        saved = save_config(home, enabled)
        print_success(f"Configuration saved to {saved}")

    if not want_configure and not overrides and config_exists and sys.stdin.isatty():
        print_warn("Using saved configuration. Run with --configure to change it.")

    run_install(home, enabled)


if __name__ == "__main__":
    main()

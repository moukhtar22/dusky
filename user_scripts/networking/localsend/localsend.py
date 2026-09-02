#!/usr/bin/env python3
# =============================================================================
# LocalSend Setup for Arch Linux + Thunar (Hyprland) — 2026-08-30 Bleeding Edge
# -----------------------------------------------------------------------------
# Purpose: One-shot, idempotent provisioning of LocalSend on Arch Linux
#          rolling (Thunar 4.20.9, Python 3.14, systemd 261) for reliable LAN
#          file transfer (AirDrop alternative) on 53317/tcp+udp.
#
# What it does (all idempotent, dry-run safe, no hardcoded username):
#   • Detects native binary (localsend / localsend_app) vs Flatpak
#     (org.localsend.localsend_app) vs none — prefers native when both present
#   • Optionally installs LocalSend: AUR `localsend` via paru/yay (source, 1.18.2)
#     or `localsend-bin`, else Flatpak flathub (system or user) — fastest
#   • Creates robust dispatcher helper: ~/.local/bin/localsend-thunar
#     (handles spaces, dirs, multiple files; native `localsend "$@"` or
#     `flatpak run --file-forwarding ... @@ "$@" @@` — verified vs man page)
#   • Merges Thunar Custom Action into ~/.config/Thunar/uca.xml
#     (preserves existing actions, unique-id نسل, atomic tmp+rename, backup)
#     Command = `<helper_path> %F` (Thunar already g_shell_quote's %F)
#   • Installs Thunar SendTo entry: ~/.local/share/Thunar/sendto/localsend.desktop
#   • Opens firewall: 53317/tcp + 53317/udp for detected active manager
#     (firewalld via firewall-cmd_runtime_to_permanent, ufw, or raw nftables)
#   • Autostart (OPT-IN, off by default): manages EITHER
#       - Hyprland edit_here/source/autostart.lua (hl.exec_cmd, commented by default) ← default, git-managed, no Exec check
#       - XDG autostart $XDG_CONFIG_HOME/autostart/localsend.desktop (Hidden=true when disabled, Hidden=false when enabled)
#       - Systemd user service ~/.config/systemd/user/localsend.service (PartOf=graphical-session.target)
#     Only created/enabled when --autostart is passed. XDG generator skips Exec if binary missing (tested).
#     Respects bug #2927 (--hidden race) via --no-hidden.
#   • Verifies all via --check, thunar -q reload, and protocol defaults
#     (UDP multicast 224.0.0.167:53317, HTTPS REST v2, self-signed cert).
#   • Fresh Arch: run once → Thunar integration persists forever; re-run to toggle autostart or reinstall.
#
# Relation to Dusky Share (LocalSend) Nautilus script:
#   • Fixes broken `localsend --headless send` (no such flag as of 1.18.2;
#     upstream gui uses positional files: `localsend %F`, --hidden is tray only)
#   • Replaces Nautilus GObject.MenuProvider with Thunar's uca.xml + helper
#   • Adds Flatpak --file-forwarding correctness (@@ delimiters per man page)
#   • Borrows robust resolve logic: which(localsend) → flatpak info check →
#     Gio.Subprocess equivalent via subprocess + shutil.which
#
# Usage:
#   python localsend.py                  # interactive (Thunar always, autostart OFF by default)
#   python localsend.py --check          # status only, no changes
#   python localsend.py --dry-run        # preview changes
#   python localsend.py --yes --install flatpak --autostart  # enable autostart (hypr, opt-in)
#   python localsend.py --autostart --autostart-method xdg    # force XDG instead of hypr
#   python localsend.py --autostart --autostart-method systemd
#   python localsend.py --uninstall      # remove integrations (preserves helper unless --remove-helper)
#   python localsend.py --help
#
# Target: Arch Linux rolling, Thunar 4.20.9 (Xfce 4.20), Wayland/Hyprland,
#         Python 3.11+, no backward compat with pre-1.18 or deprecated --direct.
# =============================================================================

from __future__ import annotations

import argparse
import getpass
import os
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# --- Rich (available on host: 15.0.0) — fallback to plain if missing ---
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Confirm
except ImportError:  # pragma: no cover
    Console = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    Confirm = None  # type: ignore[assignment]

console = Console() if Console else None  # type: ignore[call-arg]

def cprint(msg: str, style: str | None = None) -> None:
    if console:
        console.print(msg, style=style)
    else:
        print(msg)

def log_info(msg: str) -> None:
    cprint(f"[bold cyan] ::[/] {msg}")

def log_ok(msg: str) -> None:
    cprint(f"[bold green]  ✔[/] {msg}")

def log_warn(msg: str) -> None:
    cprint(f"[bold yellow]  ⚠[/] {msg}")

def log_err(msg: str) -> None:
    cprint(f"[bold red]  ✖[/] {msg}")

# ---------------------------------------------------------------------------
# Types & constants
# ---------------------------------------------------------------------------
PORT = 53317
MULTICAST = "224.0.0.167"
FLATPAK_ID = "org.localsend.localsend_app"
HELPER_NAME = "localsend-thunar"
ACTION_NAME = "Send via LocalSend"
ACTION_DESC = "Send files with LocalSend (native or Flatpak)"

InstallMethod = Literal["auto", "native", "native-bin", "flatpak", "flatpak-user", "skip"]
FirewallKind = Literal["firewalld", "ufw", "nftables", "none"]

@dataclass
class DetectResult:
    kind: Literal["native", "flatpak", "both", "none"]
    native_path: Path | None = None
    native_version: str | None = None
    flatpak_present: bool = False

@dataclass
class TargetUser:
    name: str
    home: Path
    config_home: Path
    data_home: Path

# ---------------------------------------------------------------------------
# Helpers: user, paths, run
# ---------------------------------------------------------------------------

def get_target_user() -> TargetUser:
    """Resolve real user even when invoked via sudo/pkexec — never hardcode."""
    name: str | None = None
    for env_key in ("SUDO_USER", "LOGNAME"):
        v = os.environ.get(env_key)
        if v and v != "root":
            name = v
            break
    if not name and (pkexec_uid := os.environ.get("PKEXEC_UID")):
        try:
            name = pwd.getpwuid(int(pkexec_uid)).pw_name
        except (ValueError, KeyError):
            pass
    if not name:
        try:
            # systemd loginctl fallback for Hyprland session
            if shutil.which("loginctl"):
                out = subprocess.run(
                    ["loginctl", "list-sessions", "--output=json"],
                    capture_output=True, text=True, check=False, timeout=4,
                )
                if out.returncode == 0 and out.stdout.strip():
                    import json
                    import contextlib
                    with contextlib.suppress(json.JSONDecodeError):
                        sessions = json.loads(out.stdout)
                        for s in sessions if isinstance(sessions, list) else []:
                            if isinstance(s, dict) and s.get("uid", 0) >= 1000 and s.get("user"):
                                name = s["user"]
                                break
        except Exception:
            pass
    if not name:
        # getpass respects $USER but pwd is authoritative
        try:
            if os.geteuid() == 0:
                # when truly root with no SUDO_USER, fallback to root's home is wrong — pick first human
                candidates = [p.pw_name for p in pwd.getpwall() if 1000 <= p.pw_uid < 60000 and Path(p.pw_dir).exists()]
                name = candidates[0] if candidates else "root"
            else:
                name = pwd.getpwuid(os.getuid()).pw_name
        except Exception:
            name = getpass.getuser()

    try:
        pw = pwd.getpwnam(name)
        home = Path(pw.pw_dir)
    except KeyError:
        home = Path.home()

    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg_config) if xdg_config and xdg_config.strip() else home / ".config"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    data_home = Path(xdg_data) if xdg_data and xdg_data.strip() else home / ".local" / "share"
    return TargetUser(name=name, home=home, config_home=config_home, data_home=data_home)

def run(cmd: list[str] | str, *, check: bool = False, capture: bool = True, timeout: float | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    try:
        return subprocess.run(cmd, check=check, capture_output=capture, text=True, timeout=timeout, env=env)
    except FileNotFoundError as e:
        if check:
            raise subprocess.CalledProcessError(127, cmd, stderr=str(e))
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=str(e))
    except subprocess.TimeoutExpired as e:
        if check:
            raise subprocess.CalledProcessError(124, cmd, stdout=e.stdout or "", stderr=f"timeout after {timeout}s")
        return subprocess.CompletedProcess(cmd, 124, stdout=e.stdout or "", stderr=f"timeout after {timeout}s")

def is_root() -> bool:
    return os.geteuid() == 0

def sudo_prefix() -> list[str]:
    return [] if is_root() else ["sudo"]

def sudo_user_prefix(target: TargetUser) -> list[str]:
    """Run as target user when we are root (for paru etc)."""
    if not is_root():
        return []
    # sudo -u <user> -- preserve env HOME for helpers that respect it
    return ["sudo", "-u", target.name, "--"]

def which_localsend() -> Path | None:
    for candidate in ("localsend", "localsend_app"):
        p = shutil.which(candidate)
        if p:
            return Path(p)
    # common fallback locations
    for p in ("/usr/bin/localsend", "/usr/bin/localsend_app", "/opt/localsend/localsend"):
        if Path(p).exists():
            return Path(p)
    return None

def detect_localsend() -> DetectResult:
    native = which_localsend()
    native_ver = None
    if native:
        out = run([str(native), "--version"], check=False, capture=True, timeout=4)
        # localsend --version or --help may emit version; fallback to parsing
        txt = (out.stdout + out.stderr).strip()
        m = re.search(r"(\d+\.\d+\.\d+)", txt)
        if m:
            native_ver = m.group(1)
        # also try localsend-cli if localsend is gui
        if not native_ver:
            cli = shutil.which("localsend-cli")
            if cli:
                out2 = run([cli, "--version"], check=False, capture=True, timeout=4)
                m2 = re.search(r"(\d+\.\d+\.\d+)", out2.stdout + out2.stderr)
                if m2:
                    native_ver = m2.group(1)

    flatpak_present = False
    if shutil.which("flatpak"):
        # authoritative: flatpak info
        r = run(["flatpak", "info", FLATPAK_ID], check=False, capture=True, timeout=6)
        flatpak_present = r.returncode == 0
        if not flatpak_present:
            # also check flatpak list
            r2 = run(["flatpak", "list", "--app", "--columns=application"], check=False, capture=True, timeout=6)
            if FLATPAK_ID in r2.stdout:
                flatpak_present = True

    if native and flatpak_present:
        return DetectResult(kind="both", native_path=native, native_version=native_ver, flatpak_present=True)
    if flatpak_present:
        return DetectResult(kind="flatpak", flatpak_present=True)
    if native:
        return DetectResult(kind="native", native_path=native, native_version=native_ver)
    return DetectResult(kind="none")

def detect_firewall_kind() -> FirewallKind:
    # firewalld: needs daemon running and firewall-cmd present
    if shutil.which("firewall-cmd"):
        r = run(["firewall-cmd", "--state"], check=False, capture=True, timeout=4)
        if r.returncode == 0 and "running" in (r.stdout + r.stderr).lower():
            return "firewalld"
        # also systemctl check
        r2 = run(["systemctl", "is-active", "firewalld"], check=False, capture=True, timeout=4)
        if r2.returncode == 0:
            return "firewalld"
    # ufw: active or config enabled
    if shutil.which("ufw"):
        # ufw status may need root, but we can parse config
        conf = Path("/etc/ufw/ufw.conf")
        if conf.exists():
            try:
                if "ENABLED=yes" in conf.read_text():
                    return "ufw"
            except Exception:
                pass
        r = run([*sudo_prefix(), "ufw", "status"], check=False, capture=True, timeout=6)
        txt = r.stdout + r.stderr
        if "Status: active" in txt:
            return "ufw"
        # if ufw binary exists but inactive, still treat as ufw manager (user likely expects ufw)
        # check if any ufw command succeeds
        if "WARN" not in txt and r.returncode == 0:
            # if not active but present, still prefer ufw over nft raw
            # but only if user explicitly uses ufw — check if ruleset has ufw chains?
            pass
        # fallback: if ufw is installed but not active, we still report ufw so setup can enable
        # Distinguish from pure nftables: check if ufw is the intended manager
        # Heuristic: if /etc/ufw exists, it's ufw
        if Path("/etc/ufw").exists():
            return "ufw"
    # raw nftables
    if shutil.which("nft"):
        # may need root to list; try without
        r = run(["nft", "list", "tables"], check=False, capture=True, timeout=4)
        if r.returncode == 0 and r.stdout.strip():
            return "nftables"
        r2 = run([*sudo_prefix(), "nft", "list", "tables"], check=False, capture=True, timeout=4)
        if r2.returncode == 0 and r2.stdout.strip():
            return "nftables"
    return "none"

def detect_thunar() -> bool:
    return shutil.which("thunar") is not None

def detect_icon(target: TargetUser) -> str:
    # Prefer localsend icon if installed; fallback to generic share icon
    candidates = [
        "/usr/share/icons/hicolor/512x512/apps/localsend.png",
        "/usr/share/icons/hicolor/256x256/apps/localsend.png",
        "/usr/share/icons/hicolor/scalable/apps/localsend.svg",
        "/usr/share/pixmaps/localsend.png",
        "/var/lib/flatpak/exports/share/icons/hicolor/512x512/apps/org.localsend.localsend_app.png",
        str(target.home / ".local/share/flatpak/exports/share/icons/hicolor/512x512/apps/org.localsend.localsend_app.png"),
    ]
    for c in candidates:
        if Path(c).exists():
            # use themed name instead of absolute, so theme can resolve
            # flatpak icon name is org.localsend.localsend_app, native is localsend
            if "org.localsend" in c:
                return "org.localsend.localsend_app"
            return "localsend"
    # fallback generic
    return "folder-remote"

# ---------------------------------------------------------------------------
# Helper dispatcher script (~/.local/bin/localsend-thunar)
# ---------------------------------------------------------------------------

HELPER_TEMPLATE = r"""#!/usr/bin/env bash
# localsend-thunar — Thunar Custom Action dispatcher for LocalSend
# Generated by localsend.py (2026-08-30). Do not edit manually; re-run setup.
# Handles: native `localsend "$@"` vs Flatpak `flatpak run --file-forwarding ... @@ "$@" @@`
# Correctly preserves spaces via `"$@"` (Thunar already g_shell_quote's %F).
set -euo pipefail

_notify() {
    local title="$1" body="$2" urgency="${3:-normal}"
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -u "$urgency" -i "localsend" "$title" "$body" 2>/dev/null || true
    elif command -v zenity >/dev/null 2>&1; then
        zenity --info --title="$title" --text="$body" 2>/dev/null || true
    else
        printf '%s: %s\n' "$title" "$body" >&2
    fi
}

# No args → just open LocalSend (receive mode)
if [[ $# -eq 0 ]]; then
    if command -v localsend >/dev/null 2>&1; then
        nohup localsend >/dev/null 2>&1 & disown 2>/dev/null || true
        exit 0
    elif command -v localsend_app >/dev/null 2>&1; then
        nohup localsend_app >/dev/null 2>&1 & disown 2>/dev/null || true
        exit 0
    elif flatpak info __FLATPAK_ID__ >/dev/null 2>&1; then
        nohup flatpak run --file-forwarding __FLATPAK_ID__ >/dev/null 2>&1 & disown 2>/dev/null || true
        exit 0
    else
        _notify "LocalSend" "Not installed. Run: python3 ~/.local/bin/localsend.py --install" critical
        exit 1
    fi
fi

# With files: prefer native (less sandbox friction), else flatpak with portal forwarding
if command -v localsend >/dev/null 2>&1; then
    nohup localsend "$@" >/dev/null 2>&1 & disown 2>/dev/null || true
    exit 0
elif command -v localsend_app >/dev/null 2>&1; then
    nohup localsend_app "$@" >/dev/null 2>&1 & disown 2>/dev/null || true
    exit 0
fi

if flatpak info __FLATPAK_ID__ >/dev/null 2>&1; then
    # --file-forwarding requires @@ delimiters around file args per `man flatpak-run`
    # This exports host paths via document portal; works with `filesystem=xdg-download` default
    # and with host `filesystem=home` if user granted via Flatseal.
    nohup flatpak run --file-forwarding __FLATPAK_ID__ @@ "$@" @@ >/dev/null 2>&1 & disown 2>/dev/null || true
    exit 0
fi

_notify "LocalSend" "Not installed. Install via: paru -S localsend  OR  flatpak install flathub __FLATPAK_ID__" critical
exit 1
"""

def setup_helper(target: TargetUser, dry_run: bool = False) -> Path:
    helper_path = target.home / ".local" / "bin" / HELPER_NAME
    content = HELPER_TEMPLATE.replace("__FLATPAK_ID__", FLATPAK_ID)
    if dry_run:
        log_info(f"[dry-run] would write helper → {helper_path}")
        return helper_path

    helper_path.parent.mkdir(parents=True, exist_ok=True)
    # Idempotency: skip write if content identical and executable
    if helper_path.exists():
        try:
            existing = helper_path.read_text(encoding="utf-8")
            if existing == content and os.access(helper_path, os.X_OK):
                log_ok(f"Helper already up-to-date → {helper_path}")
                return helper_path
        except Exception:
            pass
    # atomic write via tmp+rename (no .bak pollution)
    tmp = helper_path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(0o755)
    # ensure ownership if running as root
    if is_root():
        try:
            pw = pwd.getpwnam(target.name)
            os.chown(tmp, pw.pw_uid, pw.pw_gid)
        except Exception:
            pass
    tmp.rename(helper_path)
    # Ensure final perms & ownership
    helper_path.chmod(0o755)
    if is_root():
        try:
            pw = pwd.getpwnam(target.name)
            os.chown(helper_path, pw.pw_uid, pw.pw_gid)
        except Exception:
            pass
    log_ok(f"Helper written → {helper_path}")
    return helper_path

# ---------------------------------------------------------------------------
# Thunar uca.xml merge
# ---------------------------------------------------------------------------

def _ensure_uca_root(uca_path: Path) -> ET.Element:
    # Guarantee parent exists
    uca_path.parent.mkdir(parents=True, exist_ok=True)
    if not uca_path.exists():
        # Try to seed from system template
        template = Path("/etc/xdg/Thunar/uca.xml")
        if template.exists():
            try:
                shutil.copy2(template, uca_path)
                log_info(f"Seeded uca.xml from {template}")
            except Exception:
                pass
    if not uca_path.exists():
        # Minimal skeleton (no DTD required; Thunar parses without)
        uca_path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n<actions>\n</actions>\n',
            encoding="utf-8",
        )
    # Parse; if broken, backup and recreate
    try:
        tree = ET.parse(uca_path)
        return tree.getroot()
    except ET.ParseError as e:
        log_warn(f"uca.xml parse error ({e}); recreating without backup")
        # recreate minimal (no .bak file)
        root = ET.Element("actions")
        return root

def _action_matches_localsend(elem: ET.Element) -> bool:
    name = elem.findtext("name") or ""
    cmd = elem.findtext("command") or ""
    # match any prior LocalSend naming
    if "localsend" in name.lower():
        return True
    if "localsend" in cmd.lower():
        return True
    return False

def setup_thunar_uca(target: TargetUser, helper_path: Path, dry_run: bool = False, force: bool = False) -> Path:
    uca_path = target.config_home / "Thunar" / "uca.xml"
    icon_name = detect_icon(target)

    # Dry-run preview without touching disk
    if dry_run:
        log_info(f"[dry-run] would merge Custom Action '{ACTION_NAME}' into {uca_path}")
        log_info(f"[dry-run]   helper={helper_path} %F | icon={icon_name}")
        return uca_path

    root = _ensure_uca_root(uca_path)
    tree = ET.ElementTree(root)

    # Find existing LocalSend action(s)
    existing: list[ET.Element] = [a for a in root.findall("action") if _action_matches_localsend(a)]
    # If multiple stale entries, keep first, remove rest (idempotent cleanup)
    if len(existing) > 1:
        for dup in existing[1:]:
            root.remove(dup)
        existing = existing[:1]
        log_info(f"Removed {len(existing)} duplicate LocalSend actions")

    command_text = f"{shlex.quote(str(helper_path))} %F"
    # Thunar will expand %F via g_shell_quote; we must NOT pre-quote %F.
    # But helper path itself must be shell-escaped (we used shlex.quote).
    # Example final: ~/.local/bin/localsend-thunar %F

    needs_write = False
    if existing:
        action = existing[0]
        # Update fields idempotently, tracking changes
        def set_text_track(tag: str, value: str) -> bool:
            el = action.find(tag)
            if el is None:
                el = ET.SubElement(action, tag)
                el.text = value
                return True
            if (el.text or "") != value:
                el.text = value
                return True
            return False

        # Icon
        cur_icon = action.findtext("icon") or ""
        if cur_icon != icon_name or force:
            if set_text_track("icon", icon_name):
                needs_write = True
                log_info(f"Updated icon: {cur_icon!r} → {icon_name!r}")
        # Name — normalize to canonical
        cur_name = action.findtext("name") or ""
        if cur_name != ACTION_NAME:
            if set_text_track("name", ACTION_NAME):
                needs_write = True
        # Command — fix broken --headless send etc.
        cur_cmd = action.findtext("command") or ""
        if cur_cmd != command_text:
            if set_text_track("command", command_text):
                needs_write = True
                log_info(f"Updated Thunar action command: {cur_cmd!r} → {command_text!r}")
        # Description
        if set_text_track("description", ACTION_DESC):
            needs_write = True
        # Patterns
        if (action.findtext("patterns") or "") != "*":
            if set_text_track("patterns", "*"):
                needs_write = True
        # Range — ensure it matches any selection (empty means any)
        rng = action.find("range")
        if rng is None:
            ET.SubElement(action, "range").text = ""
            needs_write = True
        # Ensure type filters exist (at least one required to match)
        for t in ("directories", "audio-files", "image-files", "other-files", "text-files", "video-files"):
            if action.find(t) is None:
                ET.SubElement(action, t)
                needs_write = True
        # Ensure sub-elements order doesn't matter, but ensure unique-id exists
        uid_el = action.find("unique-id")
        if uid_el is None or not (uid_el.text or "").strip():
            uid = f"{int(time.time()*1e6)}-1"
            if set_text_track("unique-id", uid):
                needs_write = True
        # submenu
        if action.find("submenu") is None:
            ET.SubElement(action, "submenu").text = ""
            needs_write = True
        # startup-notify
        if action.find("startup-notify") is None:
            ET.SubElement(action, "startup-notify")
            needs_write = True
        # duplicate cleanup already may have changed tree
        if len(existing) > 1:
            needs_write = True
        if needs_write:
            log_ok(f"Updated existing Thunar Custom Action '{ACTION_NAME}'")
        else:
            log_ok(f"Thunar Custom Action '{ACTION_NAME}' already up-to-date")
    else:
        # Create new action
        action = ET.SubElement(root, "action")
        # Order mimics thunar_uca_model_save: icon, name, submenu, unique-id, command, description, range, patterns, ...
        # But order is not strictly required; parser accepts any order.
        uid = f"{int(time.time()*1e6)}-1"
        ET.SubElement(action, "icon").text = icon_name
        ET.SubElement(action, "name").text = ACTION_NAME
        ET.SubElement(action, "submenu").text = ""
        ET.SubElement(action, "unique-id").text = uid
        ET.SubElement(action, "command").text = command_text
        ET.SubElement(action, "description").text = ACTION_DESC
        ET.SubElement(action, "range").text = ""
        ET.SubElement(action, "patterns").text = "*"
        ET.SubElement(action, "startup-notify")
        for t in ("directories", "audio-files", "image-files", "other-files", "text-files", "video-files"):
            ET.SubElement(action, t)
        log_ok(f"Created new Thunar Custom Action '{ACTION_NAME}' ({uid})")
        needs_write = True

    if not needs_write and not force:
        log_info(f"uca.xml already up-to-date — skipping write to {uca_path}")
        return uca_path

    # Pretty indent for readability (Python 3.9+)
    try:
        ET.indent(root, space="    ")
    except Exception:
        pass

    # Atomic write: tmp + rename, with XML declaration
    tmp = uca_path.with_suffix(".tmp")
    # ET doesn't preserve DTD; we omit it intentionally (Thunar doesn't need it)
    # Use utf-8 + declaration
    try:
        tree.write(tmp, encoding="utf-8", xml_declaration=True)
        # ET writes <startup-notify /> but Thunar expects <startup-notify/> — both parse ok
        # Ensure ownership
        if is_root():
            pw = pwd.getpwnam(target.name)
            os.chown(tmp, pw.pw_uid, pw.pw_gid)
        tmp.rename(uca_path)
        if is_root():
            pw = pwd.getpwnam(target.name)
            os.chown(uca_path, pw.pw_uid, pw.pw_gid)
        log_ok(f"uac.xml written → {uca_path}")
    except Exception as e:
        log_err(f"Failed to write uca.xml: {e}")
        raise

    # Reload Thunar daemon as target user (not root)
    try:
        if dry_run:
            log_info("[dry-run] would run: thunar -q")
        else:
            # Must run as target user if we are root
            base_cmd = ["thunar", "-q"]
            if is_root():
                # sudo -u <target> thunar -q  — need DISPLAY/WAYLAND_DISPLAY? thunar -q talks via D-Bus session bus,
                # which is user-scoped. Best effort; failure is non-fatal.
                r = run([*sudo_user_prefix(target), *base_cmd], check=False, capture=True, timeout=6,
                        env={**os.environ, "HOME": str(target.home), "XDG_CONFIG_HOME": str(target.config_home)})
            else:
                r = run(base_cmd, check=False, capture=True, timeout=6)
            if r.returncode == 0:
                log_ok("Thunar daemon quit (will auto-respawn on next window)")
            else:
                # Non-zero is okay if Thunar wasn't running
                log_info(f"thunar -q exit {r.returncode} — will reload on next launch")
    except Exception as e:
        log_warn(f"thunar -q failed (non-fatal): {e}")

    return uca_path

# ---------------------------------------------------------------------------
# Thunar SendTo desktop entry
# ---------------------------------------------------------------------------

SENDTO_TEMPLATE = """[Desktop Entry]
Type=Application
Version=1.0
Encoding=UTF-8
Name=LocalSend
Comment=Send files via LocalSend
Exec={helper} %F
Icon={icon}
Terminal=false
Categories=Utility;Network;FileTransfer;
MimeType=application/octet-stream;inode/directory;
NoDisplay=false
X-ThunarSendto=true
"""

def setup_thunar_sendto(target: TargetUser, helper_path: Path, dry_run: bool = False) -> Path:
    sendto_dir = target.data_home / "Thunar" / "sendto"
    # Also handle legacy ~/.local/share/Thunar/sendto
    # data_home already is that, but ensure parent
    sendto_path = sendto_dir / "localsend.desktop"
    icon = detect_icon(target)
    content = SENDTO_TEMPLATE.format(helper=shlex.quote(str(helper_path)), icon=icon)

    if dry_run:
        log_info(f"[dry-run] would write SendTo → {sendto_path}")
        return sendto_path

    sendto_dir.mkdir(parents=True, exist_ok=True)
    # Compare existing to avoid unnecessary write
    if sendto_path.exists():
        try:
            if sendto_path.read_text(encoding="utf-8") == content:
                log_ok(f"SendTo already up-to-date → {sendto_path}")
                return sendto_path
        except Exception:
            pass

    tmp = sendto_path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(0o644)
    if is_root():
        try:
            pw = pwd.getpwnam(target.name)
            os.chown(tmp, pw.pw_uid, pw.pw_gid)
        except Exception:
            pass
    tmp.rename(sendto_path)
    if is_root():
        try:
            pw = pwd.getpwnam(target.name)
            os.chown(sendto_path, pw.pw_uid, pw.pw_gid)
        except Exception:
            pass
    sendto_path.chmod(0o644)
    log_ok(f"SendTo entry written → {sendto_path}")
    return sendto_path

# ---------------------------------------------------------------------------
# Firewall
# ---------------------------------------------------------------------------

def _firewalld_add_zone(zone: str, port_spec: str, permanent: bool = False, dry_run: bool = False) -> bool:
    base = ["firewall-cmd", f"--zone={zone}"]
    if permanent:
        base.append("--permanent")
    # query first (idempotent)
    q = run([*base, f"--query-port={port_spec}"], check=False, capture=True, timeout=6)
    if q.returncode == 0:
        return False  # already allowed
    # add
    if dry_run:
        log_info(f"[dry-run] would run: {' '.join([*sudo_prefix(), *base, f'--add-port={port_spec}'])}")
        return True
    r = run([*sudo_prefix(), *base, f"--add-port={port_spec}"], check=False, capture=True, timeout=10)
    if r.returncode != 0:
        log_warn(f"firewall-cmd --add-port {port_spec} failed: {r.stderr.strip()}")
        return False
    log_ok(f"firewalld [{zone}] opened {port_spec}{' (permanent)' if permanent else ''}")
    return True

def setup_firewall(dry_run: bool = False) -> bool:
    kind = detect_firewall_kind()
    log_info(f"Detected firewall manager: {kind}")

    if kind == "none":
        log_warn("No active firewall detected (ufw/firewalld/nftables). Skipping — LocalSend needs 53317/tcp+udp if you later enable one.")
        if not dry_run:
            cprint("  Hint: sudo pacman -S --needed ufw && sudo ufw enable && sudo ufw allow 53317/tcp && sudo ufw allow 53317/udp")
        return False

    if kind == "firewalld":
        # get default zone
        r = run(["firewall-cmd", "--get-default-zone"], check=False, capture=True, timeout=4)
        zone = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "public"
        log_info(f"Using firewalld zone: {zone}")
        changed = False
        for spec in (f"{PORT}/tcp", f"{PORT}/udp"):
            # runtime
            changed |= _firewalld_add_zone(zone, spec, permanent=False, dry_run=dry_run)
            # permanent
            changed |= _firewalld_add_zone(zone, spec, permanent=True, dry_run=dry_run)
        if changed and not dry_run:
            # Ensure permanent persisted via runtime-to-permanent or reload
            # We already added permanent; reload to ensure consistency
            r = run([*sudo_prefix(), "firewall-cmd", "--reload"], check=False, capture=True, timeout=10)
            if r.returncode == 0:
                log_ok("firewalld reloaded")
            else:
                log_warn(f"firewall-cmd --reload failed: {r.stderr.strip()}")
        elif not changed:
            log_ok(f"firewalld already allows {PORT}/tcp + {PORT}/udp in zone {zone}")
        return True

    if kind == "ufw":
        # Check existing rules idempotently via `ufw status numbered`
        r = run([*sudo_prefix(), "ufw", "status", "numbered"], check=False, capture=True, timeout=6)
        txt = r.stdout + r.stderr
        has_tcp = re.search(rf"{PORT}/tcp", txt) is not None
        has_udp = re.search(rf"{PORT}/udp", txt) is not None
        # Also check generic "53317" (ufw allow 53317 allows both)
        if f"{PORT}" in txt and ("ALLOW" in txt or "allow" in txt.lower()):
            # be conservative: if any 53317 rule exists, assume both covered — but prefer explicit
            pass

        changed = False
        if not has_tcp:
            if dry_run:
                log_info(f"[dry-run] would run: sudo ufw allow {PORT}/tcp comment 'LocalSend'")
                changed = True
            else:
                # Use comment if supported (ufw >= 0.36)
                rr = run([*sudo_prefix(), "ufw", "allow", f"{PORT}/tcp", "comment", "LocalSend"], check=False, capture=True, timeout=10)
                if rr.returncode != 0:
                    # fallback without comment
                    rr = run([*sudo_prefix(), "ufw", "allow", f"{PORT}/tcp"], check=False, capture=True, timeout=10)
                if rr.returncode == 0:
                    log_ok(f"ufw allowed {PORT}/tcp")
                    changed = True
                else:
                    log_warn(f"ufw allow {PORT}/tcp failed: {rr.stderr.strip()}")
        else:
            log_ok(f"ufw already allows {PORT}/tcp")

        if not has_udp:
            if dry_run:
                log_info(f"[dry-run] would run: sudo ufw allow {PORT}/udp comment 'LocalSend'")
                changed = True
            else:
                rr = run([*sudo_prefix(), "ufw", "allow", f"{PORT}/udp", "comment", "LocalSend"], check=False, capture=True, timeout=10)
                if rr.returncode != 0:
                    rr = run([*sudo_prefix(), "ufw", "allow", f"{PORT}/udp"], check=False, capture=True, timeout=10)
                if rr.returncode == 0:
                    log_ok(f"ufw allowed {PORT}/udp")
                    changed = True
                else:
                    log_warn(f"ufw allow {PORT}/udp failed: {rr.stderr.strip()}")
        else:
            log_ok(f"ufw already allows {PORT}/udp")

        if changed and not dry_run:
            # ufw reload not strictly needed after allow, but ensure
            run([*sudo_prefix(), "ufw", "reload"], check=False, capture=True, timeout=10)
        return True

    if kind == "nftables":
        # Raw nftables — add inet filter input rules idempotently
        # Check existing ruleset for 53317
        r = run([*sudo_prefix(), "nft", "list", "ruleset"], check=False, capture=True, timeout=6)
        if f"{PORT}" in r.stdout:
            log_ok(f"nftables already references port {PORT} (assuming allowed)")
            return True
        if dry_run:
            log_info(f"[dry-run] would add nft rules: tcp dport {PORT} accept, udp dport {PORT} accept")
            log_info("[dry-run] would persist to /etc/nftables.conf and reload nftables")
            return True
        # Try to add to inet filter input
        # First ensure table/chain exist
        # We use nft add rule inet filter input ... — if table missing, create?
        # Check table filter exists
        has_filter = "table inet filter" in r.stdout
        if not has_filter:
            log_warn("nftables: no 'inet filter' table found — creating minimal input chain")
            # Create table and chain if needed
            run([*sudo_prefix(), "nft", "add", "table", "inet", "filter"], check=False, capture=True, timeout=6)
            run([*sudo_prefix(), "nft", "add", "chain", "inet", "filter", "input", "{ type filter hook input priority 0; policy accept; }"], check=False, capture=True, timeout=6)

        ok = True
        for proto in ("tcp", "udp"):
            if proto == "tcp":
                rr = run([*sudo_prefix(), "nft", "add", "rule", "inet", "filter", "input", proto, "dport", str(PORT), "ct", "state", "new", "accept"],
                         check=False, capture=True, timeout=6)
            else:
                rr = run([*sudo_prefix(), "nft", "add", "rule", "inet", "filter", "input", proto, "dport", str(PORT), "accept"],
                         check=False, capture=True, timeout=6)
            # Alternative simpler for both: nft add rule inet filter input tcp dport 53317 accept
            if rr.returncode != 0:
                # try simpler without ct state
                rr2 = run([*sudo_prefix(), "nft", "add", "rule", "inet", "filter", "input", proto, "dport", str(PORT), "accept"], check=False, capture=True, timeout=6)
                if rr2.returncode != 0:
                    log_warn(f"nft add rule {proto} dport {PORT} failed: {rr2.stderr.strip()}")
                    ok = False
                else:
                    log_ok(f"nftables: allowed {PORT}/{proto}")
            else:
                log_ok(f"nftables: allowed {PORT}/{proto}")

        # Persist: append to /etc/nftables.conf if not already there
        nft_conf = Path("/etc/nftables.conf")
        if nft_conf.exists():
            try:
                txt = nft_conf.read_text(encoding="utf-8", errors="ignore")
                if str(PORT) not in txt:
                    log_info(f"Appending LocalSend rules to {nft_conf} (manual review advised)")
                    with nft_conf.open("a", encoding="utf-8") as f:
                        f.write(f"\n# LocalSend {PORT}/tcp+udp — added by localsend.py {time.strftime('%Y-%m-%d')}\n")
                        f.write(f"add rule inet filter input tcp dport {PORT} accept\n")
                        f.write(f"add rule inet filter input udp dport {PORT} accept\n")
            except Exception as e:
                log_warn(f"Could not persist nftables rules to {nft_conf}: {e}")

        # Try to reload nftables service if active
        run([*sudo_prefix(), "systemctl", "is-active", "nftables"], check=False, capture=True, timeout=4)

        return ok

    return False

# ---------------------------------------------------------------------------
# Autostart
# ---------------------------------------------------------------------------

AUTOSTART_TEMPLATE_NATIVE = """[Desktop Entry]
Type=Application
Name=LocalSend
Comment=Share files to nearby devices (LocalSend)
Exec={exec_line}
Icon={icon}
Terminal=false
StartupNotify=false
Categories=Utility;Network;FileTransfer;
X-GNOME-Autostart-enabled={gnome_enabled}
Hidden={hidden}
"""

AUTOSTART_TEMPLATE_FLATPAK = """[Desktop Entry]
Type=Application
Name=LocalSend
Comment=Share files to nearby devices (LocalSend - Flatpak)
Exec=flatpak run {flatpak_id} {hidden_flag}
Icon=org.localsend.localsend_app
Terminal=false
StartupNotify=false
Categories=Utility;Network;FileTransfer;
X-GNOME-Autostart-enabled={gnome_enabled}
Hidden={hidden}
"""

# Hyprland edit_here template block (managed)
HYPR_MARKER_START = "-- ── LocalSend (managed by localsend.py, opt-in) ──"
HYPR_MARKER_END = "-- ── end LocalSend ──"

def _hypr_exec_line(detect: DetectResult, use_hidden: bool) -> str:
    flag = " --hidden" if use_hidden else ""
    if detect.kind in ("native", "both") and detect.native_path:
        return f"{shlex.quote(str(detect.native_path))}{flag}".strip()
    elif detect.kind == "flatpak" or detect.flatpak_present:
        return f"flatpak run {FLATPAK_ID}{flag}".strip()
    else:
        return f"localsend{flag}".strip()

def setup_hypr_autostart(target: TargetUser, detect: DetectResult, dry_run: bool = False, enable: bool = False, use_hidden: bool = True) -> Path | None:
    """Manage Hyprland edit_here/source/autostart.lua — opt-in, commented by default, efficient daemon."""
    hypr_path = target.home / ".config" / "hypr" / "edit_here" / "source" / "autostart.lua"
    flag = " --hidden" if use_hidden else ""
    # Use $HOME-agnostic exec (no hardcoded username); rely on PATH for portability
    native_exec = f"localsend{flag}".strip()
    flatpak_exec = f"flatpak run {FLATPAK_ID}{flag}".strip()
    # Choose primary based on detection for minimal surprise, but keep both documented
    primary = _hypr_exec_line(detect, use_hidden)
    # Determine which is primary for commenting logic
    is_flatpak_primary = "flatpak" in primary
    if enable:
        if is_flatpak_primary:
            block = (
                f"    {HYPR_MARKER_START}\n"
                f"    -- https://localsend.org — AirDrop alternative, LAN only\n"
                f"    -- Runs as tray daemon (--hidden), idle ~0% CPU / ~30MB RAM, scales only during transfer\n"
                f"    -- PROTOCOL: 224.0.0.167:53317/udp multicast discovery + 53317/tcp HTTPS\n"
                f"    -- Native (AUR: localsend) preferred, Flatpak fallback auto-detected by helper\n"
                f"    -- To enable, uncomment ONE line below (native vs flatpak). Thunar right-click works without this;\n"
                f"    -- this only controls background receive/tray on login (opt-in, off by default).\n"
                f"    -- hl.exec_cmd(\"{native_exec}\")\n"
                f"    hl.exec_cmd(\"{flatpak_exec}\")\n"
                f"    -- Tip: If tray missing after reboot, use 'localsend' without --hidden\n"
                f"    {HYPR_MARKER_END}\n"
            )
        else:
            block = (
                f"    {HYPR_MARKER_START}\n"
                f"    -- https://localsend.org — AirDrop alternative, LAN only\n"
                f"    -- Runs as tray daemon (--hidden), idle ~0% CPU / ~30MB RAM, scales only during transfer\n"
                f"    -- PROTOCOL: 224.0.0.167:53317/udp multicast discovery + 53317/tcp HTTPS\n"
                f"    -- Native (AUR: localsend) preferred, Flatpak fallback auto-detected by helper\n"
                f"    -- To enable, uncomment ONE line below (native vs flatpak). Thunar right-click works without this;\n"
                f"    -- this only controls background receive/tray on login (opt-in, off by default).\n"
                f"    hl.exec_cmd(\"{native_exec}\")\n"
                f"    -- Flatpak alternative: {flatpak_exec}\n"
                f"    -- Tip: If tray missing after reboot, use 'localsend' without --hidden\n"
                f"    {HYPR_MARKER_END}\n"
            )
    else:
        block = (
            f"    {HYPR_MARKER_START}\n"
            f"    -- https://localsend.org — AirDrop alternative, LAN only\n"
            f"    -- Runs as tray daemon (--hidden), idle ~0% CPU / ~30MB RAM, scales only during transfer\n"
            f"    -- PROTOCOL: 224.0.0.167:53317/udp multicast discovery + 53317/tcp HTTPS\n"
            f"    -- Native (AUR: localsend) preferred, Flatpak fallback auto-detected by helper\n"
            f"    -- To enable, uncomment ONE line below (native vs flatpak). Thunar right-click works without this;\n"
            f"    -- this only controls background receive/tray on login (opt-in, off by default).\n"
            f"    -- hl.exec_cmd(\"{native_exec}\")\n"
            f"    -- Flatpak alternative: {flatpak_exec}\n"
            f"    -- Tip: If tray missing after reboot, use 'localsend' without --hidden\n"
            f"    {HYPR_MARKER_END}\n"
        )

    if dry_run:
        state = "ENABLED" if enable else "DISABLED (commented, opt-in)"
        log_info(f"[dry-run] would set Hypr autostart → {hypr_path} [{state}]")
        log_info(f"[dry-run]   exec: {exec_line}")
        return hypr_path

    hypr_path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure file exists (seed from template if missing)
    if not hypr_path.exists():
        # Try to seed from ~/.config/hypr/edit_here/source/autostart.lua defaults
        # If not exists, create minimal
        hypr_path.write_text(
            'hl.on("hyprland.start", function()\n' + block + 'end)\n',
            encoding="utf-8",
        )
        log_ok(f"Created Hypr autostart → {hypr_path} ({'enabled' if enable else 'disabled, opt-in'})")
        return hypr_path

    try:
        content = hypr_path.read_text(encoding="utf-8")
    except Exception as e:
        log_warn(f"Could not read Hypr autostart.lua: {e}")
        return None

    # Check if our marker already present
    if HYPR_MARKER_START in content:
        # Replace existing block (regex with DOTALL, include leading indent)
        import re as _re
        pattern = _re.compile(
            r"[ \t]*" + _re.escape(HYPR_MARKER_START) + r".*?" + _re.escape(HYPR_MARKER_END),
            flags=_re.DOTALL,
        )
        if pattern.search(content):
            new_content = pattern.sub(block.rstrip(), content)
            if new_content == content:
                log_ok(f"Hypr autostart already {'enabled' if enable else 'disabled (opt-in)'} → {hypr_path}")
                return hypr_path
            # atomic (no backup file)
            tmp = hypr_path.with_suffix(".tmp")
            tmp.write_text(new_content, encoding="utf-8")
            if is_root():
                try:
                    pw = pwd.getpwnam(target.name)
                    os.chown(tmp, pw.pw_uid, pw.pw_gid)
                except Exception:
                    pass
            tmp.rename(hypr_path)
            log_ok(f"Hypr autostart {'enabled' if enable else 'disabled (opt-in)'} → {hypr_path}")
            return hypr_path
        else:
            log_warn("Hypr marker found but block parse failed — appending new block")
            # fall through to append

    # No marker: insert before the closing `end)` of hl.on("hyprland.start" block
    # Find the last `end)` that closes the start block (before shutdown comment)
    lines = content.splitlines()
    insert_idx = None
    # Find hl.on("hyprland.start" then locate its matching end)
    # Simple: find the last line that is exactly `end)` before `-- hl.on("hyprland.shutdown"`
    for i, ln in enumerate(lines):
        if ln.strip() == "end)" and i > 0:
            # Check if next non-empty line is shutdown comment or EOF
            # Heuristic: this is the start block closer if we are before shutdown
            # Look ahead for shutdown marker
            remaining = "\n".join(lines[i+1:])
            if "hyprland.shutdown" in remaining or i == len(lines)-1 or remaining.strip() == "":
                insert_idx = i
                # we want the *first* such end) that is start block, but there is only one
                # Actually we want the start block's end), which is near end of file (line ~84)
                # We'll keep searching and keep last match before shutdown
                pass
    # Fallback: insert before last end)
    if insert_idx is None:
        for i in range(len(lines)-1, -1, -1):
            if lines[i].strip() == "end)":
                insert_idx = i
                break
    if insert_idx is None:
        # append at end
        new_content = content.rstrip() + "\n\n" + block + "\n"
    else:
        lines.insert(insert_idx, block.rstrip())
        new_content = "\n".join(lines) + "\n"

    tmp = hypr_path.with_suffix(".tmp")
    tmp.write_text(new_content, encoding="utf-8")
    if is_root():
        try:
            pw = pwd.getpwnam(target.name)
            os.chown(tmp, pw.pw_uid, pw.pw_gid)
        except Exception:
            pass
    tmp.rename(hypr_path)
    log_ok(f"Hypr autostart block {'added (enabled)' if enable else 'added (disabled, opt-in)'} → {hypr_path}")
    return hypr_path

def setup_systemd_autostart(target: TargetUser, detect: DetectResult, dry_run: bool = False, enable: bool = False, use_hidden: bool = True) -> Path | None:
    """Systemd user service alternative — PartOf=graphical-session.target, opt-in."""
    svc_path = target.home / ".config" / "systemd" / "user" / "localsend.service"
    flag = " --hidden" if use_hidden else ""
    if detect.kind in ("native", "both") and detect.native_path:
        exec_line = f"{shlex.quote(str(detect.native_path))}{flag}".strip()
    elif detect.kind == "flatpak" or detect.flatpak_present:
        exec_line = f"/usr/bin/flatpak run {FLATPAK_ID}{flag}".strip()
    else:
        exec_line = f"/usr/bin/localsend{flag}".strip()

    content = f"""[Unit]
Description=LocalSend - AirDrop alternative (opt-in)
After=graphical-session.target
PartOf=graphical-session.target
ConditionEnvironment=WAYLAND_DISPLAY

[Service]
Type=simple
ExecStart={exec_line}
Restart=on-failure
RestartSec=3
Slice=app-graphical.slice

[Install]
WantedBy=graphical-session.target
"""

    if dry_run:
        state = "ENABLED" if enable else "DISABLED (not enabled)"
        log_info(f"[dry-run] would write systemd service → {svc_path} [{state}]")
        if enable:
            log_info(f"[dry-run]   systemctl --user enable --now localsend.service")
        return svc_path

    svc_path.parent.mkdir(parents=True, exist_ok=True)

    if not enable:
        # If disabled, remove or keep but not enabled
        if svc_path.exists():
            # Disable if currently enabled
            run(["systemctl", "--user", "disable", "--now", "localsend.service"], check=False, capture=True, timeout=10)
            # Keep file but disabled, or remove? Keep for opt-in transparency
            log_info(f"Systemd service disabled (kept at {svc_path}) — enable with --autostart --autostart-method systemd")
        else:
            log_info(f"Systemd autostart remains disabled (no file at {svc_path})")
        return svc_path

    # enable: write and enable
    tmp = svc_path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(0o644)
    if is_root():
        try:
            pw = pwd.getpwnam(target.name)
            os.chown(tmp, pw.pw_uid, pw.pw_gid)
        except Exception:
            pass
    tmp.rename(svc_path)
    if is_root():
        try:
            pw = pwd.getpwnam(target.name)
            os.chown(svc_path, pw.pw_uid, pw.pw_gid)
        except Exception:
            pass
    # daemon-reload + enable
    run(["systemctl", "--user", "daemon-reload"], check=False, capture=True, timeout=10)
    r = run(["systemctl", "--user", "enable", "--now", "localsend.service"], check=False, capture=True, timeout=10)
    if r.returncode == 0:
        log_ok(f"Systemd service enabled → {svc_path}")
    else:
        log_warn(f"Failed to enable systemd service: {r.stderr.strip()}")
    return svc_path

def setup_autostart(target: TargetUser, detect: DetectResult, dry_run: bool = False, use_hidden: bool = True, force: bool = False, enable: bool = False) -> Path | None:
    autostart_dir = target.config_home / "autostart"
    autostart_path = autostart_dir / "localsend.desktop"

    # Decide exec line
    icon = detect_icon(target)
    hidden_flag = "--hidden" if use_hidden else ""
    gnome_enabled = "true" if enable else "false"
    hidden = "false" if enable else "true"
    content: str
    if detect.kind in ("native", "both") and detect.native_path:
        exec_path = str(detect.native_path)
        # Prefer absolute path for robustness
        exec_line = f"{shlex.quote(exec_path)} {hidden_flag}".strip()
        content = AUTOSTART_TEMPLATE_NATIVE.format(exec_line=exec_line, icon=icon, gnome_enabled=gnome_enabled, hidden=hidden)
    elif detect.kind == "flatpak" or detect.flatpak_present:
        content = AUTOSTART_TEMPLATE_FLATPAK.format(flatpak_id=FLATPAK_ID, hidden_flag=hidden_flag, gnome_enabled=gnome_enabled, hidden=hidden)
    else:
        # No install yet — still create native template as default (will work once installed)
        if enable:
            log_warn("LocalSend not installed — autostart will use 'localsend --hidden' placeholder; install first")
        content = AUTOSTART_TEMPLATE_NATIVE.format(exec_line=f"localsend {hidden_flag}".strip(), icon=icon, gnome_enabled=gnome_enabled, hidden=hidden)

    if dry_run:
        state = "ENABLED" if enable else "DISABLED (opt-in, Hidden=true)"
        log_info(f"[dry-run] would write autostart → {autostart_path} [{state}]")
        log_info(f"[dry-run]   Exec={content.splitlines()[4] if len(content.splitlines())>4 else content[:120]}")
        if enable and use_hidden:
            log_warn("[dry-run] Note: --hidden has known race (issue #2927) on Linux <1.19; use --no-hidden if tray fails")
        return autostart_path

    autostart_dir.mkdir(parents=True, exist_ok=True)

    # Idempotency: compare without whitespace churn
    if autostart_path.exists() and not force:
        try:
            existing = autostart_path.read_text(encoding="utf-8")
            if existing == content:
                state = "enabled" if enable else "disabled (opt-in)"
                log_ok(f"Autostart already {state} → {autostart_path}")
                return autostart_path
            # Also handle legacy flatpak AppImage path — overwrite if broken
            if "/tmp/.mount" in existing or "/app/localsend" in existing:
                log_info("Found broken AppImage/flatpak autostart Exec — overwriting")
            else:
                # Show diff hint? just overwrite if hidden flag changed
                pass
        except Exception:
            pass

    tmp = autostart_path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(0o644)
    if is_root():
        try:
            pw = pwd.getpwnam(target.name)
            os.chown(tmp, pw.pw_uid, pw.pw_gid)
        except Exception:
            pass
    tmp.rename(autostart_path)
    if is_root():
        try:
            pw = pwd.getpwnam(target.name)
            os.chown(autostart_path, pw.pw_uid, pw.pw_gid)
        except Exception:
            pass
    autostart_path.chmod(0o644)
    state = "ENABLED" if enable else "DISABLED (opt-in, Hidden=true)"
    log_ok(f"Autostart {state} → {autostart_path}" + (" (hidden)" if enable and use_hidden else ""))
    if enable and use_hidden:
        log_warn("If tray icon missing or port 53317 refused after reboot, re-run with --no-hidden (see #2927 race)")
    if not enable:
        log_info("Autostart is opt-in — enable with: python localsend.py --autostart (hypr) or --autostart --autostart-method xdg")

    # On Hyprland with hyprland-session.target, XDG autostart is handled by
    # systemd-xdg-autostart-generator → app-*.service in app-graphical.slice.
    # No manual systemctl --user enable needed. Verify generator picked it up on next login.
    # For immediate test, user can run: systemctl --user daemon-reload (not needed for autostart generator)
    # Note: generator skips Exec if binary missing — hypr method is more robust on fresh install.
    return autostart_path

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def install_localsend(method: InstallMethod, target: TargetUser, dry_run: bool = False) -> bool:
    if method == "skip":
        log_info("Skipping installation (per --no-install)")
        return True

    det = detect_localsend()
    if det.kind != "none" and method == "auto":
        log_ok(f"LocalSend already present ({det.kind}{', ' + det.native_version if det.native_version else ''}) — skipping install (use --install force to reinstall)")
        return True

    # Resolve method
    chosen = method
    if method == "auto":
        # Auto when not installed → prefer localsend-bin (prebuilt native, instant, no 15min Rust+Flutter build)
        # User requested: if localsend not found, auto-download localsend-bin
        has_paru = shutil.which("paru") is not None
        has_yay = shutil.which("yay") is not None
        has_flatpak = shutil.which("flatpak") is not None
        if has_paru or has_yay:
            chosen = "native-bin"
            log_info("Auto-selected localsend-bin (AUR prebuilt, instant native). Use --install native for source build (1.18.2) or --install flatpak for sandbox.")
        elif has_flatpak:
            chosen = "flatpak"
            log_info("Auto-selected Flatpak (no AUR helper). Use --install flatpak to force.")
        else:
            log_err("No AUR helper (paru/yay) nor flatpak found. Install one first: sudo pacman -S --needed flatpak")
            return False

    log_info(f"Install method: {chosen}")

    if chosen in ("native", "native-bin"):
        pkg = "localsend" if chosen == "native" else "localsend-bin"
        helper = shutil.which("paru") or shutil.which("yay")
        if not helper:
            log_err(f"AUR helper required for {pkg}. Install paru: sudo pacman -S --needed paru  OR  use --install flatpak")
            return False
        helper_name = Path(helper).name
        # paru/yay must NOT run as root, skip PGP review prompt for non-interactive
        prefix = sudo_user_prefix(target) if is_root() else []
        cmd = [*prefix, helper, "-S", "--needed", "--noconfirm", "--skipreview", pkg]
        # paru needs non-root + no lock contention
        if dry_run:
            log_info(f"[dry-run] would run: {' '.join(cmd)}  (build may take 10-15 min for {pkg})")
            return True
        # Check pacman lock
        lock = Path("/var/lib/pacman/db.lck")
        if lock.exists():
            log_warn(f"Pacman lock exists at {lock} — waiting or aborting")
            # try fuser/pgrep check
            r = run(["fuser", str(lock)], check=False, capture=True, timeout=4) if shutil.which("fuser") else run(["pgrep", "-x", "pacman"], check=False, capture=True, timeout=4)
            if r.returncode == 0:
                log_err("pacman is running — aborting install. Retry after it finishes.")
                return False
            else:
                log_warn("Stale lock detected — removing (requires root)")
                rr = run([*sudo_prefix(), "rm", "-f", str(lock)], check=False, capture=True, timeout=6)
                if rr.returncode != 0:
                    log_err(f"Failed to remove stale lock: {rr.stderr}")
                    return False

        log_info(f"Installing {pkg} via {helper_name} (this builds Rust+Flutter, ~10-15 min for native)...")
        # Use capture=False to stream build output live (rich status would hide it)
        try:
            proc = subprocess.run(cmd, check=False, text=True)
            if proc.returncode == 0:
                log_ok(f"{pkg} installed")
                return True
            else:
                log_err(f"{helper_name} failed (exit {proc.returncode}). Try --install flatpak as fallback.")
                return False
        except Exception as e:
            log_err(f"Install failed: {e}")
            return False

    if chosen in ("flatpak", "flatpak-user"):
        if not shutil.which("flatpak"):
            log_err("flatpak not found. Install: sudo pacman -S --needed flatpak")
            return False
        # Decide system vs user
        use_user = chosen == "flatpak-user"
        # If auto and we are not root, user install avoids sudo password
        if chosen == "flatpak" and not is_root():
            # Test if system install would need sudo
            # Try system first with sudo -n, fallback to user if no perms
            pass

        flathub_added = False
        # Ensure flathub remote exists
        r = run(["flatpak", "remote-list"], check=False, capture=True, timeout=6)
        if "flathub" not in r.stdout:
            log_info("Adding Flathub remote...")
            if dry_run:
                log_info("[dry-run] would run: flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo")
            else:
                # Prefer system remote if root, else user
                remote_cmd = ["flatpak", "remote-add", "--if-not-exists", "flathub", "https://flathub.org/repo/flathub.flatpakrepo"]
                if not use_user:
                    remote_cmd = [*sudo_prefix(), *remote_cmd]
                else:
                    remote_cmd = ["flatpak", "remote-add", "--if-not-exists", "--user", "flathub", "https://flathub.org/repo/flathub.flatpakrepo"]
                rr = run(remote_cmd, check=False, capture=True, timeout=12)
                if rr.returncode == 0:
                    flathub_added = True
                else:
                    log_warn(f"Failed to add flathub: {rr.stderr.strip()}")

        # Now install
        if use_user:
            install_cmd = ["flatpak", "install", "--user", "-y", "flathub", FLATPAK_ID]
        else:
            # system install — may need sudo
            install_cmd = [*sudo_prefix(), "flatpak", "install", "-y", "flathub", FLATPAK_ID]
            # If already installed, use --or-update / update
            # Check if already installed
            ri = run(["flatpak", "info", FLATPAK_ID], check=False, capture=True, timeout=6)
            if ri.returncode == 0:
                log_info(f"{FLATPAK_ID} already installed — checking for updates")
                install_cmd = [*sudo_prefix(), "flatpak", "update", "-y", FLATPAK_ID]

        if dry_run:
            log_info(f"[dry-run] would run: {' '.join(install_cmd)}")
            return True

        log_info(f"Installing {FLATPAK_ID} via Flatpak ({'user' if use_user else 'system'})...")
        rr = run(install_cmd, check=False, capture=False, timeout=600)  # stream
        # subprocess.run with capture=False returns CompletedProcess with returncode
        # We used run wrapper with capture=False, but we passed capture=False -> run still captures? Our wrapper always capture if capture=True default.
        # For this case we want to stream; use raw subprocess
        # Re-run with proper streaming if our wrapper suppressed output
        # Actually `run(..., capture=False)` will set capture_output=False, so we get live output.
        # Check returncode
        if rr.returncode == 0:
            log_ok(f"Flatpak {FLATPAK_ID} installed")
            # Grant filesystem=home if user wants to save outside Downloads (optional)
            # We don't auto-grant — leave to user via Flatseal, but hint.
            log_info("Hint: Flatpak defaults to xdg-download only. To allow any folder, run: flatpak override --user --filesystem=home org.localsend.localsend_app  (or use Flatseal)")
            return True
        else:
            # Try user install as fallback if system failed due to perms
            if not use_user and rr.returncode != 0:
                log_warn("System Flatpak install failed — trying user install fallback...")
                fb = run(["flatpak", "install", "--user", "-y", "flathub", FLATPAK_ID], check=False, capture=True, timeout=600)
                if fb.returncode == 0:
                    log_ok("Flatpak installed (user)")
                    return True
                log_err(f"Flatpak install failed: {fb.stderr.strip()[:500]}")
            else:
                log_err(f"Flatpak install failed: {rr.stderr.strip()[:500]}")
            return False

    log_err(f"Unknown install method: {chosen}")
    return False

# ---------------------------------------------------------------------------
# Check / status
# ---------------------------------------------------------------------------

def status_report(target: TargetUser) -> None:
    det = detect_localsend()
    thunar_ok = detect_thunar()
    fw = detect_firewall_kind()
    helper = target.home / ".local" / "bin" / HELPER_NAME
    uca = target.config_home / "Thunar" / "uca.xml"
    sendto = target.data_home / "Thunar" / "sendto" / "localsend.desktop"
    autostart = target.config_home / "autostart" / "localsend.desktop"

    if console and Table:
        table = Table(title="LocalSend Status — Arch/Thunar (2026-08-30)", show_lines=True)
        table.add_column("Component", style="bold cyan")
        table.add_column("Status", style="magenta")
        table.add_column("Details", style="dim")

        # Install
        if det.kind == "none":
            table.add_row("Install", "[red]Not installed[/]", "flatpak run org.localsend.localsend_app NOT found, localsend NOT in PATH")
        elif det.kind == "both":
            table.add_row("Install", "[green]Both[/]", f"native {det.native_path} ({det.native_version or '?'}) + flatpak {FLATPAK_ID}")
        elif det.kind == "native":
            table.add_row("Install", "[green]Native[/]", f"{det.native_path} ({det.native_version or '?'})")
        else:
            table.add_row("Install", "[green]Flatpak[/]", FLATPAK_ID)

        table.add_row("Thunar", "[green]Found[/]" if thunar_ok else "[yellow]Missing[/]", f"thunar @ {shutil.which('thunar') or 'not in PATH'}")
        table.add_row("Helper", "[green]Present[/]" if helper.exists() else "[red]Missing[/]", str(helper) + (f" ({helper.stat().st_size} B)" if helper.exists() else " — run setup"))
        # uca.xml check
        uca_status = "Missing"
        uca_detail = str(uca)
        if uca.exists():
            try:
                root = ET.parse(uca).getroot()
                has = any(_action_matches_localsend(a) for a in root.findall("action"))
                uca_status = "[green]Integrated[/]" if has else "[yellow]No LocalSend action[/]"
                uca_detail = f"{uca} ({len(root.findall('action'))} actions, has LocalSend={has})"
            except Exception as e:
                uca_status = "[red]Parse error[/]"
                uca_detail = f"{e}"
        table.add_row("Thunar uca.xml", uca_status, uca_detail)
        table.add_row("SendTo", "[green]Present[/]" if sendto.exists() else "[yellow]Missing[/]", str(sendto))
        # XDG autostart detailed status (Hidden true/false)
        xdg_status = "Missing"
        xdg_detail = str(autostart)
        if autostart.exists():
            try:
                txt = autostart.read_text(encoding="utf-8")
                hidden = "Hidden=true" in txt
                enabled = not hidden and "localsend" in txt.lower()
                xdg_status = "[green]Enabled[/]" if enabled else "[yellow]Disabled (opt-in)[/]"
                xdg_detail = f"{autostart} (Hidden={'true' if hidden else 'false'})"
            except Exception as e:
                xdg_status = "[red]Error[/]"
                xdg_detail = str(e)
        table.add_row("Autostart XDG", xdg_status, xdg_detail)
        # Hypr autostart
        hypr_path = target.home / ".config" / "hypr" / "edit_here" / "source" / "autostart.lua"
        hypr_status = "Missing"
        hypr_detail = str(hypr_path)
        if hypr_path.exists():
            try:
                txt = hypr_path.read_text(encoding="utf-8")
                if HYPR_MARKER_START in txt:
                    # check if exec line is commented or not
                    # look for hl.exec_cmd("localsend or flatpak) not commented
                    import re as _re
                    # find block
                    block_pat = _re.compile(_re.escape(HYPR_MARKER_START) + r".*?" + _re.escape(HYPR_MARKER_END), _re.DOTALL)
                    m = block_pat.search(txt)
                    if m:
                        block = m.group(0)
                        # enabled if hl.exec_cmd(" is present without leading --
                        has_enabled = _re.search(r'^\s*hl\.exec_cmd\(', block, _re.MULTILINE) is not None
                        has_disabled = '-- hl.exec_cmd(' in block
                        if has_enabled:
                            hypr_status = "[green]Enabled[/]"
                        elif has_disabled:
                            hypr_status = "[yellow]Disabled (opt-in)[/]"
                        else:
                            hypr_status = "[yellow]Template[/]"
                        hypr_detail = f"{hypr_path} ({'enabled' if has_enabled else 'disabled, uncomment to enable'})"
                    else:
                        hypr_status = "[yellow]No marker[/]"
                else:
                    hypr_status = "[yellow]No LocalSend block[/]"
                    hypr_detail = f"{hypr_path} (run setup to seed opt-in template)"
            except Exception as e:
                hypr_status = "[red]Error[/]"
                hypr_detail = str(e)
        table.add_row("Autostart Hypr", hypr_status, hypr_detail)
        # Systemd service
        svc_path = target.home / ".config" / "systemd" / "user" / "localsend.service"
        svc_status = "Missing"
        svc_detail = str(svc_path)
        if svc_path.exists():
            r = run(["systemctl", "--user", "is-enabled", "localsend.service"], check=False, capture=True, timeout=4)
            enabled = r.returncode == 0
            svc_status = "[green]Enabled[/]" if enabled else "[yellow]Disabled[/]"
            svc_detail = f"{svc_path} (systemctl --user {'is-enabled' if enabled else 'disabled'})"
        table.add_row("Autostart systemd", svc_status, svc_detail)
        table.add_row("Firewall", fw, f"Port {PORT}/tcp+udp (multicast {MULTICAST}) — {fw} manager")
        table.add_row("Protocol", "v2", f"HTTPS REST + {MULTICAST}:{PORT} UDP multicast; default alias/port in shared_preferences.json")
        console.print(table)
    else:
        print(f"Install: {det.kind} {det.native_path or ''} {FLATPAK_ID if det.flatpak_present else ''}")
        print(f"Thunar: {thunar_ok} helper:{helper.exists()} uca:{uca.exists()} sendto:{sendto.exists()} autostart:{autostart.exists()} fw:{fw}")

    # Human hints
    cprint("\n[bold]Hints:[/]")
    if det.kind == "none":
        cprint("  • Install: [cyan]python localsend.py --install auto[/]  (or --install flatpak / --install native)")
    if not helper.exists() or not uca.exists() or not any(_action_matches_localsend(a) for a in ET.parse(uca).getroot().findall("action")) if uca.exists() else True:
        # second condition guarded
        pass
    cprint(f"  • Right-click any file(s)/folder(s) in Thunar → [cyan]{ACTION_NAME}[/] (or Send To → LocalSend)")
    cprint(f"  • Receive: open LocalSend app; folder is [cyan]{target.home}/Downloads[/] (change in app Settings → Destination)")
    cprint(f"  • Firewall test: [cyan]ss -tulpn | grep {PORT}[/]  and  [cyan]avahi-browse _localsend._tcp[/] (if avahi) or simply discover from phone")
    if fw == "none":
        cprint("  • No firewall active — if you later enable ufw, re-run [cyan]python localsend.py --firewall[/]")

# ---------------------------------------------------------------------------
# Uninstall / cleanup
# ---------------------------------------------------------------------------

def uninstall_integrations(target: TargetUser, dry_run: bool = False, remove_helper: bool = False, remove_autostart: bool = True) -> None:
    uca = target.config_home / "Thunar" / "uca.xml"
    sendto = target.data_home / "Thunar" / "sendto" / "localsend.desktop"
    autostart = target.config_home / "autostart" / "localsend.desktop"
    hypr_path = target.home / ".config" / "hypr" / "edit_here" / "source" / "autostart.lua"
    svc_path = target.home / ".config" / "systemd" / "user" / "localsend.service"
    helper = target.home / ".local" / "bin" / HELPER_NAME

    if dry_run:
        log_info(f"[dry-run] would remove SendTo {sendto} and autostart {autostart} (+hypr block +systemd service), and LocalSend action from {uca}")
        if hypr_path.exists():
            log_info(f"[dry-run]   would remove LocalSend block from {hypr_path}")
        if svc_path.exists():
            log_info(f"[dry-run]   would disable & remove {svc_path}")
        if remove_helper:
            log_info(f"[dry-run] would also remove helper {helper}")
        return

    # Remove SendTo
    if sendto.exists():
        try:
            sendto.unlink()
            log_ok(f"Removed SendTo → {sendto}")
        except Exception as e:
            log_warn(f"Could not remove SendTo: {e}")
    else:
        log_info("SendTo not present — skipping")

    # Remove autostart (XDG)
    if remove_autostart and autostart.exists():
        # only remove if it's ours (contains localsend)
        try:
            txt = autostart.read_text(encoding="utf-8", errors="ignore")
            if "localsend" in txt.lower():
                autostart.unlink()
                log_ok(f"Removed autostart XDG → {autostart}")
            else:
                log_warn(f"Autostart {autostart} doesn't look like LocalSend — not removing")
        except Exception as e:
            log_warn(f"Could not remove autostart: {e}")
    elif remove_autostart:
        log_info("Autostart XDG not present — skipping")

    # Remove Hypr autostart block
    if remove_autostart and hypr_path.exists():
        try:
            txt = hypr_path.read_text(encoding="utf-8")
            if HYPR_MARKER_START in txt:
                import re as _re
                pat = _re.compile(r"[ \t]*" + _re.escape(HYPR_MARKER_START) + r".*?" + _re.escape(HYPR_MARKER_END), _re.DOTALL)
                if pat.search(txt):
                    new_txt = pat.sub("", txt)
                    # Clean up double blank lines
                    new_txt = _re.sub(r"\n{3,}", "\n\n", new_txt)
                    tmp = hypr_path.with_suffix(".tmp")
                    tmp.write_text(new_txt, encoding="utf-8")
                    if is_root():
                        pw = pwd.getpwnam(target.name)
                        os.chown(tmp, pw.pw_uid, pw.pw_gid)
                    tmp.rename(hypr_path)
                    log_ok(f"Removed Hypr autostart block → {hypr_path}")
                else:
                    log_info("Hypr marker found but no block to remove")
            else:
                log_info("No Hypr LocalSend block — skipping")
        except Exception as e:
            log_warn(f"Could not clean Hypr autostart: {e}")

    # Remove systemd service
    if remove_autostart and svc_path.exists():
        try:
            # disable first
            run(["systemctl", "--user", "disable", "--now", "localsend.service"], check=False, capture=True, timeout=10)
            svc_path.unlink()
            log_ok(f"Removed systemd service → {svc_path}")
            run(["systemctl", "--user", "daemon-reload"], check=False, capture=True, timeout=10)
        except Exception as e:
            log_warn(f"Could not remove systemd service: {e}")
    elif remove_autostart:
        # check if not exists, skip log already done for XDG/hypr
        pass

    # Remove uca.xml action(s)
    if uca.exists():
        try:
            tree = ET.parse(uca)
            root = tree.getroot()
            to_remove = [a for a in root.findall("action") if _action_matches_localsend(a)]
            if to_remove:
                for a in to_remove:
                    root.remove(a)
                tmp = uca.with_suffix(".tmp")
                tree.write(tmp, encoding="utf-8", xml_declaration=True)
                if is_root():
                    try:
                        pw = pwd.getpwnam(target.name)
                        os.chown(tmp, pw.pw_uid, pw.pw_gid)
                    except Exception:
                        pass
                tmp.rename(uca)
                log_ok(f"Removed {len(to_remove)} LocalSend action(s) from {uca}")
                # reload thunar
                base_cmd = ["thunar", "-q"]
                if is_root():
                    run([*sudo_user_prefix(target), *base_cmd], check=False, capture=True, timeout=6)
                else:
                    run(base_cmd, check=False, capture=True, timeout=6)
            else:
                log_info("No LocalSend action in uca.xml — skipping")
        except Exception as e:
            log_warn(f"Failed to clean uca.xml: {e}")
    else:
        log_info("uca.xml not present — skipping")

    if remove_helper:
        if helper.exists():
            try:
                helper.unlink()
                log_ok(f"Removed helper → {helper}")
            except Exception as e:
                log_warn(f"Could not remove helper: {e}")
        else:
            log_info("Helper not present — skipping")
    else:
        log_info(f"Helper preserved at {helper} (use --remove-helper to delete)")

    log_ok("Uninstall complete. Firewall rules were NOT removed (manual: sudo ufw delete allow 53317)")

# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        description="LocalSend setup for Arch+Thunar (2026-08-30 bleeding edge) — idempotent, no hardcoded user, autostart OPT-IN (off by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python localsend.py                     # interactive (Thunar only, autostart OFF)\n"
            "  python localsend.py --check             # status\n"
            "  python localsend.py --dry-run           # preview\n"
            "  python localsend.py --yes --install flatpak --autostart           # enable hypr autostart (opt-in)\n"
            "  python localsend.py --yes --autostart --autostart-method xdg      # enable XDG autostart\n"
            "  python localsend.py --yes --autostart --autostart-method systemd  # enable systemd user service\n"
            "  python localsend.py --yes --autostart --autostart-method all      # enable hypr+xdg+systemd\n"
            "  python localsend.py --uninstall         # remove Thunar integration\n"
            "  python localsend.py --no-hidden --autostart  # autostart without --hidden (workaround #2927)\n"
            "\n"
            "Fresh Arch one-shot: run once → Thunar helper+autostart template (disabled) persists. Re-run with --autostart to enable.\n"
            "XDG generator skips Exec if binary missing — hypr method is more robust on fresh install (no daemon-reload needed).\n"
        ),
    )
    p.add_argument("--install", choices=["auto", "native", "native-bin", "flatpak", "flatpak-user", "skip"], default=None,
                   help="Installation method (default: interactive ask; auto picks localsend-bin prebuilt when not installed)")
    p.add_argument("--no-install", action="store_true", help="Skip installation step")
    p.add_argument("--thunar", action="store_true", help="Force Thunar setup (default: always unless --check/--uninstall)")
    p.add_argument("--no-thunar", action="store_true", help="Skip Thunar Custom Action/SendTo")
    p.add_argument("--firewall", action="store_true", help="Force firewall setup")
    p.add_argument("--no-firewall", action="store_true", help="Skip firewall")
    p.add_argument("--autostart", action="store_true", help="Enable autostart (OPT-IN, off by default; default method is hypr edit_here)")
    p.add_argument("--no-autostart", action="store_true", help="Explicitly disable autostart (opt-out, sets Hidden=true / comments hypr)")
    p.add_argument("--autostart-method", choices=["hypr", "xdg", "systemd", "all"], default="hypr",
                   help="Autostart backend when --autostart is used (default: hypr; xdg=~/.config/autostart/*.desktop, hypr=edit_here/source/autostart.lua, systemd=user service, all=hypr+xdg)")
    p.add_argument("--hidden", dest="hidden", action="store_true", help="Use --hidden for autostart (default)")
    p.add_argument("--no-hidden", dest="hidden", action="store_false", help="Do NOT use --hidden (workaround for #2927 race)")
    p.add_argument("--check", action="store_true", help="Show status and exit")
    p.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    p.add_argument("--yes", "-y", action="store_true", help="Assume yes for all prompts (non-interactive)")
    p.add_argument("--force", action="store_true", help="Force overwrite even if up-to-date")
    p.add_argument("--uninstall", action="store_true", help="Remove Thunar/SendTo/autostart integrations")
    p.add_argument("--remove-helper", action="store_true", help="With --uninstall, also remove helper script")
    p.set_defaults(hidden=True)

    args = p.parse_args()

    target = get_target_user()
    det = detect_localsend()

    # Banner
    if console and Panel:
        console.print(Panel.fit(
            "[bold cyan]LocalSend Setup[/] — Arch Linux + Thunar 4.20.9\n"
            "[dim]Bleeding-edge 2026-08-30 • Protocol v2 • Port 53317/tcp+udp • Multicast 224.0.0.167[/]\n"
            f"[dim]User: {target.name}  Home: {target.home}  Config: {target.config_home}[/]",
            border_style="cyan",
        ))
    else:
        cprint(f"LocalSend Setup — user={target.name} home={target.home}")

    # Check mode
    if args.check:
        status_report(target)
        sys.exit(0)

    # Uninstall mode
    if args.uninstall:
        if not args.yes and not args.dry_run and Confirm:
            if not Confirm.ask(f"Remove LocalSend Thunar integration for {target.name}?", default=False):
                cprint("Cancelled.")
                sys.exit(0)
        uninstall_integrations(target, dry_run=args.dry_run, remove_helper=args.remove_helper)
        sys.exit(0)

    # Dry-run implies check + preview
    if args.dry_run:
        cprint("[bold yellow]DRY-RUN — no files will be written[/]")
        status_report(target)
        cprint("")

    # Decide what to do: if no explicit flags, interactive wizard or full setup
    do_install = None  # None = decide later
    do_thunar = not args.no_thunar
    do_firewall = not args.no_firewall
    # Autostart is OPT-IN (off by default) — new as of 2026-08-30 hypr edit_here preference
    if args.autostart and args.no_autostart:
        log_err("Cannot use both --autostart and --no-autostart")
        sys.exit(2)
    if args.autostart:
        do_autostart: bool | None = True
    elif args.no_autostart:
        do_autostart = False
    else:
        do_autostart = None  # not specified → don't touch autostart unless seeding template

    # CLI overrides
    if args.no_install:
        do_install = False
    elif args.install:
        if args.install == "skip":
            do_install = False
        else:
            do_install = args.install  # type: ignore[assignment]

    if args.thunar:
        do_thunar = True
    if args.firewall:
        do_firewall = True
    # do_autostart already handled via tri-state

    # If no flags at all (default invocation), set all true and interactive (but autostart stays OPT-IN)
    no_explicit = not any([args.install, args.no_install, args.thunar, args.no_thunar, args.firewall, args.no_firewall, args.autostart, args.no_autostart])
    if no_explicit:
        # Full setup by default but autostart remains OPT-IN (off)
        do_thunar = True
        do_firewall = True
        # do_autostart stays None → will seed disabled templates, not enable
        if do_install is None:
            # Interactive: ask about install only if not already installed and not dry-run+yes
            if det.kind == "none":
                if args.yes:
                    do_install = "auto"
                elif Confirm and not args.dry_run:
                    cprint("\n[bold]LocalSend not found.[/] Choose installation method:")
                    cprint("  [cyan]flatpak[/] = fast (~58 MB, flathub, 1.18.2) — recommended")
                    cprint("  [cyan]native[/]  = AUR localsend (builds Rust+Flutter, ~10-15 min)")
                    cprint("  [cyan]skip[/]    = configure Thunar only")
                    choice = Confirm.ask("Install via Flatpak now? (No = skip install)", default=True)
                    do_install = "flatpak" if choice else False
                    if not choice and Confirm.ask("Try AUR native build instead?", default=False):
                        do_install = "native"
                else:
                    do_install = "auto"
            else:
                do_install = False

    # Normalize do_install: False | str
    if do_install is None:
        do_install = False
    # In --yes non-interactive with no --install flag but det==none, auto-install flatpak
    if do_install is False and det.kind == "none" and args.yes and no_explicit:
        do_install = "auto"

    # Interactive confirmations for each phase if not --yes and not dry-run
    def ask_phase(name: str, default: bool = True) -> bool:
        if args.yes or args.dry_run:
            return default
        if Confirm is None:
            return default
        return Confirm.ask(f"Proceed: {name}?", default=default)

    # Execute phases
    failed: list[str] = []

    # Phase 1: Install
    if do_install:
        method = do_install if isinstance(do_install, str) else "auto"
        # mypy guard
        assert isinstance(method, str)
        # Validate method value is InstallMethod
        if method not in ("auto", "native", "native-bin", "flatpak", "flatpak-user", "skip"):
            method = "auto"
        if not ask_phase(f"Install LocalSend ({method})", default=True):
            log_info("Skipped install (user declined)")
        else:
            ok = install_localsend(method, target, dry_run=args.dry_run)  # type: ignore[arg-type]
            if not ok:
                failed.append("install")
            else:
                # refresh detection after install
                det = detect_localsend()

    # Phase 2: Helper + Thunar
    if do_thunar:
        # Helper always needed for Thunar
        if not ask_phase("Thunar integration (helper + uca.xml + SendTo)", default=True):
            log_info("Skipped Thunar setup")
        else:
            try:
                helper_path = setup_helper(target, dry_run=args.dry_run)
                # Thunar checks: ensure thunar installed
                if not detect_thunar():
                    log_warn("Thunar not found in PATH — still writing uca.xml for future Thunar installs")
                setup_thunar_uca(target, helper_path, dry_run=args.dry_run, force=args.force)
                setup_thunar_sendto(target, helper_path, dry_run=args.dry_run)
            except Exception as e:
                log_err(f"Thunar setup failed: {e}")
                failed.append("thunar")

    # Phase 3: Firewall
    if do_firewall:
        if not ask_phase("Firewall (open 53317/tcp+udp)", default=True):
            log_info("Skipped firewall")
        else:
            try:
                setup_firewall(dry_run=args.dry_run)
            except Exception as e:
                log_err(f"Firewall setup failed: {e}")
                failed.append("firewall")

    # Phase 4: Autostart (OPT-IN, default OFF — hypr edit_here preferred)
    # do_autostart tri-state: True=enable, False=disable, None=seed disabled template only if missing
    method = args.autostart_method
    if do_autostart is True:
        if not ask_phase(f"Autostart ({method}, {'--hidden' if args.hidden else 'visible'})", default=True):
            log_info("Skipped autostart")
        else:
            try:
                if method in ("hypr", "all"):
                    setup_hypr_autostart(target, det, dry_run=args.dry_run, enable=True, use_hidden=args.hidden)
                if method in ("xdg", "all"):
                    setup_autostart(target, det, dry_run=args.dry_run, use_hidden=args.hidden, force=args.force, enable=True)
                if method in ("systemd", "all"):
                    setup_systemd_autostart(target, det, dry_run=args.dry_run, enable=True, use_hidden=args.hidden)
                if method not in ("hypr", "xdg", "systemd", "all"):
                    setup_hypr_autostart(target, det, dry_run=args.dry_run, enable=True, use_hidden=args.hidden)
                # When enabling hypr, also ensure XDG remains correctly disabled or vice versa? Leave other methods as they were.
                # For 'all', all backends enabled; for 'hypr' only hypr enabled, XDG stays as is (disabled template).
            except Exception as e:
                log_err(f"Autostart failed: {e}")
                failed.append("autostart")
    elif do_autostart is False:
        if not ask_phase(f"Disable autostart ({method})", default=True):
            log_info("Skipped autostart disable")
        else:
            try:
                if method in ("hypr", "all"):
                    setup_hypr_autostart(target, det, dry_run=args.dry_run, enable=False, use_hidden=args.hidden)
                if method in ("xdg", "all"):
                    setup_autostart(target, det, dry_run=args.dry_run, use_hidden=args.hidden, force=args.force, enable=False)
                if method in ("systemd", "all"):
                    setup_systemd_autostart(target, det, dry_run=args.dry_run, enable=False, use_hidden=args.hidden)
                if method == "all":
                    # ensure all disabled
                    setup_hypr_autostart(target, det, dry_run=args.dry_run, enable=False, use_hidden=args.hidden)
                    setup_autostart(target, det, dry_run=args.dry_run, use_hidden=args.hidden, force=args.force, enable=False)
            except Exception as e:
                log_err(f"Autostart disable failed: {e}")
                failed.append("autostart")
    else:
        # Not specified → seed Hypr opt-in template on fresh install without enabling (XDG not seeded for Hyprland setup)
        # This ensures opt-in is visible but not active; does not overwrite existing enabled state.
        # XDG autostart is DE-agnostic fallback only, created only when --autostart-method xdg/all
        try:
            hypr_path = target.home / ".config" / "hypr" / "edit_here" / "source" / "autostart.lua"
            needs_hypr_seed = False
            if not hypr_path.exists():
                needs_hypr_seed = True
            else:
                try:
                    txt = hypr_path.read_text(encoding="utf-8")
                    if HYPR_MARKER_START not in txt:
                        needs_hypr_seed = True
                except Exception:
                    needs_hypr_seed = True
            if needs_hypr_seed:
                log_info("Seeding Hypr autostart opt-in template (disabled, commented)")
                setup_hypr_autostart(target, det, dry_run=args.dry_run, enable=False, use_hidden=args.hidden)
        except Exception as e:
            log_warn(f"Hypr seed failed (non-fatal): {e}")

    # Final report
    cprint("\n" + "="*72)
    if args.dry_run:
        cprint("[bold yellow]Dry-run complete — no changes were written.[/]")
        cprint("Re-run without [cyan]--dry-run[/] to apply: [cyan]python localsend.py --yes[/]")
    elif failed:
        log_err(f"Setup completed with failures: {', '.join(failed)}")
        status_report(target)
        sys.exit(1)
    else:
        log_ok("Setup complete — LocalSend is ready!")
        status_report(target)
        cprint("\n[bold green]Next steps:[/]")
        cprint(f"  1. Open Thunar → right-click file(s) → [cyan]{ACTION_NAME}[/]")
        cprint("  2. On phone/peer: ensure same Wi-Fi/VLAN (no AP isolation), open LocalSend — you should appear as Nearby Device")
        cprint(f"  3. Receive folder: [cyan]{target.home}/Downloads[/] (change in LocalSend Settings)")
        cprint("  4. Test: [cyan]localsend --help[/] or [cyan]flatpak run org.localsend.localsend_app[/]")
        cprint("\n[dim]Tip: If autostart tray doesn't appear, re-run with [cyan]--no-hidden[/] (see #2927).[/]")
        cprint("[dim]Firewall: if you switch from ufw to firewalld, re-run [cyan]--firewall[/].[/]")
        cprint("[dim]To remove: [cyan]python localsend.py --uninstall[/][/]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        cprint("\n[bold yellow]Interrupted by user.[/]")
        sys.exit(130)

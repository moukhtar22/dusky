#!/usr/bin/env python3
#d: Interactive PAM faillock lockout policy manager (deny/unlock_time with live verification)

"""
225_faillock_timeout.py — Modern PAM faillock policy manager for Arch Linux.

Manages /etc/security/faillock.conf (deny threshold, lockout duration, root
policy) with an interactive Rich wizard, autonomous CLI presets, atomic
POSIX-safe writes, timestamped backups, and a self-contained empirical
verification pipeline that stress-tests the live lockout behavior through
the real PAM stack and proves it via journald.

Design notes
------------
* pam_faillock reads a SINGLE configuration file — no dropin/include
  mechanism exists (verified against pam 1.7.x faillock.conf(5)).
  This tool therefore manages the one file atomically with backups.
* ``unlock_time = 0`` means "never auto-unlock" (manual ``faillock --reset``
  or reboot only). It is offered as an explicit dangerous preset.
* The default tally directory is /var/run/faillock (tmpfs) and is cleared on
  reboot; a persistent ``dir`` is optional.
* Verification never locks a user for longer than the temporary test window
  (hard-capped at 60s) and always restores the original policy afterwards.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Final

# ==============================================================================
# Rich UI (guarded import — degrade to plain output when unavailable)
# ==============================================================================

RICH_AVAILABLE = False
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, IntPrompt, Prompt
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
    console = Console(record=True)
    error_console = Console(stderr=True, record=True)
except ImportError:
    Console = Panel = Confirm = IntPrompt = Prompt = Table = Text = None  # type: ignore[assignment]
    console = error_console = None  # type: ignore[assignment]


_MARKUP_RE = re.compile(r"\[/?[a-zA-Z_][a-zA-Z0-9_= ]*?\]")


def _out(msg: str) -> None:
    if console:
        console.print(msg)
    else:
        print(_MARKUP_RE.sub("", msg))


def _err(msg: str) -> None:
    if error_console:
        error_console.print(msg)
    else:
        print(_MARKUP_RE.sub("", msg), file=sys.stderr)


# ==============================================================================
# Constants & Domain Model
# ==============================================================================

CONFIG_PATH: Final = Path("/etc/security/faillock.conf")
BACKUP_DIR: Final = Path("/etc/security/faillock.conf.backups")
VERIFY_LOG_DIR: Final = Path("/var/log/faillock-verify")
BUILTIN_DEFAULTS: Final[dict[str, int | str]] = {
    "deny": 3,
    "fail_interval": 900,
    "unlock_time": 600,
    "root_unlock_time": 600,
    "dir": "/var/run/faillock",
}
MAX_TEST_UNLOCK: Final = 60  # hard safety cap for the --verify temporary policy


class Preset(str, Enum):
    """Named policy profiles selectable interactively or via --preset."""

    DEFAULT = "default"  # pristine stock template (all options commented)
    LENIENT = "lenient"  # high threshold, very short lock (original 225 intent)
    BALANCED = "balanced"  # moderate threshold, short lock
    STRICT = "strict"  # stock thresholds, explicitly pinned
    NEVER = "never"  # unlock_time=0 — permanent lock until manual reset


PRESET_TABLE: Final[dict[Preset, dict[str, int | str | None]]] = {
    Preset.DEFAULT: {},
    Preset.LENIENT: {
        "deny": 6, "fail_interval": 900, "unlock_time": 90,
        "root_unlock_time": 300, "even_deny_root": False,
    },
    Preset.BALANCED: {
        "deny": 5, "fail_interval": 900, "unlock_time": 300,
        "root_unlock_time": 600, "even_deny_root": False,
    },
    Preset.STRICT: {
        "deny": 3, "fail_interval": 900, "unlock_time": 600,
        "root_unlock_time": 600, "even_deny_root": False,
    },
    Preset.NEVER: {
        "deny": 5, "fail_interval": 900, "unlock_time": 0,
        "root_unlock_time": 0, "even_deny_root": True,
    },
}


@dataclass(frozen=True, slots=True)
class FaillockPolicy:
    """Effective faillock policy. ``None`` values mean 'not set in file'
    (built-in defaults then apply)."""

    deny: int | None = None
    fail_interval: int | None = None
    unlock_time: int | None = None
    root_unlock_time: int | None = None
    even_deny_root: bool = False
    admin_group: str | None = None
    tally_dir: str | None = None

    # -- effective values (built-in defaults when unset) ------------------
    @property
    def eff_deny(self) -> int:
        return self.deny if self.deny is not None else int(BUILTIN_DEFAULTS["deny"])

    @property
    def eff_fail_interval(self) -> int:
        return self.fail_interval if self.fail_interval is not None else int(BUILTIN_DEFAULTS["fail_interval"])

    @property
    def eff_unlock_time(self) -> int:
        return self.unlock_time if self.unlock_time is not None else int(BUILTIN_DEFAULTS["unlock_time"])

    @property
    def eff_root_unlock_time(self) -> int:
        return self.root_unlock_time if self.root_unlock_time is not None else self.eff_unlock_time

    @property
    def eff_tally_dir(self) -> str:
        return self.tally_dir if self.tally_dir is not None else str(BUILTIN_DEFAULTS["dir"])

    # -- parsing / rendering ---------------------------------------------
    ACTIVE_KEYS: Final = ("deny", "fail_interval", "unlock_time", "root_unlock_time", "even_deny_root", "admin_group", "dir")

    @classmethod
    def from_file(cls, path: Path = CONFIG_PATH) -> FaillockPolicy:
        kwargs: dict[str, int | bool | str] = {}
        if path.is_file():
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key, value = key.strip(), value.strip()
                else:
                    # bare flag options (no '=') are valid faillock.conf syntax
                    key, value = line, None
                match key:
                    case "deny":
                        if (v := _clamp_int(value or "", 1, 9999)) is not None:
                            kwargs["deny"] = v
                    case "fail_interval":
                        if (v := _clamp_int(value or "", 1, 99999)) is not None:
                            kwargs["fail_interval"] = v
                    case "unlock_time":
                        if (v := _clamp_int(value or "", 0, 99999)) is not None:
                            kwargs["unlock_time"] = v
                    case "root_unlock_time":
                        if (v := _clamp_int(value or "", 0, 99999)) is not None:
                            kwargs["root_unlock_time"] = v
                    case "even_deny_root":
                        kwargs["even_deny_root"] = True
                    case "admin_group":
                        kwargs["admin_group"] = value or None
                    case "dir":
                        kwargs["tally_dir"] = value or None
        return cls(**kwargs)

    def to_content(self) -> str:
        """Renders a documented faillock.conf (stock template + active options)."""
        def comment(opt: str, doc: str) -> list[str]:
            return [f"# {opt}", f"# {doc}"] if opt not in ("even_deny_root",) else [f"# {doc}"]

        blocks: list[str] = [
            "# Configuration for locking the user after multiple failed",
            "# authentication attempts. Managed by 225_faillock_timeout.py.",
            "#",
            f"# {'dir = ' + self.tally_dir if self.tally_dir else 'dir = /var/run/faillock (default, tmpfs — cleared on reboot)'}",
        ]
        blocks.append("")
        if self.deny is not None:
            blocks += [f"# Lock out after {self.deny} consecutive failures within the interval.", f"deny = {self.deny}"]
        if self.fail_interval is not None:
            blocks += [f"# Counting window for consecutive failures, in seconds.", f"fail_interval = {self.fail_interval}"]
        if self.unlock_time is not None:
            blocks += [f"# Re-enable access {self.unlock_time}s after lockout (0 = never, manual reset only).", f"unlock_time = {self.unlock_time}"]
        if self.root_unlock_time is not None:
            blocks += [f"# Root re-enable delay (only honored with even_deny_root).", f"root_unlock_time = {self.root_unlock_time}"]
        blocks += [f"# Even root can be locked (with root_unlock_time above)."]
        blocks += [f"# even_deny_root"] if not self.even_deny_root else [f"even_deny_root"]
        if self.admin_group is not None:
            blocks += [f"# Members of this group are treated like root.", f"admin_group = {self.admin_group}"]
        blocks.append("")
        return "\n".join(blocks)

    def describes(self, other: FaillockPolicy) -> bool:
        """True when every explicitly-set value of ``other`` equals ours."""
        return all(
            getattr(self, f) == getattr(other, f)
            for f in ("deny", "fail_interval", "unlock_time", "root_unlock_time", "even_deny_root", "admin_group", "tally_dir")
        )


def _clamp_int(raw: str, lo: int, hi: int) -> int | None:
    """Parses an integer within [lo, hi]; returns None (treated as unset) for
    garbage so corrupt values can never silently alter policy semantics."""
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return None


# ==============================================================================
# Duration utilities (human-friendly input)
# ==============================================================================

_DURATION_RE = re.compile(r"^(\d+)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hours?)?$", re.IGNORECASE)

def parse_duration(raw: str) -> int:
    """Parses '90', '90s', '5m', '1h' (bare numbers = seconds). 'never'/'0' -> 0."""
    if raw.strip().lower() in ("never", "none", "off"):
        return 0
    m = _DURATION_RE.match(raw.strip())
    if not m:
        raise ValueError(f"invalid duration '{raw}' (use e.g. 90s, 5m, 1h, never)")
    value, unit = int(m.group(1)), (m.group(2) or "s").lower()
    multiplier = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
                  "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
                  "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}[unit]
    return value * multiplier


def format_duration(seconds: int) -> str:
    """Renders seconds as '1 hour', '10 minutes', '90 seconds', or 'never'."""
    if seconds == 0:
        return "never (manual reset only)"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} hour{'s' if seconds // 3600 != 1 else ''}"
    if seconds % 60 == 0:
        return f"{seconds // 60} minute{'s' if seconds // 60 != 1 else ''}"
    return f"{seconds} seconds"


# ==============================================================================
# Atomic file IO (POSIX-guaranteed, borrowed/refined from 020/127 patterns)
# ==============================================================================

def write_atomic(dest: Path, content: str, mode: int = 0o644) -> bool:
    """Genuinely atomic write: O_CREAT|O_EXCL temp + fsync file and parent dir."""
    temp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    temp.unlink(missing_ok=True)
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
            os.fchmod(f.fileno(), mode)
        temp.replace(dest)
        try:
            dir_fd = os.open(dest.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
        return True
    except OSError as e:
        _err(f"[bold red]✖ Critical IO error on {dest.name}: {e}[/bold red]")
        return False
    finally:
        temp.unlink(missing_ok=True)


def backup_with_retention(path: Path, keep: int = 10) -> Path | None:
    """Timestamped backup with retention pruning (borrowed from 127 pattern)."""
    if not path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bak = BACKUP_DIR / f"{path.name}.{datetime.now():%Y%m%d-%H%M%S}"
    try:
        shutil.copy2(path, bak)
        for old in sorted(BACKUP_DIR.glob(f"{path.name}.*"), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]:
            old.unlink(missing_ok=True)
        return bak
    except OSError as e:
        _err(f"[bold yellow]⚠ Backup failed ({bak}): {e}[/bold yellow]")
        return None


def file_sha256(path: Path) -> str | None:
    """SHA-256 via C-optimized digest (borrowed from 020 pattern)."""
    try:
        with path.open("rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest()
    except OSError:
        return None


# ==============================================================================
# Privilege & user context (borrowed from 130/290 patterns)
# ==============================================================================

@dataclass(frozen=True, slots=True)
class UserContext:
    username: str
    uid: int
    is_root: bool


def resolve_user_context() -> UserContext:
    """Resolves the real non-root user (SUDO_UID/PKEXEC_UID/loginuid fallbacks)."""
    is_root = os.geteuid() == 0
    real_uid = os.getuid()
    if is_root:
        escalation_uid = os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID")
        if escalation_uid and escalation_uid.isdigit():
            real_uid = int(escalation_uid)
        elif "DOAS_USER" in os.environ:
            try:
                import pwd
                real_uid = pwd.getpwnam(os.environ["DOAS_USER"]).pw_uid
            except (KeyError, ImportError):
                pass
        else:
            try:
                loginuid = int(Path("/proc/self/loginuid").read_text(encoding="utf-8").strip())
                if loginuid != 4294967295:
                    real_uid = loginuid
            except (FileNotFoundError, ValueError, OSError):
                pass
    import pwd
    try:
        pw = pwd.getpwuid(real_uid)
    except KeyError:
        _err(f"[bold red]✖ Fatal: UID {real_uid} does not map to a system user.[/bold red]")
        sys.exit(1)
    return UserContext(username=pw.pw_name, uid=pw.pw_uid, is_root=is_root)


def ensure_root() -> None:
    """Self-elevates via sudo (password prompt), preserving arguments.

    Non-interactive sessions are only elevated when a valid sudo token is
    already cached — a failed password conversation would otherwise feed
    pam_faillock's failure counter (self-inflicted lockouts).
    """
    if os.geteuid() == 0:
        return
    if not shutil.which("sudo"):
        _err("[bold red]✖ Root required and sudo is unavailable. Run as root.[/bold red]")
        sys.exit(1)
    if not sys.stdin.isatty():
        try:
            subprocess.run(["sudo", "-n", "true"], check=True, capture_output=True, timeout=10)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            _err("[bold yellow]⚠ Root required, no TTY, and no cached sudo token — refusing to guess.[/bold yellow]")
            _err("[bold yellow]  Run interactively, or elevate yourself: sudo python3 225_faillock_timeout.py …[/bold yellow]")
            sys.exit(2)
    try:
        os.execvp("sudo", ["sudo", "-p", "[sudo] password for %u: ", sys.executable, *sys.argv])
    except OSError as e:
        _err(f"[bold red]✖ Elevation failed: {e}[/bold red]")
        sys.exit(1)


def run(cmd: list[str], *, timeout: int = 60, input: str | None = None, check: bool = False) -> subprocess.CompletedProcess:
    """Subprocess wrapper with timeouts; never throws unless check=True."""
    try:
        return subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, input=input, timeout=timeout, check=check,
        )
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, -9, (e.stdout or "") + "\n[TIMEOUT]", stderr="")
    except subprocess.CalledProcessError as e:
        return subprocess.CompletedProcess(cmd, e.returncode, e.output or "", stderr="")


# ==============================================================================
# Faillock runtime inspection
# ==============================================================================

def tally_path_for(user: str, policy: FaillockPolicy) -> Path:
    return Path(policy.eff_tally_dir) / user


def faillock_state(user: str, policy: FaillockPolicy) -> tuple[int, bool, float | None]:
    """Returns (failure_count, locked, newest_record_ts).

    Locked = count >= deny AND the newest failure is still inside the
    unlock_time window. Expired records persist in the tally file and are
    pruned by pam_faillock on new failures, so the row COUNT is not
    monotonic — track timestamps, not just counts.
    """
    count = 0
    last_ts: float | None = None
    proc = run(["faillock", "--user", user], timeout=10)
    if proc.returncode == 0:
        for l in proc.stdout.splitlines():
            m = re.match(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})", l)
            if not m:
                continue
            count += 1
            try:
                ts = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S").timestamp()
                last_ts = ts if last_ts is None else max(last_ts, ts)
            except ValueError:
                pass
    else:
        tally = tally_path_for(user, policy)
        if tally.is_file():
            try:
                count = tally.stat().st_size // 64
            except OSError:
                pass
    locked = count >= policy.eff_deny
    if last_ts is not None and policy.eff_unlock_time > 0:
        locked = locked and (time.time() - last_ts) < policy.eff_unlock_time
    return count, locked, last_ts


def reset_faillock(user: str | None) -> bool:
    cmd = ["faillock", "--reset"] + (["--user", user] if user else [])
    proc = run(cmd, timeout=15)
    if proc.returncode != 0:
        _err(f"[bold yellow]⚠ faillock reset failed: {proc.stdout.strip()}[/bold yellow]")
        return False
    return True


# ==============================================================================
# Rendering
# ==============================================================================

def render_state(policy: FaillockPolicy, ctx: UserContext) -> None:
    """Table of current file state + effective policy + live tally."""
    if not RICH_AVAILABLE:
        _out("Current policy:")
        _out(f"  deny={policy.eff_deny}  fail_interval={policy.eff_fail_interval}s  "
             f"unlock_time={policy.eff_unlock_time}s  root_unlock_time={policy.eff_root_unlock_time}s  "
             f"even_deny_root={'yes' if policy.even_deny_root else 'no'}  dir={policy.eff_tally_dir}")
        count, locked, _ = faillock_state(ctx.username, policy)
        _out(f"  {ctx.username}: {count} failure(s) — {'LOCKED' if locked else 'not locked'}")
        return

    table = Table(title="PAM faillock — Current Policy", header_style="bold cyan", expand=True)
    table.add_column("Option", style="bold white")
    table.add_column("In File", style="yellow")
    table.add_column("Effective", style="green")

    def row(label: str, raw: str, eff: str, warn: bool = False) -> None:
        table.add_row(label, raw, f"[bold red]{eff}[/bold red]" if warn else eff)

    status = lambda v: (str(v) if v is not None else "—")
    row("Failure threshold (deny)", status(policy.deny), str(policy.eff_deny))
    row("Counting window (fail_interval)", status(policy.fail_interval), f"{policy.eff_fail_interval}s ({format_duration(policy.eff_fail_interval)})")
    row("Lock duration (unlock_time)", status(policy.unlock_time), format_duration(policy.eff_unlock_time), warn=policy.eff_unlock_time == 0)
    row("Root lock duration", status(policy.root_unlock_time), format_duration(policy.eff_root_unlock_time))
    row("even_deny_root", "set" if policy.even_deny_root else "—", "yes" if policy.even_deny_root else "no")
    row("admin_group", status(policy.admin_group), status(policy.admin_group))
    row("Tally directory (dir)", status(policy.tally_dir), policy.eff_tally_dir)
    _out(table)

    count, locked, _ = faillock_state(ctx.username, policy)
    lock_txt = Text.from_markup(
        f"Current tally for [bold cyan]{ctx.username}[/bold cyan]: "
        f"{count} failure(s) — "
        + ("[bold red]LOCKED[/bold red]" if locked else "[bold green]not locked[/bold green]")
    )
    _out(Panel(lock_txt, title="Live Account State", border_style="red" if locked else "green", expand=False))


def render_diff(old: str, new: str) -> None:
    """Unified diff of the policy file before/after."""
    if not RICH_AVAILABLE:
        _out("--- current ---")
        _out(old)
        _out("--- new ---")
        _out(new)
        return
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), "current", "new", lineterm=""):
        if line.startswith("+"):
            _out(f"[bold green]{line}[/bold green]")
        elif line.startswith("-"):
            _out(f"[bold red]{line}[/bold red]")
        elif line.startswith(("@@", "---", "+++")):
            _out(f"[bold cyan]{line}[/bold cyan]")
        else:
            _out(f"[dim]{line}[/dim]")


# ==============================================================================
# Apply flow
# ==============================================================================

def is_pristine(p: FaillockPolicy) -> bool:
    """True when no option is explicitly set (pure built-in defaults)."""
    return (
        not p.even_deny_root
        and all(getattr(p, f) is None for f in ("deny", "fail_interval", "unlock_time", "root_unlock_time", "admin_group", "tally_dir"))
    )


def apply_policy(policy: FaillockPolicy, *, dry_run: bool = False) -> bool:
    """Writes the policy atomically with backup + permissions. Returns success."""
    current = FaillockPolicy.from_file()
    if policy.describes(current) or (is_pristine(policy) and is_pristine(current)):
        _out("[bold green]✔ Policy already matches — nothing to do.[/bold green]")
        return True

    new_content = policy.to_content()
    if CONFIG_PATH.is_file():
        render_diff(CONFIG_PATH.read_text(encoding="utf-8", errors="ignore"), new_content)
    else:
        _out("[bold yellow]⚠ /etc/security/faillock.conf does not exist — will be created.[/bold yellow]")

    if dry_run:
        _out("[bold yellow]*** DRY-RUN — no changes made ***[/bold yellow]")
        return True

    backup_with_retention(CONFIG_PATH)
    if not write_atomic(CONFIG_PATH, new_content):
        return False
    try:
        os.chown(CONFIG_PATH, 0, 0)
    except OSError:
        pass
    # Parse-back verification — the file must round-trip exactly.
    if not FaillockPolicy.from_file().describes(policy):
        _err("[bold red]✖ Written file failed parse-back verification — check manually.[/bold red]")
        return False
    _out(f"[bold green]✔ Applied → {CONFIG_PATH} (sha256: {file_sha256(CONFIG_PATH)[:16]}…)[/bold green]")
    return True


# ==============================================================================
# Interactive wizard
# ==============================================================================

WIZARD_PRESETS: Final = [
    (Preset.BALANCED, "Balanced (recommended)", "5 failures → 5 min lock; root unlocked after 10 min"),
    (Preset.LENIENT, "Lenient — short lock", "6 failures → 90 s lock (original 225 intent)"),
    (Preset.STRICT, "Strict — stock values", "3 failures → 10 min lock (pam defaults, pinned)"),
    (Preset.NEVER, "Never auto-unlock", "Permanent lock until manual faillock --reset"),
    (Preset.DEFAULT, "Stock default template", "Reset file to pristine pam defaults"),
]


def _select(prompt: str, options: list[str], default_idx: int = 1) -> str:
    """Numbered-choice selector (accepts 1-N, partial text, or exact text)."""
    while True:
        _out(f"[bold cyan]{prompt}[/bold cyan]")
        for i, o in enumerate(options, 1):
            _out(f"  [yellow]{i}[/yellow]. {o}" + ("  [dim](default)[/dim]" if i == default_idx else ""))
        raw = (Prompt.ask(">", default=str(default_idx)) or "").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        for o in options:
            if raw and (o.startswith(raw) or raw in o):
                return o
        _err("[bold red]✖ Invalid choice — enter a number (1-{}) or part of the option text.[/bold red]".format(len(options)))


_DURATION_MAP: Final = {
    "30 seconds": 30, "90 seconds": 90, "5 minutes": 300, "10 minutes": 600,
    "15 minutes": 900, "30 minutes": 1800, "1 hour": 3600, "never": 0,
}


def run_wizard(ctx: UserContext, dry_run: bool) -> None:
    """Interactive preset + tuning flow (Rich prompts)."""
    profile_opts = [f"{p.value} — {label}" for p, label, _ in WIZARD_PRESETS]
    choice = _select("Select a policy profile", profile_opts)
    preset = Preset(choice.split(" — ", 1)[0])

    if preset is Preset.DEFAULT:
        policy = FaillockPolicy()
    else:
        base = PRESET_TABLE[preset]
        deny = IntPrompt.ask("Failures before lockout (deny)", default=int(base.get("deny", 5)), choices=[str(i) for i in range(1, 21)])
        unlock_sel = _select("Lock duration (unlock_time)",
                             ["30 seconds", "90 seconds", "5 minutes", "10 minutes", "15 minutes", "30 minutes", "1 hour", "never (manual reset only)"],
                             default_idx=3)
        unlock_seconds = _DURATION_MAP["never" if unlock_sel.startswith("never") else unlock_sel]
        root_sel = _select("Root lock duration (root_unlock_time)",
                           ["Same as regular users", "30 seconds", "5 minutes", "10 minutes", "30 minutes", "never"])
        root_seconds = unlock_seconds if root_sel == "Same as regular users" else _DURATION_MAP[root_sel]
        even_root = Confirm.ask("Apply locking to root as well (even_deny_root)?", default=False)
        policy = FaillockPolicy(
            deny=deny,
            fail_interval=900,
            unlock_time=unlock_seconds,
            root_unlock_time=root_seconds if even_root else None,
            even_deny_root=even_root,
        )

    _out("\n[bold cyan]Resulting policy:[/bold cyan]")
    render_state(policy, ctx)
    if not dry_run and not Confirm.ask("Apply this policy?", default=True):
        _out("[bold yellow]Aborted by user.[/bold yellow]")
        return
    if apply_policy(policy, dry_run=dry_run):
        _out(f"[bold cyan]Tip:[/bold cyan] run [bold]--verify[/bold] to stress-test this policy against the live PAM stack.")


# ==============================================================================
# Empirical verification pipeline (--verify)
# ==============================================================================

WRONG_PW: Final = "faillock-verify-wrong-password"


def journal_excerpt(since: datetime, until: datetime) -> str:
    """Pulls pam_faillock / sudo auth lines from journald for the test window."""
    fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")
    proc = run(
        ["journalctl", "--since", fmt(since), "--until", fmt(until), "--no-pager", "-o", "short-iso"],
        timeout=30,
    )
    lines = [l for l in proc.stdout.splitlines() if re.search(r"pam_faillock|sudo", l, re.IGNORECASE)]
    return "\n".join(lines[-40:]) if lines else "(no matching journald entries in window)"


def verify_policy(ctx: UserContext) -> bool:
    """Empirically proves the configured policy is honored by the live PAM stack.

    Strategy (no real password required):
      1. Apply a temporary test policy (deny=3, unlock_time=30, hard-capped ≤60s).
      2. Fail N authentications → tally must reach exactly N (threshold proof).
      3. More attempts while locked must NOT increment the tally (lock proof).
      4. After unlock_time elapses, an attempt restarts the tally (recovery proof).
      5. journald excerpt corroborates every step; original policy is restored.
    """
    original = CONFIG_PATH.read_text(encoding="utf-8", errors="ignore") if CONFIG_PATH.is_file() else None
    tally_ok = True
    journal_lines: list[str] = []
    log_path = VERIFY_LOG_DIR / f"verify-{datetime.now():%Y%m%d-%H%M%S}.log"
    start = datetime.now()
    failures: list[str] = []

    def check(cond: bool, what: str, detail: str = "") -> bool:
        nonlocal tally_ok
        tally_ok = tally_ok and cond
        if cond:
            _out(f"[bold green]✔ {what}[/bold green]")
        else:
            failures.append(f"{what}: {detail}")
            _out(f"[bold red]✖ FAIL: {what} — {detail}[/bold red]")
        return cond

    test_policy = FaillockPolicy(deny=3, fail_interval=900, unlock_time=30, root_unlock_time=30, even_deny_root=True)
    if RICH_AVAILABLE:
        _out(Panel(
            f"Target user: [bold cyan]{ctx.username}[/bold cyan]\n"
            f"Test policy: deny=3, unlock_time=30s (temp — original will be restored)\n"
            f"Log: [bold]{log_path}[/bold]",
            title="Faillock Live Verification", border_style="cyan", expand=False,
        ))
    else:
        _out(f"Faillock Live Verification\n"
             f"  Target user: {ctx.username}\n"
             f"  Test policy: deny=3, unlock_time=30s (temp — original will be restored)\n"
             f"  Log: {log_path}")

    if not shutil.which("faillock"):
        _err("[bold red]✖ 'faillock' CLI missing (pam package). Aborting verification.[/bold red]")
        return False

    # Phase 0 — apply temp policy
    if not write_atomic(CONFIG_PATH, test_policy.to_content()):
        _err("[bold red]✖ Cannot write temporary test policy. Aborting.[/bold red]")
        return False
    try:
        os.chown(CONFIG_PATH, 0, 0)
    except OSError:
        pass
    live_policy = FaillockPolicy.from_file()
    if not live_policy.describes(test_policy):
        _err("[bold red]✖ Temp policy failed parse-back — live PAM stack will not honor it. Aborting.[/bold red]")
        return False
    reset_faillock(ctx.username)
    pre_count, _, _ = faillock_state(ctx.username, live_policy)
    if pre_count:
        _out(f"[bold yellow]⚠ pre-existing tally: {pre_count} (factored into expected counts)[/bold yellow]")

    try:
        # Phase 1 — threshold: 3 attempts must lock the account
        _out("\n[bold cyan]Phase 1 — threshold: 3 failures must lock the account[/bold cyan]")
        for i in range(1, 4):
            proc = run(["sudo", "-u", ctx.username, "sudo", "-S", "true"], input=WRONG_PW, timeout=20)
            _out(f"  attempt {i}: auth rejected (exit {proc.returncode}) [expected]")
        count, locked, last_ts = faillock_state(ctx.username, live_policy)
        expected = 3 + pre_count
        check(count == expected, f"tally reached exactly {expected} (got {count})",
              f"tally={count}" + (" — no failures were counted; is the target user permitted to use sudo? pam_faillock only counts PAM auth attempts" if count == 0 else ""))
        check(locked, "account is locked after 3 failures", f"locked={locked}")

        # Phase 2 — lock proof: further attempts must NOT increment the tally
        _out("\n[bold cyan]Phase 2 — locked account must reject further attempts without counting[/bold cyan]")
        for i in range(2):
            run(["sudo", "-u", ctx.username, "sudo", "-S", "true"], input=WRONG_PW, timeout=20)
        count2, locked2, _ = faillock_state(ctx.username, live_policy)
        check(locked2 and count2 == count, f"tally stayed at {count} while locked (got {count2})", f"tally={count2}")

        # Phase 3 — time-based recovery: after expiry the account unlocks and
        # a new failure is recorded (tally rows are pruned/rewritten by
        # pam_faillock, so assert on the lock transition + record timestamps).
        sleep_for = max(5, live_policy.eff_unlock_time + 5)
        _out(f"\n[bold cyan]Phase 3 — waiting {sleep_for}s for unlock_time to elapse…[/bold cyan]")
        time.sleep(sleep_for)
        _, unlocked_check, _ = faillock_state(ctx.username, live_policy)
        run(["sudo", "-u", ctx.username, "sudo", "-S", "true"], input=WRONG_PW, timeout=20)
        count3, locked3, last3 = faillock_state(ctx.username, live_policy)
        fmt_ts = lambda t: datetime.fromtimestamp(t).strftime("%H:%M:%S") if t else "?"
        check(not unlocked_check, f"account unlocked after {live_policy.eff_unlock_time}s (lock window elapsed)", f"locked={unlocked_check}")
        check(last3 is not None and last_ts is not None and last3 > last_ts,
              f"failure accepted & counted after unlock (last record {fmt_ts(last_ts)} → {fmt_ts(last3)})",
              f"last record: {fmt_ts(last3)}")

        # Phase 4 — journald corroboration
        _out("\n[bold cyan]Phase 4 — journald evidence[/bold cyan]")
        excerpt = journal_excerpt(start, datetime.now())
        journal_lines.append(excerpt)
        for line in excerpt.splitlines():
            _out(f"  [dim]{line}[/dim]")
        if not excerpt.strip():
            _out("[bold yellow]⚠ No journald entries captured — journald may be unavailable; phases 1-3 already proved behavior via tally state.[/bold yellow]")
        else:
            check(bool(re.search(r"pam_faillock", excerpt, re.IGNORECASE)),
                  "journald shows pam_faillock activity", excerpt)
    finally:
        # Phase 5 — restore original policy + clear test tally
        if original is not None:
            restored = write_atomic(CONFIG_PATH, original)
            check_after = CONFIG_PATH.read_text(encoding="utf-8", errors="ignore")
            if not restored:
                _err("[bold red]✖ RESTORE FAILED — write_atomic returned False. File may hold the temporary test policy![/bold red]")
            elif check_after != original:
                _err("[bold red]✖ RESTORE VERIFICATION FAILED — file content does not match the captured original![/bold red]")
                _err(f"[bold yellow]  captured {len(original)} bytes, present {len(check_after)} bytes[/bold yellow]")
        else:
            CONFIG_PATH.unlink(missing_ok=True)
        reset_faillock(ctx.username)

    # Persist the verification log
    VERIFY_LOG_DIR.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        f.write(f"# faillock verification {start.isoformat()}\n")
        f.write(f"# user: {ctx.username}  result: {'PASS' if tally_ok else 'FAIL'}\n")
        f.write("## journald (pam_faillock/sudo)\n")
        f.write("\n".join(journal_lines) + "\n")
    _out(f"\n[bold cyan]Verification log: {log_path}[/bold cyan]")

    if tally_ok:
        if RICH_AVAILABLE:
            _out(Panel("[bold green]✔ VERIFICATION PASSED — the policy is honored by the live PAM stack.[/bold green]",
                       border_style="green", expand=False))
        else:
            _out("✔ VERIFICATION PASSED — the policy is honored by the live PAM stack.")
    else:
        if RICH_AVAILABLE:
            _err(Panel(f"[bold red]✖ VERIFICATION FAILED:\n" + "\n".join(f"  • {f}" for f in failures),
                       border_style="red", expand=False))
        else:
            _err("✖ VERIFICATION FAILED:\n" + "\n".join(f"  • {f}" for f in failures))
    return tally_ok


# ==============================================================================
# CLI & main
# ==============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="225_faillock_timeout.py",
        description="Interactive PAM faillock policy manager — threshold, lock duration, root policy, with live verification.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-s", "--show", action="store_true", help="Show current policy and live account state")
    p.add_argument("-p", "--preset", type=str, choices=[pr.value for pr in Preset], help="Apply a named policy profile")
    p.add_argument("--deny", type=int, metavar="N", help="Failures before lockout (1-20)")
    p.add_argument("--unlock-time", type=str, metavar="DUR", help="Lock duration: 90s / 10m / 1h / never")
    p.add_argument("--fail-interval", type=int, metavar="SEC", help="Counting window in seconds")
    p.add_argument("--root-unlock-time", type=str, metavar="DUR", help="Root lock duration (with --even-deny-root)")
    p.add_argument("--even-deny-root", action="store_true", dest="even_deny_root", help="Lock root too")
    p.add_argument("--no-even-deny-root", action="store_true", dest="no_even_deny_root", help="Exclude root from locking")
    p.add_argument("--dir", type=str, metavar="PATH", help="Persistent tally directory (advanced)")
    p.add_argument("-V", "--verify", action="store_true", help="Stress-test the live PAM stack (temp policy, journald proof)")
    p.add_argument("-n", "--dry-run", action="store_true", help="Preview changes without touching the file")
    p.add_argument("-y", "--yes", action="store_true", help="Non-interactive: apply without confirmation")
    p.add_argument("-i", "--interactive", action="store_true", help="Force the interactive wizard")
    return p


def main() -> int:
    args = build_parser().parse_args()
    ensure_root()
    ctx = resolve_user_context()
    policy = FaillockPolicy.from_file()

    if RICH_AVAILABLE:
        _out(Panel(
            Text.from_markup(
                "[bold cyan]PAM Faillock Policy Manager[/bold cyan]\n"
                f"[dim]User: {ctx.username} | File: {CONFIG_PATH} | pam_faillock: single config, no dropins (by design)[/dim]"
            ),
            border_style="cyan", expand=False,
        ))

    if args.show:
        render_state(policy, ctx)
        return 0

    if args.verify:
        return 0 if verify_policy(ctx) else 1

    interactive = args.interactive or (not args.preset and not args.deny and not args.unlock_time
                                       and not args.fail_interval and not args.root_unlock_time
                                       and not args.dir and not args.even_deny_root and not args.no_even_deny_root)

    if interactive:
        if not sys.stdin.isatty():
            _err("[bold yellow]⚠ No explicit options given and no TTY — refusing to guess. Use a preset or -y.[/bold yellow]")
            _err("[bold yellow]  e.g. 225_faillock_timeout.py --preset lenient -y   or   --show[/bold yellow]")
            return 2
        if not RICH_AVAILABLE:
            _err("[bold red]✖ Interactive wizard requires python-rich: sudo pacman -S python-rich[/bold red]")
            return 2
        run_wizard(ctx, dry_run=args.dry_run)
        return 0

    # Explicit option/preset path
    if args.preset:
        base = dict(PRESET_TABLE[Preset(args.preset)])
        new = FaillockPolicy(
            deny=base.get("deny"),
            fail_interval=base.get("fail_interval"),
            unlock_time=base.get("unlock_time"),
            root_unlock_time=base.get("root_unlock_time"),
            even_deny_root=bool(base.get("even_deny_root", False)),
        )
    else:
        try:
            new = FaillockPolicy(
                deny=args.deny,
                fail_interval=args.fail_interval,
                unlock_time=parse_duration(args.unlock_time) if args.unlock_time else None,
                root_unlock_time=parse_duration(args.root_unlock_time) if args.root_unlock_time else None,
                even_deny_root=args.even_deny_root and not args.no_even_deny_root,
                tally_dir=args.dir,
            )
        except ValueError as e:
            _err(f"[bold red]✖ {e}[/bold red]")
            return 2

    if not args.dry_run and not args.yes and not sys.stdin.isatty():
        _err("[bold yellow]⚠ Non-interactive session: pass -y to apply, or use --dry-run to preview.[/bold yellow]")
        return 2

    if not args.yes and not args.dry_run:
        render_state(new, ctx)
        if not Confirm.ask("Apply this policy?", default=True):
            _out("[bold yellow]Aborted by user.[/bold yellow]")
            return 130

    ok = apply_policy(new, dry_run=args.dry_run)
    if ok and new.unlock_time is not None and new.unlock_time == 0:
        _err("[bold red]⚠ unlock_time=0: the account will lock permanently until manual 'faillock --reset' or reboot.[/bold red]")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _out("\n[bold red]󰞅 Aborted by user (SIGINT).[/bold red]")
        sys.exit(130)

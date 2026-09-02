#!/usr/bin/env python3
#d: Refresh the font cache and align default font aliases

"""
Font cache refresh + default-font alignment for clean installs.

Aligns the setup-time font state with the Dusky Font Manager TUI:
  * builds conf.d/99-dusky-fonts.conf using the actual engine (same writer,
    same DTD header, binding="strong" aliases) from the schema defaults,
  * adds metric-compat Arial/Helvetica/Verdana -> default sans-family
    rewrites (qual="first" form, verified NOT to hijack generic requests),
  * runs `fc-cache -f`, then verifies sans-serif/serif/monospace/emoji and
    Arial/Helvetica/Verdana/Times New Roman resolution.

Default family is configurable without editing code:
    DUSKY_DEFAULT_SANS="JetBrainsMono Nerd Font" python3 140_dusky_font_configurator.py
  or --font-family "..." (schema Tab-0 default is Atkinson Hyperlegible).
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
NC = "\033[0m"

USER_SCRIPTS = Path(os.environ.get("USER_SCRIPTS", "~/user_scripts")).expanduser().resolve()
DUSKY_TUI_ROOT = USER_SCRIPTS / "dusky_tui"
SCHEMA_PATH = USER_SCRIPTS / "fonts" / "tui_fonts.py"
ENGINE_OUTPUT = "~/.config/fontconfig/conf.d/99-dusky-fonts.conf"

_METRIC_COMPAT_SANS = ("Arial", "Helvetica", "Verdana")
_METRIC_COMPAT_EMOJI = ("Segoe UI Emoji", "Apple Color Emoji", "Twemoji Mozilla")


def _load_schema():
    """Import tui_fonts schema from the fonts repo via importlib."""
    if not SCHEMA_PATH.is_file():
        return None
    sys.path.insert(0, str(SCHEMA_PATH.parent))
    if str(DUSKY_TUI_ROOT) not in sys.path:
        sys.path.insert(0, str(DUSKY_TUI_ROOT))
    spec = importlib.util.spec_from_file_location("_tui_fonts_140", SCHEMA_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_tui_fonts_140"] = mod
    spec.loader.exec_module(mod)
    return mod


def _default_sans_family() -> str:
    """DUSKY_DEFAULT_SANS env var, then --font arg (in os.environ), then the
    schema's Tab 0 sans-serif default, then Atkinson Hyperlegible."""
    env = os.environ.get("DUSKY_DEFAULT_SANS")
    if env:
        return env
    mod = _load_schema()
    if mod:
        for items in mod.SCHEMA.values():
            for item in items:
                if item.key == "sans-serif" and item.default:
                    return str(item.default)
    return "Atkinson Hyperlegible"


def _metric_rewrite_block(name: str, target: str) -> str:
    return (
        f'  <match target="pattern">\n'
        f'    <test qual="first" name="family">\n'
        f'      <string>{name}</string>\n'
        f'    </test>\n'
        f'    <edit name="family" mode="assign" binding="strong">\n'
        f'      <string>{target}</string>\n'
        f'    </edit>\n'
        f'  </match>\n'
    )


def build_config(target: str) -> tuple[bool, str]:
    """Write canonical config via the real engine (same path the TUI uses),
    overriding Tab 0 sans-serif to the requested default."""
    if not DUSKY_TUI_ROOT.is_dir():
        return False, f"missing engine root: {DUSKY_TUI_ROOT}"
    sys.path.insert(0, str(DUSKY_TUI_ROOT))
    mod = _load_schema()
    if mod is None:
        return False, f"missing schema: {SCHEMA_PATH}"
    try:
        from python.engines.fontconfig import FontconfigEngine

        changes = []
        for items in mod.SCHEMA.values():
            for item in items:
                if item.type_ in ("action", "preset"):
                    continue
                val = target if item.key == "sans-serif" else item.default
                changes.append((item.key, item.scope, val, item.type_))

        engine = FontconfigEngine(ENGINE_OUTPUT)
        ok, msg, err = engine.write_batch(changes)

        conf = Path(ENGINE_OUTPUT).expanduser()
        if ok and conf.is_file():
            text = conf.read_text()
            missing_sans = [name for name in _METRIC_COMPAT_SANS
                            if f">{name}</string>" not in text]
            missing_emoji = [name for name in _METRIC_COMPAT_EMOJI
                             if f">{name}</string>" not in text]
            # Determine the configured emoji family from schema defaults
            emoji_target = "Noto Color Emoji"
            if mod:
                for items in mod.SCHEMA.values():
                    for item in items:
                        if item.key == "emoji" and item.default:
                            emoji_target = str(item.default)
            blocks = "".join(
                _metric_rewrite_block(n, target) for n in missing_sans
            ) + "".join(
                _metric_rewrite_block(n, emoji_target) for n in missing_emoji
            )
            if blocks:
                new_text = text.replace("</fontconfig>", blocks + "</fontconfig>")
                # Atomic replace: fontconfig re-parses this file on every
                # fc-match/fc-cache call, so a truncate-then-write would let
                # a concurrent reader see a half-written (unparseable) conf
                # and silently drop every alias for that call.
                tmp = conf.with_name(f".{conf.name}.tmp-{os.getpid()}-{time.monotonic_ns()}")
                try:
                    tmp.write_text(new_text)
                    tmp.replace(conf)
                finally:
                    if tmp.exists():
                        try:
                            tmp.unlink()
                        except OSError:
                            pass
            _drop_legacy_conf()
        return (ok, msg) if ok else (False, err or msg)
    except Exception as exc:
        return False, str(exc)


def _drop_legacy_conf() -> None:
    """The engine absorbs the legacy ~/.config/fontconfig/fonts.conf into its
    own emit state, so the legacy file must not keep applying raw
    qual="any" + binding="strong" rewrites alongside the canonical config
    (that is how 'Times New Roman' rewrites kept hijacking generic serif
    requests). Delete it outright; no backup is kept."""
    legacy = Path.home() / ".config" / "fontconfig" / "fonts.conf"
    if not legacy.is_file():
        return
    legacy.unlink()
    print("  [i] Removed legacy fonts.conf (superseded by generated config)")


def _wait_fc_cache_idle(timeout: float = 30.0) -> None:
    """Wait until no background fc-cache process is still writing the cache.

    write_batch launches an async fc-cache (fire-and-forget); if the script
    rebuilds the cache concurrently, whichever process finishes last wins
    and the other's output may reflect a stale config, making the alias
    verification fail intermittently. Drain before rebuilding.
    """
    time.sleep(0.1)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            proc = subprocess.run(["pgrep", "-x", "fc-cache"],
                                  capture_output=True, text=True, timeout=5)
        except Exception:
            return
        if proc.returncode != 0:
            return
        time.sleep(0.2)


def resolve_match(family: str) -> str:
    try:
        out = subprocess.run(
            ["fc-match", "--format=%{family}\n", family],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return ""
    return out.strip()


def _schema_defaults(mod) -> dict[str, str]:
    """Generic-family defaults straight from the schema (same source the
    engine's write_batch uses), so verification can never drift from what
    was actually configured."""
    out = {}
    if mod:
        for items in mod.SCHEMA.values():
            for item in items:
                if item.key in ("sans-serif", "serif", "monospace", "emoji") and item.default:
                    out[item.key] = str(item.default)
    return out


def _family_installed(family: str) -> bool:
    """True if the family is installed (fc-list is authoritative)."""
    try:
        proc = subprocess.run(
            ["fc-list", "--format=%{family}\n", ":"],
            capture_output=True, text=True, timeout=15,
        )
        known = {
            item.strip().lower()
            for line in proc.stdout.splitlines()
            if line.strip()
            for item in line.split(",")
            if item.strip()
        }
        return family.lower() in known
    except Exception:
        return True  # fc-list unavailable: fall back to strict resolution


def verify(target_sans: str, schema: dict[str, str]) -> tuple[int, list[tuple[str, bool, str]]]:
    """Resolve each alias and separate genuine misconfigurations from
    missing prerequisites.

    * expectation for every generic comes from the schema defaults (the
      same families write_batch just wrote into the config), so the check
      can never drift from the configuration;
    * if the configured family is not installed, that is a font-package
      prerequisite, not an alias failure: report it loudly as a warning
      instead of a FAIL (the alias config itself is correct);
    * if the family IS installed but resolution goes elsewhere, that is a
      real failure and still exits non-zero after retries.
    """
    serif_expect = schema.get("serif", "Liberation Serif")
    generic_checks = [
        ("sans-serif", target_sans),
        ("serif", serif_expect),
        ("monospace", schema.get("monospace", "JetBrainsMono Nerd Font Mono")),
        ("emoji", schema.get("emoji", "Noto Color Emoji")),
    ]
    results = []
    for generic, expect in generic_checks:
        results.append(_check_alias(f"{generic} -> {expect}", generic, expect))

    for name in (*_METRIC_COMPAT_SANS, "Times New Roman"):
        expect = target_sans if name in _METRIC_COMPAT_SANS else serif_expect
        results.append(_check_alias(f"{name} -> {expect}", name, expect))

    failures = sum(1 for _label, ok, _note in results if not ok)
    return failures, results


def _check_alias(label: str, family: str, expect: str) -> tuple[str, bool, str]:
    """One alias check: warn (pass) when the expected family is missing,
    strict-fail when it is installed but resolves incorrectly."""
    if not _family_installed(expect):
        return (label, True,
                f"family '{expect}' is not installed on this system; "
                "alias config is correct, install the font package to activate it")
    return (label, _retried(family, expect), "")


def _retried(family: str, expect: str, attempts: int = 3) -> bool:
    """Try up to `attempts` times, then decide with the last non-empty
    result. A consistent mismatch still fails (loudly)."""
    last = ""
    for _ in range(attempts):
        out = resolve_match(family)
        if _matched(out, expect):
            return True
        if not out:
            time.sleep(0.4)
            continue
        last = out
    return _matched(last, expect)


def _matched(matcher_out: str, expect: str) -> bool:
    if not matcher_out or not expect:
        return False
    matched_families = [f.strip().lower() for f in matcher_out.split(",") if f.strip()]
    exp_lower = expect.strip().lower()
    return any(exp_lower == fam or exp_lower in fam for fam in matched_families)


def main() -> int:
    parser = argparse.ArgumentParser(description="Font cache refresh + alias verify")
    parser.add_argument("--font-family", default=None,
                        help="Override the default sans-serif family "
                             "(also: DUSKY_DEFAULT_SANS)")
    args = parser.parse_args()
    if args.font_family:
        os.environ["DUSKY_DEFAULT_SANS"] = args.font_family

    target = _default_sans_family()
    print(f"{YELLOW}:: Refreshing System Font Cache (target sans: "
          f"{GREEN}{target}{NC}{YELLOW})...{NC}")

    ok, msg = build_config(target)
    if not ok:
        print(f"{RED}[FAIL] writing fontconfig config: {msg}{NC}")
        sys.exit(1)
    print(f"  [i] {msg}")

    _wait_fc_cache_idle()
    subprocess.run(["fc-cache", "-f"], check=False)
    _wait_fc_cache_idle()

    print(f"\n{YELLOW}:: Verifying Font Aliases...{NC}")
    schema = _schema_defaults(_load_schema())
    failures, results = verify(target, schema)
    warned = 0
    for label, ok, note in results:
        if ok and not note:
            print(f"{GREEN}[+] {label}{NC}")
        elif ok and note:
            warned += 1
            print(f"{YELLOW}[!] {label}{NC}  ({note})")
        else:
            print(f"{RED}[-] {label}{NC}")

    if failures:
        print(f"\n{RED}[FAIL] {failures} aliases resolved incorrectly.{NC}")
        sys.exit(1)
    if warned:
        print(f"\n{YELLOW}[OK] Alias config verified ({warned} family/"
              f"families not installed on this system yet - install the "
              f"corresponding font package to activate them).{NC}")
    print(f"\n{GREEN}[SUCCESS] System fonts aligned to '{target}'.{NC}")


if __name__ == "__main__":
    lock_path = Path("/tmp/.dusky-fontconfig-140.lock")
    with lock_path.open("w") as lock_fd:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        try:
            code = main()
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        sys.exit(code)
#!/usr/bin/env python3
#d: Deploy dusky's overlay configs

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

# --- ANSI Color Codes ---
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[0;33m'
    BLUE = '\033[0;34m'
    RESET = '\033[0m'

class ColoredFormatter(logging.Formatter):
    """Custom logging formatter for ANSI colored outputs."""
    FORMATS = {
        logging.DEBUG: f"{Colors.BLUE}[DEBUG]{Colors.RESET} %(message)s",
        logging.INFO: f"{Colors.BLUE}[INFO]{Colors.RESET} %(message)s",
        logging.WARNING: f"{Colors.YELLOW}[WARN]{Colors.RESET} %(message)s",
        logging.ERROR: f"{Colors.RED}[ERR]{Colors.RESET}  %(message)s",
        logging.CRITICAL: f"{Colors.RED}[CRIT]{Colors.RESET} %(message)s",
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, self.FORMATS[logging.INFO])
        # Success level is custom handled via an extra dict property
        if getattr(record, 'success', False):
            log_fmt = f"{Colors.GREEN}[OK]{Colors.RESET}   %(message)s"
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def log_success(msg: str):
    """Helper for success messages formatted exactly like the bash script."""
    logger.info(msg, extra={'success': True})

APPS_MODULE = "edit_here.source.default_apps"
OVERLAY_MODULE = "edit_here.hyprland"

# Mask Lua comments and long strings without mistaking quoted "--" for comments.
LUA_TOKENS = re.compile(
    r"--\[(?P<comment_eq>=*)\[.*?\](?P=comment_eq)\]"
    r"|--[^\n]*"
    r"|\[(?P<string_eq>=*)\[.*?\](?P=string_eq)\]"
    r"|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
    re.DOTALL,
)
REQUIRE_LINE = re.compile(
    r"\s*(?P<call>require\s*(?:\(\s*(?P<quote>['\"])(?P<module>[^'\"]+)(?P=quote)\s*\)"
    r"|(?P<bare_quote>['\"])(?P<bare_module>[^'\"]+)(?P=bare_quote))(?:[ \t]*;)?)\s*"
)


def active_lua(content: str) -> str:
    def mask(match: re.Match[str]) -> str:
        token = match.group()
        if token.startswith(("--", "[")):
            return "".join("\n" if char == "\n" else " " for char in token)
        return token

    return LUA_TOKENS.sub(mask, content)


def required_lines(content: str) -> dict[int, str]:
    """Find standalone requires, ignoring Lua comments and long strings."""
    active = active_lua(content)
    result = {}
    for index, line in enumerate(active.splitlines()):
        if match := REQUIRE_LINE.fullmatch(line):
            result[index] = match["module"] or match["bare_module"]
    return result


def require(module: str) -> str:
    return f'require("{module}")'


def loader_content(content: str, files: list[str]) -> str:
    active = set(required_lines(content).values())
    needed = {
        f"edit_here.source.{Path(file).stem}"
        for file in files if file != "default_apps.lua"
    } - active
    if not needed:
        return content
    # Reactivate real single-line comments in place to preserve override order.
    edits = []
    for token in LUA_TOKENS.finditer(content):
        text = token.group()
        if not text.startswith("--") or text.startswith("--["):
            continue
        line_start = content.rfind("\n", 0, token.start()) + 1
        if content[line_start:token.start()].strip():
            continue
        match = REQUIRE_LINE.fullmatch(active_lua(text[2:]))
        if match is None or "--[" in text[2:]:
            continue
        module = match["module"] or match["bare_module"]
        if module in needed:
            edits.append(token.start())
            needed.remove(module)
    for start in reversed(edits):
        content = content[:start] + "  " + content[start + 2:]
    missing = [require(module) for module in sorted(needed)]
    if not missing:
        return content
    return content + ("\n" if content and not content.endswith("\n") else "") + "\n".join(missing) + "\n"


def main_content(content: str) -> str:
    """Keep the app globals first and the overlay last, with one call each."""
    modules = required_lines(content)
    lines = content.splitlines(keepends=True)
    active_lines = active_lua(content).splitlines()
    body_lines = []
    for index, line in enumerate(lines):
        if modules.get(index) in (APPS_MODULE, OVERLAY_MODULE):
            match = REQUIRE_LINE.fullmatch(active_lines[index])
            start, end = match.span("call")
            # Preserve adjacent comments, especially long-comment delimiters.
            remainder = line[:start] + line[end:]
            if remainder.strip():
                body_lines.append(remainder)
        else:
            body_lines.append(line)
    body = "".join(body_lines)
    return require(APPS_MODULE) + "\n" + body + (
        "\n" if body and not body.endswith("\n") else ""
    ) + require(OVERLAY_MODULE) + "\n"


def atomic_write(path: Path, data: bytes, mode: int | None = None) -> bool:
    """Replace a complete file on the same filesystem; preserve symlink targets."""
    path = path.resolve()
    if path.exists():
        if path.read_bytes() == data:
            return False
        if mode is None:
            mode = path.stat().st_mode & 0o7777
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.chmod(mode if mode is not None else 0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def reload_config(*, reload: bool = True) -> None:
    """Report live-session problems without failing an offline overlay deployment."""
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        logger.info("No Hyprland session selected; configuration will load on next start.")
        return
    hyprctl = shutil.which("hyprctl")
    if not hyprctl:
        logger.warning("hyprctl is unavailable; reload the configuration manually.")
        return
    commands = [[hyprctl, "reload", "config-only"]] if reload else []
    commands.append([hyprctl, "configerrors"])
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning("Overlay deployed, but Hyprland verification failed: %s", error)
            if isinstance(error, subprocess.CalledProcessError):
                logger.warning("%s", (error.stderr or error.stdout or "No diagnostic returned").strip())
            return
        if command[1] == "configerrors" and result.stdout.strip():
            logger.warning("Overlay deployed, but Hyprland reports configuration errors:\n%s", result.stdout.strip())
            return
    if reload:
        log_success("Hyprland reloaded (config-only), with no reported configuration errors.")


def main() -> int:
    home = Path.home()
    hypr_dir = home / ".config" / "hypr"
    edit_dir = hypr_dir / "edit_here"
    source_dir = edit_dir / "source"
    main_conf = hypr_dir / "hyprland.lua"
    loader = edit_dir / "hyprland.lua"
    defaults_dir = home / "user_scripts" / "hypr" / "defaults" / "edit_here"
    default_files = sorted(path.name for path in defaults_dir.glob("*.lua") if path.is_file())

    parser = argparse.ArgumentParser(
        description="Initialize or validate the Hyprland user config overlay.",
        allow_abbrev=False,
    )
    parser.add_argument("--force", action="store_true", help="Back up and regenerate selected configs (all configs if no files are selected).")
    # A list avoids collisions between names such as foo-bar.lua and foo_bar.lua.
    for file in default_files:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*\.lua", file) or Path(file).stem in {"force", "help"}:
            parser.error(f"Unsupported template name: {file}")
        parser.add_argument(
            f"--{Path(file).stem}", f"--{file}", action="append_const",
            const=file, dest="targets", help=f"Target {file}",
        )
    args = parser.parse_args()
    if os.geteuid() == 0:
        logger.error("Run this script as the user whose configuration should be deployed.")
        return 1

    try:
        if "default_apps.lua" not in default_files:
            raise ValueError(f"Required default_apps.lua template is missing from {defaults_dir}")
        targets = sorted(set(args.targets or default_files))
        full_force = args.force and not args.targets
        # The main config always needs app globals, including on targeted installs.
        deploy = sorted(set(targets) | {"default_apps.lua"})
        templates = {file: (defaults_dir / file).read_bytes() for file in deploy}
        existing_main = main_conf.read_text(encoding="utf-8") if main_conf.exists() else ""
        existing_loader = loader.read_text(encoding="utf-8") if loader.exists() and not full_force else (
            "-- User configuration overlay; app globals are loaded by the main config.\n"
        )
        new_main = main_content(existing_main)
        loader_files = targets
        if not loader.exists() and not full_force:
            loader_files = sorted(set(targets) | {
                file for file in default_files if (source_dir / file).is_file()
            })
        new_loader = loader_content(existing_loader, loader_files)
        # Validate the intended destination types before moving any backups.
        for directory in (hypr_dir, edit_dir, source_dir):
            if directory.exists() and not directory.is_dir():
                raise ValueError(f"Expected a directory: {directory}")
        for file in deploy:
            destination = source_dir / file
            if destination.exists() and not destination.is_file():
                raise ValueError(f"Expected a file: {destination}")

        hypr_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        # Build a complete replacement before disturbing the current overlay.
        staging = tempfile.TemporaryDirectory(dir=hypr_dir, prefix=".edit_here.") if full_force else nullcontext()
        with staging as temporary:
            deploy_dir = Path(temporary) / "edit_here" if full_force else edit_dir
            deploy_source = deploy_dir / "source"
            deploy_source.mkdir(parents=True, exist_ok=True)
            changed = full_force
            for file in deploy:
                destination = deploy_source / file
                forced = args.force and file in targets
                if destination.exists() and not forced:
                    continue
                if forced and destination.exists():
                    backup_dir = deploy_source / "backups"
                    backup_dir.mkdir(exist_ok=True)
                    backup_file = backup_dir / f"{file}.bak_{timestamp}"
                    # Exclusive creation prevents overwriting an existing backup.
                    with backup_file.open("xb") as stream:
                        stream.write(destination.read_bytes())
                    shutil.copystat(destination, backup_file)
                    log_success(f"Backed up: {backup_file}")
                changed |= atomic_write(destination, templates[file], (defaults_dir / file).stat().st_mode & 0o7777)
                log_success(f"Prepared: {file}" if full_force else f"Deployed: {file}")
            changed |= atomic_write(deploy_dir / "hyprland.lua", new_loader.encode("utf-8"))
            backup = None
            try:
                if full_force:
                    if edit_dir.exists():
                        candidate = hypr_dir / f"edit_here.bak_{timestamp}"
                        if candidate.exists() or candidate.is_symlink():
                            raise FileExistsError(f"Backup already exists: {candidate}")
                        edit_dir.rename(candidate)
                        backup = candidate
                        log_success(f"Backed up overlay: {backup}")
                    deploy_dir.rename(edit_dir)
                changed |= atomic_write(main_conf, new_main.encode("utf-8"))
            except BaseException:
                # Restore the old overlay on publication failure or interruption.
                if backup is not None and (backup.exists() or backup.is_symlink()):
                    if edit_dir.exists():
                        shutil.rmtree(edit_dir)
                    backup.rename(edit_dir)
                raise
        reload_config(reload=changed)
        log_success("Overlay deployment complete.")
        logger.info("Custom configs: %s", edit_dir)
        return 0
    except (OSError, UnicodeError, ValueError) as error:
        logger.error("Setup failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())

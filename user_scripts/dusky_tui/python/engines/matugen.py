#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MATUGEN TOML CONFIGURATION ENGINE
===============================================================================
Target: Arch Linux / Hyprland / Matugen dynamic TOML template manager.
Python 3.14.7 implementation using PEP 695 type aliases, structural pattern matching,
concurrency timestamp checks, and atomic file replacements.
"""

import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path
from typing import override

from python.frontend.core_types import BaseEngine

# PEP 695 Strict Type Alias
type ScopeKeyMap = dict[str, bool]
type ChangeTuple = tuple[str, str, str, str]


class MatugenEngine(BaseEngine):
    """
    Production-grade AST-like parser and mutator for Matugen template blocks inside `config.toml`.
    
    Provides strict atomicity, external-change detection (nanosecond timestamps), precise multiline string
    tracking (preserving post_hook scripts with triple single/double quotes), blank-line demarcation,
    and comment-toggling.
    """

    # Matches active or commented template headers, e.g.:
    # [templates.gtk3]  or  # [templates.gtk4]  or  #   [templates.master_dump]
    _RE_TEMPLATE_HEADER = re.compile(
        r"^[ \t]*(#?)[ \t]*\[templates\.['\"]?([a-zA-Z0-9_.-]+)['\"]?\][ \t]*(?:#.*)?$"
    )

    # General TOML section header check
    _RE_ANY_HEADER = re.compile(r"^[ \t]*#?[ \t]*\[.*\][ \t]*(?:#.*)?$")

    def __init__(self, config_path: str | Path = "~/.config/matugen/config.toml") -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, bool] = {}
        self.file_mtime: int = 0

    @property
    @override
    def target_path(self) -> str:
        return str(self.config_path)

    @classmethod
    def _scan_sections(
        cls, lines: list[str], blank_lines: set[int] | None = None
    ) -> list[tuple[int, str | None, bool]]:
        """Find real headers, ignoring header-like text inside TOML strings."""
        sections = []
        multiline = ""
        disabled = False
        for index, raw_line in enumerate(lines):
            if blank_lines is not None and not multiline and not raw_line.strip():
                blank_lines.add(index)
            if not multiline and cls._RE_ANY_HEADER.match(raw_line):
                match = cls._RE_TEMPLATE_HEADER.match(raw_line)
                disabled = bool(match and match.group(1))
                sections.append((index, match.group(2) if match else None, not disabled))
                continue
            line = re.sub(r"^[ \t]*# ?", "", raw_line, count=1) if disabled else raw_line
            pos = 0
            while pos < len(line):
                if multiline:
                    if line.startswith(multiline, pos):
                        # TOML permits four/five closing quotes; consume the run.
                        quote = multiline[0]
                        while pos < len(line) and line[pos] == quote:
                            pos += 1
                        multiline = ""
                    elif multiline == '"""' and line[pos] == "\\":
                        pos += 2
                    else:
                        pos += 1
                elif line[pos] == "#":
                    break
                elif line[pos] in "\"'":
                    quote = line[pos]
                    if line.startswith(quote * 3, pos):
                        multiline = quote * 3
                        pos += 3
                    else:
                        pos += 1
                        while pos < len(line):
                            if line[pos] == quote:
                                pos += 1
                                break
                            pos += 2 if quote == '"' and line[pos] == "\\" else 1
                else:
                    pos += 1
        return sections

    @override
    def load_state(self) -> dict[str, bool]:
        """
        Parses all template blocks from config.toml into a state map.
        Key: template_name (e.g. 'gtk3', 'waybar')
        Value: True if active (uncommented header), False if disabled (commented header).
        """
        if not self.config_path.exists():
            self.cache = {}
            return self.cache

        try:
            self.file_mtime = self.config_path.stat().st_mtime_ns
        except OSError:
            self.file_mtime = 0

        self.cache = {}
        try:
            with self.config_path.open(encoding="utf-8") as f:
                sections = self._scan_sections(f.readlines())
            for _, template_key, active in sections:
                if template_key is not None:
                    self.cache[template_key] = active
                    self.cache[f"DEFAULT/{template_key}"] = active
        except (OSError, UnicodeError) as e:
            print(f"[-] MatugenEngine: Failed to read {self.config_path}: {e}")

        return self.cache

    @override
    def write_value(
        self,
        target_key: str,
        target_scope: str,
        new_value: str,
        item_type: str = "bool"
    ) -> tuple[bool, str, str]:
        """Routes single write calls to batch mutator."""
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    @override
    def write_batch(self, changes: list[ChangeTuple]) -> tuple[bool, str, str]:
        """
        Atomically toggles template blocks between commented (#) and uncommented states.
        Checks the loaded timestamp, preserves multiline strings, and validates resulting TOML.
        """
        if not changes:
            return True, "No pending changes.", ""

        if not self.config_path.exists():
            return False, f"Target configuration file {self.config_path} does not exist.", ""

        # Detect changes since the last load; this is not a writer lock.
        try:
            current_mtime = self.config_path.stat().st_mtime_ns
            if self.file_mtime > 0 and current_mtime != self.file_mtime:
                return False, f"File {self.config_path.name} was modified externally. Reload required.", ""
        except OSError:
            pass

        # Read full lines
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, UnicodeError) as e:
            return False, f"Failed to read {self.config_path}: {e}", ""

        # Build normalized changes map: target_key -> bool
        changes_dict: dict[str, bool] = {}
        for key, scope, val, _ in changes:
            clean_key = key.split("/")[-1] if "/" in key else key
            if isinstance(val, bool):
                bool_val = val
            elif isinstance(val, str):
                bool_val = val.strip().lower() in {"true", "1", "yes", "on", "t", "y"}
            else:
                bool_val = bool(val)
            changes_dict[clean_key] = bool_val

        modified = False
        blank_lines: set[int] = set()
        sections = self._scan_sections(lines, blank_lines)
        missing = changes_dict.keys() - {key for _, key, _ in sections}
        if missing:
            return False, f"Template keys not found: {', '.join(sorted(missing))}", ""
        pending_cache = self.cache.copy()
        for section_index, (start_idx, key, active) in enumerate(sections):
            if key not in changes_dict:
                continue
            target_active = changes_dict[key]
            pending_cache[key] = target_active
            pending_cache[f"DEFAULT/{key}"] = target_active
            if active == target_active:
                continue
            end_idx = sections[section_index + 1][0] if section_index + 1 < len(sections) else len(lines)
            # Leave comments introducing the next section outside this block.
            # Only a blank-line-separated comment group is an introduction.
            boundary = end_idx
            while boundary > start_idx + 1 and (
                not lines[boundary - 1].strip()
                or lines[boundary - 1].lstrip().startswith("#")
            ):
                boundary -= 1
            code_after = False
            for i in range(end_idx - 1, boundary - 1, -1):
                tail = lines[i]
                if (
                    re.match(r"^[ \t]*#?[ \t]*[\w\"'-]+[ \t]*=", tail)
                    or "'''" in tail
                    or '"""' in tail
                ):
                    code_after = True
                if i in blank_lines and not code_after:
                    end_idx = i
            for i in range(start_idx, end_idx):
                line = lines[i]
                if target_active:
                    lines[i] = re.sub(r"^([ \t]*)# ?", r"\1", line, count=1)
                elif line.strip():
                    lines[i] = f"# {line}"
                modified |= lines[i] != line

        if not modified:
            self.cache = pending_cache
            return True, "No modifications required.", ""

        # Refuse a transformation that would produce invalid active TOML.
        try:
            document = tomllib.loads("".join(lines))
            if "templates" not in document:
                # Matugen requires this table even when every block is disabled.
                lines.append("\n[templates]\n")
        except tomllib.TOMLDecodeError as error:
            return False, f"Template update would produce invalid TOML: {error}", ""

        # 4. Atomic file write
        target_dir = self.config_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            fd, tmp_path_str = tempfile.mkstemp(
                dir=str(target_dir),
                prefix=f".{self.config_path.name}.tmp-",
                suffix=".tmp"
            )
            tmp_path = Path(tmp_path_str)

            with os.fdopen(fd, "w", encoding="utf-8") as out_f:
                out_f.writelines(lines)
                out_f.flush()
                os.fsync(out_f.fileno())

            # Preserve permissions if target exists
            if self.config_path.exists():
                try:
                    mode = stat.S_IMODE(self.config_path.stat().st_mode)
                    tmp_path.chmod(mode)
                except OSError:
                    pass

            if self.file_mtime and self.config_path.stat().st_mtime_ns != self.file_mtime:
                raise OSError("Configuration changed during update; reload required")
            os.replace(tmp_path, self.config_path)

            self.cache = pending_cache
            # Update stored mtime
            self.file_mtime = self.config_path.stat().st_mtime_ns
            return True, f"Successfully updated {len(changes_dict)} template key(s).", ""

        except Exception as e:
            if 'tmp_path' in locals() and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            return False, f"Atomic write failed: {e}", ""

#!/usr/bin/env python3
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

class ShellFallbackEngine(BaseEngine):
    """
    Engine for bash/shell variable fallback definitions.
    Specifically parses lines like:
    readonly KEY="${KEY:-DEFAULT_VAL}"
    
    Guarantees:
    - Inline replacement of fallback value.
    - Sudo/Pkexec safe permissions inheritance.
    """
    
    # Matches: readonly KEY="${KEY:-VALUE}"
    _RE_FALLBACK = re.compile(
        r'^([ \t]*)readonly[ \t]+([A-Za-z_][A-Za-z0-9_]*)="\$\{\2:-'
        r'("(?:\\.|[^"\\])*"|(?:\\.|[^}\\])*)\}"(.*)$'
    )

    @staticmethod
    def _decode_default(value: str) -> str:
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
            return re.sub(r'\\([$`"\\])', r'\1', value)
        return re.sub(r'\\([$`"\\}])', r'\1', value)

    @staticmethod
    def _encode_string(value: str) -> str:
        # Quote the parameter's default word, including apostrophes and braces.
        escaped = re.sub(r'([$`"\\])', r'\\\1', value)
        return f'"{escaped}"'
    
    def __init__(self, config_path: str):
        self.config_path = Path(config_path).expanduser().resolve()
        self.cache: dict[str, Any] = {}
        self.file_mtime_ns: int = 0

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def load_state(self) -> dict[str, Any]:
        self.cache = {}
        if not self.config_path.exists():
            return {}
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                for line in f:
                    clean_line = line.rstrip('\r\n')
                    match = self._RE_FALLBACK.match(clean_line)
                    if match:
                        _, key, val, _ = match.groups()
                        self.cache[f"DEFAULT/{key}"] = self._decode_default(val)
                        
        except OSError as e:
            print(f"Failed to read file {self.config_path}: {e}")
            
        return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""

        if any(scope != "DEFAULT" for _, scope, _, _ in changes):
            return False, "Shell fallback settings require DEFAULT scope.", ""
        changes_dict = {key: (str(val), itype) for key, _, val, itype in changes}
        if any(any(char in val for char in "\r\n\0") for val, _ in changes_dict.values()):
            return False, "Shell fallback values must be single-line text without NUL characters.", ""
        out_lines = []
        applied_commits = set()
        
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    if os.fstat(f.fileno()).st_mtime_ns != self.file_mtime_ns:
                        return False, f"File {self.config_path.name} was modified externally. Reload required.", ""
                    lines = f.readlines()
            else:
                lines = []
        except OSError as e:
            return False, f"Failed to open config for reading: {e}", ""

        for line in lines:
            clean_line = line.rstrip('\r\n')
            match = self._RE_FALLBACK.match(clean_line)
            if match:
                ws, key, old_val, comment = match.groups()
                if key in changes_dict:
                    new_val, item_type = changes_dict[key]
                    # Ensure bool values write as true/false
                    if item_type == "bool":
                        new_val = "true" if str(new_val).lower() in ("true", "1", "yes", "on", "t", "y") else "false"
                    elif item_type == "string":
                        new_val = self._encode_string(new_val)
                    out_lines.append(f"{ws}readonly {key}=\"${{{key}:-{new_val}}}\"{comment}\n")
                    applied_commits.add(key)
                else:
                    out_lines.append(line)
            else:
                out_lines.append(line)

        missing = changes_dict.keys() - applied_commits
        if missing:
            return False, f"Settings not found: {', '.join(sorted(missing))}", ""

        # Atomic commit
        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', dir=self.config_path.parent) as tf:
                temp_file_path = Path(tf.name)
                tf.writelines(out_lines)
                tf.flush()
                os.fsync(tf.fileno())
                
            # Keep original permissions/ownership
            orig_stat = self.config_path.stat()
            if orig_stat.st_mtime_ns != self.file_mtime_ns:
                raise OSError(f"File {self.config_path.name} was modified externally. Reload required.")
            temp_file_path.chmod(orig_stat.st_mode)
            os.chown(temp_file_path, orig_stat.st_uid, orig_stat.st_gid)
                
            os.replace(temp_file_path, self.config_path)
            
            # Reload metadata timestamp
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.file_mtime_ns = os.fstat(f.fileno()).st_mtime_ns
                
            return True, "Successfully updated configurations.", ""
        except Exception as e:
            if temp_file_path and temp_file_path.exists():
                temp_file_path.unlink()
            return False, f"Failed during atomic commit: {e}", ""

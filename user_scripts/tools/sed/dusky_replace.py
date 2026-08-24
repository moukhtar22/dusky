#!/usr/bin/env python3
# ==============================================================================
# Script:     dusky-replace.py
# Purpose:    High-performance, byte-safe, TOCTOU-resistant text replacement
# Architect:  Optimized exclusively for Arch Linux (kernel 7.x) & Python 3.14+
# Python:     Requires Python 3.14+ (uses argparse color=True, modern union
#             types, match/case, and explicit O_NOFOLLOW semantics)
# ==============================================================================

from __future__ import annotations

import argparse
import difflib
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

# --- Dependency pre-flight (Safe Auto-Install) ---

CONSOLE_STDERR = None
CONSOLE_STDOUT = None

def _get_consoles():
    global CONSOLE_STDERR, CONSOLE_STDOUT
    if CONSOLE_STDERR is None or CONSOLE_STDOUT is None:
        try:
            from rich.console import Console
            CONSOLE_STDERR = Console(stderr=True)
            CONSOLE_STDOUT = Console()
        except Exception:
            class _Fallback:
                def print(self, *a, **kw): print(*a, file=sys.stderr)
            CONSOLE_STDERR = _Fallback()
            CONSOLE_STDOUT = _Fallback()
    return CONSOLE_STDOUT, CONSOLE_STDERR

def check_environment() -> None:
    if sys.version_info < (3, 14):
        sys.stderr.write(f"[!] Python 3.14+ required, found {sys.version}\n")
        sys.exit(2)

    missing: list[str] = []
    
    # Check Python modules
    for mod, pkg in (("rich", "python-rich"), ("regex", "python-regex"), ("charset_normalizer", "python-charset-normalizer")):
        if importlib.util.find_spec(mod) is None:
            missing.append(pkg)

    # Check System binaries
    if shutil.which("rg") is None:
        missing.append("ripgrep")
    if shutil.which("delta") is None:
        missing.append("git-delta")

    if missing:
        sys.stderr.write(f"[*] Missing dependencies detected: {', '.join(missing)}\n")
        sys.stderr.write("[*] Elevating via pacman for seamless installation...\n")
        sys.stderr.write("[*] (Using -S without -y to strictly prevent partial upgrades)\n")
        try:
            # -S without -y is safe on Arch: it installs from local cache without syncing.
            subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm"] + missing, check=True)
            sys.stderr.write("[✔] Dependencies installed. Reloading environment...\n\n")
            # Reload the script seamlessly now that dependencies exist
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except subprocess.CalledProcessError:
            sys.stderr.write("\n[✖] Auto-install failed (likely stale package cache).\n")
            sys.stderr.write("    Please safely synchronize and install manually:\n")
            sys.stderr.write(f"    sudo pacman -Syu --needed {' '.join(missing)}\n")
            sys.exit(2)

check_environment()

# Safe imports - now guaranteed to exist
import regex as re
import charset_normalizer
from rich.console import Console as RichConsole
from rich.markup import escape as markup_escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Prompt
from rich.syntax import Syntax

CONSOLE_STDOUT = RichConsole()
CONSOLE_STDERR = RichConsole(stderr=True)
console_out, console_err = CONSOLE_STDOUT, CONSOLE_STDERR

# Constants
MAX_DIFF_BYTES: Final[int] = 256 * 1024
MAX_FILE_SIZE: Final[int] = 100 * 1024 * 1024
BINARY_NUL_CHECK: Final[int] = 8192

# --- Helpers ---

def is_binary_quick(raw: bytes) -> bool:
    return b"\x00" in raw[:BINARY_NUL_CHECK]

def safe_read_bytes_no_follow(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise IsADirectoryError(f"Not a regular file: {path}")
        if st.st_size > MAX_FILE_SIZE:
            raise ValueError(f"File too large ({st.st_size} bytes)")
        with os.fdopen(fd, "rb", closefd=False) as f:
            return f.read()
    finally:
        os.close(fd)

def atomic_write_preserve_mode(path: Path, data: bytes) -> None:
    parent = path.parent
    try:
        parent_is_symlink = parent.is_symlink()
    except OSError:
        parent_is_symlink = False
    if parent_is_symlink:
        raise OSError(f"Refusing to write through symlinked parent: {parent}")

    parent.mkdir(parents=True, exist_ok=True)

    try:
        st = path.lstat()
        orig_mode = stat.S_IMODE(st.st_mode)
        if path.is_symlink():
            raise OSError(f"Refusing to overwrite symlink: {path}")
    except FileNotFoundError:
        orig_mode = 0o644
    except OSError as e:
        if "symlink" in str(e).lower():
            raise
        orig_mode = 0o644

    tmp_fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=f".{path.name}.tmp.")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.chmod(tmp_name, orig_mode)
        os.replace(tmp_name, path)
        try:
            dir_fd = os.open(parent, os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass

def detect_encoding_and_decode(raw: bytes) -> tuple[str, str]:
    try:
        return "utf-8", raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        m = charset_normalizer.from_bytes(raw).best()
        if m is not None:
            enc = m.encoding or "utf-8"
            return enc, str(m)
    except Exception:
        pass
    return "utf-8", raw.decode("utf-8", errors="surrogateescape")

def display_diff_limited(path: Path, old: str, new: str, base: Path | None = None) -> None:
    # Compute relative path for diff headers to avoid leaking absolute paths
    try:
        if base is not None:
            try:
                rel = path.relative_to(base)
            except ValueError:
                try:
                    rel = path.resolve().relative_to(base.resolve())
                except Exception:
                    rel = Path(str(path).lstrip("/"))
        else:
            rel = Path(str(path).lstrip("/")) if path.is_absolute() else path
    except Exception:
        rel = Path(path.name)

    # Guard large diffs
    if len(old) > MAX_DIFF_BYTES or len(new) > MAX_DIFF_BYTES:
        console_out.print(Panel(
            f"[yellow]Diff too large to render ({len(old)} -> {len(new)} chars). Showing truncated preview.[/]\n"
            f"File: {markup_escape(str(path))}",
            title=f"[bold blue]Diff: {markup_escape(str(rel))}[/]",
            border_style="yellow",
        ))
        old_lines = old.splitlines(keepends=True)[:200]
        new_lines = new.splitlines(keepends=True)[:200]
    else:
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
        n=3,
    )
    
    diff_str = "".join(diff)
    if not diff_str:
        return
        
    if len(diff_str) > MAX_DIFF_BYTES:
        diff_str = diff_str[:MAX_DIFF_BYTES] + "\n... [truncated] ...\n"

    # --- Side-by-Side Delta Integration ---
    if shutil.which("delta"):
        try:
            # Print our own header so it matches the script's visual flow
            console_out.print(f"\n[bold blue]╭── Diff: {markup_escape(str(rel))}[/]")
            
            # Pipe raw unified diff to delta, forcing side-by-side and disabling the pager
            subprocess.run(
                [
                    "delta", 
                    "--side-by-side", 
                    "--paging=never", 
                    "--file-style=omit", 
                    "--hunk-header-style=omit"
                ],
                input=diff_str.encode("utf-8", errors="replace"),
                check=False
            )
            return
        except Exception as e:
            console_err.print(f"[dim yellow][!] Failed to launch delta ({e}). Falling back to unified view.[/]")

    # --- Fallback: Native Rich Unified Diff ---
    syntax = Syntax(diff_str, "diff", theme="monokai", background_color="default", word_wrap=False)
    console_out.print(Panel(syntax, title=f"[bold blue]Diff: {markup_escape(str(rel))}[/]", border_style="blue"))

def build_rg_command(search: str, target: Path, multiline: bool, dotall: bool, allow_binary: bool = False) -> list[str]:
    # Respect .gitignore by default (rg default). Do not follow symlinks.
    cmd: list[str] = ["rg", "-l", "--null", "--engine=pcre2"]
    if allow_binary:
        # -a / --text disables binary detection (NUL = binary). Required so --allow-binary actually finds binaries.
        cmd.append("-a")
    if multiline:
        cmd.append("--multiline")
        if dotall:
            cmd.append("--multiline-dotall")
    cmd.extend(["-e", search, "--", str(target)])
    return cmd

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dusky Replace: High-performance, byte-safe, TOCTOU-resistant text replacement for Arch Linux.",
        epilog="Examples: dusky-replace 'foo.*bar' 'baz' ./src --multiline --dotall",
        color=True,
    )
    parser.add_argument("search", help="PCRE2 regex pattern to find (engine=pcre2).")
    parser.add_argument("replace", help="Replacement text (supports \\1, \\g<name> backreferences).")
    parser.add_argument("target_dir", type=Path, help="Directory to search within.")
    parser.add_argument("--multiline", action="store_true", help="Enable rg --multiline (allows \\n in pattern).")
    parser.add_argument("--dotall", action="store_true", help="Enables --multiline and --multiline-dotall; '.' matches newline (implies --multiline).")
    parser.add_argument("--allow-binary", action="store_true", help="Do not skip files containing NUL bytes; passes -a to ripgrep.")
    parser.add_argument("--dry-run", action="store_true", help="Preview diffs only, no writes.")
    parser.add_argument("-y", "--yes", action="store_true", help="Batch mode: apply to all without prompting.")
    parser.add_argument("--max-files", type=int, default=0, help="Abort if more than N files match (0 = no limit).")

    args = parser.parse_args()

    if args.dotall and not args.multiline:
        args.multiline = True

    target: Path = args.target_dir
    if not target.exists():
        console_err.print(f"[bold red][✖] Target '{markup_escape(str(target))}' does not exist.[/]")
        sys.exit(1)
    if not target.is_dir():
        console_err.print(f"[bold red][✖] Target '{markup_escape(str(target))}' is not a directory.[/]")
        sys.exit(1)

    target_resolved = target.resolve()

    try:
        flags = re.MULTILINE
        if args.dotall:
            flags |= re.DOTALL
        search_pattern = re.compile(args.search, flags=flags | re.V1)
    except re.error as e:
        console_err.print(f"[bold red][✖] Invalid regex pattern: {markup_escape(str(e))}[/]")
        sys.exit(1)

    rg_cmd = build_rg_command(args.search, target, args.multiline, args.dotall, args.allow_binary)
    console_out.print(f"[bold cyan]Searching with:[/] [dim]{' '.join(markup_escape(c) for c in rg_cmd)}[/]")

    try:
        proc = subprocess.run(
            rg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        console_err.print("[bold red][✖] ripgrep binary not found (shutil.which failed).[/]")
        sys.exit(1)

    match proc.returncode:
        case 0:
            pass
        case 1:
            console_out.print("[bold green][i] No matches found. Exiting cleanly.[/]")
            sys.exit(0)
        case 2:
            err_msg = proc.stderr.decode("utf-8", errors="replace")[:2000]
            if proc.stdout:
                console_err.print(f"[yellow][!] ripgrep warnings (continuing with {len(proc.stdout.split(b'\x00'))-1} files):[/] {markup_escape(err_msg)}")
            else:
                console_err.print(f"[bold red][✖] ripgrep error:[/] {markup_escape(err_msg)}")
                sys.exit(1)
        case _:
            err_msg = proc.stderr.decode("utf-8", errors="replace")[:2000]
            console_err.print(f"[bold red][✖] Unexpected ripgrep exit {proc.returncode}: {markup_escape(err_msg)}[/]")
            sys.exit(1)

    raw_paths = proc.stdout.split(b"\0")
    files: list[Path] = []
    for p in raw_paths:
        if not p:
            continue
        try:
            decoded = os.fsdecode(p)
        except Exception:
            decoded = p.decode("utf-8", errors="surrogateescape")
        files.append(Path(decoded))

    if args.max_files and len(files) > args.max_files:
        console_err.print(f"[bold red][✖] Too many matches ({len(files)} > {args.max_files}). Aborting.[/]")
        sys.exit(1)

    console_out.print(f"\n[bold]Found {len(files)} files containing matches.[/]")
    
    if not args.yes and not args.dry_run:
        console_out.print("  [bold cyan][1][/] Interactive (preview diffs and confirm per-file)")
        console_out.print("  [bold cyan][2][/] Batch All   (apply without per-file prompt)")
        console_out.print("  [bold cyan][3][/] Dry Run     (preview only)")
        console_out.print("  [bold red][q][/] Quit")
        choice = Prompt.ask("\nChoose execution method", choices=["1", "2", "3", "q"], default="1", case_sensitive=False, show_choices=False)
    elif args.yes:
        choice = "2"
    elif args.dry_run:
        choice = "3"
    else:
        choice = "1"

    if choice == "q":
        console_out.print("[yellow]Aborting as requested.[/]")
        sys.exit(0)

    mode_interactive = choice == "1"
    mode_batch = choice == "2"
    mode_dry = choice == "3"

    batch_override = mode_batch
    processed_count = 0
    skipped_count = 0
    failed_count = 0

    progress_ctx = None
    if mode_batch:
        progress_ctx = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console_out,
            transient=False,
        )

    def process_files(iterable):
        nonlocal processed_count, skipped_count, failed_count, batch_override
        for file_path in iterable:
            try:
                file_path.lstat()
                if file_path.is_symlink():
                    console_err.print(f"[yellow][!] Skipping symlink: {markup_escape(str(file_path))}[/]")
                    skipped_count += 1
                    continue
                if not file_path.is_file():
                    skipped_count += 1
                    continue
                try:
                    resolved = file_path.resolve()
                    if not resolved.is_relative_to(target_resolved):
                        console_err.print(f"[yellow][!] Skipping out-of-tree file (symlink escape?): {markup_escape(str(file_path))} -> {markup_escape(str(resolved))}[/]")
                        skipped_count += 1
                        continue
                except Exception:
                    skipped_count += 1
                    continue
            except FileNotFoundError:
                skipped_count += 1
                continue
            except OSError as e:
                console_err.print(f"[yellow][!] Lstat error on {markup_escape(str(file_path))}: {markup_escape(str(e))}[/]")
                skipped_count += 1
                continue

            try:
                raw_bytes = safe_read_bytes_no_follow(file_path)
            except OSError as e:
                console_err.print(f"[yellow][!] IO Error / symlink on {markup_escape(str(file_path))}. Skipping. ({markup_escape(str(e))})[/]")
                skipped_count += 1
                continue
            except ValueError as e:
                console_err.print(f"[yellow][!] Skipping large file {markup_escape(str(file_path))}: {markup_escape(str(e))}[/]")
                skipped_count += 1
                continue

            if not args.allow_binary and is_binary_quick(raw_bytes):
                console_out.print(f"[dim][i] Skipping binary file: {markup_escape(str(file_path))}[/]")
                skipped_count += 1
                continue

            detected_encoding, original_text = detect_encoding_and_decode(raw_bytes)

            try:
                new_text, sub_count = search_pattern.subn(args.replace, original_text)
            except Exception as e:
                console_err.print(f"[red][✖] Regex error on {markup_escape(str(file_path))}: {markup_escape(str(e))}[/]")
                failed_count += 1
                continue

            if sub_count == 0:
                skipped_count += 1
                console_out.print(f"[dim][i] No change in {markup_escape(str(file_path))} (detected {markup_escape(detected_encoding)}); skipping.[/]")
                continue

            if mode_dry or (mode_interactive and not batch_override):
                display_diff_limited(file_path, original_text, new_text, target_resolved)

            if mode_dry:
                continue

            if not batch_override:
                action = Prompt.ask(
                    f"Apply to [cyan]{markup_escape(str(file_path))}[/]? ([green]y[/]/[red]n[/]/[yellow]q[/]uit/[magenta]a[/]ll)",
                    choices=["y", "n", "q", "a"],
                    default="y",
                    case_sensitive=False,
                    show_choices=False,
                    console=console_out,
                )
                match action.lower():
                    case "q":
                        console_out.print("[yellow]Aborted by user.[/]")
                        break
                    case "n":
                        skipped_count += 1
                        continue
                    case "a":
                        batch_override = True
                    case _:
                        pass

            try:
                contains_surrogates = any(0xDC80 <= ord(ch) <= 0xDCFF for ch in original_text)
                if contains_surrogates:
                    output_bytes = new_text.encode(detected_encoding, errors="surrogateescape")
                else:
                    output_bytes = new_text.encode(detected_encoding, errors="strict")
            except UnicodeEncodeError as e:
                console_err.print(
                    f"[red][✖] Cannot encode {markup_escape(str(file_path))} as {markup_escape(detected_encoding)}: {markup_escape(str(e))}. Skipping.[/]"
                )
                failed_count += 1
                continue
            except Exception as e:
                console_err.print(f"[red][✖] Encode failed on {markup_escape(str(file_path))}: {markup_escape(str(e))}[/]")
                failed_count += 1
                continue

            try:
                atomic_write_preserve_mode(file_path, output_bytes)
                processed_count += 1
                if not mode_batch:
                    console_out.print(f"[green][✔] Updated {markup_escape(str(file_path))} ({markup_escape(detected_encoding)})[/]")
            except OSError as e:
                console_err.print(f"[bold red][✖] Write failed on {markup_escape(str(file_path))}: {markup_escape(str(e))}[/]")
                failed_count += 1
                continue

    try:
        if progress_ctx:
            with progress_ctx as progress:
                task = progress.add_task("Processing...", total=len(files))
                def progress_iter():
                    for f in files:
                        yield f
                        progress.advance(task)
                process_files(progress_iter())
        else:
            process_files(files)
    except KeyboardInterrupt:
        console_err.print("\n[bold red][!] Interrupted by user.[/]")
        sys.exit(130)

    console_out.print(f"\n[bold green]Done![/] Updated {processed_count} files, skipped {skipped_count}, failed {failed_count}.")

if __name__ == "__main__":
    main()

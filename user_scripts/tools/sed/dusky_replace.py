#!/usr/bin/env python3
"""Replace text using Python regex VERSION1; requires Python 3.14+, regex, rich and rg.

Ripgrep supplies ignore-aware file discovery, never regex filtering. Files without
an explicit encoding or Unicode BOM use UTF-8 with lossless surrogateescape.
Writes replace individual files atomically, preserving owner, mode and xattrs;
hard links retain their old inode. A batch is not a filesystem transaction.
"""

import argparse
import codecs
import difflib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice
from io import StringIO
from pathlib import Path
from typing import Final

MAX_DIFF_CHARS: Final = 256 * 1024
MAX_FILE_SIZE: Final = 100 * 1024 * 1024
UNICODE_BOMS: Final = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
    (codecs.BOM_UTF8, "utf-8"),
)
WIDE_BOM_PREFIXES: Final = tuple(bom for bom, codec in UNICODE_BOMS if codec != "utf-8")


def check_environment() -> None:
    if sys.version_info < (3, 14):
        raise SystemExit("Python 3.14+ is required.")
    # Installation belongs to ISO provisioning, not a text replacement command.
    try:
        import regex  # noqa: F401
        import rich  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            f"Missing dependency: {exc}. Install python-regex and python-rich."
        ) from exc
    if shutil.which("rg") is None:
        raise SystemExit("Missing dependency: ripgrep.")


# Imports remain side-effect-free: --help and module inspection never install packages.
try:
    import regex as re
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel
    from rich.progress import Progress
    from rich.prompt import Prompt
    from rich.syntax import Syntax
except ImportError:
    if __name__ == "__main__":
        check_environment()
    raise

console_out = Console()
console_err = Console(stderr=True)


def fingerprint(st: os.stat_result) -> tuple[int, ...]:
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


class SkippedFile(ValueError):
    """A candidate intentionally excluded from processing."""


def read_file(path: Path) -> tuple[bytes, os.stat_result]:
    # NONBLOCK prevents a replaced candidate that became a FIFO from hanging.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise SkippedFile("Not a regular file")
        if before.st_size > MAX_FILE_SIZE:
            raise SkippedFile(f"File exceeds {MAX_FILE_SIZE} bytes")
        raw = stream.read(MAX_FILE_SIZE + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > MAX_FILE_SIZE:
        raise SkippedFile(f"File exceeds {MAX_FILE_SIZE} bytes")
    if fingerprint(before) != fingerprint(after):
        raise OSError("File changed while being read")
    return raw, after


def decode_text(raw: bytes, encoding: str | None) -> tuple[str, str, bytes]:
    if encoding is not None:
        return raw.decode(encoding), encoding, b""
    # Check UTF-32 before UTF-16 because their little-endian BOMs overlap.
    for bom, codec in UNICODE_BOMS:
        if raw.startswith(bom):
            return raw[len(bom) :].decode(codec), codec, bom
    return raw.decode("utf-8", errors="surrogateescape"), "utf-8", b""


def text_parts(text: str, multiline: bool) -> Iterator[tuple[str, str]]:
    if multiline:
        yield text, ""
        return
    # LF is the search boundary. CR and every other byte remain untouched.
    start = 0
    while (end := text.find("\n", start)) != -1:
        yield text[start:end], "\n"
        start = end + 1
    if start < len(text) or not text:
        yield text[start:], ""


def substitute(
    pattern: re.Pattern, replacement: str, text: str, multiline: bool
) -> str:
    if multiline or "\n" not in text:
        return pattern.sub(replacement, text)
    with StringIO() as output:
        for part, ending in text_parts(text, False):
            output.write(pattern.sub(replacement, part) + ending)
        return output.getvalue()


def atomic_write(path: Path, data: bytes, expected: os.stat_result) -> None:
    # Check once before creating the temporary file and again immediately before
    # replace. This detects ordinary concurrent edits, not arbitrary path races.
    if fingerprint(path.lstat()) != fingerprint(expected):
        raise OSError("File changed since discovery; refusing to overwrite it")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".dusky-replace-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            current = os.fstat(stream.fileno())
            if (current.st_uid, current.st_gid) != (expected.st_uid, expected.st_gid):
                os.fchown(stream.fileno(), expected.st_uid, expected.st_gid)
            for attribute in os.listxattr(path, follow_symlinks=False):
                os.setxattr(
                    stream.fileno(),
                    attribute,
                    os.getxattr(path, attribute, follow_symlinks=False),
                )
            os.fchmod(stream.fileno(), stat.S_IMODE(expected.st_mode))
            os.fsync(stream.fileno())
        if fingerprint(path.lstat()) != fingerprint(expected):
            raise OSError("File changed during replacement; refusing to overwrite it")
        os.replace(name, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise OSError(
                f"Replacement completed, but directory sync failed: {exc}"
            ) from exc
    finally:
        Path(name).unlink(missing_ok=True)


def display_diff(path: Path, old: str, new: str, base: Path) -> None:
    relative = path.relative_to(base)
    truncated = max(len(old), len(new)) > MAX_DIFF_CHARS
    # Slice before splitting, so a huge single line or millions of lines cannot
    # turn a bounded preview into an unbounded allocation or expensive diff.
    old_lines = list(
        islice(
            old[:MAX_DIFF_CHARS].splitlines(keepends=True), 200 if truncated else None
        )
    )
    new_lines = list(
        islice(
            new[:MAX_DIFF_CHARS].splitlines(keepends=True), 200 if truncated else None
        )
    )
    diff_lines = difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{relative}", tofile=f"b/{relative}"
    )
    diff = "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in diff_lines
    )
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS]
        truncated = True
    console_out.print(f"\n[bold blue]Diff: {escape(str(relative))}[/]")
    if truncated:
        console_out.print(
            "[yellow]Preview truncated; changes outside the preview may be omitted.[/]"
        )
    if not diff:
        return
    # Show undecodable bytes consistently in both renderers.
    diff = diff.encode("utf-8", errors="backslashreplace").decode("utf-8")
    delta = shutil.which("delta")
    if delta is not None:
        try:
            result = subprocess.run(
                [
                    delta,
                    "--side-by-side",
                    "--paging=never",
                    "--file-style=omit",
                    "--hunk-header-style=omit",
                ],
                input=diff.encode("utf-8", errors="replace"),
                check=False,
            )
            if result.returncode == 0:
                return
        except OSError as exc:
            console_err.print(f"[yellow]Delta unavailable: {escape(str(exc))}[/]")
    console_out.print(Panel(Syntax(diff, "diff", word_wrap=False)))


@dataclass(frozen=True, slots=True)
class Candidate:
    path: Path
    snapshot: os.stat_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, color=True)
    parser.add_argument("search", help="Python regex VERSION1 pattern (not PCRE2).")
    parser.add_argument("replace", help=r"Replacement; supports \1 and \g<name>.")
    parser.add_argument("target_dir", type=Path)
    parser.add_argument(
        "--multiline",
        action="store_true",
        help="Allow matches across LF line boundaries.",
    )
    parser.add_argument(
        "--dotall",
        action="store_true",
        help="Make '.' match newline; implies --multiline.",
    )
    parser.add_argument(
        "--allow-binary",
        action="store_true",
        help="Allow NUL characters; preserve undecodable bytes.",
    )
    parser.add_argument(
        "--encoding",
        help="Explicit text encoding; default: Unicode BOM or lossless UTF-8.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only, including when --yes is set.",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Apply without prompts."
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Abort before writes if more than N files would change (0: unlimited).",
    )
    args = parser.parse_args()
    check_environment()
    if args.max_files < 0:
        parser.error("--max-files must be nonnegative")
    if args.encoding:
        try:
            args.encoding = codecs.lookup(args.encoding).name
            "x".encode(args.encoding).decode(args.encoding)
        except (LookupError, TypeError) as exc:
            parser.error(f"Invalid text encoding: {exc}")
    multiline = args.multiline or args.dotall
    try:
        target = args.target_dir.resolve(strict=True)
        if not target.is_dir():
            parser.error("Target must be a directory")
        pattern = re.compile(
            args.search, re.VERSION1 | re.MULTILINE | (re.DOTALL if args.dotall else 0)
        )
    except (OSError, RuntimeError, re.error) as exc:
        parser.error(str(exc))

    # Ignore user rg config: it can change paths, follow symlinks or output format.
    result = subprocess.run(
        ["rg", "--no-config", "--files", "--null", "--", str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        console_err.print(
            f"[red]Discovery failed: {escape(result.stderr.decode(errors='replace')[:2000])}[/]"
        )
        return 1
    candidates: list[Candidate] = []
    skipped = failed = updated = previewed = 0
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = Path(os.fsdecode(raw_path))
        try:
            raw, snapshot = read_file(path)
            # Skip ordinary binary data before decoding, while allowing wide
            # Unicode text whose encoding naturally contains zero bytes.
            if (
                not args.allow_binary
                and b"\0" in raw
                and not raw.startswith(WIDE_BOM_PREFIXES)
                and not (args.encoding or "").startswith(("utf-16", "utf-32"))
            ):
                skipped += 1
                continue
            text, encoding, bom = decode_text(raw, args.encoding)
            if not args.allow_binary and "\0" in text:
                skipped += 1
                continue
            if args.encoding and text.encode(encoding, errors="surrogateescape") != raw:
                raise ValueError("Encoding does not round-trip the original bytes")
            # Validate replacements and encoding before any writes begin.
            new = substitute(pattern, args.replace, text, multiline)
            if new == text:
                continue
            output = bom + new.encode(encoding, errors="surrogateescape")
            if output != raw:
                candidates.append(Candidate(path, snapshot))
                if args.max_files and len(candidates) > args.max_files:
                    console_err.print(
                        f"[red]More than {args.max_files} files would change; aborted before writes.[/]"
                    )
                    return 1
        except SkippedFile as exc:
            skipped += 1
            console_err.print(
                f"[yellow]Skipped {escape(str(path))}: {escape(str(exc))}[/]"
            )
        except (OSError, ValueError, re.error, IndexError, LookupError) as exc:
            console_err.print(
                f"[red]Preflight failed for {escape(str(path))}: {escape(str(exc))}[/]"
            )
            return 1
        finally:
            # Keep whole-file buffers from surviving into the next read.
            raw = output = b""
            text = new = ""
    if not candidates:
        console_out.print("No files would change.")
        return 0
    console_out.print(f"Found {len(candidates)} files to change.")
    if args.dry_run:
        mode = "3"
    elif args.yes:
        mode = "2"
    else:
        mode = Prompt.ask(
            "Mode: 1=interactive, 2=batch, 3=dry run, q=quit",
            choices=["1", "2", "3", "q"],
            default="1",
            console=console_out,
            case_sensitive=False,
        )
    if mode == "q":
        return 0
    batch = mode == "2"
    with Progress(console=console_out, disable=mode != "2") as progress:
        task = progress.add_task("Replacing", total=len(candidates))
        for candidate in candidates:
            try:
                raw, snapshot = read_file(candidate.path)
                if fingerprint(snapshot) != fingerprint(candidate.snapshot):
                    raise OSError("File changed since discovery")
                text, encoding, bom = decode_text(raw, args.encoding)
                new = substitute(pattern, args.replace, text, multiline)
                output = bom + new.encode(encoding, errors="surrogateescape")
                if mode == "3" or not batch:
                    display_diff(candidate.path, text, new, target)
                if mode == "3":
                    previewed += 1
                    continue
                if not batch:
                    action = Prompt.ask(
                        f"Apply to {escape(str(candidate.path))}?",
                        choices=["y", "n", "q", "a"],
                        default="y",
                        console=console_out,
                        case_sensitive=False,
                    )
                    if action == "q":
                        break
                    if action == "n":
                        skipped += 1
                        continue
                    batch = action == "a"
                atomic_write(candidate.path, output, snapshot)
                updated += 1
            except (OSError, ValueError, re.error, IndexError, LookupError) as exc:
                failed += 1
                console_err.print(
                    f"[red]Failed {escape(str(candidate.path))}: {escape(str(exc))}[/]"
                )
            finally:
                raw = output = b""
                text = new = ""
                progress.advance(task)
    console_out.print(
        f"Updated {updated}, previewed {previewed}, skipped {skipped}, failed {failed}."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        console_err.print("\nInterrupted.")
        raise SystemExit(130) from None
    except EOFError:
        console_err.print("No interactive input; use --yes or --dry-run.")
        raise SystemExit(1) from None
    except OSError as exc:
        console_err.print(f"[red]{escape(str(exc))}[/]")
        raise SystemExit(1) from None

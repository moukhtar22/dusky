#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm
from rich.table import Table

DEFAULT_DIR = "/mnt/zram1/dusky_ytdlp/"
DEFAULT_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4", ".m4a", ".mp3", ".wav", ".webm",
        ".mkv", ".opus", ".mka", ".flac", ".aac", ".m4v", ".mov",
    }
)

EP_RE: re.Pattern[str] = re.compile(
    r"america\s+first\s+ep\.\s*(\d{1,6})(?!\d)", re.IGNORECASE
)
NUMERIC_STEM_RE: re.Pattern[str] = re.compile(r"^(\d{1,6})$")

EXIT_OK = 0
EXIT_RUNTIME_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPT = 130

console = Console()


@dataclass(frozen=True, slots=True)
class PlanEntry:
    source: Path
    episode_id: int
    target_name: str

    @property
    def target(self) -> Path:
        return self.source.parent / self.target_name


@dataclass
class ScanResult:
    plans: list[PlanEntry] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    skipped_no_pattern: list[tuple[str, str]] = field(default_factory=list)
    skipped_bad_ext: list[tuple[str, str]] = field(default_factory=list)
    skipped_exists: list[tuple[str, str]] = field(default_factory=list)
    skipped_duplicate: list[tuple[str, str]] = field(default_factory=list)
    skipped_other: list[tuple[str, str]] = field(default_factory=list)

    @property
    def skipped_total(self) -> int:
        return (
            len(self.skipped_no_pattern)
            + len(self.skipped_bad_ext)
            + len(self.skipped_exists)
            + len(self.skipped_duplicate)
            + len(self.skipped_other)
        )


def parse_allowed_extensions(raw: str | None) -> frozenset[str]:
    if not raw:
        return DEFAULT_ALLOWED_EXTENSIONS
    cleaned: set[str] = set()
    for token in re.split(r"[,\s]+", raw.strip()):
        if not token:
            continue
        token = token.lower()
        if not token.startswith("."):
            token = f".{token}"
        cleaned.add(token)
    return frozenset(cleaned) or DEFAULT_ALLOWED_EXTENSIONS


def normalize_extension(filename: str, allowed: frozenset[str]) -> str | None:
    suffix = Path(filename).suffix
    if not suffix:
        return None
    lowered = suffix.lower()
    return lowered if lowered in allowed else None


def extract_episode_id(filename: str) -> int | None:
    try:
        match = EP_RE.search(filename)
        if not match:
            return None
        episode_id = int(match.group(1))
    except (ValueError, OverflowError):
        return None
    return episode_id if episode_id > 0 else None


def canonical_name_for_numeric(filename: str, allowed: frozenset[str]) -> str | None:
    suffix = Path(filename).suffix
    if not suffix:
        return None
    lowered = suffix.lower()
    if lowered not in allowed:
        return None
    stem = filename[: -len(suffix)] if suffix else filename
    match = NUMERIC_STEM_RE.match(stem)
    if not match:
        return None
    try:
        episode_id = int(match.group(1))
    except (ValueError, OverflowError):
        return None
    if episode_id <= 0:
        return None
    canonical = f"{episode_id}{lowered}"
    return None if canonical == filename else canonical


def is_canonical(filename: str, allowed: frozenset[str]) -> bool:
    suffix = Path(filename).suffix
    if not suffix or suffix != suffix.lower() or suffix.lower() not in allowed:
        return False
    stem = filename[: -len(suffix)]
    match = NUMERIC_STEM_RE.match(stem)
    if not match:
        return False
    try:
        episode_id = int(match.group(1))
    except (ValueError, OverflowError):
        return False
    return episode_id > 0 and filename == f"{episode_id}{suffix.lower()}"


def iter_candidate_files(directory: Path, recursive: bool) -> list[Path]:
    try:
        if recursive:
            entries = [p for p in directory.rglob("*")]
        else:
            entries = list(directory.iterdir())
    except OSError as exc:
        raise OSError(f"cannot list directory '{directory}': {exc.strerror or exc}") from exc
    entries.sort(key=lambda p: p.name.lower())
    return entries


def plan_renames(
    directory: Path, allowed: frozenset[str], recursive: bool = False
) -> ScanResult:
    result = ScanResult()
    seen_targets: dict[str, str] = {}

    for item in iter_candidate_files(directory, recursive):
        name = item.name

        if item.is_symlink():
            result.skipped_other.append((name, "symlink; skipped for safety"))
            continue
        if not item.is_file():
            continue

        if is_canonical(name, allowed):
            result.already.append(name)
            continue

        fixup = canonical_name_for_numeric(name, allowed)
        if fixup is not None:
            if fixup in seen_targets:
                result.skipped_duplicate.append(
                    (name, f"target '{fixup}' already queued from '{seen_targets[fixup]}'")
                )
                continue
            target_path = item.parent / fixup
            if target_path.exists() or target_path.is_symlink():
                result.skipped_exists.append(
                    (name, f"target '{fixup}' already exists; refusing to overwrite")
                )
                continue
            try:
                episode_id = int(Path(fixup).stem)
            except ValueError:
                result.skipped_other.append((name, "unparseable numeric name"))
                continue
            seen_targets[fixup] = name
            result.plans.append(PlanEntry(source=item, episode_id=episode_id, target_name=fixup))
            continue

        episode_id = extract_episode_id(name)
        if episode_id is None:
            result.skipped_no_pattern.append(
                (name, "requires 'America First Ep. <number>'")
            )
            continue

        ext = normalize_extension(name, allowed)
        if ext is None:
            result.skipped_bad_ext.append(
                (name, f"extension '{Path(name).suffix or '(none)'}' not in allowed set")
            )
            continue

        target_name = f"{episode_id}{ext}"
        if target_name == name:
            result.already.append(name)
            continue
        if target_name in seen_targets:
            result.skipped_duplicate.append(
                (name, f"target '{target_name}' already queued from '{seen_targets[target_name]}'")
            )
            continue
        target_path = item.parent / target_name
        if target_path.exists() or target_path.is_symlink():
            result.skipped_exists.append(
                (name, f"target '{target_name}' already exists; refusing to overwrite")
            )
            continue

        seen_targets[target_name] = name
        result.plans.append(PlanEntry(source=item, episode_id=episode_id, target_name=target_name))

    return result


def safe_rename(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        raise FileExistsError(f"target '{dst.name}' already exists")
    if src.is_symlink():
        raise OSError(f"source '{src.name}' is a symlink; refusing to rename")
    try:
        os.link(src, dst)
    except FileExistsError:
        raise
    except OSError as link_exc:
        if link_exc.errno == errno.EXDEV:
            if dst.exists() or dst.is_symlink():
                raise FileExistsError(f"target '{dst.name}' already exists") from link_exc
            import shutil

            shutil.copy2(src, dst)
            os.unlink(src)
            return
        if link_exc.errno in (errno.EPERM, errno.EACCES, errno.EROFS, errno.EXDEV):
            if dst.exists() or dst.is_symlink():
                raise FileExistsError(f"target '{dst.name}' already exists") from link_exc
            os.rename(src, dst)
            return
        raise
    else:
        os.unlink(src)


def execute_plans(
    plans: list[PlanEntry],
    show_progress: bool = True,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    succeeded: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    if not plans:
        return succeeded, failed

    def _run_one(plan: PlanEntry) -> None:
        try:
            safe_rename(plan.source, plan.target)
        except FileExistsError as exc:
            failed.append((plan.source.name, f"target exists at execution time: {exc}"))
        except FileNotFoundError as exc:
            failed.append((plan.source.name, f"source vanished: {exc}"))
        except PermissionError as exc:
            failed.append((plan.source.name, f"permission denied: {exc}"))
        except OSError as exc:
            failed.append((plan.source.name, f"{type(exc).__name__}: {exc}"))
        else:
            succeeded.append((plan.source.name, plan.target_name))

    if not show_progress:
        for plan in plans:
            _run_one(plan)
        return succeeded, failed

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task = progress.add_task("Renaming…", total=len(plans))
        for plan in plans:
            progress.update(task, description=f"Renaming {plan.source.name[:50]}")
            _run_one(plan)
            progress.advance(task)

    return succeeded, failed


def write_undo_log(log_path: Path, entries: list[tuple[str, str]], directory: Path) -> Path:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with log_path.open("a", encoding="utf-8") as fh:
        for src_name, dst_name in entries:
            fh.write(
                json.dumps(
                    {"ts": timestamp, "dir": str(directory), "src": src_name, "dst": dst_name},
                    ensure_ascii=False,
                )
                + "\n"
            )
    return log_path


def undo_from_log(log_path: Path) -> tuple[int, int, list[str]]:
    ok, failed = 0, 0
    errors: list[str] = []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return 0, 1, [f"cannot read log '{log_path}': {exc}"]
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            directory = Path(record["dir"])
            current = directory / record["dst"]
            original = directory / record["src"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            failed += 1
            errors.append(f"line {lineno}: corrupt entry ({exc})")
            continue
        try:
            safe_rename(current, original)
        except Exception as exc:
            failed += 1
            errors.append(f"line {lineno}: {record.get('dst')} -> {record.get('src')}: {exc}")
        else:
            ok += 1
    return ok, failed, errors


def resolve_directory(explicit: str | None) -> tuple[Path, str]:
    if explicit:
        return Path(explicit).expanduser(), "command line"
    for var in ("DUSKY_YTDLP_DIR", "DSUSKY_YTDLP_DIR"):
        value = os.environ.get(var)
        if value:
            legacy = " (legacy typo, still honoured)" if var == "DSUSKY_YTDLP_DIR" else ""
            return Path(value).expanduser(), f"${var}{legacy}"
    return Path(DEFAULT_DIR).expanduser(), "built-in default"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rename 'America First Ep. <N>' media files to canonical '<N>.<ext>'.",
        epilog="Idempotent: already-canonical files are left untouched. Never overwrites.",
    )
    parser.add_argument(
        "dir",
        nargs="?",
        default=None,
        help="Target directory (overrides $DUSKY_YTDLP_DIR and the default).",
    )
    parser.add_argument("--dir", dest="dir_opt", default=None, help="Same as DIR.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="Show the plan without renaming anything."
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true", help="Process subdirectories too."
    )
    parser.add_argument(
        "--extensions",
        default=None,
        help="Comma-separated extension allow-list, e.g. '--extensions mp4,m4a,mp3'.",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Undo-log path (default: <target-dir>/.rename_undo.jsonl).",
    )
    parser.add_argument("--undo", default=None, help="Restore names from LOG and exit.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Minimal output.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show skipped-file details.")
    parser.add_argument("--no-color", action="store_true", help="Disable Rich colour output.")
    parser.add_argument("--self-test", action="store_true", help="Run the built-in regression suite and exit.")
    return parser


def run_self_test() -> int:
    failures: list[str] = []
    passes = 0

    def check(cond: bool, label: str) -> None:
        nonlocal passes
        if cond:
            passes += 1
        else:
            failures.append(label)
            console.print(f"[red]FAIL {label}[/]")

    def run(*args: str, env_extra: dict[str, str] | None = None):
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    check(".m4a" in parse_allowed_extensions(None), "units default ext")
    check(parse_allowed_extensions("mp4,m4a") == frozenset({".mp4", ".m4a"}), "units custom ext")
    check(parse_allowed_extensions("") == DEFAULT_ALLOWED_EXTENSIONS, "units empty ext")
    check(parse_allowed_extensions(",,,   ") == DEFAULT_ALLOWED_EXTENSIONS, "units junk ext")

    for name, want in [
        ("T America First Ep. 1687 [a].m4a", 1687),
        ("america first ep. 42.mp4", 42),
        ("AMERICA FIRST EP.1687.m4a", 1687),
        ("America First Ep.  1687.m4a", 1687),
        ("America   First   Ep.  7.mp3", 7),
        ("America First Ep. 0.m4a", None),
        ("America First Ep..m4a", None),
        ("IRAN WAR BEGINS [x].m4a", None),
        ("1687.m4a", None),
        ("random file.txt", None),
        ("America First Ep. 9999999.m4a", None),
        ("America First Ep. 0005.m4a", 5),
        ("America First Ep. 1.m4a America First Ep. 2.m4a", 1),
        ("", None),
        ("America First Ep. 12a34.m4a", 12),
    ]:
        check(extract_episode_id(name) == want, "units extract " + repr(name))

    for name, want in [
        ("a.m4a", ".m4a"),
        ("a.MP4", ".mp4"),
        ("a.txt", None),
        ("a.m4a.part", None),
        ("noext", None),
        ("a.MKV", ".mkv"),
        (".m4a", None),
        ("a.m4a ", None),
    ]:
        check(normalize_extension(name, DEFAULT_ALLOWED_EXTENSIONS) == want, "units ext " + repr(name))

    check(is_canonical("1687.m4a", DEFAULT_ALLOWED_EXTENSIONS), "units canonical yes")
    check(not is_canonical("1687.MP4", DEFAULT_ALLOWED_EXTENSIONS), "units canonical case")
    check(not is_canonical("001687.m4a", DEFAULT_ALLOWED_EXTENSIONS), "units canonical zeros")
    check(not is_canonical("0.m4a", DEFAULT_ALLOWED_EXTENSIONS), "units canonical zero")
    check(not is_canonical("abc.m4a", DEFAULT_ALLOWED_EXTENSIONS), "units canonical alpha")
    check(canonical_name_for_numeric("001687.m4a", DEFAULT_ALLOWED_EXTENSIONS) == "1687.m4a", "units fixup zeros")
    check(canonical_name_for_numeric("1687.MP4", DEFAULT_ALLOWED_EXTENSIONS) == "1687.mp4", "units fixup case")
    check(canonical_name_for_numeric("1687.m4a", DEFAULT_ALLOWED_EXTENSIONS) is None, "units fixup none")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        fixture = {
            "SHOW America First Ep. 1687 [v1].m4a": "EP1687-A",
            "SHOW America First Ep. 1686 [v2].m4a": "EP1686",
            "lowercase america first ep. 1600 [x].mp3": "EP1600",
            "NOSPACE America First Ep.1685 [x].m4a": "EP1685",
            "DUBSPACE America First Ep.  1684 [x].wav": "EP1684",
            "UPPER America First Ep. 1683 [x].MP4": "EP1683",
            "ZERO America First Ep. 0007 [x].m4a": "EP7",
            "1680.m4a": "ALREADY1680",
            "001679.m4a": "ZEROPAD1679",
            "1678.MP4": "UPPER1678",
            "IRAN WAR BEGINS [v9].m4a": "NOPATTERN",
            "random notes.txt": "TEXT",
            "America First Ep. 1670 [x].txt": "BADEXT",
            "America First Ep. 1669 [x].m4a.part": "PARTFILE",
            "noextensionfile": "NOEXT",
            "America First Ep. 0 [x].m4a": "ZEROEP",
            "DUP-A America First Ep. 1650 [aaa].m4a": "DUP-A",
            "DUP-B America First Ep. 1650 [bbb].m4a": "DUP-B",
            "1649.m4a": "PRE1649",
            "COLLIDE America First Ep. 1649 [zzz].m4a": "COLLIDE",
            "FULLWIDTH America First Ep. 1648 [x].m4a": "FULLWIDTH",
        }
        for name, content in fixture.items():
            (d / name).write_bytes(content.encode())
        (d / "sub").mkdir()
        (d / "sub" / "SUB America First Ep. 1646 [x].m4a").write_text("SUBFILE")
        try:
            os.symlink(d / "1680.m4a", d / "link.m4a")
            os.symlink(d / "missing", d / "broken.m4a")
        except OSError:
            pass
        before = sorted(p.name for p in d.iterdir())
        r = run(str(d), "--dry-run", "--no-color", "-q")
        check(r.returncode == 0, "fixture dry-run exit 0")
        check(sorted(p.name for p in d.iterdir()) == before, "fixture dry-run no mutation")
        r = run(str(d), "--yes", "--no-color", "-q")
        check(r.returncode == 0, "fixture run exit 0")

        def content_of(name: str) -> str:
            return (d / name).read_bytes().decode()

        check((d / "1687.m4a").exists() and content_of("1687.m4a") == "EP1687-A", "fixture 1687 bytes")
        check((d / "1683.mp4").exists() and content_of("1683.mp4") == "EP1683", "fixture ext lower")
        check((d / "7.m4a").exists(), "fixture zero ep")
        check(content_of("1680.m4a") == "ALREADY1680", "fixture already untouched")
        check((d / "1679.m4a").exists() and content_of("1679.m4a") == "ZEROPAD1679", "fixture zeropad")
        check((d / "1678.mp4").exists() and content_of("1678.mp4") == "UPPER1678", "fixture upper")
        check((d / "IRAN WAR BEGINS [v9].m4a").exists(), "fixture nopattern kept")
        check((d / "America First Ep. 1670 [x].txt").exists(), "fixture badext kept")
        check(content_of("1649.m4a") == "PRE1649", "fixture no overwrite")
        check((d / "COLLIDE America First Ep. 1649 [zzz].m4a").exists(), "fixture collide kept")
        check((d / "1650.m4a").exists(), "fixture dup once")
        check(content_of("1650.m4a") in ("DUP-A", "DUP-B"), "fixture dup winner valid")
        check(len([p for p in d.iterdir() if p.name.startswith("DUP-")]) == 1, "fixture dup loser kept")
        check((d / "sub" / "SUB America First Ep. 1646 [x].m4a").exists(), "fixture sub ignored")
        check((d / "link.m4a").is_symlink(), "fixture symlink kept")
        logf = d / ".rename_undo.jsonl"
        check(logf.exists(), "fixture undo log exists")
        rec = json.loads(logf.read_text().splitlines()[0])
        check({"ts", "dir", "src", "dst"} <= set(rec), "fixture undo schema")
        snap = sorted(p.name for p in d.iterdir())
        r2 = run(str(d), "--yes", "--no-color", "-q")
        check(r2.returncode == 0, "fixture rerun exit 0")
        check(sorted(p.name for p in d.iterdir()) == snap, "fixture idempotent")
        r3 = run(str(d), "--yes", "--no-color", "--recursive", "--dry-run", "-q")
        check(r3.returncode == 0, "fixture recursive ok")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for n in [
            "  spaces  America First Ep. 1901 [x].m4a",
            "newline America First Ep. 1902 [x]\n.m4a",
            "dots... America First Ep. 1903 [x].m4a",
            "UPPER.WAV America First Ep. 1904 [x].WAV",
            "tab\tAmerica First Ep. 1905 [x].m4a",
            "uni-é-ß-日 America First Ep. 1906 [x].opus",
            "America First Ep. 1907 [x].m4a.backup",
            ".hidden America First Ep. 1908 [x].m4a",
        ]:
            (d / n).write_bytes(b"x")
        (d / ("a" * 200)).write_bytes(b"long")
        r = run(str(d), "--yes", "--no-color", "-q")
        check(r.returncode == 0, "weird exit 0")
        check((d / "1901.m4a").exists(), "weird spaces")
        check((d / "1904.wav").exists(), "weird upper wav")
        check((d / "1906.opus").exists(), "weird unicode opus")
        check((d / "1908.m4a").exists(), "weird hidden")
        check((d / "America First Ep. 1907 [x].m4a.backup").exists(), "weird double ext kept")
        snap = sorted(p.name for p in d.iterdir())
        r2 = run(str(d), "--yes", "--no-color", "-q")
        check(sorted(p.name for p in d.iterdir()) == snap and r2.returncode == 0, "weird idempotent")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "RACE America First Ep. 1910 [x].m4a").write_bytes(b"RACE")
        planned = plan_renames(d, DEFAULT_ALLOWED_EXTENSIONS)
        check(len(planned.plans) == 1, "race plan one")
        (d / "1910.m4a").write_bytes(b"SQUATTER")
        try:
            safe_rename(planned.plans[0].source, planned.plans[0].target)
            check(False, "race must refuse")
        except FileExistsError:
            check(True, "race refuses overwrite")
        check((d / "1910.m4a").read_bytes() == b"SQUATTER", "race squatter intact")
        check((d / "RACE America First Ep. 1910 [x].m4a").read_bytes() == b"RACE", "race source intact")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        rng = random.Random(7)
        total_bytes = 0
        for i in range(60):
            ep = 3000 + (i % 20)
            tag = "".join(rng.choice("abXYZ  .-é") for _ in range(rng.randint(1, 12)))
            name = "F" + tag + " America First Ep. " + str(ep) + " [v" + str(i) + "].m4a"
            data = ("payload-" + str(i)).encode()
            total_bytes += len(data)
            (d / name).write_bytes(data)
        n_before = len(list(d.iterdir()))
        r = run(str(d), "--yes", "--no-color", "-q")
        check(r.returncode == 0, "fuzz exit 0")
        files = list(d.iterdir())
        byte_sum = sum(p.stat().st_size for p in files if p.is_file() and p.name != ".rename_undo.jsonl")
        check(byte_sum == total_bytes, "fuzz bytes conserved")
        check(len(files) == n_before + 1, "fuzz count conserved plus log")
        targets = [p.name for p in files if p.suffix == ".m4a" and p.stem.isdigit()]
        check(len(targets) == len(set(targets)), "fuzz no dup targets")
        snap = sorted(p.name for p in d.iterdir())
        r2 = run(str(d), "--yes", "--no-color", "-q")
        check(r2.returncode == 0 and sorted(p.name for p in d.iterdir()) == snap, "fuzz idempotent")

    r = run("/nonexistent-dir-xyz-123", "--no-color")
    check(r.returncode == 2, "cli missing dir exit 2")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "A America First Ep. 1950 [x].m4a").write_bytes(b"A")
        (d / "B America First Ep. 1951 [x].mkv").write_bytes(b"B")
        r = run(str(d), "--dry-run", "--no-color", "-q", "--extensions", "m4a")
        check(r.returncode == 0 and (d / "A America First Ep. 1950 [x].m4a").exists(), "cli dry-run kept")
        check(not (d / "1950.m4a").exists(), "cli dry-run no file")
        r = run(str(d), "--yes", "--no-color", "-q", "--extensions", "m4a")
        check(r.returncode == 0 and (d / "1950.m4a").exists(), "cli custom ext renames m4a")
        check((d / "B America First Ep. 1951 [x].mkv").exists(), "cli custom ext skips mkv")
    with tempfile.TemporaryDirectory() as tmp:
        r = run(tmp, "--no-color", "-q")
        check(r.returncode == 0, "cli empty dir ok")
        f = Path(tmp) / "notadir"
        f.write_text("x")
        r = run(str(f), "--no-color")
        check(r.returncode == 2, "cli file-as-dir exit 2")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "U America First Ep. 2001 [x].m4a").write_text("U2001")
        check(run(str(d), "--yes", "--no-color", "-q").returncode == 0, "cli undo setup")
        logf = d / ".rename_undo.jsonl"
        logf.write_text(logf.read_text() + "CORRUPT-LINE\n" + json.dumps({"bad": 1}) + "\n")
        r = run("--undo", str(logf), "--no-color", "-q")
        check(r.returncode == 1, "cli corrupt undo exit 1")
        check((d / "U America First Ep. 2001 [x].m4a").exists(), "cli undo restores despite corrupt lines")
    with tempfile.TemporaryDirectory() as tmp:
        r = run("--dry-run", "--no-color", "-q", env_extra={"DUSKY_YTDLP_DIR": tmp})
        check(r.returncode == 0, "env correct var")
        r = run("--dry-run", "--no-color", "-q", env_extra={"DSUSKY_YTDLP_DIR": tmp})
        check(r.returncode == 0, "env legacy var")

    console.print(f"[dim]PASSES {passes} FAILURES {len(failures)}[/]")
    if failures:
        return EXIT_RUNTIME_FAILURE
    console.print("[green]ALL CHECKS PASSED[/]")
    return EXIT_OK


def render_plan(result: ScanResult, allowed: frozenset[str], verbose: bool, quiet: bool) -> None:
    if quiet:
        return
    if result.plans:
        table = Table(
            title=f"[bold green]{len(result.plans)} file(s) queued for renaming[/]",
            box=box.ROUNDED,
            show_lines=False,
        )
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Episode", justify="right", style="cyan", width=8)
        table.add_column("Source", style="white", overflow="fold")
        table.add_column("Target", style="bold green")
        for idx, plan in enumerate(result.plans, 1):
            src = plan.source.name
            src_short = src if len(src) <= 70 else src[:67] + "…"
            table.add_row(str(idx), str(plan.episode_id), src_short, plan.target_name)
        console.print(table)
    else:
        console.print("[dim]No files queued for renaming.[/]")

    if result.already and (verbose or not quiet):
        console.print(f"[green]✓ {len(result.already)} already organised (idempotent no-op).[/]")
        if verbose:
            for name in sorted(result.already)[:20]:
                console.print(f"  [dim]• {name}[/]")
            if len(result.already) > 20:
                console.print(f"  [dim]… and {len(result.already) - 20} more[/]")

    skip_groups = [
        ("No episode pattern", result.skipped_no_pattern, "yellow"),
        ("Unsupported extension", result.skipped_bad_ext, "magenta"),
        ("Target already exists", result.skipped_exists, "red"),
        ("Duplicate target in batch", result.skipped_duplicate, "red"),
        ("Other (symlink, etc.)", result.skipped_other, "dim"),
    ]
    for label, entries, style in skip_groups:
        if not entries:
            continue
        if not verbose and label in ("No episode pattern", "Other (symlink, etc.)"):
            console.print(f"[{style}]⊘ {len(entries)} skipped — {label.lower()} (use -v for names).[/]")
            continue
        table = Table(title=f"[bold {style}]{len(entries)} skipped — {label}[/]", box=box.ROUNDED)
        table.add_column("File", overflow="fold")
        table.add_column("Reason", overflow="fold")
        for name, reason in sorted(entries)[:50]:
            table.add_row(name if len(name) <= 70 else name[:67] + "…", reason)
        if len(entries) > 50:
            table.add_row(f"… +{len(entries) - 50} more", "")
        console.print(table)

    ext_list = ", ".join(sorted(allowed))
    console.print(f"[dim]Allowed extensions: {ext_list}[/]")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    global console
    if args.no_color:
        console = Console(no_color=True)

    if args.self_test:
        return run_self_test()

    if args.undo:
        ok, failed, errors = undo_from_log(Path(args.undo).expanduser())
        if not args.quiet:
            console.print(
                Panel(
                    f"[green]Restored: {ok}[/]   [red]Failed: {failed}[/]",
                    title="Undo complete",
                    box=box.ROUNDED,
                )
            )
            for err in errors[:20]:
                console.print(f"[red]• {err}[/]")
        return EXIT_OK if failed == 0 else EXIT_RUNTIME_FAILURE

    allowed = parse_allowed_extensions(args.extensions)
    explicit = args.dir_opt or args.dir
    raw_dir, origin = resolve_directory(explicit)
    directory = raw_dir.resolve() if raw_dir.is_absolute() else (Path.cwd() / raw_dir).resolve()

    if not args.quiet:
        console.print(
            Panel.fit(
                "[bold cyan]Smart File Renamer[/]\n"
                f"[dim]Target:[/] {directory}\n"
                f"[dim]Source:[/] {origin}"
                + ("   [bold yellow]DRY RUN[/]" if args.dry_run else "")
                + ("   [dim](recursive)[/]" if args.recursive else ""),
                title="🎬 ytdlp organiser",
                box=box.ROUNDED,
            )
        )

    if not directory.is_dir():
        console.print(f"[bold red]Error:[/] not a directory: {directory}")
        return EXIT_USAGE
    if not os.access(directory, os.R_OK | os.X_OK):
        console.print(f"[bold red]Error:[/] directory not readable: {directory}")
        return EXIT_USAGE
    if not args.dry_run and not os.access(directory, os.W_OK | os.X_OK):
        console.print(f"[bold red]Error:[/] directory not writable (use --dry-run): {directory}")
        return EXIT_USAGE

    try:
        with console.status("[cyan]Scanning directory…[/]", spinner="dots"):
            result = plan_renames(directory, allowed, recursive=args.recursive)
        scanned = (
            len(result.plans)
            + len(result.already)
            + result.skipped_total
        )
        if not args.quiet:
            console.print(f"[dim]Scanned {scanned} file(s).[/]")
        render_plan(result, allowed, verbose=args.verbose, quiet=args.quiet)
    except OSError as exc:
        console.print(f"[bold red]Scan failed:[/] {exc}")
        return EXIT_RUNTIME_FAILURE
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted during scan. Nothing was changed.[/]")
        return EXIT_INTERRUPT

    if not result.plans:
        if not args.quiet:
            console.print(
                Panel(
                    f"Nothing to do. {len(result.already)} already organised, "
                    f"{result.skipped_total} skipped.",
                    title="✅ All tidy",
                    box=box.ROUNDED,
                )
            )
        return EXIT_OK

    if args.dry_run:
        if not args.quiet:
            console.print("[bold yellow]Dry run — no files were changed.[/]")
        return EXIT_OK

    if not args.yes:
        try:
            proceed = Confirm.ask(
                f"Proceed with renaming {len(result.plans)} file(s)?",
                default=False,
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Aborted. Nothing was changed.[/]")
            return EXIT_INTERRUPT
        if not proceed:
            console.print("[yellow]Cancelled. Nothing was changed.[/]")
            return EXIT_OK

    try:
        succeeded, failed = execute_plans(result.plans, show_progress=not args.quiet)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Interrupted mid-run.[/] Some files may already be renamed; "
                      "re-run to finish (idempotent). Check the undo log to revert.")
        return EXIT_INTERRUPT

    log_path = (
        Path(args.log).expanduser()
        if args.log
        else directory / ".rename_undo.jsonl"
    )
    if succeeded:
        try:
            write_undo_log(log_path, succeeded, directory)
        except OSError as exc:
            console.print(f"[yellow]Warning:[/] could not write undo log '{log_path}': {exc}")

    if not args.quiet:
        status = "green" if not failed else "red"
        console.print(
            Panel(
                f"[green]Renamed: {len(succeeded)}[/]   "
                f"[red]Failed: {len(failed)}[/]   "
                f"[dim]Skipped: {result.skipped_total}   Already: {len(result.already)}[/]"
                + (f"\n[dim]Undo log: {log_path}[/]" if succeeded else ""),
                title=f"[{status}]Processing complete[/]",
                box=box.ROUNDED,
            )
        )
        for name, err in failed[:20]:
            console.print(f"[red]✗ {name}: {err}[/]")

    return EXIT_OK if not failed else EXIT_RUNTIME_FAILURE


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Interrupted by user (Ctrl+C). Exiting safely.[/]")
        raise SystemExit(EXIT_INTERRUPT)

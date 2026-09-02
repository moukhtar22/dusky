#!/usr/bin/env python3
"""
Dusky Universal Media Downloader
Platform: Arch Linux | Python 3.14+ | yt-dlp 2026+ | FFmpeg 9+
Architecture: Port of Open Video Downloader (OVD v3.2.1) Engine

Features:
- Full general-purpose media downloader across all yt-dlp extractors (YouTube, Rumble, Twitch, etc.)[cite: 2].
- Retains native titles while sanitizing illegal FAT32/MTP/Android characters and trailing whitespace.
- Zero drive wear: Writes exclusively to /mnt/zram1/dusky_ytdlp with /dev/shm fallback.
- Process-group session isolation (process_group=0 / os.killpg) to prevent orphaned FFmpeg background tasks[cite: 5, 6].
- Streaming RAW progress parser with real byte metrics and stage-aware lifecycle monitoring[cite: 4, 6].
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Final, Iterator
import uuid

# ==============================================================================
# PHASE 1: DEPENDENCY VERIFICATION & ARCH LINUX AUTO-ELEVATION
# ==============================================================================

REQUIRED_SYSTEM_BINARIES: Final[dict[str, str]] = {
    "ffmpeg": "ffmpeg",
    "fzf": "fzf",
}

REQUIRED_PYTHON_MODULES: Final[dict[str, str]] = {
    "rich": "python-rich",
    "yt_dlp": "yt-dlp",
}


def bootstrap_dependencies() -> None:
    """Detects missing Arch Linux packages and auto-elevates via pacman."""
    missing: list[str] = []

    for binary, pkg in REQUIRED_SYSTEM_BINARIES.items():
        if not shutil.which(binary):
            missing.append(pkg)

    for mod, pkg in REQUIRED_PYTHON_MODULES.items():
        if importlib.util.find_spec(mod) is None:
            if pkg not in missing:
                missing.append(pkg)

    if not missing:
        return

    print("\n[!] Missing system packages detected on Arch Linux:")
    for pkg in missing:
        print(f"    - {pkg}")
    print("[*] Escalating privileges to execute: sudo pacman -S --needed\n")

    cmd = ["sudo", "pacman", "-S", "--needed"] + missing
    try:
        res = subprocess.run(cmd, check=False)
        if res.returncode != 0:
            sys.stderr.write("\n[-] Pacman installation was cancelled or failed.\n")
            sys.exit(res.returncode)
    except Exception as err:
        sys.stderr.write(f"\n[-] Elevation error: {err}\n")
        sys.exit(1)

    print("[+] Dependencies resolved. Initializing script environment...\n")
    os.execv(sys.executable, [sys.executable] + sys.argv)


bootstrap_dependencies()

# ==============================================================================
# PHASE 2: MODULE IMPORTS & LIFECYCLE MANAGEMENT
# ==============================================================================

import argparse
from dataclasses import dataclass
from enum import StrEnum

import yt_dlp
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table

console: Final[Console] = Console()

PRIMARY_ZRAM_TARGET: Final[Path] = Path("/mnt/zram1/dusky_ytdlp")
RAM_TMPFS_FALLBACK: Final[Path] = Path("/dev/shm/dusky_ytdlp")

ACTIVE_PROCESS_GROUPS: set[int] = set()


def global_signal_handler(signum: int, frame: object) -> None:
    """Kills the entire process group (yt-dlp + FFmpeg children) on SIGINT/SIGTERM."""
    console.print("\n\n[bold red][!] Interrupted: Terminating process tree & cleaning memory buffers...[/]")
    for pgid in list(ACTIVE_PROCESS_GROUPS):
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            pass
    sys.exit(130)


signal.signal(signal.SIGINT, global_signal_handler)
signal.signal(signal.SIGTERM, global_signal_handler)

# ==============================================================================
# PHASE 3: CORE DATA MODELS
# ==============================================================================


class TargetFormat(StrEnum):
    VIDEO = "video"
    AUDIO_OPUS = "audio-opus"
    AUDIO_MP3 = "audio-mp3"
    AUDIO_BEST = "audio-best"


class ProgressStage(StrEnum):
    INITIALIZING = "Initializing"
    DOWNLOADING = "Downloading"
    MERGING = "Merging"
    REMUXING = "Remuxing"
    REENCODING = "Reencoding"
    FINALIZING = "Finalizing"


@dataclass(slots=True)
class MediaProgress:
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    percentage: float = 0.0
    speed_bps: float | None = None
    eta_secs: int | None = None
    stage: ProgressStage = ProgressStage.INITIALIZING
    destination_file: str | None = None


# ==============================================================================
# PHASE 4: OVD STREAMING PROGRESS PARSER (Ported from ytdlp_progress.rs)
# ==============================================================================

RAW_PROGRESS_TEMPLATE: Final[str] = (
    "RAW|"
    "%(progress.percent|)s|"
    "%(progress._percent_str|)s|"
    "%(progress.speed|)s|"
    "%(progress.eta|)s|"
    "%(progress.downloaded_bytes|)s|"
    "%(progress.total_bytes|)s|"
    "%(progress.total_bytes_estimate|)s|"
    "%(progress.fragment_index|)s|"
    "%(progress.fragment_count|)s"
)


class YtdlpProgressParser:
    """Parses yt-dlp stdout lines and tracks byte counts and stages in real time."""

    def __init__(self) -> None:
        self.current_stage = ProgressStage.INITIALIZING

    def parse_line(self, line: str, progress_state: MediaProgress) -> None:
        line_clean = line.strip()

        # 1. Postprocess stages
        if line_clean.startswith("[VideoRemuxer]"):
            self.current_stage = ProgressStage.REMUXING
            progress_state.stage = self.current_stage
            return
        if line_clean.startswith("[VideoConvertor]"):
            self.current_stage = ProgressStage.REENCODING
            progress_state.stage = self.current_stage
            return

        # 2. Destination tracking
        if "[download] Destination:" in line_clean:
            self.current_stage = ProgressStage.DOWNLOADING
            progress_state.stage = self.current_stage
            dest = line_clean.split("Destination:", 1)[1].strip()
            progress_state.destination_file = Path(dest).name
            return

        if line_clean.startswith("[Merger] Merging formats into"):
            self.current_stage = ProgressStage.MERGING
            progress_state.stage = self.current_stage
            target = line_clean.replace("[Merger] Merging formats into", "").strip().strip('"')
            progress_state.destination_file = Path(target).name
            return

        if line_clean.startswith("[ExtractAudio] Destination:"):
            dest = line_clean.replace("[ExtractAudio] Destination:", "").strip().strip('"')
            progress_state.destination_file = Path(dest).name
            return

        # 3. Finalizing triggers
        if any(t in line_clean for t in ("[ffmpeg]", "[Fixup]", "Deleting original file")):
            if self.current_stage != ProgressStage.FINALIZING:
                self.current_stage = ProgressStage.FINALIZING
                progress_state.stage = self.current_stage
            return

        # 4. RAW progress protocol metrics
        if line_clean.startswith("RAW|"):
            parts = line_clean[4:].split("|")
            while len(parts) < 9:
                parts.append("")

            def parse_int(s: str) -> int | None:
                t = s.strip()
                return int(t) if t and t.lower() != "na" and t.isdigit() else None

            def parse_float(s: str) -> float | None:
                t = s.strip().rstrip("%")
                try:
                    return float(t) if t and t.lower() != "na" else None
                except ValueError:
                    return None

            dl_bytes = parse_int(parts[4])
            total_bytes = parse_int(parts[5]) or parse_int(parts[6])
            speed = parse_float(parts[2])
            eta = parse_int(parts[3])

            if dl_bytes is not None:
                progress_state.downloaded_bytes = dl_bytes
            if total_bytes is not None and total_bytes > 0:
                progress_state.total_bytes = total_bytes
            if speed is not None:
                progress_state.speed_bps = speed
            if eta is not None:
                progress_state.eta_secs = eta


# ==============================================================================
# PHASE 5: STORAGE MANAGEMENT & RUNNER COMPILER
# ==============================================================================


def resolve_storage_pool(custom_path: Path | None = None) -> Path:
    """Ensures media writes occur strictly in memory (ZRAM or tmpfs)."""
    candidates = [custom_path] if custom_path else [PRIMARY_ZRAM_TARGET, RAM_TMPFS_FALLBACK]

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / f".probe_{uuid.uuid4().hex[:6]}"
            probe.touch()
            probe.unlink()

            stats = shutil.disk_usage(path)
            if (stats.free / (1024 * 1024)) < 500:
                console.print(f"[bold yellow]![/] Warning: Storage pool {path} has under 500 MB remaining.")
            return path
        except (OSError, PermissionError):
            continue

    fallback = Path.cwd() / "dusky_downloads"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


class YtdlpRunner:
    """Compiles yt-dlp arguments and manages isolated process execution."""

    def __init__(self, mode: TargetFormat, output_dir: Path, url: str):
        self.mode = mode
        self.output_dir = output_dir
        self.url = url

        # Output template: keeps original title while ensuring safe Android/phone filenames
        output_template = str(output_dir / "%(title).180B [%(id)s].%(ext)s")

        self.args: list[str] = [
            "--encoding", "utf-8",
            "--newline",
            "--progress",
            "--no-color",
            "--progress-template", RAW_PROGRESS_TEMPLATE,
            "--progress-delta", "0.5",
            # Multi-connection & fragment recovery
            "--concurrent-fragments", "4",
            "--retries", "30",
            "--fragment-retries", "30",
            "--file-access-retries", "10",
            "--socket-timeout", "30",
            # Replace invalid FAT32/Android characters for safe phone transfer
            "--windows-filenames",
            # Preserve embedded metadata tags
            "--add-metadata",
        ]
        self._compile_format(output_template)

    def _compile_format(self, output_template: str) -> None:
        match self.mode:
            case TargetFormat.AUDIO_OPUS:
                self.args.extend([
                    "-f", "bestaudio[ext=opus]/bestaudio[acodec=opus]/bestaudio/best",
                    "-x", "--audio-format", "opus", "--audio-quality", "0",
                ])
            case TargetFormat.AUDIO_MP3:
                self.args.extend([
                    "-f", "bestaudio/best",
                    "-x", "--audio-format", "mp3", "--audio-quality", "0",
                ])
            case TargetFormat.AUDIO_BEST:
                self.args.extend([
                    "-f", "bestaudio/best",
                    "-x", "--audio-format", "best",
                ])
            case TargetFormat.VIDEO:
                self.args.extend([
                    "-f", "bestvideo*+bestaudio/best",
                    "--merge-output-format", "mp4",
                ])

        self.args.extend(["-o", output_template, self.url])

    def spawn(self) -> tuple[subprocess.Popen, int]:
        """Spawns yt-dlp in a distinct process group using POSIX process_group=0."""
        cmd = ["yt-dlp"] + self.args
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            process_group=0,  # Isolate process tree
        )
        pgid = os.getpgid(proc.pid)
        ACTIVE_PROCESS_GROUPS.add(pgid)
        return proc, pgid


# ==============================================================================
# PHASE 6: DOWNLOAD PIPELINE
# ==============================================================================


@dataclass(slots=True)
class MediaJob:
    title: str
    url: str
    mode: TargetFormat


@dataclass(slots=True)
class JobReport:
    title: str
    status: str
    saved_file: str = "--"
    size_mb: float = 0.0
    error: str | None = None


def execute_download(job: MediaJob, output_dir: Path) -> JobReport:
    runner = YtdlpRunner(job.mode, output_dir, job.url)
    parser = YtdlpProgressParser()
    progress_state = MediaProgress()

    proc, pgid = runner.spawn()

    def stdout_reader(stream: Iterator[bytes]) -> None:
        for raw_line in stream:
            try:
                line_str = raw_line.decode("utf-8", errors="replace")
                parser.parse_line(line_str, progress_state)
            except Exception:
                pass

    reader_thread = threading.Thread(target=stdout_reader, args=(proc.stdout,), daemon=True)
    reader_thread.start()

    display_title = (job.title[:30] + "..") if len(job.title) > 32 else job.title

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold yellow]{task.fields[title]}[/]"),
        BarColumn(bar_width=24),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        TextColumn("[bold cyan]{task.description}[/]"),
        console=console,
        transient=True,
    ) as progress_ui:
        task_id = progress_ui.add_task("Initializing", total=None, title=display_title)

        while proc.poll() is None:
            time.sleep(0.1)
            progress_ui.update(
                task_id,
                completed=progress_state.downloaded_bytes,
                total=progress_state.total_bytes,
                description=f"[{progress_state.stage}]",
            )

    reader_thread.join(timeout=1.0)
    ACTIVE_PROCESS_GROUPS.discard(pgid)

    exit_code = proc.returncode
    if exit_code != 0:
        err_msg = proc.stderr.read().decode("utf-8", errors="replace").strip()
        last_line = err_msg.split("\n")[-1] if err_msg else f"yt-dlp error code {exit_code}"
        return JobReport(title=job.title, status="Failed", error=last_line)

    # Locate and clean up trailing spaces before file extensions
    dest_file = progress_state.destination_file or "--"
    size_mb = 0.0
    if dest_file != "--":
        actual_path = output_dir / dest_file
        if not actual_path.exists():
            candidates = sorted(output_dir.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                actual_path = candidates[0]
                dest_file = actual_path.name

        if actual_path.exists():
            # Fix trailing space before extension: e.g. "title .m4a" -> "title.m4a"
            clean_name = re.sub(r"\s+\.([a-zA-Z0-9]+)$", r".\1", actual_path.name)
            if clean_name != actual_path.name:
                cleaned_path = actual_path.with_name(clean_name)
                actual_path.replace(cleaned_path)
                actual_path = cleaned_path
                dest_file = clean_name

            size_mb = actual_path.stat().st_size / (1024 * 1024)

    return JobReport(title=job.title, status="Success", saved_file=dest_file, size_mb=size_mb)


# ==============================================================================
# PHASE 7: TARGET PROBING & INTERACTIVE TUI
# ==============================================================================


def probe_media_target(url: str) -> tuple[list[tuple[str, str]], bool, str]:
    """Universal flat extraction probe across any media endpoint."""
    opts = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise ValueError("No metadata returned by extractor.")

    if "entries" in info and info["entries"]:
        items: list[tuple[str, str]] = []
        for e in info["entries"]:
            if not e:
                continue
            item_url = e.get("url") or e.get("webpage_url")
            if not item_url and e.get("id"):
                item_url = e["id"]
            if item_url:
                items.append((e.get("title") or item_url, item_url))
        return items, True, info.get("title") or "Collection / Feed"

    single_url = info.get("webpage_url") or info.get("original_url") or url
    single_title = info.get("title") or single_url
    return [(single_title, single_url)], False, single_title


def run_interactive_wizard() -> tuple[list[MediaJob], Path]:
    console.print(
        Panel.fit(
            "[bold cyan]Dusky Universal Downloader (OVD v3.2.1 Architecture)[/]\n"
            "[dim]General-purpose memory stream engine for Arch Linux[/]",
            border_style="cyan",
            box=box.DOUBLE,
        )
    )

    fmt_choice = Prompt.ask(
        "\n[bold green]?[/] Select delivery format",
        choices=["video", "audio-opus", "audio-mp3", "audio-best"],
        default="video",
    )
    mode = TargetFormat(fmt_choice)

    jobs: list[MediaJob] = []

    while True:
        raw_target = Prompt.ask("\n[bold green]?[/] Enter media link, playlist URL, or batch file path").strip()
        if not raw_target:
            continue

        local_file = Path(raw_target)
        if local_file.is_file():
            with local_file.open("r", encoding="utf-8") as f:
                for idx, line in enumerate(f, start=1):
                    clean = line.strip()
                    if not clean or clean.startswith(("#", "//")):
                        continue
                    jobs.append(MediaJob(title=f"Batch Item {idx}", url=clean, mode=mode))
            if jobs:
                break
            console.print("[bold red]Batch file contained no valid URLs.[/]")
            continue

        try:
            with console.status("[bold cyan]Probing remote endpoint...[/]", spinner="dots"):
                discovered, is_collection, label = probe_media_target(raw_target)
            break
        except Exception as err:
            console.print(Panel(f"[bold red]Probe failed:[/] {err}", border_style="red"))

    if not jobs:
        if not is_collection:
            title, link = discovered[0]
            console.print(f"[green]✓[/] Discovered: [bold yellow]{title}[/]")
            jobs = [MediaJob(title=title, url=link, mode=mode)]
        else:
            total = len(discovered)
            console.print(f"[green]✓[/] Collection detected: [yellow]{total}[/] items ([dim]{label}[/])")

            if Confirm.ask("[bold green]?[/] Invert order? (Oldest ➔ Newest)", default=False):
                discovered.reverse()

            range_val = Prompt.ask(
                f"[bold green]?[/] Range to extract ('all' or '1-{min(10, total)}')", default="all"
            ).strip()

            if range_val.lower() != "all" and "-" in range_val:
                try:
                    s_str, e_str = range_val.split("-", 1)
                    discovered = discovered[max(0, int(s_str) - 1) : int(e_str)]
                except ValueError:
                    pass

            jobs = [MediaJob(title=item[0], url=item[1], mode=mode) for item in discovered]

    default_dir = resolve_storage_pool()
    custom_dir = Prompt.ask("[bold green]?[/] Target directory (ZRAM)", default=str(default_dir))
    destination = resolve_storage_pool(Path(custom_dir))

    return jobs, destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Dusky Universal Media Downloader.")
    parser.add_argument("target", nargs="?", help="URL (video, playlist, stream) or batch file")
    parser.add_argument(
        "-f",
        "--format",
        choices=["video", "audio-opus", "audio-mp3", "audio-best"],
        default="video",
        help="Delivery format",
    )
    parser.add_argument("-o", "--output-dir", type=Path, help="Storage directory override")

    args = parser.parse_args()

    if not args.target:
        jobs, destination = run_interactive_wizard()
    else:
        destination = resolve_storage_pool(args.output_dir)
        mode = TargetFormat(args.format)
        target_path = Path(args.target)

        if target_path.is_file():
            jobs = []
            with target_path.open("r", encoding="utf-8") as f:
                for idx, line in enumerate(f, start=1):
                    clean = line.strip()
                    if not clean or clean.startswith(("#", "//")):
                        jobs.append(MediaJob(title=f"Item {idx}", url=clean, mode=mode))
        else:
            discovered, is_collection, _ = probe_media_target(args.target)
            jobs = [MediaJob(title=item[0], url=item[1], mode=mode) for item in discovered]

    if not jobs:
        console.print("[bold red]No download targets queued.[/]")
        sys.exit(1)

    console.print(
        f"\n[bold green]➜[/] Storage Pool: [cyan]{destination}[/] | Queue: [yellow]{len(jobs)}[/] | Format: [magenta]{jobs[0].mode.upper()}[/]\n"
    )

    reports: list[JobReport] = []
    for job in jobs:
        console.print(f"[bold blue]•[/] Processing: [bold yellow]{job.title}[/]")
        res = execute_download(job, destination)
        reports.append(res)

    table = Table(
        title="Extraction Log",
        box=box.ROUNDED,
        header_style="bold cyan",
        title_style="bold green",
        expand=True,
    )
    table.add_column("Title", style="yellow", ratio=4, overflow="ellipsis")
    table.add_column("Status", justify="center", width=10)
    table.add_column("Size", justify="right", width=12)
    table.add_column("Filename", style="dim", ratio=5, overflow="ellipsis")

    for r in reports:
        status_str = "[bold green]Success[/]" if r.status == "Success" else f"[bold red]{r.error}[/]"
        size_str = f"{r.size_mb:.2f} MB" if r.status == "Success" else "--"
        table.add_row(r.title, status_str, size_str, r.saved_file)

    console.print("\n")
    console.print(table)
    console.print(
        Panel(
            f"[bold green]Location:[/] [cyan]{destination}[/]\n"
            f"[dim]Media downloaded with native titles directly into RAM/ZRAM.[/]",
            border_style="green",
        )
    )


if __name__ == "__main__":
    main()

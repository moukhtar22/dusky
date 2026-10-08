#!/usr/bin/env python3
"""Benchmark independent Zstandard frames using reusable C contexts.

The default frame size is the system page size, matching zram's compression
unit. This is a userspace codec benchmark, not a measurement of zram's actual
RAM consumption: kernel parameters, same-filled pages, raw-page storage,
zsmalloc overhead and kernel I/O paths are not modeled.
"""

import argparse
import ctypes
import hashlib
import os
import platform
import resource
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from statistics import median

if sys.version_info < (3, 14):
    sys.exit("Python 3.14 or newer is required.")

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
    from rich.prompt import IntPrompt
    from rich.text import Text
    from rich.table import Table
except ImportError:
    sys.exit("Missing python-rich. Install it with: pacman -S python-rich")

console = Console(highlight=False)
MIB = 1024 * 1024
METHOD = (
    "Single-threaded libzstd; reusable contexts and buffers; one full warmup per "
    "level; median measured passes; every pass verified byte-for-byte. "
    "Times include Python loop, ctypes calls and error checks, but exclude "
    "data generation, buffer allocation and byte comparison. Minor page faults "
    "are process-wide medians, not allocation counts. Timing spread is "
    "(slowest minus fastest pass) / median, not a confidence interval."
)
LIMITATION = (
    "Codec frame sizes and speeds are not actual zram RAM usage or throughput. "
    "Kernel codec parameters/version, same-filled-page handling, raw storage of "
    "incompressible pages, allocator overhead and kernel I/O are not modeled. "
    "Synthetic mixed data is 33% random bytes and 67% repeated text in 1 MiB "
    "regions, not a representative sample of a machine's memory."
)


class Zstd:
    """Only documented, stable libzstd APIs; no CLI subprocesses."""

    def __init__(self) -> None:
        self.lib = ctypes.CDLL("libzstd.so.1")
        size = ctypes.c_size_t
        ptr = ctypes.c_void_p
        signatures = {
            "ZSTD_isError": ([size], ctypes.c_uint),
            "ZSTD_getErrorName": ([size], ctypes.c_char_p),
            "ZSTD_versionString": ([], ctypes.c_char_p),
            "ZSTD_maxCLevel": ([], ctypes.c_int),
            "ZSTD_compressBound": ([size], size),
            "ZSTD_createCCtx": ([], ptr),
            "ZSTD_createDCtx": ([], ptr),
            "ZSTD_freeCCtx": ([ptr], size),
            "ZSTD_freeDCtx": ([ptr], size),
            "ZSTD_compressCCtx": ([ptr, ptr, size, ptr, size, ctypes.c_int], size),
            "ZSTD_decompressDCtx": ([ptr, ptr, size, ptr, size], size),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes = args
            function.restype = result
        self.version = self.lib.ZSTD_versionString().decode("ascii")
        self.max_level = self.lib.ZSTD_maxCLevel()

    def check(self, code: int, operation: str) -> int:
        if self.lib.ZSTD_isError(code):
            error = self.lib.ZSTD_getErrorName(code).decode("utf-8")
            raise RuntimeError(f"{operation}: {error}")
        return code

    @contextmanager
    def contexts(self) -> Iterator[tuple[int, int]]:
        cctx = self.lib.ZSTD_createCCtx()
        dctx = None
        try:
            if not cctx:
                raise MemoryError("Could not allocate the compression context")
            dctx = self.lib.ZSTD_createDCtx()
            if not dctx:
                raise MemoryError("Could not allocate the decompression context")
            yield cctx, dctx
        finally:
            # Both free functions explicitly accept NULL, including partial setup.
            self.lib.ZSTD_freeDCtx(dctx)
            self.lib.ZSTD_freeCCtx(cctx)


@dataclass(slots=True)
class Block:
    source: ctypes.c_void_p
    compressed: ctypes.c_void_p
    restored: ctypes.c_void_p
    size: int
    capacity: int
    compressed_size: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkResult:
    level: int
    original_bytes: int
    compressed_bytes: int
    comp_time_ns: int
    decomp_time_ns: int
    comp_page_faults: float
    decomp_page_faults: float
    sample_count: int = 1
    comp_spread_pct: float = 0.0
    decomp_spread_pct: float = 0.0

    @property
    def saved_pct(self) -> float:
        return (1 - self.compressed_bytes / self.original_bytes) * 100

    @property
    def ratio(self) -> float:
        return self.original_bytes / self.compressed_bytes

    @property
    def comp_speed(self) -> float:
        return self.original_bytes / MIB / (self.comp_time_ns / 1e9)

    @property
    def decomp_speed(self) -> float:
        return self.original_bytes / MIB / (self.decomp_time_ns / 1e9)


def generate_data(size_bytes: int, entropy: str) -> bytearray:
    data = bytearray(size_bytes)
    if entropy == "zero":
        return data
    text = (
        b'{"log_level":"INFO","timestamp":"2026-06-26T19:02:55Z","system":"arch_linux_core","kernel":"7.1.0-arch1-1",'
        b'"event":"memory_compaction","metrics":{"cpu":14.5,"mem_free":1024,"zram_active":true,"throughput_mb":1350.5}} '
        b'Arch Linux rolling release. Memory compression is a critical facet of modern system architectures. '
    )
    # Bounded temporary buffers instead of a second payload-sized allocation.
    for offset in range(0, size_bytes, MIB):
        length = min(MIB, size_bytes - offset)
        random_length = length if entropy == "random" else length * 33 // 100
        data[offset:offset + random_length] = os.urandom(random_length)
        text_length = length - random_length
        if text_length:
            start = offset + random_length
            data[start:start + text_length] = (
                text * ((text_length + len(text) - 1) // len(text))
            )[:text_length]
    return data


class Workload:
    def __init__(self, zstd: Zstd, data: bytearray, block_size: int) -> None:
        self.zstd = zstd
        self.data = data
        self.block_size = min(block_size or len(data), len(data))
        capacity = zstd.check(
            zstd.lib.ZSTD_compressBound(self.block_size), "Compression bound"
        )
        if not capacity:
            raise ValueError("Block size exceeds libzstd's supported input size")
        block_count = (len(data) + self.block_size - 1) // self.block_size
        self.compressed = bytearray(capacity * block_count)
        self.restored = bytearray(len(data))
        # Keep exported views alive for the full lifetime of the raw pointers.
        self.views = [
            (ctypes.c_char * len(buffer)).from_buffer(buffer)
            for buffer in (self.data, self.compressed, self.restored)
        ]
        source, compressed, restored = map(ctypes.addressof, self.views)
        self.blocks = [
            Block(
                ctypes.c_void_p(source + offset),
                ctypes.c_void_p(compressed + index * capacity),
                ctypes.c_void_p(restored + offset),
                min(self.block_size, len(data) - offset),
                capacity,
            )
            for index, offset in enumerate(range(0, len(data), self.block_size))
        ]

    def run(self, cctx: int, dctx: int, level: int) -> BenchmarkResult:
        lib = self.zstd.lib
        check = self.zstd.check
        faults = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
        start = time.perf_counter_ns()
        for block in self.blocks:
            block.compressed_size = check(
                lib.ZSTD_compressCCtx(
                    cctx, block.compressed, block.capacity,
                    block.source, block.size, level,
                ),
                "Compression",
            )
        comp_ns = max(time.perf_counter_ns() - start, 1)
        comp_faults = resource.getrusage(resource.RUSAGE_SELF).ru_minflt - faults

        faults = resource.getrusage(resource.RUSAGE_SELF).ru_minflt
        start = time.perf_counter_ns()
        for block in self.blocks:
            restored_size = check(
                lib.ZSTD_decompressDCtx(
                    dctx, block.restored, block.size,
                    block.compressed, block.compressed_size,
                ),
                "Decompression",
            )
            if restored_size != block.size:
                raise RuntimeError(
                    f"Decompressed size mismatch: {restored_size} != {block.size}"
                )
        decomp_ns = max(time.perf_counter_ns() - start, 1)
        decomp_faults = resource.getrusage(resource.RUSAGE_SELF).ru_minflt - faults
        if self.restored != self.data:
            raise RuntimeError("Decompressed bytes differ from the input")
        return BenchmarkResult(
            level=level,
            original_bytes=len(self.data),
            compressed_bytes=sum(block.compressed_size for block in self.blocks),
            comp_time_ns=comp_ns,
            decomp_time_ns=decomp_ns,
            comp_page_faults=comp_faults,
            decomp_page_faults=decomp_faults,
        )

    def measure(
        self, cctx: int, dctx: int, level: int, repeats: int,
        on_pass: Callable[[], None] | None = None,
    ) -> BenchmarkResult:
        self.run(cctx, dctx, level)  # Full workload warmup at the measured level.
        if on_pass is not None:
            on_pass()
        samples = []
        for _ in range(repeats):
            samples.append(self.run(cctx, dctx, level))
            if on_pass is not None:
                on_pass()  # Rendering always occurs outside timed regions.
        first = samples[0]
        if any(sample.compressed_bytes != first.compressed_bytes for sample in samples):
            raise RuntimeError("Compressed size changed between identical measured passes")
        comp_times = [sample.comp_time_ns for sample in samples]
        decomp_times = [sample.decomp_time_ns for sample in samples]
        comp_median = int(median(comp_times))
        decomp_median = int(median(decomp_times))
        return BenchmarkResult(
            level=level,
            original_bytes=first.original_bytes,
            compressed_bytes=first.compressed_bytes,
            comp_time_ns=comp_median,
            decomp_time_ns=decomp_median,
            comp_page_faults=median(sample.comp_page_faults for sample in samples),
            decomp_page_faults=median(sample.decomp_page_faults for sample in samples),
            sample_count=repeats,
            comp_spread_pct=(max(comp_times) - min(comp_times)) / comp_median * 100,
            decomp_spread_pct=(max(decomp_times) - min(decomp_times)) / decomp_median * 100,
        )


def result_cells(result: BenchmarkResult) -> tuple[str, ...]:
    comp_spread, decomp_spread = spread_cells(result)
    return (
        str(result.level),
        f"{result.compressed_bytes / MIB:.4f} MiB ({result.compressed_bytes} B)",
        f"{result.ratio:.3f}x",
        f"{(result.original_bytes - result.compressed_bytes) / MIB:.4f} MiB",
        f"{result.comp_time_ns / 1e6:.3f} ms ({result.comp_speed:.1f} MiB/s)",
        f"{result.decomp_time_ns / 1e6:.3f} ms ({result.decomp_speed:.1f} MiB/s)",
        f"{result.comp_page_faults:g} / {result.decomp_page_faults:g}",
        comp_spread, decomp_spread,
    )


COLUMNS = (
    "Level", "Compressed", "Ratio", "Saved", "Compression", "Decompression",
    "Minor faults C/D", "Compression spread", "Decompression spread",
)


def spread_cells(result: BenchmarkResult) -> tuple[str, str]:
    if result.sample_count < 2:
        return "N/A", "N/A"
    return f"{result.comp_spread_pct:.1f}%", f"{result.decomp_spread_pct:.1f}%"


def show_setup(workload: Workload, source: str, passes: str) -> None:
    settings = Table.grid(padding=(0, 2))
    settings.add_column(style="dim", no_wrap=True)
    settings.add_column()
    for key, value in (
        ("Payload", f"{len(workload.data) / MIB:,.3f} MiB · {len(workload.data):,} bytes"),
        ("Source", source),
        ("Frames", f"{len(workload.blocks):,} independent frames · up to {workload.block_size:,} bytes each"),
        ("Sampling", passes),
        ("Library", f"libzstd {workload.zstd.version} · single thread"),
    ):
        settings.add_row(key, Text(value))
    console.print(Panel(settings, title="[bold cyan]Zstandard compression benchmark[/]",
                        title_align="left", border_style="cyan", box=box.ROUNDED))
    console.print("[green]Savings[/]  ·  [cyan]Compression[/]  ·  [magenta]Decompression[/]")
    console.print("[dim]Userspace codec results; actual zram memory usage is not measured.[/]\n")


def show_results(results: list[BenchmarkResult]) -> None:
    """Use compact tables at normal widths and readable cards on small terminals."""
    console.print("\n[bold]Compression results[/] [dim]· median speeds; larger is faster[/]")
    if console.width >= 76:
        table = Table(box=box.SIMPLE_HEAVY, border_style="dim", padding=(0, 1),
                      header_style="bold", highlight=False)
        for heading, style in (
            ("Level", "bold"), ("Compressed\nMiB", "green"), ("Ratio", "green"),
            ("Saved\n%", "green"), ("Compress\nMiB/s", "cyan"),
            ("Decompress\nMiB/s", "magenta"),
        ):
            table.add_column(heading, style=style, justify="right", no_wrap=True,
                             overflow="fold")
        for result in results:
            saving_style = "green" if result.saved_pct >= 0 else "red"
            table.add_row(
                str(result.level), Text(f"{result.compressed_bytes / MIB:.4f}", style=saving_style),
                Text(f"{result.ratio:.3f}x", style=saving_style),
                Text(f"{result.saved_pct:.2f}", style=saving_style),
                f"{result.comp_speed:,.1f}", f"{result.decomp_speed:,.1f}",
            )
        console.print(table)
        diagnostics = Table(title="Timing diagnostics", title_justify="left",
                            box=box.SIMPLE, header_style="bold", highlight=False)
        for heading, style in (
            ("Level", "bold"), ("Comp\nms", "cyan"), ("Decomp\nms", "magenta"),
            ("Comp\nspread", "cyan"), ("Decomp\nspread", "magenta"),
            ("Minor faults\nC / D", "dim"),
        ):
            diagnostics.add_column(heading, style=style, justify="right", no_wrap=True,
                                   overflow="fold")
        for result in results:
            comp_spread, decomp_spread = spread_cells(result)
            diagnostics.add_row(
                str(result.level), f"{result.comp_time_ns / 1e6:.3f}",
                f"{result.decomp_time_ns / 1e6:.3f}", comp_spread, decomp_spread,
                f"{result.comp_page_faults:g} / {result.decomp_page_faults:g}",
            )
        console.print(diagnostics)
    else:
        for result in results:
            comp_spread, decomp_spread = spread_cells(result)
            details = Text()
            details.append(f"{result.compressed_bytes / MIB:.4f} MiB · {result.ratio:.3f}x · "
                           f"{result.saved_pct:.2f}% saved\n",
                           style="green" if result.saved_pct >= 0 else "red")
            details.append(f"Compress: {result.comp_speed:,.1f} MiB/s\n"
                           f"  {result.comp_time_ns / 1e6:.3f} ms · spread {comp_spread}\n", "cyan")
            details.append(f"Decompress: {result.decomp_speed:,.1f} MiB/s\n"
                           f"  {result.decomp_time_ns / 1e6:.3f} ms · spread {decomp_spread}\n", "magenta")
            details.append(f"Minor faults C/D: {result.comp_page_faults:g} / "
                           f"{result.decomp_page_faults:g}", "dim")
            console.print(Panel(details, title=f"Level {result.level}", title_align="left",
                                border_style="dim", box=box.ROUNDED))
    smallest = min(results, key=lambda result: result.compressed_bytes)
    fastest_comp = max(results, key=lambda result: result.comp_speed)
    fastest_decomp = max(results, key=lambda result: result.decomp_speed)
    summary = Text()
    summary.append(f"Smallest output: level {smallest.level} ({smallest.ratio:.3f}x)\n",
                   "green" if smallest.saved_pct >= 0 else "red")
    summary.append(f"Fastest compression: level {fastest_comp.level} "
                   f"({fastest_comp.comp_speed:,.1f} MiB/s)\n", "cyan")
    summary.append(f"Fastest decompression: level {fastest_decomp.level} "
                   f"({fastest_decomp.decomp_speed:,.1f} MiB/s)", "magenta")
    console.print(Panel(summary, title="Observed in this run", title_align="left",
                        border_style="dim", box=box.ROUNDED))


def save_report(results: list[BenchmarkResult], metadata: str, filepath: Path) -> None:
    content = "\n".join([
        "# Zstandard codec benchmark", "", metadata, "", METHOD, "", LIMITATION, "",
        "| " + " | ".join(COLUMNS) + " |",
        "| " + " | ".join(["---"] * len(COLUMNS)) + " |",
        *("| " + " | ".join(result_cells(result)) + " |" for result in results),
        "",
    ])
    filepath.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=filepath.parent,
            prefix=f".{filepath.name}.", delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(content)
        # A failed write leaves an existing report intact; errors reach the caller.
        temporary.replace(filepath)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    console.print(Text.assemble(("Report saved: ", "bold green"), str(filepath)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-level", type=int, help="Test levels 1 through this level (inclusive)")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--size-mb", type=int, help="Synthetic payload size in MiB (default: 50; stress: 1)")
    source.add_argument("--input", type=Path, help="Benchmark all bytes of a real data file instead of synthetic data")
    parser.add_argument("--entropy", choices=("mixed", "zero", "random"), default="mixed")
    parser.add_argument("--block-size", type=int, default=os.sysconf("SC_PAGESIZE"),
                        help="Independent frame size in bytes (default: system page size); 0: whole payload")
    parser.add_argument("--repeats", type=int, default=3, help="Measured passes per level (default: 3)")
    parser.add_argument("--auto", action="store_true", help="Skip prompts and save a report")
    parser.add_argument("--report", type=Path, help="Report path (also saves without --auto)")
    parser.add_argument("--chaos-leak", action="store_true",
                        help="Stress reusable contexts: 100 sweeps of levels 1–10, with round-trip checks")
    args = parser.parse_args()
    if args.block_size < 0 or args.repeats < 1:
        parser.error("--block-size must be nonnegative and --repeats must be positive")
    if args.size_mb is not None and args.size_mb <= 0:
        parser.error("--size-mb must be positive")
    if args.input is not None and args.entropy != "mixed":
        parser.error("--entropy applies only to synthetic data")
    if args.chaos_leak and (args.max_level is not None or args.report or args.auto):
        parser.error("--chaos-leak cannot be combined with --max-level, --report or --auto")

    zstd = Zstd()
    max_level = args.max_level
    if max_level is not None and not 1 <= max_level <= zstd.max_level:
        parser.error(f"--max-level must be between 1 and {zstd.max_level}")
    interactive = not args.auto and not args.chaos_leak and sys.stdin.isatty()
    if args.chaos_leak:
        max_level = min(10, zstd.max_level)
    elif max_level is None:
        if interactive:
            while True:
                max_level = IntPrompt.ask(f"Maximum level (1–{zstd.max_level})", default=10)
                if 1 <= max_level <= zstd.max_level:
                    break
                console.print("[red]Level outside the supported range.[/]")
        else:
            max_level = min(12, zstd.max_level)

    if args.input is not None:
        with args.input.open("rb") as input_file:
            data = bytearray(input_file.read())
        source_description = f"File: {args.input}"
    else:
        size_mb = args.size_mb
        if size_mb is None:
            size_mb = 1 if args.chaos_leak else 50
            if interactive:
                size_mb = IntPrompt.ask("Synthetic payload size (MiB)", default=size_mb)
        if size_mb <= 0:
            parser.error("Payload size must be positive")
        console.print(f"Generating {size_mb} MiB of synthetic {args.entropy} data.")
        data = generate_data(size_mb * MIB, args.entropy)
        source_description = f"Synthetic: {args.entropy}"
    if not data:
        parser.error("Input file must not be empty")

    workload = Workload(zstd, data, args.block_size)
    passes = "100 stress sweeps" if args.chaos_leak else f"{args.repeats} measured passes per level"
    metadata = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S %z')} · Python {platform.python_version()} · "
        f"kernel {platform.release()} · libzstd {zstd.version}\n"
        f"{source_description} · {len(data)} bytes · independent frames up to "
        f"{workload.block_size} bytes · {len(workload.blocks)} frames · {passes}"
    )
    console.print("Preparing payload fingerprint (outside measured passes).", style="dim")
    fingerprint = hashlib.sha256(data).hexdigest()
    metadata += f"\nPayload SHA-256: {fingerprint}"
    show_setup(workload, source_description, passes)
    results = []
    with zstd.contexts() as (cctx, dctx):
        if args.chaos_leak:
            for sweep in range(100):
                for level in range(1, max_level + 1):
                    workload.run(cctx, dctx, level)
                if (sweep + 1) % 10 == 0:
                    console.print(f"[cyan]Verified stress sweeps: {sweep + 1}/100[/]")
        else:
            with Progress(
                TextColumn("{task.description}"),
                BarColumn(bar_width=None, complete_style="cyan", finished_style="green"),
                TextColumn("{task.completed:.0f}/{task.total:.0f}"),
                TimeElapsedColumn(), console=console, auto_refresh=False,
                redirect_stdout=False, redirect_stderr=False,
                disable=not console.is_terminal,
            ) as progress:
                task = progress.add_task("Preparing", total=max_level * (args.repeats + 1))

                def verified_pass() -> None:
                    progress.advance(task)
                    progress.refresh()

                for level in range(1, max_level + 1):
                    progress.update(task, description=f"Level {level}/{max_level}", refresh=True)
                    if not console.is_terminal:
                        console.print(f"Level {level}/{max_level}: warmup + {args.repeats} measured passes", style="dim")
                    results.append(workload.measure(cctx, dctx, level, args.repeats, verified_pass))
    if args.chaos_leak:
        console.print("[bold green]PASS[/] · 100 stress sweeps verified; both C contexts freed.")
        return 0

    console.print(f"[bold green]PASS[/] · {max_level} levels · "
                  f"{max_level * (args.repeats + 1)} full passes verified byte-for-byte")
    show_results(results)
    report = args.report
    if report is None and args.auto:
        report = Path(tempfile.gettempdir()) / "zstd_autonomous_report.md"
    if report is not None:
        save_report(results, metadata, report)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("Interrupted; allocated C contexts released.", style="yellow")
        sys.exit(130)
    except (OSError, MemoryError, RuntimeError, ValueError, OverflowError) as error:
        console.print(Text.assemble(("Benchmark failed: ", "bold red"), str(error)))
        sys.exit(1)

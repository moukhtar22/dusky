#!/usr/bin/env python3
"""
sysbench_benchmark.py

A Python 3.14 rewrite of the Sysbench Ultimate Dashboard.
Provides a comprehensive CPU, Memory, and Threads benchmark dashboard
supporting interactive TUI menus and non-interactive command line usage.
Respects process CPU affinity and restores temporary governor/boost settings.
"""

import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path


try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ModuleNotFoundError as exc:
    if exc.name != "rich":
        raise
    sys.exit("Missing dependency: Rich (Arch package: python-rich).")


console = Console(highlight=False, markup=False)
error_console = Console(stderr=True, highlight=False, markup=False)


def eprint(message: str) -> None:
    error_console.print(message, style="yellow", soft_wrap=True)


def ask(prompt: str) -> str:
    return console.input(Text(prompt, style="bold cyan")).strip().lower()


def print_menu(title: str, choices: list[tuple[str, str, str]], *, back_label: str = "Back") -> None:
    console.rule(title, style="cyan")
    table = Table(box=None, show_header=False, padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="bold")
    table.add_column(style="dim")
    for key, name, description in choices:
        table.add_row(key, Text(name), Text(description))
    table.add_row("q", back_label, "")
    console.print(table)


def format_cores(cpus: list[int]) -> str:
    """Compact sorted CPU IDs into taskset-compatible ranges."""
    ranges = []
    first = last = cpus[0]
    for cpu in cpus[1:]:
        if cpu == last + 1:
            last = cpu
        else:
            ranges.append(str(first) if first == last else f"{first}-{last}")
            first = last = cpu
    ranges.append(str(first) if first == last else f"{first}-{last}")
    return ",".join(ranges)


def get_cpu_model() -> str:
    try:
        with Path("/proc/cpuinfo").open() as cpuinfo:
            for line in cpuinfo:
                key, sep, value = line.partition(":")
                if sep and key.strip().lower() in {"model name", "hardware"}:
                    return value.strip()
    except OSError:
        pass
    return "Unknown CPU"


def get_online_cpus() -> list[int]:
    """Return online CPUs available to this process, including cpuset restrictions."""
    return sorted(os.sched_getaffinity(0))


def check_deps() -> None:
    for command in ("sysbench", "taskset"):
        if not shutil.which(command):
            raise RuntimeError(f"Required command '{command}' is missing.")


def print_header(cpu_model: str, online_cores: list[int]) -> None:
    text = Text(cpu_model, style="bold")
    text.append(f"\n{len(online_cores)} available logical CPUs  •  {format_cores(online_cores)}", style="cyan")
    text.append(f"\nLinux {os.uname().release}", style="dim")
    console.print()
    console.print(Panel(text, title="Sysbench Dashboard", title_align="left", border_style="cyan"))


def parse_cores(cores_str: str) -> list[int]:
    """Parse taskset CPU lists, including ranges with strides; count each CPU once."""
    available = os.sched_getaffinity(0)
    max_cpu = max(available)
    cpus: set[int] = set()
    for part in cores_str.split(","):
        match = re.fullmatch(r"([0-9]+)(?:-([0-9]+)(?::([0-9]+))?)?", part.strip())
        if not match:
            raise ValueError("Use a CPU list such as 0,2-4 or 0-6:2.")
        first = int(match[1])
        last = int(match[2]) if match[2] is not None else first
        stride = int(match[3]) if match[3] is not None else 1
        if last < first or stride < 1:
            raise ValueError("CPU ranges must ascend and strides must be positive.")
        # Reject huge/out-of-range endpoints before expanding the range.
        if first > max_cpu or last > max_cpu:
            raise ValueError("CPU range is outside the available CPU IDs.")
        cpus.update(range(first, last + 1, stride))
    unavailable = cpus - available
    if unavailable:
        raise ValueError(f"CPUs are offline or unavailable: {sorted(unavailable)}")
    return sorted(cpus)


# One privileged process owns the entire transaction. EOF on its input pipe
# restores settings even if the dashboard is killed; no sudo cache is needed
# during restoration. Only settings with a recorded original value are written.
CPU_SETTINGS_HELPER = r"""
import json
import signal
import sys
from pathlib import Path

signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
original = {}
failed = False
try:
    settings = json.loads(sys.stdin.readline())
    for name, value in settings.items():
        path = Path(name)
        old = path.read_text().strip()
        if old != value:
            original[name] = old
            path.write_text(value)
            if path.read_text().strip() != value:
                raise OSError(f"Setting did not take effect: {name}")
    print("ready", flush=True)
    sys.stdin.read()
except (OSError, ValueError) as exc:
    print(f"CPU optimization failed: {exc}", file=sys.stderr)
    failed = True
finally:
    for name, value in reversed(list(original.items())):
        try:
            path = Path(name)
            path.write_text(value)
            if path.read_text().strip() != value:
                raise OSError("restored value differs")
        except OSError as exc:
            print(f"CPU restoration failed for {name}: {exc}", file=sys.stderr)
            failed = True
sys.exit(1 if failed else 0)
"""


@contextlib.contextmanager
def optimize_cpu_performance(*, non_interactive: bool = False) -> Iterator[None]:
    settings: dict[str, str] = {}
    for path in Path("/sys/devices/system/cpu/cpufreq").glob("policy*/scaling_governor"):
        governors = path.with_name("scaling_available_governors").read_text().split()
        if "performance" in governors:
            settings[str(path)] = "performance"
        else:
            eprint(f"Warning: performance governor unavailable for {path.parent.name}.")
    for name, value in (
        ("/sys/devices/system/cpu/intel_pstate/no_turbo", "0"),
        ("/sys/devices/system/cpu/cpufreq/boost", "1"),
    ):
        if Path(name).exists():
            settings[name] = value
    if not settings:
        eprint("Warning: No supported CPU performance controls found.")
        yield
        return

    cmd = [sys.executable, "-c", CPU_SETTINGS_HELPER]
    if os.geteuid() != 0:
        if not shutil.which("sudo"):
            raise RuntimeError("CPU optimization requires sudo or root.")
        cmd = ["sudo", *(["--non-interactive"] if non_interactive else []), "--", *cmd]
    console.print("Applying available CPU performance and boost settings…", style="yellow")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        proc.stdin.write(json.dumps(settings) + "\n")
        proc.stdin.flush()
        if proc.stdout.readline().strip() != "ready":
            raise RuntimeError("CPU optimization failed; benchmark was not started.")
        yield
    finally:
        # A second interrupt must not interrupt the wait for restoration.
        handlers = {sig: signal.signal(sig, signal.SIG_IGN)
                    for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            # Close first: restoration must not depend on terminal output succeeding.
            with contextlib.suppress(BrokenPipeError):
                proc.stdin.close()
            status = proc.wait()
            proc.stdout.close()
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        if status:
            raise RuntimeError("CPU settings helper failed; see diagnostics above.")
        console.print("Original CPU settings restored.", style="green")


def parse_latency_block(stdout: str) -> tuple[dict[str, str], str]:
    header = re.search(r"^\s*Latency \((ms|us|sec)\):\s*\n", stdout, re.MULTILINE | re.IGNORECASE)
    if header is None:
        raise RuntimeError("Sysbench output has no recognized latency block.")
    block = stdout[header.end():].split("\n\n", 1)[0]
    metrics = {}
    for key, label in (("min", "min"), ("avg", "avg"), ("max", "max"), ("95th", "95th percentile")):
        match = re.search(rf"^\s*{label}:\s*(\d+(?:\.\d+)?)\s*$", block, re.MULTILINE)
        if match is None:
            raise RuntimeError(f"Sysbench output is missing latency metric: {label}")
        metrics[key] = match[1]
    return metrics, header[1].lower()


def read_metric(stdout: str, label: str, *, integer: bool = False, suffix: str = "") -> str:
    number = r"[0-9]+" if integer else r"[0-9]+(?:\.[0-9]+)?"
    match = re.search(rf"^\s*{re.escape(label)}:\s*({number}){re.escape(suffix)}\s*$",
                      stdout, re.MULTILINE)
    if match is None:
        raise RuntimeError(f"Sysbench output is missing a valid metric: {label}")
    return match[1]


def print_results(title: str, stdout: str, rows: list[tuple[str, str]], note: str = "") -> None:
    latency, unit = parse_latency_block(stdout)
    elapsed = read_metric(stdout, "total time", suffix="s")
    table = Table(box=box.SIMPLE_HEAD, header_style="bold green", padding=(0, 1))
    table.add_column("Metric")
    table.add_column("Result", justify="right", style="bold")
    for label, value in rows:
        table.add_row(Text(label), Text(value))
    table.add_row("Elapsed", f"{elapsed} s")
    for key, label in (("min", "Minimum"), ("avg", "Average"), ("max", "Maximum"), ("95th", "95th percentile")):
        table.add_row(f"{label} latency", f"{latency[key]} {unit}")
    console.print()
    console.print(Panel(table, title=title, title_align="left", border_style="green", expand=False))
    if note:
        console.print(note, style="dim")
    console.print("Latency covers a whole event; 0.00 is below Sysbench's printed precision.", style="dim")


def run_sysbench_cmd(test_type: str, args_list: list[str], thread_count: int, run_time: int, cores: str | None = None) -> str:
    cmd = []
    if cores:
        cmd.extend(["taskset", "-c", cores])

    cmd.extend([
        "sysbench",
        test_type,
        f"--threads={thread_count}",
        f"--time={run_time}",
        "--events=0",
        "--report-interval=0",
        "--percentile=95",
        *args_list,
        "run"
    ])

    console.print()
    console.print(Panel(
        Text(f"{test_type.upper()}  •  {thread_count} {'worker' if thread_count == 1 else 'workers'}  •  {run_time} s\nCPUs: {cores or 'inherited affinity'}"),
        title="Running benchmark", title_align="left", border_style="blue",
    ))
    console.print(shlex.join(cmd), style="dim", soft_wrap=True)
    console.print("Please wait for the result. Ctrl+C cancels the run.", style="dim")
    # Stable labels/numeric formatting for parsing, independent of user locale.
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, env={**os.environ, "LC_ALL": "C"},
                          start_new_session=True) as proc:
        try:
            stdout, stderr = proc.communicate()
        except BaseException:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise
        if proc.returncode:
            raise RuntimeError(
                f"Benchmark exited with status {proc.returncode}:\n{stderr.strip()}\n{stdout.strip()}"
            )
    if stderr.strip():
        eprint(stderr.strip())
    version = re.search(r"^sysbench ([^\s]+)", stdout, re.MULTILINE)
    if version:
        console.print(f"Sysbench {version[1]}", style="dim")
    return stdout


def run_cpu_test(thread_count: int, run_time: int, cores: str | None = None) -> None:
    console.print("CPU workload: prime calculation up to 50,000.", style="dim")
    stdout = run_sysbench_cmd("cpu", ["--cpu-max-prime=50000"], thread_count, run_time, cores)
    speed = read_metric(stdout, "events per second")
    events = read_metric(stdout, "total number of events", integer=True)
    print_results("CPU results", stdout, [("CPU speed", f"{speed} events/s"), ("Events", events)],
                  "Higher events/s means more throughput for this workload.")


MEMORY_MODES = {
    "seq_read": ("read", "seq", "local", "64M"),
    "rnd_read": ("read", "rnd", "global", "4K"),
    "seq_write": ("write", "seq", "local", "64M"),
}


def run_memory_test(thread_count: int, run_time: int, cores: str | None = None,
                    mode: str = "seq_read", block_size: str | None = None,
                    scope: str | None = None) -> None:
    oper, access_mode, default_scope, default_block = MEMORY_MODES[mode]
    actual_block = block_size or default_block
    actual_scope = scope or default_scope
    console.print(f"Memory workload: {oper} / {access_mode} / {actual_scope} buffers / {actual_block} blocks.", style="dim")
    stdout = run_sysbench_cmd(
        "memory",
        [f"--memory-block-size={actual_block}", f"--memory-access-mode={access_mode}",
         f"--memory-scope={actual_scope}", "--memory-total-size=0", f"--memory-oper={oper}"],
        thread_count, run_time, cores,
    )
    match = re.search(r"^\s*([0-9]+(?:\.[0-9]+)?)\s+(\w+) transferred \(([0-9]+(?:\.[0-9]+)?) ([\w/]+)\)\s*$",
                      stdout, re.MULTILINE)
    if match is None:
        raise RuntimeError("Sysbench output is missing valid memory throughput.")
    print_results("Memory results", stdout,
                  [("Throughput", f"{match[3]} {match[4]}"), ("Data transferred", f"{match[1]} {match[2]}")],
                  "Buffers are reused; small blocks typically measure cache throughput. Random read includes random-number generation.")


def run_threads_test(thread_count: int, run_time: int, cores: str | None = None) -> None:
    console.print("Threads workload: 1 shared lock and 1,000 scheduler yields per event.", style="dim")
    stdout = run_sysbench_cmd("threads", ["--thread-locks=1", "--thread-yields=1000"],
                             thread_count, run_time, cores)
    events = read_metric(stdout, "total number of events", integer=True)
    elapsed = float(read_metric(stdout, "total time", suffix="s"))
    if elapsed <= 0:
        raise RuntimeError("Sysbench reported a non-positive elapsed time.")
    print_results("Thread contention results", stdout,
                  [("Event rate", f"{int(events) / elapsed:.2f} events/s"), ("Events", events)],
                  "This workload combines mutex contention and scheduler yields.")


def prompt_cores() -> tuple[str | None, int]:
    while True:
        cpus = get_online_cpus()
        print_menu("CPU selection", [
            ("1", "All available CPUs", f"{len(cpus)} workers · default"),
            ("2", "First available CPU", f"CPU {cpus[0]} · 1 worker"),
            ("3", "Last available CPU", f"CPU {cpus[-1]} · 1 worker"),
            ("4", "Custom CPU list", "Ranges and strides supported"),
        ])
        choice = ask("Select CPUs [1]: ")
        if choice in {"", "1"}:
            return format_cores(cpus), len(cpus)
        if choice in {"2", "3"}:
            return str(cpus[0] if choice == "2" else cpus[-1]), 1
        if choice == "q":
            return None, 0
        if choice == "4":
            custom = ask(f"Available: {format_cores(cpus)}\nCPU list (e.g. 0,2-4 or 0-6:2; q back): ")
            if custom == "q":
                continue
            try:
                selected = parse_cores(custom)
            except ValueError as exc:
                eprint(f"Invalid CPU list: {exc}")
                continue
            return format_cores(selected), len(selected)
        eprint("Choose 1–4, Enter for all CPUs, or q to go back.")


def prompt_duration() -> int | None:
    console.rule("Run duration", style="cyan")
    while True:
        choice = ask("Seconds [10] (1–86400; q back): ")
        if choice == "q":
            return None
        try:
            return positive_duration(choice or "10")
        except argparse.ArgumentTypeError as exc:
            eprint(str(exc))


def prompt_memory_mode() -> str | None:
    print_menu("Memory workload", [
        ("1", "Sequential read", "64 MiB per worker · default"),
        ("2", "Random read", "Shared 4 KiB buffer · cache-sized workload"),
        ("3", "Sequential write", "64 MiB per worker"),
    ])
    while True:
        choice = ask("Select workload [1]: ")
        if choice == "q":
            return None
        if choice in {"", "1", "2", "3"}:
            return {"2": "rnd_read", "3": "seq_write"}.get(choice, "seq_read")
        eprint("Choose 1–3, Enter for sequential read, or q to go back.")


def prompt_performance() -> bool | None:
    while True:
        choice = ask("Temporarily enable CPU performance settings? [y/N] (q back): ")
        if choice == "q":
            return None
        if choice in {"y", "yes"}:
            return True
        if choice in {"", "n", "no"}:
            return False
        eprint("Enter y/yes, n/no, or q to go back.")


def interactive_menu(cpu_model: str) -> None:
    while True:
        print_header(cpu_model, get_online_cpus())
        print_menu("Choose a benchmark", [
            ("1", "CPU", "Prime calculation throughput"),
            ("2", "Memory", "Sequential or random block operations"),
            ("3", "Threads", "Mutex contention and scheduler yields"),
        ], back_label="Quit")
        console.print("Ctrl+C cancels a running benchmark", style="dim")
        choice = ask("Benchmark: ")
        if choice == "q":
            return
        if choice not in {"1", "2", "3"}:
            if choice:
                eprint("Choose 1–3 or q to quit.")
            continue
        cores, workers = prompt_cores()
        if cores is None:
            continue
        duration = prompt_duration()
        if duration is None:
            continue
        mode = prompt_memory_mode() if choice == "2" else "seq_read"
        if mode is None:
            continue
        performance = prompt_performance()
        if performance is None:
            continue
        try:
            cpu_opt = optimize_cpu_performance() if performance else contextlib.nullcontext()
            with cpu_opt:
                if choice == "1":
                    run_cpu_test(workers, duration, cores)
                elif choice == "2":
                    run_memory_test(workers, duration, cores, mode)
                else:
                    run_threads_test(workers, duration, cores)
        except KeyboardInterrupt:
            eprint("Run cancelled.")
        except (OSError, RuntimeError) as exc:
            eprint(f"Error: {exc}")
        if ask("\nEnter returns to the dashboard; q quits: ") == "q":
            return


def positive_duration(value: str) -> int:
    try:
        duration = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Duration must be an integer from 1 to 86400 seconds.") from None
    if not 1 <= duration <= 86400:
        raise argparse.ArgumentTypeError("Duration must be from 1 to 86400 seconds.")
    return duration


def validate_block_size(value: str) -> str:
    match = re.fullmatch(r"([0-9]+)([KMGT]?)", value.upper())
    if not match:
        raise ValueError("Memory block size must be bytes or an integer with K/M/G/T suffix.")
    size = int(match[1]) * 1024 ** ("KMGT".index(match[2]) + 1 if match[2] else 0)
    if size < struct.calcsize("P") or size & (size - 1) or size >= 2 ** 63:
        raise ValueError("Memory block size must be a power of two, at least one native word, and below 2^63 bytes.")
    return value.upper()


def terminate(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CPU, memory and thread benchmarks with a Rich terminal dashboard.",
        epilog="Examples:\n"
               "  %(prog)s --test cpu --time 10\n"
               "  %(prog)s --test memory --memory-mode seq_write --cores 0-3\n"
               "  sudo -v && %(prog)s --test threads --performance\n"
               "Omit --test for the interactive dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        suggest_on_error=True,
    )
    parser.add_argument(
        "--test",
        choices=["cpu", "memory", "threads"],
        help="Run a specific benchmark non-interactively.",
    )
    parser.add_argument(
        "--time",
        type=positive_duration,
        default=10,
        help="Run duration in seconds, 1–86400 (default: 10).",
    )
    parser.add_argument(
        "--cores",
        help="Cores to pin the test to (e.g. 0-3, 0, or 19). Defaults to all CPUs available to this process; supports range strides (0-6:2).",
    )
    parser.add_argument(
        "--performance",
        action="store_true",
        help="Temporarily set supported governors/boost controls; CLI requires root or cached sudo credentials.",
    )
    parser.add_argument(
        "--memory-mode",
        choices=["seq_read", "rnd_read", "seq_write"],
        help="Memory mode (default: seq_read; requires --test memory).",
    )
    parser.add_argument(
        "--memory-block-size",
        help="Power-of-two block size, e.g. 4K or 64M (requires --test memory).",
    )
    parser.add_argument(
        "--memory-scope",
        choices=["global", "local"],
        help="Shared (global) or per-worker (local) buffers (requires --test memory).",
    )
    args = parser.parse_args()

    if args.memory_block_size is not None:
        try:
            args.memory_block_size = validate_block_size(args.memory_block_size)
        except ValueError as exc:
            parser.error(str(exc))
    if not args.test and any((args.cores is not None, args.performance,
                             args.memory_block_size is not None,
                             args.memory_scope is not None,
                             args.memory_mode is not None, args.time != 10)):
        parser.error("Benchmark options require --test.")
    if args.test != "memory" and any(value is not None for value in
                                    (args.memory_mode, args.memory_block_size, args.memory_scope)):
        parser.error("Memory options require --test memory.")
    if not args.test and not sys.stdin.isatty():
        parser.error("Interactive mode requires a terminal; use --test.")
    check_deps()

    cpu_model = get_cpu_model()
    online_cores = get_online_cpus()

    if args.test:
        # Non-interactive CLI
        try:
            selected = parse_cores(args.cores) if args.cores is not None else online_cores
        except ValueError as exc:
            parser.error(str(exc))
        cores = format_cores(selected)
        thread_count = len(selected)
        cpu_opt = (optimize_cpu_performance(non_interactive=True)
                   if args.performance else contextlib.nullcontext())

        print_header(cpu_model, online_cores)
        with cpu_opt:
            if args.test == "cpu":
                run_cpu_test(thread_count, args.time, cores)
            elif args.test == "memory":
                run_memory_test(
                    thread_count,
                    args.time,
                    cores,
                    mode=args.memory_mode or "seq_read",
                    block_size=args.memory_block_size,
                    scope=args.memory_scope
                )
            elif args.test == "threads":
                run_threads_test(thread_count, args.time, cores)
    else:
        # Interactive UI
        interactive_menu(cpu_model)

    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, terminate)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        eprint("\nBenchmark interrupted.")
        sys.exit(130)
    except EOFError:
        eprint("\nInput closed.")
        sys.exit(0)
    except (OSError, RuntimeError) as exc:
        eprint(f"Error: {exc}")
        sys.exit(1)

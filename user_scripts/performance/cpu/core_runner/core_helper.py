#!/usr/bin/env python3
"""Dusky CPU hotplug helper — privileged sysfs writer.

Invoked by core_runner.py (rarely by humans):

    core_helper.py --online  <cpulist>
    core_helper.py --offline <cpulist>

    cpulist: kernel grammar — N | N-N | N-N:S, comma-joined (e.g. 0-3,8-11:2)

Must run as root (typically via `sudo -n`). Linux hotplug ABI:
    echo 1 > /sys/devices/system/cpu/cpuX/online   # logical online
    echo 0 > /sys/devices/system/cpu/cpuX/online   # logical offline
CPU0 and friends without an `online` node are not hotpluggable; the helper
refuses to offline the last remaining online CPU and verifies the kernel
actually honoured every requested transition before exiting 0.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
from pathlib import Path

SYS_CPU = Path("/sys/devices/system/cpu")
MAX_CPU_ID = 1_048_575
_TOKEN = re.compile(r"^(0|[1-9][0-9]*)(?:-(0|[1-9][0-9]*)(?::([1-9][0-9]*))?)?$")


class HelperError(RuntimeError):
    pass


def parse_cpu_list(spec: str) -> list[int]:
    if not spec or spec.startswith(",") or spec.endswith(",") or ",," in spec:
        raise HelperError(f"invalid CPU list: {spec!r}")
    out: set[int] = set()
    for token in spec.split(","):
        if (match := _TOKEN.fullmatch(token)) is None:
            raise HelperError(f"invalid CPU list token: {token!r}")
        start = int(match.group(1))
        if match.group(2) is None:
            end, step = start, 1
        else:
            end = int(match.group(2))
            step = int(match.group(3)) if match.group(3) else 1
        if step < 1:
            raise HelperError(f"stride must be >= 1: {token!r}")
        if end < start:
            raise HelperError(f"inverted range: {token!r}")
        if end > MAX_CPU_ID:
            raise HelperError(f"CPU id exceeds {MAX_CPU_ID}: {token!r}")
        out.update(range(start, end + 1, step))
    if not out:
        raise HelperError(f"empty CPU list: {spec!r}")
    return sorted(out)


def read_text(path: Path) -> str:
    try:
        return text if (text := path.read_text(encoding="ascii").strip()) else ""
    except OSError as exc:
        raise HelperError(f"cannot read {path}: {exc.strerror}") from exc


def present_cpus() -> set[int]:
    return set(parse_cpu_list(read_text(SYS_CPU / "present")))


def online_cpus() -> set[int]:
    return set(parse_cpu_list(read_text(SYS_CPU / "online")))


def is_hotpluggable(cpu: int) -> bool:
    return (SYS_CPU / f"cpu{cpu}" / "online").is_file()


def write_online_state(cpu: int, value: str) -> None:
    path = SYS_CPU / f"cpu{cpu}" / "online"
    if not path.is_file():
        if value == "0":
            raise HelperError(f"CPU{cpu} has no hotplug control")
        return
    try:
        path.write_text(value + "\n", encoding="ascii")
    except OSError as exc:
        raise HelperError(f"write {path}: {exc.strerror}") from exc


def apply(cpus: list[int], want_online: bool) -> None:
    present = present_cpus()
    missing = [c for c in cpus if c not in present]
    if missing:
        raise HelperError(f"CPUs not present on this system: {missing}")

    current = online_cpus()
    if want_online:
        pending = [c for c in cpus if c not in current]
    else:
        locked = [c for c in cpus if c == 0 or not is_hotpluggable(c)]
        if locked:
            raise HelperError(f"CPUs cannot be offlined: {locked}")
        targeted = set(cpus) & current
        remaining = current - targeted
        if not remaining:
            raise HelperError("refusing to offline the last remaining online CPU")
        for unit in ("user.slice", "system.slice"):
            path = Path("/sys/fs/cgroup") / unit / "cpuset.cpus"
            if path.exists() and (raw := read_text(path)):
                allowed = set(parse_cpu_list(raw)) & current
                if allowed and not allowed - targeted:
                    raise HelperError(f"request removes every online CPU allowed by {unit}")
        pending = sorted(targeted)

    try:
        for cpu in pending:
            write_online_state(cpu, "1" if want_online else "0")
        final = online_cpus()
        failed = [c for c in cpus if (c in final) != want_online]
        if failed:
            raise HelperError(f"kernel did not honour hotplug for CPUs {failed}")
    except (HelperError, KeyboardInterrupt) as exc:
        rollback_errors = []
        for cpu in reversed(pending):
            try:
                write_online_state(cpu, "1" if cpu in current else "0")
            except HelperError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            raise HelperError(f"{exc}; rollback failed: {'; '.join(rollback_errors)}") from exc
        raise


def main(argv: list[str] | None = None) -> int:
    if os.geteuid() != 0:
        print("core_helper: must run as root (invoke via sudo)", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(
        prog="core_helper.py",
        allow_abbrev=False,
        color=False,
        description="Privileged CPU hotplug writer for dusky core_runner.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--online", metavar="CPULIST", help="bring CPUs online")
    group.add_argument("--offline", metavar="CPULIST", help="take CPUs offline")
    group.add_argument("--hold-online", metavar="CPULIST",
                       help="online CPUs until stdin closes, then restore their original state")
    args = parser.parse_args(argv)

    try:
        if args.hold_online is not None:
            selection = parse_cpu_list(args.hold_online)
            # Finish sudo's foreground command before holding CPUs. Its monitor
            # otherwise forwards terminal hangup as SIGTERM to this helper.
            try:
                if os.fork() > 0:
                    return 0
                os.setsid()
            except OSError as exc:
                raise HelperError(f"cannot detach hotplug holder: {exc}") from exc
            restore = sorted(set(selection) - online_cpus())
            def interrupted(_signum: int, _frame: object) -> None:
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, interrupted)
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            apply(selection, want_online=True)
            try:
                print("READY", flush=True)
                while sys.stdin.buffer.read(1):
                    pass
            finally:
                apply(restore, want_online=False)
                print("RESTORED", flush=True)
        else:
            selection = parse_cpu_list(args.online if args.online is not None else args.offline)
            apply(selection, want_online=args.online is not None)
    except HelperError as exc:
        print(f"core_helper: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None

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
    try:
        return set(parse_cpu_list(read_text(SYS_CPU / "present")))
    except HelperError:
        return set()


def online_cpus() -> set[int]:
    raw = read_text(SYS_CPU / "online")
    if not raw:
        return present_cpus()
    try:
        return set(parse_cpu_list(raw))
    except HelperError:
        return present_cpus()


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
        targeted = {c for c in cpus if c in current and is_hotpluggable(c)}
        remaining = current - targeted
        if not remaining:
            raise HelperError("refusing to offline the last remaining online CPU")
        if len(targeted) * 2 > len(current):
            raise HelperError(
                f"refusing mass-offline: request would take {len(targeted)} of "
                f"{len(current)} online CPUs down; this helper only restores "
                "small launch subsets"
            )
        pending = sorted(targeted)

    for cpu in pending:
        if not want_online and not is_hotpluggable(cpu):
            raise HelperError(f"CPU{cpu} is not hotpluggable")
        write_online_state(cpu, "1" if want_online else "0")

    final = online_cpus()
    failed = [c for c in cpus if (c in final) != want_online]
    if failed:
        raise HelperError(f"kernel did not honour hotplug for CPUs {failed}")


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
    args = parser.parse_args(argv)

    try:
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

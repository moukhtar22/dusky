#!/usr/bin/env python3
"""
Hardware privilege escalation module for core_runner.
Exclusively engineered for Python 3.14+
"""
import sys
import argparse
import time
import errno
from pathlib import Path

def set_core_state(cpu_id: int, state: str) -> None:
    if cpu_id == 0 and state == "0":
        print("Skipping core 0: BSP lock active.", file=sys.stderr)
        return
        
    path = Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online")
    if not path.exists():
        if state == "1":
            return
        raise FileNotFoundError(f"Hardware node {path} is inaccessible.")
            
    for attempt in range(20):
        try:
            path.write_text(state)
            return
        except PermissionError:
            sys.exit("Permission denied: Sudoers bridge failed.")
        except OSError as e:
            if state == "0" and e.errno == errno.EBUSY and attempt < 19:
                time.sleep(0.1)
                continue
            sys.exit(f"Kernel ACPI Exception: {e}")

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", type=str)
    parser.add_argument("--offline", type=str)
    args = parser.parse_args()
    
    if not args.online and not args.offline:
        parser.print_help()
        sys.exit(1)
        
    if args.online:
        for cpu in filter(None, (p.strip() for p in args.online.split(','))):
            try:
                set_core_state(int(cpu), "1")
            except ValueError:
                sys.exit(f"Invalid core ID: {cpu}")
    if args.offline:
        for cpu in filter(None, (p.strip() for p in args.offline.split(','))):
            try:
                set_core_state(int(cpu), "0")
            except ValueError:
                sys.exit(f"Invalid core ID: {cpu}")

if __name__ == "__main__":
    main()

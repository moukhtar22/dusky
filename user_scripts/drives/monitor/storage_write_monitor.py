#!/usr/bin/env python3
"""
Storage Write Monitor
Tracks per-block-device and per-process disk writes in real-time or background daemon mode.
Can run standalone or as a systemd service.
"""

import os
import sys
import time
import glob
import sqlite3
import argparse
from datetime import datetime

DB_PATH = os.path.expanduser("~/.local/share/storage_write_monitor/monitor.db")


def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS device_samples (
            timestamp REAL,
            datetime TEXT,
            device TEXT,
            sectors_written INTEGER,
            delta_mb REAL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS process_samples (
            timestamp REAL,
            datetime TEXT,
            pid INTEGER,
            comm TEXT,
            cmdline TEXT,
            write_bytes INTEGER,
            delta_mb REAL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dev_ts ON device_samples(timestamp)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_proc_ts ON process_samples(timestamp)")
    conn.commit()
    conn.close()


def sample_devices():
    devices = {}
    try:
        with open("/proc/diskstats", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 14:
                    dev = parts[2]
                    # Filter for nvme, dm, sd, loop if relevant
                    if dev.startswith("nvme") or dev.startswith("dm-") or dev.startswith("sd"):
                        sectors_written = int(parts[9])
                        devices[dev] = sectors_written
    except Exception as e:
        print(f"Error reading /proc/diskstats: {e}", file=sys.stderr)
    return devices


def sample_processes():
    processes = {}
    for p in glob.glob("/proc/[0-9]*/io"):
        try:
            pid = int(p.split("/")[2])
            with open(f"/proc/{pid}/comm", "r") as f:
                comm = f.read().strip()
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode(errors="ignore").strip()
            with open(p, "r") as f:
                wb = 0
                for line in f:
                    if line.startswith("write_bytes:"):
                        wb = int(line.split(":")[1].strip())
                        break
            processes[pid] = (comm, cmd[:120], wb)
        except Exception:
            continue
    return processes


def monitor_loop(interval_sec=60):
    init_db(DB_PATH)
    print(f"Storage Write Monitor started (interval: {interval_sec}s). Logging to {DB_PATH}")

    last_dev = sample_devices()
    last_proc = sample_processes()

    while True:
        try:
            time.sleep(interval_sec)
            now = time.time()
            dt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            curr_dev = sample_devices()
            curr_proc = sample_processes()

            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()

            # Record device deltas
            for dev, sec in curr_dev.items():
                if dev in last_dev:
                    delta_sec = sec - last_dev[dev]
                    if delta_sec > 0:
                        delta_mb = (delta_sec * 512) / (1024 * 1024)
                        cur.execute(
                            "INSERT INTO device_samples VALUES (?, ?, ?, ?, ?)",
                            (now, dt_str, dev, sec, delta_mb)
                        )
            last_dev = curr_dev

            # Record process deltas
            for pid, (comm, cmd, wb) in curr_proc.items():
                if pid in last_proc:
                    prev_wb = last_proc[pid][2]
                    delta_b = wb - prev_wb
                    if delta_b > 0:
                        delta_mb = delta_b / (1024 * 1024)
                        cur.execute(
                            "INSERT INTO process_samples VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (now, dt_str, pid, comm, cmd, wb, delta_mb)
                        )
            last_proc = curr_proc

            conn.commit()
            conn.close()

        except KeyboardInterrupt:
            print("\nMonitoring stopped.")
            break
        except Exception as e:
            print(f"Loop error: {e}", file=sys.stderr)


def show_summary(hours=1):
    if not os.path.exists(DB_PATH):
        print(f"No database found at {DB_PATH}. Run monitor first.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cutoff = time.time() - (hours * 3600)

    print(f"=== STORAGE WRITE SUMMARY (Past {hours} Hours) ===")

    print("\n--- By Block Device ---")
    cur.execute("""
        SELECT device, SUM(delta_mb) as total_mb
        FROM device_samples
        WHERE timestamp >= ?
        GROUP BY device
        ORDER BY total_mb DESC
    """, (cutoff,))
    for dev, mb in cur.fetchall():
        print(f"  {dev:15} : {mb:10.2f} MB ({mb/1024:6.2f} GB)")

    print("\n--- Top 15 Processes by Writes ---")
    cur.execute("""
        SELECT comm, cmdline, SUM(delta_mb) as total_mb
        FROM process_samples
        WHERE timestamp >= ?
        GROUP BY comm, cmdline
        ORDER BY total_mb DESC
        LIMIT 15
    """, (cutoff,))
    for comm, cmd, mb in cur.fetchall():
        display_cmd = cmd if cmd else f"[{comm}]"
        print(f"  {mb:10.2f} MB | {comm:15} | {display_cmd[:65]}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Storage Write Monitor")
    parser.add_argument("--daemon", action="store_true", help="Run background monitor loop")
    parser.add_argument("--interval", type=int, default=60, help="Sampling interval in seconds (default: 60)")
    parser.add_argument("--summary", type=float, default=0, help="Print summary of last N hours")
    args = parser.parse_args()

    if args.summary > 0:
        show_summary(args.summary)
    else:
        monitor_loop(args.interval)

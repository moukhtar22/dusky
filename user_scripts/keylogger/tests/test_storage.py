"""SQLite store: schema, pragmas, v1->v2 migration, writer thread, concurrency."""
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from dusky_keylogger.storage import EventWriter, KeyStore, row_from_press


def make_press(ts_us, key="KEY_A", char="a", kind="printable"):
    return type(
        "P",
        (),
        {"key_name": key, "keycode": 30, "char": char, "kind": kind,
         "device": "T", "ts_us": ts_us},
    )()


@pytest.fixture()
def store(tmp_path):
    s = KeyStore(tmp_path / "keys.db")
    s.init_db()
    return s


def test_schema_and_permissions(store, tmp_path):
    db = store.path
    assert oct(os.stat(db).st_mode & 0o777) == "0o600"
    assert oct(os.stat(tmp_path).st_mode & 0o777) == "0o700"
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA application_id").fetchone()[0] == 0x44534B59
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='events'").fetchone()[0]
    assert "STRICT" in sql.upper()
    conn.close()


def test_roundtrip_and_local_parts(store):
    ts_ms = int(datetime.now().timestamp() * 1000)
    store.insert_many([row_from_press(make_press(ts_ms * 1000))])
    r = store.recent(1)[0]
    lt = time.localtime(r.ts_ms / 1000)
    assert r.local_date == time.strftime("%Y-%m-%d", lt)
    assert (r.hour, r.minute) == (lt.tm_hour, lt.tm_min)
    assert r.weekday == lt.tm_wday + 1
    assert r.minute_of_day == lt.tm_hour * 60 + lt.tm_min


def test_v1_migration(tmp_path):
    db = tmp_path / "v1.db"
    c = sqlite3.connect(db)
    c.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_ms INTEGER NOT NULL, date TEXT NOT NULL, hour INTEGER NOT NULL,
            minute INTEGER NOT NULL, weekday INTEGER NOT NULL, key_name TEXT NOT NULL,
            keycode INTEGER NOT NULL, char TEXT, kind TEXT NOT NULL,
            device TEXT NOT NULL DEFAULT ''
        );
        """
    )
    c.execute(
        "INSERT INTO events (ts_ms, date, hour, minute, weekday, key_name, keycode,"
        " char, kind, device) VALUES (1700000000000,'2023-11-14',10,30,2,'KEY_B',48,'b','printable','')"
    )
    c.commit()
    c.close()

    s = KeyStore(db)
    s.init_db()
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    row = conn.execute("SELECT ts_us, local_date, minute_of_day FROM events").fetchone()
    n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert {"ts_us", "local_date", "minute_of_day"} <= cols
    assert n == 1
    assert row[0] == 1700000000000 * 1000
    assert row[1] == "2023-11-14"
    assert row[2] == 630


def test_reader_unblocked_by_open_write_txn(store):
    now_ms = int(datetime.now().timestamp() * 1000)
    store.insert_many([row_from_press(make_press(now_ms * 1000))])
    wconn = store.open_writer()
    wconn.execute("BEGIN")
    wconn.execute(
        "INSERT INTO events (ts_us, ts_ms, local_date, hour, minute, minute_of_day,"
        " weekday, key_name, keycode, char, kind, device)"
        " VALUES (1, 1, '2026-01-01', 0, 0, 0, 1, 'X', 30, NULL, 'other', '')"
    )
    try:
        assert store.total() == 1  # snapshot read, no block
    finally:
        wconn.rollback()
        wconn.close()


def test_writer_thread_batches_and_flushes(store):
    ew = EventWriter(store, queue_size=8)
    ew.start()
    base = int(datetime.now().timestamp() * 1e9)
    rows = [row_from_press(make_press(base + i)) for i in range(200)]
    for i in range(0, len(rows), 37):
        assert ew.submit(rows[i : i + 37]) is True
    ew.close(timeout=8)
    assert ew.written == 200
    assert ew.last_error is None
    assert store.total() == 200


def test_iter_between_ordered(store):
    base = int(datetime.now().timestamp() * 1e6)
    store.insert_many([row_from_press(make_press(base + i)) for i in range(50)])
    start = datetime.now() - timedelta(hours=1)
    end = datetime.now() + timedelta(hours=1)
    got = list(store.iter_between(start, end))
    assert len(got) == 50
    assert [g.ts_us for g in got] == sorted(g.ts_us for g in got)


def test_max_id_change_detector(store):
    assert store.max_id() == 0
    base = int(datetime.now().timestamp() * 1e9)
    store.insert_many([row_from_press(make_press(base))])
    assert store.max_id() >= 1

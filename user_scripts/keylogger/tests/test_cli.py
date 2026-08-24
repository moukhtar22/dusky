"""CLI end-to-end in an isolated HOME + data dir (no root, no devices)."""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from dusky_keylogger import cli


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    data = tmp_path / "data"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DUSKY_KEYLOGGER_DATA_DIR", str(data))
    monkeypatch.setenv("DUSKY_KEYLOGGER_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("DUSKY_TRANSCRIPT_DIR", str(tmp_path / "tx"))
    monkeypatch.delenv("DUSKY_TRANSCRIPT_FORMAT", raising=False)
    return tmp_path, data


def seed(store, text="Hi\n"):
    from dusky_keylogger import keycodes as kc

    rows = []
    base = int(datetime.now().timestamp() * 1e6)
    seq = [(kc.KEY_H, "H"), (kc.KEY_I, "i"), (kc.KEY_ENTER, None)]
    for i, (code, ch) in enumerate(seq):
        rows.append(
            cli.row_from_press(
                type(
                    "P",
                    (),
                    {"key_name": kc.key_name(code), "keycode": code, "char": ch,
                     "kind": kc.classify_key(code), "device": "T",
                     "ts_us": base + i * 1000},
                )()
            )
        )
    store.insert_many(rows)


def test_seed_and_stats_json(sandbox):
    tmp_path, data = sandbox
    assert cli.main(["seed", "--days", "3", "--seed", "7"]) == 0
    db = data / "keys.db"
    assert db.exists()
    conn = sqlite3.connect(db)
    n1 = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    assert n1 > 0
    rc = cli.main(["stats", "--period", "week", "--json"])
    assert rc == 0


def test_text_export_text_and_markdown(sandbox, capsys):
    tmp_path, _ = sandbox
    store = cli._get_store(type("A", (), {"data_dir": None})())
    seed(store)

    assert cli.main(["text", "--period", "today", "--format", "text"]) == 0
    txts = list((tmp_path / "tx").glob("dusky-typed-today-*.txt"))
    assert len(txts) == 1
    assert oct(os.stat(txts[0]).st_mode & 0o777) == "0o600"
    assert "Hi\n" in txts[0].read_text()

    assert cli.main(["text", "--period", "today", "--format", "markdown"]) == 0
    mds = list((tmp_path / "tx").glob("dusky-typed-today-*.md"))
    assert len(mds) == 1
    body = mds[0].read_text()
    assert "# Dusky Keylogger" in body and "```text" in body
    # header references the real DB path, not a hardcoded one
    assert str(store.path) in body


def test_transcript_symlink_refused(sandbox):
    """O_NOFOLLOW: a pre-planted symlink at the output path must not be followed."""
    tmp_path, _ = sandbox
    store = cli._get_store(type("A", (), {"data_dir": None})())
    seed(store)
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch")
    out = tmp_path / "tx"
    out.mkdir(exist_ok=True)
    link = out / f"dusky-typed-today-{datetime.now():%Y-%m-%d}.txt"
    link.symlink_to(victim)
    rc = cli.main(["text", "--period", "today", "--format", "text"])
    assert rc != 0
    assert victim.read_text() == "do not touch"


def test_out_flag_wins(sandbox):
    tmp_path, _ = sandbox
    store = cli._get_store(type("A", (), {"data_dir": None})())
    seed(store)
    outp = tmp_path / "custom" / "out.txt"
    assert cli.main(["text", "--period", "today", "--out", str(outp)]) == 0
    assert outp.exists()


def test_v1_db_migrated_by_cli(sandbox, tmp_path):
    _, data = sandbox
    data.mkdir(parents=True, exist_ok=True)
    db = data / "keys.db"
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
        PRAGMA user_version = 0;
        """
    )
    c.commit()
    c.close()
    assert cli.main(["stats", "--period", "today"]) == 0
    c = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    uv = c.execute("PRAGMA user_version").fetchone()[0]
    cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
    c.close()
    assert uv >= 2 and "local_date" in cols


def test_corrupt_config_tolerated(sandbox):
    cfg = Path(os.environ["DUSKY_KEYLOGGER_CONFIG"])
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{not json")
    assert cli.main(["status"]) == 0


def test_events_and_status(sandbox):
    store = cli._get_store(type("A", (), {"data_dir": None})())
    seed(store)
    assert cli.main(["events", "--limit", "5"]) == 0
    assert cli.main(["status"]) == 0

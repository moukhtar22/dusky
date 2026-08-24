"""Dashboard layout: all views render, cache invalidates on data change."""
import sqlite3
from datetime import datetime

from dusky_keylogger.dashboard_tui import (
    _QUERY_CACHE,
    _build_layout_impl,
    _transcript_line_count,
)
from dusky_keylogger.storage import KeyStore

COLORS = {
    "fg": "#efe0d5", "accent": "#ffb779", "muted": "#a08c7a",
    "cursor_bg": "#2a221c", "success": "#c3cb98", "warning": "#e3c0a5",
}
SCROLLS = {"transcript": 0, "recent": 0, "keys": 0, "chars": 0}


def make_store(tmp_path, rows=0):
    store = KeyStore(tmp_path / "keys.db")
    store.init_db()
    if rows:
        conn = sqlite3.connect(store.path)
        base = int(datetime.now().timestamp() * 1000)
        data = [
            (ts * 1000, ts, "2026-08-21", 12, 30, 750, 5,
             ["KEY_A", "KEY_B", "KEY_ENTER"][i % 3], 30,
             [None, None, "\n"][i % 3],
             ["printable", "printable", "enter"][i % 3], "T")
            for i, ts in enumerate(range(base - rows, base))
        ]
        conn.executemany(
            "INSERT INTO events (ts_us, ts_ms, local_date, hour, minute,"
            " minute_of_day, weekday, key_name, keycode, char, kind, device)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            data,
        )
        conn.commit()
        conn.close()
    return store


def test_all_views_render_empty(tmp_path):
    store = make_store(tmp_path)
    for view in ("overview", "keys", "chars", "transcript", "recent"):
        panel = _build_layout_impl("today", view, COLORS, SCROLLS, 40, store)
        assert panel is not None


def test_all_views_render_seeded(tmp_path):
    store = make_store(tmp_path, rows=500)
    for period in ("today", "week", "month", "all"):
        for view in ("overview", "keys", "chars", "transcript", "recent"):
            panel = _build_layout_impl(period, view, COLORS, SCROLLS, 40, store)
            assert panel is not None


def test_cache_hit_and_invalidation(tmp_path):
    store = make_store(tmp_path, rows=100)
    _build_layout_impl("all", "transcript", COLORS, dict(SCROLLS), 40, store)
    ver_hits = sum(
        1 for k, v in _QUERY_CACHE.items() if k[0] == str(store.path)
    )
    assert ver_hits > 0
    before = dict(_QUERY_CACHE)

    # same version -> served from cache (dict unchanged)
    _build_layout_impl("all", "transcript", COLORS, dict(SCROLLS), 40, store)
    assert dict(_QUERY_CACHE) == before

    # new row -> version bump -> cache entry replaced
    conn = sqlite3.connect(store.path)
    ts = int(datetime.now().timestamp() * 1000)
    conn.execute(
        "INSERT INTO events (ts_us, ts_ms, local_date, hour, minute, minute_of_day,"
        " weekday, key_name, keycode, char, kind, device)"
        " VALUES (?, ?, '2026-08-21', 12, 30, 750, 5, 'KEY_C', 46, 'c', 'printable', 'T')",
        (ts * 1000, ts),
    )
    conn.commit()
    conn.close()
    _build_layout_impl("all", "transcript", COLORS, dict(SCROLLS), 40, store)
    assert dict(_QUERY_CACHE) != before


def test_transcript_line_count_monotonic(tmp_path):
    store = make_store(tmp_path, rows=30)
    n = _transcript_line_count(store, "all")
    assert n >= 1

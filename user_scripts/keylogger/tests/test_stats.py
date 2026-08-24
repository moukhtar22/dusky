"""Period range boundaries and summarize math."""
from datetime import datetime

import pytest

from dusky_keylogger.stats import period_range


def d(y, m, day, hh=0, mm=0):
    return datetime(y, m, day, hh, mm)


def test_today():
    s, e = period_range("today", d(2026, 8, 21, 15, 30))
    assert (s, e) == (d(2026, 8, 21), d(2026, 8, 22))


def test_iso_week():
    s, e = period_range("week", d(2026, 8, 21))  # Friday
    assert (s, e) == (d(2026, 8, 17), d(2026, 8, 24))
    s, _ = period_range("week", d(2026, 8, 23))  # Sunday
    assert s == d(2026, 8, 17)


def test_month_rollover():
    _, e = period_range("month", d(2026, 12, 31))
    assert e == d(2027, 1, 1)
    _, e = period_range("month", d(2026, 2, 28))
    assert e == d(2026, 3, 1)


def test_all_and_invalid():
    s, e = period_range("all")
    assert s.timestamp() == 0.0
    assert e.year == 9999
    with pytest.raises(ValueError):
        period_range("fortnight")

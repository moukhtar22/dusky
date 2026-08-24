"""Statistical summaries over the keystroke store.

Totals, printable/backspace/modifier breakdowns, keys-per-minute and
estimated WPM, top keys, and hourly activity -- for today, this ISO
week, this month, or the full history.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from . import keycodes as kc
from .storage import KeyStore

WPM_DIVISOR = 5

_PERIODS = ("today", "week", "month", "all")


def period_range(
    period: str, now: datetime | None = None
) -> tuple[datetime, datetime]:
    """Return [start, end) for a named period.

    * today  -- from local midnight
    * week   -- from the most recent Monday (ISO week)
    * month  -- from the 1st of the current month
    * all    -- Unix epoch to a far-future sentinel
    """
    now = now or datetime.now()
    if period == "today":
        start = datetime.combine(now.date(), time.min)
        return start, start + timedelta(days=1)
    if period == "week":
        start = datetime.combine(
            now.date() - timedelta(days=now.isoweekday() - 1), time.min
        )
        return start, start + timedelta(days=7)
    if period == "month":
        start = datetime.combine(now.date().replace(day=1), time.min)
        # Calendar-aware: advance one month properly (avoids the 32-day trick's edge on month-end).
        year, month = start.year, start.month
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        return start, end
    if period == "all":
        return datetime.fromtimestamp(0), datetime(9999, 12, 31)
    raise ValueError(f"Unknown period {period!r}: expected one of {', '.join(_PERIODS)}")


@dataclass(slots=True)
class PeriodStats:
    """Aggregate statistics for one period."""

    period: str
    start: datetime
    end: datetime

    total_keys: int = 0
    printable: int = 0
    backspace: int = 0
    delete: int = 0
    enter: int = 0
    tab: int = 0
    escape: int = 0
    modifiers: int = 0
    navigation: int = 0
    function: int = 0
    other: int = 0

    active_minutes: int = 0
    top_keys: list[tuple[str, int]] = field(default_factory=list)
    top_chars: list[tuple[str, int]] = field(default_factory=list)

    @property
    def keys_per_minute(self) -> float:
        if self.active_minutes <= 0:
            return 0.0
        return self.total_keys / self.active_minutes

    @property
    def words_per_minute(self) -> float:
        if self.active_minutes <= 0:
            return 0.0
        return (self.printable / WPM_DIVISOR) / self.active_minutes

    @property
    def backspace_ratio(self) -> float:
        if self.total_keys <= 0:
            return 0.0
        return self.backspace / self.total_keys


def summarize(
    store: KeyStore, period: str = "today", limit_keys: int = 12
) -> PeriodStats:
    start, end = period_range(period)
    kinds = store.kind_totals(start, end)
    stats = PeriodStats(period=period, start=start, end=end)
    stats.total_keys = sum(kinds.values())
    stats.printable = kinds.get(kc.KIND_PRINTABLE, 0)
    stats.backspace = kinds.get(kc.KIND_BACKSPACE, 0)
    stats.delete = kinds.get(kc.KIND_DELETE, 0)
    stats.enter = kinds.get(kc.KIND_ENTER, 0)
    stats.tab = kinds.get(kc.KIND_TAB, 0)
    stats.escape = kinds.get(kc.KIND_ESCAPE, 0)
    stats.modifiers = kinds.get(kc.KIND_MODIFIER, 0)
    stats.navigation = kinds.get(kc.KIND_NAVIGATION, 0)
    stats.function = kinds.get(kc.KIND_FUNCTION, 0)
    stats.other = kinds.get(kc.KIND_OTHER, 0)
    stats.active_minutes = store.active_minutes(start, end)
    stats.top_keys = store.top_keys(limit_keys, start, end)
    stats.top_chars = store.printable_frequency(limit_keys, start, end)
    return stats


def card_totals(store: KeyStore) -> dict[str, int]:
    """Today / week / month / all-time totals in a single table scan."""
    now = datetime.now()
    ranges = [period_range(p, now) for p in _PERIODS]
    counts = store.count_ranges(ranges)
    return dict(zip(_PERIODS, counts, strict=True))


def daily_series(store: KeyStore, days: int = 30) -> list[tuple[str, int]]:
    return store.daily_totals(days)


def hourly_series(store: KeyStore, day: str | None = None) -> list[tuple[int, int]]:
    if day is None:
        day = datetime.now().strftime("%Y-%m-%d")
    return store.hourly_totals(day)

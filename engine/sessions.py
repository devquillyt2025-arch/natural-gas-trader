"""MCX session hours and the EIA weekly storage report calendar. All pure.

Official schedule (check it before every holiday week): https://ir.eia.gov/ngs/schedule.html
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import IST, Limits

NY = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 0)

# Published alternate releases, keyed by the regular Thursday they replace. Times are US Eastern.
# Copied from https://ir.eia.gov/ngs/schedule.html (read November 2026).
OFFICIAL_SHIFTS: dict[date, datetime] = {
    date(2025, 1, 2): datetime(2025, 1, 3, 10, 30),
    date(2025, 1, 9): datetime(2025, 1, 8, 12, 0),
    date(2025, 6, 19): datetime(2025, 6, 18, 12, 0),
    date(2025, 11, 13): datetime(2025, 11, 14, 10, 30),
    date(2025, 11, 27): datetime(2025, 11, 26, 12, 0),
    date(2025, 12, 25): datetime(2025, 12, 29, 12, 0),
    date(2026, 1, 1): datetime(2025, 12, 31, 12, 0),
    date(2026, 11, 12): datetime(2026, 11, 13, 10, 30),
    date(2026, 11, 26): datetime(2026, 11, 25, 12, 0),
    date(2027, 11, 11): datetime(2027, 11, 10, 12, 0),
    date(2027, 11, 25): datetime(2027, 11, 24, 12, 0),
}
# Holiday shifts after this date have not been published/copied; the UI warns past it.
VERIFIED_UNTIL = date(2027, 12, 31)


def thanksgiving(year: int) -> date:
    first = date(year, 11, 1)
    return first + timedelta(days=(3 - first.weekday()) % 7 + 21)  # 4th Thursday


class EiaCalendar:
    def __init__(self, overrides: Mapping[date, datetime | None] | None = None):
        self.overrides = dict(overrides or {})
        self.verified_until = VERIFIED_UNTIL

    def release_for(self, thursday: date) -> datetime | None:
        """Actual release (in IST) for the week whose regular day is `thursday`."""
        naive: datetime | None
        if thursday in self.overrides:
            naive = self.overrides[thursday]
        elif thursday in OFFICIAL_SHIFTS:
            naive = OFFICIAL_SHIFTS[thursday]
        elif thursday == thanksgiving(thursday.year):
            naive = datetime.combine(thursday - timedelta(days=1), time(12, 0))
        else:
            naive = datetime.combine(thursday, time(10, 30))
        return None if naive is None else naive.replace(tzinfo=NY).astimezone(IST)

    def releases_between(self, start: datetime, end: datetime) -> list[datetime]:
        d = start.astimezone(NY).date() - timedelta(days=14)
        d += timedelta(days=(3 - d.weekday()) % 7)
        last = end.astimezone(NY).date() + timedelta(days=14)
        out: list[datetime] = []
        while d <= last:
            r = self.release_for(d)
            if r is not None and start <= r <= end:
                out.append(r)
            d += timedelta(days=7)
        return sorted(out)

    def next_release(self, now: datetime) -> datetime:
        upcoming = self.releases_between(now, now + timedelta(days=45))
        if not upcoming:
            raise ValueError("no EIA release found in the next 45 days; check EIA_OVERRIDES")
        return upcoming[0]

    def in_blackout(self, now: datetime, before_min: int, after_min: int) -> bool:
        lo, hi = now - timedelta(minutes=after_min), now + timedelta(minutes=before_min)
        return bool(self.releases_between(lo, hi))

    def is_verified(self, when: datetime) -> bool:
        return when.date() <= self.verified_until


# ------------------------------------------------------------------------------------ MCX
def market_close(day: date) -> time:
    """Non-agri MCX close: 23:30 while the US is on daylight saving, 23:55 in US winter."""
    us_noon = datetime.combine(day, time(12, 0), tzinfo=NY)
    return time(23, 30) if us_noon.dst() else time(23, 55)


def is_trading_day(day: date, no_trade_dates: frozenset[date] = frozenset()) -> bool:
    return day.weekday() < 5 and day not in no_trade_dates


def market_open(now: datetime, no_trade_dates: frozenset[date] = frozenset()) -> bool:
    local = now.astimezone(IST)
    return is_trading_day(local.date(), no_trade_dates) and MARKET_OPEN <= local.time() < market_close(local.date())


def at(day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=IST)


def in_entry_window(now: datetime, limits: Limits, no_trade_dates: frozenset[date] = frozenset()) -> bool:
    local = now.astimezone(IST)
    day = local.date()
    return is_trading_day(day, no_trade_dates) and at(day, limits.entry_start) <= local <= at(day, limits.no_entry_after)


def chart_windows(
    calendar: EiaCalendar,
    limits: Limits,
    start: datetime,
    end: datetime,
    no_trade_dates: frozenset[date] = frozenset(),
) -> list[dict[str, str]]:
    """Spans for the UI to shade: EIA blackout and the parts of the session closed to new entries."""
    out: list[dict[str, str]] = []

    def add(kind: str, a: datetime, b: datetime) -> None:
        out.append({"kind": kind, "start": a.isoformat(timespec="minutes"), "end": b.isoformat(timespec="minutes")})

    day = start.astimezone(IST).date()
    while day <= end.astimezone(IST).date():
        if is_trading_day(day, no_trade_dates):
            add("no_entry", at(day, MARKET_OPEN), at(day, limits.entry_start))
            add("no_entry", at(day, limits.no_entry_after), at(day, market_close(day)))
        day += timedelta(days=1)
    for r in calendar.releases_between(start, end):
        add("eia", r - timedelta(minutes=limits.eia_before_min), r + timedelta(minutes=limits.eia_after_min))
    return out

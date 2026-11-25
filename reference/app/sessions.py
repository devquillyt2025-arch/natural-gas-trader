"""Trading-session and EIA-storage-report calendar helpers (all pure functions)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import IST, Settings, hhmm

NY = ZoneInfo("America/New_York")


def _thanksgiving(year: int) -> date:
    d = date(year, 11, 1)
    thursdays = [d + timedelta(days=i) for i in range(30) if (d + timedelta(days=i)).weekday() == 3]
    return thursdays[3]


def eia_release_times(around: datetime) -> list[datetime]:
    """EIA weekly storage report: Thursday 10:30 ET. In Thanksgiving week it moves to
    Wednesday 12:00 ET (per EIA's holiday schedule for 2021-2025). Other holiday shifts
    (Christmas / New Year) are not modelled: check https://ir.eia.gov/ngs/schedule.html
    and keep trading off around them."""
    ny = around.astimezone(NY)
    out = []
    for off in range(-8, 9):
        d = ny.date() + timedelta(days=off)
        if d.weekday() != 3:
            continue
        if d == _thanksgiving(d.year):
            out.append(datetime.combine(d - timedelta(days=1), time(12, 0), tzinfo=NY).astimezone(IST))
        else:
            out.append(datetime.combine(d, time(10, 30), tzinfo=NY).astimezone(IST))
    return sorted(out)


def next_eia(now: datetime) -> datetime:
    for r in eia_release_times(now):
        if r >= now:
            return r
    return eia_release_times(now + timedelta(days=7))[-1]


def in_eia_blackout(now: datetime, before_min: int, after_min: int) -> bool:
    for r in eia_release_times(now):
        if r - timedelta(minutes=before_min) <= now <= r + timedelta(minutes=after_min):
            return True
    return False


def _at(now: datetime, hm: str) -> datetime:
    h, m = hhmm(hm)
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def market_open(now: datetime, s: Settings) -> bool:
    """MCX trades Mon-Fri. The engine runs until SESSION_END, a little after FORCE_FLAT, so the flatten always fires."""
    return now.weekday() < 5 and now.replace(hour=9, minute=0, second=0, microsecond=0) <= now <= _at(now, s.session_end)


def entry_allowed(now: datetime, s: Settings) -> bool:
    return (
        now.weekday() < 5
        and _at(now, s.entry_start) <= now <= _at(now, s.no_entry_after)
        and not in_eia_blackout(now, s.eia_before_min, s.eia_after_min)
    )


def must_flatten(now: datetime, s: Settings) -> bool:
    return now >= _at(now, s.force_flat)

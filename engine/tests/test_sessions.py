"""EIA calendar and MCX session helpers."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from engine import sessions
from engine.config import IST, Limits, parse_eia_overrides
from engine.sessions import NY, EiaCalendar

CAL = EiaCalendar()


def ist(y: int, m: int, d: int, hh: int = 9, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def md(t: datetime) -> tuple[int, int, int, int]:
    return (t.month, t.day, t.hour, t.minute)


def test_regular_thursday_in_us_winter_is_2100_ist() -> None:
    assert md(CAL.next_release(ist(2026, 12, 10))) == (12, 10, 21, 0)


def test_regular_thursday_in_us_summer_is_2000_ist() -> None:
    assert md(CAL.next_release(ist(2026, 7, 16))) == (7, 16, 20, 0)


def test_dst_transition_changes_the_ist_time() -> None:
    # US DST ends Sun 1 Nov 2026: Thu 29 Oct is 20:00 IST, Thu 5 Nov is 21:00 IST
    assert md(CAL.next_release(ist(2026, 10, 29))) == (10, 29, 20, 0)
    assert md(CAL.next_release(ist(2026, 11, 5))) == (11, 5, 21, 0)
    # US DST starts Sun 14 Mar 2027
    assert md(CAL.next_release(ist(2027, 3, 11))) == (3, 11, 21, 0)
    assert md(CAL.next_release(ist(2027, 3, 18))) == (3, 18, 20, 0)


def test_thanksgiving_week_2026_is_wednesday_noon_et() -> None:
    r = CAL.next_release(ist(2026, 11, 23))
    assert md(r) == (11, 25, 22, 30)
    assert r.astimezone(NY).strftime("%a %H:%M") == "Wed 12:00"


def test_thanksgiving_thursday_itself_has_no_release() -> None:
    assert md(CAL.next_release(ist(2026, 11, 26))) == (12, 3, 21, 0)


def test_thanksgiving_rule_applies_beyond_the_published_table() -> None:
    # Thanksgiving 2030 = 28 Nov -> Wed 27 Nov 12:00 ET
    r = CAL.next_release(ist(2030, 11, 25))
    assert md(r) == (11, 27, 22, 30)


def test_published_veterans_day_shifts() -> None:
    r = CAL.next_release(ist(2026, 11, 9))  # Fri 13 Nov 10:30 ET
    assert md(r) == (11, 13, 21, 0)
    r = CAL.next_release(ist(2027, 11, 8))  # Wed 10 Nov 12:00 ET
    assert md(r) == (11, 10, 22, 30)


def test_published_christmas_2025_shift_moves_into_next_week() -> None:
    r = CAL.next_release(ist(2025, 12, 22))  # Thu 25 Dec -> Mon 29 Dec 12:00 ET
    assert md(r) == (12, 29, 22, 30)
    assert md(CAL.next_release(r + timedelta(minutes=1))) == (12, 31, 22, 30)  # New Year's week


def test_next_release_is_inclusive_of_now() -> None:
    r = CAL.next_release(ist(2026, 12, 10))
    assert CAL.next_release(r) == r
    assert CAL.next_release(r + timedelta(seconds=1)) == r + timedelta(days=7)


def test_env_override_moves_and_skips_a_release() -> None:
    cal = EiaCalendar(parse_eia_overrides("2026-12-24=2026-12-28T12:00, 2026-12-31=none"))
    assert md(cal.next_release(ist(2026, 12, 21))) == (12, 28, 22, 30)
    assert md(cal.next_release(ist(2026, 12, 29))) == (1, 7, 21, 0)


@pytest.mark.parametrize("bad", ["2026-12-23=2026-12-28T12:00", "garbage", "2026-12-24=nonsense"])
def test_bad_override_is_an_error_not_silently_ignored(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_eia_overrides(bad)  # 23 Dec 2026 is a Wednesday: keys must be the regular Thursday


def test_blackout_window() -> None:
    r = CAL.next_release(ist(2026, 12, 10))
    assert CAL.in_blackout(r - timedelta(minutes=20), 20, 30)
    assert CAL.in_blackout(r + timedelta(minutes=30), 20, 30)
    assert not CAL.in_blackout(r - timedelta(minutes=20, seconds=1), 20, 30)
    assert not CAL.in_blackout(r + timedelta(minutes=30, seconds=1), 20, 30)


def test_verified_until_and_unverified_flag() -> None:
    assert CAL.verified_until == date(2027, 12, 31)
    assert CAL.is_verified(ist(2027, 6, 1))
    assert not CAL.is_verified(ist(2028, 1, 6))


def test_mcx_close_is_2330_during_us_dst_and_2355_otherwise() -> None:
    assert sessions.market_close(date(2026, 7, 1)) == time(23, 30)
    assert sessions.market_close(date(2026, 12, 1)) == time(23, 55)


@pytest.mark.parametrize(
    "when,open_",
    [
        (ist(2026, 11, 24, 8, 59), False),
        (ist(2026, 11, 24, 9, 0), True),
        (ist(2026, 11, 24, 23, 54), True),  # US winter: open till 23:55
        (ist(2026, 7, 14, 23, 30), False),  # US summer: closed at 23:30
        (ist(2026, 11, 28, 12, 0), False),  # Saturday
    ],
)
def test_market_open(when: datetime, open_: bool) -> None:
    assert sessions.market_open(when) is open_


def test_market_closed_on_configured_holiday() -> None:
    assert not sessions.market_open(ist(2026, 11, 24, 12), frozenset({date(2026, 11, 24)}))


def test_chart_windows_cover_entry_closures_and_eia() -> None:
    w = sessions.chart_windows(CAL, Limits(), ist(2026, 11, 23, 0), ist(2026, 11, 25, 23, 59))
    eia = [x for x in w if x["kind"] == "eia"]
    assert len(eia) == 1
    assert eia[0]["start"].startswith("2026-11-25T22:10") and eia[0]["end"].startswith("2026-11-25T23:00")
    closed = [x for x in w if x["kind"] == "no_entry"]
    assert any(x["start"].startswith("2026-11-24T22:30") for x in closed)
    assert any(x["start"].startswith("2026-11-24T09:00") and x["end"].startswith("2026-11-24T09:30") for x in closed)

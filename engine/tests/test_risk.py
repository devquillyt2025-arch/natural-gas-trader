"""Risk layer: every rule, both directions, at the boundaries. Written before engine/risk.py."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta
from typing import Any

import pytest

from engine import risk
from engine.config import IST, Limits
from engine.models import Decision, Position
from engine.sessions import EiaCalendar

L = Limits()
CAL = EiaCalendar()
FORCE_FLAT = time(23, 10)
# A plain Tuesday. The only EIA release that week is Wed 25 Nov 22:30 IST (Thanksgiving week).
TUE = datetime(2026, 11, 24, 12, 0, tzinfo=IST)


def ctx(**kw: Any) -> risk.RiskContext:
    base: dict[str, Any] = dict(
        now=TUE, ltp=300.0, atr=1.0, multiplier=250, position=None,
        daily_pnl=0.0, trades_today=0, last_loss_at=None,
    )
    base.update(kw)
    return risk.RiskContext(**base)


def long(**kw: Any) -> Decision:
    base: dict[str, Any] = dict(action="ENTER_LONG", lots=1, stop_loss=298.0, target=304.0, confidence=0.8)
    base.update(kw)
    return Decision(**base)


def short(**kw: Any) -> Decision:
    base: dict[str, Any] = dict(action="ENTER_SHORT", lots=1, stop_loss=302.0, target=296.0, confidence=0.8)
    base.update(kw)
    return Decision(**base)


def pos(side: str = "LONG", sl: float | None = None, **kw: Any) -> Position:
    stop = sl if sl is not None else (298.0 if side == "LONG" else 302.0)
    base: dict[str, Any] = dict(
        id="p1", side=side, lots=1, multiplier=250, entry=300.0, stop_loss=stop,
        target=304.0 if side == "LONG" else 296.0, initial_stop=stop, opened_at=TUE.isoformat(),
        instrument_key="SIM|X", mode="paper",
    )
    base.update(kw)
    return Position(**base)


def check(d: Decision, c: risk.RiskContext, limits: Limits = L) -> risk.Verdict:
    return risk.validate(limits, d, c, CAL)


def rejected(d: Decision, c: risk.RiskContext, code: str, limits: Limits = L) -> None:
    v = check(d, c, limits)
    assert not v.ok, f"expected rejection {code}, got approval: {v.reason}"
    assert v.code == code, f"expected {code}, got {v.code}: {v.reason}"


def approved(d: Decision, c: risk.RiskContext, limits: Limits = L) -> risk.Verdict:
    v = check(d, c, limits)
    assert v.ok, f"{v.code}: {v.reason}"
    return v


# --------------------------------------------------------------------------- basics
@pytest.mark.parametrize("d", [long(), short()])
def test_good_entries_are_approved(d: Decision) -> None:
    v = approved(d, ctx())
    assert v.code == "approved" and v.decision.lots == 1


def test_hold_is_always_ok_even_when_everything_is_blocked() -> None:
    c = ctx(kill=True, orphan=True, safety_halt=True, data_fresh=False, daily_pnl=-99999)
    assert approved(Decision(action="HOLD"), c).code == "hold"


def test_unknown_action_rejected() -> None:
    rejected(Decision(action="YOLO"), ctx(), "unknown_action")


def test_exit_needs_a_position() -> None:
    rejected(Decision(action="EXIT"), ctx(), "no_position")
    assert approved(Decision(action="EXIT"), ctx(position=pos())).code == "exit"


@pytest.mark.parametrize("flag", ["kill", "orphan", "safety_halt"])
def test_exit_is_allowed_under_every_entry_block(flag: str) -> None:
    approved(Decision(action="EXIT"), ctx(position=pos(), **{flag: True}))


def test_exit_allowed_outside_window_with_stale_data_and_daily_cap() -> None:
    c = ctx(position=pos(), now=TUE.replace(hour=23, minute=5), data_fresh=False,
            control_ok=False, daily_pnl=-5000)
    approved(Decision(action="EXIT"), c)


# --------------------------------------------------------------------------- entry blocks
@pytest.mark.parametrize("d", [long(), short()])
@pytest.mark.parametrize(
    "kw,code",
    [
        ({"kill": True}, "kill_switch"),
        ({"orphan": True}, "orphan_position"),
        ({"safety_halt": True}, "safety_halt"),
        ({"data_fresh": False}, "stale_data"),
        ({"control_ok": False}, "control_channel_down"),
        ({"daily_halt_latched": True}, "daily_loss_cap"),
    ],
)
def test_entry_blocking_flags(d: Decision, kw: dict[str, Any], code: str) -> None:
    rejected(d, ctx(**kw), code)


def test_no_pyramiding_or_flipping() -> None:
    rejected(long(), ctx(position=pos("LONG")), "already_in_position")
    rejected(short(), ctx(position=pos("LONG")), "already_in_position")
    rejected(long(), ctx(position=pos("SHORT")), "already_in_position")


def test_daily_loss_cap_boundary() -> None:
    rejected(long(), ctx(daily_pnl=-3000.0), "daily_loss_cap")
    rejected(long(), ctx(daily_pnl=-3000.01), "daily_loss_cap")
    # 1 rupee short of the cap: not halted, but 1 lot (500) no longer fits the remaining budget
    rejected(long(), ctx(daily_pnl=-2999.0), "risk_budget")
    assert risk.daily_loss_breached(L, -3000.0)
    assert not risk.daily_loss_breached(L, -2999.99)


def test_max_trades_boundary() -> None:
    approved(long(), ctx(trades_today=3))
    rejected(long(), ctx(trades_today=4), "max_trades")
    rejected(long(), ctx(trades_today=5), "max_trades")


@pytest.mark.parametrize(
    "hh,mm,ss,ok",
    [(9, 29, 59, False), (9, 30, 0, True), (22, 30, 0, True), (22, 30, 1, False), (23, 0, 0, False), (3, 0, 0, False)],
)
def test_entry_window_boundaries(hh: int, mm: int, ss: int, ok: bool) -> None:
    c = ctx(now=TUE.replace(hour=hh, minute=mm, second=ss))
    if ok:
        approved(long(), c)
    else:
        rejected(long(), c, "outside_window")


def test_weekend_blocked() -> None:
    for day in (28, 29):  # Sat, Sun
        rejected(long(), ctx(now=datetime(2026, 11, day, 12, 0, tzinfo=IST)), "outside_window")


def test_configured_holiday_blocked() -> None:
    c = ctx(no_trade_dates=frozenset({TUE.date()}))
    rejected(long(), c, "outside_window")


@pytest.mark.parametrize(
    "when,ok",
    [
        # Thanksgiving week 2026: release Wed 25 Nov 22:30 IST. Window is [22:10, 23:00].
        (datetime(2026, 11, 25, 22, 9, 59, tzinfo=IST), True),
        (datetime(2026, 11, 25, 22, 10, 0, tzinfo=IST), False),
        (datetime(2026, 11, 25, 22, 29, 0, tzinfo=IST), False),
        # Regular week: Thu 10 Dec 21:00 IST. Window [20:40, 21:30].
        (datetime(2026, 12, 10, 20, 39, 59, tzinfo=IST), True),
        (datetime(2026, 12, 10, 20, 40, 0, tzinfo=IST), False),
        (datetime(2026, 12, 10, 21, 30, 0, tzinfo=IST), False),
        (datetime(2026, 12, 10, 21, 30, 1, tzinfo=IST), True),
    ],
)
def test_eia_blackout_boundaries(when: datetime, ok: bool) -> None:
    if ok:
        approved(long(), ctx(now=when))
    else:
        rejected(long(), ctx(now=when), "eia_blackout")


def test_cooldown_after_loss_boundary() -> None:
    rejected(long(), ctx(last_loss_at=TUE - timedelta(minutes=29, seconds=59)), "cooldown")
    approved(long(), ctx(last_loss_at=TUE - timedelta(minutes=30)))


def test_confidence_boundary_and_garbage() -> None:
    approved(long(confidence=0.6), ctx())
    rejected(long(confidence=0.5999), ctx(), "low_confidence")
    rejected(long(confidence=float("nan")), ctx(), "low_confidence")
    rejected(long(confidence=1.5), ctx(), "low_confidence")


@pytest.mark.parametrize("atr", [None, 0.0, -1.0, float("nan")])
def test_no_atr_means_no_entry(atr: float | None) -> None:
    rejected(long(), ctx(atr=atr), "no_atr")


@pytest.mark.parametrize("ltp", [0.0, -1.0, float("nan"), float("inf")])
def test_bad_price_rejected(ltp: float) -> None:
    rejected(long(), ctx(ltp=ltp), "bad_price")


@pytest.mark.parametrize("maker", [long, short])
@pytest.mark.parametrize("field", ["stop_loss", "target"])
@pytest.mark.parametrize("value", [None, float("nan"), 0.0, -5.0])
def test_entry_needs_both_levels(maker: Any, field: str, value: float | None) -> None:
    rejected(maker(**{field: value}), ctx(), "missing_levels")


@pytest.mark.parametrize(
    "d",
    [
        long(stop_loss=300.0),  # stop at price
        long(stop_loss=302.0),  # stop above price
        long(target=300.0),  # target at price
        long(target=299.0),
        short(stop_loss=300.0),
        short(stop_loss=298.0),
        short(target=300.0),
        short(target=301.0),
    ],
)
def test_levels_on_wrong_side_rejected(d: Decision) -> None:
    rejected(d, ctx(), "wrong_side")


# --------------------------------------------------------------------------- stop distance & R:R
@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize(
    "dist,code", [(0.4, "stop_too_tight"), (0.5, None), (3.0, None), (3.1, "stop_too_wide")]
)
def test_stop_distance_atr_bounds(side: str, dist: float, code: str | None) -> None:
    sign = 1 if side == "long" else -1
    maker = long if side == "long" else short
    d = maker(stop_loss=300.0 - sign * dist, target=300.0 + sign * dist * 2)
    if code:
        rejected(d, ctx(), code)
    else:
        approved(d, ctx())


@pytest.mark.parametrize("side", ["long", "short"])
def test_reward_to_risk_boundary(side: str) -> None:
    sign = 1 if side == "long" else -1
    maker = long if side == "long" else short
    approved(maker(stop_loss=300.0 - sign * 2.0, target=300.0 + sign * 3.0), ctx())  # exactly 1.5
    rejected(maker(stop_loss=300.0 - sign * 2.0, target=300.0 + sign * 2.9), ctx(), "poor_rr")


def test_prices_are_rounded_to_tick_before_checks() -> None:
    v = approved(long(stop_loss=298.04, target=304.06), ctx())
    assert v.decision.stop_loss == 298.0 and v.decision.target == 304.1
    assert risk.round_to_tick(298.05, 0.10) in (298.0, 298.1)
    assert risk.round_to_tick(298.26, 0.10) == 298.3


# --------------------------------------------------------------------------- sizing
def test_lots_are_clipped_to_max_lots() -> None:
    v = approved(long(lots=50), ctx())
    assert v.decision.lots == 1


def test_lots_are_clipped_to_per_trade_risk() -> None:
    # 2.0 * 250 = 500 per lot; 1500 budget -> 3 lots
    v = approved(long(lots=10), ctx(), replace(L, max_lots=10))
    assert v.decision.lots == 3


def test_lots_are_never_clipped_up() -> None:
    v = approved(long(lots=2), ctx(), replace(L, max_lots=10))
    assert v.decision.lots == 2


def test_lots_are_clipped_to_remaining_daily_budget() -> None:
    # cap 3000, down 1900 -> 1100 left -> 2 lots of 500
    v = approved(long(lots=10), ctx(daily_pnl=-1900), replace(L, max_lots=10))
    assert v.decision.lots == 2


def test_profit_does_not_raise_per_trade_budget() -> None:
    v = approved(long(lots=10), ctx(daily_pnl=10000), replace(L, max_lots=10))
    assert v.decision.lots == 3


def test_reject_when_one_lot_does_not_fit_risk() -> None:
    # 2.9 * 1250 = 3625 > 1500
    rejected(long(stop_loss=297.1, target=305.0), ctx(multiplier=1250), "risk_budget")


def test_one_lot_exactly_at_budget_is_approved() -> None:
    # 2.0 * 250 = 500
    approved(long(), ctx(), replace(L, max_risk_inr=500.0))
    rejected(long(), ctx(), "risk_budget", replace(L, max_risk_inr=499.99))


@pytest.mark.parametrize("lots", [0, -3])
def test_non_positive_lots_rejected(lots: int) -> None:
    rejected(long(lots=lots), ctx(), "bad_lots")


# --------------------------------------------------------------------------- trailing stops
def trail(sl: float | None) -> Decision:
    return Decision(action="TRAIL_STOP", stop_loss=sl)


def test_trail_needs_position_and_stop() -> None:
    rejected(trail(299.0), ctx(), "no_position")
    rejected(trail(None), ctx(position=pos()), "missing_levels")
    rejected(trail(float("nan")), ctx(position=pos()), "missing_levels")


@pytest.mark.parametrize(
    "side,old,new,ltp,ok",
    [
        ("LONG", 298.0, 299.0, 302.0, True),
        ("LONG", 298.0, 298.0, 302.0, False),  # unchanged is not tighter
        ("LONG", 298.0, 297.0, 302.0, False),  # widen
        ("LONG", 298.0, 301.7, 302.0, True),  # buffer = max(0.05, 0.25 ATR) = 0.25; 0.3 away
        ("LONG", 298.0, 301.8, 302.0, False),  # only 0.2 away
        ("LONG", 298.0, 302.5, 302.0, False),  # through price
        ("SHORT", 302.0, 301.0, 298.0, True),
        ("SHORT", 302.0, 302.0, 298.0, False),
        ("SHORT", 302.0, 303.0, 298.0, False),  # widen
        ("SHORT", 302.0, 298.3, 298.0, True),
        ("SHORT", 302.0, 298.2, 298.0, False),
        ("SHORT", 302.0, 297.0, 298.0, False),
    ],
)
def test_trail_only_tightens_and_keeps_buffer(side: str, old: float, new: float, ltp: float, ok: bool) -> None:
    c = ctx(position=pos(side, sl=old), ltp=ltp)
    if ok:
        assert approved(trail(new), c).code == "trail"
    else:
        rejected(trail(new), c, "trail_invalid")


def test_trail_minimum_buffer_without_atr() -> None:
    c = ctx(position=pos("LONG", sl=298.0), ltp=302.0, atr=None, tick_size=0.01)
    approved(trail(301.95), c)
    rejected(trail(301.96), c, "trail_invalid")


def test_trail_allowed_under_kill_because_it_only_reduces_risk() -> None:
    approved(trail(299.0), ctx(position=pos(), ltp=302.0, kill=True))


# --------------------------------------------------------------------------- entry_block (skips the LLM)
def test_entry_block_none_when_clear() -> None:
    assert risk.entry_block(L, ctx(), CAL) is None


def test_entry_block_reports_first_reason() -> None:
    blocked = risk.entry_block(L, ctx(kill=True, trades_today=99), CAL)
    assert blocked is not None and blocked[0] == "kill_switch"
    blocked = risk.entry_block(L, ctx(trades_today=4), CAL)
    assert blocked is not None and blocked[0] == "max_trades"


# --------------------------------------------------------------------------- forced exits (never via the LLM)
def forced(now: datetime = TUE, position: Position | None = None, daily: float = 0.0, kill: bool = False) -> str | None:
    return risk.forced_exit_reason(L, FORCE_FLAT, now, position or pos(), daily, kill)


def test_forced_exit_none_when_flat_or_calm() -> None:
    assert risk.forced_exit_reason(L, FORCE_FLAT, TUE, None, -99999, True) is None
    assert forced() is None


def test_forced_exit_reasons() -> None:
    assert forced(kill=True) == "forced: kill switch"
    assert forced(daily=-3000) == "forced: daily loss cap"
    assert forced(daily=-2999) is None
    assert forced(now=TUE.replace(hour=23, minute=9, second=59)) is None
    assert forced(now=TUE.replace(hour=23, minute=10)) == "forced: end of session"
    assert forced(now=datetime(2026, 11, 28, 12, 0, tzinfo=IST)) is not None  # weekend


def test_position_from_a_previous_day_is_flattened_immediately() -> None:
    next_morning = datetime(2026, 11, 25, 9, 0, 5, tzinfo=IST)
    assert forced(now=next_morning) == "forced: overnight position"

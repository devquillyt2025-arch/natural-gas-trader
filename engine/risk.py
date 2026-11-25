"""The hard risk layer. Claude proposes; this module disposes. Pure functions, no I/O.

Nothing here can be overridden by the model: the engine calls `validate` on every decision and
`forced_exit_reason` on every tick, independently of the model.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

from . import sessions
from .config import IST, Limits
from .models import ACTIONS, ENTRY_ACTIONS, Decision, Position
from .sessions import EiaCalendar

EPS = 1e-9
MIN_TRAIL_BUFFER = 0.05
TRAIL_ATR_FRACTION = 0.25


@dataclass(frozen=True)
class RiskContext:
    now: datetime
    ltp: float
    atr: float | None  # of the decision candles
    multiplier: int  # mmBtu per lot
    position: Position | None
    daily_pnl: float  # realized + unrealized today
    trades_today: int
    last_loss_at: datetime | None
    kill: bool = False
    orphan: bool = False
    safety_halt: bool = False  # latched after any ambiguous/failed live order until Resume
    daily_halt_latched: bool = False
    data_fresh: bool = True
    control_ok: bool = True  # can the UI still reach us?
    tick_size: float = 0.10
    no_trade_dates: frozenset[date] = frozenset()


@dataclass(frozen=True)
class Verdict:
    ok: bool
    code: str
    reason: str
    decision: Decision  # possibly adjusted: prices rounded to tick, lots clipped down


def round_to_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 4)


def daily_loss_breached(limits: Limits, daily_pnl: float) -> bool:
    return daily_pnl <= -limits.daily_loss_cap_inr


def _finite_positive(x: float | None) -> bool:
    return x is not None and math.isfinite(x) and x > 0


def trail_buffer(atr: float | None) -> float:
    usable = atr if _finite_positive(atr) else 0.0
    return max(MIN_TRAIL_BUFFER, TRAIL_ATR_FRACTION * (usable or 0.0))


def entry_block(limits: Limits, c: RiskContext, calendar: EiaCalendar) -> tuple[str, str] | None:
    """Reasons that would reject ANY entry right now. The engine also uses this to skip the LLM call."""
    if c.kill:
        return "kill_switch", "kill switch is ON"
    if c.orphan:
        return "orphan_position", "broker holds a position this app does not know about"
    if c.safety_halt:
        return "safety_halt", "halted after an order problem; check Events, then press Resume"
    if not c.control_ok:
        return "control_channel_down", "control channel unreachable, so the kill switch would not work"
    if c.position is not None:
        return "already_in_position", "already in a position (exit first; no pyramiding or flipping)"
    if c.daily_halt_latched or daily_loss_breached(limits, c.daily_pnl):
        return "daily_loss_cap", f"daily loss cap of {limits.daily_loss_cap_inr:.0f} INR reached"
    if c.trades_today >= limits.max_trades_per_day:
        return "max_trades", f"max trades per day reached ({limits.max_trades_per_day})"
    if not sessions.in_entry_window(c.now, limits, c.no_trade_dates):
        return "outside_window", f"outside the entry window {limits.entry_start:%H:%M}-{limits.no_entry_after:%H:%M} IST (Mon-Fri)"
    if calendar.in_blackout(c.now, limits.eia_before_min, limits.eia_after_min):
        return "eia_blackout", f"EIA storage report blackout (-{limits.eia_before_min}/+{limits.eia_after_min} min)"
    if c.last_loss_at and c.now - c.last_loss_at < timedelta(minutes=limits.cooldown_min_after_loss):
        return "cooldown", f"cooldown after a loss ({limits.cooldown_min_after_loss} min)"
    if not c.data_fresh:  # checked last: outside the session, "outside window" is the useful message
        return "stale_data", "market data is stale"
    return None


def validate(limits: Limits, d: Decision, c: RiskContext, calendar: EiaCalendar) -> Verdict:
    def no(code: str, why: str) -> Verdict:
        return Verdict(False, code, why, d)

    if d.action not in ACTIONS:
        return no("unknown_action", f"unknown action {d.action!r}")
    if d.action == "HOLD":
        return Verdict(True, "hold", "hold", d)
    if d.action == "EXIT":
        if c.position is None:
            return no("no_position", "EXIT but there is no open position")
        return Verdict(True, "exit", "exit approved", d)

    if not _finite_positive(c.ltp):
        return no("bad_price", f"no usable price ({c.ltp})")
    if d.action == "TRAIL_STOP":
        return _validate_trail(d, c)

    assert d.action in ENTRY_ACTIONS
    blocked = entry_block(limits, c, calendar)
    if blocked:
        return no(*blocked)
    if not (math.isfinite(d.confidence) and limits.min_confidence <= d.confidence <= 1.0):
        return no("low_confidence", f"confidence {d.confidence:.2f} below {limits.min_confidence:.2f} (or invalid)")
    atr = c.atr
    if atr is None or not _finite_positive(atr):
        return no("no_atr", "no ATR available")
    if not (_finite_positive(d.stop_loss) and _finite_positive(d.target)):
        return no("missing_levels", "an entry needs both stop_loss and target")
    assert d.stop_loss is not None and d.target is not None
    stop = round_to_tick(d.stop_loss, c.tick_size)
    target = round_to_tick(d.target, c.tick_size)

    long = d.action == "ENTER_LONG"
    if long and not (stop < c.ltp < target):
        return no("wrong_side", f"long needs stop < price < target ({stop} / {c.ltp} / {target})")
    if not long and not (target < c.ltp < stop):
        return no("wrong_side", f"short needs target < price < stop ({target} / {c.ltp} / {stop})")

    dist = abs(c.ltp - stop)
    reward = abs(target - c.ltp)
    if dist < limits.min_sl_atr * atr - EPS:
        return no("stop_too_tight", f"stop {dist:.2f} away is under {limits.min_sl_atr} x ATR ({atr:.2f})")
    if dist > limits.max_sl_atr * atr + EPS:
        return no("stop_too_wide", f"stop {dist:.2f} away is over {limits.max_sl_atr} x ATR ({atr:.2f})")
    if reward / dist < limits.min_rr - 1e-6:
        return no("poor_rr", f"reward:risk {reward / dist:.2f} below {limits.min_rr}")

    if d.lots < 1:
        return no("bad_lots", f"lots must be at least 1, got {d.lots}")
    risk_per_lot = dist * c.multiplier
    # Per-trade budget, further limited by what is left of today's loss cap.
    budget = min(limits.max_risk_inr, limits.daily_loss_cap_inr + c.daily_pnl)
    fits = math.floor((budget + 1e-6) / risk_per_lot)
    lots = min(d.lots, limits.max_lots, fits)
    if lots < 1:
        return no("risk_budget", f"1 lot risks {risk_per_lot:.0f} INR, only {budget:.0f} INR allowed")
    adjusted = replace(d, lots=lots, stop_loss=stop, target=target)
    return Verdict(True, "approved", f"approved: {lots} lot, risking {risk_per_lot * lots:.0f} INR", adjusted)


def _validate_trail(d: Decision, c: RiskContext) -> Verdict:
    p = c.position
    if p is None:
        return Verdict(False, "no_position", "TRAIL_STOP but there is no open position", d)
    if not _finite_positive(d.stop_loss):
        return Verdict(False, "missing_levels", "TRAIL_STOP without a stop_loss", d)
    assert d.stop_loss is not None
    new = round_to_tick(d.stop_loss, c.tick_size)
    buf = trail_buffer(c.atr)
    if p.side == "LONG":
        ok = new > p.stop_loss + EPS and c.ltp - new >= buf - 1e-6
    else:
        ok = new < p.stop_loss - EPS and new - c.ltp >= buf - 1e-6
    if not ok:
        return Verdict(
            False, "trail_invalid",
            f"new stop {new} must tighten {p.stop_loss} and stay at least {buf:.2f} beyond price {c.ltp}", d,
        )
    return Verdict(True, "trail", f"stop tightened {p.stop_loss} -> {new}", replace(d, stop_loss=new))


def forced_exit_reason(
    limits: Limits,
    force_flat: time,
    now: datetime,
    position: Position | None,
    daily_pnl: float,
    kill: bool,
    daily_halt_latched: bool = False,
) -> str | None:
    """Code-level exits. These never go through the model."""
    if position is None:
        return None
    local = now.astimezone(IST)
    if kill:
        return "forced: kill switch"
    if daily_halt_latched or daily_loss_breached(limits, daily_pnl):
        return "forced: daily loss cap"
    if datetime.fromisoformat(position.opened_at).astimezone(IST).date() < local.date():
        return "forced: overnight position"
    if local.weekday() >= 5 or local.time() >= force_flat:
        return "forced: end of session"
    return None

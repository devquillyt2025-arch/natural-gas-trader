"""Code-enforced risk layer. Claude proposes; this module disposes. Pure and unit-tested."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Optional

from . import sessions
from .config import Settings
from .models import ACTIONS, Decision, Position


@dataclass
class RiskContext:
    now: datetime
    ltp: float
    atr15: Optional[float]
    lot_size: int
    position: Optional[Position]
    daily_pnl: float  # realized + unrealized today
    trades_today: int
    last_loss_at: Optional[datetime]
    kill: bool = False


@dataclass
class Verdict:
    ok: bool
    reason: str
    decision: Decision


def daily_halted(s: Settings, daily_pnl: float) -> bool:
    return daily_pnl <= -s.daily_loss_cap_inr


def validate(s: Settings, d: Decision, c: RiskContext) -> Verdict:
    def no(why: str) -> Verdict:
        return Verdict(False, why, d)

    if d.action not in ACTIONS:
        return no(f"unknown action {d.action!r}")

    if d.action == "HOLD":
        return Verdict(True, "hold", d)

    if d.action == "EXIT":
        return Verdict(True, "exit", d) if c.position else no("EXIT but no open position")

    if c.kill:
        return no("kill switch is ON")

    # ---- stop adjustment: may only tighten, never widen -----------------------------
    if d.action == "TRAIL_STOP":
        p = c.position
        if not p:
            return no("TRAIL_STOP but no open position")
        if d.stop_loss is None:
            return no("TRAIL_STOP without stop_loss")
        buf = max(0.05, 0.25 * (c.atr15 or 0))
        if p.side == "LONG":
            if not (p.stop_loss < d.stop_loss < c.ltp - buf):
                return no(f"new stop {d.stop_loss} must be above {p.stop_loss} and below ltp-{buf:.2f}")
        else:
            if not (c.ltp + buf < d.stop_loss < p.stop_loss):
                return no(f"new stop {d.stop_loss} must be below {p.stop_loss} and above ltp+{buf:.2f}")
        return Verdict(True, "stop tightened", d)

    # ---- entries ----------------------------------------------------------------------
    if c.position:
        return no("already in a position (exit first; no pyramiding or flipping)")
    if daily_halted(s, c.daily_pnl):
        return no(f"daily loss cap reached ({c.daily_pnl:.0f} <= -{s.daily_loss_cap_inr:.0f})")
    if c.trades_today >= s.max_trades_per_day:
        return no(f"max trades/day reached ({s.max_trades_per_day})")
    if not sessions.entry_allowed(c.now, s):
        return no("entries not allowed now (outside window or EIA blackout)")
    if c.last_loss_at and c.now - c.last_loss_at < timedelta(minutes=s.cooldown_min_after_loss):
        return no(f"cooldown after loss ({s.cooldown_min_after_loss} min)")
    if d.confidence < s.min_confidence:
        return no(f"confidence {d.confidence:.2f} < {s.min_confidence:.2f}")
    if not c.atr15 or c.atr15 <= 0:
        return no("no ATR available")
    if d.stop_loss is None or d.target is None:
        return no("entry requires both stop_loss and target")

    long = d.action == "ENTER_LONG"
    if long and not (d.stop_loss < c.ltp < d.target):
        return no("LONG needs stop < price < target")
    if not long and not (d.target < c.ltp < d.stop_loss):
        return no("SHORT needs target < price < stop")

    dist = abs(c.ltp - d.stop_loss)
    reward = abs(d.target - c.ltp)
    if dist < s.min_sl_atr * c.atr15:
        return no(f"stop too tight ({dist:.2f} < {s.min_sl_atr}xATR {c.atr15:.2f})")
    if dist > s.max_sl_atr * c.atr15:
        return no(f"stop too wide ({dist:.2f} > {s.max_sl_atr}xATR {c.atr15:.2f})")
    if reward / dist < s.min_rr:
        return no(f"reward/risk {reward / dist:.2f} < {s.min_rr}")

    # Size: respect max lots, per-trade risk, and the remaining daily loss budget.
    risk_per_lot = dist * c.lot_size
    budget = min(s.max_risk_inr, s.daily_loss_cap_inr + c.daily_pnl)
    lots = min(max(1, d.lots), s.max_lots, math.floor(budget / risk_per_lot))
    if lots < 1:
        return no(f"1 lot risks {risk_per_lot:.0f} INR > allowed {budget:.0f}")
    return Verdict(True, f"approved ({lots} lot, risk {risk_per_lot * lots:.0f} INR)", replace(d, lots=lots))

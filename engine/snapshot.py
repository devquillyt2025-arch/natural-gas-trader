"""Builds the compact JSON market snapshot Claude reasons over (and that is stored for audit)."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from . import sessions
from .config import IST, Limits, Settings
from .indicators import atr, ema, r2, rsi, swing_levels
from .models import Candle, Contract, Position
from .sessions import EiaCalendar

DAILY_ROWS = 40
INTRADAY_ROWS = 80  # 20 hours of 15m, under 7 hours of 5m


def _block(candles: Sequence[Candle], rows: int, stamp: str) -> dict[str, Any]:
    closes = [c.c for c in candles]
    e9, e21, e50 = ema(closes, 9), ema(closes, 21), ema(closes, 50)
    return {
        "atr14": r2(atr(candles, 14)),
        "rsi14": r2(rsi(closes, 14), 1),
        "ema9": r2(e9[-1]) if e9 else None,
        "ema21": r2(e21[-1]) if e21 else None,
        "ema50": r2(e50[-1]) if e50 else None,
        "swing": swing_levels(candles),
        "ohlc_oldest_to_newest": [
            [c.ts.astimezone(IST).strftime(stamp), r2(c.o), r2(c.h), r2(c.l), r2(c.c)] for c in candles[-rows:]
        ],
    }


def _previous_day(daily: Sequence[Candle], now: datetime) -> Candle | None:
    today = now.astimezone(IST).date()
    earlier = [c for c in daily if c.ts.astimezone(IST).date() < today]
    return earlier[-1] if earlier else None


def market_levels(daily: Sequence[Candle], bars: Sequence[Candle], now: datetime) -> dict[str, Any]:
    """The levels the engine uses; the same numbers are drawn on the UI chart."""
    prev = _previous_day(daily, now)
    swings = swing_levels(bars)
    return {
        "prev_day_high": r2(prev.h) if prev else None,
        "prev_day_low": r2(prev.l) if prev else None,
        "resistance": swings["resistance"],
        "support": swings["support"],
    }


def build(
    *,
    settings: Settings,
    limits: Limits,
    calendar: EiaCalendar,
    contract: Contract,
    daily: Sequence[Candle],
    bars: Sequence[Candle],
    now: datetime,
    ltp: float,
    position: Position | None,
    daily_pnl: float,
    trades_today: int,
    entry_block: str | None,
    last_decisions: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    local = now.astimezone(IST)
    today_bars = [c for c in bars if c.ts.astimezone(IST).date() == local.date()]
    prev = _previous_day(daily, now)
    next_eia = calendar.next_release(now)
    flat_at = sessions.at(local.date(), settings.force_flat)

    snap: dict[str, Any] = {
        "time_ist": local.strftime("%a %Y-%m-%d %H:%M"),
        "instrument": {
            "symbol": contract.trading_symbol, "lot_size_mmbtu": contract.multiplier,
            "quote": "INR per mmBtu", "tick": contract.tick_size,
        },
        "ltp": r2(ltp),
        "today": {
            "open": today_bars[0].o if today_bars else None,
            "high": max((c.h for c in today_bars), default=None),
            "low": min((c.l for c in today_bars), default=None),
        },
        "prev_day": {"h": prev.h, "l": prev.l, "c": prev.c} if prev else None,
        "daily": _block(daily, DAILY_ROWS, "%m-%d"),
        f"intraday_{settings.tf}": _block(bars, INTRADAY_ROWS, "%m-%d %H:%M"),
        "session": {
            "entries_allowed_now": entry_block is None,
            "entry_block_reason": entry_block,
            "minutes_to_force_flat": max(0, int((flat_at - local).total_seconds() // 60)),
            "next_eia_storage_report_ist": next_eia.strftime("%a %d %b %H:%M"),
        },
        "position": None,
        "today_account": {"realized_plus_unrealized_pnl_inr": r2(daily_pnl), "trades": trades_today},
        "hard_limits": {
            "max_lots": limits.max_lots,
            "max_risk_per_trade_inr": limits.max_risk_inr,
            "daily_loss_cap_inr": limits.daily_loss_cap_inr,
            "max_trades_per_day": limits.max_trades_per_day,
            "min_reward_to_risk": limits.min_rr,
            f"stop_distance_in_{settings.tf}_atr": [limits.min_sl_atr, limits.max_sl_atr],
            "min_confidence": limits.min_confidence,
        },
        "your_last_decisions": [
            {
                "t": str(d["ts"])[11:16], "action": d["action"], "approved": bool(d["approved"]),
                "verdict": str(d["verdict"])[:90],
            }
            for d in last_decisions[:5]
        ],
        "news_brief": "(none)",
    }
    if position:
        pnl = position.unrealized(ltp)
        opened = datetime.fromisoformat(position.opened_at)
        snap["position"] = {
            "side": position.side,
            "lots": position.lots,
            "entry": position.entry,
            "stop_loss": position.stop_loss,
            "initial_stop": position.initial_stop,
            "target": position.target,
            "unrealized_inr": r2(pnl),
            "unrealized_r": position.r_multiple(pnl),
            "minutes_in_trade": int((now - opened).total_seconds() // 60),
            "entry_reasoning": position.reasoning[:300],
        }
    return snap

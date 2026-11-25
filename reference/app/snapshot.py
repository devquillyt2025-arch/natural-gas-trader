"""Builds the compact JSON market snapshot that Claude reasons over."""
from __future__ import annotations

from datetime import datetime

from . import sessions
from .config import Settings
from .feed import Feed
from .indicators import atr, ema, r2, rsi, swing_levels
from .models import Candle, Position


def _tf_block(candles: list[Candle], rows: int) -> dict:
    closes = [c.c for c in candles]
    e9, e21, e50 = ema(closes, 9), ema(closes, 21), ema(closes, 50)
    return {
        "atr14": r2(atr(candles, 14)),
        "rsi14": r2(rsi(closes, 14), 1),
        "ema9": r2(e9[-1]) if e9 else None,
        "ema21": r2(e21[-1]) if e21 else None,
        "ema50": r2(e50[-1]) if e50 else None,
        "swing": {k: [r2(x) for x in v] for k, v in swing_levels(candles).items()},
        "bars_ohlc": [[c.ts[5:16], c.o, c.h, c.l, c.c] for c in candles[-rows:]],  # oldest -> newest
    }


def build(s: Settings, feed: Feed, now: datetime, position: Position | None,
          daily_pnl: float, trades_today: int, news: str, last_decisions: list[dict]) -> dict:
    d1 = feed.candles("1d", 60, now)
    m15 = feed.candles("15m", 120, now)
    ltp = feed.ltp(now)
    con = feed.contract()
    today = now.date().isoformat()
    today_bars = [c for c in m15 if c.ts[:10] == today]
    prev = [c for c in d1 if c.ts[:10] < today]

    ctx: dict = {
        "time_ist": now.strftime("%a %Y-%m-%d %H:%M"),
        "instrument": {"symbol": con.trading_symbol, "lot_size_mmbtu": con.lot_size, "quote": "INR per mmBtu"},
        "ltp": r2(ltp),
        "today": {
            "open": today_bars[0].o if today_bars else None,
            "high": max((c.h for c in today_bars), default=None),
            "low": min((c.l for c in today_bars), default=None),
        },
        "prev_day": {"h": prev[-1].h, "l": prev[-1].l, "c": prev[-1].c} if prev else None,
        "daily": _tf_block(d1, 25),
        "intraday_15m": _tf_block(m15, 40),
        "session": {
            "entries_allowed_now": sessions.entry_allowed(now, s),
            "next_eia_storage_report_ist": sessions.next_eia(now).strftime("%a %H:%M"),
            "minutes_to_force_flat": max(0, int((sessions._at(now, s.force_flat) - now).total_seconds() // 60)),
        },
        "position": None,
        "account": {"realized_plus_unrealized_pnl_today_inr": r2(daily_pnl), "trades_today": trades_today},
        "hard_limits": {
            "max_lots": s.max_lots, "max_risk_per_trade_inr": s.max_risk_inr,
            "min_reward_to_risk": s.min_rr, "stop_distance_atr15_range": [s.min_sl_atr, s.max_sl_atr],
            "daily_loss_cap_inr": s.daily_loss_cap_inr, "min_confidence": s.min_confidence,
        },
        "news_brief": news or "(none)",
        "your_last_decisions": [
            {"t": d["ts"][11:16], "action": d["action"], "approved": bool(d["approved"]), "verdict": d["verdict"][:80]}
            for d in last_decisions[:5]
        ],
    }
    if position:
        ctx["position"] = {
            "side": position.side, "lots": position.lots, "entry": position.entry,
            "stop_loss": position.stop_loss, "target": position.target,
            "unrealized_inr": r2(position.unrealized(ltp)), "opened_at": position.opened_at[11:16],
        }
    return ctx

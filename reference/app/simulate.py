"""Offline replay: synthetic MCX-like bars -> engine -> paper broker. No network, no API key.

    python -m app.simulate                 # EMA stub agent on synthetic data
    python -m app.simulate --seed 3 --days 30
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from .agent import StubAgent
from .broker import PaperBroker
from .config import IST, Settings
from .engine import Engine
from .feed import SimFeed
from .notify import Notifier
from .store import Store


def run(seed: int = 7, days: int = 45, replay_days: int = 25, agent=None, settings: Settings | None = None) -> dict:
    s = settings or Settings()
    s.use_news = False
    s.db_path = ":memory:"
    store = Store(":memory:")
    feed = SimFeed(seed=seed, days=days)
    broker = PaperBroker(s, store, feed.contract().instrument_key)
    notifier = Notifier(s, store)
    eng = Engine(s, store, feed, broker, agent or StubAgent(), notifier)

    bars = feed.bars
    start_idx = max(0, len(bars) - replay_days * 58)
    for b in bars[start_idx:]:
        now = datetime.fromisoformat(b.ts) + timedelta(minutes=15)  # bar close = decision time
        eng.monitor(now, hi=b.h, lo=b.l)
        eng.cycle(now)

    trades = store.trades(10_000)
    decisions = store.decisions(100_000)
    by_day: dict[str, float] = {}
    for t in trades:
        by_day[t["closed_at"][:10]] = by_day.get(t["closed_at"][:10], 0) + t["pnl"]
    pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "net_pnl_inr": round(pnl, 2),
        "worst_trade_inr": min((t["pnl"] for t in trades), default=0),
        "worst_day_inr": min(by_day.values(), default=0),
        "decisions": len(decisions),
        "rejected": sum(1 for d in decisions if not d["approved"]),
        "open_position_at_end": broker.position() is not None,
        "settings": s,
        "_trades": trades,
        "_by_day": by_day,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--replay", type=int, default=25)
    a = ap.parse_args()
    r = run(a.seed, a.days, a.replay)
    for k, v in r.items():
        if not k.startswith("_") and k != "settings":
            print(f"{k:>22}: {v}")

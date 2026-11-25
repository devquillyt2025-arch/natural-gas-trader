"""Offline replay of the whole engine on synthetic prices. No network, no API key.

    python -m engine.simulate                          # stub agent, 25 days
    python -m engine.simulate --agent reckless         # prove the risk limits hold
    python -m engine.simulate --export sim.json        # every row it would publish, as JSON
    python -m engine.simulate --serve --speed 60       # live loop feeding the UI (see README)

This proves plumbing and risk limits. Synthetic prices say nothing about profitability.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from psycopg.conninfo import conninfo_to_dict

from . import logs
from .agent_base import Agent, RecklessAgent, StubAgent
from .brokers.paper import PaperBroker
from .clock import ManualClock, ScaledClock
from .config import IST, Settings
from .engine import Engine, InlineExecutor
from .feed import SimFeed
from .models import Trade
from .store import LocalStore, MemoryBus, PostgresBus, Store, Syncer


@dataclass
class Result:
    engine: Engine
    bus: MemoryBus
    trades: list[Trade]
    days_ending_with_position: list[date]
    decisions: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        by_day: Counter[str] = Counter()
        pnl_by_day: dict[str, float] = {}
        for t in self.trades:
            by_day[t.closed_at[:10]] += 1
            pnl_by_day[t.closed_at[:10]] = pnl_by_day.get(t.closed_at[:10], 0.0) + t.pnl
        wins = [t for t in self.trades if t.pnl > 0]
        rejected = Counter(d["verdict_code"] for d in self.decisions if not d["approved"])
        return {
            "trades": len(self.trades),
            "win_rate": round(len(wins) / len(self.trades), 3) if self.trades else None,
            "net_pnl_inr": round(sum(t.pnl for t in self.trades), 2),
            "worst_trade_inr": min((t.pnl for t in self.trades), default=0.0),
            "worst_day_inr": round(min(pnl_by_day.values(), default=0.0), 2),
            "max_trades_in_a_day": max(by_day.values(), default=0),
            "max_lots": max((t.lots for t in self.trades), default=0),
            "overnight_positions": len(self.days_ending_with_position),
            "exits": dict(Counter(t.exit_reason for t in self.trades)),
            "decisions": len(self.decisions),
            "rejected_by_reason": dict(rejected.most_common()),
        }


def run(
    seed: int = 7,
    days: int = 45,
    replay_days: int = 25,
    agent: Agent | None = None,
    settings: Settings | None = None,
    end_date: date = date(2026, 11, 20),
    publish: bool = False,
) -> Result:
    s = settings or Settings()
    bus = MemoryBus()
    feed = SimFeed(
        seed=seed, days=days, symbol=s.symbol, multiplier=s.multiplier, end_date=end_date, bar_min=s.candle_min
    )
    start = max(0, len(feed.bars) - replay_days * feed.per_day)
    clock = ManualClock(feed.bars[start].ts)
    store = Store(LocalStore(":memory:"), bus, s.mode, clock)
    engine = Engine(
        s, store, feed, PaperBroker(s, store), agent or StubAgent(), clock,
        executor=InlineExecutor(), wall=clock.now,
    )
    decisions: list[dict[str, Any]] = []
    overnight: list[date] = []
    for i in range(start, len(feed.bars)):
        bar = feed.bars[i]
        clock.set(bar.ts + feed.bar)  # decisions happen at bar close
        store.last_command_poll_ok = clock.monotonic()  # no real control channel offline
        engine.monitor(hi=bar.h, lo=bar.l, open_=bar.o)
        if publish:
            engine.publish_market()
            store.flush(2000)
        record = engine.cycle()
        if record:
            decisions.append(record.to_dict())
        last_of_day = i + 1 == len(feed.bars) or feed.bars[i + 1].ts.date() != bar.ts.date()
        if last_of_day and engine.broker.position() is not None:
            overnight.append(bar.ts.date())
    decisions += [d for d in store.recent_decisions(100_000) if d["verdict_code"] == "forced"]
    if publish:
        now = clock.now()
        engine.heartbeat()
        engine.publish_auth()
        engine.publish_position()
        engine.publish_limits(now)
        engine.publish_session(now)
    while store.flush(2000):
        pass
    return Result(engine, bus, store.all_trades(), overnight, decisions)


def default_start() -> datetime:
    day = datetime.now(IST).date()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime(day.year, day.month, day.day, 11, 59, 30, tzinfo=IST)


DEMO_DATABASE_URL = "postgresql://postgres:demo@127.0.0.1:54321/postgres?sslmode=disable"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_local_database(url: str) -> bool:
    """True only if every host libpq could connect to is this machine."""
    info = conninfo_to_dict(url)
    hosts = [h for key in ("host", "hostaddr") for h in str(info.get(key) or "").split(",") if h]
    return bool(hosts) and all(h in LOCAL_HOSTS for h in hosts) and not info.get("service")


def serve(
    url: str,
    start: datetime,
    speed: float,
    seed: int,
    agent: Agent,
    stop: threading.Event,
    candle_min: int = 15,
) -> None:
    """The real engine loop against a mocked feed, publishing to a Postgres database (normally the demo one).

    Everything except market data and the model is the production path, including command polling.
    Heartbeats use real time; the market clock runs `speed` times faster.
    """
    settings = replace(Settings(), database_url=url, use_news=False, candle_min=candle_min)
    clock = ScaledClock(start, speed)
    store = Store(LocalStore(":memory:"), PostgresBus(url), settings.mode, clock)
    feed = SimFeed(seed=seed, days=40, end_date=start.date(), bar_min=candle_min)
    engine = Engine(settings, store, feed, PaperBroker(settings, store), agent, clock, instance_id="simulator")
    syncer = Syncer(store)
    syncer.start()
    try:
        engine.run_forever(stop)
    finally:
        syncer.stop()


def export(result: Result, path: Path) -> None:
    tables = {name: result.bus.rows(name) for name in result.bus.tables}
    tables["commands"] = result.bus.commands
    path.write_text(json.dumps(tables, default=str), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--days", type=int, default=45, help="synthetic trading days generated")
    ap.add_argument("--replay", type=int, default=25, help="how many of them to trade")
    ap.add_argument("--agent", choices=["stub", "reckless"], default="stub")
    ap.add_argument("--end", type=date.fromisoformat, default=date(2026, 11, 20), help="last synthetic day")
    ap.add_argument("--export", type=Path, help="write all published rows to this JSON file")
    ap.add_argument("--verbose", action="store_true")
    live = ap.add_argument_group("--serve: run the real loop against a mocked feed (paper only)")
    live.add_argument("--serve", action="store_true")
    # deliberately not read from the environment: synthetic trades must not land in a real project by accident
    live.add_argument("--database-url", default=DEMO_DATABASE_URL)
    live.add_argument("--allow-remote", action="store_true", help="allow a non-local database (use a throwaway one)")
    live.add_argument("--candle", type=int, choices=[5, 15], default=15, help="candle size in minutes")
    live.add_argument("--speed", type=float, default=30.0, help="market seconds per real second")
    live.add_argument("--start", type=datetime.fromisoformat, help="market start time, IST (default: today 12:00)")
    a = ap.parse_args()
    agent: Agent = RecklessAgent() if a.agent == "reckless" else StubAgent()
    if a.serve:
        if not a.allow_remote and not is_local_database(a.database_url):
            ap.error("--serve writes SYNTHETIC trades; refusing a non-local database without --allow-remote")
        logs.setup("INFO" if a.verbose else "WARNING")
        start = (a.start.replace(tzinfo=a.start.tzinfo or IST)) if a.start else default_start()
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        port = conninfo_to_dict(a.database_url).get("port", 5432)  # never print the URL: it holds a password
        print(f"mocked feed from {start:%a %d %b %H:%M} IST at {a.speed:g}x, database port {port} (Ctrl+C to stop)", flush=True)
        serve(a.database_url, start, a.speed, a.seed, agent, stop, a.candle)
        return
    logging.basicConfig(level=logging.INFO if a.verbose else logging.CRITICAL, format="%(message)s")
    started = datetime.now(IST)
    result = run(a.seed, a.days, a.replay, agent, end_date=a.end, publish=bool(a.export))
    print(f"SYNTHETIC DATA - {a.agent} agent, seed {a.seed}, {a.replay} days")
    for key, value in result.summary().items():
        print(f"{key:>22}: {value}")
    print(f"{'took':>22}: {(datetime.now(IST) - started) / timedelta(seconds=1):.1f}s")
    if a.export:
        export(result, a.export)
        print(f"{'exported':>22}: {a.export}")


if __name__ == "__main__":
    main()

"""CANDLE_MIN=5: decisions, indicators, stop limits and the chart all move to 5-minute candles."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta

import pytest

from engine import simulate
from engine.agent import system_prompt
from engine.agent_base import RecklessAgent, StubAgent
from engine.agent_cli import cli_prompt
from engine.config import IST, Settings
from engine.engine import is_fresh
from engine.feed import SimFeed
from engine.models import Candle
from engine.upstox import UpstoxFeed

from .conftest import TUE, Harness
from .test_replay_invariants import assert_invariants
from .test_upstox_client import ITEMS, Api, bar, ok

FIVE = Settings(candle_min=5)


def test_the_setting_accepts_5_or_15_only() -> None:
    assert Settings.from_env({}).tf == "15m"
    assert Settings.from_env({"CANDLE_MIN": "5"}).tf == "5m"
    for bad in ("1", "3", "10", "60"):
        with pytest.raises(ValueError, match="CANDLE_MIN"):
            Settings.from_env({"CANDLE_MIN": bad})


def test_the_prompt_names_the_candle_size() -> None:
    for prompt in (system_prompt(5), cli_prompt(5)):
        assert "once per 5-minute candle close" in prompt and "Think in 5m ATR" in prompt and "15" not in prompt
        assert "{" not in prompt
    assert "once per 15-minute candle close" in system_prompt(15) and "StructuredOutput" in cli_prompt(15)


def test_freshness_allows_one_late_candle_of_either_size() -> None:
    now = datetime(2026, 11, 24, 12, 0, tzinfo=IST)

    def last(minutes_ago: int) -> list[Candle]:
        return [Candle(now - timedelta(minutes=minutes_ago), 1, 1, 1, 1)]

    assert is_fresh(last(15), now, 5) and not is_fresh(last(16), now, 5)
    assert is_fresh(last(35), now, 15) and not is_fresh(last(36), now, 15)


def test_synthetic_feed_in_5_minute_bars() -> None:
    feed = SimFeed(seed=3, days=3, bar_min=5)
    assert feed.per_day == 174 and feed.bars[1].ts - feed.bars[0].ts == timedelta(minutes=5)
    now = feed.bars[-1].ts + feed.bar
    assert len(feed.candles("5m", 50, now)) == 50 and len(feed.candles("1d", 10, now)) == 3
    with pytest.raises(ValueError, match="5m and 1d"):
        feed.candles("15m", 5, now)


def test_upstox_is_asked_for_5_minute_candles() -> None:
    now = datetime(2026, 11, 24, 9, 16, tzinfo=IST)
    today = datetime(2026, 11, 24, 9, 0, tzinfo=IST)
    intraday = ok({"candles": [bar(today + timedelta(minutes=5 * i), 300 + i) for i in range(4)]})  # 09:15 forming
    api = Api(gzip.compress(json.dumps(ITEMS).encode()), ok({"candles": []}), intraday, ok({"candles": []}), intraday)
    feed = UpstoxFeed(api.client, 5)
    assert [c.c for c in feed.candles("5m", 100, now)] == [300, 301, 302]
    paths = [r.url.raw_path.decode() for r in api.seen]
    assert paths[1].startswith("/v3/historical-candle/MCX_FO%7C2/minutes/5/2026-11-24/2026-11-16")
    assert paths[2] == "/v3/historical-candle/intraday/MCX_FO%7C2/minutes/5"
    feed.candles("5m", 100, now + timedelta(seconds=10))
    assert len(api.seen) == 3  # same candle: cached
    feed.candles("5m", 100, now + timedelta(minutes=5))
    assert len(api.seen) == 5  # the next one closed: fetched again
    with pytest.raises(ValueError):
        feed.candles("15m", 5, now)


def test_engine_decides_every_5_minutes_on_5_minute_data() -> None:
    h = Harness(FIVE, start=datetime(2026, 11, 24, 11, 59, 50, tzinfo=IST))
    feed = SimFeed(seed=7, days=30, bar_min=5, end_date=TUE.date())
    h.engine.feed = feed
    h.engine.startup()
    for _ in range(60 * 11):  # 11 minutes of one-second ticks
        h.engine.tick()
        h.advance(seconds=1)
    assert [s["time_ist"][-5:] for s in h.agent.snapshots] == ["11:59", "12:00", "12:05", "12:10"]
    snapshot = h.agent.snapshots[-1]
    assert "intraday_5m" in snapshot and "intraday_15m" not in snapshot
    assert "stop_distance_in_5m_atr" in snapshot["hard_limits"]
    assert snapshot["intraday_5m"]["ohlc_oldest_to_newest"][-1][0] == "11-24 12:05"
    assert {row["tf"] for row in h.flushed("candles")} == {"5m", "1d"}
    assert h.state("session")["candle_min"] == 5


@pytest.mark.parametrize("agent", [StubAgent(), RecklessAgent(stop_atr=1.0)], ids=["stub", "reckless"])
def test_limits_still_hold_on_5_minute_candles(agent: StubAgent) -> None:
    r = simulate.run(seed=3, replay_days=10, agent=agent, settings=FIVE)
    assert r.trades, "the replay should actually trade"
    assert_invariants(r)


def test_the_candle_being_made_is_published_for_the_chart_only() -> None:
    h = Harness()
    h.feed.live_bar = Candle(TUE.replace(second=0), 300.0, 301.5, 299.5, 301.0, 12.4)
    h.engine.monitor()
    expected = {"tf": "15m", "ts": "2026-11-24T12:00:00+05:30", "o": 300.0, "h": 301.5, "l": 299.5, "c": 301.0, "v": 12}
    assert h.state("forming") == expected
    h.engine.cycle()
    assert "301.5" not in json.dumps(h.agent.snapshots[-1])  # the model decides on closed candles

    def broken(now: datetime) -> Candle | None:
        raise RuntimeError("no data")

    h.feed.forming = broken  # type: ignore[method-assign]
    h.engine.monitor()  # a chart problem must not disturb monitoring
    assert h.state("forming") == expected
    h.feed.forming = lambda now: None  # type: ignore[method-assign]
    h.engine.monitor()
    assert h.state("forming") is None


def test_the_synthetic_forming_candle_agrees_with_its_price() -> None:
    feed = SimFeed(seed=3, days=3, bar_min=5)
    start = feed.bars[40].ts
    for seconds in (0, 60, 150, 299):
        now = start + timedelta(seconds=seconds)
        bar = feed.forming(now)
        assert bar is not None and bar.ts == start and bar.c == feed.ltp(now) and bar.l <= bar.c <= bar.h
        assert bar.h <= feed.bars[40].h and bar.l >= feed.bars[40].l
    assert feed.forming(feed.bars[-1].ts + feed.bar) is None


def test_upstox_forming_candle_is_the_unfinished_one() -> None:
    now = datetime(2026, 11, 24, 9, 12, tzinfo=IST)
    today = datetime(2026, 11, 24, 9, 0, tzinfo=IST)
    rows = ok({"candles": [bar(today + timedelta(minutes=5 * i), 300 + i) for i in (2, 1, 0)]})
    api = Api(gzip.compress(json.dumps(ITEMS).encode()), rows, ok({"candles": []}))
    feed = UpstoxFeed(api.client, 5)
    live = feed.forming(now)
    assert live is not None and (live.ts.minute, live.c) == (10, 302)
    assert feed.forming(now) is None

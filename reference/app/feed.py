"""Market data feeds: UpstoxFeed (real) and SimFeed (synthetic, for offline simulation/tests)."""
from __future__ import annotations

import random
from bisect import bisect_right
from datetime import date, datetime, timedelta
from typing import Protocol

from .config import IST
from .models import Candle, Contract
from .upstox import UpstoxClient


class Feed(Protocol):
    def contract(self) -> Contract: ...
    def candles(self, tf: str, n: int, now: datetime) -> list[Candle]: ...  # tf: "15m" | "1d"
    def ltp(self, now: datetime) -> float: ...


def _aggregate_daily(bars: list[Candle]) -> list[Candle]:
    days: dict[str, list[Candle]] = {}
    for b in bars:
        days.setdefault(b.ts[:10], []).append(b)
    out = []
    for d, bs in sorted(days.items()):
        out.append(Candle(d, bs[0].o, max(x.h for x in bs), min(x.l for x in bs), bs[-1].c, sum(x.v for x in bs)))
    return out


class UpstoxFeed:
    def __init__(self, client: UpstoxClient):
        self.client = client

    def contract(self) -> Contract:
        return self.client.contract()

    def candles(self, tf: str, n: int, now: datetime) -> list[Candle]:
        if tf == "15m":
            bars = self.client.candles("minutes", 15, days_back=6)
            # drop the still-forming bar
            bars = [b for b in bars if datetime.fromisoformat(b.ts) + timedelta(minutes=15) <= now]
            return bars[-n:]
        # Daily: historical days + today's partial bar built from 15m data
        days = self.client.candles("days", 1, days_back=max(60, n * 2))
        today = now.date().isoformat()
        days = [d for d in days if d.ts[:10] != today]
        intraday = [b for b in self.client.candles("minutes", 15, days_back=1) if b.ts[:10] == today]
        if intraday:
            days += _aggregate_daily(intraday)
        for d in days:
            d.ts = d.ts[:10]
        return days[-n:]

    def ltp(self, now: datetime) -> float:
        return self.client.ltp()


class SimFeed:
    """Random-walk MCX-like 15m bars with slow regime drift. Deterministic per seed."""

    def __init__(self, seed: int = 7, days: int = 45, start: float = 290.0, lot_size: int = 250,
                 start_date: date | None = None):
        rnd = random.Random(seed)
        self._contract = Contract("SIM|NATGASMINI", "NATGASMINI SIM FUT", lot_size, "2099-01-01")
        d = start_date or (datetime.now(IST).date() - timedelta(days=days * 2))
        price, drift = start, 0.0
        self.bars: list[Candle] = []
        made = 0
        while made < days:
            if d.weekday() < 5:
                drift = 0.85 * drift + rnd.gauss(0, 0.00012)
                for i in range(58):  # 09:00 .. 23:30
                    t = datetime(d.year, d.month, d.day, 9, 0, tzinfo=IST) + timedelta(minutes=15 * i)
                    o = price
                    pts = [o]
                    for _ in range(3):
                        pts.append(pts[-1] * (1 + drift + rnd.gauss(0, 0.0016)))
                    c = pts[-1]
                    h = max(pts) * (1 + abs(rnd.gauss(0, 0.0005)))
                    l = min(pts) * (1 - abs(rnd.gauss(0, 0.0005)))
                    self.bars.append(Candle(t.isoformat(), round(o, 2), round(h, 2), round(l, 2), round(c, 2), 100))
                    price = c
                made += 1
            d += timedelta(days=1)
        self._ends = [datetime.fromisoformat(b.ts) + timedelta(minutes=15) for b in self.bars]

    def contract(self) -> Contract:
        return self._contract

    def _upto(self, now: datetime) -> list[Candle]:
        """Bars fully closed at `now` (bars are time-sorted, so bisect)."""
        return self.bars[: bisect_right(self._ends, now)]

    def candles(self, tf: str, n: int, now: datetime) -> list[Candle]:
        bars = self._upto(now)
        if tf == "15m":
            return bars[-n:]
        return _aggregate_daily(bars)[-n:]

    def ltp(self, now: datetime) -> float:
        bars = self._upto(now)
        return bars[-1].c

"""Market data. `SimFeed` is synthetic (offline replay); the Upstox feed lives in `upstox.py`."""
from __future__ import annotations

import random
from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Protocol

from .config import IST
from .models import Candle, Contract

SIM_BARS_PER_DAY = 58  # 15-minute bars, 09:00 .. 23:30


class Feed(Protocol):
    def contract(self, now: datetime) -> Contract: ...
    def candles(self, tf: str, n: int, now: datetime) -> list[Candle]:
        """Closed bars only, oldest first. tf is the decision timeframe ("5m"/"15m") or "1d" (today's is partial)."""
        ...
    def ltp(self, now: datetime) -> float: ...
    def forming(self, now: datetime) -> Candle | None:
        """The candle still being made, for the chart only. Decisions never see it."""
        ...
    def bar_range(self, since: datetime, now: datetime) -> tuple[float, float] | None:
        """High/low traded in completed 1-minute bars after `since`, if the feed can tell."""
        ...


def aggregate_daily(bars: Sequence[Candle]) -> list[Candle]:
    days: dict[date, list[Candle]] = {}
    for b in bars:
        days.setdefault(b.ts.astimezone(IST).date(), []).append(b)
    return [
        Candle(
            datetime(d.year, d.month, d.day, tzinfo=IST), bs[0].o, max(x.h for x in bs),
            min(x.l for x in bs), bs[-1].c, sum(x.v for x in bs),
        )
        for d, bs in sorted(days.items())
    ]


def tick(price: float) -> float:
    return round(round(price * 10) / 10, 2)


class SimFeed:
    """Random-walk MCX-like 5m or 15m bars with slow regime drift. Deterministic per seed.

    It exercises plumbing and risk limits. It says nothing about whether any strategy makes money.
    """

    def __init__(
        self,
        seed: int = 7,
        days: int = 45,
        start_price: float = 300.0,
        multiplier: int = 250,
        symbol: str = "NATGASMINI",
        end_date: date = date(2026, 11, 20),
        bar_min: int = 15,
    ):
        self.bar = timedelta(minutes=bar_min)
        self.tf = f"{bar_min}m"
        self.per_day = SIM_BARS_PER_DAY * 15 // bar_min
        wobble = (bar_min / 15) ** 0.5  # shorter bars move less
        rnd = random.Random(seed)
        self._contract = Contract(f"SIM|{symbol}", f"{symbol} SIM FUT", "2099-01-01", multiplier)
        trading_days: list[date] = []
        d = end_date
        while len(trading_days) < days:
            if d.weekday() < 5:
                trading_days.append(d)
            d -= timedelta(days=1)

        price, drift = start_price, 0.0
        self.bars: list[Candle] = []
        for day in reversed(trading_days):
            drift = 0.85 * drift + rnd.gauss(0, 0.00012)
            for i in range(self.per_day):
                t = datetime(day.year, day.month, day.day, 9, 0, tzinfo=IST) + self.bar * i
                o = price
                path = [o]
                for _ in range(3):
                    path.append(path[-1] * (1 + drift + rnd.gauss(0, 0.0016 * wobble)))
                c = path[-1]
                h = max(path) * (1 + abs(rnd.gauss(0, 0.0005 * wobble)))
                low = min(path) * (1 - abs(rnd.gauss(0, 0.0005 * wobble)))
                o_, c_ = tick(o), tick(c)
                self.bars.append(Candle(t, o_, max(tick(h), o_, c_), min(tick(low), o_, c_), c_, float(rnd.randint(50, 900))))
                price = c
        self._ends = [b.ts + self.bar for b in self.bars]
        self._daily = aggregate_daily(self.bars)  # every day holds per_day bars

    def contract(self, now: datetime) -> Contract:
        return self._contract

    def closed_count(self, now: datetime) -> int:
        return bisect_right(self._ends, now)

    def candles(self, tf: str, n: int, now: datetime) -> list[Candle]:
        end = self.closed_count(now)
        if tf == self.tf:
            return self.bars[max(0, end - n) : end]
        if tf != "1d":
            raise ValueError(f"this feed has {self.tf} and 1d bars, not {tf}")
        full_days, partial = divmod(end, self.per_day)
        days = self._daily[:full_days]
        if partial:  # today so far
            days = days + aggregate_daily(self.bars[full_days * self.per_day : end])
        return days[-n:]

    def _walk(self, now: datetime) -> tuple[Candle, list[float]] | None:
        """Inside a forming bar, walk open -> extreme -> other extreme -> close. Returns the prices so far."""
        i = self.closed_count(now)
        if i >= len(self.bars) or self.bars[i].ts > now:
            return None
        b = self.bars[i]
        path = [b.o, b.l, b.h, b.c] if b.c >= b.o else [b.o, b.h, b.l, b.c]
        x = (now - b.ts) / self.bar * 3
        k = min(int(x), 2)
        return b, [*path[: k + 1], tick(path[k] + (path[k + 1] - path[k]) * (x - k))]

    def forming(self, now: datetime) -> Candle | None:
        walked = self._walk(now)
        if walked is None:
            return None
        b, seen = walked
        return Candle(b.ts, b.o, max(seen), min(seen), seen[-1], b.v * ((now - b.ts) / self.bar))

    def ltp(self, now: datetime) -> float:
        walked = self._walk(now)
        if walked:
            return walked[1][-1]
        i = self.closed_count(now)
        if i == 0:
            return self.bars[0].o
        return self.bars[i - 1].c

    def bar_range(self, since: datetime, now: datetime) -> tuple[float, float] | None:
        return None

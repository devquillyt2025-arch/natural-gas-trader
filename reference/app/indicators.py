"""Small dependency-free indicator helpers."""
from __future__ import annotations

from .models import Candle


def ema(values: list[float], n: int) -> list[float]:
    if not values:
        return []
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def atr(candles: list[Candle], n: int = 14) -> float | None:
    if len(candles) < 2:
        return None
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.h - c.l, abs(c.h - p.c), abs(c.l - p.c)))
    window = trs[-n:]
    return sum(window) / len(window)


def swing_levels(candles: list[Candle], lookback: int = 3, max_levels: int = 3) -> dict:
    """Recent pivot highs/lows (fractal with `lookback` bars each side), nearest-to-latest first."""
    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        w = candles[i - lookback : i + lookback + 1]
        if candles[i].h == max(x.h for x in w):
            highs.append(candles[i].h)
        if candles[i].l == min(x.l for x in w):
            lows.append(candles[i].l)
    return {"resistance": highs[::-1][:max_levels], "support": lows[::-1][:max_levels]}


def r2(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)

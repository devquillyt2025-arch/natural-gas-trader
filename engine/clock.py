"""Injected time, so the whole engine is replayable offline."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Protocol

from .config import IST


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(IST)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class ManualClock:
    """Time only moves when told to (tests and bar replay)."""

    def __init__(self, start: datetime):
        self._now = start
        self._mono = 0.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        self._mono += seconds

    def set(self, when: datetime) -> None:
        self.advance(max(0.0, (when - self._now).total_seconds()))
        self._now = when


class ScaledClock:
    """Starts at a chosen market time and runs `speed` times faster than real time (mock paper runs)."""

    def __init__(self, start: datetime, speed: float = 1.0):
        self.start, self.speed = start, speed
        self._t0 = time.monotonic()

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self.monotonic())

    def monotonic(self) -> float:
        return (time.monotonic() - self._t0) * self.speed

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds / self.speed)

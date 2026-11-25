"""A controllable engine: fake price, scripted agent, manual clock, in-memory bus."""
from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import Executor, Future
from datetime import datetime, timedelta
from typing import Any

import pytest

from engine.agent_base import AgentResult
from engine.brokers.base import Broker
from engine.brokers.paper import PaperBroker
from engine.clock import ManualClock
from engine.config import IST, Settings
from engine.engine import Engine, InlineExecutor
from engine.models import Candle, Contract, Decision
from engine.store import LocalStore, MemoryBus, Store

TUE = datetime(2026, 11, 24, 12, 0, 10, tzinfo=IST)


class FakeFeed:
    """Flat bars with a true range of exactly 1.0, so ATR is 1.0; the price is set by the test."""

    def __init__(self, price: float = 300.0):
        self.price = price
        self.fail = False
        self.stale = False
        self.span: tuple[float, float] | None = None
        self.live_bar: Candle | None = None

    def contract(self, now: datetime) -> Contract:
        return Contract("MCX_FO|999", "NATGASMINI FUT 25 DEC 26", "2026-12-24", 250)

    def candles(self, tf: str, n: int, now: datetime) -> list[Candle]:
        if tf == "1d":
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return [Candle(day - timedelta(days=i), 300, 304, 296, 300) for i in range(n - 1, -1, -1)]
        end = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        if self.stale:
            end -= timedelta(hours=3)
        return [Candle(end - timedelta(minutes=15 * (i + 1)), 300, 300.5, 299.5, 300, 10) for i in range(n - 1, -1, -1)]

    def ltp(self, now: datetime) -> float:
        if self.fail:
            raise RuntimeError("quote service down")
        return self.price

    def forming(self, now: datetime) -> Candle | None:
        return self.live_bar

    def bar_range(self, since: datetime, now: datetime) -> tuple[float, float] | None:
        return self.span


def go_long(**kw: Any) -> Decision:
    base: dict[str, Any] = dict(action="ENTER_LONG", lots=1, stop_loss=298.0, target=304.0, confidence=0.8,
                                reasoning="break and hold of 300")
    base.update(kw)
    return Decision(**base)


class ScriptedAgent:
    def __init__(self) -> None:
        self.queue: list[Decision | AgentResult | Exception] = []
        self.snapshots: list[Mapping[str, Any]] = []
        self.brief = "storage draw expected"

    @property
    def calls(self) -> int:
        return len(self.snapshots)

    def news(self, now: datetime) -> str:
        return self.brief

    def decide(self, snapshot: Mapping[str, Any]) -> AgentResult:
        self.snapshots.append(snapshot)
        item = self.queue.pop(0) if self.queue else Decision("HOLD", confidence=0.3, reasoning="nothing here")
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, AgentResult) else AgentResult(item)


class HeldExecutor(Executor):
    """The model "is still thinking" until the test calls release()."""

    def __init__(self) -> None:
        self.job: Any = None
        self.future: Future[Any] = Future()

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.job, self.future = fn, Future()
        return self.future

    def release(self) -> None:
        self.future.set_result(self.job())


class Harness:
    def __init__(self, settings: Settings | None = None, start: datetime = TUE, executor: Executor | None = None,
                 bus: MemoryBus | None = None, local: LocalStore | None = None):
        self.s = settings or Settings()
        self.clock = ManualClock(start)
        self.bus = bus or MemoryBus()
        self.local = local or LocalStore()
        self.store = Store(self.local, self.bus, self.s.mode, self.clock)
        self.feed = FakeFeed()
        self.agent = ScriptedAgent()
        self.broker: Broker = PaperBroker(self.s, self.store)
        self.engine = Engine(
            self.s, self.store, self.feed, self.broker, self.agent, self.clock,
            executor=executor or InlineExecutor(), wall=self.clock.now, instance_id="test",
        )

    def advance(self, **kw: float) -> None:
        self.clock.advance(timedelta(**kw).total_seconds())
        self.store.fetch_commands()  # the running engine would have kept polling

    def at(self, hh: int, mm: int, ss: int = 0) -> datetime:
        self.clock.set(self.clock.now().replace(hour=hh, minute=mm, second=ss))
        self.store.fetch_commands()
        return self.clock.now()

    def command(self, type_: str, payload: dict[str, Any] | None = None, age_sec: float = 0) -> dict[str, Any]:
        from engine import commands

        ts = (self.clock.now() - timedelta(seconds=age_sec)).isoformat()
        cmd = self.bus.add_command(type_, payload, ts)
        commands.process(self.engine)
        return next(c for c in self.bus.commands if c["id"] == cmd["id"])

    def open_long(self) -> None:
        self.agent.queue.append(go_long())
        record = self.engine.cycle()
        assert record and record.approved, record
        assert self.broker.position() is not None

    def flushed(self, table: str) -> list[dict[str, Any]]:
        self.store.flush(5000)
        return self.bus.rows(table)

    def state(self, key: str) -> Any:
        self.store.flush(5000)
        return self.bus.state_value(key)


@pytest.fixture
def h() -> Harness:
    return Harness()

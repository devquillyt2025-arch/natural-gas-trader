"""Broker contract shared by the paper and live implementations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..models import Position, Trade
from ..store import Store


class BrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class EntryRequest:
    side: str  # LONG | SHORT
    lots: int
    multiplier: int
    ltp: float
    stop: float
    target: float
    instrument_key: str
    decision_id: int | None
    reasoning: str
    now: datetime


class Broker(Protocol):
    def position(self) -> Position | None: ...
    def orphan(self) -> bool: ...
    def startup(self, now: datetime) -> None: ...
    def enter(self, req: EntryRequest) -> Position: ...
    def move_stop(self, new_stop: float) -> None: ...
    def exit(self, ltp: float, reason: str, now: datetime) -> Trade | None: ...
    def poll(
        self, ltp: float, now: datetime, hi: float | None = None, lo: float | None = None, open_: float | None = None
    ) -> Trade | None: ...


def load_position(store: Store, mode: str) -> Position | None:
    data = store.get(f"position_{mode}")
    return Position.from_dict(data) if data else None


def save_position(store: Store, mode: str, position: Position | None) -> None:
    store.set(f"position_{mode}", position.to_dict() if position else None)


def build_trade(p: Position, exit_price: float, reason: str, now: datetime, cost_per_lot: float) -> Trade:
    """The trade id is the position id, so recording the same close twice cannot double-count it."""
    pnl = (exit_price - p.entry) * p.direction * p.qty - cost_per_lot * p.lots
    return Trade(
        id=p.id,
        opened_at=p.opened_at,
        closed_at=now.isoformat(timespec="seconds"),
        side=p.side,
        lots=p.lots,
        qty=p.qty,
        entry=round(p.entry, 2),
        exit=round(exit_price, 2),
        pnl=round(pnl, 2),
        r_multiple=p.r_multiple(pnl),
        exit_reason=reason,
        reasoning=p.reasoning,
        decision_id=p.decision_id,
        mode=p.mode,
    )

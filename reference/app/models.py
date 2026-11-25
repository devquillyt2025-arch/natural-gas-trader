from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

ACTIONS = ("ENTER_LONG", "ENTER_SHORT", "HOLD", "EXIT", "TRAIL_STOP")


@dataclass
class Decision:
    action: str = "HOLD"
    lots: int = 1
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    confidence: float = 0.0
    reasoning: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Position:
    side: str  # LONG | SHORT
    lots: int
    qty: int
    entry: float
    stop_loss: float
    target: Optional[float]
    opened_at: str
    instrument_key: str
    sl_order_id: Optional[str] = None
    reasoning: str = ""

    @property
    def direction(self) -> int:
        return 1 if self.side == "LONG" else -1

    def unrealized(self, ltp: float) -> float:
        return (ltp - self.entry) * self.direction * self.qty

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Position":
        return Position(**d)


@dataclass
class Contract:
    instrument_key: str
    trading_symbol: str
    lot_size: int
    expiry: str  # ISO date


@dataclass
class Candle:
    ts: str  # ISO timestamp (IST)
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0

    def row(self) -> list:
        return [self.ts, self.o, self.h, self.l, self.c, self.v]

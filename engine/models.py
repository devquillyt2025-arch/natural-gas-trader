"""Plain data types shared by the engine. No I/O here."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any

ACTIONS = ("ENTER_LONG", "ENTER_SHORT", "HOLD", "EXIT", "TRAIL_STOP")
ENTRY_ACTIONS = ("ENTER_LONG", "ENTER_SHORT")


@dataclass(frozen=True)
class Decision:
    action: str = "HOLD"
    lots: int = 1
    stop_loss: float | None = None
    target: float | None = None
    confidence: float = 0.0
    reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Position:
    """An open position the app owns. Money maths always uses lots x multiplier (mmBtu)."""

    id: str
    side: str  # LONG | SHORT
    lots: int  # also the order quantity sent to Upstox (MCX quantity is counted in lots)
    multiplier: int  # mmBtu per lot
    entry: float
    stop_loss: float
    target: float | None
    initial_stop: float  # for R-multiples; never changes after entry
    opened_at: str  # ISO 8601 with offset
    instrument_key: str
    mode: str
    decision_id: int | None = None
    reasoning: str = ""
    # --- live broker bookkeeping --------------------------------------------------------
    sl_order_id: str | None = None
    exit_order_id: str | None = None
    exit_tag: str | None = None  # an exit was sent but we do not yet know its order id
    exit_order_at: str | None = None
    flatten_reason: str | None = None  # set while an exit is wanted but not yet confirmed
    broker_unit: int = 1  # what Upstox positions report per lot (1 or the multiplier)

    @property
    def direction(self) -> int:
        return 1 if self.side == "LONG" else -1

    @property
    def qty(self) -> int:
        return self.lots * self.multiplier

    def unrealized(self, ltp: float) -> float:
        return (ltp - self.entry) * self.direction * self.qty

    def initial_risk(self) -> float:
        return abs(self.entry - self.initial_stop) * self.qty

    def r_multiple(self, pnl: float) -> float | None:
        risk = self.initial_risk()
        return round(pnl / risk, 2) if risk > 0 else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Position:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass(frozen=True)
class Contract:
    instrument_key: str
    trading_symbol: str
    expiry: str  # ISO date
    multiplier: int  # mmBtu per lot, from our own spec table (not trusted from the API)
    tick_size: float = 0.10


@dataclass(frozen=True)
class Candle:
    ts: datetime  # bar open time, timezone-aware (IST)
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


@dataclass(frozen=True)
class Trade:
    id: str
    opened_at: str
    closed_at: str
    side: str
    lots: int
    qty: int
    entry: float
    exit: float
    pnl: float
    r_multiple: float | None
    exit_reason: str
    reasoning: str
    decision_id: int | None
    mode: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Trade:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass(frozen=True)
class DecisionRecord:
    id: int
    ts: str
    price: float | None
    action: str
    lots: int | None
    stop_loss: float | None
    target: float | None
    confidence: float | None
    reasoning: str
    approved: bool
    verdict_code: str
    verdict: str
    mode: str
    snapshot: dict[str, Any] | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        # shallow on purpose: asdict() would deep-copy the snapshot only for it to be serialised
        return {f.name: getattr(self, f.name) for f in fields(self)}

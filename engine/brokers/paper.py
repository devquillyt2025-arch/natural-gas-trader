"""Simulated fills: LTP plus/minus slippage, fixed round-trip costs, stop assumed to fill before target."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ..config import Settings
from ..models import Position, Trade
from ..store import Store
from .base import BrokerError, EntryRequest, build_trade, load_position, save_position


class PaperBroker:
    mode = "paper"

    def __init__(self, settings: Settings, store: Store):
        self.s, self.store = settings, store

    def position(self) -> Position | None:
        return load_position(self.store, self.mode)

    def orphan(self) -> bool:
        return False

    def startup(self, now: datetime) -> None:
        return None

    def enter(self, req: EntryRequest) -> Position:
        if self.position():
            raise BrokerError("already in a position")
        direction = 1 if req.side == "LONG" else -1
        p = Position(
            id=f"t{self.store.new_id()}", side=req.side, lots=req.lots, multiplier=req.multiplier,
            entry=round(req.ltp + direction * self.s.slippage_inr, 2), stop_loss=req.stop, target=req.target,
            initial_stop=req.stop, opened_at=req.now.isoformat(timespec="seconds"),
            instrument_key=req.instrument_key, mode=self.mode, decision_id=req.decision_id, reasoning=req.reasoning,
        )
        save_position(self.store, self.mode, p)
        return p

    def move_stop(self, new_stop: float) -> None:
        p = self.position()
        if not p:
            raise BrokerError("no position to move a stop on")
        if (new_stop - p.stop_loss) * p.direction <= 0:
            raise BrokerError("a stop can only be tightened")
        save_position(self.store, self.mode, replace(p, stop_loss=new_stop))

    def _close(self, p: Position, price: float, reason: str, now: datetime) -> Trade:
        trade = build_trade(p, price, reason, now, self.s.cost_per_lot_inr)
        self.store.save_trade(trade)
        save_position(self.store, self.mode, None)
        return trade

    def exit(self, ltp: float, reason: str, now: datetime) -> Trade | None:
        p = self.position()
        if not p:
            return None
        return self._close(p, ltp - p.direction * self.s.slippage_inr, reason, now)

    def poll(
        self, ltp: float, now: datetime, hi: float | None = None, lo: float | None = None, open_: float | None = None
    ) -> Trade | None:
        p = self.position()
        if not p:
            return None
        high = ltp if hi is None else max(hi, ltp)
        low = ltp if lo is None else min(lo, ltp)
        d, slip = p.direction, self.s.slippage_inr
        worst, best = (low, high) if d == 1 else (high, low)

        if (worst - p.stop_loss) * d <= 0:  # stop first: conservative when both are touched in one bar
            # A gap through the stop fills at the gap price, not at the stop.
            gap = open_ if open_ is not None else (ltp if hi is None else p.stop_loss)
            ref = min(p.stop_loss, gap) if d == 1 else max(p.stop_loss, gap)
            return self._close(p, ref - d * slip, "stop", now)
        if p.target is not None and (best - p.target) * d >= 0:
            return self._close(p, p.target - d * slip, "target", now)
        return None

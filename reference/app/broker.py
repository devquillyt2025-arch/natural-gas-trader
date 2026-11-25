"""Brokers: PaperBroker (simulated fills) and LiveBroker (Upstox, exchange-side stop always attached)."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional, Protocol

from .config import Settings
from .models import Position
from .store import Store
from .upstox import UpstoxClient, UpstoxError

log = logging.getLogger("broker")


class Broker(Protocol):
    def position(self) -> Optional[Position]: ...
    def orphan(self) -> bool: ...
    def enter(self, side: str, lots: int, qty: int, ltp: float, sl: float, target: float,
              reasoning: str, now: datetime) -> Position: ...
    def move_stop(self, new_sl: float) -> None: ...
    def exit(self, ltp: float, reason: str, now: datetime) -> Optional[dict]: ...
    def poll(self, ltp: float, now: datetime, hi: Optional[float] = None, lo: Optional[float] = None) -> Optional[dict]: ...


def _trade(s: Settings, p: Position, exit_px: float, reason: str, now: datetime) -> dict:
    pnl = (exit_px - p.entry) * p.direction * p.qty - s.cost_per_lot_inr * p.lots
    return {
        "opened_at": p.opened_at, "closed_at": now.isoformat(timespec="seconds"), "side": p.side,
        "lots": p.lots, "qty": p.qty, "entry": round(p.entry, 2), "exit": round(exit_px, 2),
        "pnl": round(pnl, 2), "exit_reason": reason, "reasoning": p.reasoning, "mode": "paper",
    }


# ---------------------------------------------------------------------------------------
class PaperBroker:
    def __init__(self, s: Settings, store: Store, instrument_key: str = "PAPER"):
        self.s, self.store, self.key = s, store, instrument_key

    def position(self) -> Optional[Position]:
        d = self.store.get("position")
        return Position.from_dict(d) if d else None

    def orphan(self) -> bool:
        return False

    def _save(self, p: Optional[Position]) -> None:
        self.store.set("position", p.to_dict() if p else None)

    def enter(self, side, lots, qty, ltp, sl, target, reasoning, now) -> Position:
        d = 1 if side == "LONG" else -1
        p = Position(side, lots, qty, round(ltp + d * self.s.slippage_inr, 2), sl, target,
                     now.isoformat(timespec="seconds"), self.key, reasoning=reasoning)
        self._save(p)
        return p

    def move_stop(self, new_sl: float) -> None:
        p = self.position()
        if p:
            p.stop_loss = new_sl
            self._save(p)

    def _close(self, p: Position, px: float, reason: str, now: datetime) -> dict:
        t = _trade(self.s, p, px, reason, now)
        t["mode"] = "paper"
        self.store.log_trade(t)
        self._save(None)
        return t

    def exit(self, ltp, reason, now) -> Optional[dict]:
        p = self.position()
        if not p:
            return None
        return self._close(p, ltp - p.direction * self.s.slippage_inr, reason, now)

    def poll(self, ltp, now, hi=None, lo=None) -> Optional[dict]:
        p = self.position()
        if not p:
            return None
        hi = ltp if hi is None else max(hi, ltp)
        lo = ltp if lo is None else min(lo, ltp)
        slip = self.s.slippage_inr
        if p.side == "LONG":
            if lo <= p.stop_loss:  # stop checked first: conservative when both hit in one bar
                return self._close(p, p.stop_loss - slip, "stop", now)
            if p.target and hi >= p.target:
                return self._close(p, p.target, "target", now)
        else:
            if hi >= p.stop_loss:
                return self._close(p, p.stop_loss + slip, "stop", now)
            if p.target and lo <= p.target:
                return self._close(p, p.target, "target", now)
        return None


# ---------------------------------------------------------------------------------------
class LiveBroker:
    """Real orders. Invariant: never leave a position without an exchange-side stop order."""

    def __init__(self, s: Settings, store: Store, client: UpstoxClient):
        self.s, self.store, self.c = s, store, client
        self._orphan = False

    # -- state ------------------------------------------------------------------------
    def position(self) -> Optional[Position]:
        d = self.store.get("position")
        return Position.from_dict(d) if d else None

    def orphan(self) -> bool:
        return self._orphan

    def _save(self, p: Optional[Position]) -> None:
        self.store.set("position", p.to_dict() if p else None)

    # -- helpers ----------------------------------------------------------------------
    def _fill(self, order_id: str, timeout: float = 15.0) -> Optional[float]:
        end = time.time() + timeout
        while time.time() < end:
            d = self.c.order_details(order_id)
            st = str(d.get("status", "")).lower()
            if st == "complete":
                return float(d.get("average_price") or d.get("price"))
            if st in ("rejected", "cancelled"):
                raise UpstoxError(f"order {order_id} {st}: {d.get('status_message')}")
            time.sleep(0.7)
        return None

    def _place_stop(self, side: str, qty: int, sl: float) -> str:
        """SL-M first; if the exchange rejects it for this contract, fall back to SL-limit with a buffer."""
        try:
            return self.c.place_order(side, qty, "SL-M", trigger=sl, tag="natgas-sl")
        except UpstoxError as e:
            log.warning("SL-M rejected (%s); falling back to SL-limit", e)
            buf = max(0.5, round(0.004 * sl, 2))
            px = round(sl - buf, 2) if side == "SELL" else round(sl + buf, 2)
            return self.c.place_order(side, qty, "SL", trigger=sl, price=px, tag="natgas-sl")

    @staticmethod
    def _opp(side: str) -> str:
        return "SELL" if side == "LONG" else "BUY"

    # -- actions ----------------------------------------------------------------------
    def enter(self, side, lots, qty, ltp, sl, target, reasoning, now) -> Position:
        oid = self.c.place_order("BUY" if side == "LONG" else "SELL", qty, "MARKET", tag="natgas-in")
        px = self._fill(oid)
        if px is None:
            try:
                self.c.cancel_order(oid)
            except UpstoxError:
                pass
            if self.c.net_qty() == 0:
                raise UpstoxError("entry not filled in time; cancelled")
            px = ltp  # filled during cancel race: continue and protect it
        p = Position(side, lots, qty, px, sl, target, now.isoformat(timespec="seconds"),
                     self.c.contract().instrument_key, reasoning=reasoning)
        try:
            p.sl_order_id = self._place_stop(self._opp(side), qty, sl)
        except UpstoxError as e:
            log.error("could not place stop, flattening: %s", e)
            self.c.place_order(self._opp(side), qty, "MARKET", tag="natgas-panic")
            raise UpstoxError(f"stop order failed, position flattened: {e}")
        self._save(p)
        return p

    def move_stop(self, new_sl: float) -> None:
        p = self.position()
        if not p:
            return
        old_sl = p.stop_loss
        try:
            if p.sl_order_id:
                self.c.cancel_order(p.sl_order_id)
            p.sl_order_id = self._place_stop(self._opp(p.side), p.qty, new_sl)
            p.stop_loss = new_sl
        except UpstoxError as e:
            log.error("stop move failed (%s); restoring old stop", e)
            try:
                p.sl_order_id = self._place_stop(self._opp(p.side), p.qty, old_sl)
            except UpstoxError:
                self.c.place_order(self._opp(p.side), p.qty, "MARKET", tag="natgas-panic")
                self._save(None)
                raise UpstoxError("could not keep a stop in place; position flattened")
            self._save(p)
            raise UpstoxError(f"stop move failed, old stop restored: {e}")
        self._save(p)

    def exit(self, ltp, reason, now) -> Optional[dict]:
        p = self.position()
        if not p:
            return None
        if p.sl_order_id:
            try:
                self.c.cancel_order(p.sl_order_id)
            except UpstoxError:
                pass  # may already be triggered/complete
        if self.c.net_qty() == 0:  # stop already fired: do NOT send an exit (it would open the opposite side)
            return self._closed_by_stop(p, now)
        oid = self.c.place_order(self._opp(p.side), p.qty, "MARKET", tag="natgas-out")
        px = self._fill(oid) or ltp
        t = _trade(self.s, p, px, reason, now)
        t["mode"] = "live"
        self.store.log_trade(t)
        self._save(None)
        return t

    def _closed_by_stop(self, p: Position, now: datetime) -> dict:
        px = p.stop_loss
        if p.sl_order_id:
            try:
                d = self.c.order_details(p.sl_order_id)
                if str(d.get("status", "")).lower() == "complete":
                    px = float(d.get("average_price") or px)
            except UpstoxError:
                pass
        t = _trade(self.s, p, px, "stop/closed at broker", now)
        t["mode"] = "live"
        self.store.log_trade(t)
        self._save(None)
        return t

    def poll(self, ltp, now, hi=None, lo=None) -> Optional[dict]:
        p = self.position()
        net = self.c.net_qty()
        if not p:
            self._orphan = net != 0
            return None
        self._orphan = False
        if net == 0:
            return self._closed_by_stop(p, now)
        if p.target and ((p.side == "LONG" and ltp >= p.target) or (p.side == "SHORT" and ltp <= p.target)):
            return self.exit(ltp, "target", now)
        return None

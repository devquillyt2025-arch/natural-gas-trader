"""Real orders on Upstox.

Invariants, in priority order:
  1. A position is never left without an exchange-side stop order. If a stop cannot be placed, flatten.
  2. An exit is never sent if the stop already filled (it would open the opposite side).
  3. Order writes are never blindly retried. An unknown outcome is resolved from the order book and
     positions; when in doubt, flatten, halt new entries and alert.
  4. A position is only forgotten once it is confirmed closed.

Entries and exits are MARKET orders. If the exchange refuses those, a LIMIT order priced through the
market is used instead.

Quantities sent to Upstox are LOTS. What the positions endpoint reports per lot (1 or the contract
size) is learnt from the first reading, and anything bigger than expected is treated as an emergency.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from ..clock import Clock
from ..config import Settings
from ..models import Position, Trade
from ..store import Store
from ..upstox import BrokerPosition, MCXApiBlocked, NotAuthenticated, OrderInfo, OrderRejected, UpstoxError
from .base import BrokerError, EntryRequest, build_trade, load_position, save_position

log = logging.getLogger("live")

DONE = "complete"
DEAD = ("rejected", "cancelled")
FINAL = (DONE, *DEAD)
STOP_RESTING = ("trigger pending", "open")
NATGAS_PREFIXES = ("NATURALGAS", "NATGASMINI")

FILL_TIMEOUT = 15.0
CONFIRM_TIMEOUT = 6.0
POLL_EVERY = 0.5
LAG_GRACE = 30.0  # positions can lag a fresh fill by a few seconds
EXIT_RESEND_AFTER = 30.0
# Used only if the exchange refuses a MARKET order: how far through the price a limit order may fill.
ENTRY_LIMIT_BUFFER = 0.003
EXIT_LIMIT_BUFFER = 0.01


class OrderClient(Protocol):
    def place_order(self, instrument_key: str, side: str, lots: int, order_type: str, tag: str,
                    price: float = 0.0, trigger_price: float = 0.0) -> str: ...
    def cancel_order(self, order_id: str) -> None: ...
    def order_details(self, order_id: str) -> OrderInfo: ...
    def order_book(self) -> list[OrderInfo]: ...
    def positions(self) -> list[BrokerPosition]: ...


Alert = Callable[[str, str], None]  # (key for de-duplication, message)


def _base36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out or "0"


def _exit_side(side: str) -> str:
    return "SELL" if side == "LONG" else "BUY"


def _is_natgas(p: BrokerPosition) -> bool:
    on_mcx = p.exchange.upper() == "MCX" or p.instrument_key.startswith("MCX")
    return on_mcx and p.trading_symbol.upper().replace(" ", "").startswith(NATGAS_PREFIXES)


class LiveBroker:
    mode = "live"

    def __init__(self, settings: Settings, store: Store, client: OrderClient, clock: Clock, alert: Alert):
        self.s, self.store, self.c, self.clock, self.alert = settings, store, client, clock, alert
        self._orphan = False

    # ------------------------------------------------------------------ state
    def position(self) -> Position | None:
        return load_position(self.store, self.mode)

    def _save(self, p: Position | None) -> None:
        save_position(self.store, self.mode, p)

    def orphan(self) -> bool:
        return self._orphan

    def _halt(self, why: str) -> None:
        """Block new entries until a human presses Resume."""
        self.store.set("safety_halt", why)
        self.alert(f"halt:{why}", f"SAFETY HALT: {why}. New entries are blocked until you press Resume.")

    def _tag(self, kind: str) -> str:
        return f"ng-{kind}-{_base36(self.store.new_id())}"

    # ------------------------------------------------------------------ market (or marketable limit)
    def _market_refused(self) -> bool:
        return bool(self.store.get("market_refused_on") == self.clock.now().date().isoformat())

    def _refuse_market(self, why: str) -> None:
        self.store.set("market_refused_on", self.clock.now().date().isoformat())
        self.alert("market-refused", f"MARKET order refused; using marketable LIMIT orders today: {why}")

    def _send_market(
        self,
        instrument_key: str,
        side: str,
        lots: int,
        kind: str,
        ltp: float,
        buffer: float,
        before: Callable[[str], None],
    ) -> str:
        """MARKET, or a LIMIT priced through the market if the exchange refuses MARKET orders.

        `before(tag)` runs ahead of every attempt, so the caller always knows which tag may be live.
        """
        if not self._market_refused():
            tag = self._tag(kind)
            before(tag)
            try:
                return self.c.place_order(instrument_key, side, lots, "MARKET", tag)
            except MCXApiBlocked:
                raise
            except OrderRejected as e:  # definitely not placed, so a second order cannot double up
                self._refuse_market(str(e))
        tag = self._tag(kind)
        before(tag)
        price = round(ltp * (1 + buffer if side == "BUY" else 1 - buffer), 1)
        return self.c.place_order(instrument_key, side, lots, "LIMIT", tag, price=price)

    def _age(self, iso: str | None) -> float:
        return (self.clock.now() - datetime.fromisoformat(iso)).total_seconds() if iso else float("inf")

    # ------------------------------------------------------------------ order helpers
    def _details(self, order_id: str) -> OrderInfo | None:
        try:
            return self.c.order_details(order_id)
        except UpstoxError as e:
            log.warning("order details failed for %s: %s", order_id, e)
            return None

    def _wait(self, order_id: str, wanted: tuple[str, ...], timeout: float) -> OrderInfo | None:
        """Poll until the status is one of `wanted`; returns the last thing seen (None if nothing)."""
        deadline = self.clock.monotonic() + timeout
        last: OrderInfo | None = None
        while True:
            info = self._details(order_id)
            if info:
                last = info
                if info.status in wanted:
                    return info
            if self.clock.monotonic() >= deadline:
                return last
            self.clock.sleep(POLL_EVERY)

    def _cancel(self, order_id: str) -> OrderInfo | None:
        """Ask for a cancel and report where the order actually ended up."""
        try:
            self.c.cancel_order(order_id)
        except UpstoxError as e:
            log.info("cancel %s: %s", order_id, e)  # it may already be filled; the status decides
        return self._wait(order_id, FINAL, CONFIRM_TIMEOUT)

    def _by_tag(self, tag: str) -> list[OrderInfo] | None:
        try:
            return [o for o in self.c.order_book() if o.tag == tag]
        except UpstoxError as e:
            log.warning("order book unavailable: %s", e)
            return None

    def _net(self, instrument_key: str, positions: list[BrokerPosition] | None = None) -> int:
        positions = self.c.positions() if positions is None else positions
        return sum(p.quantity for p in positions if p.instrument_key == instrument_key)

    def _foreign(self, positions: list[BrokerPosition], own_key: str | None) -> bool:
        return any(p.quantity and p.instrument_key != own_key and _is_natgas(p) for p in positions)

    def _sync_unit(self, p: Position, net: int) -> Position:
        size = abs(net)
        for unit in (1, p.multiplier):
            if size == p.lots * unit and unit != p.broker_unit:
                p = replace(p, broker_unit=unit)
                self._save(p)
        return p

    @staticmethod
    def _open_lots(p: Position, net: int) -> int:
        if net * p.direction <= 0:
            return 0
        return min(p.lots, max(1, abs(net) // p.broker_unit))

    # ------------------------------------------------------------------ protective stop
    def _stop_price(self, side: str, trigger: float) -> float:
        buffer = max(0.5, round(0.004 * trigger, 1))
        return round(trigger - buffer if side == "SELL" else trigger + buffer, 2)

    def _place_stop(self, p: Position, trigger: float) -> str:
        """SL-M first, SL (limit with a buffer) if the exchange refuses. Returns only a confirmed order."""
        side = _exit_side(p.side)
        reasons: list[str] = []
        for order_type in ("SL-M", "SL"):
            price = self._stop_price(side, trigger) if order_type == "SL" else 0.0
            tag = self._tag("s")
            try:
                order_id = self.c.place_order(p.instrument_key, side, p.lots, order_type, tag,
                                              price=price, trigger_price=trigger)
            except MCXApiBlocked as e:
                raise BrokerError(str(e)) from e
            except (OrderRejected, NotAuthenticated) as e:  # definitely not placed
                reasons.append(f"{order_type}: {e}")
                continue
            except UpstoxError as e:  # unknown: look for it rather than placing a second one
                found = self._by_tag(tag)
                if not found:
                    self.store.set("check_strays", True)
                    raise BrokerError(f"{order_type} outcome unknown: {e}") from e
                order_id = found[0].order_id
            info = self._wait(order_id, (*STOP_RESTING, *FINAL), CONFIRM_TIMEOUT)
            if info and info.status in (*STOP_RESTING, DONE):
                return order_id
            if info and info.status in DEAD:  # exchange rejected it after accepting the request
                reasons.append(f"{order_type}: {info.status} {info.message}")
                continue
            self._cancel(order_id)
            self.store.set("check_strays", True)
            raise BrokerError(f"{order_type} could not be confirmed")
        raise BrokerError("no stop order accepted: " + "; ".join(reasons))

    def _restore_stop(self, p: Position) -> bool:
        if p.sl_order_id:
            return True
        # A stop we placed but never recorded (crash, unknown outcome) must not end up doubled.
        self._cancel_strays(keep=None)
        try:
            order_id = self._place_stop(p, p.stop_loss)
        except BrokerError as e:
            self.alert("naked", f"POSITION HAS NO STOP and one could not be placed: {e}")
            return False
        self._save(replace(p, sl_order_id=order_id))
        return True

    def _cancel_strays(self, keep: str | None) -> None:
        """Remove stops of ours that protect nothing: left resting, they could open a position."""
        try:
            orders = self.c.order_book()
        except UpstoxError as e:
            log.warning("could not check for stray stops: %s", e)
            self.store.set("check_strays", True)
            return
        clean = True
        for o in orders:
            if o.tag.startswith("ng-s-") and o.status not in FINAL and o.order_id != keep:
                info = self._cancel(o.order_id)
                self.alert(f"stray:{o.order_id}", f"cancelled a stray stop order {o.order_id}")
                clean = clean and bool(info and info.status in FINAL)
        self.store.set("check_strays", not clean)

    # ------------------------------------------------------------------ startup
    def startup(self, now: datetime) -> None:
        p = self.position()
        try:
            positions = self.c.positions()
        except UpstoxError as e:
            self.alert("positions", f"cannot read broker positions at startup: {e}")
            return
        self._orphan = self._foreign(positions, p.instrument_key if p else None)
        if p:
            self.alert("recovered", f"recovered an open {p.side} position from disk; reconciling")
            if p.sl_order_id:
                self._cancel_strays(keep=p.sl_order_id)
        elif not self.store.get("pending_entry"):
            self._cancel_strays(keep=None)

    # ------------------------------------------------------------------ entry
    def enter(self, req: EntryRequest) -> Position:
        if self.position() or self.store.get("pending_entry"):
            raise BrokerError("already in a position, or the last entry is still unresolved")
        try:
            positions = self.c.positions()
        except UpstoxError as e:
            raise BrokerError(f"cannot read positions before entry: {e}") from e
        if self._foreign(positions, None):
            self._orphan = True
            raise BrokerError("the broker already holds a natural gas position")

        intent: dict[str, Any] = {
            "id": f"t{self.store.new_id()}", "side": req.side, "lots": req.lots, "multiplier": req.multiplier,
            "instrument_key": req.instrument_key, "stop": req.stop, "target": req.target, "tag": "",
            "decision_id": req.decision_id, "reasoning": req.reasoning, "ltp": req.ltp,
            "at": req.now.isoformat(timespec="seconds"),
        }
        buy_sell = "BUY" if req.side == "LONG" else "SELL"

        def record(tag: str) -> None:  # written BEFORE each order, so a crash cannot lose it
            intent["tag"] = tag
            self.store.set("pending_entry", intent)

        try:
            order_id = self._send_market(
                req.instrument_key, buy_sell, req.lots, "e", req.ltp, ENTRY_LIMIT_BUFFER, record
            )
        except MCXApiBlocked as e:
            self.store.set("pending_entry", None)
            self._halt("Upstox rejects MCX orders via the API (UDAPI1161)")
            raise BrokerError(str(e)) from e
        except (OrderRejected, NotAuthenticated) as e:
            self.store.set("pending_entry", None)
            raise BrokerError(f"entry rejected: {e}") from e
        except UpstoxError as e:
            self._halt("an entry order had an unknown outcome")
            self._resolve_intent(req.ltp)
            raise BrokerError(f"entry outcome unknown: {e}") from e

        info = self._wait(order_id, FINAL, FILL_TIMEOUT)
        if info is None or info.status != DONE:
            if info and info.status == "rejected" and not info.filled_quantity:
                self.store.set("pending_entry", None)
                if "market" in info.message.lower():
                    self._refuse_market(info.message)
                raise BrokerError(f"entry rejected: {info.message}")
            self._halt("an entry order did not fill cleanly")
            self._cancel(order_id)
            self._resolve_intent(req.ltp)
            raise BrokerError(f"entry not filled ({info.status if info else 'no status'}); cancelled")

        p = self._position_from(intent, req.lots, info.average_price or req.ltp)
        self._save(p)  # persisted before the stop: a restart would still see it and protect it
        self.store.set("pending_entry", None)
        try:
            p = replace(p, sl_order_id=self._place_stop(p, req.stop))
        except BrokerError as e:
            self._halt("a stop order could not be placed")
            self.exit(req.ltp, "forced: stop placement failed", self.clock.now())
            raise BrokerError(f"stop order failed, flattening: {e}") from e
        self._save(p)
        return self._verify_size(p, req.ltp)

    def _position_from(self, intent: dict[str, Any], lots: int, fill: float) -> Position:
        return Position(
            id=intent["id"], side=intent["side"], lots=lots, multiplier=intent["multiplier"], entry=fill,
            stop_loss=intent["stop"], target=intent["target"], initial_stop=intent["stop"],
            opened_at=intent["at"], instrument_key=intent["instrument_key"], mode=self.mode,
            decision_id=intent["decision_id"], reasoning=intent["reasoning"],
        )

    def _verify_size(self, p: Position, ltp: float) -> Position:
        try:
            net = self._net(p.instrument_key)
        except UpstoxError:
            return p  # the monitor reconciles on its next tick
        size = abs(net)
        if size > p.lots * p.multiplier or net * p.direction < 0:
            self._emergency_flatten(p, net, ltp)
            raise BrokerError(f"position size {net} does not match the order; flattened everything")
        return self._sync_unit(p, net)

    def _emergency_flatten(self, p: Position, net: int, ltp: float) -> None:
        """The broker holds far more than we asked for. We were flat before, so close all of it."""
        self._halt(f"broker position {net} is far larger than ordered")
        if p.sl_order_id:
            self._cancel(p.sl_order_id)
            p = replace(p, sl_order_id=None)
            self._save(p)
        quantity = abs(net)
        lots = quantity // p.multiplier if quantity % p.multiplier == 0 else quantity
        side = "SELL" if net > 0 else "BUY"
        try:
            self._send_market(p.instrument_key, side, lots, "x", ltp, EXIT_LIMIT_BUFFER, lambda tag: None)
        except UpstoxError as e:
            self.alert("emergency", f"EMERGENCY FLATTEN FAILED, close manually in Upstox now: {e}")
            return
        self.alert("emergency", f"EMERGENCY: closed {lots} lots; verify in Upstox now")
        self._finalize(p, ltp, "forced: size mismatch", self.clock.now())

    def _resolve_intent(self, ltp: float) -> Trade | None:
        """An entry whose outcome we do not know. Cancel what is resting; close anything that filled."""
        intent = self.store.get("pending_entry")
        if not intent:
            return None
        ltp = ltp if ltp > 0 else float(intent["ltp"])
        found = self._by_tag(intent["tag"])
        if found is None:
            self.alert("intent", "cannot read the order book to resolve an entry; check Upstox")
            return None
        if any(o.status not in FINAL for o in found):
            for o in found:
                if o.status not in FINAL:
                    self._cancel(o.order_id)
            found = self._by_tag(intent["tag"]) or found
        try:
            net = self._net(intent["instrument_key"])
        except UpstoxError as e:
            self.alert("intent", f"cannot read positions to resolve an entry: {e}")
            return None
        filled = sum(o.filled_quantity for o in found)
        direction = 1 if intent["side"] == "LONG" else -1
        if net == 0:
            if any(o.status not in FINAL for o in found):
                return None
            if filled and self._age(intent["at"]) < LAG_GRACE:
                return None  # filled, but positions have not caught up yet
            if filled:
                self.alert("intent-flat", "an unconfirmed entry filled but nothing is held now; check Upstox")
            self.store.set("pending_entry", None)
            return None
        if net * direction < 0:
            self.store.set("pending_entry", None)
            self._orphan = True
            return None
        size = abs(net)
        unit = intent["multiplier"] if size % intent["multiplier"] == 0 and size >= intent["multiplier"] else 1
        lots = max(1, size // unit)
        fill = next((o.average_price for o in found if o.average_price), intent["ltp"])
        p = replace(self._position_from(intent, lots, fill), broker_unit=unit, flatten_reason="forced: unconfirmed entry")
        self._save(p)
        self.store.set("pending_entry", None)
        self.store.set("check_strays", True)
        return self.exit(ltp, "forced: unconfirmed entry", self.clock.now())

    # ------------------------------------------------------------------ trail
    def move_stop(self, new_stop: float) -> None:
        p = self.position()
        if not p or p.flatten_reason:
            raise BrokerError("no position whose stop can be moved")
        if (new_stop - p.stop_loss) * p.direction <= 0:
            raise BrokerError("a stop can only be tightened")
        if p.sl_order_id:
            info = self._cancel(p.sl_order_id)
            if info is None or info.status not in FINAL:
                raise BrokerError("could not cancel the old stop; it stays in place")
            if info.status == DONE:
                raise BrokerError("the stop already filled")
            p = replace(p, sl_order_id=None)
            self._save(p)
            if info.filled_quantity:
                self._halt("the old stop was partly filled while being moved")
                self.exit(p.stop_loss, "forced: partial stop fill", self.clock.now())
                raise BrokerError("old stop partly filled; flattening the rest")
        try:
            order_id = self._place_stop(p, new_stop)
        except BrokerError as new_error:
            if self._restore_stop(p):
                raise BrokerError(f"stop move failed, old stop restored: {new_error}") from new_error
            self._halt("no stop order could be kept in place")
            self.exit(p.stop_loss, "forced: stop placement failed", self.clock.now())
            raise BrokerError(f"could not keep a stop in place, flattening: {new_error}") from new_error
        self._save(replace(p, stop_loss=new_stop, sl_order_id=order_id))

    # ------------------------------------------------------------------ exit
    def exit(self, ltp: float, reason: str, now: datetime) -> Trade | None:
        """Close the position. Returns the trade once confirmed; None means "still working on it"."""
        p = self.position()
        if not p:
            return None
        reason = p.flatten_reason or reason

        # 1. an exit already in flight?
        if p.exit_tag and not p.exit_order_id:
            found = self._by_tag(p.exit_tag)
            if found is None:
                return None
            p = replace(p, exit_order_id=found[0].order_id if found else None, exit_tag=p.exit_tag if found else None)
            self._save(p)
        if p.exit_order_id:
            info = self._details(p.exit_order_id)
            if info is None:
                return None
            if info.status not in FINAL:
                if self._age(p.exit_order_at) < EXIT_RESEND_AFTER:
                    return None
                info = self._cancel(p.exit_order_id)
                if info is None or info.status not in FINAL:
                    return None
            if info.status == DONE:
                return self._finalize(p, info.average_price or ltp, reason, now)
            p = replace(p, exit_order_id=None, exit_tag=None)
            self._save(p)

        p = replace(p, flatten_reason=reason)
        self._save(p)

        # 2. the exchange stop: if it filled we are done; otherwise it must be gone before we sell
        if p.sl_order_id:
            info = self._details(p.sl_order_id)
            if info is None:
                self.alert("exit", "cannot check the stop order; it stays in place and the exit is retried")
                return None
            if info.status not in FINAL:
                info = self._cancel(p.sl_order_id)
                if info is None or info.status not in FINAL:
                    self.alert("exit", "could not cancel the stop; it still protects the position, will retry")
                    return None
            if info.status == DONE:
                return self._finalize(p, info.average_price or p.stop_loss, "stop", now)
            p = replace(p, sl_order_id=None)
            self._save(p)

        # 3. what does the broker actually hold?
        try:
            net = self._net(p.instrument_key)
        except UpstoxError as e:
            self.alert("exit", f"cannot read positions, putting the stop back: {e}")
            self._restore_stop(p)
            return None
        p = self._sync_unit(p, net)
        lots = self._open_lots(p, net)
        if lots == 0:
            if self._age(p.opened_at) < LAG_GRACE:
                self._restore_stop(p)  # positions may not show a fresh fill yet
                return None
            self._halt("the position was closed outside the app")
            return self._finalize(p, ltp, "external", now)

        # 4. market out
        sent: list[str] = []  # tags tried; the last one is the order that may be live
        try:
            order_id = self._send_market(
                p.instrument_key, _exit_side(p.side), lots, "x", ltp, EXIT_LIMIT_BUFFER, sent.append
            )
        except MCXApiBlocked as e:
            self._halt("Upstox rejects MCX orders via the API: close the position in the Upstox app NOW")
            self.alert("exit", str(e))
            self._restore_stop(p)
            return None
        except (OrderRejected, NotAuthenticated) as e:
            self.alert("exit", f"exit order rejected, retrying: {e}")
            self._restore_stop(p)
            return None
        except UpstoxError as e:
            self._halt("an exit order had an unknown outcome")
            found = self._by_tag(sent[-1])
            if found is None:
                self._save(replace(p, exit_tag=sent[-1], exit_order_at=now.isoformat(timespec="seconds")))
                return None
            if not found:
                self.alert("exit", f"exit order did not reach the exchange: {e}")
                self._restore_stop(p)
                return None
            order_id = found[0].order_id
        p = replace(p, exit_order_id=order_id, exit_tag=sent[-1], exit_order_at=now.isoformat(timespec="seconds"))
        self._save(p)

        info = self._wait(order_id, FINAL, FILL_TIMEOUT)
        if info and info.status == DONE:
            return self._finalize(p, info.average_price or ltp, reason, now)
        if info and info.status in DEAD:
            p = replace(p, exit_order_id=None, exit_tag=None)
            self._save(p)
            if info.status == "rejected" and not self._market_refused():
                self._refuse_market(info.message)  # the retry goes out as a marketable limit
            self.alert("exit", f"exit order {info.status}: {info.message}; retrying")
            self._restore_stop(p)
            return None
        self.alert("exit", "exit order not confirmed yet; will check again")
        return None

    def _finalize(self, p: Position, price: float, reason: str, now: datetime) -> Trade:
        trade = build_trade(p, price, reason, now, self.s.cost_per_lot_inr)
        self.store.save_trade(trade)  # same id as the position: recording twice cannot double count
        self._save(None)
        self.store.set("check_strays", True)  # swept on the next tick, once we are flat
        return trade

    # ------------------------------------------------------------------ reconcile (every monitor tick)
    def poll(
        self, ltp: float, now: datetime, hi: float | None = None, lo: float | None = None, open_: float | None = None
    ) -> Trade | None:
        if self.store.get("pending_entry"):
            return self._resolve_intent(ltp)
        try:
            positions = self.c.positions()
        except UpstoxError as e:
            self.alert("positions", f"cannot read broker positions: {e}")
            return None
        p = self.position()
        self._orphan = self._foreign(positions, p.instrument_key if p else None)
        if p is None:
            if self.store.get("check_strays"):
                self._cancel_strays(keep=None)
            return None
        if p.flatten_reason or p.exit_order_id or p.exit_tag:
            return self.exit(ltp, p.flatten_reason or "forced: pending exit", now)

        info = self._details(p.sl_order_id) if p.sl_order_id else None
        if info and info.status == DONE:
            return self._finalize(p, info.average_price or p.stop_loss, "stop", now)

        net = self._net(p.instrument_key, positions)
        p = self._sync_unit(p, net)
        if net == 0:
            if self._age(p.opened_at) < LAG_GRACE:
                return None
            self.clock.sleep(1.0)
            try:
                if self._net(p.instrument_key) != 0:
                    return None
            except UpstoxError:
                return None
            if p.sl_order_id:
                cancelled = self._cancel(p.sl_order_id)  # a resting stop would now open a position
                if cancelled and cancelled.status == DONE:
                    return self._finalize(p, cancelled.average_price or p.stop_loss, "stop", now)
                if not cancelled or cancelled.status not in FINAL:
                    self.store.set("check_strays", True)
            self._halt("the position was closed outside the app")
            return self._finalize(p, ltp, "external", now)
        if net * p.direction < 0:
            self._halt("the broker position is on the opposite side of ours")
            if p.sl_order_id:
                self._cancel(p.sl_order_id)
            self._orphan = True
            return self._finalize(p, ltp, "external", now)
        if abs(net) > p.lots * p.broker_unit:
            self._orphan = True  # someone added to it; we manage only our own lots
        elif abs(net) < p.lots * p.broker_unit:
            self._halt("part of the position was closed outside the app")
            return self.exit(ltp, "forced: size mismatch", now)

        if p.sl_order_id is None or (info and info.status in DEAD):
            if p.sl_order_id:
                self.alert("stop-lost", "the exchange stop was cancelled or rejected; replacing it")
                p = replace(p, sl_order_id=None)
                self._save(p)
            if not self._restore_stop(p):
                self._halt("no stop order could be kept in place")
                return self.exit(ltp, "forced: stop placement failed", now)
            return None
        crossed = (ltp - p.stop_loss) * p.direction <= 0
        if info and info.status == "open" and crossed:
            return self.exit(ltp, "stop", now)  # stop-limit triggered but unfilled: price ran past it
        if p.target is not None and (ltp - p.target) * p.direction >= 0:
            return self.exit(ltp, "target", now)
        return None

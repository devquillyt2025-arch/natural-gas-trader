"""LiveBroker against a fake Upstox. The fake keeps an order book and a net position like the exchange."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from engine.brokers.base import BrokerError, EntryRequest
from engine.brokers.live import LiveBroker
from engine.clock import ManualClock
from engine.config import Settings
from engine.models import Position, Trade
from engine.store import LocalStore, Store
from engine.upstox import (
    BrokerPosition,
    MCXApiBlocked,
    OrderAmbiguous,
    OrderInfo,
    OrderRejected,
    UpstoxError,
)

from .conftest import TUE

KEY = "MCX_FO|999"
SYMBOL = "NATGASMINI25DEC26FUT"


class FakeUpstox:
    def __init__(self, unit: int = 1):
        self.unit = unit  # what positions report per lot
        self.orders: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.net = 0
        self.fill_price = 300.0
        self.behaviour: dict[str, list[str]] = {}  # order_type -> queue of behaviours
        self.fail_cancel = False
        self.fail_positions = False
        self.fail_details = False
        self.fail_book = False
        self.frozen_positions: int | None = None  # simulate a lagging positions endpoint
        self.fill_multiplier = 1
        self.others: list[BrokerPosition] = []

    # --- exchange side -----------------------------------------------------------------
    def _fill(self, o: dict[str, Any], price: float) -> None:
        o["status"], o["avg"], o["filled"] = "complete", price, o["lots"]
        self.net += o["lots"] * self.unit * self.fill_multiplier * (1 if o["side"] == "BUY" else -1)

    def trigger_stop(self, price: float | None = None) -> None:
        stop = next(o for o in reversed(self.orders) if o["type"] in ("SL-M", "SL") and o["status"] == "trigger pending")
        self._fill(stop, price if price is not None else stop["trigger"])

    def stops(self) -> list[dict[str, Any]]:
        return [o for o in self.orders if o["type"] in ("SL-M", "SL")]

    def resting_stops(self) -> list[dict[str, Any]]:
        return [o for o in self.stops() if o["status"] in ("trigger pending", "open")]

    def markets(self) -> list[dict[str, Any]]:
        return [o for o in self.orders if o["type"] == "MARKET"]

    def limits(self) -> list[dict[str, Any]]:
        return [o for o in self.orders if o["type"] == "LIMIT"]

    # --- OrderClient -------------------------------------------------------------------
    def place_order(self, instrument_key: str, side: str, lots: int, order_type: str, tag: str,
                    price: float = 0.0, trigger_price: float = 0.0) -> str:
        queue = self.behaviour.get(order_type) or []
        how = queue.pop(0) if queue else "ok"
        if how == "reject":
            raise OrderRejected(f"{order_type} not allowed")
        if how == "blocked":
            raise MCXApiBlocked("UDAPI1161 MCX orders via API are temporarily disabled")
        if how == "lost":
            raise OrderAmbiguous("timeout")
        o = {"id": f"O{len(self.orders) + 1}", "side": side, "lots": lots, "type": order_type, "tag": tag,
             "price": price, "trigger": trigger_price, "status": "open", "avg": 0.0, "filled": 0, "key": instrument_key}
        self.orders.append(o)
        if how == "async_reject":
            o["status"] = "rejected"
        elif how == "pending":
            o["status"] = "open pending"
        elif order_type in ("MARKET", "LIMIT"):  # a marketable limit fills like a market order
            if how != "resting":
                self._fill(o, self.fill_price)
        else:
            o["status"] = "trigger pending"
        if how == "placed_but_timeout":
            raise OrderAmbiguous("timeout")
        return str(o["id"])

    def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)
        o = self._get(order_id)
        if self.fail_cancel:
            raise OrderAmbiguous("cancel timed out")
        if o["status"] == "complete":
            raise OrderRejected("order already complete")
        o["status"] = "cancelled"

    def _get(self, order_id: str) -> dict[str, Any]:
        return next(o for o in self.orders if o["id"] == order_id)

    def _info(self, o: dict[str, Any]) -> OrderInfo:
        return OrderInfo(o["id"], o["status"], o["avg"], o["filled"], o["lots"], o["tag"], "")

    def order_details(self, order_id: str) -> OrderInfo:
        if self.fail_details:
            raise UpstoxError("details down")
        return self._info(self._get(order_id))

    def order_book(self) -> list[OrderInfo]:
        if self.fail_book:
            raise UpstoxError("book down")
        return [self._info(o) for o in self.orders]

    def positions(self) -> list[BrokerPosition]:
        if self.fail_positions:
            raise UpstoxError("positions down")
        net = self.net if self.frozen_positions is None else self.frozen_positions
        return [BrokerPosition(KEY, SYMBOL, "MCX", net), *self.others]


class Rig:
    def __init__(self, fake: FakeUpstox | None = None, store: Store | None = None):
        self.fake = fake or FakeUpstox()
        self.clock = ManualClock(TUE)
        self.store = store or Store(LocalStore(), None, "live", self.clock)
        self.alerts: list[str] = []
        self.broker = LiveBroker(Settings(), self.store, self.fake, self.clock, lambda k, m: self.alerts.append(m))

    def enter(self, side: str = "LONG", lots: int = 1) -> Position:
        long = side == "LONG"
        return self.broker.enter(EntryRequest(side, lots, 250, 300.0, 297.5 if long else 302.5,
                                              305.0 if long else 295.0, KEY, 7, "why", self.clock.now()))

    def later(self, seconds: float = 60) -> None:
        self.clock.advance(seconds)

    def poll(self, ltp: float = 300.0) -> Trade | None:
        return self.broker.poll(ltp, self.clock.now())

    def exit(self, ltp: float = 301.0, reason: str = "agent") -> Trade | None:
        return self.broker.exit(ltp, reason, self.clock.now())

    @property
    def halted(self) -> str | None:
        value = self.store.get("safety_halt")
        return str(value) if value else None


# --------------------------------------------------------------------------- stop always attached
@pytest.mark.parametrize("side,exit_side,trigger", [("LONG", "SELL", 297.5), ("SHORT", "BUY", 302.5)])
def test_entry_always_gets_an_exchange_stop_for_the_same_lots(side: str, exit_side: str, trigger: float) -> None:
    r = Rig()
    p = r.enter(side)
    assert [o["type"] for o in r.fake.orders] == ["MARKET", "SL-M"]
    assert r.fake.orders[0]["lots"] == 1, "MCX quantity is LOTS, never mmBtu"
    stop = r.fake.orders[1]
    assert stop["side"] == exit_side and stop["trigger"] == trigger and stop["lots"] == 1
    assert p.sl_order_id == stop["id"] and r.broker.position().sl_order_id == stop["id"]  # type: ignore[union-attr]
    assert p.entry == 300.0 and p.qty == 250 and r.store.get("pending_entry") is None and not r.halted


@pytest.mark.parametrize("how", ["reject", "async_reject"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_slm_refused_falls_back_to_sl_limit_with_buffer(how: str, side: str) -> None:
    r = Rig()
    r.fake.behaviour = {"SL-M": [how]}
    r.enter(side)
    sl = r.fake.resting_stops()[0]
    assert sl["type"] == "SL"
    if side == "LONG":
        assert sl["trigger"] == 297.5 and sl["price"] < 297.5
    else:
        assert sl["trigger"] == 302.5 and sl["price"] > 302.5
    assert r.broker.position() is not None


@pytest.mark.parametrize("how", ["reject", "async_reject"])
def test_no_stop_possible_means_flatten_halt_and_a_recorded_trade(how: str) -> None:
    r = Rig()
    r.fake.behaviour = {"SL-M": [how], "SL": [how]}
    with pytest.raises(BrokerError, match="stop order failed"):
        r.enter()
    assert r.fake.net == 0 and r.broker.position() is None
    assert [o["side"] for o in r.fake.markets()] == ["BUY", "SELL"]
    assert r.halted
    trade = r.store.all_trades()[0]
    assert trade.exit_reason == "forced: stop placement failed" and trade.pnl < 0


def test_ambiguous_stop_that_did_land_is_adopted_not_duplicated() -> None:
    r = Rig()
    r.fake.behaviour = {"SL-M": ["placed_but_timeout"]}
    p = r.enter()
    assert len(r.fake.stops()) == 1 and p.sl_order_id == r.fake.stops()[0]["id"]


def test_ambiguous_stop_that_cannot_be_found_flattens_and_never_places_a_second() -> None:
    r = Rig()
    r.fake.behaviour = {"SL-M": ["lost"]}
    with pytest.raises(BrokerError):
        r.enter()
    assert r.fake.stops() == [] and r.fake.net == 0 and r.broker.position() is None and r.halted


def test_stop_stuck_unconfirmed_is_cancelled_and_position_flattened() -> None:
    r = Rig()
    r.fake.behaviour = {"SL-M": ["pending"]}
    with pytest.raises(BrokerError):
        r.enter()
    assert r.fake.net == 0 and r.fake.stops()[0]["status"] == "cancelled" and r.broker.position() is None


# --------------------------------------------------------------------------- entry outcomes
def test_rejected_entry_leaves_nothing_behind() -> None:
    r = Rig()
    r.fake.behaviour = {"MARKET": ["reject"], "LIMIT": ["reject"]}
    with pytest.raises(BrokerError, match="entry rejected"):
        r.enter()
    assert r.broker.position() is None and r.store.get("pending_entry") is None and r.fake.stops() == []


def test_mcx_api_block_halts_instead_of_hammering() -> None:
    r = Rig()
    r.fake.behaviour = {"MARKET": ["blocked"]}
    with pytest.raises(BrokerError, match="UDAPI1161"):
        r.enter()
    assert "UDAPI1161" in str(r.halted) and r.broker.position() is None


def test_ambiguous_entry_that_filled_is_flattened() -> None:
    r = Rig()
    r.fake.behaviour = {"MARKET": ["placed_but_timeout"]}
    with pytest.raises(BrokerError, match="unknown"):
        r.enter()
    assert r.fake.net == 0 and r.broker.position() is None and r.halted
    assert r.store.all_trades()[0].exit_reason == "forced: unconfirmed entry"


def test_ambiguous_entry_that_never_arrived_changes_nothing() -> None:
    r = Rig()
    r.fake.behaviour = {"MARKET": ["lost"]}
    with pytest.raises(BrokerError):
        r.enter()
    assert r.fake.orders == [] and r.store.get("pending_entry") is None and r.halted


def test_unfilled_entry_is_cancelled() -> None:
    r = Rig()
    r.fake.behaviour = {"MARKET": ["resting"]}
    with pytest.raises(BrokerError, match="not filled"):
        r.enter()
    assert r.fake.orders[0]["status"] == "cancelled" and r.broker.position() is None and r.fake.net == 0


def test_crash_between_fill_and_stop_is_recovered_by_flattening() -> None:
    r = Rig()
    # the order filled but the process died before anything else was recorded
    r.fake.place_order(KEY, "BUY", 1, "MARKET", "ng-e-abc")
    r.store.set("pending_entry", {
        "id": "t1", "side": "LONG", "lots": 1, "multiplier": 250, "instrument_key": KEY, "stop": 297.5,
        "target": 305.0, "tag": "ng-e-abc", "decision_id": 7, "reasoning": "x", "ltp": 300.0, "at": TUE.isoformat(),
    })
    r.later()
    trade = r.poll()
    assert trade and trade.exit_reason == "forced: unconfirmed entry"
    assert r.fake.net == 0 and r.store.get("pending_entry") is None


def test_restart_recovers_the_position_and_its_stop() -> None:
    first = Rig()
    first.enter()
    second = Rig(first.fake, first.store)
    second.broker.startup(TUE)
    assert second.broker.position() is not None and not second.broker.orphan()
    assert len(second.fake.resting_stops()) == 1


def test_no_entry_while_the_broker_holds_an_unknown_position() -> None:
    r = Rig()
    r.fake.net = 1
    with pytest.raises(BrokerError, match="already holds"):
        r.enter()
    assert r.fake.orders == [] and r.broker.orphan()


# --------------------------------------------------------------------------- MARKET orders refused
@pytest.mark.parametrize("side,order_side,price", [("LONG", "BUY", 300.9), ("SHORT", "SELL", 299.1)])
def test_refused_market_entry_becomes_a_capped_limit_still_protected(side: str, order_side: str, price: float) -> None:
    r = Rig()
    r.fake.behaviour = {"MARKET": ["reject"]}
    r.enter(side)
    limit = r.fake.limits()[0]
    assert limit["side"] == order_side and limit["price"] == price  # 0.3% through the price, no further
    assert len(r.fake.resting_stops()) == 1 and r.store.get("pending_entry") is None


def test_refused_market_exit_still_gets_out_and_the_choice_lasts_for_the_day() -> None:
    r = Rig()
    r.enter()
    r.fake.behaviour = {"MARKET": ["reject"]}
    trade = r.exit(300.0)
    assert trade and r.fake.net == 0
    assert r.fake.limits()[-1]["side"] == "SELL" and r.fake.limits()[-1]["price"] == 297.0  # 1% through
    r.enter()
    r.exit()
    assert len(r.fake.markets()) == 1, "only the first entry used MARKET; nothing tries it again today"
    r.later(86_400)
    r.enter()
    assert len(r.fake.markets()) == 2


def test_ambiguous_limit_fallback_entry_is_resolved_by_its_own_tag() -> None:
    r = Rig()
    r.fake.behaviour = {"MARKET": ["reject"], "LIMIT": ["placed_but_timeout"]}
    with pytest.raises(BrokerError, match="unknown"):
        r.enter()
    assert r.fake.net == 0 and r.broker.position() is None and r.halted


# --------------------------------------------------------------------------- size units
@pytest.mark.parametrize("unit", [1, 250])
def test_positions_may_report_lots_or_units(unit: int) -> None:
    r = Rig(FakeUpstox(unit=unit))
    r.enter()
    assert r.broker.position().broker_unit == unit  # type: ignore[union-attr]
    assert r.poll(301.0) is None and not r.broker.orphan()
    trade = r.exit()
    assert trade and r.fake.net == 0 and r.fake.markets()[-1]["lots"] == 1


def test_absurdly_large_fill_is_flattened_completely() -> None:
    r = Rig(FakeUpstox(unit=250))
    r.fake.fill_multiplier = 250  # the order was somehow read as 250 lots
    with pytest.raises(BrokerError, match="does not match"):
        r.enter()
    assert r.fake.markets()[-1]["lots"] == 250 and r.fake.markets()[-1]["side"] == "SELL"
    assert r.halted and r.broker.position() is None and r.fake.resting_stops() == []


# --------------------------------------------------------------------------- exits
def test_exit_cancels_the_stop_then_sells() -> None:
    r = Rig()
    r.enter()
    r.fake.fill_price = 301.0
    trade = r.exit()
    assert r.fake.orders[1]["status"] == "cancelled" and r.fake.markets()[-1]["side"] == "SELL"
    assert trade and trade.exit == 301.0 and trade.pnl == pytest.approx(250 - 60) and trade.mode == "live"
    assert r.broker.position() is None and r.fake.net == 0 and len(r.store.all_trades()) == 1


def test_exit_after_the_stop_already_filled_sends_no_order() -> None:
    r = Rig()
    r.enter()
    r.fake.trigger_stop()
    before = len(r.fake.orders)
    trade = r.exit(296.0)
    assert len(r.fake.orders) == before, "an exit now would open the opposite side"
    assert trade and trade.exit == 297.5 and trade.exit_reason == "stop" and trade.pnl < 0
    assert r.fake.net == 0 and r.broker.position() is None


def test_stop_filling_while_we_cancel_it_sends_no_order() -> None:
    r = Rig()
    r.enter()
    real_cancel = r.fake.cancel_order

    def fills_first(order_id: str) -> None:
        r.fake.trigger_stop()
        real_cancel(order_id)

    r.fake.cancel_order = fills_first  # type: ignore[method-assign]
    trade = r.exit()
    assert trade and trade.exit_reason == "stop" and len(r.fake.markets()) == 1 and r.fake.net == 0


def test_cannot_cancel_stop_means_no_exit_order_and_stop_stays() -> None:
    r = Rig()
    r.enter()
    r.fake.fail_cancel = True
    assert r.exit() is None
    assert len(r.fake.markets()) == 1 and len(r.fake.resting_stops()) == 1
    assert r.broker.position().flatten_reason == "agent"  # type: ignore[union-attr]
    r.fake.fail_cancel = False
    trade = r.poll()
    assert trade and trade.exit_reason == "agent" and r.fake.net == 0


@pytest.mark.parametrize("how", ["reject", "async_reject"])
def test_rejected_exit_puts_the_stop_back_and_retries(how: str) -> None:
    r = Rig()
    r.enter()
    r.fake.behaviour = {"MARKET": [how], "LIMIT": ["reject"] if how == "reject" else []}
    assert r.exit() is None
    assert r.fake.net == 1 and len(r.fake.resting_stops()) == 1
    assert r.poll() is not None and r.fake.net == 0


def test_ambiguous_exit_is_not_resent_blindly() -> None:
    r = Rig()
    r.enter()
    r.fake.behaviour = {"MARKET": ["placed_but_timeout"]}
    r.fake.fail_book = True
    assert r.exit() is None
    sells = len(r.fake.markets())
    assert r.poll() is None and len(r.fake.markets()) == sells, "order book down: wait, do not resend"
    r.fake.fail_book = False
    trade = r.poll()
    assert trade and len(r.fake.markets()) == sells and r.fake.net == 0


def test_unfilled_exit_is_cancelled_and_resent_after_a_wait() -> None:
    r = Rig()
    r.enter()
    r.fake.behaviour = {"MARKET": ["resting"]}
    assert r.exit() is None
    assert r.poll() is None and len(r.fake.markets()) == 2
    r.later(60)
    assert r.poll() is not None
    assert [o["status"] for o in r.fake.markets()] == ["complete", "cancelled", "complete"]
    assert r.fake.net == 0


def test_positions_down_during_exit_restores_the_stop() -> None:
    r = Rig()
    r.enter()
    r.fake.fail_positions = True
    assert r.exit() is None
    assert len(r.fake.resting_stops()) == 1 and len(r.fake.markets()) == 1


def test_lagging_positions_do_not_look_like_a_closed_position() -> None:
    r = Rig()
    r.enter()
    r.fake.frozen_positions = 0
    assert r.poll() is None and r.broker.position() is not None
    assert r.exit() is None and len(r.fake.markets()) == 1 and len(r.fake.resting_stops()) == 1


# --------------------------------------------------------------------------- reconciliation
def test_poll_detects_a_stop_fill() -> None:
    r = Rig()
    r.enter()
    r.fake.trigger_stop(297.3)
    trade = r.poll(297.0)
    assert trade and trade.exit_reason == "stop" and trade.exit == 297.3 and r.broker.position() is None
    assert trade.r_multiple == pytest.approx(((297.3 - 300) * 250 - 60) / (2.5 * 250), abs=0.01)


def test_target_hit_exits_by_market() -> None:
    r = Rig()
    r.enter()
    r.fake.fill_price = 305.1
    trade = r.poll(305.2)
    assert trade and trade.exit_reason == "target" and trade.exit == 305.1
    assert r.fake.resting_stops() == []


def test_triggered_but_unfilled_stop_limit_is_taken_out_at_market() -> None:
    r = Rig()
    r.fake.behaviour = {"SL-M": ["reject"]}
    r.enter()
    r.fake.stops()[-1]["status"] = "open"  # triggered, price ran past the limit
    r.fake.fill_price = 294.0
    trade = r.poll(294.0)
    assert trade and trade.exit_reason == "stop" and trade.exit == 294.0 and r.fake.net == 0


def test_stop_cancelled_at_the_exchange_is_replaced() -> None:
    r = Rig()
    r.enter()
    r.fake.stops()[0]["status"] = "cancelled"
    assert r.poll() is None
    assert len(r.fake.resting_stops()) == 1 and r.broker.position() is not None


def test_stop_lost_and_irreplaceable_flattens() -> None:
    r = Rig()
    r.enter()
    r.fake.stops()[0]["status"] = "cancelled"
    r.fake.behaviour = {"SL-M": ["reject"], "SL": ["reject"]}
    trade = r.poll()
    assert trade and trade.exit_reason == "forced: stop placement failed" and r.fake.net == 0 and r.halted


def test_position_closed_by_hand_cancels_the_resting_stop() -> None:
    r = Rig()
    r.enter()
    r.later()
    r.fake.net = 0  # user squared off in the Upstox app
    trade = r.poll(300.5)
    assert trade and trade.exit_reason == "external"
    assert r.fake.resting_stops() == [], "a left-over stop would open a new position"
    assert len(r.fake.markets()) == 1 and r.halted


def test_orphan_is_detected_and_cleared() -> None:
    r = Rig()
    r.fake.net = -1
    assert r.poll() is None and r.broker.orphan()
    r.fake.net = 0
    r.poll()
    assert not r.broker.orphan()
    r.fake.others = [BrokerPosition("MCX_FO|1", "NATURALGAS25JANFUT", "MCX", 1250)]
    r.poll()
    assert r.broker.orphan()
    r.fake.others = [BrokerPosition("MCX_FO|2", "CRUDEOIL25JANFUT", "MCX", 100)]
    r.poll()
    assert not r.broker.orphan(), "other commodities are none of our business"
    assert r.fake.orders == [], "orphans are reported, never traded"


def test_extra_quantity_on_our_contract_is_an_orphan_and_only_ours_is_closed() -> None:
    r = Rig()
    r.enter()
    r.later()
    r.fake.net = 3
    r.poll()
    assert r.broker.orphan()
    r.exit()
    assert r.fake.markets()[-1]["lots"] == 1 and r.fake.net == 2


def test_stray_stop_left_while_flat_is_cancelled_on_startup() -> None:
    r = Rig()
    r.fake.place_order(KEY, "SELL", 1, "SL-M", "ng-s-old", trigger_price=297.0)
    r.fake.place_order(KEY, "SELL", 1, "SL-M", "my-own-order", trigger_price=250.0)
    r.broker.startup(TUE)
    assert [o["status"] for o in r.fake.orders] == ["cancelled", "trigger pending"]


def test_crash_during_a_stop_move_never_leaves_two_stops() -> None:
    r = Rig()
    p = r.enter()
    # crash state: old stop cancelled, new one resting at the exchange, but its id was never saved
    r.fake.cancel_order(p.sl_order_id)  # type: ignore[arg-type]
    r.fake.place_order(KEY, "SELL", 1, "SL-M", "ng-s-unsaved", trigger_price=299.0)
    from engine.brokers.base import save_position

    save_position(r.store, "live", replace(p, sl_order_id=None))
    restarted = Rig(r.fake, r.store)
    restarted.broker.startup(TUE)
    restarted.poll()
    assert len(r.fake.resting_stops()) == 1
    assert r.fake.resting_stops()[0]["id"] == restarted.broker.position().sl_order_id  # type: ignore[union-attr]


def test_extra_stop_beside_the_recorded_one_is_removed_on_restart() -> None:
    r = Rig()
    p = r.enter()
    r.fake.place_order(KEY, "SELL", 1, "SL-M", "ng-s-extra", trigger_price=299.0)
    Rig(r.fake, r.store).broker.startup(TUE)
    assert [o["id"] for o in r.fake.resting_stops()] == [p.sl_order_id]


# --------------------------------------------------------------------------- moving the stop
def test_move_stop_replaces_the_order() -> None:
    r = Rig()
    r.enter()
    r.broker.move_stop(299.0)
    assert r.fake.orders[1]["status"] == "cancelled"
    assert [o["trigger"] for o in r.fake.resting_stops()] == [299.0]
    assert r.broker.position().stop_loss == 299.0  # type: ignore[union-attr]
    assert r.broker.position().initial_stop == 297.5  # type: ignore[union-attr]


def test_move_stop_never_widens() -> None:
    r = Rig()
    r.enter()
    with pytest.raises(BrokerError, match="tightened"):
        r.broker.move_stop(296.0)
    assert r.fake.cancelled == []


def test_move_stop_failure_restores_the_old_stop() -> None:
    r = Rig()
    r.enter()
    r.fake.behaviour = {"SL-M": ["reject"], "SL": ["reject"]}  # new stop fails; the restore succeeds
    with pytest.raises(BrokerError, match="old stop restored"):
        r.broker.move_stop(299.0)
    assert [o["trigger"] for o in r.fake.resting_stops()] == [297.5]
    assert r.broker.position().stop_loss == 297.5  # type: ignore[union-attr]
    assert r.fake.net == 1


def test_move_stop_when_neither_can_be_placed_flattens() -> None:
    r = Rig()
    r.enter()
    r.fake.behaviour = {"SL-M": ["reject", "reject"], "SL": ["reject", "reject"]}
    with pytest.raises(BrokerError, match="flattening"):
        r.broker.move_stop(299.0)
    assert r.fake.net == 0 and r.broker.position() is None and r.halted


def test_move_stop_with_uncancellable_old_stop_keeps_it() -> None:
    r = Rig()
    r.enter()
    r.fake.fail_cancel = True
    with pytest.raises(BrokerError, match="stays in place"):
        r.broker.move_stop(299.0)
    assert len(r.fake.stops()) == 1 and r.broker.position().stop_loss == 297.5  # type: ignore[union-attr]


def test_move_stop_after_fill_places_nothing() -> None:
    r = Rig()
    r.enter()
    r.fake.trigger_stop()
    with pytest.raises(BrokerError, match="already filled"):
        r.broker.move_stop(299.0)
    assert len(r.fake.stops()) == 1


def test_closing_twice_records_one_trade() -> None:
    r = Rig()
    p = r.enter()
    r.exit()
    from engine.brokers.base import save_position

    save_position(r.store, "live", replace(p, sl_order_id=None))  # pretend a crash lost the clear
    r.later()
    r.exit()
    assert len(r.store.all_trades()) == 1

"""LiveBroker safety logic against a fake Upstox client (no network)."""
from datetime import datetime

import pytest

from app.broker import LiveBroker
from app.config import IST, Settings
from app.models import Contract
from app.store import Store
from app.upstox import UpstoxError

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=IST)


class FakeClient:
    def __init__(self):
        self.orders: list[dict] = []
        self.cancelled: list[str] = []
        self.net = 0
        self.fill_px = 300.0
        self.fail_types: set[str] = set()  # order types that raise on placement
        self.sl_status = "open"

    def contract(self):
        return Contract("MCX_FO|123", "NATGASMINI FUT", 250, "2026-12-25")

    def place_order(self, side, qty, order_type="MARKET", trigger=0.0, price=0.0, tag=""):
        if order_type in self.fail_types:
            raise UpstoxError(f"{order_type} not allowed")
        oid = f"O{len(self.orders) + 1}"
        self.orders.append(dict(id=oid, side=side, qty=qty, type=order_type, trigger=trigger, price=price, tag=tag))
        if order_type == "MARKET":
            self.net += qty if side == "BUY" else -qty
            if tag in ("natgas-out", "natgas-panic"):
                self.net = 0
        return oid

    def cancel_order(self, oid):
        self.cancelled.append(oid)

    def order_details(self, oid):
        o = next(x for x in self.orders if x["id"] == oid)
        if o["type"] == "MARKET":
            return {"status": "complete", "average_price": self.fill_px}
        return {"status": self.sl_status, "average_price": o["trigger"] if self.sl_status == "complete" else 0}

    def net_qty(self):
        return self.net


def make(fake=None):
    s, store = Settings(), Store(":memory:")
    fake = fake or FakeClient()
    return LiveBroker(s, store, fake), fake, store


def enter(b):
    return b.enter("LONG", 1, 250, 300.0, 297.5, 305.0, "test", NOW)


def test_entry_always_gets_an_exchange_stop():
    b, f, _ = make()
    p = enter(b)
    assert [o["type"] for o in f.orders] == ["MARKET", "SL-M"]
    assert f.orders[1]["side"] == "SELL" and f.orders[1]["trigger"] == 297.5
    assert p.sl_order_id == "O2" and b.position().sl_order_id == "O2"


def test_slm_rejected_falls_back_to_sl_limit():
    f = FakeClient()
    f.fail_types = {"SL-M"}
    b, f, _ = make(f)
    enter(b)
    sl = f.orders[1]
    assert sl["type"] == "SL" and sl["trigger"] == 297.5 and sl["price"] < 297.5


def test_no_stop_possible_means_flatten_and_error():
    f = FakeClient()
    f.fail_types = {"SL-M", "SL"}
    b, f, _ = make(f)
    with pytest.raises(UpstoxError):
        enter(b)
    assert f.orders[-1]["tag"] == "natgas-panic" and f.orders[-1]["side"] == "SELL"
    assert b.position() is None and f.net == 0


def test_exit_cancels_stop_then_sells():
    b, f, store = make()
    enter(b)
    t = b.exit(301.0, "agent exit", NOW)
    assert "O2" in f.cancelled and f.orders[-1]["tag"] == "natgas-out"
    assert t["exit"] == 300.0 and t["pnl"] == pytest.approx(-60.0)  # flat fill, only costs
    assert b.position() is None and len(store.trades()) == 1


def test_exit_after_stop_already_fired_sends_no_order():
    b, f, store = make()
    enter(b)
    f.net = 0  # exchange stop filled
    f.sl_status = "complete"
    n_orders = len(f.orders)
    t = b.exit(296.0, "agent exit", NOW)
    assert len(f.orders) == n_orders, "must not send an exit that would open the opposite side"
    assert t["exit"] == 297.5 and t["pnl"] < 0 and b.position() is None


def test_poll_detects_stop_fill_at_broker():
    b, f, _ = make()
    enter(b)
    f.net = 0
    f.sl_status = "complete"
    t = b.poll(297.0, NOW)
    assert t and t["exit_reason"].startswith("stop") and b.position() is None


def test_poll_flags_orphan_position():
    b, f, _ = make()
    f.net = -250  # someone/something opened a short we don't know about
    assert b.poll(300.0, NOW) is None
    assert b.orphan() is True
    f.net = 0
    b.poll(300.0, NOW)
    assert b.orphan() is False


def test_target_hit_exits_via_market():
    b, f, _ = make()
    enter(b)
    t = b.poll(305.2, NOW)
    assert t and t["exit_reason"] == "target" and f.orders[-1]["tag"] == "natgas-out"


def test_move_stop_replaces_order():
    b, f, _ = make()
    enter(b)
    b.move_stop(299.0)
    assert "O2" in f.cancelled
    assert f.orders[-1]["trigger"] == 299.0 and b.position().stop_loss == 299.0


def test_move_stop_failure_restores_old_stop():
    b, f, _ = make()
    enter(b)
    calls = {"n": 0}
    orig = f.place_order

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 2:  # both SL-M and the SL-limit fallback fail for the new stop
            raise UpstoxError("exchange hiccup")
        return orig(*a, **k)

    f.place_order = flaky
    with pytest.raises(UpstoxError):
        b.move_stop(299.0)
    assert f.orders[-1]["trigger"] == 297.5, "old stop must be restored"
    assert b.position() is not None and b.position().stop_loss == 297.5

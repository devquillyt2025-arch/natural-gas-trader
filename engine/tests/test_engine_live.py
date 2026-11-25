"""The whole engine driving the LiveBroker against the fake exchange."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from engine import commands
from engine.brokers.live import LiveBroker
from engine.config import Settings

from .conftest import TUE, Harness, go_long
from .test_live_broker import FakeUpstox


def live_harness(fake: FakeUpstox | None = None) -> tuple[Harness, FakeUpstox]:
    h = Harness(Settings(mode="live", live_confirm="I_ACCEPT_THE_RISK", database_url="postgresql://x"))
    fake = fake or FakeUpstox()
    h.broker = LiveBroker(h.s, h.store, fake, h.clock, lambda k, m: h.engine.alert_once(f"broker:{k}", m))
    h.engine.broker = h.broker
    return h, fake


def test_approved_entry_places_market_then_exchange_stop() -> None:
    h, fake = live_harness()
    h.agent.queue.append(go_long(lots=9))
    record = h.engine.cycle()
    assert record and record.approved
    assert [(o["type"], o["lots"]) for o in fake.orders] == [("MARKET", 1), ("SL-M", 1)]
    h.engine.publish_position()
    state: Any = h.state("position")
    assert state["exchange_stop"] is True and state["mode"] == "live"


def test_failed_order_is_recorded_as_not_done_and_halts() -> None:
    h, fake = live_harness()
    fake.behaviour = {"MARKET": ["blocked"]}
    h.agent.queue.append(go_long())
    record = h.engine.cycle()
    assert record and not record.approved and record.verdict_code == "order_failed"
    assert h.engine.safety_halt and "UDAPI1161" in h.engine.safety_halt
    h.advance(minutes=1)
    calls = h.agent.calls
    assert h.engine.cycle() is None and h.agent.calls == calls, "halted: do not even ask the model"
    h.command("resume")
    assert h.engine.safety_halt is None


def test_kill_command_flattens_and_cancels_the_stop() -> None:
    h, fake = live_harness()
    h.agent.queue.append(go_long())
    h.engine.cycle()
    cmd = h.command("kill")
    assert cmd["status"] == "acked" and "position closed" in cmd["result"]
    assert fake.net == 0 and fake.resting_stops() == []


def test_force_flat_after_the_stop_filled_sends_nothing() -> None:
    h, fake = live_harness()
    h.agent.queue.append(go_long())
    h.engine.cycle()
    fake.trigger_stop()
    orders = len(fake.orders)
    h.at(23, 10)
    h.engine.monitor()
    assert len(fake.orders) == orders and h.broker.position() is None
    assert h.store.all_trades()[0].exit_reason == "stop"


def test_force_flat_keeps_trying_but_never_doubles_up() -> None:
    h, fake = live_harness()
    h.agent.queue.append(go_long())
    h.engine.cycle()
    fake.behaviour = {"MARKET": ["resting"]}
    h.at(23, 10)
    h.engine.monitor()
    h.engine.monitor()
    assert len([o for o in fake.markets() if o["side"] == "SELL"]) == 1, "one exit in flight at a time"
    h.advance(seconds=40)
    h.engine.monitor()
    assert fake.net == 0 and h.broker.position() is None
    assert h.store.all_trades()[0].exit_reason == "forced: end of session"


def test_orphan_blocks_the_model_and_is_published() -> None:
    h, fake = live_harness()
    fake.net = -1
    h.engine.monitor()
    assert h.engine.cycle() is None and h.agent.calls == 0
    h.engine.publish_session(h.clock.now())
    session: Any = h.state("session")
    assert session["orphan"] is True and session["block_code"] == "orphan_position"
    assert fake.orders == []


def test_unresolved_entry_is_reconciled_even_before_the_first_quote() -> None:
    h, fake = live_harness()
    fake.place_order("MCX_FO|999", "BUY", 1, "MARKET", "ng-e-x")
    h.store.set("pending_entry", {
        "id": "t1", "side": "LONG", "lots": 1, "multiplier": 250, "instrument_key": "MCX_FO|999", "stop": 297.5,
        "target": 305.0, "tag": "ng-e-x", "decision_id": 1, "reasoning": "x", "ltp": 300.0,
        "at": (TUE - timedelta(minutes=5)).isoformat(),
    })
    h.feed.fail = True
    h.engine.monitor()
    assert fake.net == 0 and h.store.get("pending_entry") is None
    assert commands.process(h.engine) == 0

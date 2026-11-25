"""Engine loop behaviour: what gets called, what gets logged, and every code-level exit."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from engine.agent_base import AgentResult
from engine.config import IST, Settings
from engine.models import Decision

from .conftest import Harness, HeldExecutor, go_long


def last_decision(h: Harness) -> dict[str, Any]:
    return h.store.recent_decisions(1)[0]


def test_approved_entry_opens_position_and_stores_exact_snapshot(h: Harness) -> None:
    h.open_long()
    p = h.broker.position()
    assert p and p.side == "LONG" and p.entry == 300.1 and p.stop_loss == 298.0 and p.qty == 250
    row = h.flushed("decisions")[0]
    assert row["approved"] and row["verdict_code"] == "approved"
    assert row["snapshot"] == json.loads(json.dumps(h.agent.snapshots[0]))
    assert row["snapshot"]["news_brief"] == "storage draw expected"
    assert row["snapshot"]["intraday_15m"]["atr14"] == 1.0
    assert row["id"] == p.decision_id
    assert h.state("position")["stop_loss"] == 298.0
    assert h.state("news_brief")["text"] == "storage draw expected"


def test_a_decision_served_by_a_fallback_model_is_logged(h: Harness) -> None:
    h.agent.queue.append(AgentResult(Decision("HOLD"), meta={"model": "claude-sonnet-5"}))
    h.agent.queue.append(AgentResult(Decision("HOLD"), meta={"model": h.s.model}))
    h.engine.cycle()
    h.engine.cycle()
    fallbacks = [e["msg"] for e in h.flushed("events") if "fallback" in e["msg"]]
    assert fallbacks == [f"this decision came from claude-sonnet-5, not {h.s.model} (server-side fallback)"]


def test_snapshot_is_compact(h: Harness) -> None:
    h.engine.cycle()
    assert len(json.dumps(h.agent.snapshots[0], separators=(",", ":"))) < 16_000  # about 4k tokens


def test_rejected_decision_is_logged_and_nothing_happens(h: Harness) -> None:
    h.agent.queue.append(go_long(confidence=0.2))
    record = h.engine.cycle()
    assert record and not record.approved and record.verdict_code == "low_confidence"
    assert h.broker.position() is None


@pytest.mark.parametrize("hh,mm", [(9, 5), (22, 45)])
def test_model_not_called_when_flat_and_outside_window(h: Harness, hh: int, mm: int) -> None:
    h.at(hh, mm)
    assert h.engine.cycle() is None
    assert h.agent.calls == 0


def test_model_not_called_when_flat_and_data_stale(h: Harness) -> None:
    h.feed.stale = True
    assert h.engine.cycle() is None and h.agent.calls == 0


def test_model_not_called_when_killed(h: Harness) -> None:
    h.command("kill")
    assert h.engine.cycle() is None and h.agent.calls == 0


def test_model_not_called_when_control_channel_is_down(h: Harness) -> None:
    h.bus.failing = True
    h.advance(seconds=120)
    assert h.store.fetch_commands() == []
    assert h.engine.cycle() is None and h.agent.calls == 0
    h.bus.failing = False
    h.store.fetch_commands()
    assert h.engine.cycle() is not None and h.agent.calls == 1


def test_model_is_called_in_a_position_even_outside_the_entry_window(h: Harness) -> None:
    h.open_long()
    h.at(22, 45)
    h.engine.cycle()
    assert h.agent.calls == 2
    assert h.agent.snapshots[1]["position"]["side"] == "LONG"
    assert h.agent.snapshots[1]["session"]["entries_allowed_now"] is False


@pytest.mark.parametrize("failure", [AgentResult.hold("model returned no decision"), RuntimeError("boom")])
def test_any_agent_failure_is_a_hold(h: Harness, failure: AgentResult | Exception) -> None:
    h.agent.queue.append(failure)
    record = h.engine.cycle()
    assert record and record.action == "HOLD" and not record.approved and record.verdict_code == "agent_error"
    assert h.broker.position() is None
    assert h.state("engine_error")["source"] == "agent"
    h.engine.cycle()
    assert h.state("engine_error") is None


def test_price_is_revalidated_after_the_model_answers() -> None:
    executor = HeldExecutor()
    h = Harness(executor=executor)
    h.agent.queue.append(go_long())
    pending = h.engine.start_cycle()
    assert pending
    h.feed.price = 297.0  # fell through the proposed stop while the model was thinking
    executor.release()
    record = h.engine.finish_cycle(pending)
    assert not record.approved and record.verdict_code == "wrong_side"
    assert h.broker.position() is None


def test_answer_that_arrives_too_late_is_discarded() -> None:
    executor = HeldExecutor()
    h = Harness(executor=executor)
    h.agent.queue.append(go_long())
    pending = h.engine.start_cycle()
    assert pending
    h.advance(seconds=181)
    executor.release()
    assert h.engine.finish_cycle(pending).verdict_code == "stale_decision"
    assert h.broker.position() is None


def test_position_closed_while_thinking_cannot_become_a_flip() -> None:
    executor = HeldExecutor()
    h = Harness(executor=executor)
    h.agent.queue.append(go_long())
    first = h.engine.start_cycle()
    assert first
    executor.release()
    h.engine.finish_cycle(first)
    h.agent.queue.append(Decision("ENTER_SHORT", stop_loss=299.0, target=294.0, confidence=0.9))
    second = h.engine.start_cycle()
    assert second
    h.feed.price = 297.9
    h.engine.monitor()  # stop fills while the model is still thinking
    assert h.broker.position() is None
    executor.release()
    assert h.engine.finish_cycle(second).verdict_code == "position_changed"
    assert h.broker.position() is None


def test_agent_exit_and_trail(h: Harness) -> None:
    h.open_long()
    h.feed.price = 302.0
    h.agent.queue.append(Decision("TRAIL_STOP", stop_loss=300.5, confidence=0.7))
    assert h.engine.cycle().verdict_code == "trail"  # type: ignore[union-attr]
    assert h.broker.position().stop_loss == 300.5  # type: ignore[union-attr]
    h.agent.queue.append(Decision("TRAIL_STOP", stop_loss=299.0, confidence=0.7))
    assert h.engine.cycle().verdict_code == "trail_invalid"  # type: ignore[union-attr]
    assert h.broker.position().stop_loss == 300.5  # type: ignore[union-attr]
    h.agent.queue.append(Decision("EXIT", confidence=0.7, reasoning="momentum gone"))
    h.engine.cycle()
    trade = h.store.all_trades()[0]
    assert h.broker.position() is None and trade.exit_reason == "agent" and trade.exit == 301.9


def test_stop_hit_by_monitor_records_r_multiple(h: Harness) -> None:
    h.open_long()
    h.feed.price = 297.9
    h.engine.monitor()
    trade = h.flushed("trades")[0]
    assert trade["exit_reason"] == "stop" and trade["pnl"] < 0
    assert trade["r_multiple"] == pytest.approx(trade["pnl"] / ((300.1 - 298.0) * 250), abs=0.01)
    assert h.state("position") is None


def test_monitor_uses_one_minute_range_in_paper_mode(h: Harness) -> None:
    h.open_long()
    h.feed.span = (300.6, 297.5)  # (hi, lo): the stop was touched between two polls
    h.engine.monitor()
    assert h.store.all_trades()[0].exit_reason == "stop"


def test_one_minute_ranges_from_before_a_trailed_stop_do_not_trigger_it(h: Harness) -> None:
    asked: list[datetime] = []

    def bar_range(since: datetime, now: datetime) -> tuple[float, float] | None:
        asked.append(since)
        return None

    h.feed.bar_range = bar_range  # type: ignore[method-assign]
    h.open_long()
    h.advance(minutes=10)
    h.engine.monitor()
    h.feed.price = 302.0
    h.advance(minutes=5)
    h.agent.queue.append(Decision("TRAIL_STOP", stop_loss=300.5, confidence=0.7))
    h.engine.cycle()
    trailed_at = h.clock.now()
    for _ in range(3):
        h.advance(seconds=20)
        h.engine.monitor()
    assert all(since >= trailed_at for since in asked[-3:])


def test_force_flat_at_2310_without_the_model(h: Harness) -> None:
    h.open_long()
    calls = h.agent.calls
    h.at(23, 9, 59)
    h.engine.monitor()
    assert h.broker.position() is not None
    h.at(23, 10)
    h.engine.monitor()
    assert h.broker.position() is None and h.agent.calls == calls
    assert h.store.all_trades()[0].exit_reason == "forced: end of session"
    forced = last_decision(h)
    assert forced["verdict_code"] == "forced" and forced["action"] == "EXIT"


def test_force_flat_still_happens_when_quotes_are_down(h: Harness) -> None:
    h.open_long()
    h.feed.fail = True
    h.at(23, 10)
    h.engine.monitor()
    assert h.broker.position() is None


def test_leftover_overnight_position_is_flattened_next_morning(h: Harness) -> None:
    h.open_long()
    h.clock.set(datetime(2026, 11, 25, 9, 0, 5, tzinfo=IST))
    h.engine.monitor()
    assert h.store.all_trades()[0].exit_reason == "forced: overnight position"


def test_daily_cap_on_unrealized_loss_flattens_and_latches() -> None:
    s = Settings.from_env({"DAILY_LOSS_CAP_INR": "400", "MAX_RISK_INR": "600"})
    h = Harness(s)
    # budget is min(600, 400) so use a tight stop; the price then gaps well past it
    h.agent.queue.append(go_long(stop_loss=298.8, target=303.0))
    assert h.engine.cycle().approved  # type: ignore[union-attr]
    h.feed.price = 298.0  # unrealized -525 beyond the 400 cap
    h.engine.monitor()
    assert h.broker.position() is None
    assert h.engine.halt_latched(h.clock.now())
    assert h.state("limits")["usage"]["halted"] is True
    h.feed.price = 300.0
    h.advance(minutes=60)
    calls = h.agent.calls
    assert h.engine.cycle() is None and h.agent.calls == calls
    h.engine.publish_session(h.clock.now())
    assert h.state("session")["block_code"] == "daily_loss_cap"


def test_cooldown_after_a_loss_skips_the_model(h: Harness) -> None:
    h.open_long()
    h.feed.price = 297.9
    h.engine.monitor()
    h.feed.price = 300.0
    h.advance(minutes=15)
    assert h.engine.cycle() is None
    h.advance(minutes=16)
    assert h.engine.cycle() is not None


def test_state_survives_a_restart() -> None:
    first = Harness()
    first.open_long()
    first.command("update_limits", {"max_trades_per_day": 2})
    second = Harness(local=first.local, bus=first.bus)
    assert second.broker.position() is not None
    assert second.engine.limits.max_trades_per_day == 2


def test_tick_schedules_one_decision_per_candle_close() -> None:
    h = Harness(start=datetime(2026, 11, 24, 11, 59, 50, tzinfo=IST))
    h.engine.startup()
    for _ in range(60 * 31):  # 31 minutes of one-second ticks
        h.engine.tick()
        h.advance(seconds=1)
    times = [s["time_ist"][-5:] for s in h.agent.snapshots]
    assert times == ["11:59", "12:00", "12:15", "12:30"]  # first one is the catch-up at startup
    assert h.state("heartbeat")["mode"] == "paper"
    assert len(h.flushed("candles")) >= 600 + 30


def test_tick_does_nothing_but_heartbeat_when_login_is_missing() -> None:
    class NoAuth:
        def authenticated(self) -> bool:
            return False

        def status(self) -> dict[str, Any]:
            return {"authenticated": False}

        def exchange_code(self, code: str) -> None:
            raise AssertionError

    h = Harness()
    h.engine.auth = NoAuth()
    h.engine.startup()
    h.engine.tick()
    assert h.agent.calls == 0
    assert h.state("upstox_auth") == {"authenticated": False}
    assert h.state("session")["block_code"] == "upstox_login"
    assert h.state("heartbeat")

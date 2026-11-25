"""Replay invariants on synthetic data: whatever the agent does, the limits hold."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from engine import simulate
from engine.agent_base import RecklessAgent, StubAgent
from engine.config import Limits, Settings

LOT = 250


def assert_invariants(r: simulate.Result) -> None:
    s = r.engine.s
    lim = s.limits
    # worst case of one trade: the risk cap, plus slippage on both sides, plus costs
    worst_trade = lim.max_risk_inr + 2 * s.slippage_inr * LOT * lim.max_lots + s.cost_per_lot_inr * lim.max_lots
    summary = r.summary()
    assert summary["overnight_positions"] == 0
    assert r.engine.broker.position() is None
    assert summary["max_trades_in_a_day"] <= lim.max_trades_per_day
    assert summary["max_lots"] <= lim.max_lots
    for t in r.trades:
        assert t.opened_at[:10] == t.closed_at[:10], "held overnight"
        opened = datetime.fromisoformat(t.opened_at)
        assert lim.entry_start <= opened.time() <= lim.no_entry_after
        assert opened.weekday() < 5
        assert not r.engine.calendar.in_blackout(opened, lim.eia_before_min, lim.eia_after_min)
        assert t.pnl >= -worst_trade - 1
    assert summary["worst_day_inr"] >= -(lim.daily_loss_cap_inr + worst_trade)


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11])
def test_invariants_with_stub_agent(seed: int) -> None:
    r = simulate.run(seed=seed, agent=StubAgent())
    assert r.trades, "the replay should actually trade"
    assert_invariants(r)


@pytest.mark.parametrize("seed", [3, 5, 9])
def test_reckless_agent_is_contained(seed: int) -> None:
    r = simulate.run(seed=seed, agent=RecklessAgent(stop_atr=1.0))
    assert r.trades
    assert_invariants(r)
    rejected = r.summary()["rejected_by_reason"]
    assert rejected.get("trail_invalid"), "attempts to widen a stop must be rejected"
    assert rejected.get("already_in_position"), "attempts to pyramid must be rejected"


def test_absurdly_wide_stops_never_trade() -> None:
    r = simulate.run(seed=5, agent=RecklessAgent(stop_atr=10.0))
    assert r.trades == [] and r.summary()["rejected_by_reason"].get("stop_too_wide")


def test_daily_loss_cap_binds_and_still_holds() -> None:
    limits = replace(Limits(), daily_loss_cap_inr=900.0, max_risk_inr=900.0, max_trades_per_day=50,
                     cooldown_min_after_loss=0)
    r = simulate.run(seed=3, agent=RecklessAgent(stop_atr=2.5), settings=Settings(limits=limits, ceilings=limits))
    assert_invariants(r)
    assert any(t.exit_reason == "forced: daily loss cap" for t in r.trades) or r.summary()["worst_day_inr"] <= -600


def test_replay_is_deterministic() -> None:
    a = simulate.run(seed=4, replay_days=5).summary()
    b = simulate.run(seed=4, replay_days=5).summary()
    assert a == b

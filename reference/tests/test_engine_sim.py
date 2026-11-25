"""End-to-end invariants on a synthetic replay: whatever the agent does, the risk layer must hold."""
import pytest

from app import simulate
from app.agent import StubAgent
from app.models import Decision


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 11])
def test_invariants_hold_with_stub_agent(seed):
    r = simulate.run(seed=seed)
    s = r["settings"]
    assert not r["open_position_at_end"], "never hold overnight"
    # one lot sized to max_risk, plus slippage on both sides and costs
    tol = s.max_risk_inr + 2 * s.slippage_inr * 250 + s.cost_per_lot_inr + 1
    assert r["worst_trade_inr"] >= -tol
    # daily cap can be exceeded by at most one stopped-out trade after the check
    assert r["worst_day_inr"] >= -(s.daily_loss_cap_inr + tol)
    assert r["_by_day"] is not None
    per_day: dict[str, int] = {}
    for t in r["_trades"]:
        per_day[t["closed_at"][:10]] = per_day.get(t["closed_at"][:10], 0) + 1
    assert all(n <= s.max_trades_per_day for n in per_day.values())
    assert all(t["lots"] <= s.max_lots for t in r["_trades"])


class ReckelessAgent(StubAgent):
    """Tries to enter every candle with absurd size and no sense. The guard must contain it."""

    def decide(self, snap):
        ltp = snap["ltp"]
        if snap["position"]:
            return Decision(action="HOLD")
        return Decision("ENTER_LONG", 50, ltp - 10, ltp + 10, 0.99, "yolo")


def test_reckless_agent_is_contained():
    r = simulate.run(seed=5, agent=ReckelessAgent())
    assert r["trades"] == 0, "stop of 10 INR is way beyond max ATR multiple, all entries must be rejected"
    assert r["rejected"] > 0


class AlwaysLongTightStop(StubAgent):
    def decide(self, snap):
        ltp, atr = snap["ltp"], snap["intraday_15m"]["atr14"]
        if snap["position"] or not atr:
            return Decision(action="HOLD")
        return Decision("ENTER_LONG", 9, ltp - 1.0 * atr, ltp + 2.0 * atr, 0.9, "always long")


def test_high_frequency_agent_respects_limits():
    r = simulate.run(seed=9, agent=AlwaysLongTightStop())
    s = r["settings"]
    assert r["trades"] > 0
    assert all(t["lots"] == 1 for t in r["_trades"])
    assert not r["open_position_at_end"]
    assert r["worst_day_inr"] >= -(s.daily_loss_cap_inr + s.max_risk_inr + 200)

"""The agent interface, plus deterministic stand-ins for offline simulation and tests (no API calls)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from .models import Decision


@dataclass(frozen=True)
class AgentResult:
    decision: Decision
    error: str | None = None  # set => the decision is a fail-safe HOLD
    meta: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def hold(cls, error: str) -> AgentResult:
        return cls(Decision("HOLD", reasoning=error[:600]), error=error[:600])


class Agent(Protocol):
    def decide(self, snapshot: Mapping[str, Any]) -> AgentResult: ...
    def news(self, now: datetime) -> str: ...


def intraday(snap: Mapping[str, Any]) -> Mapping[str, Any]:
    """The snapshot's decision-timeframe block, whatever its size (intraday_5m or intraday_15m)."""
    block: Mapping[str, Any] = next(value for key, value in snap.items() if key.startswith("intraday_"))
    return block


class StubAgent:
    """EMA-trend pullback rule. A plumbing stand-in, not a strategy."""

    def news(self, now: datetime) -> str:
        return ""

    def decide(self, snapshot: Mapping[str, Any]) -> AgentResult:
        return AgentResult(self._rule(snapshot))

    def _rule(self, snap: Mapping[str, Any]) -> Decision:
        m, ltp = intraday(snap), snap["ltp"]
        atr, e9, e21, e50, rsi = m["atr14"], m["ema9"], m["ema21"], m["ema50"], m["rsi14"]
        if None in (atr, e9, e21, e50, rsi):
            return Decision("HOLD", reasoning="warming up")
        pos = snap["position"]
        if pos:
            against = (pos["side"] == "LONG" and e9 < e21) or (pos["side"] == "SHORT" and e9 > e21)
            if against:
                return Decision("EXIT", confidence=0.7, reasoning="[stub] EMA9 crossed back through EMA21")
            if (pos["unrealized_r"] or 0) >= 1.0:
                new = ltp - atr if pos["side"] == "LONG" else ltp + atr
                if (new - pos["stop_loss"]) * (1 if pos["side"] == "LONG" else -1) > 0:
                    return Decision("TRAIL_STOP", stop_loss=new, confidence=0.7, reasoning="[stub] trailing after 1R")
            return Decision("HOLD", confidence=0.5, reasoning="[stub] thesis intact")
        if e9 > e21 > e50 and 50 < rsi < 68 and abs(ltp - e9) < atr:
            return Decision("ENTER_LONG", 1, ltp - 1.5 * atr, ltp + 2.7 * atr, 0.7, f"[stub] uptrend pullback to EMA9 {e9}")
        if e9 < e21 < e50 and 32 < rsi < 50 and abs(ltp - e9) < atr:
            return Decision("ENTER_SHORT", 1, ltp + 1.5 * atr, ltp - 2.7 * atr, 0.7, f"[stub] downtrend pullback to EMA9 {e9}")
        return Decision("HOLD", confidence=0.4, reasoning="[stub] no setup")


class RecklessAgent(StubAgent):
    """Enters on every candle with absurd size, to prove the risk layer contains it.

    stop_atr controls how wide its stop is: 10 is far outside the limits, 1.0 is just acceptable.
    """

    def __init__(self, stop_atr: float = 1.0):
        self.stop_atr = stop_atr
        self.calls = 0

    def _rule(self, snap: Mapping[str, Any]) -> Decision:
        self.calls += 1
        ltp, atr = snap["ltp"], intraday(snap)["atr14"]
        if snap["position"]:
            # also try to widen the stop and to pyramid
            side = snap["position"]["side"]
            if self.calls % 2:
                wider = snap["position"]["stop_loss"] + (-5 if side == "LONG" else 5)
                return Decision("TRAIL_STOP", stop_loss=wider, confidence=1.0, reasoning="[reckless] widen")
            return Decision("ENTER_LONG", 50, ltp - atr, ltp + 3 * atr, 1.0, "[reckless] pyramid")
        sign = 1 if (self.calls // 3) % 2 else -1
        action = "ENTER_LONG" if sign == 1 else "ENTER_SHORT"
        return Decision(action, 50, ltp - sign * self.stop_atr * atr, ltp + sign * 3 * self.stop_atr * atr, 1.0, "[reckless] yolo")

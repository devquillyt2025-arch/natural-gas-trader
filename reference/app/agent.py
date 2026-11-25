"""The trader's brain: Claude reads a market snapshot and returns one structured decision."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from .config import Settings
from .models import Decision

log = logging.getLogger("agent")

SYSTEM = """You are the sole decision-maker of a small, intraday MCX Natural Gas futures account (INR per mmBtu).
Your job is to grow capital slowly and, above all, not blow it up.

How you work:
- You are called once per 15-minute candle close with a JSON snapshot: daily + 15m candles, EMAs, RSI, ATR, swing
  support/resistance, your open position (if any), today's P&L, hard risk limits, session state and a news brief.
- You answer by calling submit_decision exactly once. HOLD is the default. Most candles deserve HOLD.
- Trade only when there is a clear, nameable edge: trend continuation after a pullback, a clean break and hold of a
  level, or a rejection at a level with room to a target. In chop between levels, or mid-range, HOLD.
- Every entry needs stop_loss and target as absolute prices. The stop must sit beyond a real structural level (swing
  low/high), not an arbitrary distance. Reward:risk must meet the hard limit. Think in ATR of the 15m chart.
- Respect hard_limits. A separate risk layer will REJECT anything outside them, so don't waste calls proposing it.
  If entries_allowed_now is false, do not enter.
- Natural gas is event-driven: the EIA storage report, weather model shifts (HDD forecasts), LNG feedgas and freeze-offs
  move it violently. Use news_brief for context but never chase a spike; never hold through a scheduled report
  unless you are deliberately accepting the risk and the position is small and already protected.
- Managing an open position: EXIT if the thesis is invalidated before the stop. TRAIL_STOP to lock in profit once
  the trade is about 1R in profit (new stop must only tighten). Otherwise HOLD. Do not average down, add, or flip.
- Be honest about uncertainty. Set confidence in [0,1]: below the min_confidence limit means you will not be allowed to
  enter, so use that as the way to say "marginal". Keep reasoning to 2-4 sentences naming the level/trigger used."""

TOOL = {
    "name": "submit_decision",
    "description": "Submit exactly one trading decision for this candle.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["ENTER_LONG", "ENTER_SHORT", "HOLD", "EXIT", "TRAIL_STOP"]},
            "lots": {"type": "integer", "minimum": 1, "description": "Lots for a new entry (will be clipped by risk limits)."},
            "stop_loss": {"type": "number", "description": "Absolute price. Required for entries and TRAIL_STOP."},
            "target": {"type": "number", "description": "Absolute price. Required for entries."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string"},
        },
        "required": ["action", "confidence", "reasoning"],
    },
}

NEWS_PROMPT = (
    "Give a tight trader's brief (max 120 words) on US natural gas / Henry Hub right now: what is driving price this "
    "week, the latest weather-model (HDD) shifts, storage expectations vs the 5-year average, LNG feedgas, and any "
    "scheduled catalyst in the next 24 hours. Facts only, include dates, no advice."
)


class ClaudeAgent:
    def __init__(self, s: Settings):
        import anthropic  # imported lazily so tests/simulation do not need a key

        self.s = s
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key)
        self._news: str = ""
        self._news_at: Optional[datetime] = None

    # ---------------------------------------------------------------- news
    def news(self, now: datetime) -> str:
        if self._news_at and now - self._news_at < timedelta(minutes=self.s.news_ttl_min):
            return self._news
        try:
            resp = self.client.messages.create(
                model=self.s.model,
                max_tokens=700,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
                messages=[{"role": "user", "content": NEWS_PROMPT}],
            )
            self._news = " ".join(b.text for b in resp.content if b.type == "text").strip()[:1500]
        except Exception as e:  # news is optional; never block trading logic on it
            log.warning("news brief failed: %s", e)
            self._news = self._news or ""
        self._news_at = now
        return self._news

    # ------------------------------------------------------------ decision
    def decide(self, snapshot: dict) -> Decision:
        try:
            resp = self.client.messages.create(
                model=self.s.model,
                max_tokens=800,
                system=SYSTEM,
                tools=[TOOL],
                tool_choice={"type": "tool", "name": "submit_decision"},
                messages=[{"role": "user", "content": "Market snapshot:\n" + json.dumps(snapshot, separators=(",", ":"))}],
            )
            for b in resp.content:
                if b.type == "tool_use" and b.name == "submit_decision":
                    return _coerce(b.input)
            return Decision(action="HOLD", reasoning="model returned no decision")
        except Exception as e:
            log.error("decide failed: %s", e)
            return Decision(action="HOLD", reasoning=f"agent error: {e}"[:200])


def _coerce(x: dict) -> Decision:
    def num(v):
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    return Decision(
        action=str(x.get("action", "HOLD")).upper(),
        lots=int(x.get("lots") or 1),
        stop_loss=num(x.get("stop_loss")),
        target=num(x.get("target")),
        confidence=num(x.get("confidence")) or 0.0,
        reasoning=str(x.get("reasoning", ""))[:600],
    )


class StubAgent:
    """Deterministic EMA-trend stand-in used for offline simulation and tests (no API calls)."""

    def news(self, now: datetime) -> str:
        return ""

    def decide(self, snap: dict) -> Decision:
        m, ltp = snap["intraday_15m"], snap["ltp"]
        atr, e9, e21, e50, rsi = m["atr14"], m["ema9"], m["ema21"], m["ema50"], m["rsi14"]
        if None in (atr, e9, e21, e50, rsi):
            return Decision(action="HOLD", reasoning="warming up")
        pos = snap["position"]
        if pos:
            against = (pos["side"] == "LONG" and e9 < e21) or (pos["side"] == "SHORT" and e9 > e21)
            return Decision(action="EXIT", reasoning="trend flipped") if against else Decision(action="HOLD")
        if e9 > e21 > e50 and 50 < rsi < 68 and abs(ltp - e9) < atr:
            return Decision("ENTER_LONG", 1, ltp - 1.5 * atr, ltp + 2.7 * atr, 0.7, "uptrend pullback to EMA9")
        if e9 < e21 < e50 and 32 < rsi < 50 and abs(ltp - e9) < atr:
            return Decision("ENTER_SHORT", 1, ltp + 1.5 * atr, ltp - 2.7 * atr, 0.7, "downtrend pullback to EMA9")
        return Decision(action="HOLD", reasoning="no setup")

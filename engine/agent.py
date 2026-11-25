"""Claude is the sole decision-maker. It answers through one strict tool; anything else is a HOLD.

Current Claude models reject a forced `tool_choice`, so we use `auto` with a strict schema, an
explicit instruction, and no parallel calls. A reply without the tool call is treated as HOLD.
"""
from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import anthropic

from .agent_base import AgentResult
from .config import Settings
from .models import ACTIONS, Decision

log = logging.getLogger("agent")

_SYSTEM = """You are the sole trader of a small, intraday MCX Natural Gas futures account (prices in INR per mmBtu).
Capital preservation comes first. Growing the account slowly is the second goal.

You are called once per {minutes}-minute candle close with a JSON snapshot: daily and {tf} candles (oldest to newest), EMAs,
RSI, ATR, swing support and resistance, your open position if any, today's P&L, the hard limits, session state, your
recent decisions and a news brief. Answer by calling submit_decision exactly once. Never answer in plain text only.

How to decide:
- HOLD is the default. Most candles deserve HOLD.
- Enter only with a nameable edge: a pullback within a trend, a level that broke and held, or a rejection at a level
  with room to the target. In chop, mid-range, or when you cannot name the level, HOLD.
- Every entry needs stop_loss and target as absolute prices. Put the stop beyond real structure (a swing high or low,
  a defended level), not at an arbitrary distance, and keep reward:risk at or above the limit. Think in {tf} ATR.
- Respect hard_limits. A separate risk layer enforces them and rejects anything outside; it also sizes down. If
  entries_allowed_now is false, do not propose an entry.
- Natural gas is event-driven: the EIA storage report, weather model shifts (heating and cooling degree days), LNG
  feedgas, freeze-offs. Use news_brief as context. Never chase a spike.
- Managing an open position: EXIT if the thesis is invalidated before the stop is reached. TRAIL_STOP once the trade
  is about 1R in profit; a stop may only move in your favour and must stay a sensible distance from price. Otherwise
  HOLD. Never average down, add to a position, or flip it.
- confidence is your honest probability-like read in [0,1]. Below min_confidence means "marginal" and blocks entry.
- reasoning: 2 to 4 sentences that name the level or trigger you used."""

def system_prompt(minutes: int) -> str:
    return _SYSTEM.replace("{minutes}", str(minutes)).replace("{tf}", f"{minutes}m")


DECISION_TOOL: dict[str, Any] = {
    "name": "submit_decision",
    "description": "Submit exactly one trading decision for this candle. This is the only way to act.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "lots": {"type": "integer", "description": "Lots for a new entry, at least 1. Use 1 for other actions."},
            "stop_loss": {
                "anyOf": [{"type": "number"}, {"type": "null"}],
                "description": "Absolute price. Required for entries and TRAIL_STOP, otherwise null.",
            },
            "target": {
                "anyOf": [{"type": "number"}, {"type": "null"}],
                "description": "Absolute price. Required for entries, otherwise null.",
            },
            "confidence": {"type": "number", "description": "0 to 1."},
            "reasoning": {"type": "string", "description": "2-4 sentences naming the level used."},
        },
        "required": ["action", "lots", "stop_loss", "target", "confidence", "reasoning"],
        "additionalProperties": False,
    },
}

NEWS_PROMPT = (
    "Today is {today} (IST). Search the web and write a trader's brief on US natural gas (Henry Hub), at most 120 "
    "words: what is driving price this week, the latest weather-model heating/cooling degree day shifts, storage "
    "expectations against the 5-year average, LNG feedgas flows, and scheduled catalysts in the next 24 hours. "
    "Facts with dates only, no advice, no preamble."
)

# Models that decline some requests and support server-side fallback with the "default" setting.
FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5-2")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
NO_EFFORT_MODELS = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-3")
NEW_SEARCH_MODELS = (
    "claude-sonnet-4-6", "claude-sonnet-5", "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
    "claude-opus-5", "claude-fable", "claude-mythos",
)
MAX_TOKENS = 16_000  # includes adaptive thinking
MAX_SEARCH_CONTINUATIONS = 3


class BriefingAgent:
    """The hourly news brief and its caching. Subclasses say how to search."""

    def __init__(self, settings: Settings):
        self.s = settings
        self._brief = ""
        self._brief_at: datetime | None = None  # last successful brief
        self._tried_at: datetime | None = None

    def _search(self, now: datetime) -> str:
        raise NotImplementedError

    def news(self, now: datetime) -> str:
        """At most once per NEWS_TTL_MIN. Optional: a failure never blocks trading."""
        if not self.s.use_news:
            return ""
        ttl = timedelta(minutes=self.s.news_ttl_min)
        if self._tried_at and now - self._tried_at < ttl:
            return self._current(now)
        self._tried_at = now
        try:
            brief = self._search(now)
        except Exception as e:
            log.warning("news brief failed: %s: %s", type(e).__name__, str(e)[:200])
        else:
            if brief:
                self._brief, self._brief_at = brief, now
        return self._current(now)

    def _current(self, now: datetime) -> str:
        if self._brief_at and now - self._brief_at < timedelta(hours=3):
            return f"(as of {self._brief_at:%H:%M} IST) {self._brief}"
        return ""


class ClaudeAgent(BriefingAgent):
    """Claude through the Anthropic API, billed to an API key."""

    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        super().__init__(settings)
        client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.client = client.with_options(timeout=settings.llm_timeout_sec, max_retries=1)

    # ------------------------------------------------------------------ decision
    def _create(self, **request: Any) -> Any:
        if self.s.model.startswith(FALLBACK_MODELS):
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **request)
        return self.client.messages.create(**request)

    def decide(self, snapshot: Mapping[str, Any]) -> AgentResult:
        payload = json.dumps(snapshot, separators=(",", ":"), default=str)
        request: dict[str, Any] = {
            "model": self.s.model,
            "max_tokens": MAX_TOKENS,
            "system": system_prompt(self.s.candle_min),
            "tools": [DECISION_TOOL],
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
            "messages": [{
                "role": "user",
                "content": f"Market snapshot:\n{payload}\n\nDecide now by calling submit_decision.",
            }],
        }
        if not self.s.model.startswith(NO_EFFORT_MODELS):
            request["output_config"] = {"effort": self.s.effort}
        try:
            response = self._create(**request)
        except anthropic.AuthenticationError:
            return AgentResult.hold("Anthropic rejected the API key")
        except anthropic.RateLimitError:
            return AgentResult.hold("Anthropic rate limit")
        except anthropic.APITimeoutError:
            return AgentResult.hold("Claude call timed out")
        except anthropic.APIConnectionError:
            return AgentResult.hold("could not reach the Anthropic API")
        except anthropic.APIStatusError as e:
            return AgentResult.hold(f"Anthropic API error {e.status_code}: {e.message}"[:300])
        except Exception as e:  # any failure in the decision path means HOLD
            log.exception("decide failed")
            return AgentResult.hold(f"agent error: {type(e).__name__}: {e}"[:300])
        return parse_response(response)

    # ------------------------------------------------------------------ news
    def _search(self, now: datetime) -> str:
        new = self.s.model.startswith(NEW_SEARCH_MODELS)
        tool = {"type": "web_search_20260209" if new else "web_search_20250305", "name": "web_search", "max_uses": 3}
        messages: list[Any] = [{"role": "user", "content": NEWS_PROMPT.format(today=now.strftime("%A %d %B %Y %H:%M"))}]
        for _ in range(MAX_SEARCH_CONTINUATIONS + 1):
            response = self._create(model=self.s.model, max_tokens=MAX_TOKENS, tools=[tool], messages=messages)
            if response.stop_reason != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": response.content}]
        if response.stop_reason in ("refusal", "pause_turn"):
            return ""
        return final_text(response.content)


def final_text(content: list[Any]) -> str:
    """The answer is the text after the last search block (earlier text is narration)."""
    parts: list[str] = []
    for block in content:
        if block.type == "text":
            parts.append(block.text)
        elif block.type not in ("thinking", "redacted_thinking"):
            parts.clear()
    words = "".join(parts).split()
    return " ".join(words[:150])


def _number(value: Any, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"not a number: {value!r}")
    return float(value)


def parse_response(response: Any) -> AgentResult:
    meta = {"model": getattr(response, "model", None), "stop_reason": response.stop_reason}
    if response.stop_reason == "refusal":
        return AgentResult.hold("the model declined the request")
    if response.stop_reason == "max_tokens":
        return AgentResult.hold("reply was cut off at max_tokens")
    calls = [b for b in response.content if b.type == "tool_use" and b.name == "submit_decision"]
    if len(calls) != 1:
        return AgentResult.hold(f"expected one submit_decision call, got {len(calls)}")
    return parse_decision(calls[0].input, meta)


def parse_decision(x: Any, meta: Mapping[str, Any]) -> AgentResult:
    """Strict reading of the model's answer, however it was obtained. Anything odd is a HOLD."""
    try:
        action = x["action"]
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        lots = x["lots"]
        if isinstance(lots, bool) or not isinstance(lots, int):
            raise ValueError("lots must be an integer")
        confidence = _number(x["confidence"])
        assert confidence is not None
        if not isinstance(x["reasoning"], str):
            raise ValueError("reasoning must be text")
        decision = Decision(
            action=action, lots=lots, stop_loss=_number(x["stop_loss"], nullable=True),
            target=_number(x["target"], nullable=True), confidence=confidence, reasoning=x["reasoning"][:1200],
        )
    except (KeyError, TypeError, ValueError) as e:
        return AgentResult.hold(f"malformed decision: {e}"[:300])
    return AgentResult(decision, meta=meta)

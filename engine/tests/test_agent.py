"""The Claude client: request shape, strict parsing, and HOLD on every failure. No network."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any

import anthropic
import httpx2
import pytest

from engine.agent import ClaudeAgent, final_text
from engine.config import Settings

from .conftest import TUE

SNAPSHOT = {"ltp": 300.0, "position": None}
GOOD = {"action": "ENTER_LONG", "lots": 1, "stop_loss": 298, "target": 304.5, "confidence": 0.72, "reasoning": "why"}


def message(content: list[dict[str, Any]], stop_reason: str = "tool_use") -> dict[str, Any]:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def tool_use(data: dict[str, Any], name: str = "submit_decision") -> dict[str, Any]:
    return {"type": "tool_use", "id": "tu_1", "name": name, "input": data}


class Api:
    """A real Anthropic SDK client wired to a fake transport."""

    def __init__(self, *replies: dict[str, Any] | int | Exception, settings: Settings | None = None):
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx2.Headers] = []
        transport = httpx2.MockTransport(self.handle)
        client = anthropic.Anthropic(api_key="sk-test", http_client=anthropic.DefaultHttpxClient(transport=transport))
        self.agent = ClaudeAgent(settings or Settings(), client)
        self.agent.client = self.agent.client.with_options(max_retries=0)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, int):
            return httpx2.Response(reply, json={"type": "error", "error": {"type": "x", "message": "nope"}})
        return httpx2.Response(200, json=reply)


def test_request_shape_and_parsed_decision() -> None:
    api = Api(message([tool_use(GOOD)]))
    result = api.agent.decide(SNAPSHOT)
    assert result.error is None
    assert result.decision.action == "ENTER_LONG" and result.decision.stop_loss == 298.0
    body = api.requests[0]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    tool = body["tools"][0]
    assert tool["name"] == "submit_decision" and tool["strict"] is True
    assert tool["input_schema"]["additionalProperties"] is False
    assert body["output_config"] == {"effort": "medium"}
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in api.headers[0]["anthropic-beta"]
    assert "temperature" not in body and "thinking" not in body
    assert '"ltp":300.0' in body["messages"][0]["content"]


def test_older_model_ids_skip_unsupported_parameters() -> None:
    api = Api(message([tool_use(GOOD)]), settings=replace(Settings(), model="claude-haiku-4-5"))
    assert api.agent.decide(SNAPSHOT).error is None
    assert "output_config" not in api.requests[0] and "fallbacks" not in api.requests[0]


@pytest.mark.parametrize(
    "reply",
    [
        message([{"type": "text", "text": "I would hold."}], "end_turn"),
        message([tool_use(GOOD)], "max_tokens"),
        message([], "refusal"),
        message([tool_use(GOOD, name="other_tool")]),
        message([tool_use(GOOD), tool_use(GOOD)]),
        message([tool_use({**GOOD, "action": "YOLO"})]),
        message([tool_use({**GOOD, "lots": "2"})]),
        message([tool_use({**GOOD, "stop_loss": "298"})]),
        message([tool_use({**GOOD, "confidence": None})]),
        message([tool_use({"action": "ENTER_LONG"})]),
        400, 401, 429, 500, 529,
        httpx2.ReadTimeout("slow"),
        httpx2.ConnectError("down"),
    ],
)
def test_every_failure_becomes_hold(reply: Any) -> None:
    result = Api(reply).agent.decide(SNAPSHOT)
    assert result.decision.action == "HOLD" and result.error


def search_reply(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return message([
        {"type": "text", "text": "Let me search."},
        {"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {"query": "natural gas"}},
        {"type": "web_search_tool_result", "tool_use_id": "srv_1", "content": []},
        {"type": "text", "text": text},
    ], stop_reason)


def test_news_uses_web_search_once_per_ttl_and_drops_narration() -> None:
    api = Api(search_reply("Draw of 40 Bcf expected."))
    first = api.agent.news(TUE)
    assert first.endswith("Draw of 40 Bcf expected.") and "Let me search" not in first
    assert api.agent.news(TUE + timedelta(minutes=59)) == first
    assert len(api.requests) == 1
    assert api.requests[0]["tools"][0]["type"] == "web_search_20260209"


def test_news_continues_a_paused_turn() -> None:
    api = Api(search_reply("partial", "pause_turn"), search_reply("Final brief."))
    assert api.agent.news(TUE).endswith("Final brief.")
    assert api.requests[1]["messages"][1]["role"] == "assistant"


def test_news_failure_is_silent_and_not_retried_every_candle() -> None:
    api = Api(500, search_reply("Back."))
    assert api.agent.news(TUE) == ""
    assert api.agent.news(TUE + timedelta(minutes=15)) == ""
    assert api.agent.news(TUE + timedelta(minutes=61)).endswith("Back.")


def test_news_can_be_disabled() -> None:
    api = Api(settings=replace(Settings(), use_news=False))
    assert api.agent.news(TUE) == "" and api.requests == []


def test_final_text_caps_length() -> None:
    class Block:
        type = "text"
        text = "word " * 500

    assert len(final_text([Block()]).split()) == 150

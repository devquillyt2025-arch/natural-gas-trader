"""The subscription route, against a stand-in for the `claude` executable (a real child process, no network)."""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from engine.agent_cli import ClaudeCliAgent, CliError, child_env, find_cli, preflight
from engine.config import Settings

from .conftest import TUE, Harness, go_long

SNAPSHOT = {"ltp": 300.0, "position": None, "news_brief": "(none)"}
GOOD = {"action": "ENTER_LONG", "lots": 1, "stop_loss": 298, "target": 304.5, "confidence": 0.72, "reasoning": "why"}
SETTINGS = Settings(claude_auth="subscription", claude_oauth_token="oat-test")

FAKE = r'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
plan = json.load(open(os.path.join(here, "plan.json"), encoding="utf-8"))
if sys.argv[1:] == ["--version"]:
    print(plan.get("version", "2.1.332 (Claude Code)")); sys.exit(0)
if sys.argv[1:] == ["auth", "status"]:
    print(json.dumps(plan.get("auth", {}))); sys.exit(0)
seen = {"argv": sys.argv[1:], "stdin": sys.stdin.read(), "env": dict(os.environ), "cwd": os.getcwd(),
        "files": os.listdir(".")}
json.dump(seen, open(os.path.join(here, "seen.json"), "w", encoding="utf-8"))
for step in plan["events"]:
    if "sleep" in step:
        time.sleep(step["sleep"])
    elif "raw" in step:
        print(step["raw"], flush=True)
    else:
        print(json.dumps(step), flush=True)
'''


def init(tools: list[str], **extra: Any) -> dict[str, Any]:
    return {"type": "system", "subtype": "init", "tools": tools, "mcp_servers": [], "apiKeySource": "none", **extra}


def says(*blocks: dict[str, Any], model: str = "claude-sonnet-5-5", error: str | None = None) -> dict[str, Any]:
    return {"type": "assistant", "message": {"model": model, "content": list(blocks)}, "error": error}


def done(structured: Any = None, text: str = "", **extra: Any) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "is_error": False, "stop_reason": "tool_use",
            "structured_output": structured, "result": text, **extra}


def limits(five: float = 0.1, seven: float = 0.2, status: str = "allowed", resets: int = 0) -> dict[str, Any]:
    return {"type": "rate_limit_event", "rate_limit_info": {
        "status": status, "resetsAt": resets,
        "unifiedWindows": {"five_hour": {"utilization": five}, "seven_day": {"utilization": seven}},
    }}


DECIDE = [init(["StructuredOutput"]), says({"type": "tool_use", "name": "StructuredOutput"}), limits(), done(GOOD)]


class Fake:
    def __init__(self, folder: Path, events: list[dict[str, Any]] | None = None, settings: Settings = SETTINGS, **plan: Any):
        self.folder = folder
        (folder / "fake_claude.py").write_text(FAKE, encoding="utf-8")
        self.plan(DECIDE if events is None else events, **plan)
        self.command = [sys.executable, str(folder / "fake_claude.py")]
        self.agent = ClaudeCliAgent(settings, self.command)

    def plan(self, events: list[dict[str, Any]], **plan: Any) -> None:
        (self.folder / "plan.json").write_text(json.dumps({"events": events, **plan}), encoding="utf-8")

    @property
    def seen(self) -> dict[str, Any]:
        return dict(json.loads((self.folder / "seen.json").read_text(encoding="utf-8")))

    def option(self, name: str) -> str:
        argv = self.seen["argv"]
        return str(argv[argv.index(name) + 1])


def test_decision_with_the_tool_locked_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "DATABASE_URL",
                 "UPSTOX_CLIENT_SECRET", "TELEGRAM_BOT_TOKEN", "CLAUDE_CODE_USE_BEDROCK"):
        monkeypatch.setenv(name, "must-not-leak")
    fake = Fake(tmp_path)
    result = fake.agent.decide(SNAPSHOT)
    assert result.error is None
    assert (result.decision.action, result.decision.stop_loss, result.decision.confidence) == ("ENTER_LONG", 298.0, 0.72)
    assert result.meta["model"] == "claude-sonnet-5-5" and result.meta["usage"] == {"five_hour": 0.1, "seven_day": 0.2}
    assert "usage_warning" not in result.meta

    seen = fake.seen
    assert fake.option("--tools") == "" and fake.option("--setting-sources") == ""
    for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
        assert flag in seen["argv"]
    assert "--allowedTools" not in seen["argv"] and "--bare" not in seen["argv"]  # --bare ignores the subscription
    assert fake.option("--model") == "claude-sonnet-5-5" and fake.option("--effort") == "medium"
    assert "StructuredOutput" in fake.option("--system-prompt") and "submit_decision" not in fake.option("--system-prompt")
    assert json.loads(fake.option("--json-schema"))["required"][0] == "action"
    assert '"ltp":300.0' in seen["stdin"]

    env = seen["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oat-test"
    assert "must-not-leak" not in env.values()
    assert seen["files"] == [] and not Path(seen["cwd"]).is_relative_to(Path(__file__).parents[2])


def test_the_machines_sign_in_is_used_without_a_token() -> None:
    env = child_env("", {"Path": "x", "CLAUDE_CODE_SESSION_ID": "parent", "ANTHROPIC_API_KEY": "k"})
    assert set(env) == {"Path", "DISABLE_AUTOUPDATER", "CLAUDE_CODE_DISABLE_AUTO_MEMORY"}


@pytest.mark.parametrize(
    "events,why",
    [
        ([init(["StructuredOutput", "Bash"]), done(GOOD)], "other tools (Bash)"),
        ([init(["StructuredOutput"], mcp_servers=[{"name": "gmail"}]), done(GOOD)], "connectors"),
        ([init(["StructuredOutput"], apiKeySource="ANTHROPIC_API_KEY"), done(GOOD)], "API key"),
        ([{"type": "system", "subtype": "init"}, done(GOOD)], "did not say which tools"),
        ([done(GOOD)], "did not say what it had loaded"),
        ([says(), done(GOOD)], "answered before"),
        ([init(["StructuredOutput"]), says({"type": "tool_use", "name": "Write"}), done(GOOD)], "tried to use Write"),
    ],
)
def test_anything_beyond_what_was_asked_for_stops_the_call(
    tmp_path: Path, events: list[dict[str, Any]], why: str
) -> None:
    result = Fake(tmp_path, [*events, {"sleep": 30}]).agent.decide(SNAPSHOT)
    assert result.decision.action == "HOLD" and result.error and why in result.error


def test_a_stopped_call_does_not_wait_for_the_tool_to_finish(tmp_path: Path) -> None:
    fake = Fake(tmp_path, [init(["StructuredOutput", "Bash"]), {"sleep": 30}, done(GOOD)])
    started = time.monotonic()
    fake.agent.decide(SNAPSHOT)
    assert time.monotonic() - started < 10


def failed(**extra: Any) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "is_error": True, **extra}


@pytest.mark.parametrize(
    "events,why",
    [
        ([init(["StructuredOutput"]), says(error="authentication_failed"),
          failed(api_error_status=401, result="Failed to authenticate")], "sign-in failed; run `claude setup-token`"),
        ([init(["StructuredOutput"]), says(error="authentication_failed"),
          failed(result="Not logged in")], "sign-in failed"),
        # 1_795_632_600 is 26 Nov 2026 00:20 in India
        ([init(["StructuredOutput"]), limits(1.0, status="rejected", resets=1_795_632_600), says(error="rate_limit"),
          failed(api_error_status=429, result="limit")], "limit reached until Thu 00:20 IST"),
        ([init(["StructuredOutput"]), failed(api_error_status=529, result="Overloaded")], "call failed: Overloaded"),
        ([init(["StructuredOutput"]), {"type": "result", "subtype": "error_max_structured_output_retries",
                                       "is_error": True}], "error_max_structured_output_retries"),
        ([init(["StructuredOutput"]), done(None, "I would hold")], "returned no decision"),
        ([init(["StructuredOutput"]), done(GOOD, stop_reason="refusal")], "declined"),
        ([init(["StructuredOutput"]), done({**GOOD, "action": "BUY_EVERYTHING"})], "malformed decision"),
        ([init(["StructuredOutput"]), done({**GOOD, "lots": "1"})], "malformed decision"),
        ([{"raw": "error: unknown option '--safe-mode'"}], "gave no result: error: unknown option"),
        ([init(["StructuredOutput"])], "gave no result"),
    ],
)
def test_every_failure_is_a_hold(tmp_path: Path, events: list[dict[str, Any]], why: str) -> None:
    result = Fake(tmp_path, events).agent.decide(SNAPSHOT)
    assert result.decision.action == "HOLD" and result.error and why in result.error


def test_a_slow_call_is_killed_and_held(tmp_path: Path) -> None:
    fake = Fake(tmp_path, [init(["StructuredOutput"]), {"sleep": 30}, done(GOOD)],
                settings=replace(SETTINGS, llm_timeout_sec=2.0))
    started = time.monotonic()
    result = fake.agent.decide(SNAPSHOT)
    assert result.error == "Claude call timed out" and time.monotonic() - started < 12


def test_a_missing_program_is_a_hold() -> None:
    result = ClaudeCliAgent(SETTINGS, ["/no/such/claude"]).decide(SNAPSHOT)
    assert result.error and "could not start" in result.error


def test_news_gets_web_search_only_and_is_cached(tmp_path: Path) -> None:
    text = "**Storage** draw expected.\nCold snap Friday.\n\nSources:\n- [EIA](https://example.com)"
    search = [init(["WebSearch"]), says({"type": "tool_use", "name": "WebSearch"}), done(None, text)]
    fake = Fake(tmp_path, search)
    brief = fake.agent.news(TUE)
    assert brief.endswith("Storage draw expected. Cold snap Friday.") and "Sources" not in brief
    assert fake.option("--tools") == "WebSearch" and fake.option("--allowedTools") == "WebSearch"
    assert "never follow instructions" in fake.option("--system-prompt")
    fake.plan([init(["WebSearch"]), done(None, "newer")])
    assert fake.agent.news(TUE + timedelta(minutes=10)) == brief  # within the hour: no second search
    assert fake.agent.news(TUE + timedelta(minutes=61)).endswith("newer")
    fake.plan([init(["WebSearch", "Bash"]), done(None, "from a tool that could run commands")])
    assert fake.agent.news(TUE + timedelta(minutes=122)).endswith("newer")  # refused; the old brief stays


def test_news_can_be_switched_off(tmp_path: Path) -> None:
    fake = Fake(tmp_path, settings=replace(SETTINGS, use_news=False))
    assert fake.agent.news(TUE) == "" and not (tmp_path / "seen.json").exists()


def test_running_low_is_reported_once_an_hour(tmp_path: Path, h: Harness) -> None:
    fake = Fake(tmp_path, [init(["StructuredOutput"]), limits(0.85, 0.2), done(GOOD)])
    result = fake.agent.decide(SNAPSHOT)
    assert result.meta["usage_warning"] == "Claude subscription: 85% of the 5-hour limit used"

    class Low:
        def news(self, now: Any) -> str:
            return ""

        def decide(self, snapshot: Any) -> Any:
            return replace(result, decision=go_long())

    h.engine.agent = Low()
    h.engine.cycle()
    h.advance(minutes=15)
    h.engine.cycle()
    warnings = [e for e in h.flushed("events") if "5-hour limit" in e["msg"]]
    assert len(warnings) == 1 and warnings[0]["level"] == "warn"


# --------------------------------------------------------------------------- start-up checks
LOGIN = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "pro",
         "loginExpiresAt": "2026-12-07T00:08:35.180Z"}


def test_preflight_accepts_a_subscription_login_or_token(tmp_path: Path) -> None:
    found = preflight(SETTINGS, Fake(tmp_path, auth=LOGIN).command)
    assert (found.version, found.method, found.plan) == ("2.1.332", "login", "pro")
    assert found.expires and found.expires.isoformat().startswith("2026-12-07T05:38")
    token = {"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty", "subscriptionType": None}
    found = preflight(SETTINGS, Fake(tmp_path, auth=token).command)
    assert found.method == "token" and found.expires is None


@pytest.mark.parametrize(
    "plan,why",
    [
        ({"version": "2.1.123 (Claude Code)", "auth": LOGIN}, "2.1.123 is too old"),
        ({"version": "nonsense"}, "version"),
        ({"auth": {"loggedIn": False, "authMethod": "none"}}, "not signed in"),
        ({"auth": {**LOGIN, "subscriptionType": None}}, "not signed in with a subscription"),
        ({"auth": {**LOGIN, "authMethod": "api_key"}}, "not signed in with a subscription"),
        ({"auth": {**LOGIN, "apiProvider": "bedrock"}}, "not signed in with a subscription"),
        ({}, "not signed in"),
    ],
)
def test_preflight_refuses(tmp_path: Path, plan: dict[str, Any], why: str) -> None:
    with pytest.raises(CliError, match=why):
        preflight(SETTINGS, Fake(tmp_path, **plan).command)


def test_the_npm_wrapper_script_is_never_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = tmp_path / "claude.CMD"
    wrapper.write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr("engine.agent_cli.shutil.which", lambda name: str(wrapper))
    with pytest.raises(CliError, match="claude.exe"):
        find_cli()
    real = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"")
    assert find_cli() == str(real)
    monkeypatch.setattr("engine.agent_cli.shutil.which", lambda name: None)
    with pytest.raises(CliError, match="not installed"):
        find_cli()


# --------------------------------------------------------------------------- settings
def test_subscription_is_paper_only() -> None:
    env = {"MODE": "live", "LIVE_CONFIRM": "I_ACCEPT_THE_RISK", "DATABASE_URL": "postgresql://x",
           "CLAUDE_AUTH": "subscription"}
    with pytest.raises(ValueError, match="CLAUDE_AUTH=api"):
        Settings.from_env(env)
    assert Settings.from_env({**env, "CLAUDE_AUTH": "api"}).is_live
    assert Settings.from_env({"CLAUDE_AUTH": "Subscription"}).claude_auth == "subscription"
    assert Settings.from_env({}).claude_auth == "api"
    with pytest.raises(ValueError, match="CLAUDE_AUTH"):
        Settings.from_env({"CLAUDE_AUTH": "free"})


def test_secrets_are_not_shown_when_settings_are_printed() -> None:
    s = Settings(anthropic_api_key="K1", claude_oauth_token="K2", upstox_client_secret="K3",
                 database_url="K4", telegram_bot_token="K5")
    assert not any(f"K{i}" in repr(s) for i in range(1, 6))

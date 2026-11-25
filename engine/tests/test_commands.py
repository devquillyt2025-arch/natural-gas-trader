"""Control actions coming from the UI through the `commands` table."""
from __future__ import annotations

from typing import Any

import pytest

from engine import commands
from engine.store import BusError

from .conftest import Harness, go_long


def test_kill_flattens_immediately_blocks_entries_and_reports(h: Harness) -> None:
    h.open_long()
    cmd = h.command("kill")
    assert cmd["status"] == "acked" and "position closed" in cmd["result"]
    assert h.broker.position() is None
    assert h.store.all_trades()[0].exit_reason == "forced: kill switch"
    assert h.state("session")["kill"] is True
    h.advance(hours=1)
    assert h.engine.cycle() is None


def test_resume_releases_the_kill_switch(h: Harness) -> None:
    h.command("kill")
    cmd = h.command("resume")
    assert cmd["status"] == "acked"
    assert h.state("session")["kill"] is False
    h.agent.queue.append(go_long())
    assert h.engine.cycle().approved  # type: ignore[union-attr]


def test_flatten(h: Harness) -> None:
    assert h.command("flatten")["result"] == "no open position"
    h.open_long()
    assert "position closed" in h.command("flatten")["result"]
    assert h.store.all_trades()[0].exit_reason == "forced: manual flatten"


def test_old_resume_expires_but_old_kill_is_still_honoured(h: Harness) -> None:
    h.store.set("kill", True)
    cmd = h.command("resume", age_sec=3600)
    assert cmd["status"] == "failed" and "expired" in cmd["result"]
    assert h.engine.kill
    h.store.set("kill", False)
    assert h.command("kill", age_sec=3600)["status"] == "acked"
    assert h.engine.kill


def test_update_limits_tighten_ok_loosen_rejected_and_logged(h: Harness) -> None:
    cmd = h.command("update_limits", {"max_risk_inr": 800, "daily_loss_cap_inr": 9000})
    assert cmd["status"] == "failed" and "daily_loss_cap_inr rejected" in cmd["result"]
    assert h.engine.limits.max_risk_inr == 800 and h.engine.limits.daily_loss_cap_inr == 3000
    messages = " | ".join(e["msg"] for e in h.flushed("events"))
    assert "limit changed from UI: max_risk_inr" in messages and "limit change rejected" in messages
    assert h.state("limits")["effective"]["max_risk_inr"] == 800
    assert h.command("update_limits", {"max_risk_inr": 1500})["status"] == "acked"  # back to env value


def test_tightened_limit_is_enforced(h: Harness) -> None:
    h.command("update_limits", {"max_risk_inr": 400})  # 1 lot risks about 500
    h.agent.queue.append(go_long())
    assert h.engine.cycle().verdict_code == "risk_budget"  # type: ignore[union-attr]


class FakeAuth:
    def __init__(self, fail: bool = False):
        self.codes: list[str] = []
        self.fail = fail

    def authenticated(self) -> bool:
        return bool(self.codes) and not self.fail

    def status(self) -> dict[str, Any]:
        return {"authenticated": self.authenticated()}

    def exchange_code(self, code: str) -> None:
        if self.fail:
            raise RuntimeError("token exchange failed: 401")
        self.codes.append(code)


def test_upstox_code_is_exchanged_and_redacted(h: Harness) -> None:
    auth = FakeAuth()
    h.engine.auth = auth
    cmd = h.command("upstox_code", {"code": "SECRET123"})
    assert cmd["status"] == "acked" and auth.codes == ["SECRET123"]
    assert cmd["payload"] == {"code": "[redacted]"}
    assert h.state("upstox_auth") == {"authenticated": True}
    assert "SECRET123" not in str(h.flushed("events"))


def test_failed_login_is_reported_and_still_redacted(h: Harness) -> None:
    h.engine.auth = FakeAuth(fail=True)
    cmd = h.command("upstox_code", {"code": "SECRET123"})
    assert cmd["status"] == "failed" and "token exchange failed" in cmd["result"]
    assert cmd["payload"] == {"code": "[redacted]"}


def test_unknown_command_fails(h: Harness) -> None:
    assert h.command("go_live")["status"] == "failed"
    assert h.s.mode == "paper"


def test_a_reused_command_id_is_still_executed(h: Harness) -> None:
    h.command("flatten")
    h.bus.commands.clear()  # the commands table was recreated; ids start again at 1
    h.advance(minutes=1)
    h.open_long()
    assert h.command("kill")["id"] == 1
    assert h.engine.kill and h.broker.position() is None


def test_command_is_not_executed_twice_when_the_ack_is_lost(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    def lost(command_id: int, values: Any) -> None:
        raise BusError("network")

    h.open_long()
    with monkeypatch.context() as broken:
        broken.setattr(h.bus, "update_command", lost)
        h.command("flatten")
    assert h.bus.commands[0]["status"] == "pending"
    h.advance(minutes=31)  # past the cooldown
    h.agent.queue.append(go_long())
    h.engine.cycle()  # a new position opened since must not be flattened by the replayed command
    assert h.broker.position() is not None
    commands.process(h.engine)
    assert h.bus.commands[0]["status"] == "acked"
    assert h.broker.position() is not None and len(h.store.all_trades()) == 1

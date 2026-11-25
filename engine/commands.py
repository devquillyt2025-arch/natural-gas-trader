"""Control actions the UI inserts into the `commands` table. The engine is the only thing that acts."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import limits as limits_mod
from .brokers.base import BrokerError
from .store import Command

if TYPE_CHECKING:
    from .engine import Engine

log = logging.getLogger("commands")

# These only reduce risk, so they are honoured however old they are.
NEVER_EXPIRE = ("kill", "flatten")


@dataclass(frozen=True)
class Outcome:
    ok: bool
    result: str
    redacted: dict[str, Any] | None = None

    @property
    def status(self) -> str:
        return "acked" if self.ok else "failed"


def process(engine: Engine) -> int:
    commands = engine.store.fetch_commands()
    if not commands:
        return 0
    done: dict[str, list[Any]] = engine.store.get("processed_commands", {})
    for cmd in commands:
        # id AND timestamp: if the table is ever recreated, a new `kill` may reuse an old id and must still run
        key = f"{cmd.id}|{cmd.ts}"
        previous = done.get(key)
        if previous:  # ran already; only the acknowledgement was lost
            engine.store.finish_command(cmd.id, previous[0], previous[1], previous[2])
            continue
        try:
            outcome = _run(engine, cmd)
        except Exception as e:
            log.exception("command %s failed", cmd.type)
            redacted = {"code": "[redacted]"} if cmd.type == "upstox_code" else None
            outcome = Outcome(False, f"{type(e).__name__}: {e}", redacted)
        done[key] = [outcome.status, outcome.result, outcome.redacted]
        engine.store.set("processed_commands", dict(list(done.items())[-200:]))
        engine.store.finish_command(cmd.id, outcome.status, outcome.result, outcome.redacted)
    now = engine.clock.now()
    engine.publish_position()
    engine.publish_limits(now)
    engine.publish_session(now)
    return len(commands)


def _age_seconds(engine: Engine, cmd: Command) -> float:
    if not cmd.ts:
        return 0.0
    return (engine.wall() - datetime.fromisoformat(cmd.ts.replace("Z", "+00:00"))).total_seconds()


def _close(engine: Engine, reason: str) -> str:
    position = engine.broker.position()
    if position is None:
        return "no open position"
    now = engine.clock.now()
    try:
        price = engine.ltp(now)
    except Exception:  # still close: a market order does not need our price
        price = engine._price or position.entry
    try:
        trade = engine.force_exit(reason, price, now)
    except BrokerError as e:
        raise BrokerError(f"could not close: {e}") from e
    if trade:
        return f"position closed at {trade.exit:.2f} (P&L {trade.pnl:+.0f} INR)"
    return "exit sent but not yet confirmed; the engine keeps retrying (watch Events)"


def _run(engine: Engine, cmd: Command) -> Outcome:
    redacted = {"code": "[redacted]"} if cmd.type == "upstox_code" else None
    age = _age_seconds(engine, cmd)
    if cmd.type not in NEVER_EXPIRE and age > engine.s.command_ttl_sec:
        return Outcome(False, f"expired: sent {age / 60:.0f} min ago, while the engine was not listening", redacted)

    if cmd.type == "kill":
        engine.store.set("kill", True)
        engine.notifier.send("KILL SWITCH ON", "warn")
        return Outcome(True, f"kill switch ON; {_close(engine, 'forced: kill switch')}")

    if cmd.type == "resume":
        engine.store.set("kill", False)
        halted = engine.safety_halt
        engine.store.set("safety_halt", None)
        engine.notifier.send("kill switch released" + (f"; safety halt cleared ({halted})" if halted else ""), "info")
        note = "; the daily loss cap still blocks entries today" if engine.halt_latched(engine.clock.now()) else ""
        return Outcome(True, f"resumed{note}")

    if cmd.type == "flatten":
        engine.notifier.send("flatten requested from the UI", "warn")
        return Outcome(True, _close(engine, "forced: manual flatten"))

    if cmd.type == "update_limits":
        result = limits_mod.apply_update(engine.limits, engine.s.limits, engine.s.ceilings, cmd.payload)
        engine.limits = result.limits
        saved = engine.store.get("limits_override", {})
        saved.update({name: getattr(result.limits, name) for name in result.applied})
        engine.store.set("limits_override", saved)
        for name, change in result.applied.items():
            engine.notifier.send(f"limit changed from UI: {name} {change}", "warn")
        for name, why in result.rejected.items():
            engine.notifier.send(f"limit change rejected: {name}: {why}", "warn")
        parts = [f"{n}: {c}" for n, c in result.applied.items()]
        parts += [f"{n} rejected ({w})" for n, w in result.rejected.items()]
        return Outcome(not result.rejected, "; ".join(parts) or "nothing to change")

    if cmd.type == "upstox_code":
        if engine.auth is None:
            return Outcome(False, "this engine runs without Upstox (simulated feed)", redacted)
        code = str(cmd.payload.get("code", ""))
        if not code:
            return Outcome(False, "no authorisation code in the request", redacted)
        engine.auth.exchange_code(code)
        engine.publish_auth()
        engine.notifier.send("Upstox login OK", "info")
        engine._slot = None  # refresh market data right away
        engine._guard("startup", lambda: engine.broker.startup(engine.clock.now()))
        return Outcome(True, "Upstox login OK", redacted)

    return Outcome(False, f"unknown command type {cmd.type!r}")

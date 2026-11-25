"""Store facade used by the engine.

Reads that matter for safety (position, today's trades, kill switch) come from local SQLite, so a bus
outage can never blind the engine. Everything the UI shows is queued and pushed to the bus.
Secrets (the Upstox token) use `set`, which never leaves this machine.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..clock import Clock
from ..config import IST
from ..models import Candle, DecisionRecord, Trade
from .bus import Bus, BusError, MemoryBus, PostgresBus
from .local import LocalStore

__all__ = ["Bus", "BusError", "Command", "LocalStore", "MemoryBus", "PostgresBus", "Store", "Syncer"]

log = logging.getLogger("store")


@dataclass(frozen=True)
class Command:
    id: int
    ts: str
    type: str
    payload: dict[str, Any]


def to_ist_iso(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(IST).isoformat(timespec="seconds")


class Store:
    def __init__(self, local: LocalStore, bus: Bus | None, mode: str, clock: Clock):
        self.local, self.bus, self.mode, self.clock = local, bus, mode, clock
        self._last_id = 0
        self._id_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self.last_command_poll_ok = clock.monotonic()

    def new_id(self) -> int:
        """Engine-generated, time-ordered ids make every bus write an idempotent upsert."""
        with self._id_lock:
            self._last_id = max(self._last_id + 1, int(self.clock.now().timestamp() * 1000))
            return self._last_id

    # --- private (local only) -------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.local.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.local.set(key, value)

    # --- UI state -------------------------------------------------------------------------
    def publish(self, key: str, value: Any, *, if_changed: bool = False) -> None:
        if if_changed and self.local.get(key, table="published", default=object()) == json.loads(json.dumps(value)):
            return
        self.local.set(key, value, table="published")
        if self.bus is not None:
            row = {"key": key, "value": value, "updated_at": datetime.now(IST).isoformat()}
            self.local.enqueue("state", key, row)

    def published(self, key: str) -> Any:
        return self.local.get(key, table="published")

    # --- records --------------------------------------------------------------------------
    def save_decision(self, rec: DecisionRecord) -> None:
        row = rec.to_dict()
        self.local.put_decision(row)
        if self.bus is not None:
            self.local.enqueue("decisions", str(rec.id), row)

    def recent_decisions(self, limit: int) -> list[dict[str, Any]]:
        return self.local.decisions(self.mode, limit)

    def save_trade(self, trade: Trade) -> None:
        row = trade.to_dict()
        self.local.put_trade(row)
        if self.bus is not None:
            self.local.enqueue("trades", trade.id, row)

    def trades_on(self, day: date) -> list[Trade]:
        return [Trade.from_dict(r) for r in self.local.trades(self.mode, day.isoformat())]

    def all_trades(self) -> list[Trade]:
        return [Trade.from_dict(r) for r in self.local.trades(self.mode)]

    def event(self, level: str, msg: str) -> None:
        event_id = self.new_id()
        ts = self.clock.now().isoformat(timespec="seconds")
        self.local.put_event(event_id, ts, level, msg[:1000])
        if self.bus is not None:
            row = {"id": event_id, "ts": ts, "level": level, "msg": msg[:1000]}
            self.local.enqueue("events", str(event_id), row)

    def put_candles(self, tf: str, candles: Sequence[Candle]) -> None:
        if self.bus is None:
            return
        for c in candles:
            ts = c.ts.isoformat(timespec="seconds")
            self.local.enqueue("candles", f"{tf}|{ts}", {"tf": tf, "ts": ts, "o": c.o, "h": c.h, "l": c.l, "c": c.c, "v": c.v})

    def prune_candles(self, tf: str, before: datetime) -> None:
        if self.bus is None:
            return
        try:
            self.bus.prune_candles(tf, before.isoformat(timespec="seconds"))
        except BusError as e:
            log.warning("candle prune failed: %s", e)

    # --- commands -------------------------------------------------------------------------
    def fetch_commands(self) -> list[Command]:
        if self.bus is None:
            return []
        try:
            rows = self.bus.pending_commands()
        except BusError as e:
            log.warning("command poll failed: %s", e)
            return []
        self.last_command_poll_ok = self.clock.monotonic()
        return [Command(int(r["id"]), str(r.get("ts") or ""), str(r["type"]), dict(r.get("payload") or {})) for r in rows]

    def control_age(self) -> float:
        """Seconds since the engine last managed to read the command queue."""
        return 0.0 if self.bus is None else self.clock.monotonic() - self.last_command_poll_ok

    def finish_command(self, command_id: int, status: str, result: str, payload: dict[str, Any] | None = None) -> bool:
        if self.bus is None:
            return True
        values: dict[str, Any] = {"status": status, "result": result[:500]}
        if payload is not None:
            values["payload"] = payload
        try:
            self.bus.update_command(command_id, values)
            return True
        except BusError as e:
            log.warning("command ack failed: %s", e)
            return False

    # --- sync -----------------------------------------------------------------------------
    def flush(self, limit: int = 300) -> int:
        """Push queued rows. Raises BusError (retryable) if the bus is unavailable."""
        if self.bus is None:
            return 0
        with self._flush_lock:
            batch = self.local.outbox(limit)
            by_table: dict[str, list[tuple[int, dict[str, Any]]]] = {}
            for oid, table, row in batch:
                by_table.setdefault(table, []).append((oid, row))
            for table, items in by_table.items():
                try:
                    self.bus.upsert(table, [row for _, row in items])
                except BusError as e:
                    if e.retryable:
                        raise
                    self._push_one_by_one(table, items)
                self.local.drop_outbox([oid for oid, _ in items])
            return len(batch)

    def _push_one_by_one(self, table: str, items: list[tuple[int, dict[str, Any]]]) -> None:
        """A permanently rejected row must not wedge the queue."""
        assert self.bus is not None
        for _, row in items:
            try:
                self.bus.upsert(table, [row])
            except BusError as e:
                if e.retryable:
                    raise
                log.error("dropping row the bus rejects (%s): %s", table, e)

    def hydrate(self) -> int:
        """After a fresh install or lost disk, recover today's trades so daily limits still apply."""
        if self.bus is None:
            return 0
        since = (self.clock.now() - timedelta(days=2)).isoformat(timespec="seconds")
        try:
            trades = self.bus.trades_since(self.mode, since)
            decisions = self.bus.decisions_since(self.mode, since, 10)
        except BusError as e:
            log.warning("hydrate failed: %s", e)
            return 0
        for row in trades:
            row = {**row, "opened_at": to_ist_iso(row["opened_at"]), "closed_at": to_ist_iso(row["closed_at"])}
            self.local.put_trade(Trade.from_dict(row).to_dict())
        for row in decisions:
            self.local.put_decision({**row, "ts": to_ist_iso(row["ts"])})
        return len(trades)


class Syncer(threading.Thread):
    """Pushes the outbox in the background so a slow bus never delays trading logic."""

    def __init__(self, store: Store):
        super().__init__(daemon=True, name="bus-sync")
        self.store = store
        self._halt = threading.Event()

    def run(self) -> None:
        backoff = 1.0
        while not self._halt.is_set():
            try:
                sent = self.store.flush()
                backoff = 1.0
                self._halt.wait(0.2 if sent else 0.8)
            except BusError as e:
                log.warning("bus sync failed, retrying in %.0fs: %s", backoff, e)
                self._halt.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            except Exception:  # never let the sync thread die silently
                log.exception("bus sync crashed")
                self._halt.wait(backoff)

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=5)
        try:
            self.store.flush()
        except BusError:
            pass

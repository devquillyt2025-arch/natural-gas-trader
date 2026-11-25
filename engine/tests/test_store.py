"""Outbox behaviour, and PostgresBus against a real Postgres (the demo database, started with Node)."""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest

from engine.clock import ManualClock
from engine.models import Trade
from engine.simulate import is_local_database
from engine.store import BusError, LocalStore, MemoryBus, PostgresBus, Store

from .conftest import TUE


def make(bus: object = None) -> Store:
    return Store(LocalStore(), bus or MemoryBus(), "paper", ManualClock(TUE))  # type: ignore[arg-type]


def trade(i: str, closed_at: str, pnl: float = -100.0, mode: str = "paper") -> Trade:
    return Trade(i, closed_at, closed_at, "LONG", 1, 250, 300, 299.6, pnl, -0.2, "stop", "", 1, mode)


def test_state_writes_are_coalesced_and_survive_an_outage() -> None:
    bus = MemoryBus()
    store = make(bus)
    bus.failing = True
    for price in (1, 2, 3):
        store.publish("ltp", {"price": price})
    with pytest.raises(BusError):
        store.flush()
    assert len(store.local.outbox(100)) == 1
    bus.failing = False
    assert store.flush() == 1
    assert bus.state_value("ltp") == {"price": 3}
    assert store.flush() == 0


def test_private_values_never_reach_the_bus() -> None:
    bus = MemoryBus()
    store = make(bus)
    store.set("upstox_token", {"token": "SECRET"})
    store.flush()
    assert "SECRET" not in json.dumps([bus.rows(t) for t in bus.tables])


def test_a_row_the_bus_rejects_does_not_wedge_the_queue() -> None:
    class Picky(MemoryBus):
        def upsert(self, table: str, rows: list[dict[str, object]]) -> None:
            if any(r.get("msg") == "bad" for r in rows):
                raise BusError("400 check violation", retryable=False)
            super().upsert(table, rows)

    bus = Picky()
    store = make(bus)
    for msg in ("one", "bad", "two"):
        store.event("info", msg)
    store.flush()
    assert sorted(r["msg"] for r in bus.rows("events")) == ["one", "two"]
    assert store.local.outbox(10) == []


def test_ids_are_unique_and_increasing() -> None:
    store = make()
    ids = [store.new_id() for _ in range(100)]
    assert ids == sorted(set(ids))


def test_hydrate_recovers_todays_trades_in_ist() -> None:
    bus = MemoryBus()
    # 18:40 UTC on the 23rd is 00:10 IST on the 24th
    bus.upsert("trades", [trade("a", "2026-11-23T18:40:00+00:00").to_dict()])
    bus.upsert("trades", [trade("b", "2026-11-24T05:00:00+00:00", mode="live").to_dict()])
    store = Store(LocalStore(), bus, "paper", ManualClock(TUE))
    bus.upsert("trades", [])
    assert store.hydrate() == 1
    assert [t.id for t in store.trades_on(TUE.date())] == ["a"]
    assert store.trades_on(TUE.date() - timedelta(days=1)) == []


WEB = Path(__file__).resolve().parents[2] / "web"


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    """The demo database: PGlite behind the Postgres wire protocol, built from the real migration."""
    node = shutil.which("node")
    if node is None or not (WEB / "node_modules" / "@electric-sql" / "pglite").exists():
        pytest.skip("needs Node and the web packages")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen([node, "mock/serve.mjs", "--port", str(port)], cwd=WEB, stdout=subprocess.PIPE, text=True)
    try:
        assert server.stdout is not None
        line = server.stdout.readline()
        assert "demo database on " in line, line
        yield line.split("demo database on ")[1].strip()
    finally:
        server.kill()
        server.wait()


def test_postgres_bus_round_trip(database: str) -> None:
    bus = PostgresBus(database)
    candle = {"tf": "15m", "ts": "2026-11-24T09:00:00+05:30", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10}
    bus.upsert("candles", [candle, {**candle, "ts": "2026-11-24T09:15:00+05:30"}])
    bus.upsert("candles", [{**candle, "ts": "2026-11-24T09:15:00+05:30", "c": 1.75}])  # same key: updated
    bus.upsert("candles", [])
    bus.prune_candles("15m", "2026-11-24T09:15:00+05:30")
    stamp = "2026-11-24T09:00:00+05:30"
    bus.upsert("state", [{"key": "ltp", "value": {"price": 301.5}, "updated_at": stamp}])
    bus.upsert("state", [{"key": "position", "value": None, "updated_at": stamp}])
    assert bus.state_value("ltp") == {"price": 301.5} and bus.state_value("position") is None

    bus.upsert("trades", [trade("p", "2026-11-24T10:00:00+05:30").to_dict()])
    found = bus.trades_since("paper", "2026-11-24T00:00:00+05:30")
    assert [t["id"] for t in found] == ["p"] and found[0]["closed_at"].startswith("2026-11-24T04:30:00")
    assert Store(LocalStore(), bus, "paper", ManualClock(TUE)).hydrate() == 1

    with psycopg.connect(database, autocommit=True) as conn:
        assert conn.execute("select c from public.candles").fetchall() == [(1.75,)]
        conn.execute("""insert into public.commands (type, payload) values ('update_limits', '{"max_lots": 1}')""")
    [command] = bus.pending_commands()
    assert command["type"] == "update_limits" and command["payload"] == {"max_lots": 1} and command["ts"]
    bus.update_command(command["id"], {"status": "acked", "result": "ok", "payload": {"code": "[redacted]"}})
    assert bus.pending_commands() == []


def test_postgres_bus_drops_only_rows_the_database_can_never_accept(database: str) -> None:
    store = Store(LocalStore(), PostgresBus(database), "paper", ManualClock(TUE))
    store.event("info", "good")
    store.event("shout", "not a level the table allows")
    store.flush()
    assert store.local.outbox(10) == []
    with psycopg.connect(database, autocommit=True) as conn:
        assert [r[0] for r in conn.execute("select msg from public.events order by id")] == ["good"]


def test_postgres_bus_outage_is_retryable_and_never_hangs() -> None:
    silent = socket.socket()  # accepts the connection and then says nothing, like a stalled network
    silent.bind(("127.0.0.1", 0))
    silent.listen()
    stalled = f"postgresql://u:secretpw@127.0.0.1:{silent.getsockname()[1]}/db?sslmode=disable"
    try:
        for url, limit in ((stalled, 0.5), ("postgresql://u:secretpw@127.0.0.1:1/db", 5.0)):
            started = time.monotonic()
            with pytest.raises(BusError) as err:
                PostgresBus(url, timeout=limit).pending_commands()
            assert err.value.retryable and time.monotonic() - started < limit + 3
            assert "secretpw" not in str(err.value)
    finally:
        silent.close()


@pytest.mark.parametrize(
    "url,local",
    [
        ("postgresql://postgres:demo@127.0.0.1:54321/postgres", True),
        ("postgresql://u:p@localhost/db", True),
        ("postgresql://u:p@ep-cool-name.ap-southeast-1.aws.neon.tech/neondb?sslmode=require", False),
        ("postgresql://u:p@127.0.0.1/db?host=ep-x.neon.tech", False),
        ("postgresql://u:p@127.0.0.1,ep-x.neon.tech/db", False),
        ("service=production", False),
        ("postgresql:///db", False),
    ],
)
def test_synthetic_trades_only_go_to_a_database_on_this_machine(url: str, local: bool) -> None:
    assert is_local_database(url) is local

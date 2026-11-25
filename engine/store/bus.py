"""The bus between engine and UI: Postgres (Neon) in production, an in-memory twin in tests."""
from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypeVar

import psycopg
from psycopg import sql
from psycopg.types.json import Json

Row = dict[str, Any]
T = TypeVar("T")

CONFLICT_KEYS: dict[str, tuple[str, ...]] = {
    "state": ("key",),
    "decisions": ("id",),
    "trades": ("id",),
    "candles": ("tf", "ts"),
    "events": ("id",),
}


class BusError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class Bus(Protocol):
    def upsert(self, table: str, rows: list[Row]) -> None: ...
    def pending_commands(self) -> list[Row]: ...
    def update_command(self, command_id: int, values: Mapping[str, Any]) -> None: ...
    def trades_since(self, mode: str, since_iso: str) -> list[Row]: ...
    def decisions_since(self, mode: str, since_iso: str, limit: int) -> list[Row]: ...
    def state_value(self, key: str) -> Any: ...
    def prune_candles(self, tf: str, before_iso: str) -> None: ...


class _Slot:
    """The connection belonging to one calling thread."""

    def __init__(self) -> None:
        self.conn: psycopg.Connection[Any] | None = None


class PostgresBus:
    """Neon (or any Postgres), connected as the database owner. Only the engine holds this connection string.

    Every call has a hard deadline, so a stalled network can never freeze the trading loop. Each calling
    thread keeps its own connection, so a slow upload never delays reading the kill switch.
    """

    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout
        self._local = threading.local()

    def _slot(self) -> _Slot:
        slot: _Slot | None = getattr(self._local, "slot", None)
        if slot is None:
            slot = self._local.slot = _Slot()
        return slot

    def _call(self, what: str, query: Callable[[psycopg.Connection[Any]], T]) -> T:
        slot = self._slot()
        done: list[T] = []
        failed: list[Exception] = []

        def work() -> None:
            try:
                if slot.conn is None or slot.conn.closed:
                    # no server-side prepared statements: they do not survive a connection pooler
                    slot.conn = psycopg.connect(
                        self.url, autocommit=True, prepare_threshold=None,
                        connect_timeout=max(2, int(self.timeout)), application_name="natgas-engine",
                    )
                done.append(query(slot.conn))
            except Exception as e:  # handed to the caller below
                failed.append(e)

        worker = threading.Thread(target=work, daemon=True, name="bus-call")
        worker.start()
        worker.join(self.timeout)
        if worker.is_alive():
            self._local.slot = _Slot()  # the stuck call keeps the old connection; start afresh
            raise BusError(f"{what}: no answer in {self.timeout:g}s")
        if not failed:
            return done[0]
        error = failed[0]
        if not isinstance(error, psycopg.Error):
            raise error
        if slot.conn is not None and (slot.conn.closed or isinstance(error, psycopg.OperationalError)):
            slot.conn.close()
            slot.conn = None
        # Only a row the database can never accept is dropped. Everything else (outage, permissions,
        # schema not applied yet) is kept and retried.
        code = error.sqlstate or ""
        bad_row = code[:2] in ("22", "23") or code in ("42703", "42804")
        first_line = (str(error).splitlines() or [""])[0][:200]
        raise BusError(f"{what}: {type(error).__name__}: {first_line}", not bad_row)

    def _rows(self, what: str, query: str, args: tuple[Any, ...]) -> list[Row]:
        # as JSON, so timestamps arrive as ISO strings and numbers as numbers
        wrapped = f"select coalesce(json_agg(q), '[]'::json) from ({query}) q"

        def run(conn: psycopg.Connection[Any]) -> list[Row]:
            row = conn.execute(wrapped.encode(), args).fetchone()
            return list(row[0]) if row else []

        return self._call(what, run)

    def upsert(self, table: str, rows: list[Row]) -> None:
        if not rows:
            return
        keys = CONFLICT_KEYS[table]
        columns = list(dict.fromkeys(name for row in rows for name in row))
        updates = [
            sql.SQL("{c} = excluded.{c}").format(c=sql.Identifier(c)) for c in columns if c not in keys
        ]
        statement = sql.SQL(
            "insert into {target} ({names}) select {names} from json_populate_recordset(null::{target}, %s::json) "
            "on conflict ({keys}) do update set {updates}"
        ).format(
            target=sql.Identifier("public", table),
            names=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            keys=sql.SQL(", ").join(sql.Identifier(k) for k in keys),
            updates=sql.SQL(", ").join(updates),
        )

        def run(conn: psycopg.Connection[Any]) -> None:
            conn.execute(statement, (Json(rows),))

        self._call(f"upsert {table}", run)

    def pending_commands(self) -> list[Row]:
        return self._rows(
            "read commands", "select * from public.commands where status = 'pending' order by id limit 20", ()
        )

    def update_command(self, command_id: int, values: Mapping[str, Any]) -> None:
        sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(name)) for name in values)
        statement = sql.SQL("update public.commands set {} where id = %s").format(sets)
        args = [Json(v) if isinstance(v, dict) else v for v in values.values()]

        def run(conn: psycopg.Connection[Any]) -> None:
            conn.execute(statement, (*args, command_id))

        self._call("update command", run)

    def trades_since(self, mode: str, since_iso: str) -> list[Row]:
        return self._rows(
            "read trades",
            "select * from public.trades where mode = %s and closed_at >= %s::timestamptz order by closed_at",
            (mode, since_iso),
        )

    def decisions_since(self, mode: str, since_iso: str, limit: int) -> list[Row]:
        return self._rows(
            "read decisions",
            "select * from public.decisions where mode = %s and ts >= %s::timestamptz order by ts desc limit %s",
            (mode, since_iso, limit),
        )

    def state_value(self, key: str) -> Any:
        rows = self._rows("read state", "select value from public.state where key = %s", (key,))
        return rows[0]["value"] if rows else None

    def prune_candles(self, tf: str, before_iso: str) -> None:
        def run(conn: psycopg.Connection[Any]) -> None:
            conn.execute("delete from public.candles where tf = %s and ts < %s::timestamptz", (tf, before_iso))

        self._call("prune candles", run)


class MemoryBus:
    """Same behaviour as PostgresBus, held in dicts (tests, simulation, offline demo export)."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[tuple[Any, ...], Row]] = {t: {} for t in CONFLICT_KEYS}
        self.commands: list[Row] = []
        self.failing = False  # set True to simulate an outage
        self._lock = threading.Lock()

    def _check(self) -> None:
        if self.failing:
            raise BusError("bus unavailable")

    def upsert(self, table: str, rows: list[Row]) -> None:
        self._check()
        with self._lock:
            for row in rows:
                key = tuple(row[k] for k in CONFLICT_KEYS[table])
                self.tables[table][key] = {**self.tables[table].get(key, {}), **row}

    def rows(self, table: str) -> list[Row]:
        with self._lock:
            return list(self.tables[table].values())

    def add_command(self, type_: str, payload: Row | None = None, ts: str = "") -> Row:
        cmd: Row = {
            "id": len(self.commands) + 1, "ts": ts, "type": type_, "payload": payload or {},
            "status": "pending", "result": None,
        }
        self.commands.append(cmd)
        return cmd

    def pending_commands(self) -> list[Row]:
        self._check()
        return [dict(c) for c in self.commands if c["status"] == "pending"]

    def update_command(self, command_id: int, values: Mapping[str, Any]) -> None:
        self._check()
        for c in self.commands:
            if c["id"] == command_id:
                c.update(values)

    def trades_since(self, mode: str, since_iso: str) -> list[Row]:
        self._check()
        return [r for r in self.rows("trades") if r["mode"] == mode and r["closed_at"] >= since_iso]

    def decisions_since(self, mode: str, since_iso: str, limit: int) -> list[Row]:
        self._check()
        found = [r for r in self.rows("decisions") if r["mode"] == mode and r["ts"] >= since_iso]
        return sorted(found, key=lambda r: r["ts"], reverse=True)[:limit]

    def state_value(self, key: str) -> Any:
        self._check()
        row = self.tables["state"].get((key,))
        return row["value"] if row else None

    def prune_candles(self, tf: str, before_iso: str) -> None:
        self._check()
        with self._lock:
            for key in [k for k, r in self.tables["candles"].items() if r["tf"] == tf and r["ts"] < before_iso]:
                del self.tables["candles"][key]

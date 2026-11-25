"""Local SQLite: the engine's source of truth, plus an outbox for rows not yet pushed to the bus."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv        (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS published (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, ts TEXT, mode TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trades    (id TEXT PRIMARY KEY, closed_at TEXT, mode TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events    (id INTEGER PRIMARY KEY, ts TEXT, level TEXT, msg TEXT);
CREATE TABLE IF NOT EXISTS outbox    (id INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT, k TEXT, row TEXT, UNIQUE(tbl, k));
"""


class LocalStore:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        if str(path) != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _run(self, sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # --- key/value ------------------------------------------------------------------------
    def get(self, key: str, default: Any = None, table: str = "kv") -> Any:
        rows = self._run(f"SELECT v FROM {table} WHERE k=?", (key,))
        return json.loads(rows[0][0]) if rows else default

    def set(self, key: str, value: Any, table: str = "kv") -> None:
        self._run(f"INSERT OR REPLACE INTO {table}(k, v) VALUES (?, ?)", (key, json.dumps(value)))

    # --- records --------------------------------------------------------------------------
    def put_decision(self, row: dict[str, Any]) -> None:
        self._run(
            "INSERT OR REPLACE INTO decisions(id, ts, mode, data) VALUES (?,?,?,?)",
            (row["id"], row["ts"], row["mode"], json.dumps(row)),
        )

    def decisions(self, mode: str, limit: int) -> list[dict[str, Any]]:
        rows = self._run("SELECT data FROM decisions WHERE mode=? ORDER BY id DESC LIMIT ?", (mode, limit))
        return [json.loads(r[0]) for r in rows]

    def put_trade(self, row: dict[str, Any]) -> None:
        self._run(
            "INSERT OR REPLACE INTO trades(id, closed_at, mode, data) VALUES (?,?,?,?)",
            (row["id"], row["closed_at"], row["mode"], json.dumps(row)),
        )

    def trades(self, mode: str, day: str | None = None) -> list[dict[str, Any]]:
        if day is None:
            rows = self._run("SELECT data FROM trades WHERE mode=? ORDER BY closed_at", (mode,))
        else:
            rows = self._run(
                "SELECT data FROM trades WHERE mode=? AND substr(closed_at,1,10)=? ORDER BY closed_at", (mode, day)
            )
        return [json.loads(r[0]) for r in rows]

    def put_event(self, event_id: int, ts: str, level: str, msg: str) -> None:
        self._run("INSERT OR REPLACE INTO events(id, ts, level, msg) VALUES (?,?,?,?)", (event_id, ts, level, msg))

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._run("SELECT id, ts, level, msg FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(zip(("id", "ts", "level", "msg"), r, strict=True)) for r in rows]

    # --- outbox ---------------------------------------------------------------------------
    def enqueue(self, table: str, key: str, row: dict[str, Any]) -> None:
        # A newer version of the same row replaces the queued one (and moves to the back).
        self._run("INSERT OR REPLACE INTO outbox(tbl, k, row) VALUES (?,?,?)", (table, key, json.dumps(row)))

    def outbox(self, limit: int) -> list[tuple[int, str, dict[str, Any]]]:
        rows = self._run("SELECT id, tbl, row FROM outbox ORDER BY id LIMIT ?", (limit,))
        return [(r[0], r[1], json.loads(r[2])) for r in rows]

    def drop_outbox(self, ids: list[int]) -> None:
        if ids:
            self._run(f"DELETE FROM outbox WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))

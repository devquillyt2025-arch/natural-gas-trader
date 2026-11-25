"""SQLite persistence: decisions, trades, key/value state. One connection per call (thread-safe)."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

from .config import IST

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, price REAL, action TEXT, approved INTEGER, verdict TEXT,
  confidence REAL, reasoning TEXT, payload TEXT, mode TEXT
);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opened_at TEXT, closed_at TEXT, side TEXT, lots INTEGER, qty INTEGER,
  entry REAL, exit REAL, pnl REAL, exit_reason TEXT, reasoning TEXT, mode TEXT
);
CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, msg TEXT);
"""


def now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if path == ":memory:" else None
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        c = self._mem or sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            if not self._mem:
                c.close()

    # --- key/value ---------------------------------------------------------
    def get(self, k: str, default: Any = None) -> Any:
        with self._conn() as c:
            r = c.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def set(self, k: str, v: Any) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO state(k, v) VALUES (?, ?)", (k, json.dumps(v)))

    # --- decisions ---------------------------------------------------------
    def log_decision(self, ts: str, price: float, d: dict, approved: bool, verdict: str, mode: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO decisions(ts, price, action, approved, verdict, confidence, reasoning, payload, mode)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, price, d.get("action"), int(approved), verdict, d.get("confidence"),
                 d.get("reasoning"), json.dumps(d), mode),
            )

    def decisions(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- trades ------------------------------------------------------------
    def log_trade(self, t: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO trades(opened_at, closed_at, side, lots, qty, entry, exit, pnl, exit_reason, reasoning, mode)"
                " VALUES (:opened_at,:closed_at,:side,:lots,:qty,:entry,:exit,:pnl,:exit_reason,:reasoning,:mode)",
                t,
            )

    def trades(self, limit: int = 100) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def trades_on(self, date_iso: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM trades WHERE substr(closed_at,1,10)=? ORDER BY id", (date_iso,)).fetchall()
        return [dict(r) for r in rows]

    # --- events ------------------------------------------------------------
    def event(self, level: str, msg: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO events(ts, level, msg) VALUES (?,?,?)", (now_iso(), level, msg))

    def events(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

"""The trading loop: snapshot -> Claude -> risk guard -> broker. Time is injected so it can be simulated."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from . import risk, sessions, snapshot
from .agent import ClaudeAgent
from .broker import Broker
from .config import IST, Settings
from .feed import Feed
from .models import Decision, Position
from .notify import Notifier
from .upstox import UpstoxError
from .store import Store

log = logging.getLogger("engine")


class Engine:
    def __init__(self, s: Settings, store: Store, feed: Feed, broker: Broker, agent, notifier: Notifier):
        self.s, self.store, self.feed, self.broker, self.agent, self.notify = s, store, feed, broker, agent, notifier
        self.fail_count = 0
        self.last_error = ""
        self._stop = threading.Event()

    # ------------------------------------------------------------------ helpers
    def _daily_pnl(self, now: datetime, ltp: float, pos: Optional[Position]) -> tuple[float, int, Optional[datetime]]:
        today = self.store.trades_on(now.date().isoformat())
        realized = sum(t["pnl"] for t in today)
        losses = [t for t in today if t["pnl"] < 0]
        last_loss = datetime.fromisoformat(losses[-1]["closed_at"]) if losses else None
        unreal = pos.unrealized(ltp) if pos else 0.0
        return realized + unreal, len(today), last_loss

    def _closed(self, t: Optional[dict]) -> None:
        if t:
            self.notify.send(
                f"CLOSED {t['side']} {t['lots']}L {t['entry']} -> {t['exit']} | {t['exit_reason']} | PnL {t['pnl']:+.0f} INR",
                "trade",
            )

    def kill_on(self) -> bool:
        return bool(self.store.get("kill", False)) or self.broker.orphan()

    # -------------------------------------------------------------- fast monitor
    def monitor(self, now: datetime, hi: Optional[float] = None, lo: Optional[float] = None) -> None:
        pos = self.broker.position()
        if not pos and not self.s.is_live:
            return
        ltp = self.feed.ltp(now)
        self.store.set("last_ltp", {"p": ltp, "t": now.isoformat(timespec="seconds")})
        closed = self.broker.poll(ltp, now, hi, lo)
        self._closed(closed)
        pos = self.broker.position()
        if pos and (sessions.must_flatten(now, self.s) or self.kill_on()):
            self._closed(self.broker.exit(ltp, "end-of-session flatten" if sessions.must_flatten(now, self.s) else "kill switch", now))
        if self.broker.orphan():
            self.notify.send("ORPHAN position found at broker that the app does not know about. Entries blocked.", "error")

    # ------------------------------------------------------------ decision cycle
    def cycle(self, now: datetime) -> Optional[dict]:
        if not sessions.market_open(now, self.s):
            return None
        pos = self.broker.position()
        ltp = self.feed.ltp(now)
        self.store.set("last_ltp", {"p": ltp, "t": now.isoformat(timespec="seconds")})
        daily, trades_n, last_loss = self._daily_pnl(now, ltp, pos)

        # Code-level exits: no LLM involved.
        if pos:
            why = None
            if sessions.must_flatten(now, self.s):
                why = "end-of-session flatten"
            elif self.kill_on():
                why = "kill switch"
            elif risk.daily_halted(self.s, daily):
                why = "daily loss cap"
            if why:
                self._closed(self.broker.exit(ltp, why, now))
                self.store.log_decision(now.isoformat(timespec="seconds"), ltp,
                                        Decision("EXIT", reasoning=why).to_dict(), True, f"forced: {why}", self.s.mode)
                return None

        # Flat and entries not allowed: skip the LLM call entirely (saves cost, nothing to decide).
        if not pos and not sessions.entry_allowed(now, self.s):
            return None

        news = self.agent.news(now) if self.s.use_news else ""
        last = self.store.decisions(5)
        snap = snapshot.build(self.s, self.feed, now, pos, daily, trades_n, news, last)
        d = self.agent.decide(snap)

        atr15 = snap["intraday_15m"]["atr14"]
        ctx = risk.RiskContext(now, ltp, atr15, self.feed.contract().lot_size, pos, daily,
                               trades_n, last_loss, self.kill_on())
        v = risk.validate(self.s, d, ctx)
        self.store.log_decision(now.isoformat(timespec="seconds"), ltp, d.to_dict(), v.ok, v.reason, self.s.mode)
        if not v.ok or d.action == "HOLD":
            return {"decision": d, "verdict": v}

        d = v.decision
        try:
            if d.action in ("ENTER_LONG", "ENTER_SHORT"):
                side = "LONG" if d.action == "ENTER_LONG" else "SHORT"
                lot = self.feed.contract().lot_size
                p = self.broker.enter(side, d.lots, d.lots * lot, ltp, d.stop_loss, d.target, d.reasoning, now)
                self.notify.send(f"OPENED {side} {d.lots}L @ {p.entry} SL {p.stop_loss} TP {p.target} | {d.reasoning}", "trade")
            elif d.action == "EXIT":
                self._closed(self.broker.exit(ltp, "agent exit", now))
            elif d.action == "TRAIL_STOP":
                self.broker.move_stop(d.stop_loss)
                self.notify.send(f"STOP moved to {d.stop_loss} | {d.reasoning}", "trade")
        except UpstoxError as e:
            self.notify.send(f"ORDER ERROR on {d.action}: {e}", "error")
        return {"decision": d, "verdict": v}

    # ----------------------------------------------------------------- live loop
    def run_forever(self, client=None) -> None:
        last_slot: Optional[datetime] = None
        last_monitor = 0.0
        warned_auth = 0.0
        self.notify.send(f"engine started in {self.s.mode.upper()} mode", "info")
        while not self._stop.is_set():
            now = datetime.now(IST)
            self.store.set("heartbeat", now.isoformat(timespec="seconds"))
            try:
                if client is not None and not client.authed():
                    if time.time() - warned_auth > 1800:
                        self.notify.send("Upstox login needed: open /auth/login", "warn")
                        warned_auth = time.time()
                elif sessions.market_open(now, self.s):
                    if time.time() - last_monitor >= self.s.monitor_sec:
                        self.monitor(now)
                        last_monitor = time.time()
                    iv = self.s.decision_interval_min
                    slot = now.replace(minute=(now.minute // iv) * iv, second=0, microsecond=0)
                    if slot != last_slot and (now - slot).total_seconds() >= 8:
                        last_slot = slot
                        self.cycle(now)
                    self.fail_count = 0
                    self.last_error = ""
            except Exception as e:
                self.fail_count += 1
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                log.exception("loop error")
                self.store.event("error", self.last_error)
                if self.fail_count == 3:
                    self.notify.send(f"3 consecutive engine errors: {self.last_error}", "error")
            self._stop.wait(5)

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------- status
    def status(self) -> dict:
        now = datetime.now(IST)
        pos = self.broker.position()
        lp = self.store.get("last_ltp") or {}
        ltp = lp.get("p")
        daily = None
        if ltp is not None:
            daily = round(self._daily_pnl(now, ltp, pos)[0], 2)
        return {
            "mode": self.s.mode,
            "symbol": self.s.symbol,
            "kill": self.kill_on(),
            "orphan": self.broker.orphan(),
            "market_open": sessions.market_open(now, self.s),
            "entries_allowed": sessions.entry_allowed(now, self.s),
            "next_eia": sessions.next_eia(now).isoformat(timespec="minutes"),
            "ltp": ltp,
            "ltp_at": lp.get("t"),
            "position": pos.to_dict() if pos else None,
            "unrealized": round(pos.unrealized(ltp), 2) if pos and ltp else None,
            "pnl_today": daily,
            "limits": {"max_lots": self.s.max_lots, "max_risk_inr": self.s.max_risk_inr,
                       "daily_loss_cap_inr": self.s.daily_loss_cap_inr},
            "heartbeat": self.store.get("heartbeat"),
            "last_error": self.last_error,
            "model": self.s.model,
            "now": now.isoformat(timespec="seconds"),
        }

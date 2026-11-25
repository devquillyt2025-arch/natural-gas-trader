"""The trading loop: snapshot -> Claude -> risk guard -> broker, plus the fast safety monitor.

One scheduler thread owns all state and all broker calls. Only the model call runs in a worker, so
stops, targets, the kill switch and the force-flat keep being checked while Claude is thinking.
Time is injected, so the same code is replayed offline.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol

from . import __version__, commands, risk, sessions, snapshot
from . import limits as limits_mod
from .agent_base import Agent, AgentResult
from .brokers.base import Broker, EntryRequest
from .clock import Clock
from .config import IST, Settings
from .feed import Feed
from .indicators import atr
from .models import ENTRY_ACTIONS, Candle, Contract, Decision, DecisionRecord, Position, Trade
from .notify import Notifier
from .sessions import EiaCalendar
from .store import Store

log = logging.getLogger("engine")

CHART_BARS = 600
CHART_BARS_1D = 60
DECISION_BARS = 200
ALERT_REPEAT = timedelta(minutes=10)


class AuthProvider(Protocol):
    def authenticated(self) -> bool: ...
    def status(self) -> dict[str, Any]: ...
    def exchange_code(self, code: str) -> None: ...


class InlineExecutor(Executor):
    """Runs the job immediately (tests and bar replay stay deterministic)."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as e:
            future.set_exception(e)
        return future


@dataclass
class PendingDecision:
    future: Future[AgentResult]
    snapshot: dict[str, Any]
    position_id: str | None
    started_mono: float
    atr: float | None
    data_fresh: bool


@dataclass(frozen=True)
class Day:
    realized: float
    unrealized: float
    trades: int
    last_loss_at: datetime | None

    @property
    def total(self) -> float:
        return self.realized + self.unrealized


def is_fresh(bars: list[Candle], now: datetime, minutes: int) -> bool:
    """The last closed bar must be recent; otherwise the feed (or the market) has stopped."""
    width = timedelta(minutes=minutes)
    return bool(bars) and now - (bars[-1].ts + width) <= width + timedelta(minutes=5)


class Engine:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        feed: Feed,
        broker: Broker,
        agent: Agent,
        clock: Clock,
        *,
        auth: AuthProvider | None = None,
        executor: Executor | None = None,
        wall: Callable[[], datetime] = lambda: datetime.now(IST),
        instance_id: str = "engine",
    ):
        self.s, self.store, self.feed, self.broker, self.agent, self.clock = settings, store, feed, broker, agent, clock
        self.auth, self.wall, self.instance_id = auth, wall, instance_id
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent")
        self.notifier = Notifier(settings, store)
        self.calendar = EiaCalendar(settings.eia_overrides)
        self.limits = limits_mod.restore(settings.limits, settings.ceilings, store.get("limits_override", {}))
        self.started_at = wall().isoformat(timespec="seconds")
        self._pending: PendingDecision | None = None
        self._due: dict[str, float] = {}
        self._slot: datetime | None = None
        self._errors: dict[str, str] = {}
        self._alerted: dict[str, datetime] = {}
        self._price: float | None = None
        self._contract: Contract | None = None
        self._data_fresh = True
        self._levels: dict[str, Any] = {}
        self._full_publish_day: str = ""
        self._windows: tuple[str, list[dict[str, str]]] = ("", [])
        self._range_checked: datetime | None = None
        self._stop_set_at: datetime | None = None  # 1m ranges older than the current stop must not trigger it
        self._forced_logged: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------ small helpers
    @property
    def kill(self) -> bool:
        return bool(self.store.get("kill", False))

    @property
    def safety_halt(self) -> str | None:
        value = self.store.get("safety_halt")
        return str(value) if value else None

    def halt_latched(self, now: datetime) -> bool:
        return bool(self.store.get("daily_halt") == now.astimezone(IST).date().isoformat())

    def authed(self) -> bool:
        return self.auth is None or self.auth.authenticated()

    def _due_now(self, name: str, every: float) -> bool:
        mono = self.clock.monotonic()
        if mono - self._due.get(name, float("-inf")) >= every:
            self._due[name] = mono
            return True
        return False

    def alert_once(self, key: str, message: str, level: str = "error", every: timedelta = ALERT_REPEAT) -> None:
        now = self.clock.now()
        if key not in self._alerted or now - self._alerted[key] >= every:
            self._alerted[key] = now
            self.notifier.send(message, level)

    def set_error(self, source: str, message: str) -> None:
        message = message[:300]
        self._errors[source] = message
        self.alert_once(f"error:{source}:{message}", f"{source}: {message}")
        self.store.publish("engine_error", {"source": source, "msg": message, "ts": self.wall().isoformat(timespec="seconds")})

    def clear_error(self, source: str) -> None:
        if self._errors.pop(source, None) is None:
            return
        if self._errors:
            src, msg = next(iter(self._errors.items()))
            self.store.publish("engine_error", {"source": src, "msg": msg, "ts": self.wall().isoformat(timespec="seconds")})
        else:
            self.store.publish("engine_error", None)

    def _guard(self, source: str, fn: Callable[[], object]) -> None:
        """Any failure is reported and leaves things as they are (HOLD)."""
        try:
            fn()
        except Exception as e:
            log.exception("%s failed", source)
            self.set_error(source, f"{type(e).__name__}: {e}")
        else:
            self.clear_error(source)

    def ltp(self, now: datetime) -> float:
        price = self.feed.ltp(now)
        self._price = price
        self.store.publish("ltp", {"price": round(price, 2), "ts": now.isoformat(timespec="seconds")})
        return price

    def publish_forming(self, now: datetime) -> None:
        """The candle still being made, so the chart moves between closes. Decisions only use closed candles."""
        try:
            bar = self.feed.forming(now)
        except Exception as e:  # only the picture; never let it disturb monitoring
            log.debug("no forming candle: %s", e)
            return
        value = None
        if bar is not None:
            value = {"tf": self.s.tf, "ts": bar.ts.isoformat(timespec="seconds"),
                     "o": bar.o, "h": bar.h, "l": bar.l, "c": bar.c, "v": round(bar.v)}
        self.store.publish("forming", value, if_changed=True)

    def day(self, now: datetime, price: float | None, position: Position | None) -> Day:
        trades = self.store.trades_on(now.astimezone(IST).date())
        losses = [datetime.fromisoformat(t.closed_at) for t in trades if t.pnl < 0]
        return Day(
            realized=sum(t.pnl for t in trades),
            unrealized=position.unrealized(price) if position and price is not None else 0.0,
            trades=len(trades) + (1 if position else 0),
            last_loss_at=max(losses) if losses else None,
        )

    def context(
        self, now: datetime, price: float, atr_now: float | None, position: Position | None, day: Day, data_fresh: bool
    ) -> risk.RiskContext:
        contract = self._contract or self.feed.contract(now)
        return risk.RiskContext(
            now=now, ltp=price, atr=atr_now, multiplier=contract.multiplier, position=position,
            daily_pnl=day.total, trades_today=day.trades, last_loss_at=day.last_loss_at,
            kill=self.kill, orphan=self.broker.orphan(), safety_halt=self.safety_halt is not None,
            daily_halt_latched=self.halt_latched(now), data_fresh=data_fresh,
            control_ok=self.store.control_age() <= self.s.control_timeout_sec,
            tick_size=contract.tick_size, no_trade_dates=self.s.no_trade_dates,
        )

    def _latch_daily_halt(self, now: datetime, total: float) -> None:
        if risk.daily_loss_breached(self.limits, total) and not self.halt_latched(now):
            self.store.set("daily_halt", now.astimezone(IST).date().isoformat())
            self.notifier.send(f"Daily loss cap hit ({total:.0f} INR). No more entries today.", "error")

    # ------------------------------------------------------------------ exits
    def on_closed(self, trade: Trade) -> None:
        self.notifier.send(
            f"CLOSED {trade.side} {trade.lots} lot {trade.entry:.2f} -> {trade.exit:.2f} "
            f"({trade.exit_reason}) P&L {trade.pnl:+.0f} INR",
            "trade",
        )
        now = self.clock.now()
        self._latch_daily_halt(now, self.day(now, None, None).realized)

    def force_exit(self, reason: str, price: float, now: datetime) -> Trade | None:
        """Code-level exit. The model is never asked."""
        position = self.broker.position()
        if position is None:
            return None
        if (position.id, reason) not in self._forced_logged:
            self._forced_logged.add((position.id, reason))
            self.record(
                self.store.new_id(), Decision("EXIT", reasoning=f"Code-level exit ({reason}); the model was not asked."),
                True, "forced", reason, price, now, None,
            )
        trade = self.broker.exit(price, reason, now)
        if trade:
            self.on_closed(trade)
        return trade

    # ------------------------------------------------------------------ fast monitor
    def monitor(
        self,
        now: datetime | None = None,
        hi: float | None = None,
        lo: float | None = None,
        open_: float | None = None,
    ) -> None:
        now = now or self.clock.now()
        position = self.broker.position()
        price_ok = True
        try:
            self.ltp(now)
        except Exception as e:  # time-based exits must still happen without a price
            price_ok = False
            self.set_error("price", f"{type(e).__name__}: {e}")
        else:
            self.clear_error("price")
            self.publish_forming(now)
        price = self._price if self._price is not None else (position.entry if position else None)
        if price is None:
            if not self.s.is_live:
                return
            price = 0.0  # flat and no quote yet: the live broker must still reconcile with the exchange

        if position and not self.s.is_live and price_ok and hi is None and lo is None:
            floor = datetime.min.replace(tzinfo=IST)
            since = max(datetime.fromisoformat(position.opened_at), self._stop_set_at or floor, self._range_checked or floor)
            try:
                span = self.feed.bar_range(since, now)
            except Exception as e:
                log.warning("1m range unavailable: %s", e)
            else:
                self._range_checked = now - timedelta(minutes=1)
                if span:
                    hi, lo = span

        # A paper fill needs a real price. The live broker still reconciles with the exchange without one.
        if price_ok or self.s.is_live:
            trade = self.broker.poll(price, now, hi, lo, open_)
            if trade:
                self.on_closed(trade)

        position = self.broker.position()
        day = self.day(now, price, position)
        self._latch_daily_halt(now, day.total)
        reason = risk.forced_exit_reason(
            self.limits, self.s.force_flat, now, position, day.total, self.kill, self.halt_latched(now)
        )
        # an exit already in progress is driven by broker.poll above; do not push it twice per tick
        if reason and position and not position.flatten_reason:
            self.force_exit(reason, price, now)
        if self.broker.orphan():
            self.alert_once("orphan", "ORPHAN: the broker holds a position this app does not know about. Entries blocked.")
        self.publish_position()
        self.publish_limits(now)

    # ------------------------------------------------------------------ decision cycle
    def start_cycle(self, now: datetime | None = None) -> PendingDecision | None:
        now = now or self.clock.now()
        if not sessions.market_open(now, self.s.no_trade_dates) or not self.authed():
            return None
        position = self.broker.position()
        price = self.ltp(now)
        day = self.day(now, price, position)
        self._contract = self.feed.contract(now)

        if position:
            reason = risk.forced_exit_reason(
                self.limits, self.s.force_flat, now, position, day.total, self.kill, self.halt_latched(now)
            )
            if reason:
                self._latch_daily_halt(now, day.total)
                self.force_exit(reason, price, now)
                return None
            if position.flatten_reason:
                return None  # an exit is already in progress; nothing for the model to decide

        bars = self.feed.candles(self.s.tf, DECISION_BARS, now)
        self._data_fresh = is_fresh(bars, now, self.s.candle_min)
        atr_now = atr(bars)
        context = self.context(now, price, atr_now, position, day, self._data_fresh)
        blocked = risk.entry_block(self.limits, replace(context, position=None), self.calendar)
        if position is None and blocked:
            log.info("flat and entries blocked (%s): model not called", blocked[0])
            return None

        daily = self.feed.candles("1d", CHART_BARS_1D, now)
        snap = snapshot.build(
            settings=self.s, limits=self.limits, calendar=self.calendar, contract=self._contract,
            daily=daily, bars=bars, now=now, ltp=price, position=position, daily_pnl=day.total,
            trades_today=day.trades, entry_block=blocked[1] if blocked else None,
            last_decisions=self.store.recent_decisions(5),
        )

        def job() -> AgentResult:
            snap["news_brief"] = self.agent.news(now) or "(none)"
            return self.agent.decide(snap)

        pending = PendingDecision(
            self.executor.submit(job), snap, position.id if position else None,
            self.clock.monotonic(), atr_now, self._data_fresh,
        )
        self._pending = pending
        return pending

    def finish_cycle(self, pending: PendingDecision) -> DecisionRecord:
        self._pending = None
        try:
            result = pending.future.result()
        except Exception as e:
            log.exception("agent job failed")
            result = AgentResult.hold(f"agent error: {type(e).__name__}: {e}")
        now = self.clock.now()
        snap = pending.snapshot
        news = snap.get("news_brief")
        if news and news != "(none)":
            previous = self.store.published("news_brief") or {}
            if previous.get("text") != news:
                self.store.publish("news_brief", {"text": news, "ts": now.isoformat(timespec="seconds")})

        record_id = self.store.new_id()
        if result.error:
            self.set_error("agent", result.error)
            return self.record(record_id, Decision("HOLD", reasoning=result.error), False, "agent_error",
                               result.error, snap.get("ltp"), now, snap)
        self.clear_error("agent")
        served_by = result.meta.get("model")
        if served_by and not str(served_by).startswith(self.s.model):
            self.notifier.send(f"this decision came from {served_by}, not {self.s.model} (server-side fallback)", "warn")
        if result.meta.get("usage_warning"):
            self.alert_once("claude-usage", str(result.meta["usage_warning"]), "warn", every=timedelta(hours=1))
        d = result.decision
        try:
            price = self.ltp(now)  # validate against the price now, not the one the model saw
        except Exception as e:
            return self.record(record_id, d, False, "no_price", f"could not refresh price: {e}"[:200],
                               snap.get("ltp"), now, snap)

        position = self.broker.position()
        if d.action != "HOLD" and self.clock.monotonic() - pending.started_mono > self.s.decision_max_age_sec:
            return self.record(record_id, d, False, "stale_decision", "answer arrived too late to act on", price, now, snap)
        if d.action != "HOLD" and (position.id if position else None) != pending.position_id:
            return self.record(record_id, d, False, "position_changed",
                               "position changed while the model was deciding", price, now, snap)
        day = self.day(now, price, position)
        verdict = risk.validate(
            self.limits, d, self.context(now, price, pending.atr, position, day, pending.data_fresh), self.calendar
        )
        d = verdict.decision
        record = self.record(record_id, d, verdict.ok, verdict.code, verdict.reason, price, now, snap)
        if not verdict.ok or d.action == "HOLD":
            return record
        try:
            self._execute(d, record_id, price, now)
        except Exception as e:  # whatever failed, the record must not say it happened
            log.exception("order execution failed")
            self.notifier.send(f"ORDER ERROR on {d.action}: {e}", "error")
            record = self.record(record_id, d, False, "order_failed", f"{verdict.reason}; order failed: {e}"[:300],
                                 price, now, snap)
        self.publish_position()
        self.publish_limits(now)
        return record

    def _execute(self, d: Decision, record_id: int, price: float, now: datetime) -> None:
        if d.action in ENTRY_ACTIONS:
            contract = self.feed.contract(now)
            assert d.stop_loss is not None and d.target is not None
            p = self.broker.enter(EntryRequest(
                side="LONG" if d.action == "ENTER_LONG" else "SHORT", lots=d.lots, multiplier=contract.multiplier,
                ltp=price, stop=d.stop_loss, target=d.target, instrument_key=contract.instrument_key,
                decision_id=record_id, reasoning=d.reasoning, now=now,
            ))
            self._range_checked = None
            self.notifier.send(
                f"OPENED {p.side} {p.lots} lot @ {p.entry:.2f} SL {p.stop_loss} TP {p.target}", "trade"
            )
        elif d.action == "EXIT":
            trade = self.broker.exit(price, "agent", now)
            if trade:
                self.on_closed(trade)
        elif d.action == "TRAIL_STOP":
            assert d.stop_loss is not None
            self.broker.move_stop(d.stop_loss)
            self._stop_set_at = now
            self.notifier.send(f"STOP tightened to {d.stop_loss}", "trade")

    def cycle(self, now: datetime | None = None) -> DecisionRecord | None:
        """Synchronous decision cycle (replay and tests; requires InlineExecutor)."""
        pending = self.start_cycle(now)
        return self.finish_cycle(pending) if pending else None

    def record(
        self,
        record_id: int,
        d: Decision,
        approved: bool,
        code: str,
        verdict: str,
        price: float | None,
        now: datetime,
        snap: Mapping[str, Any] | None,
    ) -> DecisionRecord:
        orders = d.action in ENTRY_ACTIONS
        has_stop = orders or d.action == "TRAIL_STOP"
        record = DecisionRecord(
            id=record_id, ts=now.isoformat(timespec="seconds"), price=price, action=d.action,
            lots=d.lots if orders else None, stop_loss=d.stop_loss if has_stop else None,
            target=d.target if orders else None, confidence=None if code == "forced" else d.confidence,
            reasoning=d.reasoning[:1200], approved=approved, verdict_code=code, verdict=verdict[:400],
            mode=self.s.mode, snapshot=dict(snap) if snap is not None else None,
        )
        self.store.save_decision(record)
        return record

    # ------------------------------------------------------------------ publishing for the UI
    def heartbeat(self) -> None:
        self.store.publish("heartbeat", {
            "ts": self.wall().isoformat(timespec="seconds"),
            "engine_time": self.clock.now().isoformat(timespec="seconds"),
            "mode": self.s.mode, "instance": self.instance_id, "started_at": self.started_at, "version": __version__,
        })

    def publish_auth(self) -> None:
        status = self.auth.status() if self.auth else {"authenticated": True, "simulated": True}
        self.store.publish("upstox_auth", status, if_changed=True)

    def publish_position(self) -> None:
        p = self.broker.position()
        value: dict[str, Any] | None = None
        if p:
            value = {
                key: getattr(p, key)
                for key in ("id", "side", "lots", "multiplier", "entry", "stop_loss", "initial_stop", "target",
                            "opened_at", "reasoning", "mode", "decision_id", "flatten_reason")
            }
            value["qty"] = p.qty
            value["exchange_stop"] = p.sl_order_id is not None
        self.store.publish("position", value, if_changed=True)

    def publish_limits(self, now: datetime) -> None:
        position = self.broker.position()
        day = self.day(now, self._price, position)
        cap = self.limits.daily_loss_cap_inr
        self.store.publish("limits", {
            "effective": limits_mod.to_dict(self.limits),
            "ceilings": limits_mod.ceilings_dict(self.s.limits, self.s.ceilings),
            "usage": {
                "daily_pnl": round(day.total, 2), "realized": round(day.realized, 2),
                "unrealized": round(day.unrealized, 2), "loss_used": round(max(0.0, -day.total), 2),
                "trades_today": day.trades,
                "halted": self.halt_latched(now) or risk.daily_loss_breached(self.limits, day.total),
                "risk_budget": round(max(0.0, min(self.limits.max_risk_inr, cap + day.total)), 2),
                "date": now.astimezone(IST).date().isoformat(),
            },
        }, if_changed=True)

    def publish_session(self, now: datetime) -> None:
        local = now.astimezone(IST)
        position = self.broker.position()
        day = self.day(now, self._price, position)
        code: str | None = None
        reason: str | None = None
        if not self.authed():
            code, reason = "upstox_login", "Upstox login needed"
        elif self._contract:
            context = self.context(now, self._price or 1.0, None, None, day, self._data_fresh)
            blocked = risk.entry_block(self.limits, context, self.calendar)
            if blocked:
                code, reason = blocked
        else:
            code, reason = "starting", "waiting for market data"
        next_eia = self.calendar.next_release(now)
        key = f"{local.date()}|{self.limits.eia_before_min}|{self.limits.eia_after_min}"
        if self._windows[0] != key:
            self._windows = (key, sessions.chart_windows(
                self.calendar, self.limits, sessions.at(local.date() - timedelta(days=16), sessions.MARKET_OPEN),
                sessions.at(local.date() + timedelta(days=1), sessions.market_close(local.date())),
                self.s.no_trade_dates,
            ))
        contract = self._contract
        self.store.publish("session", {
            "symbol": self.s.symbol,
            "trading_symbol": contract.trading_symbol if contract else None,
            "expiry": contract.expiry if contract else None,
            "lot_size": contract.multiplier if contract else self.s.multiplier,
            "mode": self.s.mode,
            "model": self.s.model,
            "candle_min": self.s.candle_min,
            "market_open": sessions.market_open(now, self.s.no_trade_dates),
            "market_close": f"{sessions.market_close(local.date()):%H:%M}",
            "entries_allowed": code is None and position is None,
            "block_code": code or ("already_in_position" if position else None),
            "block_reason": reason or ("in a position" if position else None),
            "force_flat_at": sessions.at(local.date(), self.s.force_flat).isoformat(timespec="seconds"),
            "next_eia": next_eia.isoformat(timespec="seconds"),
            "eia_verified": self.calendar.is_verified(next_eia),
            "kill": self.kill,
            "orphan": self.broker.orphan(),
            "safety_halt": self.safety_halt,
            "windows": self._windows[1],
            "levels": self._levels,
        }, if_changed=True)

    def publish_market(self, now: datetime | None = None) -> None:
        """Candles for the chart and the levels the engine uses. Full refresh once a day, deltas after."""
        now = now or self.clock.now()
        self._contract = self.feed.contract(now)
        today = now.astimezone(IST).date().isoformat()
        full = self._full_publish_day != today
        bars = self.feed.candles(self.s.tf, CHART_BARS if full else DECISION_BARS, now)
        daily = self.feed.candles("1d", CHART_BARS_1D, now)
        if not bars:
            return
        self.store.put_candles(self.s.tf, bars if full else bars[-4:])
        self.store.put_candles("1d", daily if full else daily[-2:])
        if full:
            self.store.prune_candles(self.s.tf, bars[0].ts)
            if daily:
                self.store.prune_candles("1d", daily[0].ts)
            self._full_publish_day = today
        self._data_fresh = is_fresh(bars, now, self.s.candle_min)
        self._levels = snapshot.market_levels(daily, bars[-DECISION_BARS:], now)

    # ------------------------------------------------------------------ scheduler
    def startup(self) -> None:
        now = self.clock.now()
        if self.store.bus is not None:
            self._check_single_instance()
        recovered = self.store.hydrate()
        if recovered:
            log.info("recovered %d recent trades from the bus", recovered)
        self.heartbeat()
        self.store.publish("engine_error", None)
        self.publish_auth()
        if self.authed():
            self._guard("startup", lambda: self.broker.startup(now))
            self._guard("market_data", lambda: self.publish_market(now))
        self.publish_position()
        self.publish_limits(now)
        self.publish_session(now)
        about = f"{self.s.symbol}, {self.s.model}, {self.s.tf} candles"
        self.notifier.send(f"engine started in {self.s.mode.upper()} mode ({about})", "info")

    def _check_single_instance(self) -> None:
        """Two engines on one account would fight each other's orders."""
        assert self.store.bus is not None
        try:
            beat = self.store.bus.state_value("heartbeat")
        except Exception as e:
            log.warning("could not check for another engine: %s", e)
            return
        if not beat or beat.get("instance") == self.instance_id:
            return
        age = (self.wall() - datetime.fromisoformat(beat["ts"])).total_seconds()
        if age < 30:
            message = f"another engine ({beat.get('instance')}) was alive {age:.0f}s ago"
            if self.s.is_live:
                raise SystemExit(f"Refusing to start: {message}. Stop it first.")
            log.warning(message)

    def tick(self) -> None:
        """One scheduler iteration; called about once a second."""
        now = self.clock.now()
        if self._due_now("heartbeat", self.s.heartbeat_sec):
            self.heartbeat()
            self._guard("publish", lambda: self.publish_session(now))
        if self._due_now("commands", self.s.command_poll_sec):
            self._guard("commands", lambda: commands.process(self))
        if self._due_now("auth", 30):
            self.publish_auth()

        pending = self._pending
        if pending and pending.future.done():
            self._guard("decision", lambda: self.finish_cycle(pending))

        position = self.broker.position()
        if not self.authed():
            if position:
                self.alert_once("auth", "Position open but the Upstox login has expired. Log in now.")
            return
        market = sessions.market_open(now, self.s.no_trade_dates)
        if (market or position) and self._due_now("monitor", self.s.monitor_sec):
            self._guard("monitor", self.monitor)
        if not market:
            return
        step = self.s.candle_min
        slot = now.replace(minute=now.minute // step * step, second=0, microsecond=0)
        if slot != self._slot and (now - slot).total_seconds() >= self.s.decision_delay_sec:
            self._slot = slot
            self._guard("market_data", self.publish_market)
            if self._pending is None:
                self._guard("decision", self.start_cycle)

    def run_forever(self, stop: threading.Event) -> None:
        self.startup()
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:  # the loop itself must survive anything
                log.exception("tick failed")
                self.set_error("loop", f"{type(e).__name__}: {e}")
            self.clock.sleep(1)
        self.executor.shutdown(wait=False, cancel_futures=True)

"""Run the engine.

    python -m engine              the trading worker (paper unless the env says otherwise)
    python -m engine login        paste an Upstox authorisation code by hand (no web app needed)
    python -m engine doctor       read-only checks against real Upstox data; places no orders
    python -m engine claude       check the Claude set-up with ONE real request on made-up prices
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from datetime import date, timedelta
from typing import IO
from urllib.parse import urlencode

from dotenv import load_dotenv

from . import logs, snapshot
from .agent_base import Agent
from .brokers.base import Broker
from .brokers.live import LiveBroker
from .brokers.paper import PaperBroker
from .clock import SystemClock
from .config import ENGINE_DIR, Settings
from .engine import CHART_BARS_1D, DECISION_BARS, Engine
from .feed import SimFeed
from .sessions import EiaCalendar
from .store import LocalStore, PostgresBus, Store, Syncer
from .upstox import BASE, UpstoxClient, UpstoxError, UpstoxFeed

log = logging.getLogger("main")


def single_instance_lock(settings: Settings) -> IO[str]:
    """Two engines on one machine would trade the same account twice."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    handle = open(settings.data_dir / f"{settings.mode}.lock", "a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(f"another {settings.mode} engine is already running from {settings.data_dir}") from None
    return handle


def make_agent(settings: Settings) -> Agent:
    if settings.claude_auth == "subscription":
        from .agent_cli import ClaudeCliAgent

        return ClaudeCliAgent(settings)
    from .agent import ClaudeAgent  # imported here so `doctor` works without the SDK configured

    return ClaudeAgent(settings)


def check_subscription(settings: Settings) -> str:
    """Stop at start-up, with the reason, rather than HOLD on every candle. Spends nothing."""
    from .agent_cli import CliError, preflight

    try:
        found = preflight(settings)
    except CliError as e:
        raise SystemExit(f"Claude subscription: {e}") from e
    plan = f"{found.plan.title()} plan" if found.plan else "setup token"
    note = f"Claude: your subscription ({plan}), Claude Code {found.version}"
    if found.expires and found.expires - SystemClock().now() < timedelta(days=14):
        log.warning(
            "the Claude Code sign-in expires on %s. For unattended use run `claude setup-token` and put the "
            "token in CLAUDE_CODE_OAUTH_TOKEN in engine/.env", f"{found.expires:%d %b %Y}",
        )
    return note


def build(settings: Settings) -> tuple[Engine, Store]:
    clock = SystemClock()
    local = LocalStore(settings.data_dir / f"{settings.mode}.db")
    bus = PostgresBus(settings.database_url) if settings.has_database else None
    store = Store(local, bus, settings.mode, clock)
    client = UpstoxClient(settings, local, clock)
    feed = UpstoxFeed(client, settings.candle_min)
    engines: list[Engine] = []
    broker: Broker
    if settings.is_live:
        broker = LiveBroker(settings, store, client, clock,
                            lambda key, message: engines[0].alert_once(f"broker:{key}", message))
    else:
        broker = PaperBroker(settings, store)
    instance = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
    engine = Engine(settings, store, feed, broker, make_agent(settings), clock, auth=client, instance_id=instance)
    engines.append(engine)
    return engine, store


def run(settings: Settings) -> None:
    subscription = settings.claude_auth == "subscription"
    missing = [name for name, value in (
        ("ANTHROPIC_API_KEY", subscription or settings.anthropic_api_key),
        ("UPSTOX_CLIENT_ID", settings.upstox_client_id),
        ("UPSTOX_CLIENT_SECRET", settings.upstox_client_secret),
        ("UPSTOX_REDIRECT_URI", settings.upstox_redirect_uri),
    ) if not value]
    if missing:
        raise SystemExit(f"missing in engine/.env: {', '.join(missing)}")
    claude = check_subscription(settings) if subscription else "Claude: API key"
    lock = single_instance_lock(settings)
    engine, store = build(settings)
    if store.bus is None:
        log.warning("DATABASE_URL not set: no UI and no remote kill switch (paper mode only)")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    syncer = Syncer(store)
    syncer.start()
    log.info("starting in %s mode. %s", settings.mode.upper(), claude)
    try:
        engine.run_forever(stop)
    finally:
        position = engine.broker.position()
        if position:
            where = "its exchange stop stays in place" if settings.is_live else "it is simulated"
            engine.notifier.send(f"engine stopping with an open {position.side} position; {where}", "warn")
        else:
            engine.notifier.send("engine stopped", "info")
        syncer.stop()
        lock.close()


def login_url(settings: Settings) -> str:
    query = urlencode({"client_id": settings.upstox_client_id, "redirect_uri": settings.upstox_redirect_uri,
                       "response_type": "code"})
    return f"{BASE}/v2/login/authorization/dialog?{query}"


def login(settings: Settings) -> None:
    print("1. Open this URL and log in:\n  ", login_url(settings))
    print("2. After the redirect, copy the value of `code=` from the address bar.")
    code = input("code: ").strip()
    local = LocalStore(settings.data_dir / f"{settings.mode}.db")
    UpstoxClient(settings, local, SystemClock()).exchange_code(code)
    print("Logged in. The token is stored locally and expires around 03:30 IST.")


def doctor(settings: Settings) -> None:
    """Verify on day one: contract, lot size, candle timestamps, quotes, positions. Read-only."""
    clock = SystemClock()
    local = LocalStore(settings.data_dir / f"{settings.mode}.db")
    client = UpstoxClient(settings, local, clock)
    if not client.authenticated():
        raise SystemExit("not logged in: run `python -m engine login` (or log in from the web app) first")
    feed = UpstoxFeed(client, settings.candle_min)
    now = clock.now()
    try:
        contract = feed.contract(now)
        print(f"contract      {contract.trading_symbol}  key={contract.instrument_key}  expiry={contract.expiry}")
        print(f"multiplier    {contract.multiplier} mmBtu per lot (orders are sent in LOTS)")
        print(f"ltp           {feed.ltp(now)}")
        for tf, n in ((settings.tf, 5), ("1d", 3)):
            for c in feed.candles(tf, n, now):
                print(f"{tf:<4} candle   {c.ts.isoformat()}  o={c.o} h={c.h} l={c.l} c={c.c}")
        recent = feed.bar_range(now - timedelta(minutes=30), now)
        print(f"1m range      {recent or '(none: market closed?)'}")
        positions = [p for p in client.positions() if p.quantity]
        print(f"positions     {positions or 'flat'}")
    except UpstoxError as e:
        raise SystemExit(f"FAILED: {e}") from e
    print(f"\nCheck: {settings.tf} timestamps are bar OPEN times in IST, prices match your terminal, and the contract is right.")
    print("Not checked here (needs a real order): whether Upstox accepts MCX orders by API, and SL-M on this contract.")


def sample_snapshot(settings: Settings) -> dict[str, object]:
    """A realistic snapshot on synthetic prices, so the check needs neither Upstox nor an open market."""
    feed = SimFeed(
        seed=7, days=40, symbol=settings.symbol, multiplier=settings.multiplier, end_date=date(2026, 11, 24),
        bar_min=settings.candle_min,
    )
    now = feed.bars[-30].ts + feed.bar
    return snapshot.build(
        settings=settings, limits=settings.limits, calendar=EiaCalendar(settings.eia_overrides),
        contract=feed.contract(now), daily=feed.candles("1d", CHART_BARS_1D, now),
        bars=feed.candles(settings.tf, DECISION_BARS, now), now=now, ltp=feed.ltp(now), position=None,
        daily_pnl=0.0, trades_today=0, entry_block=None, last_decisions=[],
    )


def claude_check(settings: Settings) -> None:
    if settings.claude_auth == "subscription":
        print(check_subscription(settings))
    elif settings.anthropic_api_key:
        print("Claude: API key")
    else:
        raise SystemExit("set CLAUDE_AUTH=subscription, or ANTHROPIC_API_KEY, in engine/.env")
    print(f"asking {settings.model} for one decision on made-up prices...")
    started = time.monotonic()
    result = make_agent(settings).decide(sample_snapshot(settings))
    took = time.monotonic() - started
    if result.error:
        raise SystemExit(f"FAILED after {took:.0f}s: {result.error}")
    d = result.decision
    print(f"OK in {took:.0f}s: {d.action}, confidence {d.confidence:.2f}\n  {d.reasoning}")
    for name, share in result.meta.get("usage", {}).items():
        print(f"  {name.replace('_', ' ')} limit used: {share:.0%}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m engine", description=(__doc__ or "").splitlines()[0])
    ap.add_argument("command", nargs="?", choices=["run", "login", "doctor", "claude"], default="run")
    args = ap.parse_args()
    load_dotenv(os.environ.get("ENGINE_ENV_FILE") or ENGINE_DIR / ".env")
    try:
        settings = Settings.from_env()
    except ValueError as e:
        raise SystemExit(f"configuration error: {e}") from e
    logs.setup(settings.log_level)
    {"run": run, "login": login, "doctor": doctor, "claude": claude_check}[args.command](settings)


if __name__ == "__main__":
    main()

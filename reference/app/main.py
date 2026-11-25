"""FastAPI app: dashboard + control API + Upstox OAuth callback. The engine runs in a background thread.

    uvicorn app.main:app --host 127.0.0.1 --port 8001
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from .agent import ClaudeAgent
from .broker import LiveBroker, PaperBroker
from .config import Settings
from .engine import Engine
from .feed import UpstoxFeed
from .notify import Notifier
from .store import Store
from .upstox import UpstoxClient, UpstoxError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

settings = Settings()
settings.validate()
if not settings.anthropic_api_key:
    raise SystemExit("ANTHROPIC_API_KEY is not set (see .env.example)")

store = Store(settings.db_path)
client = UpstoxClient(settings, store)
feed = UpstoxFeed(client)
broker = LiveBroker(settings, store, client) if settings.is_live else PaperBroker(settings, store, "PAPER")
notifier = Notifier(settings, store)
engine = Engine(settings, store, feed, broker, ClaudeAgent(settings), notifier)


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=engine.run_forever, kwargs={"client": client}, daemon=True, name="engine")
    t.start()
    yield
    engine.stop()


app = FastAPI(title="NatGas Trader", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"


def admin(x_admin_token: str = Header(default="")) -> None:
    if settings.admin_token and x_admin_token != settings.admin_token:
        raise HTTPException(401, "bad admin token")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {**engine.status(), "upstox_authed": client.authed(), "admin_required": bool(settings.admin_token)}


@app.get("/api/decisions")
def decisions(limit: int = 40):
    return store.decisions(min(limit, 500))


@app.get("/api/trades")
def trades(limit: int = 50):
    return store.trades(min(limit, 500))


@app.get("/api/events")
def events(limit: int = 30):
    return store.events(min(limit, 200))


@app.post("/api/kill", dependencies=[Depends(admin)])
def kill():
    """Blocks all new entries and flattens any open position on the next monitor tick."""
    store.set("kill", True)
    notifier.send("KILL SWITCH ON", "warn")
    return {"kill": True}


@app.post("/api/resume", dependencies=[Depends(admin)])
def resume():
    store.set("kill", False)
    notifier.send("kill switch released", "info")
    return {"kill": False}


@app.get("/auth/login")
def login():
    if not settings.upstox_client_id:
        raise HTTPException(500, "UPSTOX_CLIENT_ID not set")
    return RedirectResponse(client.login_url())


@app.get("/auth/callback")
def callback(code: str):
    try:
        client.exchange_code(code)
    except UpstoxError as e:
        raise HTTPException(400, str(e))
    notifier.send("Upstox login OK", "info")
    return RedirectResponse("/")

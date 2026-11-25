"""Thin Upstox REST client: OAuth, instrument lookup, candles, LTP, orders, positions.

Endpoints (verified against Upstox docs):
  login dialog  GET  https://api.upstox.com/v2/login/authorization/dialog
  token         POST https://api.upstox.com/v2/login/authorization/token
  candles v3    GET  /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}
  intraday v3   GET  /v3/historical-candle/intraday/{key}/{unit}/{interval}
  ltp           GET  /v2/market-quote/ltp?instrument_key=
  orders        POST https://api-hft.upstox.com/v2/order/place  (DELETE .../cancel)
  positions     GET  /v2/portfolio/short-term-positions
"""
from __future__ import annotations

import gzip
import json
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx

from .config import IST, Settings
from .models import Candle, Contract
from .store import Store

BASE = "https://api.upstox.com"
HFT = "https://api-hft.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz"


class UpstoxError(RuntimeError):
    pass


class UpstoxClient:
    def __init__(self, s: Settings, store: Store):
        self.s = s
        self.store = store
        self.http = httpx.Client(timeout=20)
        self._contract: Optional[Contract] = None

    # ------------------------------------------------------------------ auth
    def login_url(self) -> str:
        q = urlencode({
            "client_id": self.s.upstox_client_id,
            "redirect_uri": self.s.upstox_redirect_uri,
            "response_type": "code",
        })
        return f"{BASE}/v2/login/authorization/dialog?{q}"

    def exchange_code(self, code: str) -> None:
        r = self.http.post(
            f"{BASE}/v2/login/authorization/token",
            data={
                "code": code,
                "client_id": self.s.upstox_client_id,
                "client_secret": self.s.upstox_client_secret,
                "redirect_uri": self.s.upstox_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        if r.status_code != 200:
            raise UpstoxError(f"token exchange failed: {r.status_code} {r.text[:200]}")
        self.store.set("upstox_token", {"token": r.json()["access_token"], "at": datetime.now(IST).isoformat()})

    def token(self) -> Optional[str]:
        """Tokens expire daily at ~03:30 IST, so one issued before the latest 03:30 is dead."""
        t = self.store.get("upstox_token")
        if not t:
            return None
        issued = datetime.fromisoformat(t["at"])
        now = datetime.now(IST)
        cutoff = now.replace(hour=3, minute=30, second=0, microsecond=0)
        if now < cutoff:
            cutoff -= timedelta(days=1)
        return t["token"] if issued >= cutoff else None

    def authed(self) -> bool:
        return self.token() is not None

    # ------------------------------------------------------------------ http
    def _req(self, method: str, url: str, *, params=None, json_body=None, retries: int = 3) -> Any:
        tok = self.token()
        if not tok:
            raise UpstoxError("not logged in to Upstox (open /auth/login)")
        headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        last: Exception | None = None
        for i in range(retries):
            try:
                r = self.http.request(method, url, params=params, json=json_body, headers=headers)
                if r.status_code == 429 or r.status_code >= 500:
                    raise UpstoxError(f"{r.status_code} {r.text[:150]}")
                body = r.json()
                if r.status_code >= 400 or body.get("status") == "error":
                    raise UpstoxError(f"{r.status_code} {json.dumps(body)[:300]}")
                return body
            except (httpx.HTTPError, UpstoxError) as e:
                last = e
                if isinstance(e, UpstoxError) and not str(e).startswith(("429", "5")):
                    break  # client error: do not retry
                time.sleep(0.6 * (i + 1))
        raise UpstoxError(f"{method} {url}: {last}")

    # ------------------------------------------------------------ instruments
    def contract(self, today: Optional[date] = None) -> Contract:
        """Nearest-expiry future for SYMBOL, rolling ROLL_DAYS before expiry."""
        today = today or datetime.now(IST).date()
        if self._contract and date.fromisoformat(self._contract.expiry) - timedelta(days=self.s.roll_days) > today:
            return self._contract
        r = self.http.get(INSTRUMENTS_URL)
        r.raise_for_status()
        items = json.loads(gzip.decompress(r.content))
        sym = self.s.symbol.upper()
        cands = []
        for it in items:
            if it.get("instrument_type") != "FUT":
                continue
            ts = str(it.get("trading_symbol", "")).upper()
            us = str(it.get("underlying_symbol", "")).upper()
            if us != sym and ts.split(" ")[0] != sym:
                continue
            exp = datetime.fromtimestamp(it["expiry"] / 1000, IST).date()
            if exp - timedelta(days=self.s.roll_days) > today:
                cands.append((exp, it))
        if not cands:
            raise UpstoxError(f"no live {sym} future found in MCX instruments")
        exp, it = min(cands, key=lambda x: x[0])
        self._contract = Contract(it["instrument_key"], it["trading_symbol"], int(it["lot_size"]), exp.isoformat())
        return self._contract

    # ---------------------------------------------------------------- candles
    @staticmethod
    def _parse(rows: list) -> list[Candle]:
        out = [Candle(ts=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=r[5] if len(r) > 5 else 0) for r in rows]
        return sorted(out, key=lambda c: c.ts)

    def candles(self, unit: str, interval: int, days_back: int) -> list[Candle]:
        key = quote(self.contract().instrument_key, safe="")
        to = datetime.now(IST).date()
        frm = to - timedelta(days=days_back)
        hist = self._req("GET", f"{BASE}/v3/historical-candle/{key}/{unit}/{interval}/{to}/{frm}")
        rows = hist["data"]["candles"]
        try:
            intra = self._req("GET", f"{BASE}/v3/historical-candle/intraday/{key}/{unit}/{interval}")
            rows = rows + intra["data"]["candles"]
        except UpstoxError:
            pass  # intraday can be empty outside session
        seen, merged = set(), []
        for c in self._parse(rows):
            if c.ts not in seen:
                seen.add(c.ts)
                merged.append(c)
        return merged

    def ltp(self) -> float:
        key = self.contract().instrument_key
        body = self._req("GET", f"{BASE}/v2/market-quote/ltp", params={"instrument_key": key})
        data = body["data"]
        return float(next(iter(data.values()))["last_price"])

    # ----------------------------------------------------------------- orders
    def place_order(self, side: str, qty: int, order_type: str = "MARKET",
                    trigger: float = 0.0, price: float = 0.0, tag: str = "natgas") -> str:
        body = {
            "quantity": qty,
            "product": self.s.product,
            "validity": "DAY",
            "price": price,
            "tag": tag,
            "instrument_token": self.contract().instrument_key,
            "order_type": order_type,
            "transaction_type": side,
            "disclosed_quantity": 0,
            "trigger_price": trigger,
            "is_amo": False,
        }
        res = self._req("POST", f"{HFT}/v2/order/place", json_body=body, retries=1)
        return res["data"]["order_id"]

    def cancel_order(self, order_id: str) -> None:
        self._req("DELETE", f"{HFT}/v2/order/cancel", params={"order_id": order_id}, retries=1)

    def order_details(self, order_id: str) -> dict:
        d = self._req("GET", f"{BASE}/v2/order/details", params={"order_id": order_id})["data"]
        return d[-1] if isinstance(d, list) else d

    def net_qty(self) -> int:
        """Signed net quantity currently open in our contract."""
        key = self.contract().instrument_key
        body = self._req("GET", f"{BASE}/v2/portfolio/short-term-positions")
        return sum(int(p.get("quantity", 0)) for p in body.get("data", []) if p.get("instrument_token") == key)

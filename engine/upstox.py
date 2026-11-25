"""Upstox REST client and feed.

Order errors are split in two because the live broker treats them very differently:
  OrderRejected  - the request definitely did nothing (4xx, error body, never sent)
  OrderAmbiguous - it may or may not have happened (timeout, 5xx, garbled reply)

MCX specifics (see README): order `quantity` is the number of LOTS, and at the time of writing Upstox
rejects MCX orders placed through the API (UDAPI1161). Market data is unaffected.
"""
from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .clock import Clock
from .config import IST, MULTIPLIERS, TICK_SIZE, Settings
from .feed import aggregate_daily
from .models import Candle, Contract

log = logging.getLogger("upstox")

BASE = "https://api.upstox.com"
HFT = "https://api-hft.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz"
TOKEN_EXPIRY = time(3, 30)  # IST, daily
MCX_BLOCK_CODES = ("UDAPI1161", "UDAPI1178")


class UpstoxError(RuntimeError):
    pass


class NotAuthenticated(UpstoxError):
    pass


class OrderRejected(UpstoxError):
    pass


class MCXApiBlocked(OrderRejected):
    pass


class OrderAmbiguous(UpstoxError):
    pass


@dataclass(frozen=True)
class OrderInfo:
    order_id: str
    status: str  # lower-case Upstox status, e.g. "complete", "trigger pending", "rejected"
    average_price: float = 0.0
    filled_quantity: int = 0
    quantity: int = 0
    tag: str = ""
    message: str = ""


@dataclass(frozen=True)
class BrokerPosition:
    instrument_key: str
    trading_symbol: str
    exchange: str
    quantity: int  # signed net


class KeyValue(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


def _order_info(d: Mapping[str, Any]) -> OrderInfo:
    return OrderInfo(
        order_id=str(d.get("order_id") or ""),
        status=str(d.get("status") or "").lower(),
        average_price=float(d.get("average_price") or 0),
        filled_quantity=int(d.get("filled_quantity") or 0),
        quantity=int(d.get("quantity") or 0),
        tag=str(d.get("tag") or ""),
        message=str(d.get("status_message") or "")[:200],
    )


def latest_expiry_cutoff(now: datetime) -> datetime:
    """Tokens die at ~03:30 IST; anything issued before the latest 03:30 is dead."""
    local = now.astimezone(IST)
    cutoff = datetime.combine(local.date(), TOKEN_EXPIRY, tzinfo=IST)
    return cutoff if local >= cutoff else cutoff - timedelta(days=1)


def select_contract(items: list[dict[str, Any]], symbol: str, today: date, roll_days: int) -> Contract:
    """Nearest live future for `symbol`, rolling `roll_days` before expiry."""
    multiplier = MULTIPLIERS[symbol]
    candidates: list[tuple[date, dict[str, Any]]] = []
    for item in items:
        if item.get("instrument_type") != "FUT":
            continue
        trading = str(item.get("trading_symbol", "")).upper()
        underlying = str(item.get("underlying_symbol", "")).upper()
        if underlying != symbol and trading.split(" ")[0] != symbol:
            continue
        expiry = datetime.fromtimestamp(item["expiry"] / 1000, IST).date()
        if expiry - timedelta(days=roll_days) > today:
            candidates.append((expiry, item))
    if not candidates:
        raise UpstoxError(f"no live {symbol} future found in the MCX instrument list")
    expiry, item = min(candidates, key=lambda c: c[0])
    listed = item.get("lot_size")
    if listed is not None and int(listed) not in (1, multiplier):
        # All risk maths depends on the contract size, so a surprise here must stop trading.
        raise UpstoxError(f"{symbol}: Upstox lists lot_size {listed}, expected {multiplier}. Verify before trading.")
    return Contract(str(item["instrument_key"]), str(item["trading_symbol"]), expiry.isoformat(), multiplier, TICK_SIZE)


class UpstoxClient:
    """Also the engine's auth provider. The access token is stored locally and never leaves the engine."""

    def __init__(
        self,
        settings: Settings,
        local: KeyValue,
        clock: Clock,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.s, self.local, self.clock = settings, local, clock
        self.http = http or httpx.Client(timeout=15.0)
        self.sleep = sleep or clock.sleep
        self._instruments: tuple[date, list[dict[str, Any]]] | None = None

    # ------------------------------------------------------------------ auth
    def exchange_code(self, code: str) -> None:
        try:
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
            token = r.json().get("access_token") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as e:
            self.local.set("upstox_error", "token exchange failed: network")
            raise UpstoxError(f"token exchange failed: {type(e).__name__}") from e
        if not token:
            message = f"token exchange failed: HTTP {r.status_code} {_error_text(r)}"
            self.local.set("upstox_error", message)
            raise UpstoxError(message)
        self.local.set("upstox_token", {"token": token, "at": self.clock.now().isoformat()})
        self.local.set("upstox_error", None)

    def token(self) -> str | None:
        data = self.local.get("upstox_token")
        if not data:
            return None
        issued = datetime.fromisoformat(data["at"])
        return str(data["token"]) if issued >= latest_expiry_cutoff(self.clock.now()) else None

    def authenticated(self) -> bool:
        return self.token() is not None

    def status(self) -> dict[str, Any]:
        data = self.local.get("upstox_token")
        authed = self.authenticated()
        expires = latest_expiry_cutoff(self.clock.now()) + timedelta(days=1) if authed else None
        return {
            "authenticated": authed,
            "issued_at": data["at"] if data and authed else None,
            "expires_at": expires.isoformat() if expires else None,
            "error": self.local.get("upstox_error"),
        }

    # ------------------------------------------------------------------ http
    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        """GETs retry with backoff on 429/5xx. Order writes are never retried."""
        token = self.token()
        if not token:
            raise NotAuthenticated("not logged in to Upstox")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        write = method != "GET"
        attempts = 1 if write else 4
        path = url.split(".com", 1)[-1]
        for attempt in range(attempts):
            try:
                r = self.http.request(method, url, params=params, json=body, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:  # never reached Upstox
                raise OrderRejected(f"{method} {path}: could not connect") from e
            except httpx.HTTPError as e:
                raise (OrderAmbiguous if write else UpstoxError)(f"{method} {path}: {type(e).__name__}") from e
            if (r.status_code == 429 or r.status_code >= 500) and attempt + 1 < attempts:
                self.sleep(0.6 * 2**attempt)
                continue
            break
        if r.status_code == 401:
            self.local.set("upstox_token", None)
            self.local.set("upstox_error", "Upstox rejected the access token; log in again")
            raise NotAuthenticated("Upstox rejected the access token")
        if r.status_code >= 500:
            raise (OrderAmbiguous if write else UpstoxError)(f"{method} {path}: HTTP {r.status_code}")
        try:
            payload = r.json()
        except ValueError as e:
            kind = OrderRejected if r.status_code >= 400 else (OrderAmbiguous if write else UpstoxError)
            raise kind(f"{method} {path}: HTTP {r.status_code}, unreadable reply") from e
        if r.status_code >= 400 or payload.get("status") == "error":
            message = f"{method} {path}: HTTP {r.status_code} {_error_text(r)}"
            if any(code in r.text for code in MCX_BLOCK_CODES):
                raise MCXApiBlocked(message)
            raise OrderRejected(message)
        return payload

    # ------------------------------------------------------------ instruments
    def contract(self, today: date) -> Contract:
        if self._instruments is None or self._instruments[0] != today:
            try:
                r = self.http.get(INSTRUMENTS_URL, timeout=30.0)
                r.raise_for_status()
                items = json.loads(gzip.decompress(r.content))
            except (httpx.HTTPError, OSError, ValueError) as e:
                if self._instruments is None:
                    raise UpstoxError(f"could not load the MCX instrument list: {type(e).__name__}") from e
                log.warning("instrument refresh failed, using %s", self._instruments[0])
                items = self._instruments[1]
            self._instruments = (today, items)
        return select_contract(self._instruments[1], self.s.symbol, today, self.s.roll_days)

    # ---------------------------------------------------------------- market data
    @staticmethod
    def _candles(payload: Mapping[str, Any]) -> list[Candle]:
        out = [
            Candle(
                datetime.fromisoformat(r[0]).astimezone(IST), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                float(r[5]) if len(r) > 5 else 0.0,
            )
            for r in payload["data"]["candles"]
        ]
        return sorted(out, key=lambda c: c.ts)  # Upstox sends newest first

    def historical(self, key: str, unit: str, interval: int, start: date, end: date) -> list[Candle]:
        url = f"{BASE}/v3/historical-candle/{quote(key, safe='')}/{unit}/{interval}/{end}/{start}"
        return self._candles(self._request("GET", url))

    def intraday(self, key: str, unit: str, interval: int) -> list[Candle]:
        url = f"{BASE}/v3/historical-candle/intraday/{quote(key, safe='')}/{unit}/{interval}"
        return self._candles(self._request("GET", url))

    def ltp(self, key: str) -> float:
        data = self._request("GET", f"{BASE}/v2/market-quote/ltp", params={"instrument_key": key})["data"]
        if not data:
            raise UpstoxError("no quote returned")
        return float(next(iter(data.values()))["last_price"])

    # ----------------------------------------------------------------- orders
    def place_order(
        self,
        instrument_key: str,
        side: str,
        lots: int,
        order_type: str,
        tag: str,
        price: float = 0.0,
        trigger_price: float = 0.0,
    ) -> str:
        body: dict[str, Any] = {
            "quantity": lots,  # MCX: number of lots, NOT mmBtu
            "product": self.s.product,
            "validity": "DAY",
            "price": price,
            "tag": tag,
            "instrument_token": instrument_key,
            "order_type": order_type,
            "transaction_type": side,
            "disclosed_quantity": 0,
            "trigger_price": trigger_price,
            "is_amo": False,
        }
        if self.s.market_protection is not None and order_type in ("MARKET", "SL-M"):
            body["market_protection"] = self.s.market_protection
        payload = self._request("POST", f"{HFT}/v2/order/place", body=body)
        order_id = (payload.get("data") or {}).get("order_id")
        if not order_id:
            raise OrderAmbiguous("order accepted without an order_id")
        return str(order_id)

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"{HFT}/v2/order/cancel", params={"order_id": order_id})

    def order_details(self, order_id: str) -> OrderInfo:
        data = self._request("GET", f"{BASE}/v2/order/details", params={"order_id": order_id})["data"]
        return _order_info(data[-1] if isinstance(data, list) else data)

    def order_book(self) -> list[OrderInfo]:
        """Used only to find an order by tag after an ambiguous placement, and to clear stray stops."""
        data = self._request("GET", f"{BASE}/v2/order/retrieve-all").get("data") or []
        return [_order_info(d) for d in data]

    def positions(self) -> list[BrokerPosition]:
        data = self._request("GET", f"{BASE}/v2/portfolio/short-term-positions").get("data") or []
        return [
            BrokerPosition(
                instrument_key=str(p.get("instrument_token") or ""),
                trading_symbol=str(p.get("trading_symbol") or p.get("tradingsymbol") or ""),
                exchange=str(p.get("exchange") or ""),
                quantity=int(p.get("quantity") or 0),
            )
            for p in data
        ]


def _error_text(r: httpx.Response) -> str:
    """Upstox error code and message, without echoing request data."""
    try:
        errors = r.json().get("errors") or []
        return "; ".join(f"{e.get('errorCode') or e.get('error_code')}: {e.get('message')}" for e in errors)[:250]
    except (ValueError, AttributeError):
        return r.text[:150]


class UpstoxFeed:
    def __init__(self, client: UpstoxClient, candle_min: int = 15):
        self.client = client
        self.minutes = candle_min
        self.tf = f"{candle_min}m"
        self._cache: dict[tuple[str, str], tuple[datetime, list[Candle]]] = {}

    def contract(self, now: datetime) -> Contract:
        return self.client.contract(now.astimezone(IST).date())

    def ltp(self, now: datetime) -> float:
        return self.client.ltp(self.contract(now).instrument_key)

    def _closed(self, key: str, now: datetime) -> list[Candle]:
        """Closed intraday bars (weeks of 15m, about a week of 5m); refetched when a new bar should exist."""
        width = timedelta(minutes=self.minutes)
        slot = now.replace(minute=now.minute // self.minutes * self.minutes, second=0, microsecond=0)
        cached = self._cache.get((key, self.tf))
        if cached:
            fetched, bars = cached
            has_latest = bool(bars) and bars[-1].ts + width >= slot
            if (has_latest and fetched >= slot) or now - fetched < timedelta(seconds=30):
                return bars
        today = now.astimezone(IST).date()
        days = 22 if self.minutes >= 15 else 8
        history = self.client.historical(key, "minutes", self.minutes, today - timedelta(days=days), today)
        merged = {c.ts: c for c in history}
        merged.update({c.ts: c for c in self.client.intraday(key, "minutes", self.minutes)})
        bars = [c for ts, c in sorted(merged.items()) if ts + width <= now]  # drop forming bar
        self._cache[(key, self.tf)] = (now, bars)
        return bars

    def _past_days(self, key: str, today: date) -> list[Candle]:
        cached = self._cache.get((key, "1d"))
        if cached and cached[0].date() == today:
            return cached[1]
        bars = self.client.historical(key, "days", 1, today - timedelta(days=150), today)
        bars = [c for c in bars if c.ts.date() < today]
        self._cache[(key, "1d")] = (datetime.combine(today, time(), tzinfo=IST), bars)
        return bars

    def candles(self, tf: str, n: int, now: datetime) -> list[Candle]:
        key = self.contract(now).instrument_key
        if tf == self.tf:
            return self._closed(key, now)[-n:]
        if tf != "1d":
            raise ValueError(f"this feed has {self.tf} and 1d bars, not {tf}")
        today = now.astimezone(IST).date()
        partial = aggregate_daily([c for c in self._closed(key, now) if c.ts.date() == today])
        return (self._past_days(key, today) + partial)[-n:]

    def forming(self, now: datetime) -> Candle | None:
        """The intraday endpoint includes the candle still being made."""
        key = self.contract(now).instrument_key
        width = timedelta(minutes=self.minutes)
        current = [c for c in self.client.intraday(key, "minutes", self.minutes) if c.ts <= now < c.ts + width]
        return max(current, key=lambda c: c.ts, default=None)

    def bar_range(self, since: datetime, now: datetime) -> tuple[float, float] | None:
        key = self.contract(now).instrument_key
        bars = [
            c for c in self.client.intraday(key, "minutes", 1)
            if c.ts >= since and c.ts + timedelta(minutes=1) <= now
        ]
        return (max(c.h for c in bars), min(c.l for c in bars)) if bars else None

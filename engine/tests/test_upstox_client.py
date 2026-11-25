"""Upstox client: exact endpoints and bodies, retry rules, error classification. No network."""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest

from engine.clock import ManualClock
from engine.config import IST, Settings
from engine.store import LocalStore
from engine.upstox import (
    MCXApiBlocked,
    NotAuthenticated,
    OrderAmbiguous,
    OrderRejected,
    UpstoxClient,
    UpstoxError,
    UpstoxFeed,
    select_contract,
)

from .conftest import TUE

KEY = "MCX_FO|999"


def ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 23, 30, tzinfo=IST).timestamp() * 1000)


def instrument(expiry: date, symbol: str = "NATGASMINI", lot: int = 250, key: str = KEY, kind: str = "FUT") -> dict[str, Any]:
    return {"instrument_type": kind, "underlying_symbol": symbol, "trading_symbol": f"{symbol} FUT {expiry:%d %b %y}",
            "expiry": ms(expiry), "lot_size": lot, "instrument_key": key}


ITEMS = [
    instrument(date(2026, 11, 25), key="MCX_FO|1"),
    instrument(date(2026, 12, 28), key="MCX_FO|2"),
    instrument(date(2027, 1, 26), key="MCX_FO|3"),
    instrument(date(2026, 12, 28), symbol="NATURALGAS", lot=1250, key="MCX_FO|4"),
    instrument(date(2026, 12, 28), key="MCX_FO|5", kind="CE"),
]


def test_contract_is_nearest_future_with_roll() -> None:
    assert select_contract(ITEMS, "NATGASMINI", date(2026, 11, 20), 3).instrument_key == "MCX_FO|1"
    # 3 days before the 25 Nov expiry we are already on December
    assert select_contract(ITEMS, "NATGASMINI", date(2026, 11, 22), 3).instrument_key == "MCX_FO|2"
    c = select_contract(ITEMS, "NATURALGAS", date(2026, 11, 22), 3)
    assert c.instrument_key == "MCX_FO|4" and c.multiplier == 1250


def test_contract_matches_trading_symbol_when_underlying_missing() -> None:
    item = instrument(date(2026, 12, 28))
    del item["underlying_symbol"]
    assert select_contract([item], "NATGASMINI", date(2026, 11, 1), 3).multiplier == 250


def test_unexpected_lot_size_stops_trading() -> None:
    with pytest.raises(UpstoxError, match="lot_size"):
        select_contract([instrument(date(2026, 12, 28), lot=100)], "NATGASMINI", date(2026, 11, 1), 3)
    assert select_contract([instrument(date(2026, 12, 28), lot=1)], "NATGASMINI", date(2026, 11, 1), 3).multiplier == 250
    with pytest.raises(UpstoxError, match="no live"):
        select_contract(ITEMS, "NATGASMINI", date(2027, 6, 1), 3)


class Api:
    def __init__(self, *replies: Any, login: bool = True, settings: Settings | None = None):
        self.replies = list(replies)
        self.seen: list[httpx.Request] = []
        self.slept: list[float] = []
        self.local = LocalStore()
        self.clock = ManualClock(TUE)
        self.client = UpstoxClient(
            settings or Settings(upstox_client_id="cid", upstox_client_secret="sec", upstox_redirect_uri="https://x/cb"),
            self.local, self.clock, httpx.Client(transport=httpx.MockTransport(self.handle)), self.slept.append,
        )
        if login:
            self.local.set("upstox_token", {"token": "tok", "at": TUE.isoformat()})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, int):
            return httpx.Response(reply, json={"status": "error", "errors": [{"errorCode": "X", "message": "m"}]})
        if isinstance(reply, bytes):
            return httpx.Response(200, content=reply)
        return httpx.Response(200, json=reply)


def ok(data: Any) -> dict[str, Any]:
    return {"status": "success", "data": data}


def test_token_exchange_form_and_daily_expiry() -> None:
    api = Api({"access_token": "abc"}, login=False)
    api.client.exchange_code("the-code")
    r = api.seen[0]
    assert str(r.url) == "https://api.upstox.com/v2/login/authorization/token"
    form = dict(x.split("=", 1) for x in r.content.decode().split("&"))
    assert form["code"] == "the-code" and form["grant_type"] == "authorization_code"
    assert form["client_secret"] == "sec" and form["redirect_uri"].startswith("https")
    assert api.client.token() == "abc"
    assert api.client.status()["expires_at"].startswith("2026-11-25T03:30")
    api.clock.set(datetime(2026, 11, 25, 3, 29, tzinfo=IST))
    assert api.client.authenticated()
    api.clock.set(datetime(2026, 11, 25, 3, 31, tzinfo=IST))
    assert not api.client.authenticated()


def test_failed_token_exchange_is_reported() -> None:
    api = Api(401, login=False)
    with pytest.raises(UpstoxError, match="token exchange failed"):
        api.client.exchange_code("bad")
    assert "token exchange failed" in api.client.status()["error"]


def test_calls_without_login_fail_without_touching_the_network() -> None:
    api = Api(login=False)
    with pytest.raises(NotAuthenticated):
        api.client.ltp(KEY)
    assert api.seen == []


def test_place_order_body_is_exactly_as_documented_and_quantity_is_lots() -> None:
    api = Api(ok({"order_id": "o1"}))
    assert api.client.place_order(KEY, "SELL", 1, "SL", "ng-s-1", price=297.0, trigger_price=298.0) == "o1"
    r = api.seen[0]
    assert r.method == "POST" and str(r.url) == "https://api-hft.upstox.com/v2/order/place"
    assert r.headers["Authorization"] == "Bearer tok"
    assert json.loads(r.content) == {
        "quantity": 1, "product": "I", "validity": "DAY", "price": 297.0, "tag": "ng-s-1", "instrument_token": KEY,
        "order_type": "SL", "transaction_type": "SELL", "disclosed_quantity": 0, "trigger_price": 298.0, "is_amo": False,
    }


def test_market_protection_is_only_sent_when_configured() -> None:
    api = Api(ok({"order_id": "o1"}), settings=Settings(market_protection=5))
    api.client.place_order(KEY, "BUY", 1, "MARKET", "t")
    assert json.loads(api.seen[0].content)["market_protection"] == 5


@pytest.mark.parametrize(
    "reply,error",
    [
        (500, OrderAmbiguous),
        (503, OrderAmbiguous),
        (httpx.ReadTimeout("slow"), OrderAmbiguous),
        (httpx.ConnectError("down"), OrderRejected),
        (400, OrderRejected),
        (429, OrderRejected),
        (b"<html>oops</html>", OrderAmbiguous),
        (ok({}), OrderAmbiguous),
    ],
)
def test_order_writes_are_never_retried_and_are_classified(reply: Any, error: type[Exception]) -> None:
    api = Api(reply)
    with pytest.raises(error):
        api.client.place_order(KEY, "BUY", 1, "MARKET", "t")
    assert len(api.seen) == 1


def test_mcx_api_block_is_recognised() -> None:
    body = {"status": "error", "errors": [{"errorCode": "UDAPI1161", "message": "MCX orders via API are temporarily disabled"}]}
    api = Api()
    api.client.http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, json=body)))
    with pytest.raises(MCXApiBlocked, match="UDAPI1161"):
        api.client.place_order(KEY, "BUY", 1, "MARKET", "t")


def test_reads_back_off_and_retry_on_429_and_5xx_only() -> None:
    api = Api(429, 502, ok({"MCX_FO:X": {"last_price": 301.2}}))
    assert api.client.ltp(KEY) == 301.2
    assert len(api.seen) == 3 and api.slept == [0.6, 1.2]
    assert api.seen[0].url.params["instrument_key"] == KEY
    api = Api(400)
    with pytest.raises(UpstoxError):
        api.client.ltp(KEY)
    assert len(api.seen) == 1


def test_401_logs_us_out() -> None:
    api = Api(401)
    with pytest.raises(NotAuthenticated):
        api.client.ltp(KEY)
    assert not api.client.authenticated() and "log in again" in api.client.status()["error"]


def test_other_endpoints() -> None:
    order = {"order_id": "o1", "status": "Trigger Pending", "average_price": 0, "filled_quantity": 0, "quantity": 1,
             "tag": "ng-s-1"}
    api = Api(ok({}), ok(order), ok([order]),
              ok([{"instrument_token": KEY, "trading_symbol": "NATGASMINI25DECFUT", "exchange": "MCX", "quantity": -1}]))
    api.client.cancel_order("o1")
    assert api.client.order_details("o1").status == "trigger pending"
    assert api.client.order_book()[0].tag == "ng-s-1"
    assert api.client.positions()[0].quantity == -1
    urls = [(r.method, str(r.url)) for r in api.seen]
    assert urls == [
        ("DELETE", "https://api-hft.upstox.com/v2/order/cancel?order_id=o1"),
        ("GET", "https://api.upstox.com/v2/order/details?order_id=o1"),
        ("GET", "https://api.upstox.com/v2/order/retrieve-all"),
        ("GET", "https://api.upstox.com/v2/portfolio/short-term-positions"),
    ]


def bar(ts: datetime, close: float) -> list[Any]:
    return [ts.isoformat(), close, close + 1, close - 1, close, 10, 100]


def test_feed_builds_candles_drops_forming_bar_and_encodes_key() -> None:
    now = datetime(2026, 11, 24, 9, 40, tzinfo=IST)
    monday = datetime(2026, 11, 23, 9, 0, tzinfo=IST)
    today = datetime(2026, 11, 24, 9, 0, tzinfo=IST)
    instruments = gzip.compress(json.dumps(ITEMS).encode())
    history = ok({"candles": [bar(monday + timedelta(minutes=15), 299), bar(monday, 298)]})  # newest first
    intraday = ok({"candles": [bar(today + timedelta(minutes=30), 303), bar(today + timedelta(minutes=15), 302),
                               bar(today, 301)]})
    days = ok({"candles": [bar(datetime(2026, 11, 23, tzinfo=IST), 299)]})
    api = Api(instruments, history, intraday, days)
    feed = UpstoxFeed(api.client)
    m15 = feed.candles("15m", 100, now)
    assert [c.c for c in m15] == [298, 299, 301, 302]  # the 09:30 bar is still forming
    daily = feed.candles("1d", 10, now)
    assert [(c.ts.day, c.o, c.c) for c in daily] == [(23, 299, 299), (24, 301, 302)]
    paths = [r.url.raw_path.decode() for r in api.seen]
    assert paths[1].startswith("/v3/historical-candle/MCX_FO%7C2/minutes/15/2026-11-24/")
    assert paths[2] == "/v3/historical-candle/intraday/MCX_FO%7C2/minutes/15"
    assert paths[3].startswith("/v3/historical-candle/MCX_FO%7C2/days/1/")
    feed.candles("15m", 100, now + timedelta(seconds=10))  # cached
    assert len(api.seen) == 4


def test_feed_one_minute_range_uses_only_completed_bars_after_since() -> None:
    now = datetime(2026, 11, 24, 10, 0, 30, tzinfo=IST)
    start = datetime(2026, 11, 24, 9, 57, tzinfo=IST)
    rows = ok({"candles": [bar(start + timedelta(minutes=i), 300 + i) for i in range(4)]})  # 10:00 is forming
    api = Api(gzip.compress(json.dumps(ITEMS).encode()), rows)
    assert UpstoxFeed(api.client).bar_range(start + timedelta(seconds=30), now) == (303, 300)

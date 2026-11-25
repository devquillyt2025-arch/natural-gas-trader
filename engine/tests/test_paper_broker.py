"""Paper fills: slippage, costs, stop-before-target, gaps."""
from __future__ import annotations

import pytest

from engine.brokers.base import BrokerError, EntryRequest
from engine.brokers.paper import PaperBroker
from engine.clock import ManualClock
from engine.config import Settings
from engine.store import LocalStore, Store

from .conftest import TUE


def make() -> tuple[PaperBroker, Store]:
    s = Settings()
    store = Store(LocalStore(), None, "paper", ManualClock(TUE))
    return PaperBroker(s, store), store


def enter(b: PaperBroker, side: str = "LONG") -> None:
    long = side == "LONG"
    b.enter(EntryRequest(side, 1, 250, 300.0, 298.0 if long else 302.0, 304.0 if long else 296.0,
                         "SIM|X", 1, "why", TUE))


@pytest.mark.parametrize("side,fill", [("LONG", 300.1), ("SHORT", 299.9)])
def test_entry_pays_slippage(side: str, fill: float) -> None:
    b, _ = make()
    enter(b, side)
    assert b.position().entry == fill  # type: ignore[union-attr]


def test_round_trip_pnl_includes_slippage_and_costs() -> None:
    b, store = make()
    enter(b)
    t = b.exit(302.0, "agent", TUE)
    assert t and t.exit == 301.9 and t.pnl == pytest.approx((301.9 - 300.1) * 250 - 60)
    assert store.all_trades() == [t]
    assert b.exit(302.0, "agent", TUE) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_stop_is_assumed_first_when_both_are_hit_in_one_bar(side: str) -> None:
    b, _ = make()
    enter(b, side)
    t = b.poll(300.0, TUE, hi=305.0, lo=295.0, open_=300.0)
    assert t and t.exit_reason == "stop" and t.pnl < 0
    assert t.exit == (297.9 if side == "LONG" else 302.1)


@pytest.mark.parametrize("side,price,fill", [("LONG", 304.0, 303.9), ("SHORT", 296.0, 296.1)])
def test_target_fill_also_pays_slippage(side: str, price: float, fill: float) -> None:
    b, _ = make()
    enter(b, side)
    t = b.poll(price, TUE)
    assert t and t.exit_reason == "target" and t.exit == fill


def test_gap_through_stop_fills_at_the_gap() -> None:
    b, _ = make()
    enter(b)
    t = b.poll(295.0, TUE, hi=296.0, lo=294.0, open_=296.0)
    assert t and t.exit == 295.9
    b2, _ = make()
    enter(b2)
    t2 = b2.poll(295.0, TUE)  # live polling: price already far below the stop
    assert t2 and t2.exit == 294.9


def test_stop_only_tightens_and_no_second_position() -> None:
    b, _ = make()
    enter(b)
    with pytest.raises(BrokerError):
        b.move_stop(297.0)
    b.move_stop(299.0)
    assert b.position().stop_loss == 299.0 and b.position().initial_stop == 298.0  # type: ignore[union-attr]
    with pytest.raises(BrokerError):
        enter(b)

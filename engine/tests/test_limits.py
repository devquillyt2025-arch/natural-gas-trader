"""update_limits: tighten freely, loosen only up to the env ceiling."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from engine import limits as lm
from engine.config import Limits, Settings

BASE = Limits()


def update(updates: dict[str, Any], current: Limits = BASE, ceilings: Limits = BASE) -> lm.UpdateResult:
    return lm.apply_update(current, BASE, ceilings, updates)


@pytest.mark.parametrize(
    "name,tighter,looser",
    [
        ("max_lots", 1, 2),
        ("max_risk_inr", 1000, 2000),
        ("daily_loss_cap_inr", 2000, 4000),
        ("max_trades_per_day", 2, 6),
        ("min_rr", 2.0, 1.0),
        ("min_sl_atr", 0.8, 0.3),
        ("max_sl_atr", 2.0, 4.0),
        ("min_confidence", 0.7, 0.4),
        ("cooldown_min_after_loss", 60, 5),
        ("eia_before_min", 45, 0),
        ("eia_after_min", 60, 0),
    ],
)
def test_tighten_accepted_loosen_beyond_ceiling_rejected(name: str, tighter: float, looser: float) -> None:
    r = update({name: tighter})
    if getattr(BASE, name) != tighter:
        assert name in r.applied and getattr(r.limits, name) == tighter
    r = update({name: looser})
    assert name in r.rejected and getattr(r.limits, name) == getattr(BASE, name)


def test_loosen_within_ceiling_is_accepted() -> None:
    ceil = replace(BASE, max_risk_inr=2500, min_confidence=0.5)
    r = update({"max_risk_inr": 2500, "min_confidence": 0.5}, ceilings=ceil)
    assert r.limits.max_risk_inr == 2500 and r.limits.min_confidence == 0.5 and not r.rejected
    assert "max_risk_inr" in update({"max_risk_inr": 2501}, ceilings=ceil).rejected


def test_can_return_to_configured_value_after_tightening() -> None:
    tight = update({"max_risk_inr": 500}).limits
    r = update({"max_risk_inr": 1500}, current=tight)
    assert r.limits.max_risk_inr == 1500 and not r.rejected


def test_mixed_update_applies_good_keys_and_reports_bad_ones() -> None:
    r = update({"max_risk_inr": 1000, "max_lots": 9, "force_flat": "23:59", "min_rr": "3"})
    assert r.limits.max_risk_inr == 1000 and r.limits.max_lots == 1
    assert set(r.rejected) == {"max_lots", "force_flat", "min_rr"}


@pytest.mark.parametrize("value", [True, None, float("nan"), float("inf"), -1, 1.5])
def test_garbage_values_rejected(value: Any) -> None:
    assert "max_trades_per_day" in update({"max_trades_per_day": value}).rejected


def test_stop_bounds_cannot_cross() -> None:
    assert "min_sl_atr" in update({"min_sl_atr": 3.5}).rejected


def test_restore_clamps_saved_overrides_to_current_ceiling() -> None:
    restored = lm.restore(BASE, BASE, {"max_risk_inr": 9000, "max_trades_per_day": 2, "junk": 1})
    assert restored.max_risk_inr == 1500 and restored.max_trades_per_day == 2


def test_env_ceilings_default_to_configured_limits() -> None:
    s = Settings.from_env({"MAX_RISK_INR": "900"})
    assert s.limits.max_risk_inr == 900 and s.ceilings.max_risk_inr == 900
    s = Settings.from_env({"MAX_RISK_INR": "900", "CEIL_MAX_RISK_INR": "1800"})
    assert lm.ceilings_dict(s.limits, s.ceilings)["max_risk_inr"] == 1800


@pytest.mark.parametrize(
    "env,message",
    [
        ({"MODE": "live"}, "Refusing to start"),
        ({"MODE": "live", "LIVE_CONFIRM": "yes"}, "Refusing to start"),
        ({"MODE": "live", "LIVE_CONFIRM": "I_ACCEPT_THE_RISK"}, "DATABASE_URL"),
        ({"MODE": "Paperish"}, "MODE"),
        ({"SYMBOL": "CRUDEOIL"}, "SYMBOL"),
        ({"FORCE_FLAT": "23:25"}, "FORCE_FLAT"),
        ({"NO_ENTRY_AFTER": "23:12"}, "ENTRY_START"),
    ],
)
def test_unsafe_configuration_refuses_to_start(env: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings.from_env(env)


def test_default_mode_is_paper_and_live_needs_both_flags() -> None:
    assert Settings.from_env({}).mode == "paper"
    s = Settings.from_env({
        "MODE": "live", "LIVE_CONFIRM": "I_ACCEPT_THE_RISK",
        "DATABASE_URL": "postgresql://u:p@db.example.com/natgas",
    })
    assert s.is_live
    assert Settings.from_env({"LIVE_CONFIRM": "I_ACCEPT_THE_RISK"}).mode == "paper"

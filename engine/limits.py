"""Runtime edits of hard limits from the UI.

Rule: tightening is always accepted; loosening only as far as the ceiling set in the engine's env.
`ceilings` holds the *loosest* permitted value of each limit (a floor for "higher is tighter" ones).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .config import FLOAT_LIMITS, INT_LIMITS, Limits

EDITABLE = INT_LIMITS + FLOAT_LIMITS
# True: a LOWER number is stricter. False: a HIGHER number is stricter.
LOWER_IS_TIGHTER: dict[str, bool] = {
    "max_lots": True,
    "max_risk_inr": True,
    "daily_loss_cap_inr": True,
    "max_trades_per_day": True,
    "min_rr": False,
    "min_sl_atr": False,
    "max_sl_atr": True,
    "min_confidence": False,
    "cooldown_min_after_loss": False,
    "eia_before_min": False,
    "eia_after_min": False,
}
SANE_RANGE: dict[str, tuple[float, float]] = {
    "max_lots": (1, 100),
    "max_risk_inr": (1, 10_000_000),
    "daily_loss_cap_inr": (1, 10_000_000),
    "max_trades_per_day": (0, 100),
    "min_rr": (0, 20),
    "min_sl_atr": (0.05, 20),
    "max_sl_atr": (0.05, 20),
    "min_confidence": (0, 1),
    "cooldown_min_after_loss": (0, 1440),
    "eia_before_min": (0, 240),
    "eia_after_min": (0, 240),
}


@dataclass(frozen=True)
class UpdateResult:
    limits: Limits
    applied: dict[str, str]  # name -> "old -> new"
    rejected: dict[str, str]  # name -> reason


def _with(limits: Limits, name: str, value: float) -> Limits:
    change: dict[str, Any] = {name: int(value) if name in INT_LIMITS else float(value)}
    return replace(limits, **change)


def loosest(name: str, configured: float, ceiling: float) -> float:
    """The configured value is always allowed, even if a ceiling was set tighter than it."""
    return max(configured, ceiling) if LOWER_IS_TIGHTER[name] else min(configured, ceiling)


def _is_looser(name: str, new: float, than: float) -> bool:
    return new > than if LOWER_IS_TIGHTER[name] else new < than


def apply_update(
    current: Limits, configured: Limits, ceilings: Limits, updates: Mapping[str, Any]
) -> UpdateResult:
    limits = current
    applied: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for name, raw in updates.items():
        if name not in EDITABLE:
            rejected[name] = "not an editable limit"
            continue
        if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
            rejected[name] = "must be a number"
            continue
        lo, hi = SANE_RANGE[name]
        if not lo <= raw <= hi:
            rejected[name] = f"must be between {lo} and {hi}"
            continue
        if name in INT_LIMITS and raw != int(raw):
            rejected[name] = "must be a whole number"
            continue
        new = int(raw) if name in INT_LIMITS else float(raw)
        old = getattr(limits, name)
        if new == old:
            continue
        ceiling = loosest(name, getattr(configured, name), getattr(ceilings, name))
        if _is_looser(name, new, old) and _is_looser(name, new, ceiling):
            rejected[name] = f"{new} is looser than the engine ceiling {ceiling}"
            continue
        candidate = _with(limits, name, new)
        if not candidate.min_sl_atr < candidate.max_sl_atr:
            rejected[name] = "min_sl_atr must stay below max_sl_atr"
            continue
        limits = candidate
        applied[name] = f"{old} -> {new}"
    return UpdateResult(limits, applied, rejected)


def restore(configured: Limits, ceilings: Limits, saved: Mapping[str, Any]) -> Limits:
    """Re-apply UI overrides after a restart, clamped to what the env allows now."""
    limits = configured
    for name, value in saved.items():
        if name not in EDITABLE:
            continue
        ceiling = loosest(name, getattr(configured, name), getattr(ceilings, name))
        if _is_looser(name, value, ceiling):
            value = ceiling
        limits = _with(limits, name, value)
    return limits if limits.min_sl_atr < limits.max_sl_atr else configured


def to_dict(limits: Limits) -> dict[str, Any]:
    out: dict[str, Any] = {name: getattr(limits, name) for name in EDITABLE}
    out["entry_start"] = f"{limits.entry_start:%H:%M}"
    out["no_entry_after"] = f"{limits.no_entry_after:%H:%M}"
    return out


def ceilings_dict(configured: Limits, ceilings: Limits) -> dict[str, Any]:
    return {n: loosest(n, getattr(configured, n), getattr(ceilings, n)) for n in EDITABLE}

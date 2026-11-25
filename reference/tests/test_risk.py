from datetime import datetime, timedelta

from app import sessions
from app.config import IST, Settings
from app.models import Decision, Position
from app.risk import RiskContext, validate

S = Settings()
TUE_NOON = datetime(2026, 11, 24, 12, 0, tzinfo=IST)  # a plain Tuesday, no EIA nearby


def ctx(**kw) -> RiskContext:
    base = dict(now=TUE_NOON, ltp=300.0, atr15=1.0, lot_size=250, position=None,
                daily_pnl=0.0, trades_today=0, last_loss_at=None, kill=False)
    base.update(kw)
    return RiskContext(**base)


def long(**kw) -> Decision:
    base = dict(action="ENTER_LONG", lots=1, stop_loss=298.0, target=304.0, confidence=0.8, reasoning="x")
    base.update(kw)
    return Decision(**base)


def pos(side="LONG", sl=298.0) -> Position:
    return Position(side, 1, 250, 300.0, sl, 304.0, TUE_NOON.isoformat(), "SIM|X")


def test_good_long_is_approved():
    v = validate(S, long(), ctx())
    assert v.ok, v.reason


def test_hold_always_ok():
    assert validate(S, Decision(action="HOLD"), ctx(kill=True)).ok


def test_kill_switch_blocks_entries_but_not_exit():
    assert not validate(S, long(), ctx(kill=True)).ok
    assert validate(S, Decision(action="EXIT"), ctx(kill=True, position=pos())).ok


def test_unknown_action_rejected():
    assert not validate(S, Decision(action="YOLO"), ctx()).ok


def test_exit_without_position_rejected():
    assert not validate(S, Decision(action="EXIT"), ctx()).ok


def test_stop_on_wrong_side_rejected():
    assert not validate(S, long(stop_loss=302.0), ctx()).ok
    short = Decision(action="ENTER_SHORT", stop_loss=298.0, target=296.0, confidence=0.9)
    assert not validate(S, short, ctx()).ok


def test_missing_stop_or_target_rejected():
    assert not validate(S, long(stop_loss=None), ctx()).ok
    assert not validate(S, long(target=None), ctx()).ok


def test_stop_too_tight_and_too_wide():
    assert not validate(S, long(stop_loss=299.7, target=302.0), ctx()).ok  # 0.3 < 0.5 ATR
    assert not validate(S, long(stop_loss=296.0, target=310.0), ctx()).ok  # 4 > 3 ATR


def test_poor_reward_risk_rejected():
    assert not validate(S, long(stop_loss=298.0, target=301.5), ctx()).ok  # RR 0.75


def test_low_confidence_rejected():
    assert not validate(S, long(confidence=0.4), ctx()).ok


def test_lots_clipped_to_risk_budget():
    # dist 2.0 * 250 = 500 INR/lot; budget 1500 -> 3 lots, but MAX_LOTS=1
    v = validate(S, long(lots=5), ctx())
    assert v.ok and v.decision.lots == 1


def test_trade_rejected_if_one_lot_exceeds_risk_cap():
    # dist 2.9 -> 725/lot fine; with lot size 1250 -> 3625 > 1500
    assert not validate(S, long(stop_loss=297.1, target=305.0), ctx(lot_size=1250)).ok


def test_no_pyramiding_or_flipping():
    assert not validate(S, long(), ctx(position=pos())).ok


def test_daily_loss_cap_blocks_entries():
    assert not validate(S, long(), ctx(daily_pnl=-S.daily_loss_cap_inr)).ok


def test_remaining_daily_budget_limits_size():
    # only 400 INR of budget left, 500 INR/lot -> reject
    assert not validate(S, long(), ctx(daily_pnl=-(S.daily_loss_cap_inr - 400))).ok


def test_max_trades_per_day():
    assert not validate(S, long(), ctx(trades_today=S.max_trades_per_day)).ok


def test_cooldown_after_loss():
    recent = TUE_NOON - timedelta(minutes=10)
    assert not validate(S, long(), ctx(last_loss_at=recent)).ok
    old = TUE_NOON - timedelta(minutes=90)
    assert validate(S, long(), ctx(last_loss_at=old)).ok


def test_weekend_and_off_hours_blocked():
    sat = datetime(2026, 11, 28, 12, 0, tzinfo=IST)
    assert not validate(S, long(), ctx(now=sat)).ok
    late = TUE_NOON.replace(hour=23, minute=0)
    assert not validate(S, long(), ctx(now=late)).ok
    early = TUE_NOON.replace(hour=9, minute=10)
    assert not validate(S, long(), ctx(now=early)).ok


def test_trail_stop_only_tightens():
    p = pos()
    up = Decision(action="TRAIL_STOP", stop_loss=299.0)
    assert validate(S, up, ctx(position=p, ltp=302.0)).ok
    widen = Decision(action="TRAIL_STOP", stop_loss=297.0)
    assert not validate(S, widen, ctx(position=p, ltp=302.0)).ok
    through_price = Decision(action="TRAIL_STOP", stop_loss=302.5)
    assert not validate(S, through_price, ctx(position=p, ltp=302.0)).ok
    ps = pos("SHORT", sl=302.0)
    assert validate(S, Decision(action="TRAIL_STOP", stop_loss=301.0), ctx(position=ps, ltp=298.0)).ok
    assert not validate(S, Decision(action="TRAIL_STOP", stop_loss=303.0), ctx(position=ps, ltp=298.0)).ok


# ---- EIA calendar -------------------------------------------------------------------
def test_eia_regular_thursday_is_2100_ist_in_winter():
    thu = datetime(2026, 12, 10, 12, 0, tzinfo=IST)
    nxt = sessions.next_eia(thu)
    assert (nxt.month, nxt.day, nxt.hour, nxt.minute) == (12, 10, 21, 0)


def test_eia_thanksgiving_week_is_wednesday_2230_ist():
    # Thanksgiving 2026 = Thu Nov 26 -> report Wed Nov 25 12:00 ET = 22:30 IST
    nxt = sessions.next_eia(datetime(2026, 11, 25, 9, 0, tzinfo=IST))
    assert (nxt.month, nxt.day, nxt.hour, nxt.minute) == (11, 25, 22, 30)


def test_eia_blackout_blocks_entries():
    t = datetime(2026, 11, 25, 22, 20, tzinfo=IST)
    assert sessions.in_eia_blackout(t, S.eia_before_min, S.eia_after_min)
    # a plain 22:20 on a non-EIA Tuesday should not be a blackout
    assert not sessions.in_eia_blackout(datetime(2026, 11, 24, 22, 20, tzinfo=IST), S.eia_before_min, S.eia_after_min)

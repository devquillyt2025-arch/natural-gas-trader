"""Settings, read from environment / .env. Every risk limit lives here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _s(name: str, default: str) -> str:
    return os.getenv(name, default)


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # --- mode -------------------------------------------------------------
    mode: str = field(default_factory=lambda: _s("MODE", "paper"))  # paper | live
    live_confirm: str = field(default_factory=lambda: _s("LIVE_CONFIRM", ""))

    # --- instrument -------------------------------------------------------
    # NATGASMINI (250 mmBtu lot) is the sane default; NATURALGAS is 1250 mmBtu.
    symbol: str = field(default_factory=lambda: _s("SYMBOL", "NATGASMINI"))
    roll_days: int = field(default_factory=lambda: _i("ROLL_DAYS", 3))
    product: str = field(default_factory=lambda: _s("PRODUCT", "I"))  # I=intraday, D=carry

    # --- Upstox -----------------------------------------------------------
    upstox_client_id: str = field(default_factory=lambda: _s("UPSTOX_CLIENT_ID", ""))
    upstox_client_secret: str = field(default_factory=lambda: _s("UPSTOX_CLIENT_SECRET", ""))
    upstox_redirect_uri: str = field(
        default_factory=lambda: _s("UPSTOX_REDIRECT_URI", "http://localhost:8001/auth/callback")
    )

    # --- Claude -----------------------------------------------------------
    anthropic_api_key: str = field(default_factory=lambda: _s("ANTHROPIC_API_KEY", ""))
    model: str = field(default_factory=lambda: _s("MODEL", "claude-sonnet-5-5"))
    use_news: bool = field(default_factory=lambda: _b("USE_NEWS", True))
    news_ttl_min: int = field(default_factory=lambda: _i("NEWS_TTL_MIN", 60))

    # --- cadence ----------------------------------------------------------
    decision_interval_min: int = field(default_factory=lambda: _i("DECISION_INTERVAL_MIN", 15))
    monitor_sec: int = field(default_factory=lambda: _i("MONITOR_SEC", 20))

    # --- HARD risk limits (code-enforced, Claude cannot override) ---------
    max_lots: int = field(default_factory=lambda: _i("MAX_LOTS", 1))
    max_risk_inr: float = field(default_factory=lambda: _f("MAX_RISK_INR", 1500))
    daily_loss_cap_inr: float = field(default_factory=lambda: _f("DAILY_LOSS_CAP_INR", 3000))
    max_trades_per_day: int = field(default_factory=lambda: _i("MAX_TRADES_PER_DAY", 4))
    min_rr: float = field(default_factory=lambda: _f("MIN_RR", 1.5))
    min_sl_atr: float = field(default_factory=lambda: _f("MIN_SL_ATR", 0.5))
    max_sl_atr: float = field(default_factory=lambda: _f("MAX_SL_ATR", 3.0))
    min_confidence: float = field(default_factory=lambda: _f("MIN_CONFIDENCE", 0.6))
    cooldown_min_after_loss: int = field(default_factory=lambda: _i("COOLDOWN_MIN_AFTER_LOSS", 30))

    # --- session (IST). MCX commodity session ~09:00-23:30 ----------------
    entry_start: str = field(default_factory=lambda: _s("ENTRY_START", "09:30"))
    no_entry_after: str = field(default_factory=lambda: _s("NO_ENTRY_AFTER", "22:30"))
    force_flat: str = field(default_factory=lambda: _s("FORCE_FLAT", "23:10"))
    session_end: str = field(default_factory=lambda: _s("SESSION_END", "23:25"))  # engine stops after this
    eia_before_min: int = field(default_factory=lambda: _i("EIA_BEFORE_MIN", 20))
    eia_after_min: int = field(default_factory=lambda: _i("EIA_AFTER_MIN", 30))

    # --- paper realism ----------------------------------------------------
    slippage_inr: float = field(default_factory=lambda: _f("SLIPPAGE_INR", 0.10))
    cost_per_lot_inr: float = field(default_factory=lambda: _f("COST_PER_LOT_INR", 60))

    # --- misc -------------------------------------------------------------
    db_path: str = field(default_factory=lambda: _s("DB_PATH", "data/trader.db"))
    admin_token: str = field(default_factory=lambda: _s("ADMIN_TOKEN", ""))
    telegram_bot_token: str = field(default_factory=lambda: _s("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: _s("TELEGRAM_CHAT_ID", ""))

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def validate(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ValueError("MODE must be 'paper' or 'live'")
        if self.is_live and self.live_confirm != "I_ACCEPT_THE_RISK":
            raise ValueError(
                "Refusing to start in live mode. Set LIVE_CONFIRM=I_ACCEPT_THE_RISK "
                "after at least 2-3 weeks of paper results you are happy with."
            )


def hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)

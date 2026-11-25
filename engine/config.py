"""Settings. `Settings()` is pure defaults; `Settings.from_env()` reads the engine's environment."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
ENGINE_DIR = Path(__file__).resolve().parent
LIVE_PHRASE = "I_ACCEPT_THE_RISK"

# mmBtu per lot. Deliberately hard-coded: all risk maths depends on it.
MULTIPLIERS = {"NATGASMINI": 250, "NATURALGAS": 1250}
TICK_SIZE = 0.10
CANDLE_MINUTES = (5, 15)
# MCX closes 23:30 at the earliest; force-flat must leave room to retry before then.
LATEST_FORCE_FLAT = time(23, 15)


@dataclass(frozen=True)
class Limits:
    """Every hard risk limit. The risk layer reads nothing else."""

    max_lots: int = 1
    max_risk_inr: float = 1500.0
    daily_loss_cap_inr: float = 3000.0
    max_trades_per_day: int = 4
    min_rr: float = 1.5
    min_sl_atr: float = 0.5
    max_sl_atr: float = 3.0
    min_confidence: float = 0.6
    cooldown_min_after_loss: int = 30
    eia_before_min: int = 20
    eia_after_min: int = 30
    entry_start: time = time(9, 30)
    no_entry_after: time = time(22, 30)


INT_LIMITS = ("max_lots", "max_trades_per_day", "cooldown_min_after_loss", "eia_before_min", "eia_after_min")
FLOAT_LIMITS = ("max_risk_inr", "daily_loss_cap_inr", "min_rr", "min_sl_atr", "max_sl_atr", "min_confidence")


def parse_hhmm(text: str) -> time:
    h, m = text.strip().split(":")
    return time(int(h), int(m))


def parse_eia_overrides(text: str) -> dict[date, datetime | None]:
    """`2026-12-24=2026-12-28T12:00,2026-12-31=none`

    Key: the *regular Thursday* being moved. Value: new date and time in US Eastern, or `none`.
    """
    out: dict[date, datetime | None] = {}
    for item in filter(None, (p.strip() for p in text.split(","))):
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"EIA_OVERRIDES entry {item!r} is not key=value")
        day = date.fromisoformat(key.strip())
        if day.weekday() != 3:
            raise ValueError(f"EIA_OVERRIDES key {day} is not a Thursday")
        value = value.strip()
        out[day] = None if value.lower() == "none" else datetime.fromisoformat(value)
    return out


@dataclass(frozen=True)
class Settings:
    # --- mode ---------------------------------------------------------------------------
    mode: str = "paper"
    live_confirm: str = ""
    # --- instrument ---------------------------------------------------------------------
    symbol: str = "NATGASMINI"
    roll_days: int = 3
    product: str = "I"
    market_protection: int | None = None  # None = let Upstox default (-1, automatic)
    # --- Upstox -------------------------------------------------------------------------
    upstox_client_id: str = ""
    upstox_client_secret: str = field(default="", repr=False)
    upstox_redirect_uri: str = ""
    # --- Claude -------------------------------------------------------------------------
    claude_auth: str = "api"  # "api" (API key) or "subscription" (Claude Code CLI, paper only)
    anthropic_api_key: str = field(default="", repr=False)
    claude_oauth_token: str = field(default="", repr=False)  # from `claude setup-token`; empty = the machine's Claude Code sign-in
    claude_cli: str = ""  # path to the claude executable; empty = find it on PATH
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"
    use_news: bool = True
    news_ttl_min: int = 60
    llm_timeout_sec: float = 60.0
    # --- Neon (Postgres) ----------------------------------------------------------------
    database_url: str = field(default="", repr=False)
    # --- cadence ------------------------------------------------------------------------
    candle_min: int = 15  # 5 or 15: decisions, indicators and stop limits all use candles of this size
    decision_delay_sec: int = 8
    monitor_sec: int = 20
    heartbeat_sec: int = 5
    command_poll_sec: int = 4
    command_ttl_sec: int = 600
    control_timeout_sec: int = 90
    decision_max_age_sec: int = 180
    # --- session ------------------------------------------------------------------------
    force_flat: time = time(23, 10)
    eia_overrides: Mapping[date, datetime | None] = field(default_factory=dict)
    no_trade_dates: frozenset[date] = frozenset()
    # --- limits -------------------------------------------------------------------------
    limits: Limits = field(default_factory=Limits)
    ceilings: Limits = field(default_factory=Limits)  # loosest values the UI may set
    # --- paper realism ------------------------------------------------------------------
    slippage_inr: float = 0.10
    cost_per_lot_inr: float = 60.0
    # --- misc ---------------------------------------------------------------------------
    data_dir: Path = ENGINE_DIR / "data"
    log_level: str = "INFO"
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def multiplier(self) -> int:
        return MULTIPLIERS[self.symbol]

    @property
    def tf(self) -> str:
        """The name of the decision timeframe, as stored with each candle."""
        return f"{self.candle_min}m"

    @property
    def has_database(self) -> bool:
        return bool(self.database_url)

    def validate(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ValueError("MODE must be 'paper' or 'live'")
        if self.symbol not in MULTIPLIERS:
            raise ValueError(f"SYMBOL must be one of {sorted(MULTIPLIERS)}")
        if self.candle_min not in CANDLE_MINUTES:
            raise ValueError(f"CANDLE_MIN must be one of {CANDLE_MINUTES}")
        if self.claude_auth not in ("api", "subscription"):
            raise ValueError("CLAUDE_AUTH must be 'api' or 'subscription'")
        if self.is_live:
            if self.claude_auth != "api":
                raise ValueError(
                    "Live mode needs CLAUDE_AUTH=api: a subscription's usage limit can run out in the middle of a session"
                )
            if self.live_confirm != LIVE_PHRASE:
                raise ValueError(
                    f"Refusing to start in live mode. LIVE_CONFIRM={LIVE_PHRASE} is also required; "
                    "set it only after the go-live checklist in the README passes."
                )
            if not self.has_database:
                raise ValueError("Live mode needs DATABASE_URL (the kill switch lives there)")
        if self.force_flat > LATEST_FORCE_FLAT:
            raise ValueError(f"FORCE_FLAT must be no later than {LATEST_FORCE_FLAT:%H:%M}")
        lim = self.limits
        if not (lim.entry_start < lim.no_entry_after < self.force_flat):
            raise ValueError("need ENTRY_START < NO_ENTRY_AFTER < FORCE_FLAT")
        if lim.max_lots < 1 or lim.max_risk_inr <= 0 or lim.daily_loss_cap_inr <= 0:
            raise ValueError("MAX_LOTS, MAX_RISK_INR and DAILY_LOSS_CAP_INR must be positive")
        if not 0 < lim.min_sl_atr < lim.max_sl_atr:
            raise ValueError("need 0 < MIN_SL_ATR < MAX_SL_ATR")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        d = cls()

        def text(name: str, default: str = "") -> str:
            return e.get(name, "").strip() or default

        def integer(name: str, default: int) -> int:
            return int(text(name, str(default)))

        def number(name: str, default: float) -> float:
            return float(text(name, str(default)))

        def flag(name: str, default: bool) -> bool:
            return text(name, str(default)).lower() in ("1", "true", "yes", "on")

        def clock(name: str, default: time) -> time:
            return parse_hhmm(text(name, f"{default:%H:%M}"))

        base = d.limits
        limits = Limits(
            max_lots=integer("MAX_LOTS", base.max_lots),
            max_risk_inr=number("MAX_RISK_INR", base.max_risk_inr),
            daily_loss_cap_inr=number("DAILY_LOSS_CAP_INR", base.daily_loss_cap_inr),
            max_trades_per_day=integer("MAX_TRADES_PER_DAY", base.max_trades_per_day),
            min_rr=number("MIN_RR", base.min_rr),
            min_sl_atr=number("MIN_SL_ATR", base.min_sl_atr),
            max_sl_atr=number("MAX_SL_ATR", base.max_sl_atr),
            min_confidence=number("MIN_CONFIDENCE", base.min_confidence),
            cooldown_min_after_loss=integer("COOLDOWN_MIN_AFTER_LOSS", base.cooldown_min_after_loss),
            eia_before_min=integer("EIA_BEFORE_MIN", base.eia_before_min),
            eia_after_min=integer("EIA_AFTER_MIN", base.eia_after_min),
            entry_start=clock("ENTRY_START", base.entry_start),
            no_entry_after=clock("NO_ENTRY_AFTER", base.no_entry_after),
        )
        # Ceilings default to the configured limits: by default the UI can only tighten.
        overrides: dict[str, float | int] = {}
        for name in INT_LIMITS + FLOAT_LIMITS:
            raw = text(f"CEIL_{name.upper()}")
            if raw:
                overrides[name] = int(raw) if name in INT_LIMITS else float(raw)
        ceilings = replace(limits, **overrides)  # type: ignore[arg-type]

        protection = text("MARKET_PROTECTION")
        data_dir = text("DATA_DIR")
        s = cls(
            mode=text("MODE", d.mode).lower(),
            live_confirm=text("LIVE_CONFIRM"),
            symbol=text("SYMBOL", d.symbol).upper(),
            roll_days=integer("ROLL_DAYS", d.roll_days),
            product=text("PRODUCT", d.product),
            market_protection=int(protection) if protection else None,
            upstox_client_id=text("UPSTOX_CLIENT_ID"),
            upstox_client_secret=text("UPSTOX_CLIENT_SECRET"),
            upstox_redirect_uri=text("UPSTOX_REDIRECT_URI"),
            claude_auth=text("CLAUDE_AUTH", d.claude_auth).lower(),
            anthropic_api_key=text("ANTHROPIC_API_KEY"),
            claude_oauth_token=text("CLAUDE_CODE_OAUTH_TOKEN"),
            claude_cli=text("CLAUDE_CLI"),
            model=text("MODEL", d.model),
            effort=text("EFFORT", d.effort),
            use_news=flag("USE_NEWS", d.use_news),
            news_ttl_min=integer("NEWS_TTL_MIN", d.news_ttl_min),
            llm_timeout_sec=number("LLM_TIMEOUT_SEC", d.llm_timeout_sec),
            database_url=text("DATABASE_URL"),
            candle_min=integer("CANDLE_MIN", d.candle_min),
            monitor_sec=integer("MONITOR_SEC", d.monitor_sec),
            force_flat=clock("FORCE_FLAT", d.force_flat),
            eia_overrides=parse_eia_overrides(text("EIA_OVERRIDES")),
            no_trade_dates=frozenset(
                date.fromisoformat(x.strip()) for x in text("NO_TRADE_DATES").split(",") if x.strip()
            ),
            limits=limits,
            ceilings=ceilings,
            slippage_inr=number("SLIPPAGE_INR", d.slippage_inr),
            cost_per_lot_inr=number("COST_PER_LOT_INR", d.cost_per_lot_inr),
            data_dir=Path(data_dir) if data_dir else d.data_dir,
            log_level=text("LOG_LEVEL", d.log_level),
            telegram_bot_token=text("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=text("TELEGRAM_CHAT_ID"),
        )
        s.validate()
        return s

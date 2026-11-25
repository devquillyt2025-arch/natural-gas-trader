from __future__ import annotations

import logging

import httpx

from .config import Settings
from .store import Store

log = logging.getLogger("notify")


class Notifier:
    """Logs every message to the events table and, if configured, sends it to Telegram."""

    def __init__(self, s: Settings, store: Store):
        self.s, self.store = s, store

    def send(self, msg: str, level: str = "info") -> None:
        self.store.event(level, msg)
        log.info(msg)
        if not (self.s.telegram_bot_token and self.s.telegram_chat_id):
            return
        try:
            httpx.post(
                f"https://api.telegram.org/bot{self.s.telegram_bot_token}/sendMessage",
                json={"chat_id": self.s.telegram_chat_id, "text": f"[natgas/{self.s.mode}] {msg}"},
                timeout=10,
            )
        except Exception as e:  # alerts must never break trading
            log.warning("telegram failed: %s", e)

"""Alerts: every message goes to the events table; Telegram too if configured."""
from __future__ import annotations

import logging
import threading

import httpx

from .config import Settings
from .store import Store

log = logging.getLogger("notify")
LEVELS = {"info": logging.INFO, "trade": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


class Notifier:
    def __init__(self, settings: Settings, store: Store):
        self.s, self.store = settings, store

    def send(self, message: str, level: str = "info") -> None:
        log.log(LEVELS.get(level, logging.INFO), message, extra={"event_level": level})
        self.store.event(level, message)
        if self.s.telegram_bot_token and self.s.telegram_chat_id:
            threading.Thread(target=self._telegram, args=(message,), daemon=True).start()

    def _telegram(self, message: str) -> None:
        try:
            httpx.post(
                f"https://api.telegram.org/bot{self.s.telegram_bot_token}/sendMessage",
                json={"chat_id": self.s.telegram_chat_id, "text": f"[natgas/{self.s.mode}] {message}"},
                timeout=10,
            )
        except Exception as e:  # alerts must never break trading; do not log the URL (it holds the token)
            log.warning("telegram failed: %s", type(e).__name__)

"""Telegram delivery — single shared implementation for all cockpit scripts."""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from cockpit.config import TELEGRAM_MESSAGE_LIMIT

logger = logging.getLogger(__name__)

_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Posts messages to a Telegram chat. Credentials loaded from environment variables."""

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> None:
        self._token   = token   or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        if not self._token or not self._chat_id:
            logger.warning(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — messages will not be delivered"
            )

    @property
    def is_configured(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, text: str, *, parse_mode: str = "HTML") -> bool:
        """Send text to the configured chat. Truncates gracefully at TELEGRAM_MESSAGE_LIMIT chars."""
        if not self.is_configured:
            logger.warning("Telegram not configured — skipping send")
            return False

        payload = text[:TELEGRAM_MESSAGE_LIMIT]
        try:
            resp = requests.post(
                _SEND_URL.format(token=self._token),
                json={"chat_id": self._chat_id, "text": payload, "parse_mode": parse_mode},
                timeout=15,
            )
            data = resp.json()
            if data.get("ok"):
                logger.info("Telegram message sent (id=%s)", data["result"]["message_id"])
                return True
            logger.error("Telegram API error: %s", data)
            return False
        except requests.RequestException as exc:
            logger.error("Telegram send failed: %s", exc)
            return False

    def send_chunked(self, text: str, *, parse_mode: str = "HTML") -> bool:
        """Split long text into chunks and send sequentially. Returns True if all chunks sent."""
        chunks = [text[i:i + TELEGRAM_MESSAGE_LIMIT] for i in range(0, len(text), TELEGRAM_MESSAGE_LIMIT)]
        return all(self.send(chunk, parse_mode=parse_mode) for chunk in chunks)

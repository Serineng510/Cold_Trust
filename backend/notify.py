"""Outbound messages to driver and admin. Telegram only; silent no-op when not configured.

Voice is produced in the browser (speechSynthesis) from the same text, so the dashboard
and the driver page do not need a TTS service.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
import uuid

from .config import Settings

log = logging.getLogger("coldtrust.notify")


class Notifier:
    def __init__(self, cfg: Settings):
        self.token = cfg.telegram_token
        self.chat_ids = cfg.telegram_chat_ids

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_ids)

    def send(self, text: str) -> int:
        """Blocking; call via asyncio.to_thread. Returns the number of chats reached."""
        if not self.enabled:
            return 0
        sent = 0
        for chat in self.chat_ids:
            data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendMessage", data=data)
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    sent += 1 if json.loads(r.read()).get("ok") else 0
            except Exception as exc:
                log.warning("Telegram send failed for %s: %s", chat, exc)
        return sent

    def send_photo(self, jpeg: bytes, caption: str) -> int:
        """Alert with the annotated camera frame attached (Telegram sendPhoto). Blocking; use asyncio.to_thread."""
        if not self.enabled:
            return 0
        sent = 0
        for chat in self.chat_ids:
            boundary = uuid.uuid4().hex
            parts = [
                f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'.encode(),
                f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption[:1000]}\r\n'.encode(),
                f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="cargo-hold.jpg"\r\n'
                f'Content-Type: image/jpeg\r\n\r\n'.encode() + jpeg + b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/sendPhoto", data=b"".join(parts),
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    sent += 1 if json.loads(r.read()).get("ok") else 0
            except Exception as exc:
                log.warning("Telegram photo failed for %s: %s", chat, exc)
        return sent

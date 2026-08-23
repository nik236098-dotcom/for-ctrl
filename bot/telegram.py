import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramSender:
    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id

    def _url(self, method: str) -> str:
        return API_BASE.format(token=self._bot_token, method=method)

    def send_message(self, text: str) -> None:
        resp = requests.post(
            self._url("sendMessage"),
            data={"chat_id": self._chat_id, "text": text},
            timeout=30,
        )
        resp.raise_for_status()

    def send_document(self, file_path: str, caption: str | None = None) -> None:
        path = Path(file_path)
        with path.open("rb") as f:
            resp = requests.post(
                self._url("sendDocument"),
                data={"chat_id": self._chat_id, "caption": caption or ""},
                files={"document": (path.name, f)},
                timeout=120,
            )
        resp.raise_for_status()
        log.info("Отправлен документ в Telegram: %s", path.name)

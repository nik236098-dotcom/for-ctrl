import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _load_offset(state_file: str) -> int | None:
    p = Path(state_file)
    if not p.exists():
        return None
    return json.loads(p.read_text()).get("offset")


def _save_offset(state_file: str, offset: int) -> None:
    p = Path(state_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"offset": offset}))


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = str(chat_id)

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

    def _get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        params: dict = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        resp = requests.get(self._url("getUpdates"), params=params, timeout=timeout + 10)
        resp.raise_for_status()
        return resp.json().get("result", [])

    def wait_for_reply(self, offset_state_file: str, overall_timeout_seconds: int = 600) -> str | None:
        """
        Long-poll: ждёт следующее текстовое сообщение из нужного чата
        (TELEGRAM_CHAT_ID) и возвращает его текст. None, если не дождались
        за overall_timeout_seconds. Сообщения из других чатов игнорируются —
        так что подкинуть код может только тот, кто пишет в этот конкретный чат.
        """
        deadline = time.monotonic() + overall_timeout_seconds
        offset = _load_offset(offset_state_file)

        while time.monotonic() < deadline:
            remaining = max(1, int(deadline - time.monotonic()))
            poll_timeout = min(30, remaining)
            try:
                updates = self._get_updates(offset, timeout=poll_timeout)
            except requests.RequestException:
                log.exception("Ошибка при опросе Telegram getUpdates, повтор через 5 сек")
                time.sleep(5)
                continue

            for upd in updates:
                offset = upd["update_id"] + 1
                _save_offset(offset_state_file, offset)

                message = upd.get("message") or {}
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = (message.get("text") or "").strip()

                if chat_id != self._chat_id:
                    continue  # сообщение не из нашего чата — игнорируем
                if text:
                    return text

        return None

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name} (проверьте .env)")
    return value


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: str
    adb_device: str
    poll_interval_seconds: int
    download_dir: str
    seen_docs_file: str

    @classmethod
    def load(cls) -> "Config":
        return cls(
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
            adb_device=os.getenv("ADB_DEVICE", "127.0.0.1:5555"),
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "300")),
            download_dir=os.getenv("DOWNLOAD_DIR", "./downloads"),
            seen_docs_file=os.getenv("SEEN_DOCS_FILE", "./data/seen_docs.json"),
        )

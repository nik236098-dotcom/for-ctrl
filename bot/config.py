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
    login_alert_state_file: str
    login_alert_cooldown_hours: float
    telegram_offset_file: str
    otp_wait_timeout_seconds: int
    goskey_login: str | None
    goskey_password: str | None

    @classmethod
    def load(cls) -> "Config":
        return cls(
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
            adb_device=os.getenv("ADB_DEVICE", "127.0.0.1:5555"),
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "300")),
            download_dir=os.getenv("DOWNLOAD_DIR", "./downloads"),
            seen_docs_file=os.getenv("SEEN_DOCS_FILE", "./data/seen_docs.json"),
            login_alert_state_file=os.getenv("LOGIN_ALERT_STATE_FILE", "./data/login_alert_state.json"),
            login_alert_cooldown_hours=float(os.getenv("LOGIN_ALERT_COOLDOWN_HOURS", "6")),
            telegram_offset_file=os.getenv("TELEGRAM_OFFSET_FILE", "./data/telegram_offset.json"),
            otp_wait_timeout_seconds=int(os.getenv("OTP_WAIT_TIMEOUT_SECONDS", "600")),
            goskey_login=os.getenv("GOSKEY_LOGIN") or None,
            goskey_password=os.getenv("GOSKEY_PASSWORD") or None,
        )

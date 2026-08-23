import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name} (проверьте ваш .env-файл аккаунта)")
    return value


def _account_slug(env_file: str) -> str:
    """.env -> 'default', .env.personal -> 'personal', .env.ip -> 'ip'."""
    name = Path(env_file).name
    if name.startswith(".env"):
        slug = name[len(".env"):].lstrip(".")
    else:
        slug = name
    return slug or "default"


@dataclass(frozen=True)
class Config:
    account_label: str
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
    def load(cls, env_file: str = ".env") -> "Config":
        """
        env_file позволяет держать несколько аккаунтов Госключ (например,
        .env.personal и .env.ip) с разными Telegram-получателями, разными
        путями к данным и разным ADB-адресом контейнера. Пути к файлам
        состояния по умолчанию автоматически разносятся по подпапке
        data/<slug>/, где slug берётся из имени файла — .env.personal
        и .env.ip не будут писать в одни и те же файлы, даже если их не
        указывать явно.
        """
        load_dotenv(env_file, override=True)
        slug = _account_slug(env_file)

        return cls(
            account_label=os.getenv("ACCOUNT_LABEL", slug),
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
            adb_device=os.getenv("ADB_DEVICE", "127.0.0.1:5555"),
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "300")),
            download_dir=os.getenv("DOWNLOAD_DIR", f"./downloads/{slug}"),
            seen_docs_file=os.getenv("SEEN_DOCS_FILE", f"./data/{slug}/seen_docs.json"),
            login_alert_state_file=os.getenv("LOGIN_ALERT_STATE_FILE", f"./data/{slug}/login_alert_state.json"),
            login_alert_cooldown_hours=float(os.getenv("LOGIN_ALERT_COOLDOWN_HOURS", "6")),
            telegram_offset_file=os.getenv("TELEGRAM_OFFSET_FILE", f"./data/{slug}/telegram_offset.json"),
            otp_wait_timeout_seconds=int(os.getenv("OTP_WAIT_TIMEOUT_SECONDS", "600")),
            goskey_login=os.getenv("GOSKEY_LOGIN") or None,
            goskey_password=os.getenv("GOSKEY_PASSWORD") or None,
        )

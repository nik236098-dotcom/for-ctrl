"""Настройки бота: читаются из окружения (файл .env подхватывает systemd)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} должен быть числом, получено: {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    sub_days: int
    max_devices: int

    crypto_pay_token: str
    price_amount: str
    price_asset: str
    crypto_pay_testnet: bool

    scripts_dir: Path
    db_path: Path
    wg_iface: str
    config_message_ttl_minutes: int

    @property
    def payments_enabled(self) -> bool:
        return bool(self.crypto_pay_token)

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.admin_ids


def load() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан — заполните vpn/bot/.env")

    admin_raw = os.getenv("ADMIN_IDS", "").strip()
    admin_ids = frozenset(
        int(part) for part in admin_raw.replace(" ", "").split(",") if part
    )
    if not admin_ids:
        raise SystemExit("ADMIN_IDS не задан — без админа боту некому выдавать инвайты")

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        sub_days=_int("SUB_DAYS", 30),
        max_devices=_int("MAX_DEVICES", 2),
        crypto_pay_token=os.getenv("CRYPTO_PAY_TOKEN", "").strip(),
        price_amount=os.getenv("PRICE_AMOUNT", "1").strip(),
        price_asset=os.getenv("PRICE_ASSET", "USDT").strip(),
        crypto_pay_testnet=os.getenv("CRYPTO_PAY_TESTNET", "0").strip() == "1",
        scripts_dir=Path(os.getenv("SCRIPTS_DIR", "/opt/ruvpn/vpn/server")),
        db_path=Path(os.getenv("DB_PATH", "/var/lib/ruvpn-bot/bot.db")),
        wg_iface=os.getenv("WG_IFACE", "wg0").strip(),
        config_message_ttl_minutes=_int("CONFIG_MESSAGE_TTL_MINUTES", 0),
    )

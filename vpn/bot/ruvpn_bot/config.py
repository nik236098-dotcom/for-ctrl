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
class Plan:
    """Тариф: сколько дней доступа и за какую сумму."""

    days: int
    amount: str

    def label(self, asset: str) -> str:
        return f"{self.days} дн. — {self.amount} {asset}"


def _plans(raw: str) -> tuple[Plan, ...]:
    """Разбирает PLANS вида «30:1,90:2.5,180:4.5»."""
    plans: list[Plan] = []
    for chunk in raw.replace(" ", "").split(","):
        if not chunk:
            continue
        days, _, amount = chunk.partition(":")
        if not days.isdigit() or not amount:
            raise SystemExit(f"PLANS: не разобрал тариф {chunk!r}, нужно «дни:сумма»")
        try:
            float(amount)
        except ValueError as exc:
            raise SystemExit(f"PLANS: сумма {amount!r} не число") from exc
        plans.append(Plan(days=int(days), amount=amount))
    if not plans:
        raise SystemExit("PLANS пуст — боту нечего продавать")
    return tuple(plans)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    trial_days: int
    invite_only: bool
    max_devices: int
    enforce_interval: int

    crypto_pay_token: str
    price_asset: str
    crypto_pay_testnet: bool
    plans: tuple[Plan, ...]

    scripts_dir: Path
    db_path: Path
    wg_iface: str
    config_message_ttl_minutes: int
    apk_url: str

    @property
    def payments_enabled(self) -> bool:
        return bool(self.crypto_pay_token)

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.admin_ids


def load() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан — заполните .env")

    admin_raw = os.getenv("ADMIN_IDS", "").strip()
    admin_ids = frozenset(
        int(part) for part in admin_raw.replace(" ", "").split(",") if part
    )
    if not admin_ids:
        raise SystemExit("ADMIN_IDS не задан — боту некому подчиняться")

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        trial_days=_int("TRIAL_DAYS", 3),
        invite_only=os.getenv("INVITE_ONLY", "0").strip() == "1",
        max_devices=_int("MAX_DEVICES", 2),
        enforce_interval=max(30, _int("ENFORCE_INTERVAL_SECONDS", 60)),
        crypto_pay_token=os.getenv("CRYPTO_PAY_TOKEN", "").strip(),
        price_asset=os.getenv("PRICE_ASSET", "USDT").strip(),
        crypto_pay_testnet=os.getenv("CRYPTO_PAY_TESTNET", "1").strip() == "1",
        plans=_plans(os.getenv("PLANS", "30:1,90:2.5,180:4.5")),
        scripts_dir=Path(os.getenv("SCRIPTS_DIR", "/opt/ruvpn/server")),
        db_path=Path(os.getenv("DB_PATH", "/var/lib/ruvpn-bot/bot.db")),
        wg_iface=os.getenv("WG_IFACE", "wg0").strip(),
        config_message_ttl_minutes=_int("CONFIG_MESSAGE_TTL_MINUTES", 0),
        apk_url=os.getenv("APK_URL", "").strip(),
    )

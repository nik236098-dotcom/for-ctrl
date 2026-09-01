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


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} должен быть числом, получено: {raw!r}") from exc


@dataclass(frozen=True)
class Plan:
    """Тариф: сколько дней доступа и за сколько рублей."""

    days: int
    rub: int

    def label(self) -> str:
        return f"{self.days} {_days_word(self.days)} - {self.rub}Р"


def _days_word(days: int) -> str:
    """Склонение «день/дня/дней» — используется и в тарифах, и в сообщении об оплате."""
    if days % 10 == 1 and days % 100 != 11:
        return "день"
    if 2 <= days % 10 <= 4 and not 12 <= days % 100 <= 14:
        return "дня"
    return "дней"


def _plans(raw: str) -> tuple[Plan, ...]:
    """Разбирает PLANS вида «1:60,3:150,7:250,30:450» (дни:рубли)."""
    plans: list[Plan] = []
    for chunk in raw.replace(" ", "").split(","):
        if not chunk:
            continue
        days, _, rub = chunk.partition(":")
        if not days.isdigit() or not rub.isdigit():
            raise SystemExit(f"PLANS: не разобрал тариф {chunk!r}, нужно «дни:рубли»")
        plans.append(Plan(days=int(days), rub=int(rub)))
    if not plans:
        raise SystemExit("PLANS пуст — боту нечего продавать")
    return tuple(plans)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    max_devices: int
    enforce_interval: int
    telegram_proxy: str

    plans: tuple[Plan, ...]

    # --- оплата -----------------------------------------------------------
    crypto_pay_token: str
    crypto_pay_testnet: bool

    xrocket_api_token: str
    # xRocket не умеет фиатные счета (только крипто-активы) — рублёвую цену
    # тарифа переводим в USDT по этому курсу перед выставлением счёта. У
    # CryptoBot всё проще: там есть настоящий фиатный режим (currency_type
    # "fiat", fiat "RUB"), конвертирует сам CryptoBot по своему курсу.
    xrocket_currency: str
    rub_per_usdt: float

    scripts_dir: Path
    db_path: Path

    @property
    def payments_enabled(self) -> bool:
        return bool(self.crypto_pay_token) or bool(self.xrocket_api_token)

    def is_admin(self, tg_id: int) -> bool:
        return tg_id in self.admin_ids

    def usdt_for(self, plan: Plan) -> str:
        """Сумма тарифа в USDT для xRocket — с округлением до цента."""
        amount = plan.rub / self.rub_per_usdt
        return f"{amount:.2f}"


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
        max_devices=_int("MAX_DEVICES", 2),
        enforce_interval=max(30, _int("ENFORCE_INTERVAL_SECONDS", 60)),
        telegram_proxy=os.getenv("TELEGRAM_PROXY", "").strip(),
        plans=_plans(os.getenv("PLANS", "1:60,3:150,7:250,30:450")),
        crypto_pay_token=os.getenv("CRYPTO_PAY_TOKEN", "").strip(),
        crypto_pay_testnet=os.getenv("CRYPTO_PAY_TESTNET", "1").strip() == "1",
        xrocket_api_token=os.getenv("XROCKET_API_TOKEN", "").strip(),
        xrocket_currency=os.getenv("XROCKET_CURRENCY", "USDT").strip(),
        rub_per_usdt=_float("RUB_PER_USDT", 95.0),
        scripts_dir=Path(os.getenv("SCRIPTS_DIR", "/opt/happ-vpn/server")),
        db_path=Path(os.getenv("DB_PATH", "/var/lib/happ-vpn-bot/bot.db")),
    )

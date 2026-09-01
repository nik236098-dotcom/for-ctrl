"""Точка входа: python -m happ_bot"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import config as config_module
from .config import Config, _days_word
from .cryptobot import CryptoPay, CryptoPayError
from .db import Storage, utcnow
from .handlers import BTN_DEVICES, Deps, notify, resume_devices, router, suspend_devices
from .xray import Xray
from .xrocket import XRocketPay

log = logging.getLogger("happ_bot")

PAYMENT_CHECK_SECONDS = 60
WARN_HOURS_BEFORE = 6


def build_bot(cfg: Config) -> Bot:
    if not cfg.telegram_proxy:
        return Bot(cfg.bot_token)
    return Bot(cfg.bot_token, session=AiohttpSession(proxy=cfg.telegram_proxy))


def load_dotenv(path: Path) -> None:
    try:
        if not path.exists():
            return
        text = path.read_text()
    except PermissionError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


async def enforce_terms(bot: Bot, deps: Deps) -> None:
    """Следит за сроками: истёк — устройства выключаются, оплачен — включаются."""
    while True:
        try:
            for user in deps.db.users():
                devices = deps.db.devices(user.tg_id)
                if not devices:
                    continue
                if user.active:
                    if any(device.suspended for device in devices):
                        resumed = await resume_devices(deps, user.tg_id)
                        if resumed:
                            log.info("включено устройств у %s: %s", user.tg_id, resumed)
                    if (
                        user.expires_at - utcnow() <= timedelta(hours=WARN_HOURS_BEFORE)
                        and user.warned_at is None
                    ):
                        deps.db.mark_warned(user.tg_id)
                        await notify(
                            bot,
                            user.tg_id,
                            "Подписка скоро закончится — продлите через "
                            "«💳Оформить подписку», чтобы устройства не выключились.",
                        )
                elif any(not device.suspended for device in devices):
                    stopped = await suspend_devices(deps, user.tg_id)
                    log.info("срок %s истёк, выключено устройств: %s", user.tg_id, stopped)
                    await notify(
                        bot,
                        user.tg_id,
                        "Срок подписки закончился, устройства выключены.\n"
                        "Оплатите «💳Оформить подписку» — те же ключи включатся сами.",
                    )
        except Exception:
            log.exception("проверка сроков сорвалась")
        await asyncio.sleep(deps.cfg.enforce_interval)


_DEVICES_BUTTON = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text=BTN_DEVICES, callback_data="menu:devices")]]
)


async def _apply_payment(bot: Bot, deps: Deps, row) -> None:
    deps.db.extend(row["tg_id"], row["days"])
    resumed = await resume_devices(deps, row["tg_id"])
    text = f"✅Подписка успешно оформлена на {row['days']} {_days_word(row['days'])}"
    if not resumed:
        text += "\nПодключите новое устройство и начните пользоваться уже сейчас"
    await notify(bot, row["tg_id"], text, reply_markup=_DEVICES_BUTTON)


async def cryptobot_watch(bot: Bot, deps: Deps) -> None:
    assert deps.crypto is not None
    while True:
        try:
            pending = deps.db.pending_payments("cryptobot")
            if pending:
                statuses = await deps.crypto.statuses([row["invoice_id"] for row in pending])
                for row in pending:
                    status = statuses.get(row["invoice_id"])
                    if status == "paid" and deps.db.close_payment(
                        row["invoice_id"], "cryptobot", "paid"
                    ):
                        await _apply_payment(bot, deps, row)
                    elif status == "expired":
                        deps.db.close_payment(row["invoice_id"], "cryptobot", "expired")
        except CryptoPayError as exc:
            log.warning("CryptoBot недоступен: %s", exc)
        except Exception:
            log.exception("проверка платежей CryptoBot сорвалась")
        await asyncio.sleep(PAYMENT_CHECK_SECONDS)


async def xrocket_watch(bot: Bot, deps: Deps) -> None:
    """xRocket отдаёт статус только по одному счёту за раз (нет пакетного
    запроса, в отличие от CryptoBot) — при большом числе одновременно
    висящих счетов это N запросов, но обычно их единицы."""
    assert deps.xrocket is not None
    while True:
        try:
            for row in deps.db.pending_payments("xrocket"):
                status = await deps.xrocket.status(row["invoice_id"])
                if status == "paid" and deps.db.close_payment(
                    row["invoice_id"], "xrocket", "paid"
                ):
                    await _apply_payment(bot, deps, row)
                elif status == "expired":
                    deps.db.close_payment(row["invoice_id"], "xrocket", "expired")
        except Exception:
            log.exception("проверка платежей xRocket сорвалась")
        await asyncio.sleep(PAYMENT_CHECK_SECONDS)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    cfg = config_module.load()
    deps = Deps(
        cfg=cfg,
        db=Storage(cfg.db_path),
        xray=Xray(cfg.scripts_dir),
        crypto=CryptoPay(cfg.crypto_pay_token, cfg.crypto_pay_testnet)
        if cfg.crypto_pay_token
        else None,
        xrocket=XRocketPay(cfg.xrocket_api_token) if cfg.xrocket_api_token else None,
    )

    bot = build_bot(cfg)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)

    tasks = [asyncio.create_task(enforce_terms(bot, deps))]
    if deps.crypto is not None:
        tasks.append(asyncio.create_task(cryptobot_watch(bot, deps)))
        if cfg.crypto_pay_testnet:
            log.warning("CryptoBot в ТЕСТОВОЙ сети — счета идут в @CryptoTestnetBot")
    if deps.xrocket is not None:
        tasks.append(asyncio.create_task(xrocket_watch(bot, deps)))
    if not cfg.payments_enabled:
        log.info("оплата не настроена — доступ продлевает админ командой /extend")

    log.info("бот запущен: тарифов %s, макс. устройств %s", len(cfg.plans), cfg.max_devices)
    try:
        await dispatcher.start_polling(bot, deps=deps)
    finally:
        for task in tasks:
            task.cancel()
        if deps.crypto is not None:
            await deps.crypto.close()
        if deps.xrocket is not None:
            await deps.xrocket.close()
        deps.db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

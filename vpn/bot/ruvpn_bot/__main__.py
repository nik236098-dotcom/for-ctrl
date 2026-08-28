"""Точка входа: python -m ruvpn_bot"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher

from . import config as config_module
from .db import Storage
from .handlers import Deps, revoke_all, router
from .payments import CryptoPay, CryptoPayError
from .wg import Wireguard, WgError

log = logging.getLogger("ruvpn_bot")

EXPIRY_CHECK_SECONDS = 3600
PAYMENT_CHECK_SECONDS = 60
WARN_DAYS_BEFORE = 3


def load_dotenv(path: Path) -> None:
    """Читает .env для запуска руками; под systemd это делает EnvironmentFile."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


async def expiry_watch(bot: Bot, deps: Deps) -> None:
    """Предупреждает об окончании доступа и снимает ключи у просроченных."""
    while True:
        try:
            for user in deps.db.users():
                if user.active:
                    if user.days_left <= WARN_DAYS_BEFORE and user.warned_at is None:
                        deps.db.mark_warned(user.tg_id)
                        await notify(
                            bot,
                            user.tg_id,
                            f"Доступ заканчивается через {user.days_left} дн. "
                            + ("Продлить: /pay" if deps.cfg.payments_enabled else ""),
                        )
                elif deps.db.devices(user.tg_id):
                    count = await revoke_all(deps, user.tg_id)
                    log.info("доступ %s истёк, отозвано ключей: %s", user.tg_id, count)
                    await notify(
                        bot,
                        user.tg_id,
                        "Доступ закончился, ключи отозваны. "
                        + (
                            "Продлить: /pay"
                            if deps.cfg.payments_enabled
                            else "За продлением — к владельцу сервера."
                        ),
                    )
        except WgError as exc:
            log.warning("проверка сроков: сервер ответил ошибкой: %s", exc)
        except Exception:
            log.exception("проверка сроков сорвалась")
        await asyncio.sleep(EXPIRY_CHECK_SECONDS)


async def payments_watch(bot: Bot, deps: Deps) -> None:
    """Опрашивает Crypto Pay: оплаченный счёт продлевает доступ."""
    assert deps.crypto is not None
    while True:
        try:
            pending = deps.db.pending_payments()
            if pending:
                statuses = await deps.crypto.statuses(
                    [row["invoice_id"] for row in pending]
                )
                for row in pending:
                    status = statuses.get(row["invoice_id"])
                    if status == "paid" and deps.db.close_payment(
                        row["invoice_id"], "paid"
                    ):
                        user = deps.db.extend(row["tg_id"], deps.cfg.sub_days)
                        await notify(
                            bot,
                            row["tg_id"],
                            f"Оплата получена, доступ продлён до "
                            f"{user.expires_at.astimezone():%d.%m.%Y}.\n"
                            "Ключ остался прежним — ничего переустанавливать не нужно.",
                        )
                    elif status == "expired":
                        deps.db.close_payment(row["invoice_id"], "expired")
        except CryptoPayError as exc:
            log.warning("Crypto Pay недоступен: %s", exc)
        except Exception:
            log.exception("проверка платежей сорвалась")
        await asyncio.sleep(PAYMENT_CHECK_SECONDS)


async def notify(bot: Bot, tg_id: int, text: str) -> None:
    try:
        await bot.send_message(tg_id, text)
    except Exception as exc:
        # Человек мог заблокировать бота — это не повод падать.
        log.info("не доставлено %s: %s", tg_id, exc)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    cfg = config_module.load()
    deps = Deps(
        cfg=cfg,
        db=Storage(cfg.db_path),
        wg=Wireguard(cfg.scripts_dir, cfg.wg_iface),
        crypto=CryptoPay(cfg.crypto_pay_token, cfg.crypto_pay_testnet)
        if cfg.payments_enabled
        else None,
    )

    bot = Bot(cfg.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    tasks = [asyncio.create_task(expiry_watch(bot, deps))]
    if deps.crypto is not None:
        tasks.append(asyncio.create_task(payments_watch(bot, deps)))
    else:
        log.info("оплата не настроена — доступ продлевает админ командой /extend")

    log.info("бот запущен, админов: %s", len(cfg.admin_ids))
    try:
        await dispatcher.start_polling(bot, deps=deps)
    finally:
        for task in tasks:
            task.cancel()
        if deps.crypto is not None:
            await deps.crypto.close()
        deps.db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

"""Точка входа: python -m ruvpn_bot"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher

from . import config as config_module
from .db import Storage
from .handlers import Deps, notify, resume_devices, router, suspend_devices
from .keyserver import start as start_keyserver
from .payments import CryptoPay, CryptoPayError
from .wg import Wireguard

log = logging.getLogger("ruvpn_bot")

PAYMENT_CHECK_SECONDS = 60
WARN_DAYS_BEFORE = 3


def load_dotenv(path: Path) -> None:
    """Читает .env для запуска руками; под systemd это делает EnvironmentFile.

    .env специально root:600 — токен бота не должен читать никто, кроме
    root. Systemd сам подставляет переменные процессу до сброса прав
    (EnvironmentFile=), а вот этот ручной путь читает файл уже от имени
    урезанного ruvpnbot и на боевом сервере закономерно получит
    PermissionError — под systemd так и должно быть, тут просто нечего
    делать (переменные уже на месте), а не падать.
    """
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
    """Следит за сроками: истёк — ключи выключаются, оплачен — включаются.

    Крутится каждую минуту, поэтому «лишнего» времени человек получает
    минуты, а не сутки.
    """
    while True:
        try:
            for user in deps.db.users():
                devices = deps.db.devices(user.tg_id)
                if not devices:
                    continue

                if user.active:
                    # Страховка на случай, если оплату применили, а ключи
                    # включить не вышло: тихо доводим до нужного состояния.
                    if any(device.suspended for device in devices):
                        resumed = await resume_devices(deps, user.tg_id)
                        if resumed:
                            log.info("включено ключей у %s: %s", user.tg_id, resumed)
                    if user.days_left <= WARN_DAYS_BEFORE and user.warned_at is None:
                        deps.db.mark_warned(user.tg_id)
                        await notify(
                            bot,
                            user.tg_id,
                            f"Доступ заканчивается: {user.days_left} дн. "
                            "Продлить — /pay",
                        )
                elif any(not device.suspended for device in devices):
                    stopped = await suspend_devices(deps, user.tg_id)
                    log.info("срок %s истёк, выключено ключей: %s", user.tg_id, stopped)
                    await notify(
                        bot,
                        user.tg_id,
                        "Срок доступа закончился, ключи выключены.\n"
                        "Оплатите /pay — те же ключи включатся сами, "
                        "переустанавливать ничего не нужно.",
                    )
        except Exception:
            log.exception("проверка сроков сорвалась")
        await asyncio.sleep(deps.cfg.enforce_interval)


async def payments_watch(bot: Bot, deps: Deps) -> None:
    """Опрашивает CryptoBot: оплаченный счёт продлевает доступ и включает ключи."""
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
                        user = deps.db.extend(row["tg_id"], row["days"])
                        resumed = await resume_devices(deps, row["tg_id"])
                        text = (
                            f"Оплата получена, доступ до "
                            f"{user.expires_at.astimezone():%d.%m.%Y %H:%M}."
                        )
                        text += (
                            "\nКлючи снова работают — те же самые."
                            if resumed
                            else "\n/key — получить ключ."
                        )
                        await notify(bot, row["tg_id"], text)
                    elif status == "expired":
                        deps.db.close_payment(row["invoice_id"], "expired")
        except CryptoPayError as exc:
            log.warning("CryptoBot недоступен: %s", exc)
        except Exception:
            log.exception("проверка платежей сорвалась")
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
        wg=Wireguard(cfg.scripts_dir, cfg.wg_iface),
        crypto=CryptoPay(cfg.crypto_pay_token, cfg.crypto_pay_testnet)
        if cfg.payments_enabled
        else None,
    )

    bot = Bot(cfg.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    keyserver_runner = await start_keyserver(
        deps.db, cfg.key_server_host, cfg.key_server_port
    )

    tasks = [asyncio.create_task(enforce_terms(bot, deps))]
    if deps.crypto is not None:
        tasks.append(asyncio.create_task(payments_watch(bot, deps)))
        if cfg.crypto_pay_testnet:
            log.warning("оплата в ТЕСТОВОЙ сети — счета идут в @CryptoTestnetBot")
    else:
        log.info("оплата не настроена — доступ продлевает админ командой /extend")

    log.info(
        "бот запущен: тарифов %s, пробных дней %s, проверка сроков раз в %s с",
        len(cfg.plans),
        cfg.trial_days,
        cfg.enforce_interval,
    )
    try:
        await dispatcher.start_polling(bot, deps=deps)
    finally:
        for task in tasks:
            task.cancel()
        await keyserver_runner.cleanup()
        if deps.crypto is not None:
            await deps.crypto.close()
        deps.db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

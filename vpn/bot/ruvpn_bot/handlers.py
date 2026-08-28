"""Команды бота: тарифы, оплата, выдача ключей, админка."""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import Config, Plan
from .db import Storage, User
from .payments import CryptoPay, CryptoPayError
from .wg import Wireguard, WgError, human_bytes, qr_png

log = logging.getLogger(__name__)
router = Router()


@dataclass
class Deps:
    cfg: Config
    db: Storage
    wg: Wireguard
    crypto: CryptoPay | None


# --- вспомогательное --------------------------------------------------------


def _device_name(tg_id: int, index: int) -> str:
    return f"tg{tg_id}" if index == 0 else f"tg{tg_id}-{index + 1}"


def _left(user: User) -> str:
    """Сколько осталось — по-человечески, вплоть до минут в последний час."""
    delta = user.expires_at - datetime.now(user.expires_at.tzinfo)
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "срок истёк"
    if seconds >= 86400:
        return f"осталось дней: {seconds // 86400}"
    if seconds >= 3600:
        return f"осталось часов: {seconds // 3600}"
    return f"осталось минут: {max(1, seconds // 60)}"


def _expiry_line(user: User) -> str:
    when = user.expires_at.astimezone().strftime("%d.%m.%Y %H:%M")
    if not user.active:
        return f"Доступ закончился {when}."
    return f"Доступ до {when} ({_left(user)})."


def plans_keyboard(cfg: Config) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=plan.label(cfg.price_asset), callback_data=f"pay:{index}"
            )
        ]
        for index, plan in enumerate(cfg.plans)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _greeting(cfg: Config) -> str:
    lines = [
        "VPN с российским IP: сайты и приложения видят вас из России.",
        "",
        "Тарифы:",
    ]
    lines += [f"• {plan.label(cfg.price_asset)}" for plan in cfg.plans]
    if cfg.trial_days:
        lines += ["", f"Первые {cfg.trial_days} дн. — бесплатно, чтобы попробовать."]
    return "\n".join(lines)


# --- вход -------------------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, deps: Deps) -> None:
    tg_id = message.from_user.id
    user = deps.db.user(tg_id)

    if user is None:
        code = (command.args or "").strip()
        if deps.cfg.invite_only and not deps.cfg.is_admin(tg_id):
            if not code:
                await message.answer(
                    "Сейчас доступ по приглашениям. Пришлите инвайт-код."
                )
                return
            if not deps.db.use_invite(code, tg_id):
                await message.answer("Код не подошёл: его нет или он уже использован.")
                return
        elif code:
            deps.db.use_invite(code, tg_id)

        user = deps.db.create_user(
            tg_id, message.from_user.username, deps.cfg.trial_days
        )
        greeting = _greeting(deps.cfg)
        if deps.cfg.trial_days:
            await message.answer(
                greeting + "\n\nПробный доступ включён. /key — получить ключ."
            )
        else:
            await message.answer(
                greeting + "\n\nВыберите тариф — и сразу получите ключ.",
                reply_markup=plans_keyboard(deps.cfg),
            )
        return

    await cmd_status(message, deps)


@router.message(Command("status"))
async def cmd_status(message: Message, deps: Deps) -> None:
    user = deps.db.user(message.from_user.id)
    if user is None:
        await message.answer("Нажмите /start, чтобы начать.")
        return

    devices = deps.db.devices(user.tg_id)
    lines = [_expiry_line(user)]

    if devices:
        traffic = {}
        try:
            traffic = await deps.wg.transfer_by_ip()
        except WgError as exc:
            log.warning("не удалось прочитать счётчики: %s", exc)

        lines += ["", "Ключи:"]
        for number, device in enumerate(devices, start=1):
            used = traffic.get(device.ip or "")
            parts = [f"{number}. {html.escape(device.title)}"]
            if device.suspended:
                parts.append("— приостановлен")
            elif used is not None:
                parts.append(f"— ↓{human_bytes(used.received)} ↑{human_bytes(used.sent)}")
            lines.append(" ".join(parts))
        lines += ["", "/key — ещё ключ, /drop N — удалить ключ №N"]
    else:
        lines += ["", "Ключей пока нет: /key — получить."]

    if not user.active:
        lines += ["", "Ключи выключены. Оплатите — включатся сами, те же самые."]

    await message.answer(
        "\n".join(lines),
        reply_markup=plans_keyboard(deps.cfg) if deps.cfg.payments_enabled else None,
    )


@router.message(F.text.regexp(r"^[A-Za-z0-9_-]{8,32}$"))
async def plain_invite_code(message: Message, deps: Deps) -> None:
    """Инвайт можно прислать сообщением, без /start."""
    if deps.db.user(message.from_user.id) is not None:
        await cmd_status(message, deps)
        return
    if not deps.db.use_invite(message.text.strip(), message.from_user.id):
        await message.answer("Код не подошёл: его нет или он уже использован.")
        return
    deps.db.create_user(
        message.from_user.id, message.from_user.username, deps.cfg.trial_days
    )
    await message.answer(
        f"Доступ открыт на {deps.cfg.trial_days} дн.\n\n/key — получить ключ."
        if deps.cfg.trial_days
        else "Приглашение принято. Выберите тариф: /pay"
    )


# --- ключи ------------------------------------------------------------------


@router.message(Command("key"))
async def cmd_key(message: Message, command: CommandObject, deps: Deps) -> None:
    user = deps.db.user(message.from_user.id)
    if user is None:
        await message.answer("Нажмите /start, чтобы начать.")
        return

    if not user.active:
        await message.answer(
            _expiry_line(user) + "\n\nВыберите тариф — и ключи включатся.",
            reply_markup=plans_keyboard(deps.cfg),
        )
        return

    devices = deps.db.devices(user.tg_id)
    if len(devices) >= deps.cfg.max_devices:
        await message.answer(
            f"Уже выдано ключей: {len(devices)} из {deps.cfg.max_devices}.\n"
            "Удалите лишний командой /drop N — и берите новый."
        )
        return

    title = ((command.args or "").strip() or f"устройство {len(devices) + 1}")[:40]

    used = {
        device.client_name
        for device in deps.db.devices(user.tg_id, include_revoked=True)
    }
    index = 0
    while _device_name(user.tg_id, index) in used:
        index += 1
    name = _device_name(user.tg_id, index)

    try:
        ip = await deps.wg.add_client(name)
        config_text = await deps.wg.client_config(name)
    except WgError as exc:
        log.exception("не удалось выдать ключ %s", name)
        await message.answer(f"Сервер не смог выдать ключ: {exc}")
        return

    deps.db.add_device(name, user.tg_id, title, ip)
    await send_config(message, deps, title, config_text)


async def send_config(message: Message, deps: Deps, title: str, config_text: str) -> None:
    """Отдаёт конфиг файлом и QR-кодом. В конфиге лежит приватный ключ."""
    document = BufferedInputFile(
        config_text.encode(), filename=f"ruvpn-{title}.conf".replace(" ", "-")
    )
    sent = await message.answer_document(
        document,
        caption=(
            f"Ключ для «{html.escape(title)}».\n\n"
            "В приложении RU VPN: «Взять конфиг из файла».\n"
            "В приложении WireGuard: «+» → импорт файла или скан QR ниже.\n\n"
            "Ключ личный: на другом устройстве он не заработает одновременно."
        ),
    )

    png = await qr_png(config_text)
    if png is not None:
        await message.answer_photo(
            BufferedInputFile(png, filename="ruvpn-qr.png"),
            caption="Тот же ключ QR-кодом",
        )

    ttl = deps.cfg.config_message_ttl_minutes
    if ttl > 0:
        await message.answer(f"Сообщение с ключом удалю из чата через {ttl} мин.")
        asyncio.create_task(_delete_later(message.bot, sent.chat.id, sent.message_id, ttl))


async def _delete_later(bot: Bot, chat_id: int, message_id: int, minutes: int) -> None:
    await asyncio.sleep(minutes * 60)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception as exc:  # сообщение могли удалить руками — это нормально
        log.debug("не удалось удалить сообщение %s: %s", message_id, exc)


@router.message(Command("drop"))
async def cmd_drop(message: Message, command: CommandObject, deps: Deps) -> None:
    user = deps.db.user(message.from_user.id)
    if user is None:
        await message.answer("Нажмите /start, чтобы начать.")
        return

    devices = deps.db.devices(user.tg_id)
    raw = (command.args or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= len(devices):
        await message.answer("Укажите номер ключа из /status, например: /drop 1")
        return

    device = devices[int(raw) - 1]
    try:
        await deps.wg.remove_client(device.client_name)
    except WgError as exc:
        await message.answer(f"Не удалось удалить: {exc}")
        return

    deps.db.mark_device_revoked(device.client_name)
    await message.answer(f"Ключ «{html.escape(device.title)}» удалён навсегда.")


# --- оплата -----------------------------------------------------------------


@router.message(Command("pay"))
async def cmd_pay(message: Message, deps: Deps) -> None:
    if not deps.cfg.payments_enabled:
        await message.answer("Оплата пока не подключена — напишите владельцу сервиса.")
        return
    if deps.db.user(message.from_user.id) is None:
        await message.answer("Нажмите /start, чтобы начать.")
        return
    await message.answer("Выберите тариф:", reply_markup=plans_keyboard(deps.cfg))


@router.callback_query(F.data.startswith("pay:"))
async def on_plan_chosen(callback: CallbackQuery, deps: Deps) -> None:
    await callback.answer()

    if deps.crypto is None:
        await callback.message.answer("Оплата не подключена.")
        return

    user = deps.db.user(callback.from_user.id)
    if user is None:
        await callback.message.answer("Нажмите /start, чтобы начать.")
        return

    index = int(callback.data.split(":", 1)[1])
    if not 0 <= index < len(deps.cfg.plans):
        return
    plan: Plan = deps.cfg.plans[index]

    try:
        invoice = await deps.crypto.create_invoice(
            amount=plan.amount,
            asset=deps.cfg.price_asset,
            description=f"VPN, доступ на {plan.days} дн.",
            payload=str(user.tg_id),
        )
    except CryptoPayError as exc:
        log.exception("CryptoBot не выставил счёт")
        await callback.message.answer(f"Не удалось выставить счёт: {exc}")
        return

    deps.db.add_payment(
        invoice.invoice_id, user.tg_id, invoice.amount, invoice.asset, plan.days
    )
    await callback.message.answer(
        f"Счёт на {invoice.amount} {invoice.asset} за {plan.days} дн.:\n"
        f"{invoice.pay_url}\n\n"
        "Оплата проходит в @CryptoBot. Как оплатите — доступ продлится "
        "в течение минуты, ключи включатся сами."
    )


# --- админка ----------------------------------------------------------------


@router.message(Command("invite"))
async def cmd_invite(message: Message, command: CommandObject, deps: Deps) -> None:
    if not deps.cfg.is_admin(message.from_user.id):
        return

    raw = (command.args or "").strip()
    count = int(raw) if raw.isdigit() and 1 <= int(raw) <= 20 else 1
    me = await message.bot.me()

    lines = ["Инвайты (одноразовые):", ""]
    for _ in range(count):
        code = deps.db.create_invite()
        lines += [f"<code>{code}</code>", f"https://t.me/{me.username}?start={code}", ""]
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("users"))
async def cmd_users(message: Message, deps: Deps) -> None:
    if not deps.cfg.is_admin(message.from_user.id):
        return

    users = deps.db.users()
    if not users:
        await message.answer("Пока никого.")
        return

    lines = []
    for user in users:
        who = f"@{user.username}" if user.username else str(user.tg_id)
        mark = "✅" if user.active else "⛔"
        devices = deps.db.devices(user.tg_id)
        lines.append(
            f"{mark} {html.escape(who)} (<code>{user.tg_id}</code>) — "
            f"до {user.expires_at.astimezone():%d.%m.%Y %H:%M}, ключей: {len(devices)}"
        )
    lines += ["", f"Неиспользованных инвайтов: {len(deps.db.unused_invites())}"]
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("extend"))
async def cmd_extend(message: Message, command: CommandObject, deps: Deps) -> None:
    if not deps.cfg.is_admin(message.from_user.id):
        return

    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].lstrip("-").isdigit() or not parts[1].isdigit():
        await message.answer("Формат: /extend <tg_id> <дней>")
        return

    tg_id, days = int(parts[0]), int(parts[1])
    try:
        user = deps.db.extend(tg_id, days)
    except KeyError:
        await message.answer("Такого пользователя нет.")
        return

    await message.answer(f"Продлено. {_expiry_line(user)}")
    await notify(message.bot, tg_id, f"Доступ продлён на {days} дн. {_expiry_line(user)}")


@router.message(Command("kick"))
async def cmd_kick(message: Message, command: CommandObject, deps: Deps) -> None:
    if not deps.cfg.is_admin(message.from_user.id):
        return

    raw = (command.args or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer("Формат: /kick <tg_id>")
        return

    tg_id = int(raw)
    if deps.db.user(tg_id) is None:
        await message.answer("Такого пользователя нет.")
        return

    deps.db.expire_now(tg_id)
    stopped = await suspend_devices(deps, tg_id)
    await message.answer(f"Доступ закрыт, ключей выключено: {stopped}.")


@router.message(Command("stats"))
async def cmd_stats(message: Message, deps: Deps) -> None:
    if not deps.cfg.is_admin(message.from_user.id):
        return

    users = deps.db.users()
    active = [user for user in users if user.active]
    devices = deps.db.active_devices()
    live = [device for device in devices if not device.suspended]

    try:
        traffic = await deps.wg.transfer_by_ip()
    except WgError as exc:
        await message.answer(f"Счётчики недоступны: {exc}")
        traffic = {}

    lines = [
        f"Людей: {len(active)} активных из {len(users)}",
        f"Ключей: {len(live)} работают, {len(devices) - len(live)} приостановлено",
        f"Трафик с рестарта сервера: "
        f"↓{human_bytes(sum(item.received for item in traffic.values()))} "
        f"↑{human_bytes(sum(item.sent for item in traffic.values()))}",
    ]
    totals = deps.db.paid_totals()
    if totals:
        lines.append("")
        lines.append("Получено:")
        lines += [
            f"• {total} {asset} за {count} платежей" for asset, total, count in totals
        ]
    await message.answer("\n".join(lines))


# --- операции над ключами, общие для команд и фоновых задач ------------------


async def suspend_devices(deps: Deps, tg_id: int) -> int:
    """Выключает все работающие ключи пользователя. Ключи сохраняются."""
    count = 0
    for device in deps.db.devices(tg_id):
        if device.suspended:
            continue
        try:
            await deps.wg.suspend_client(device.client_name)
        except WgError as exc:
            log.warning("не удалось выключить %s: %s", device.client_name, exc)
            continue
        deps.db.mark_device_suspended(device.client_name, True)
        count += 1
    return count


async def resume_devices(deps: Deps, tg_id: int) -> int:
    """Включает обратно ключи после оплаты."""
    count = 0
    for device in deps.db.devices(tg_id):
        if not device.suspended:
            continue
        try:
            await deps.wg.resume_client(device.client_name)
        except WgError as exc:
            log.warning("не удалось включить %s: %s", device.client_name, exc)
            continue
        deps.db.mark_device_suspended(device.client_name, False)
        count += 1
    return count


async def notify(bot: Bot, tg_id: int, text: str) -> None:
    try:
        await bot.send_message(tg_id, text)
    except Exception as exc:
        # Человек мог заблокировать бота — это не повод падать.
        log.info("не доставлено %s: %s", tg_id, exc)

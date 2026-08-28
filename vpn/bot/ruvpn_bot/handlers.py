"""Команды бота: выдача ключей, статус, продление, админка."""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BufferedInputFile, Message

from .config import Config
from .db import Storage, User
from .payments import CryptoPay, CryptoPayError
from .wg import Wireguard, WgError, human_bytes, qr_png

log = logging.getLogger(__name__)
router = Router()

INVITE_HINT = (
    "Это закрытый VPN.\n\n"
    "Чтобы получить доступ, пришлите инвайт-код — его выдаёт владелец сервера."
)


@dataclass
class Deps:
    cfg: Config
    db: Storage
    wg: Wireguard
    crypto: CryptoPay | None


def _device_name(tg_id: int, index: int) -> str:
    return f"tg{tg_id}" if index == 0 else f"tg{tg_id}-{index + 1}"


def _expiry_line(user: User) -> str:
    when = user.expires_at.astimezone().strftime("%d.%m.%Y")
    if not user.active:
        return f"Доступ закончился {when}."
    return f"Доступ до {when} (осталось дней: {user.days_left})."


async def send_config(
    message: Message, deps: Deps, name: str, title: str, config_text: str
) -> None:
    """Отдаёт конфиг файлом и QR-кодом. В конфиге лежит приватный ключ."""
    document = BufferedInputFile(
        config_text.encode(), filename=f"ruvpn-{title}.conf".replace(" ", "-")
    )
    caption = (
        f"Ключ для «{html.escape(title)}».\n\n"
        "В приложении RU VPN: «Взять конфиг из файла».\n"
        "В приложении WireGuard: «+» → «Импорт из файла» или скан QR ниже.\n\n"
        "Это ваш личный ключ — не передавайте его никому."
    )
    sent = await message.answer_document(document, caption=caption)

    png = await qr_png(config_text)
    if png is not None:
        await message.answer_photo(
            BufferedInputFile(png, filename="ruvpn-qr.png"),
            caption="Тот же ключ QR-кодом",
        )

    ttl = deps.cfg.config_message_ttl_minutes
    if ttl > 0:
        await message.answer(f"Сообщения с ключом удалю из чата через {ttl} мин.")
        asyncio.create_task(_delete_later(message.bot, sent.chat.id, sent.message_id, ttl))


async def _delete_later(bot: Bot, chat_id: int, message_id: int, minutes: int) -> None:
    await asyncio.sleep(minutes * 60)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception as exc:  # сообщение могли удалить руками — это нормально
        log.debug("не удалось удалить сообщение %s: %s", message_id, exc)


# --- вход -----------------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, deps: Deps) -> None:
    tg_id = message.from_user.id
    user = deps.db.user(tg_id)

    if user is None and command.args:
        await _activate(message, deps, command.args.strip())
        return

    if user is None and deps.cfg.is_admin(tg_id):
        user = deps.db.create_user(tg_id, message.from_user.username, deps.cfg.sub_days)

    if user is None:
        await message.answer(INVITE_HINT)
        return

    await cmd_status(message, deps)


@router.message(Command("status"))
async def cmd_status(message: Message, deps: Deps) -> None:
    user = deps.db.user(message.from_user.id)
    if user is None:
        await message.answer(INVITE_HINT)
        return

    devices = deps.db.devices(user.tg_id)
    lines = [_expiry_line(user)]

    if devices:
        traffic = {}
        try:
            traffic = await deps.wg.transfer_by_ip()
        except WgError as exc:
            log.warning("не удалось прочитать счётчики: %s", exc)
        lines.append("")
        lines.append("Ключи:")
        for number, device in enumerate(devices, start=1):
            used = traffic.get(device.ip or "")
            suffix = ""
            if used is not None:
                suffix = f" — ↓{human_bytes(used.received)} ↑{human_bytes(used.sent)}"
            lines.append(f"{number}. {html.escape(device.title)}{suffix}")
        lines.append("")
        lines.append("/key — новый ключ, /drop N — отозвать ключ №N")
    else:
        lines.append("")
        lines.append("Ключей пока нет: /key — получить.")

    if deps.cfg.payments_enabled:
        lines.append(
            f"/pay — продлить на {deps.cfg.sub_days} дн. "
            f"({deps.cfg.price_amount} {deps.cfg.price_asset})"
        )

    await message.answer("\n".join(lines))


async def _activate(message: Message, deps: Deps, code: str) -> None:
    if not deps.db.use_invite(code, message.from_user.id):
        await message.answer("Код не подошёл: его нет или он уже использован.")
        return

    deps.db.create_user(
        message.from_user.id, message.from_user.username, deps.cfg.sub_days
    )
    await message.answer(
        f"Доступ открыт на {deps.cfg.sub_days} дн.\n\n"
        "Команда /key выдаст ключ для устройства."
    )


@router.message(F.text.regexp(r"^[A-Za-z0-9_-]{8,32}$"))
async def plain_invite_code(message: Message, deps: Deps) -> None:
    """Инвайт можно просто прислать сообщением, без /start."""
    if deps.db.user(message.from_user.id) is not None:
        await cmd_status(message, deps)
        return
    await _activate(message, deps, message.text.strip())


# --- ключи ----------------------------------------------------------------


@router.message(Command("key"))
async def cmd_key(message: Message, command: CommandObject, deps: Deps) -> None:
    user = deps.db.user(message.from_user.id)
    if user is None:
        await message.answer(INVITE_HINT)
        return
    if not user.active:
        await message.answer(
            _expiry_line(user) + "\n\nПродлите доступ — и ключ снова заработает."
        )
        return

    devices = deps.db.devices(user.tg_id)
    if len(devices) >= deps.cfg.max_devices:
        await message.answer(
            f"Уже выдано ключей: {len(devices)} из {deps.cfg.max_devices}.\n"
            "Отзовите лишний командой /drop N — и берите новый."
        )
        return

    title = (command.args or "").strip() or f"устройство {len(devices) + 1}"
    if len(title) > 40:
        title = title[:40]

    index = 0
    used = {device.client_name for device in deps.db.devices(user.tg_id, include_revoked=True)}
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
    await send_config(message, deps, name, title, config_text)


@router.message(Command("drop"))
async def cmd_drop(message: Message, command: CommandObject, deps: Deps) -> None:
    user = deps.db.user(message.from_user.id)
    if user is None:
        await message.answer(INVITE_HINT)
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
        await message.answer(f"Не удалось отозвать: {exc}")
        return

    deps.db.mark_device_revoked(device.client_name)
    await message.answer(f"Ключ «{html.escape(device.title)}» отозван.")


# --- оплата ---------------------------------------------------------------


@router.message(Command("pay"))
async def cmd_pay(message: Message, deps: Deps) -> None:
    if deps.crypto is None:
        await message.answer(
            "Оплата не подключена — о продлении договоритесь с владельцем сервера."
        )
        return

    user = deps.db.user(message.from_user.id)
    if user is None:
        await message.answer(INVITE_HINT)
        return

    try:
        invoice = await deps.crypto.create_invoice(
            amount=deps.cfg.price_amount,
            asset=deps.cfg.price_asset,
            description=f"RU VPN: доступ на {deps.cfg.sub_days} дн.",
            payload=str(user.tg_id),
        )
    except CryptoPayError as exc:
        log.exception("Crypto Pay не выставил счёт")
        await message.answer(f"Не удалось выставить счёт: {exc}")
        return

    deps.db.add_payment(invoice.invoice_id, user.tg_id, invoice.amount, invoice.asset)
    await message.answer(
        f"Счёт на {invoice.amount} {invoice.asset} за {deps.cfg.sub_days} дн.:\n"
        f"{invoice.pay_url}\n\n"
        "Как оплатите — доступ продлится сам, в течение минуты."
    )


# --- админка --------------------------------------------------------------


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
        lines.append(f"<code>{code}</code>")
        lines.append(f"https://t.me/{me.username}?start={code}")
        lines.append("")
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
        devices = len(deps.db.devices(user.tg_id))
        lines.append(
            f"{mark} {html.escape(who)} (<code>{user.tg_id}</code>) — "
            f"до {user.expires_at.astimezone():%d.%m.%Y}, ключей: {devices}"
        )
    unused = deps.db.unused_invites()
    lines.append("")
    lines.append(f"Неиспользованных инвайтов: {len(unused)}")
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
    try:
        await message.bot.send_message(
            tg_id, f"Доступ продлён на {days} дн. {_expiry_line(user)}"
        )
    except Exception as exc:
        log.info("не смог уведомить %s: %s", tg_id, exc)


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

    revoked = await revoke_all(deps, tg_id)
    deps.db.expire_now(tg_id)
    await message.answer(f"Доступ закрыт, ключей отозвано: {revoked}.")


@router.message(Command("stats"))
async def cmd_stats(message: Message, deps: Deps) -> None:
    if not deps.cfg.is_admin(message.from_user.id):
        return

    users = deps.db.users()
    active = [user for user in users if user.active]
    devices = deps.db.active_devices()

    try:
        traffic = await deps.wg.transfer_by_ip()
    except WgError as exc:
        await message.answer(f"Счётчики недоступны: {exc}")
        traffic = {}

    total_rx = sum(item.received for item in traffic.values())
    total_tx = sum(item.sent for item in traffic.values())

    lines = [
        f"Людей: {len(active)} активных из {len(users)}",
        f"Ключей выдано: {len(devices)}",
        f"Трафик с последнего рестарта: ↓{human_bytes(total_rx)} ↑{human_bytes(total_tx)}",
    ]
    await message.answer("\n".join(lines))


async def revoke_all(deps: Deps, tg_id: int) -> int:
    """Снимает все ключи пользователя с сервера. Возвращает число отозванных."""
    count = 0
    for device in deps.db.devices(tg_id):
        try:
            await deps.wg.remove_client(device.client_name)
        except WgError as exc:
            log.warning("не удалось отозвать %s: %s", device.client_name, exc)
            continue
        deps.db.mark_device_revoked(device.client_name)
        count += 1
    return count

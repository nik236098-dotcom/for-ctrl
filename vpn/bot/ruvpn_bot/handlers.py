"""Команды бота: тарифы, оплата, выдача ключей, админка."""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import string
from dataclasses import dataclass
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import Config, Plan
from .db import Device, Storage, User
from .payments import CryptoPay, CryptoPayError
from .wg import Wireguard, WgError, human_bytes

log = logging.getLogger(__name__)
router = Router()


@dataclass(frozen=True)
class ServerInfo:
    """Один сервер WireGuard: локальный (Россия) или по SSH (см. wg.SshTarget)."""

    label: str
    wg: Wireguard


@dataclass
class Deps:
    cfg: Config
    db: Storage
    servers: dict[str, ServerInfo]
    crypto: CryptoPay | None

    @property
    def wg(self) -> Wireguard:
        """Домашний сервер (Россия) — им пользуются все места, которые
        трогают только один сервер по умолчанию: выдача первого ключа,
        /status и /stats (счётчики трафика там — только по России, у
        «зеркал» в других странах своя статистика на другом сервере,
        отдельно не агрегируется)."""
        return self.servers["ru"].wg


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


def key_keyboard(has_devices: bool = False) -> InlineKeyboardMarkup:
    text = "🔑 Ещё ключ" if has_devices else "🔑 Получить ключ"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data="get_key")]]
    )


def status_keyboard(cfg: Config, has_devices: bool) -> InlineKeyboardMarkup:
    """Кнопка ключа + (если оплата настроена) тарифы — одной клавиатурой."""
    rows = list(key_keyboard(has_devices).inline_keyboard)
    if cfg.payments_enabled:
        rows += plans_keyboard(cfg).inline_keyboard
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
                greeting + "\n\nПробный доступ включён.",
                reply_markup=key_keyboard(),
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
        lines += ["", "/drop N — удалить ключ №N"]

    if not user.active:
        lines += ["", "Ключи выключены. Оплатите — включатся сами, те же самые."]

    await message.answer(
        "\n".join(lines),
        reply_markup=status_keyboard(deps.cfg, bool(devices)),
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
        f"Доступ открыт на {deps.cfg.trial_days} дн."
        if deps.cfg.trial_days
        else "Приглашение принято. Выберите тариф:",
        reply_markup=key_keyboard() if deps.cfg.trial_days else plans_keyboard(deps.cfg),
    )


# --- ключи ------------------------------------------------------------------


@router.message(Command("key"))
async def cmd_key(message: Message, command: CommandObject, deps: Deps) -> None:
    await issue_key(message, deps, message.from_user.id, command.args or "")


@router.callback_query(F.data == "get_key")
async def cb_get_key(callback: CallbackQuery, deps: Deps) -> None:
    """Та же выдача ключа, но по кнопке — не всем удобно печатать /key."""
    await callback.answer()
    if callback.message is not None:
        await issue_key(callback.message, deps, callback.from_user.id, "")


async def issue_key(message: Message, deps: Deps, tg_id: int, title_arg: str) -> None:
    """Общая логика выдачи ключа — дёргается и из /key, и из кнопки «Получить ключ».

    Кнопка присылает нажатие через CallbackQuery, где message — это
    сообщение БОТА (у него message.from_user был бы сам бот), поэтому tg_id
    настоящего человека передаём отдельно, а не берём из message.
    """
    user = deps.db.user(tg_id)
    if user is None:
        await message.answer("Нажмите /start, чтобы начать.")
        return

    if not user.active:
        await message.answer(
            _expiry_line(user) + "\n\nВыберите тариф — и ключи включатся.",
            reply_markup=plans_keyboard(deps.cfg),
        )
        return

    # Только «домашние» (Россия) — «зеркало» в другой стране открывается тем
    # же самым ключом (см. resolve_region_config), это не второй купленный
    # ключ и в лимит max_devices не должно считаться.
    devices = deps.db.devices(tg_id, country="ru")
    if len(devices) >= deps.cfg.max_devices:
        await message.answer(
            f"Уже выдано ключей: {len(devices)} из {deps.cfg.max_devices}.\n"
            "Удалите лишний командой /drop N — и берите новый."
        )
        return

    title = (title_arg.strip() or f"устройство {len(devices) + 1}")[:40]

    used = {
        device.client_name
        for device in deps.db.devices(tg_id, include_revoked=True, country="ru")
    }
    index = 0
    while _device_name(tg_id, index) in used:
        index += 1
    name = _device_name(tg_id, index)

    try:
        ip = await deps.wg.add_client(name)
        config_text = await deps.wg.client_config(name)
    except WgError as exc:
        log.exception("не удалось выдать ключ %s", name)
        await message.answer(f"Сервер не смог выдать ключ: {exc}")
        return

    deps.db.add_device(name, tg_id, title, ip)
    await send_key(message, deps, name, title, config_text)


_KEY_ALPHABET = string.ascii_letters + string.digits
_KEY_LENGTH = 8


def make_key(deps: Deps, client_name: str, config_text: str) -> str:
    """Короткий ключ — 8 букв и цифр вперемешку, вот и всё, что видит человек.

    Само содержимое туннеля (приватный ключ, адрес сервера) в 8 символов не
    упаковать — оно остаётся на сервере, а ключ лишь указывает на запись:
    приложение вставляет его один раз, конфиг сервер ключей отдаёт сам.

    Без префикса вида ruvpn:// — он приложению не нужен: короткие
    буквенно-цифровые коды оно и так узнаёт как ключ (см. KeyStore.kt).
    """
    while True:
        code = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_LENGTH))
        if not deps.db.key_exists(code):
            break
    deps.db.store_key(code, client_name, config_text)
    return code


async def send_key(
    message: Message, deps: Deps, client_name: str, title: str, config_text: str
) -> None:
    """Отдаёт ключ строкой — нажатие копирует его целиком."""
    key = make_key(deps, client_name, config_text)
    app_line = (
        f"\n\nПриложение: {deps.cfg.apk_url}" if deps.cfg.apk_url else ""
    )
    sent = await message.answer(
        f"Ключ для «{html.escape(title)}». Нажмите на него — скопируется:\n\n"
        f"<code>{key}</code>\n\n"
        "Дальше: откройте приложение → «Вставить ключ» → «Соединить»."
        f"{app_line}",
        parse_mode="HTML",
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
    server = deps.servers.get(device.country)
    if server is None:
        await message.answer(
            f"Сервер «{device.country}» сейчас не настроен — обратитесь к владельцу."
        )
        return
    try:
        await server.wg.remove_client(device.client_name)
    except WgError as exc:
        await message.answer(f"Не удалось удалить: {exc}")
        return

    deps.db.mark_device_revoked(device.client_name)
    # Если это было «зеркало» в другой стране — сотрём и закэшированный
    # конфиг, иначе сервер ключей продолжил бы отдавать конфиг уже снятого
    # пира (см. resolve_region_config).
    region = deps.db.key_region_by_client_name(device.client_name)
    if region is not None:
        deps.db.delete_key_region(*region)
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


def _server_for(deps: Deps, device: Device) -> ServerInfo | None:
    server = deps.servers.get(device.country)
    if server is None:
        log.warning(
            "сервер «%s» не настроен, пропускаю устройство %s",
            device.country,
            device.client_name,
        )
    return server


async def suspend_devices(deps: Deps, tg_id: int) -> int:
    """Выключает все работающие ключи пользователя — на всех серверах
    разом (иначе смена страны в приложении обходила бы отключение за
    неуплату). Ключи сохраняются."""
    count = 0
    for device in deps.db.devices(tg_id):
        if device.suspended:
            continue
        server = _server_for(deps, device)
        if server is None:
            continue
        try:
            await server.wg.suspend_client(device.client_name)
        except WgError as exc:
            log.warning("не удалось выключить %s: %s", device.client_name, exc)
            continue
        deps.db.mark_device_suspended(device.client_name, True)
        count += 1
    return count


async def resume_devices(deps: Deps, tg_id: int) -> int:
    """Включает обратно ключи после оплаты — на всех серверах разом."""
    count = 0
    for device in deps.db.devices(tg_id):
        if not device.suspended:
            continue
        server = _server_for(deps, device)
        if server is None:
            continue
        try:
            await server.wg.resume_client(device.client_name)
        except WgError as exc:
            log.warning("не удалось включить %s: %s", device.client_name, exc)
            continue
        deps.db.mark_device_suspended(device.client_name, False)
        count += 1
    return count


async def resolve_region_config(deps: Deps, code: str, country: str) -> str | None:
    """Конфиг тунеля по короткому коду для нужной страны — то, что отдаёт
    сервер ключей (см. keyserver.py).

    'ru' — тот, что выдан при самой выдаче ключа (issue_key), как и раньше.
    Любая другая настроенная страна — «зеркало» того же самого кода на
    другом сервере: при первом обращении заводится там лениво (человек
    ничего заново не вводит — просто выбирает страну в приложении).
    """
    if country == "ru":
        return deps.db.key_config(code)
    server = deps.servers.get(country)
    if server is None:
        return None

    cached = deps.db.key_region_config(code, country)
    if cached is not None:
        return cached

    base_client_name = deps.db.key_client_name(code)
    if base_client_name is None:
        return None
    owner = deps.db.device(base_client_name)
    if owner is None:
        return None
    user = deps.db.user(owner.tg_id)
    if user is None or not user.active or owner.suspended:
        # Не заводим новый рабочий тунель тому, у кого доступ и так не
        # активен — иначе смена страны в приложении обходила бы отключение
        # за неуплату.
        return None

    region_client_name = f"{base_client_name}-{country}"
    ip = await server.wg.add_client(region_client_name)
    config_text = await server.wg.client_config(region_client_name)

    if deps.db.device(region_client_name) is None:
        deps.db.add_device(
            region_client_name,
            owner.tg_id,
            f"{owner.title} ({server.label})",
            ip,
            country=country,
        )
    else:
        # Уже было — например, человек снёс это «зеркало» /drop-ом раньше,
        # а теперь снова выбрал ту же страну.
        deps.db.revive_device(region_client_name, ip)
    deps.db.store_key_region(code, country, region_client_name, config_text)
    return config_text


async def notify(bot: Bot, tg_id: int, text: str) -> None:
    try:
        await bot.send_message(tg_id, text)
    except Exception as exc:
        # Человек мог заблокировать бота — это не повод падать.
        log.info("не доставлено %s: %s", tg_id, exc)

"""Все экраны бота: профиль, устройства, подписка, инструкция."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from . import rates
from .config import Config, _days_word
from .cryptobot import CryptoPay, CryptoPayError
from .db import Storage
from .xray import Xray, XrayError, human_bytes
from .xrocket import XRocketError, XRocketPay

log = logging.getLogger(__name__)
router = Router()

BTN_PROFILE = "👤 Профиль"
BTN_DEVICES = "📱Мои устройства"
BTN_SUBSCRIBE = "💳Оформить подписку"
BTN_INSTRUCTIONS = "📚Инструкция"
BTN_ADD_DEVICE = "📱Добавить устройство"

GREETING = (
    "Добро пожаловать!  Забор становится выше, но мы-то знаем, где подкоп 🪏\n\n"
    "ℹ️Стабильная связь 24/7\n\n"
    " 🛜Трафик без ограничений — качай, смотри и работай сколько влезет, "
    "никаких лимитов.\n\n"
    " ✅Ловит повсюду — работает в любых городах и у любого мобильного или "
    "домашнего провайдера.\n\n"
    "📲Под любые девайсы — легко ставится на телефон, компьютер или планшет\n\n"
    "🔒Полная безопасность — шифрование данных, никаких следов и ноль рекламы."
)

INSTRUCTIONS = (
    "1. Скачайте приложение Happ в App Store или Google Play.\n"
    "2. Нажмите «+» в приложении и вставьте ключ подписки, полученный от бота.\n"
    "3. Выберите нужный сервер в списке и нажмите кнопку включения"
)


@dataclass
class Deps:
    cfg: Config
    db: Storage
    xray: Xray
    crypto: CryptoPay | None
    xrocket: XRocketPay | None


class Rename(StatesGroup):
    waiting_name = State()


def main_menu_rows() -> list[list[InlineKeyboardButton]]:
    """Кнопки главного меню — привязаны прямо к сообщению (не отдельная
    клавиатура снизу экрана), поэтому их же добавляем внизу каждого экрана,
    чтобы не нужно было прокручивать обратно к самому первому /start."""
    return [
        [InlineKeyboardButton(text=BTN_PROFILE, callback_data="menu:profile")],
        [InlineKeyboardButton(text=BTN_DEVICES, callback_data="menu:devices")],
        [InlineKeyboardButton(text=BTN_SUBSCRIBE, callback_data="menu:subscribe")],
        [InlineKeyboardButton(text=BTN_INSTRUCTIONS, callback_data="menu:instructions")],
    ]


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=main_menu_rows())


def with_menu(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[*rows, *main_menu_rows()])


def plans_keyboard(cfg: Config) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=plan.label(), callback_data=f"plan:{index}")]
        for index, plan in enumerate(cfg.plans)
    ]
    return with_menu(rows)


def pay_method_keyboard(cfg: Config, plan_index: int) -> InlineKeyboardMarkup:
    rows = []
    if cfg.crypto_pay_token:
        rows.append(
            [InlineKeyboardButton(text="CryptoBot", callback_data=f"paymethod:{plan_index}:cryptobot")]
        )
    if cfg.xrocket_api_token:
        rows.append(
            [InlineKeyboardButton(text="Xrocket", callback_data=f"paymethod:{plan_index}:xrocket")]
        )
    return with_menu(rows)


def device_detail_keyboard(client_name: str) -> InlineKeyboardMarkup:
    return with_menu(
        [
            [InlineKeyboardButton(text="❌Удалить", callback_data=f"device_del:{client_name}")],
            [InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"device_ren:{client_name}")],
        ]
    )


# --- вход ---------------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message, deps: Deps) -> None:
    user = deps.db.user(message.from_user.id)
    if user is None:
        deps.db.create_user(message.from_user.id, message.from_user.username)
    await message.answer(GREETING, reply_markup=main_menu())


# --- профиль --------------------------------------------------------------


def _profile_text(deps: Deps, tg_id: int, username: str | None) -> str:
    user = deps.db.user(tg_id)
    if user is None:
        deps.db.create_user(tg_id, username)
        user = deps.db.user(tg_id)
        assert user is not None

    lines = [f"🆔 Telegram ID: {user.tg_id}"]
    if user.active:
        when = user.expires_at.astimezone().strftime("%d.%m %H:%M")
        lines.append(f"🔑 Подписка активна до {when}")
    else:
        lines.append("🔑 У вас пока нет активных ключей доступа")
        lines.append("")
        lines.append("Для получения ключа оплатите подписку")
    return "\n".join(lines)


@router.callback_query(F.data == "menu:profile")
async def show_profile(callback: CallbackQuery, state: FSMContext, deps: Deps) -> None:
    await callback.answer()
    await state.clear()
    if callback.message is None:
        return
    text = _profile_text(deps, callback.from_user.id, callback.from_user.username)
    await callback.message.answer(text, reply_markup=main_menu())


# --- устройства -------------------------------------------------------------


def _device_title(tg_id: int, existing_titles: set[str]) -> str:
    index = 1
    while f"Устройство {index}" in existing_titles:
        index += 1
    return f"Устройство {index}"


async def _render_devices(deps: Deps, tg_id: int) -> tuple[str, InlineKeyboardMarkup]:
    user = deps.db.user(tg_id)
    if user is None or not user.active:
        return (
            "<b>❌ У вас нет активной подписки</b>\n\n"
            "Оплатите подписку, чтобы увидеть свои устройства.",
            with_menu([]),
        )

    devices = deps.db.devices(tg_id)
    rows = [
        [InlineKeyboardButton(text=device.title, callback_data=f"device_view:{device.client_name}")]
        for device in devices
    ]
    if len(devices) < deps.cfg.max_devices:
        rows.append([InlineKeyboardButton(text=BTN_ADD_DEVICE, callback_data="device_add")])

    if not devices:
        text = f"Устройств пока нету. Доступно {deps.cfg.max_devices}/{deps.cfg.max_devices}"
    else:
        text = f"Добавлено {len(devices)}/{deps.cfg.max_devices}"
    return text, with_menu(rows)


@router.callback_query(F.data == "menu:devices")
async def show_devices_cb(callback: CallbackQuery, state: FSMContext, deps: Deps) -> None:
    await callback.answer()
    await state.clear()
    if callback.message is None:
        return
    text, keyboard = await _render_devices(deps, callback.from_user.id)
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "device_add")
async def add_device(callback: CallbackQuery, deps: Deps) -> None:
    await callback.answer()
    if callback.message is None:
        return
    tg_id = callback.from_user.id
    user = deps.db.user(tg_id)
    if user is None or not user.active:
        await callback.message.answer("Подписка не активна — оплатите доступ.")
        return

    devices = deps.db.devices(tg_id)
    if len(devices) >= deps.cfg.max_devices:
        await callback.message.answer(
            f"Уже добавлено устройств: {len(devices)} из {deps.cfg.max_devices}."
        )
        return

    used = {d.client_name for d in deps.db.devices(tg_id, include_revoked=True)}
    index = 0
    while f"tg{tg_id}-{index}" in used:
        index += 1
    client_name = f"tg{tg_id}-{index}"
    title = _device_title(tg_id, {d.title for d in devices})

    try:
        uri = await deps.xray.add_client(client_name)
    except XrayError as exc:
        log.exception("не удалось выдать ключ %s", client_name)
        await callback.message.answer(f"Сервер не смог выдать ключ: {exc}")
        return

    deps.db.add_device(client_name, tg_id, title)
    await callback.message.answer(f"Ключ vless\n\n<code>{html.escape(uri)}</code>", parse_mode="HTML")
    # Реальную отметку об использовании ключа протокол VLESS/Reality не
    # даёт (нет обратного сигнала от Happ) — считаем «подключённым» сразу
    # после выдачи, это оптимистичное сообщение, а не подтверждённый факт.
    await callback.message.answer("Устройство подключено!", reply_markup=with_menu([]))


@router.callback_query(F.data.startswith("device_view:"))
async def view_device(callback: CallbackQuery, deps: Deps) -> None:
    await callback.answer()
    if callback.message is None:
        return
    client_name = callback.data.split(":", 1)[1]
    device = deps.db.device(client_name)
    if device is None or device.tg_id != callback.from_user.id:
        await callback.message.answer("Устройство не найдено.")
        return

    lines = [html.escape(device.title)]
    if device.suspended:
        lines.append("Статус: приостановлено (нет активной подписки)")
    try:
        traffic = await deps.xray.traffic_by_name()
    except XrayError as exc:
        log.warning("не удалось прочитать трафик: %s", exc)
        traffic = {}
    used = traffic.get(client_name)
    if used is not None:
        lines.append(f"Трафик: ↓{human_bytes(used.received)} ↑{human_bytes(used.sent)}")
    else:
        lines.append("Трафик: пока нет данных")

    await callback.message.answer(
        "\n".join(lines), reply_markup=device_detail_keyboard(client_name)
    )


@router.callback_query(F.data.startswith("device_del:"))
async def delete_device(callback: CallbackQuery, deps: Deps) -> None:
    await callback.answer()
    if callback.message is None:
        return
    client_name = callback.data.split(":", 1)[1]
    device = deps.db.device(client_name)
    if device is None or device.tg_id != callback.from_user.id:
        await callback.message.answer("Устройство не найдено.")
        return

    try:
        await deps.xray.remove_client(client_name)
    except XrayError as exc:
        await callback.message.answer(f"Не удалось удалить: {exc}")
        return

    deps.db.mark_device_revoked(client_name)
    await callback.message.answer("Устройство успешно удалено")


@router.callback_query(F.data.startswith("device_ren:"))
async def rename_device_start(callback: CallbackQuery, state: FSMContext, deps: Deps) -> None:
    await callback.answer()
    if callback.message is None:
        return
    client_name = callback.data.split(":", 1)[1]
    device = deps.db.device(client_name)
    if device is None or device.tg_id != callback.from_user.id:
        await callback.message.answer("Устройство не найдено.")
        return

    await state.set_state(Rename.waiting_name)
    await state.update_data(client_name=client_name)
    await callback.message.answer("Введите новое имя для устройства:")


@router.message(StateFilter(Rename.waiting_name))
async def rename_device_finish(message: Message, state: FSMContext, deps: Deps) -> None:
    # Кнопки меню теперь инлайновые (привязаны к своим сообщениям, не к
    # этому текстовому вводу) — их нажатие уходит отдельным callback-запросом
    # и само чистит это состояние (см. state.clear() в каждом menu:*-
    # обработчике), сюда попадает только реально напечатанный текст.
    data = await state.get_data()
    client_name = data.get("client_name")
    await state.clear()
    device = deps.db.device(client_name) if client_name else None
    if device is None or device.tg_id != message.from_user.id:
        await message.answer("Устройство не найдено.")
        return

    title = (message.text or "").strip()[:40]
    if not title:
        await message.answer("Имя не может быть пустым — попробуйте ещё раз через ✏️ Переименовать.")
        return

    deps.db.rename_device(client_name, title)
    await message.answer("Устройство успешно переименовано")


# --- инструкция -------------------------------------------------------------


@router.callback_query(F.data == "menu:instructions")
async def show_instructions(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    if callback.message is not None:
        await callback.message.answer(INSTRUCTIONS, reply_markup=main_menu())


# --- подписка ---------------------------------------------------------------


@router.callback_query(F.data == "menu:subscribe")
async def show_plans(callback: CallbackQuery, state: FSMContext, deps: Deps) -> None:
    await callback.answer()
    await state.clear()
    if callback.message is None:
        return
    if not deps.cfg.payments_enabled:
        await callback.message.answer(
            "Оплата пока не подключена — напишите владельцу сервиса.", reply_markup=main_menu()
        )
        return
    if deps.db.user(callback.from_user.id) is None:
        deps.db.create_user(callback.from_user.id, callback.from_user.username)
    await callback.message.answer("Выберите тариф:", reply_markup=plans_keyboard(deps.cfg))


@router.callback_query(F.data.startswith("plan:"))
async def on_plan_chosen(callback: CallbackQuery, deps: Deps) -> None:
    await callback.answer()
    if callback.message is None:
        return
    index = int(callback.data.split(":", 1)[1])
    if not 0 <= index < len(deps.cfg.plans):
        return
    await callback.message.answer(
        "💳Выберите способ оплаты:", reply_markup=pay_method_keyboard(deps.cfg, index)
    )


@router.callback_query(F.data.startswith("paymethod:"))
async def on_pay_method_chosen(callback: CallbackQuery, deps: Deps) -> None:
    await callback.answer()
    if callback.message is None:
        return

    _, index_raw, provider = callback.data.split(":", 2)
    index = int(index_raw)
    if not 0 <= index < len(deps.cfg.plans):
        return
    plan = deps.cfg.plans[index]
    user = deps.db.user(callback.from_user.id)
    if user is None:
        await callback.message.answer("Нажмите /start, чтобы начать.")
        return

    description = f"VPN, доступ на {plan.days} {_days_word(plan.days)}"
    if provider == "cryptobot":
        if deps.crypto is None:
            await callback.message.answer("CryptoBot не подключён.")
            return
        try:
            invoice = await deps.crypto.create_invoice(plan.rub, description, str(user.tg_id))
        except CryptoPayError as exc:
            log.exception("CryptoBot не выставил счёт")
            await callback.message.answer(f"Не удалось выставить счёт: {exc}")
            return
        deps.db.add_payment(
            invoice.invoice_id, "cryptobot", user.tg_id, invoice.amount, invoice.currency, plan.days
        )
        await callback.message.answer(
            f"Счёт на {invoice.amount} {invoice.currency} за {plan.days} "
            f"{_days_word(plan.days)}:\n{invoice.pay_url}\n\n"
            "Оплата проходит в @CryptoBot. Как оплатите — доступ откроется "
            "в течение минуты."
        )
    elif provider == "xrocket":
        if deps.xrocket is None:
            await callback.message.answer("xRocket не подключён.")
            return
        try:
            rate = await rates.usdt_rub_rate()
            amount = f"{plan.rub / rate:.2f}"
        except rates.RateError as exc:
            log.warning(
                "живой курс USDT/RUB недоступен (%s), беру запасной %.2f",
                exc,
                deps.cfg.rub_per_usdt,
            )
            amount = deps.cfg.usdt_for(plan)
        try:
            invoice = await deps.xrocket.create_invoice(
                amount, deps.cfg.xrocket_currency, description, str(user.tg_id)
            )
        except XRocketError as exc:
            log.exception("xRocket не выставил счёт")
            await callback.message.answer(f"Не удалось выставить счёт: {exc}")
            return
        deps.db.add_payment(
            invoice.invoice_id, "xrocket", user.tg_id, invoice.amount, invoice.currency, plan.days
        )
        await callback.message.answer(
            f"Счёт на {invoice.amount} {invoice.currency} (≈{plan.rub}₽) за {plan.days} "
            f"{_days_word(plan.days)}:\n{invoice.pay_url}\n\n"
            "Оплата проходит в xRocket. Как оплатите — доступ откроется в течение минуты."
        )


# --- админка ----------------------------------------------------------------


@router.message(Command("extend"))
async def cmd_extend(message: Message, command: CommandObject, deps: Deps) -> None:
    if not deps.cfg.is_admin(message.from_user.id):
        return
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].lstrip("-").isdigit() or not parts[1].isdigit():
        await message.answer("Формат: /extend <tg_id> <дней>")
        return

    tg_id, days = int(parts[0]), int(parts[1])
    if deps.db.user(tg_id) is None:
        deps.db.create_user(tg_id, None)
    user = deps.db.extend(tg_id, days)
    resumed = await resume_devices(deps, tg_id)
    await message.answer(f"Продлено до {user.expires_at.astimezone():%d.%m.%Y %H:%M}, включено ключей: {resumed}.")
    await notify(message.bot, tg_id, f"Доступ продлён на {days} {_days_word(days)}.")


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
    await message.answer(f"Доступ закрыт, устройств выключено: {stopped}.")


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
            f"до {user.expires_at.astimezone():%d.%m.%Y %H:%M}, устройств: {len(devices)}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("stats"))
async def cmd_stats(message: Message, deps: Deps) -> None:
    if not deps.cfg.is_admin(message.from_user.id):
        return
    users = deps.db.users()
    active = [user for user in users if user.active]
    devices = deps.db.active_devices()
    live = [device for device in devices if not device.suspended]
    lines = [
        f"Людей: {len(active)} активных из {len(users)}",
        f"Устройств: {len(live)} работают, {len(devices) - len(live)} приостановлено",
    ]
    totals = deps.db.paid_totals()
    if totals:
        lines += ["", "Получено:"]
        lines += [f"• {total} {currency} за {count} платежей" for currency, total, count in totals]
    await message.answer("\n".join(lines))


# --- операции над устройствами, общие для оплаты и фоновых задач ------------


async def suspend_devices(deps: Deps, tg_id: int) -> int:
    count = 0
    for device in deps.db.devices(tg_id):
        if device.suspended:
            continue
        try:
            await deps.xray.suspend_client(device.client_name)
        except XrayError as exc:
            log.warning("не удалось выключить %s: %s", device.client_name, exc)
            continue
        deps.db.mark_device_suspended(device.client_name, True)
        count += 1
    return count


async def resume_devices(deps: Deps, tg_id: int) -> int:
    count = 0
    for device in deps.db.devices(tg_id):
        if not device.suspended:
            continue
        try:
            await deps.xray.resume_client(device.client_name)
        except XrayError as exc:
            log.warning("не удалось включить %s: %s", device.client_name, exc)
            continue
        deps.db.mark_device_suspended(device.client_name, False)
        count += 1
    return count


async def notify(bot: Bot, tg_id: int, text: str, **kwargs) -> None:
    try:
        await bot.send_message(tg_id, text, **kwargs)
    except Exception as exc:
        log.info("не доставлено %s: %s", tg_id, exc)

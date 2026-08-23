import argparse
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from bot.config import Config
from bot.goskey_automation import GoskeyAutomation
from bot.telegram import TelegramClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("poller")


def load_seen(path: str) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    return set(json.loads(p.read_text()))


def save_seen(path: str, seen: set[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2))


def should_send_login_alert(state_file: str, cooldown_hours: float) -> bool:
    p = Path(state_file)
    if not p.exists():
        return True
    last_sent = datetime.fromisoformat(json.loads(p.read_text())["last_sent"])
    return datetime.now() - last_sent > timedelta(hours=cooldown_hours)


def mark_login_alert_sent(state_file: str) -> None:
    p = Path(state_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"last_sent": datetime.now().isoformat()}))


def clear_login_alert_state(state_file: str) -> None:
    Path(state_file).unlink(missing_ok=True)


def try_recover_login(cfg: Config, automation: GoskeyAutomation, telegram: TelegramClient) -> bool:
    """
    Пытается восстановить сессию без похода в scrcpy:
      1. если открылась форма логин+пароль — заполняет её из .env
         (если GOSKEY_LOGIN/GOSKEY_PASSWORD не заданы, тут дальше идти нельзя);
      2. если приложение просит код из SMS — спрашивает его в Telegram
         и ждёт ваш ответ (SIM всегда при вас, как вы и предлагали).
    Возвращает True, если в итоге залогинены, False — если восстановить
    не вышло (тогда остаётся только ручной вход через scrcpy).
    """
    if automation.is_logged_in():
        return True

    if automation.is_login_form():
        if not (cfg.goskey_login and cfg.goskey_password):
            log.warning("Экран логина, но GOSKEY_LOGIN/GOSKEY_PASSWORD не заданы в .env")
            return False
        log.info("Заполняю форму логина автоматически")
        automation.fill_login_form(cfg.goskey_login, cfg.goskey_password)

    if automation.is_awaiting_otp():
        telegram.send_message(
            f"🔐 [{cfg.account_label}] Госключ просит код из SMS для входа. "
            "Пришлите его в ответ сюда одним сообщением (просто цифры)."
        )
        code = telegram.wait_for_reply(cfg.telegram_offset_file, cfg.otp_wait_timeout_seconds)
        if not code:
            telegram.send_message(
                f"[{cfg.account_label}] Не дождался кода за {cfg.otp_wait_timeout_seconds // 60} мин, попробую позже."
            )
            return False
        log.info("Код получен от пользователя, ввожу")
        automation.submit_otp(code)

    return automation.is_logged_in()


def run_once(cfg: Config, automation: GoskeyAutomation, telegram: TelegramClient, seen: set[str]) -> set[str]:
    automation.ensure_app_open()

    if not try_recover_login(cfg, automation, telegram):
        log.warning("[%s] Сессия Госключ не активна и не восстановлена автоматически", cfg.account_label)
        if should_send_login_alert(cfg.login_alert_state_file, cfg.login_alert_cooldown_hours):
            telegram.send_message(
                f"⚠️ [{cfg.account_label}] Не смог восстановить сессию Госключ "
                "автоматически. Нужен ручной вход через scrcpy/weditor (см. README, п.4)."
            )
            mark_login_alert_sent(cfg.login_alert_state_file)
        return seen

    # если до этого была отправлена тревога, а сессия восстановилась — сообщим об этом
    if Path(cfg.login_alert_state_file).exists():
        telegram.send_message(f"✅ [{cfg.account_label}] Сессия Госключ восстановлена, бот снова работает.")
        clear_login_alert_state(cfg.login_alert_state_file)

    docs = automation.list_documents()
    log.info("[%s] Найдено документов на экране: %d", cfg.account_label, len(docs))

    new_docs = [d for d in docs if d.doc_id not in seen]
    if not new_docs:
        log.info("[%s] Новых документов нет", cfg.account_label)
        return seen

    Path(cfg.download_dir).mkdir(parents=True, exist_ok=True)
    for doc in new_docs:
        log.info("[%s] Новый документ: %s", cfg.account_label, doc.title)
        try:
            file_path = automation.download_document(doc, cfg.download_dir)
            telegram.send_document(file_path, caption=f"[{cfg.account_label}] {doc.title}")
            seen.add(doc.doc_id)
        except Exception:
            log.exception(
                "[%s] Не удалось обработать документ %s — попробуем в следующий раз",
                cfg.account_label,
                doc.title,
            )

    return seen


def main() -> None:
    parser = argparse.ArgumentParser(description="Пересылка документов Госключ в Telegram")
    parser.add_argument(
        "--env",
        default=".env",
        help="Путь к .env-файлу аккаунта (например .env.personal или .env.ip для второго аккаунта)",
    )
    args = parser.parse_args()

    cfg = Config.load(args.env)
    automation = GoskeyAutomation(cfg.adb_device)
    telegram = TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id)
    seen = load_seen(cfg.seen_docs_file)

    log.info("[%s] Старт: проверка каждые %d сек (ADB %s)", cfg.account_label, cfg.poll_interval_seconds, cfg.adb_device)
    while True:
        try:
            seen = run_once(cfg, automation, telegram, seen)
            save_seen(cfg.seen_docs_file, seen)
        except Exception:
            log.exception("[%s] Ошибка в цикле опроса — продолжаем", cfg.account_label)
        time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    main()

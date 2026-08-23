import json
import logging
import time
from pathlib import Path

from bot.config import Config
from bot.goskey_automation import GoskeyAutomation
from bot.telegram import TelegramSender

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


def run_once(cfg: Config, automation: GoskeyAutomation, telegram: TelegramSender, seen: set[str]) -> set[str]:
    automation.ensure_app_open()
    docs = automation.list_documents()
    log.info("Найдено документов на экране: %d", len(docs))

    new_docs = [d for d in docs if d.doc_id not in seen]
    if not new_docs:
        log.info("Новых документов нет")
        return seen

    Path(cfg.download_dir).mkdir(parents=True, exist_ok=True)
    for doc in new_docs:
        log.info("Новый документ: %s", doc.title)
        try:
            file_path = automation.download_document(doc, cfg.download_dir)
            telegram.send_document(file_path, caption=doc.title)
            seen.add(doc.doc_id)
        except Exception:
            log.exception("Не удалось обработать документ %s — попробуем в следующий раз", doc.title)

    return seen


def main() -> None:
    cfg = Config.load()
    automation = GoskeyAutomation(cfg.adb_device)
    telegram = TelegramSender(cfg.telegram_bot_token, cfg.telegram_chat_id)
    seen = load_seen(cfg.seen_docs_file)

    log.info("Старт: проверка каждые %d сек", cfg.poll_interval_seconds)
    while True:
        try:
            seen = run_once(cfg, automation, telegram, seen)
            save_seen(cfg.seen_docs_file, seen)
        except Exception:
            log.exception("Ошибка в цикле опроса — продолжаем")
        time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    main()

"""Сервер ключей: отдаёт конфиг тунеля по короткому коду.

Приложение хранит только 8-символьный ключ, который выдаёт бот. Сами
настройки тунеля (приватный ключ устройства, адрес сервера) оно забирает
отсюда один раз, при вставке ключа — дальше подключение работает офлайн,
без обращений сюда.
"""

from __future__ import annotations

import logging

from aiohttp import web

from .db import Storage

log = logging.getLogger(__name__)


def build_app(db: Storage) -> web.Application:
    app = web.Application()

    async def handle_key(request: web.Request) -> web.Response:
        code = request.match_info["code"]
        config = db.key_config(code)
        if config is None:
            return web.Response(status=404, text="ключ не найден")
        return web.Response(text=config, content_type="text/plain")

    app.router.add_get("/key/{code}", handle_key)
    return app


async def start(db: Storage, host: str, port: int) -> web.AppRunner:
    """Поднимает сервер и возвращает runner — его нужно остановить (.cleanup()) при выходе."""
    runner = web.AppRunner(build_app(db))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("сервер ключей слушает %s:%s", host, port)
    return runner

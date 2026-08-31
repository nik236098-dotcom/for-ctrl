"""Сервер ключей: отдаёт конфиг тунеля по короткому коду.

Приложение хранит только 8-символьный ключ, который выдаёт бот. Сами
настройки тунеля (приватный ключ устройства, адрес сервера) оно забирает
отсюда один раз, при вставке ключа — дальше подключение работает офлайн,
без обращений сюда.

Необязательный параметр ?country= выбирает сервер (по умолчанию "ru" —
домашний, как и было всегда); любая другая настроенная страна (см.
handlers.resolve_region_config) заводится на своём сервере лениво при
первом обращении — тем же самым кодом, без нового ключа.
"""

from __future__ import annotations

import logging

from aiohttp import web

from .handlers import Deps, resolve_region_config
from .wg import WgError

log = logging.getLogger(__name__)


def build_app(deps: Deps) -> web.Application:
    app = web.Application()

    async def handle_key(request: web.Request) -> web.Response:
        code = request.match_info["code"]
        country = (request.query.get("country") or "ru").strip().lower()
        try:
            config = await resolve_region_config(deps, code, country)
        except WgError as exc:
            log.warning("сервер %s не смог выдать пира для %s: %s", country, code, exc)
            return web.Response(
                status=502, text=f"сервер «{country}» не смог выдать ключ: {exc}"
            )
        if config is None:
            return web.Response(status=404, text="ключ не найден")
        return web.Response(text=config, content_type="text/plain")

    app.router.add_get("/key/{code}", handle_key)
    return app


async def start(deps: Deps, host: str, port: int) -> web.AppRunner:
    """Поднимает сервер и возвращает runner — его нужно остановить (.cleanup()) при выходе."""
    runner = web.AppRunner(build_app(deps))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("сервер ключей слушает %s:%s", host, port)
    return runner

"""Живой курс USDT/RUB — для перевода рублёвой цены тарифа в USDT перед
выставлением счёта через xRocket (у него нет фиатных счетов, только крипта).

CoinGecko — бесплатный публичный API, ключ не нужен. Короткий кэш (5 мин),
чтобы не дёргать его на каждый счёт; если API недоступен — вызывающий код
сам решает, что делать (см. handlers.on_pay_method_chosen — там запасной
статический курс из Config.rub_per_usdt).
"""

from __future__ import annotations

import time

import aiohttp

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=rub"
CACHE_SECONDS = 300


class RateError(RuntimeError):
    pass


_cache: tuple[float, float] | None = None  # (получено в момент monotonic(), курс)


async def usdt_rub_rate() -> float:
    """Сколько рублей стоит 1 USDT прямо сейчас."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _cache[1]

    async with aiohttp.ClientSession() as session:
        async with session.get(
            COINGECKO_URL, timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            if response.status != 200:
                raise RateError(f"CoinGecko ответил {response.status}")
            data = await response.json()

    try:
        rate = float(data["tether"]["rub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RateError(f"не разобрал ответ CoinGecko: {data!r}") from exc
    if rate <= 0:
        raise RateError(f"курс из CoinGecko некорректный: {rate}")

    _cache = (now, rate)
    return rate

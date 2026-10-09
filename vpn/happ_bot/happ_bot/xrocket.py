"""Оплата через xRocket Pay (https://pay.xrocket.tg/).

В отличие от CryptoBot фиатных счетов тут нет — только крипто-активы
(TONCOIN, USDT и т.д.), поэтому рублёвую цену тарифа переводим в USDT сами
(см. Config.usdt_for) перед выставлением счёта.

Официальный OpenAPI: POST/GET /tg-invoices, заголовок Rocket-Pay-Key,
ответ вида {"success": true, "data": {...}}.
"""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

BASE = "https://pay.xrocket.tg/"


class XRocketError(RuntimeError):
    pass


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    pay_url: str
    amount: str
    currency: str


class XRocketPay:
    def __init__(self, token: str) -> None:
        self._headers = {"Rocket-Pay-Key": token}
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(headers=self._headers)
        return self._session

    async def create_invoice(
        self, amount: str, currency: str, description: str, payload: str
    ) -> Invoice:
        async with self._client().post(
            BASE + "tg-invoices",
            json={
                "amount": float(amount),
                "currency": currency,
                "description": description,
                "payload": payload,
                "expiredIn": 3600,
            },
        ) as response:
            body = await response.json()
        if not body.get("success"):
            raise XRocketError(str(body.get("message") or body))
        data = body["data"]
        return Invoice(
            invoice_id=str(data["id"]),
            pay_url=data["link"],
            amount=amount,
            currency=currency,
        )

    async def status(self, invoice_id: str) -> str | None:
        """Статус одного счёта: active/paid/expired. None — счёт не нашёлся."""
        async with self._client().get(BASE + f"tg-invoices/{invoice_id}") as response:
            if response.status == 404:
                return None
            body = await response.json()
        if not body.get("success"):
            raise XRocketError(str(body.get("message") or body))
        return body["data"]["status"]

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

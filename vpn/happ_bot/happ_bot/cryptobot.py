"""Оплата через @CryptoBot (Crypto Pay API), в рублях (фиатный режим).

Вебхук не используется: он требует публичного HTTPS-адреса. Вместо этого
фоновая задача раз в минуту спрашивает статус выставленных счетов (см.
__main__.payments_watch).
"""

from __future__ import annotations

from dataclasses import dataclass

import aiohttp

MAINNET = "https://pay.crypt.bot/api/"
TESTNET = "https://testnet-pay.crypt.bot/api/"


class CryptoPayError(RuntimeError):
    pass


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    pay_url: str
    amount: str
    currency: str


class CryptoPay:
    def __init__(self, token: str, testnet: bool = False) -> None:
        self._base = TESTNET if testnet else MAINNET
        self._headers = {"Crypto-Pay-API-Token": token}
        self._session: aiohttp.ClientSession | None = None

    async def _call(self, method: str, params: dict) -> dict:
        if self._session is None:
            self._session = aiohttp.ClientSession(headers=self._headers)
        async with self._session.post(self._base + method, json=params) as response:
            body = await response.json()
        if not body.get("ok"):
            raise CryptoPayError(str(body.get("error") or body))
        return body["result"]

    async def create_invoice(self, rub_amount: int, description: str, payload: str) -> Invoice:
        """Счёт в рублях — CryptoBot сам конвертирует в крипту по своему курсу
        (currency_type=fiat), плательщику незачем знать курсы самому."""
        result = await self._call(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": str(rub_amount),
                "description": description,
                "payload": payload,
                "expires_in": 3600,
            },
        )
        pay_url = (
            result.get("bot_invoice_url")
            or result.get("mini_app_invoice_url")
            or result.get("pay_url", "")
        )
        return Invoice(
            invoice_id=str(result["invoice_id"]),
            pay_url=pay_url,
            amount=str(result.get("amount", rub_amount)),
            currency="RUB",
        )

    async def statuses(self, invoice_ids: list[str]) -> dict[str, str]:
        """{invoice_id: status}. Статусы Crypto Pay: active, paid, expired."""
        if not invoice_ids:
            return {}
        result = await self._call("getInvoices", {"invoice_ids": ",".join(invoice_ids)})
        items = result.get("items", []) if isinstance(result, dict) else result
        return {str(item["invoice_id"]): item["status"] for item in items}

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

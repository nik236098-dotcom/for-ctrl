"""Обёртка над серверными скриптами Xray/Happ (см. vpn/server/*_client_happ.sh).

В отличие от WireGuard-бота тут нет второго сервера и SSH — Xray живёт на
одном-единственном сервере (см. vpn/happ_bot/README.md), и этот бот
запускается прямо на нём же, поэтому всё — через локальный `sudo -n`.
Права на команды бот получает через sudoers (см. install_bot.sh), root он не
получает вообще.

Скрипты вызываются НАПРЯМУЮ (sudo -n /путь/script.sh, без обёртки в bash) —
это важно: sudoers в install_bot.sh разрешает ровно эту команду, "sudo -n
bash /путь/script.sh" для него уже другая команда и не пройдёт (реальный
баг, пойманный вживую — «Добавить устройство» падал именно из-за этого).
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_URI_LINE = re.compile(r"^vless://\S+$", re.MULTILINE)


class XrayError(RuntimeError):
    """Скрипт вернул ненулевой код — текст ошибки внутри."""


@dataclass(frozen=True)
class Transfer:
    received: int
    sent: int


def _run(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise XrayError(message or f"{argv[0]}: код {result.returncode}")
    return result.stdout


class Xray:
    def __init__(self, scripts_dir: Path) -> None:
        self._scripts = scripts_dir

    def _script(self, name: str) -> str:
        path = self._scripts / name
        if not path.exists():
            raise XrayError(f"нет скрипта {path}")
        return str(path)

    async def add_client(self, name: str) -> str:
        """Заводит клиента и возвращает готовую ссылку vless://.

        add_client_happ.sh кроме самой ссылки печатает ещё и пояснительный
        текст (см. скрипт) — вытаскиваем из вывода именно строку с vless://.
        """
        output = await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("add_client_happ.sh"), name]
        )
        match = _URI_LINE.search(output)
        if match is None:
            raise XrayError("сервер не вернул ссылку vless://")
        return match.group(0)

    async def client_uri(self, name: str) -> str:
        output = await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("show_client_happ.sh"), name]
        )
        match = _URI_LINE.search(output)
        if match is None:
            raise XrayError("сервер не вернул ссылку vless://")
        return match.group(0)

    async def suspend_client(self, name: str) -> None:
        await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("suspend_client_happ.sh"), name]
        )

    async def resume_client(self, name: str) -> None:
        await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("resume_client_happ.sh"), name]
        )

    async def remove_client(self, name: str) -> None:
        await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("remove_client_happ.sh"), name]
        )

    async def traffic_by_name(self) -> dict[str, Transfer]:
        """Счётчики трафика по имени клиента (email в статистике Xray).

        Требует блок "api"/"stats"/"policy" в config.json (см.
        install_xray.sh) — на сервере без него скрипт вернёт ошибку, тогда
        просто не показываем цифры трафика, не роняя всё остальное (см.
        вызов в handlers.py — обёрнут в try/except XrayError).
        """
        output = await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("xray_traffic.sh")]
        )
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise XrayError(f"не разобрал ответ statsquery: {exc}") from exc

        result: dict[str, Transfer] = {}
        uplink: dict[str, int] = {}
        downlink: dict[str, int] = {}
        for entry in data.get("stat") or []:
            name = entry.get("name", "")
            parts = name.split(">>>")
            if len(parts) != 4 or parts[0] != "user" or parts[2] != "traffic":
                continue
            client, direction, value = parts[1], parts[3], int(entry.get("value") or 0)
            (uplink if direction == "uplink" else downlink)[client] = value

        for client in uplink.keys() | downlink.keys():
            result[client] = Transfer(
                received=downlink.get(client, 0), sent=uplink.get(client, 0)
            )
        return result


def human_bytes(value: int) -> str:
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    size = float(value)
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    return f"{size:.1f} {units[unit]}"

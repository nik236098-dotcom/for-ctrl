"""Обёртки над серверными скриптами WireGuard.

Бот работает не под root: три скрипта и `wg show` разрешены ему через
sudoers (см. install_bot.sh), больше он на сервере ничего не может.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_IP_IN_OUTPUT = re.compile(r"добавлен:\s*(\S+)")


class WgError(RuntimeError):
    """Скрипт вернул ненулевой код — текст ошибки внутри."""


@dataclass(frozen=True)
class Transfer:
    received: int
    sent: int


def _run(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise WgError(message or f"{argv[0]}: код {result.returncode}")
    return result.stdout


class Wireguard:
    def __init__(self, scripts_dir: Path, iface: str) -> None:
        self._scripts = scripts_dir
        self._iface = iface

    def _script(self, name: str) -> str:
        path = self._scripts / name
        if not path.exists():
            raise WgError(f"нет скрипта {path}")
        return str(path)

    async def add_client(self, name: str) -> str | None:
        """Заводит пира и возвращает выданный ему адрес в туннеле."""
        output = await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("add_client.sh"), name]
        )
        match = _IP_IN_OUTPUT.search(output)
        return match.group(1) if match else None

    async def client_config(self, name: str) -> str:
        return await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("show_client.sh"), name]
        )

    async def suspend_client(self, name: str) -> None:
        """Снимает пира с интерфейса, сохраняя его ключ до оплаты."""
        await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("suspend_client.sh"), name]
        )

    async def resume_client(self, name: str) -> None:
        await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("resume_client.sh"), name]
        )

    async def remove_client(self, name: str) -> None:
        await asyncio.to_thread(
            _run, ["sudo", "-n", self._script("remove_client.sh"), name]
        )

    async def transfer_by_ip(self) -> dict[str, Transfer]:
        """Счётчики трафика в разрезе адресов туннеля.

        `wg show` отдаёт их по публичным ключам, поэтому склеиваем с
        таблицей allowed-ips — адрес мы знаем из момента выдачи ключа.
        """
        allowed = await asyncio.to_thread(
            _run, ["sudo", "-n", "wg", "show", self._iface, "allowed-ips"]
        )
        transfer = await asyncio.to_thread(
            _run, ["sudo", "-n", "wg", "show", self._iface, "transfer"]
        )

        ip_by_key: dict[str, str] = {}
        for line in allowed.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                ip_by_key[parts[0]] = parts[1].split("/")[0]

        result: dict[str, Transfer] = {}
        for line in transfer.splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] in ip_by_key:
                result[ip_by_key[parts[0]]] = Transfer(int(parts[1]), int(parts[2]))
        return result


async def qr_png(text: str) -> bytes | None:
    """QR-код конфига картинкой. None, если qrencode не установлен."""
    if shutil.which("qrencode") is None:
        return None

    def encode() -> bytes:
        result = subprocess.run(
            ["qrencode", "-t", "PNG", "-o", "-", "-s", "6", "-m", "2"],
            input=text.encode(),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise WgError(result.stderr.decode().strip() or "qrencode не смог")
        return result.stdout

    return await asyncio.to_thread(encode)


def human_bytes(value: int) -> str:
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    size = float(value)
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    return f"{size:.1f} {units[unit]}"

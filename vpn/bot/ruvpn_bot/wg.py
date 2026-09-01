"""Обёртки над серверными скриптами WireGuard.

Бот работает не под root: три скрипта и `wg show` разрешены ему через
sudoers (см. install_bot.sh), больше он на сервере ничего не может. То же
самое — и для второго (например, американского) сервера, только не
локально, а по SSH: см. [SshTarget].
"""

from __future__ import annotations

import asyncio
import re
import shlex
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


@dataclass(frozen=True)
class SshTarget:
    """Второй сервер управляется не локальным sudo, а тем же самым набором
    команд по SSH — тот же принцип: не полный root-доступ, а ровно те
    команды, что уже разрешены sudoers на том сервере (тот же
    install_bot.sh/install_wireguard.sh, только запущенный там же, где и
    сам WireGuard, а не рядом с ботом)."""

    host: str
    user: str
    key_path: Path
    port: int = 22


def _run(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise WgError(message or f"{argv[0]}: код {result.returncode}")
    return result.stdout


class Wireguard:
    def __init__(
        self, scripts_dir: Path, iface: str, ssh: SshTarget | None = None
    ) -> None:
        self._scripts = scripts_dir
        self._iface = iface
        self._ssh = ssh

    def _script(self, name: str) -> str:
        path = self._scripts / name
        # Существование скрипта можно проверить только на той машине, где он
        # реально исполняется: для удалённого сервера это его собственная
        # файловая система, не наша — там просто получим осмысленную ошибку
        # от самой SSH-команды, если скрипта нет.
        if self._ssh is None and not path.exists():
            raise WgError(f"нет скрипта {path}")
        return str(path)

    def _argv(self, *parts: str) -> list[str]:
        if self._ssh is None:
            return ["sudo", "-n", *parts]
        remote_command = " ".join(shlex.quote(part) for part in ["sudo", "-n", *parts])
        return [
            "ssh",
            "-i", str(self._ssh.key_path),
            "-p", str(self._ssh.port),
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            # Домашняя папка ruvpnbot (/opt/ruvpn/bot) специально принадлежит
            # root — самому ruvpnbot туда нечего писать (см. install_bot.sh).
            # Без этого ssh не может сохранить known_hosts и падает с
            # "Permission denied" вместо того, чтобы просто подключиться.
            "-o", "UserKnownHostsFile=/dev/null",
            f"{self._ssh.user}@{self._ssh.host}",
            remote_command,
        ]

    async def add_client(self, name: str) -> str | None:
        """Заводит пира и возвращает выданный ему адрес в туннеле."""
        output = await asyncio.to_thread(
            _run, self._argv(self._script("add_client.sh"), name)
        )
        match = _IP_IN_OUTPUT.search(output)
        return match.group(1) if match else None

    async def client_config(self, name: str) -> str:
        return await asyncio.to_thread(
            _run, self._argv(self._script("show_client.sh"), name)
        )

    async def suspend_client(self, name: str) -> None:
        """Снимает пира с интерфейса, сохраняя его ключ до оплаты."""
        await asyncio.to_thread(
            _run, self._argv(self._script("suspend_client.sh"), name)
        )

    async def resume_client(self, name: str) -> None:
        await asyncio.to_thread(
            _run, self._argv(self._script("resume_client.sh"), name)
        )

    async def remove_client(self, name: str) -> None:
        await asyncio.to_thread(
            _run, self._argv(self._script("remove_client.sh"), name)
        )

    async def transfer_by_ip(self) -> dict[str, Transfer]:
        """Счётчики трафика в разрезе адресов туннеля.

        `wg show` отдаёт их по публичным ключам, поэтому склеиваем с
        таблицей allowed-ips — адрес мы знаем из момента выдачи ключа.
        """
        allowed = await asyncio.to_thread(
            _run, self._argv("wg", "show", self._iface, "allowed-ips")
        )
        transfer = await asyncio.to_thread(
            _run, self._argv("wg", "show", self._iface, "transfer")
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


def human_bytes(value: int) -> str:
    units = ("Б", "КБ", "МБ", "ГБ", "ТБ")
    size = float(value)
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    return f"{size:.1f} {units[unit]}"

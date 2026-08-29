"""Хранилище: кто получил доступ, до какого числа и по какому инвайту."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id      INTEGER PRIMARY KEY,
    username   TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    warned_at  TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    client_name TEXT PRIMARY KEY,
    tg_id       INTEGER NOT NULL REFERENCES users(tg_id),
    title       TEXT NOT NULL,
    ip           TEXT,
    created_at   TEXT NOT NULL,
    suspended_at TEXT,
    revoked_at   TEXT
);

CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    used_by    INTEGER,
    used_at    TEXT
);

-- Короткий ключ (8 букв/цифр), который человек вставляет в приложении.
-- Сам конфиг тунеля в него не помещается — лежит здесь, приложение
-- забирает его с сервера ключей один раз по коду, дальше работает офлайн.
CREATE TABLE IF NOT EXISTS access_keys (
    code        TEXT PRIMARY KEY,
    client_name TEXT NOT NULL,
    config      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    invoice_id TEXT PRIMARY KEY,
    tg_id      INTEGER NOT NULL,
    amount     TEXT NOT NULL,
    asset      TEXT NOT NULL,
    days       INTEGER NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    paid_at    TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


@dataclass
class User:
    tg_id: int
    username: str | None
    created_at: datetime
    expires_at: datetime
    warned_at: datetime | None

    @property
    def active(self) -> bool:
        return self.expires_at > utcnow()

    @property
    def days_left(self) -> int:
        return max(0, (self.expires_at - utcnow()).days)


@dataclass
class Device:
    client_name: str
    tg_id: int
    title: str
    ip: str | None
    created_at: datetime
    suspended_at: datetime | None
    revoked_at: datetime | None

    @property
    def suspended(self) -> bool:
        return self.suspended_at is not None


class Storage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # --- пользователи ---------------------------------------------------

    def user(self, tg_id: int) -> User | None:
        row = self._db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        return self._user(row) if row else None

    def users(self) -> list[User]:
        rows = self._db.execute("SELECT * FROM users ORDER BY expires_at").fetchall()
        return [self._user(row) for row in rows]

    def create_user(self, tg_id: int, username: str | None, days: int) -> User:
        now = utcnow()
        self._db.execute(
            "INSERT INTO users (tg_id, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (tg_id, username, _iso(now), _iso(now + timedelta(days=days))),
        )
        self._db.commit()
        user = self.user(tg_id)
        assert user is not None
        return user

    def extend(self, tg_id: int, days: int) -> User:
        """Продлевает от текущей даты окончания, а если она в прошлом — от сегодня."""
        user = self.user(tg_id)
        if user is None:
            raise KeyError(tg_id)
        base = max(user.expires_at, utcnow())
        self._db.execute(
            "UPDATE users SET expires_at = ?, warned_at = NULL WHERE tg_id = ?",
            (_iso(base + timedelta(days=days)), tg_id),
        )
        self._db.commit()
        updated = self.user(tg_id)
        assert updated is not None
        return updated

    def expire_now(self, tg_id: int) -> None:
        self._db.execute(
            "UPDATE users SET expires_at = ? WHERE tg_id = ?", (_iso(utcnow()), tg_id)
        )
        self._db.commit()

    def mark_warned(self, tg_id: int) -> None:
        self._db.execute(
            "UPDATE users SET warned_at = ? WHERE tg_id = ?", (_iso(utcnow()), tg_id)
        )
        self._db.commit()

    # --- устройства -----------------------------------------------------

    def devices(self, tg_id: int, include_revoked: bool = False) -> list[Device]:
        query = "SELECT * FROM devices WHERE tg_id = ?"
        if not include_revoked:
            query += " AND revoked_at IS NULL"
        rows = self._db.execute(query + " ORDER BY created_at", (tg_id,)).fetchall()
        return [self._device(row) for row in rows]

    def device(self, client_name: str) -> Device | None:
        row = self._db.execute(
            "SELECT * FROM devices WHERE client_name = ?", (client_name,)
        ).fetchone()
        return self._device(row) if row else None

    def add_device(self, client_name: str, tg_id: int, title: str, ip: str | None) -> Device:
        self._db.execute(
            "INSERT INTO devices (client_name, tg_id, title, ip, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (client_name, tg_id, title, ip, _iso(utcnow())),
        )
        self._db.commit()
        device = self.device(client_name)
        assert device is not None
        return device

    def mark_device_suspended(self, client_name: str, suspended: bool) -> None:
        self._db.execute(
            "UPDATE devices SET suspended_at = ? WHERE client_name = ?",
            (_iso(utcnow()) if suspended else None, client_name),
        )
        self._db.commit()

    def mark_device_revoked(self, client_name: str) -> None:
        self._db.execute(
            "UPDATE devices SET revoked_at = ? WHERE client_name = ?",
            (_iso(utcnow()), client_name),
        )
        self._db.commit()

    def active_devices(self) -> list[Device]:
        rows = self._db.execute(
            "SELECT * FROM devices WHERE revoked_at IS NULL"
        ).fetchall()
        return [self._device(row) for row in rows]

    # --- инвайты --------------------------------------------------------

    def create_invite(self) -> str:
        code = secrets.token_urlsafe(9)
        self._db.execute(
            "INSERT INTO invites (code, created_at) VALUES (?, ?)", (code, _iso(utcnow()))
        )
        self._db.commit()
        return code

    def use_invite(self, code: str, tg_id: int) -> bool:
        """Помечает инвайт использованным. False, если кода нет или он уже потрачен."""
        cursor = self._db.execute(
            "UPDATE invites SET used_by = ?, used_at = ? WHERE code = ? AND used_by IS NULL",
            (tg_id, _iso(utcnow()), code),
        )
        self._db.commit()
        return cursor.rowcount == 1

    def unused_invites(self) -> list[str]:
        rows = self._db.execute(
            "SELECT code FROM invites WHERE used_by IS NULL ORDER BY created_at"
        ).fetchall()
        return [row["code"] for row in rows]

    # --- короткие ключи ---------------------------------------------------

    def key_exists(self, code: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM access_keys WHERE code = ?", (code,)
        ).fetchone()
        return row is not None

    def store_key(self, code: str, client_name: str, config_text: str) -> None:
        self._db.execute(
            "INSERT INTO access_keys (code, client_name, config, created_at)"
            " VALUES (?, ?, ?, ?)",
            (code, client_name, config_text, _iso(utcnow())),
        )
        self._db.commit()

    def key_config(self, code: str) -> str | None:
        """Конфиг тунеля по короткому коду — то, что отдаём в /key/<code>."""
        row = self._db.execute(
            "SELECT config FROM access_keys WHERE code = ?", (code,)
        ).fetchone()
        return row["config"] if row else None

    # --- платежи --------------------------------------------------------

    def add_payment(
        self, invoice_id: str, tg_id: int, amount: str, asset: str, days: int
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO payments"
            " (invoice_id, tg_id, amount, asset, days, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (invoice_id, tg_id, amount, asset, days, _iso(utcnow())),
        )
        self._db.commit()

    def pending_payments(self) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM payments WHERE status = 'active'"
        ).fetchall()

    def close_payment(self, invoice_id: str, status: str) -> bool:
        """Закрывает счёт. False, если он уже был закрыт (защита от двойного продления)."""
        cursor = self._db.execute(
            "UPDATE payments SET status = ?, paid_at = ?"
            " WHERE invoice_id = ? AND status = 'active'",
            (status, _iso(utcnow()), invoice_id),
        )
        self._db.commit()
        return cursor.rowcount == 1

    def paid_totals(self) -> list[tuple[str, str, int]]:
        """Сколько и в чём получено: [(актив, сумма, число платежей)]."""
        rows = self._db.execute(
            "SELECT asset, SUM(CAST(amount AS REAL)) AS total, COUNT(*) AS count"
            " FROM payments WHERE status = 'paid' GROUP BY asset"
        ).fetchall()
        return [(row["asset"], f"{row['total']:g}", row["count"]) for row in rows]

    # --- разбор строк ---------------------------------------------------

    @staticmethod
    def _user(row: sqlite3.Row) -> User:
        return User(
            tg_id=row["tg_id"],
            username=row["username"],
            created_at=_parse(row["created_at"]),
            expires_at=_parse(row["expires_at"]),
            warned_at=_parse(row["warned_at"]) if row["warned_at"] else None,
        )

    @staticmethod
    def _device(row: sqlite3.Row) -> Device:
        return Device(
            client_name=row["client_name"],
            tg_id=row["tg_id"],
            title=row["title"],
            ip=row["ip"],
            created_at=_parse(row["created_at"]),
            suspended_at=_parse(row["suspended_at"]) if row["suspended_at"] else None,
            revoked_at=_parse(row["revoked_at"]) if row["revoked_at"] else None,
        )

"""Read-only access checks, separate from config issuance and IP lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .handlers import Deps


def key_status(deps: Deps, code: str, country: str) -> str:
    if country != "ru" and country not in deps.servers:
        return "unknown"
    name = deps.db.key_client_name(code)
    if name is None:
        return "revoked"
    owner = deps.db.device(name)
    if owner is None:
        return "unknown"  # Inconsistent storage is not an explicit revocation.
    user = deps.db.user(owner.tg_id)
    if user is None:
        return "unknown"
    if not user.active or owner.suspended or owner.revoked_at is not None:
        return "revoked"
    if country != "ru":
        device = deps.db.device(f"{name}-{country}")
        if device is None:
            return "unknown"  # Do not provision a new peer during a status check.
        if device.suspended or device.revoked_at is not None:
            return "revoked"
        if deps.db.key_region_config(code, country) is None:
            return "unknown"
    return "active"

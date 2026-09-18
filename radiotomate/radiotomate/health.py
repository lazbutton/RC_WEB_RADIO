"""Shared health helpers that never include secrets in responses."""

from __future__ import annotations

import logging

from sqlalchemy import text

from radiotomate.db import QuartAlchemy

_log = logging.getLogger(__name__)


async def database_is_ready() -> bool:
    try:
        db = QuartAlchemy.get()
        async with db.session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        _log.warning("database health ping failed", exc_info=True)
        return False

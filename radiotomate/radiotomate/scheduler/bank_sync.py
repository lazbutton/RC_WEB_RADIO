"""Attach new bank files to carts that have a Finder folder mapping."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from radiotomate.enums import CartMode
from radiotomate.models import Cart, User
from radiotomate.quart import ShutdownError, or_shutdown
from radiotomate.services.carts import forget_legacy_files, reconcile_cart_bank
from radiotomate.services.media_bank import media_writable

_log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 12.0
MIN_INTERVAL_SECONDS = 4.0
MAX_INTERVAL_SECONDS = 120.0


def clamp_interval(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, value))


async def first_admin_id(session) -> int | None:
    users = list((await session.scalars(select(User).order_by(User.id))).all())
    for user in users:
        if user.can_admin():
            return user.id
    for user in users:
        if user.can_carts():
            return user.id
    return users[0].id if users else None


async def run_sync(app) -> dict:
    if not media_writable():
        return {"ok": True, "added": 0, "reason": "no_media"}
    db = app.extensions["sqlalchemy"]
    added_ids: list[int] = []
    stale_all: list = []
    async with db.session() as session:
        uploader_id = await first_admin_id(session)
        if uploader_id is None:
            return {"ok": True, "added": 0, "reason": "no_user"}
        carts = list((await session.scalars(select(Cart))).all())
        for cart in carts:
            if cart.mode is CartMode.RELAY:
                continue
            try:
                added, stale = await reconcile_cart_bank(session, cart, uploader_id)
            except Exception:
                _log.exception("bank sync failed for cart %s", cart.id)
                continue
            added_ids.extend(sound.id for sound in added)
            stale_all.extend(stale)
        await session.commit()
    forget_legacy_files(stale_all)
    if added_ids:
        try:
            from radiotomate.beets import BeetsIntegration

            beets = BeetsIntegration.get()
            for sound_id in added_ids:
                await beets.analyze_soon(sound_id)
        except RuntimeError:
            _log.debug("Beets unavailable; skipping analysis after bank sync")
        _log.info("bank sync attached %s sound(s)", len(added_ids))
    return {"ok": True, "added": len(added_ids)}


async def loop(app) -> None:
    interval = clamp_interval(app.config.get("BANK_SYNC_INTERVAL"))
    while True:
        try:
            await run_sync(app)
            await or_shutdown(asyncio.sleep(interval))
        except (ShutdownError, asyncio.CancelledError):
            break
        except Exception:
            _log.exception("bank sync loop crashed")

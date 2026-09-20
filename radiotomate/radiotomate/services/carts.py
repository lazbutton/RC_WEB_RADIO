"""Application-level cart identity and integrity rules."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import or_, select, update

from radiotomate.domain.autodj import require_expected_version
from radiotomate.domain.errors import DomainConflict
from radiotomate.models import Clock, ClockPosition, Sound
from radiotomate.services.media_bank import (
    inspect_audio,
    iter_bank_audio,
    resolve_bank_dir,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession

    from radiotomate.models import Cart


def require_cart_version(cart: Cart, expected: object) -> None:
    require_expected_version(cart.version, expected)


async def sync_cart_display_titles(session: ormSession, cart: Cart) -> None:
    """Keep legacy title columns aligned after a cart rename."""
    await session.execute(
        update(Clock)
        .where(Clock.fallback_cart_id == cart.id)
        .values(fallback_cart_title=cart.title)
    )
    await session.execute(
        update(ClockPosition)
        .where(ClockPosition.cart_id == cart.id)
        .values(cart_title=cart.title)
    )
    await session.execute(
        update(ClockPosition)
        .where(ClockPosition.fallback_cart_id == cart.id)
        .values(fallback_cart_title=cart.title)
    )


async def assert_cart_deletable(session: ormSession, cart: Cart) -> None:
    clock_id = await session.scalar(
        select(Clock.id).where(Clock.fallback_cart_id == cart.id).limit(1)
    )
    position_id = await session.scalar(
        select(ClockPosition.id)
        .where(
            or_(
                ClockPosition.cart_id == cart.id,
                ClockPosition.fallback_cart_id == cart.id,
            )
        )
        .limit(1)
    )
    if clock_id is not None or position_id is not None:
        raise DomainConflict(
            "This cart is referenced by a clock and cannot be deleted."
        )


async def sync_bank_folder(
    session: ormSession, cart: Cart, uploader_id: int
) -> list[Sound]:
    """Link new audio files from cart.bank_folder. Never copies or deletes the bank."""
    raw = str(cart.bank_folder or "").strip()
    if not raw or not cart.id:
        return []
    folder = resolve_bank_dir(raw)
    files = await asyncio.to_thread(iter_bank_audio, folder)
    paths = (
        await session.scalars(select(Sound.path).where(Sound.cart_id == cart.id))
    ).all()
    already = {Path(path).resolve() for path in paths if path}
    added: list[Sound] = []
    rank = await Sound.next_rank(session, cart.id)
    for path in files:
        resolved = path.resolve()
        if resolved in already:
            continue
        length = await asyncio.to_thread(inspect_audio, path)
        if length is None:
            continue
        already.add(resolved)
        sound = Sound(
            cart_id=cart.id,
            rank=rank,
            title=path.name,
            path=path,
            duration=int(length),
            uploader_id=uploader_id,
        )
        session.add(sound)
        added.append(sound)
        rank += 1
    if added:
        await session.flush()
    return added

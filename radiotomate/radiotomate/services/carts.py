"""Application-level cart identity and integrity rules."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete, or_, select, update

from radiotomate.domain.autodj import require_expected_version
from radiotomate.domain.errors import DomainConflict, DomainValidationError
from radiotomate.models import Clock, ClockPosition, Sound
from radiotomate.services.media_bank import (
    bind_cart_bank,
    copy_into_bank,
    forget_legacy_files,
    inspect_audio,
    iter_bank_audio,
    media_root,
    owned_by_cart_bank,
    path_in_media_bank,
    resolve_bank_dir,
    trash_exclusive_file,
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


def _resolved(path: Path | None) -> Path | None:
    if not path:
        return None
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError):
        return None


async def count_path_refs(
    session: ormSession, path: Path, exclude_id: int | None = None
) -> int:
    target = _resolved(path)
    if target is None:
        return 0
    sounds = list(await session.scalars(select(Sound)))
    count = 0
    for sound in sounds:
        if exclude_id is not None and sound.id == exclude_id:
            continue
        other = _resolved(sound.path)
        if other == target:
            count += 1
    return count


async def release_sound_file(session: ormSession, cart: Cart, sound: Sound) -> None:
    resolved = _resolved(sound.path)
    if resolved is None or not resolved.is_file():
        return
    others = await count_path_refs(session, resolved, exclude_id=sound.id)
    if others > 0:
        return
    if path_in_media_bank(resolved):
        if owned_by_cart_bank(resolved, cart.bank_folder):
            await asyncio.to_thread(trash_exclusive_file, resolved, 0)
        return
    resolved.unlink(missing_ok=True)


async def migrate_cart_sounds_to_bank(session: ormSession, cart: Cart) -> list[Path]:
    """Copy DATA_ROOT sounds into the bank, then repoint Sound.path."""
    stale: list[Path] = []
    raw = str(cart.bank_folder or "").strip()
    if not raw or not cart.id:
        return stale
    try:
        dest = resolve_bank_dir(raw)
    except DomainValidationError:
        return stale
    sounds = list(
        await session.scalars(select(Sound).where(Sound.cart_id == cart.id))
    )
    for sound in sounds:
        resolved = _resolved(sound.path)
        if resolved is None:
            continue
        if path_in_media_bank(resolved) and resolved.is_file():
            continue
        if not resolved.is_file():
            continue
        copied = await asyncio.to_thread(copy_into_bank, resolved, dest, sound.id)
        if copied is None:
            continue
        stale.append(resolved)
        sound.path = copied
    return stale


async def _stable_new_files(files: list[Path], already: set[Path]) -> list[Path]:
    pending = [path for path in files if path.resolve() not in already]
    if not pending:
        return []
    sizes: dict[Path, int] = {}
    for path in pending:
        try:
            sizes[path] = path.stat().st_size
        except OSError:
            continue
    await asyncio.sleep(0.2)
    stable: list[Path] = []
    for path, first in sizes.items():
        try:
            current = path.stat().st_size
        except OSError:
            continue
        if first <= 0 or current != first:
            continue
        stable.append(path)
    return stable


def _missing_media_ids(sounds: list[Sound]) -> list[int]:
    pruned_ids: list[int] = []
    for sound in sounds:
        resolved = _resolved(sound.path)
        if resolved is None or not path_in_media_bank(resolved):
            continue
        if resolved.is_file() or sound.id is None:
            continue
        pruned_ids.append(sound.id)
    return pruned_ids


async def sync_bank_folder(
    session: ormSession, cart: Cart, uploader_id: int
) -> list[Sound]:
    """Attach stable new bank files and drop Sound rows whose file is gone."""
    raw = str(cart.bank_folder or "").strip()
    if not raw or not cart.id:
        return []
    folder = resolve_bank_dir(raw)
    if not folder.is_dir() or not media_root().is_dir():
        return []
    files = await asyncio.to_thread(iter_bank_audio, folder)
    sounds = list(
        await session.scalars(select(Sound).where(Sound.cart_id == cart.id))
    )
    already: set[Path] = set()
    for sound in sounds:
        resolved = _resolved(sound.path)
        if resolved is not None:
            already.add(resolved)

    added: list[Sound] = []
    rank = await Sound.next_rank(session, cart.id)
    for path in await _stable_new_files(files, already):
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

    pruned_ids = _missing_media_ids(sounds)
    if pruned_ids:
        await session.execute(delete(Sound).where(Sound.id.in_(pruned_ids)))
        for sound in sounds:
            if sound.id in pruned_ids:
                session.expunge(sound)

    if added or pruned_ids:
        await session.flush()
    return added


async def reconcile_cart_bank(
    session: ormSession, cart: Cart, uploader_id: int
) -> tuple[list[Sound], list[Path]]:
    bind_cart_bank(cart)
    stale = await migrate_cart_sounds_to_bank(session, cart)
    added = await sync_bank_folder(session, cart, uploader_id)
    return added, stale


__all__ = [
    "assert_cart_deletable",
    "forget_legacy_files",
    "reconcile_cart_bank",
    "release_sound_file",
    "require_cart_version",
    "sync_bank_folder",
    "sync_cart_display_titles",
]

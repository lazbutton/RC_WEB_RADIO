"""Desk (pupitre) edits of the rundown: pins, suppressions, patches, reorder.

Split out of ``execution.py``; the forecast/reconcile core stays there and this
module only *edits* rows that the clock will later push.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select

from radiotomate.domain.errors import (
    DomainConflict,
    DomainNotFound,
    DomainValidationError,
)
from radiotomate.domain.execution import naive_datetime, rundown_summary
from radiotomate.enums import PositionKind, RundownStatus, WhenMode
from radiotomate.models import RundownItem, Sound
from radiotomate.scheduler.execution import (
    _CLOSED_DESK_STATUSES,
    DESK_KINDS,
    DESK_ORIGIN,
    _next_sequence,
    ensure_programming_version,
    item_to_payload,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession


def desk_queue_for(kind: str) -> str:
    if kind == PositionKind.JINGLE.value:
        return "jingles"
    if kind == PositionKind.MUSIQUE.value:
        return "autodj"
    return "carts"


def _desk_resource(artist: str, title: str) -> str:
    artist = artist.strip()
    title = title.strip()
    if artist and title:
        return f"{artist} — {title}"
    return title or artist or "Titre"


def _parse_iso(value: str) -> datetime:
    raw = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    parsed = naive_datetime(raw)
    if parsed is None:
        raise DomainValidationError("Horodatage invalide.", field="at")
    return parsed


def item_is_locked(item: RundownItem) -> bool:
    if item.status == RundownStatus.ON_AIR.value:
        return True
    return item.when_mode == WhenMode.ANCHORED.value


async def list_active_desk_items(session: ormSession) -> list[RundownItem]:
    rows = list(
        await session.scalars(
            select(RundownItem)
            .where(
                RundownItem.origin == DESK_ORIGIN,
                RundownItem.status.notin_(list(_CLOSED_DESK_STATUSES)),
            )
            .order_by(RundownItem.planned_at, RundownItem.sequence)
        )
    )
    return rows


def merge_desk_into_items(
    forecast: list[dict],
    desk_payloads: list[dict],
) -> list[dict]:
    suppress_at = {
        str(row.get("suppress_at") or row.get("hides_at") or "")
        for row in desk_payloads
        if row.get("suppress_at") or row.get("hides_at")
    }
    visible_desk = [
        row for row in desk_payloads if row.get("path") or not row.get("suppress_at")
    ]
    kept: list[dict] = []
    for item in forecast:
        origin = str(item.get("origin") or "clock")
        if origin == DESK_ORIGIN:
            continue
        stamped = dict(item)
        stamped.setdefault("origin", origin)
        if not stamped.get("id"):
            key = stamped.get("path") or stamped.get("resource") or ""
            stamped["id"] = f"clock|{stamped.get('at')}|{key}"
        at = str(stamped.get("at") or "")
        if at in suppress_at and stamped.get("when") != WhenMode.ANCHORED.value:
            continue
        kept.append(stamped)

    combined = kept + visible_desk

    def sort_key(row: dict) -> tuple[str, int]:
        origin = 0 if row.get("origin") == DESK_ORIGIN else 1
        return (str(row.get("at") or ""), origin)

    combined.sort(key=sort_key)
    return combined


async def expire_past_desk_items(
    session: ormSession, now: datetime | None = None
) -> None:
    """Drop unlocked desk pins whose planned window is already over."""
    cutoff = naive_datetime(now) or datetime.now()
    rows = await list_active_desk_items(session)
    stale: list[RundownItem] = []
    for row in rows:
        if item_is_locked(row):
            continue
        start = row.planned_at
        if start is None:
            continue
        hold = max(float(row.duration or 0), 30.0)
        if start + timedelta(seconds=hold) <= cutoff:
            stale.append(row)
    if not stale:
        return
    for row in stale:
        await session.delete(row)
    await session.commit()


async def attach_desk_pins(session: ormSession, payload: dict) -> dict:
    await expire_past_desk_items(session)
    desk_rows = await list_active_desk_items(session)
    desk_payloads = []
    for row in desk_rows:
        item = item_to_payload(row)
        details = dict(row.details or {})
        if details.get("suppress_at"):
            item["suppress_at"] = details["suppress_at"]
        desk_payloads.append(item)
    items = merge_desk_into_items(list(payload.get("items") or []), desk_payloads)
    merged = dict(payload)
    merged["items"] = items
    merged["summary"] = rundown_summary(items)
    return merged


async def _on_air_end(session: ormSession) -> datetime | None:
    row = await session.scalar(
        select(RundownItem)
        .where(RundownItem.status == RundownStatus.ON_AIR.value)
        .order_by(RundownItem.started_at.desc(), RundownItem.planned_at.desc())
        .limit(1)
    )
    if row is None:
        return None
    start = row.started_at or row.planned_at
    return start + timedelta(seconds=float(row.duration or 0))


async def _planned_at_for_insert(
    session: ormSession,
    *,
    after_id: str | None,
    replace_at: str | None,
) -> datetime:
    if replace_at:
        return _parse_iso(replace_at)
    if after_id:
        after = await RundownItem.from_id(session, after_id)
        if after is not None:
            start = after.started_at or after.planned_at
            return start + timedelta(seconds=float(after.duration or 0))
    on_air_end = await _on_air_end(session)
    if on_air_end is not None:
        return on_air_end
    return datetime.now()


async def insert_desk_item(session: ormSession, data: dict) -> RundownItem:
    suppress_at = str(data.get("suppress_at") or "").strip()
    if suppress_at:
        return await _insert_suppress(session, suppress_at)

    path = str(data.get("path") or "").strip()
    if not path:
        raise DomainValidationError("Chemin audio requis.", field="path")
    kind = str(data.get("kind") or PositionKind.SON.value).strip()
    if kind not in DESK_KINDS:
        raise DomainValidationError("Type audio inconnu.", field="kind")
    artist = str(data.get("artist") or "").strip()
    title = str(data.get("title") or "").strip()
    try:
        duration = float(data.get("duration") or 0)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("Durée invalide.", field="duration") from exc
    if duration <= 0:
        duration = 8.0

    sound = await Sound.from_media_path(session, path)
    version = await ensure_programming_version(session)
    planned_at = await _planned_at_for_insert(
        session,
        after_id=str(data.get("after_id") or "").strip() or None,
        replace_at=str(data.get("replace_at") or "").strip() or None,
    )
    details = {
        "artist": artist,
        "title": title or _desk_resource(artist, title),
        "cart": str(data.get("cart") or ""),
    }
    if data.get("replace_at"):
        details["hides_at"] = str(data.get("replace_at"))
    item = RundownItem(
        id=str(uuid4()),
        programming_version_id=version.id,
        sequence=await _next_sequence(session),
        planned_at=naive_datetime(planned_at) or datetime.now(),
        duration=duration,
        kind=kind,
        when_mode=WhenMode.SEQUENTIAL.value,
        queue=desk_queue_for(kind),
        resource=_desk_resource(artist, title),
        path=path,
        details=details,
        status=RundownStatus.PLANNED.value,
        origin=DESK_ORIGIN,
        cart_id=sound.cart_id if sound is not None else None,
        sound_id=sound.id if sound is not None else None,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def _insert_suppress(session: ormSession, at: str) -> RundownItem:
    _parse_iso(at)
    version = await ensure_programming_version(session)
    item = RundownItem(
        id=str(uuid4()),
        programming_version_id=version.id,
        sequence=await _next_sequence(session),
        planned_at=_parse_iso(at),
        duration=0,
        kind=PositionKind.SON.value,
        when_mode=WhenMode.SEQUENTIAL.value,
        queue="carts",
        resource="",
        path=None,
        details={"suppress_at": at},
        status=RundownStatus.PLANNED.value,
        origin=DESK_ORIGIN,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


def _require_mutable_desk(item: RundownItem) -> None:
    if item_is_locked(item):
        raise DomainConflict(
            "Impossible de modifier un passage à l'antenne ou une ancre dure."
        )
    if item.origin != DESK_ORIGIN:
        raise DomainConflict("Ce passage vient de l'horloge.")


async def patch_desk_item(session: ormSession, item_id: str, data: dict) -> RundownItem:
    item = await RundownItem.from_id(session, item_id)
    if item is None:
        raise DomainNotFound("Passage introuvable.")
    _require_mutable_desk(item)
    path = str(data.get("path") or item.path or "").strip()
    if not path:
        raise DomainValidationError("Chemin audio requis.", field="path")
    kind = str(data.get("kind") or item.kind).strip()
    if kind not in DESK_KINDS:
        raise DomainValidationError("Type audio inconnu.", field="kind")
    artist = str(data.get("artist") or (item.details or {}).get("artist") or "").strip()
    title = str(data.get("title") or (item.details or {}).get("title") or "").strip()
    if data.get("duration") is not None:
        try:
            duration = float(data.get("duration") or 0)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("Durée invalide.", field="duration") from exc
        if duration > 0:
            item.duration = duration
    sound = await Sound.from_media_path(session, path)
    item.path = path
    item.kind = kind
    item.queue = desk_queue_for(kind)
    item.resource = _desk_resource(artist, title)
    item.details = {
        **dict(item.details or {}),
        "artist": artist,
        "title": title or item.resource,
    }
    item.cart_id = sound.cart_id if sound is not None else None
    item.sound_id = sound.id if sound is not None else None
    await session.commit()
    await session.refresh(item)
    return item


async def delete_desk_item(session: ormSession, item_id: str) -> None:
    item = await RundownItem.from_id(session, item_id)
    if item is None:
        raise DomainNotFound("Passage introuvable.")
    if item_is_locked(item):
        raise DomainConflict(
            "Impossible de retirer un passage à l'antenne ou une ancre dure."
        )
    await session.delete(item)
    await session.commit()


async def reorder_desk_items(session: ormSession, ids: list[str]) -> None:
    if not ids:
        raise DomainValidationError("Liste d'identifiants requise.", field="ids")
    rows = {
        row.id: row
        for row in await list_active_desk_items(session)
        if not (row.details or {}).get("suppress_at")
    }
    ordered: list[RundownItem] = []
    for item_id in ids:
        row = rows.get(item_id)
        if row is None:
            continue
        if item_is_locked(row):
            raise DomainConflict("Impossible de réordonner un passage verrouillé.")
        ordered.append(row)
    if not ordered:
        raise DomainValidationError("Aucun passage pupitre à réordonner.", field="ids")
    cursor = await _on_air_end(session) or datetime.now()
    for index, row in enumerate(ordered):
        row.planned_at = naive_datetime(cursor) or datetime.now()
        row.sequence = index
        cursor = row.planned_at + timedelta(seconds=float(row.duration or 0))
    await session.commit()

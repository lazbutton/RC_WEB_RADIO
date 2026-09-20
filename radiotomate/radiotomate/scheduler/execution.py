"""Persist the forecast rundown and reconcile it with as-run events."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import delete, select

from radiotomate.domain.errors import (
    DomainConflict,
    DomainNotFound,
    DomainValidationError,
)
from radiotomate.domain.execution import (
    ENGAGED_STATUSES,
    REPLACEABLE_STATUSES,
    RESET_KEEP_STATUSES,
    naive_datetime,
    programming_fingerprint,
    rundown_summary,
    status_label,
)
from radiotomate.enums import CommandStatus, PositionKind, RundownStatus, WhenMode
from radiotomate.models import (
    AutoDJSlot,
    Clock,
    MetadataLog,
    PlayoutCommand,
    ProgrammingVersion,
    RundownItem,
    Setting,
    Sound,
)
from radiotomate.scheduler.clock import (
    SETTING_CLOCK_SEQ_CURSOR,
    now_paris,
    reset_sequencer,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession

    from radiotomate.beets import BeetsIntegration

_log = logging.getLogger(__name__)

SETTING_FIRED_ANCHORS = "clock_fired_anchors"
SETTING_PROGRAMMING_VERSION = "clock_programming_version"
DESK_ORIGIN = "desk"
DESK_KINDS = {
    PositionKind.MUSIQUE.value,
    PositionKind.JINGLE.value,
    PositionKind.SON.value,
    PositionKind.PUB.value,
}
_CLOSED_DESK_STATUSES = {
    RundownStatus.PLAYED.value,
    RundownStatus.SKIPPED.value,
    RundownStatus.REPLACED.value,
}


async def programming_parts(session: ormSession) -> list[str]:
    clocks = await Clock.all(session, load_positions=False)
    slots = list(await AutoDJSlot.all(session))
    parts = [
        f"c:{clock.id}:{clock.version}:{clock.name}"
        for clock in sorted(clocks, key=lambda row: row.id)
    ]
    parts.extend(
        (
            f"s:{slot.id}:{slot.version}:{slot.day_of_week}:{slot.minute}:"
            f"{slot.clock_id}"
        )
        for slot in slots
    )
    cursor = await Setting.from_key(session, SETTING_CLOCK_SEQ_CURSOR)
    parts.append(f"cursor:{cursor.value if cursor else '0'}")
    return parts


async def ensure_programming_version(
    session: ormSession,
    *,
    source: str = "autodj",
) -> ProgrammingVersion:
    version_id = programming_fingerprint(await programming_parts(session))
    existing = await session.get(ProgrammingVersion, version_id)
    if existing is not None:
        return existing
    version = ProgrammingVersion(
        id=version_id,
        source=source,
        description="clocks+dayparts",
        created=datetime.now(),
    )
    session.add(version)
    await session.flush()
    await Setting.upsert(session, SETTING_PROGRAMMING_VERSION, version_id)
    return version


def fired_anchor_key(date_iso: str, hour: int, minute: int) -> str:
    return f"{date_iso}|{hour}|{minute}"


def parse_fired_anchor(raw: str) -> tuple[str, int, int] | None:
    parts = raw.split("|")
    if len(parts) != 3:
        return None
    try:
        return parts[0], int(parts[1]), int(parts[2])
    except ValueError:
        return None


async def load_fired_anchors(session: ormSession) -> set[tuple[str, int, int]]:
    row = await Setting.from_key(session, SETTING_FIRED_ANCHORS)
    if row is None or not row.value:
        return set()
    try:
        raw = json.loads(row.value)
    except json.JSONDecodeError:
        return set()
    keys: set[tuple[str, int, int]] = set()
    if not isinstance(raw, list):
        return keys
    for item in raw:
        parsed = parse_fired_anchor(str(item))
        if parsed is not None:
            keys.add(parsed)
    return keys


async def persist_fired_anchors(
    session: ormSession,
    keys: set[tuple[str, int, int]],
) -> None:
    payload = [
        fired_anchor_key(date_iso, hour, minute)
        for date_iso, hour, minute in sorted(keys)
    ]
    await Setting.upsert(session, SETTING_FIRED_ANCHORS, json.dumps(payload))


def item_to_payload(item: RundownItem) -> dict:
    details = dict(item.details or {})
    payload = {
        "id": item.id,
        "at": item.planned_at.isoformat(),
        "kind": item.kind,
        "when": item.when_mode,
        "resource": item.resource,
        "queue": item.queue,
        "status": status_label(item.status),
        "status_code": item.status,
        "reason": item.reason,
        "clock": item.clock_name,
        "daypart": item.daypart,
        "duration": item.duration,
        "clock_id": item.clock_id,
        "position_id": item.clock_position_id,
        "cart_id": item.cart_id,
        "sound_id": item.sound_id,
        "beets_id": item.beets_id,
        "fallback_used": item.fallback_used,
        "programming_version": item.programming_version_id,
        "origin": item.origin,
    }
    if item.when_mode == "anchored":
        payload["minute"] = details.get("minute", item.details.get("minute"))
        payload["sync"] = item.sync
    if details.get("category"):
        payload["category"] = details["category"]
    if details.get("cart"):
        payload["cart"] = details["cart"]
    if item.path:
        payload["path"] = item.path
    if details.get("artist"):
        payload["artist"] = details["artist"]
    if details.get("title"):
        payload["title"] = details["title"]
    if details.get("hides_at"):
        payload["hides_at"] = details["hides_at"]
    if details.get("suppress_at"):
        payload["suppress_at"] = details["suppress_at"]
    return payload


def _status_from_forecast(payload: dict) -> str:
    raw = str(payload.get("status") or "")
    if raw in {item.value for item in RundownStatus}:
        return raw
    if raw == "secours" or payload.get("fallback_used"):
        return RundownStatus.RESCUE.value
    if raw == "sauté":
        return RundownStatus.SKIPPED.value
    return RundownStatus.PLANNED.value


def item_from_forecast(
    payload: dict,
    version: ProgrammingVersion,
    sequence: int,
) -> RundownItem:
    planned_at = naive_datetime(datetime.fromisoformat(payload["at"]))
    details = {
        "minute": payload.get("minute"),
        "category": payload.get("category"),
        "cart": payload.get("cart"),
        "glissement": payload.get("reason") == "glissement",
    }
    return RundownItem(
        id=str(payload.get("id") or uuid4()),
        programming_version_id=version.id,
        sequence=sequence,
        planned_at=planned_at,
        duration=float(payload.get("duration") or 0),
        kind=str(payload.get("kind") or ""),
        when_mode=str(payload.get("when") or "sequential"),
        sync=payload.get("sync"),
        queue=str(payload.get("queue") or "autodj"),
        resource=str(payload.get("resource") or ""),
        path=payload.get("path"),
        clock_name=payload.get("clock"),
        daypart=payload.get("daypart"),
        details=details,
        status=_status_from_forecast(payload),
        reason=payload.get("reason"),
        origin="clock",
        clock_id=payload.get("clock_id"),
        clock_position_id=payload.get("position_id"),
        cart_id=payload.get("cart_id"),
        sound_id=payload.get("sound_id"),
        beets_id=payload.get("beets_id"),
        fallback_used=bool(payload.get("fallback_used")),
        created=datetime.now(),
    )


async def replace_forecast_items(
    session: ormSession,
    version: ProgrammingVersion,
    payloads: list[dict],
    *,
    now: datetime,
) -> list[RundownItem]:
    cutoff = naive_datetime(now)
    await session.execute(
        delete(RundownItem).where(
            RundownItem.status.in_(list(REPLACEABLE_STATUSES)),
            RundownItem.planned_at >= cutoff,
            RundownItem.origin != DESK_ORIGIN,
        )
    )
    items = [
        item_from_forecast(payload, version, sequence)
        for sequence, payload in enumerate(payloads)
    ]
    session.add_all(items)
    await session.flush()
    return items


async def materialize_rundown(
    session: ormSession,
    beets: BeetsIntegration,
    now: datetime | None = None,
    horizon_min: int = 30,
    cursor: int | None = None,
) -> dict:
    from radiotomate.scheduler.rundown import build_rundown

    now = now_paris(now)
    forecast = await build_rundown(
        session,
        beets,
        now=now,
        horizon_min=horizon_min,
        cursor=cursor,
    )
    version = await ensure_programming_version(session)
    await replace_forecast_items(
        session,
        version,
        list(forecast.get("items") or []),
        now=now,
    )
    rows = await _visible_items(session, now, horizon_min=horizon_min)
    await session.commit()
    items = [item_to_payload(item) for item in rows]
    return {
        **forecast,
        "horizon_min": horizon_min,
        "programming_version": version.id,
        "items": items,
        "summary": rundown_summary(items),
    }


async def _visible_items(
    session: ormSession,
    now: datetime,
    *,
    horizon_min: int = 30,
) -> list[RundownItem]:
    lookback = max(15, min(int(horizon_min), 60))
    cutoff = naive_datetime(now) - timedelta(minutes=lookback)
    visible = set(REPLACEABLE_STATUSES) | ENGAGED_STATUSES | {
        RundownStatus.ON_AIR.value,
        RundownStatus.PLAYED.value,
        RundownStatus.FAILED.value,
    }
    rows = list(
        await session.scalars(
            select(RundownItem)
            .where(
                RundownItem.status.in_(list(visible)),
                RundownItem.planned_at >= cutoff,
            )
            .order_by(RundownItem.planned_at, RundownItem.sequence)
            .limit(800)
        )
    )
    return rows


async def preview_rundown(
    session: ormSession,
    beets: BeetsIntegration,
    now: datetime | None = None,
    horizon_min: int = 30,
) -> dict:
    """Forecast for the console: no DELETE/INSERT of rundown_items."""
    from radiotomate.scheduler.rundown import build_rundown

    now = now_paris(now)
    forecast = await build_rundown(
        session,
        beets,
        now=now,
        horizon_min=horizon_min,
    )
    items = list(forecast.get("items") or [])
    return {
        **forecast,
        "horizon_min": horizon_min,
        "items": items,
        "summary": rundown_summary(items),
    }


async def clear_replaceable_forecast(session: ormSession) -> None:
    """Drop planned/rescue forecast rows and planned desk pins. On-air stays."""
    await session.execute(
        delete(RundownItem).where(
            RundownItem.status.in_(list(REPLACEABLE_STATUSES)),
        )
    )


async def clear_live_forecast(session: ormSession) -> None:
    """Drop every rundown row except on-air and already played."""
    doomed = list(
        await session.scalars(
            select(RundownItem).where(
                RundownItem.status.notin_(list(RESET_KEEP_STATUSES)),
            )
        )
    )
    doomed_ids = [item.id for item in doomed]
    commands = list(
        await session.scalars(
            select(PlayoutCommand).where(
                PlayoutCommand.status.in_(
                    [
                        CommandStatus.PENDING.value,
                        CommandStatus.SENDING.value,
                    ]
                )
            )
        )
    )
    if doomed_ids:
        linked = list(
            await session.scalars(
                select(PlayoutCommand).where(
                    PlayoutCommand.rundown_item_id.in_(doomed_ids),
                )
            )
        )
        seen = {command.id for command in commands}
        for command in linked:
            if command.id not in seen:
                commands.append(command)
    for command in commands:
        command.rundown_item_id = None
        if command.status in {
            CommandStatus.PENDING.value,
            CommandStatus.SENDING.value,
        }:
            command.status = CommandStatus.EXPIRED.value
            command.last_error = "conducteur reset"
    if doomed_ids:
        await session.flush()
        await session.execute(
            delete(RundownItem).where(RundownItem.id.in_(doomed_ids))
        )


async def reset_conducteur(
    session: ormSession,
    beets: BeetsIntegration,
    horizon_min: int = 30,
) -> dict:
    """Reset motif cursor, drop queued forecast, rebuild from now."""
    await reset_sequencer(session, commit=False)
    await clear_live_forecast(session)
    await session.commit()
    payload = await preview_rundown(session, beets, horizon_min=horizon_min)
    payload["action"] = "reset"
    return payload


async def published_rundown(
    session: ormSession,
    beets: BeetsIntegration,
    now: datetime | None = None,
    horizon_min: int = 30,
) -> dict:
    return await materialize_rundown(
        session,
        beets,
        now=now,
        horizon_min=horizon_min,
    )


async def record_live_item(
    session: ormSession,
    *,
    payload: dict,
    status: str = RundownStatus.RESERVED.value,
) -> RundownItem:
    version = await ensure_programming_version(session)
    item = item_from_forecast(payload, version, payload.get("sequence") or 0)
    item.status = status
    item.reserved_at = datetime.now()
    session.add(item)
    await session.flush()
    return item


async def reconcile_as_run(session: ormSession, log: MetadataLog) -> None:
    """Link an as-run row to its rundown item without merging the two tables."""
    item = None
    if log.rundown_item_id:
        item = await RundownItem.from_id(session, log.rundown_item_id)
    if item is None and log.extra:
        extra_id = log.extra.get("radiotomate_item_id")
        if extra_id:
            item = await RundownItem.from_id(session, str(extra_id))
        extra_sound = log.extra.get("radiotomate_sound_id")
        if item is None and extra_sound:
            try:
                item = await _latest_item_for_sound(session, int(extra_sound))
            except (TypeError, ValueError):
                item = None
    if item is None:
        item = await _latest_open_item(session, log)
    if item is None:
        return
    log.rundown_item_id = item.id
    previous = list(
        await session.scalars(
            select(RundownItem).where(
                RundownItem.status == RundownStatus.ON_AIR.value,
                RundownItem.id != item.id,
            )
        )
    )
    now = naive_datetime(log.on_air) or datetime.now()
    for row in previous:
        row.status = RundownStatus.PLAYED.value
        row.closed_at = now
    item.status = RundownStatus.ON_AIR.value
    item.started_at = item.started_at or now
    if log.playout_command_id:
        item.details = {
            **dict(item.details or {}),
            "playout_command_id": log.playout_command_id,
        }


async def _latest_item_for_sound(
    session: ormSession,
    sound_id: int,
) -> RundownItem | None:
    return await session.scalar(
        select(RundownItem)
        .where(RundownItem.sound_id == sound_id)
        .order_by(RundownItem.created.desc())
        .limit(1)
    )


async def _latest_open_item(
    session: ormSession,
    log: MetadataLog,
) -> RundownItem | None:
    q = select(RundownItem).where(
        RundownItem.status.in_(
            [
                RundownStatus.IN_QUEUE.value,
                RundownStatus.ACCEPTED.value,
                RundownStatus.SENT.value,
            ]
        )
    )
    if log.title:
        q = q.where(RundownItem.resource.contains(log.title))
    return await session.scalar(q.order_by(RundownItem.created.desc()).limit(1))


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
        row
        for row in desk_payloads
        if row.get("path") or not row.get("suppress_at")
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


async def expire_past_desk_items(session: ormSession, now: datetime | None = None) -> None:
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


async def _next_sequence(session: ormSession) -> int:
    current = await session.scalar(
        select(RundownItem.sequence).order_by(RundownItem.sequence.desc()).limit(1)
    )
    return int(current or 0) + 1


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

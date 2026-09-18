"""Persist the forecast rundown and reconcile it with as-run events."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import delete, select

from radiotomate.domain.execution import (
    ENGAGED_STATUSES,
    REPLACEABLE_STATUSES,
    naive_datetime,
    programming_fingerprint,
    rundown_summary,
    status_label,
)
from radiotomate.enums import RundownStatus
from radiotomate.models import (
    AutoDJSlot,
    Clock,
    MetadataLog,
    ProgrammingVersion,
    RundownItem,
    Setting,
)
from radiotomate.scheduler.clock import SETTING_CLOCK_SEQ_CURSOR, now_paris

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession

    from radiotomate.beets import BeetsIntegration

_log = logging.getLogger(__name__)

SETTING_FIRED_ANCHORS = "clock_fired_anchors"
SETTING_PROGRAMMING_VERSION = "clock_programming_version"


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

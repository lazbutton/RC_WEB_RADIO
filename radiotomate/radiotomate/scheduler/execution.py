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
)
from radiotomate.scheduler.clock import (
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
    # The motif cursor is *state*, not programming: including it minted one
    # ProgrammingVersion per track (196 rows in 20 h on Nasgul).
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
    if raw == "manquant":
        return RundownStatus.FAILED.value
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
    if payload.get("artist"):
        details["artist"] = payload["artist"]
    if payload.get("title"):
        details["title"] = payload["title"]
    if payload.get("rg_track_gain") is not None:
        details["rg_track_gain"] = payload["rg_track_gain"]
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


def _align_tz(dt: datetime, ref: datetime) -> datetime:
    if dt.tzinfo is None and ref.tzinfo is not None:
        return dt.replace(tzinfo=ref.tzinfo)
    if dt.tzinfo is not None and ref.tzinfo is None:
        return dt.replace(tzinfo=None)
    return dt


def _row_end_at(row: RundownItem) -> datetime:
    return row.planned_at + timedelta(seconds=max(0.0, float(row.duration or 0)))


def _horizon_end(rows: list[RundownItem], now: datetime) -> datetime:
    last = None
    for row in rows:
        end = _align_tz(_row_end_at(row), now)
        if last is None or end > last:
            last = end
    return last if last is not None else now


async def _cursor_after_items(
    session: ormSession,
    rows: list[RundownItem],
) -> int | None:
    for row in reversed(rows):
        if row.origin == DESK_ORIGIN:
            continue
        if row.when_mode == WhenMode.ANCHORED.value:
            continue
        if not row.clock_id or not row.clock_position_id:
            continue
        clock = await Clock.from_id(session, row.clock_id)
        if clock is None:
            continue
        for index, pos in enumerate(clock.sequential_positions()):
            if pos.id == row.clock_position_id:
                return index + 1
    return None


async def append_forecast_items(
    session: ormSession,
    version: ProgrammingVersion,
    payloads: list[dict],
    *,
    existing: list[RundownItem],
) -> list[RundownItem]:
    cutoff = None
    existing_keys: set[tuple] = set()
    for row in existing:
        end = _row_end_at(row)
        if cutoff is None or end > cutoff:
            cutoff = end
        planned = naive_datetime(row.planned_at)
        stamp = planned.replace(microsecond=0) if planned is not None else planned
        existing_keys.add((stamp, row.path or row.resource))
    sequence = await _next_sequence(session)
    items: list[RundownItem] = []
    cutoff_naive = naive_datetime(cutoff) if cutoff is not None else None
    for payload in payloads:
        try:
            planned = naive_datetime(datetime.fromisoformat(str(payload["at"])))
        except (KeyError, TypeError, ValueError):
            continue
        if planned is None:
            continue
        if cutoff_naive is not None and planned < cutoff_naive:
            continue
        key = (
            planned.replace(microsecond=0),
            payload.get("path") or payload.get("resource"),
        )
        if key in existing_keys:
            continue
        items.append(item_from_forecast(payload, version, sequence))
        sequence += 1
        existing_keys.add(key)
    if items:
        session.add_all(items)
        await session.flush()
    return items


async def _payload_from_visible(
    session: ormSession,
    rows: list[RundownItem],
    *,
    now: datetime,
    horizon_min: int,
    version: ProgrammingVersion,
) -> dict:
    items = [item_to_payload(row) for row in rows]
    clock = None
    daypart = None
    for item in items:
        if item.get("clock"):
            clock = item.get("clock")
            daypart = item.get("daypart")
            break
    if not clock:
        from radiotomate.scheduler.rundown import _header

        clock, daypart = await _header(session, now)
    return {
        "now": now.isoformat(),
        "horizon_min": horizon_min,
        "clock": clock,
        "daypart": daypart,
        "programming_version": version.id,
        "items": items,
        "summary": rundown_summary(items),
    }


def _coverage_rows(rows: list[RundownItem]) -> list[RundownItem]:
    """Rows that count as planned antenna. Failed rows stay visible for the
    console but never as coverage: a burst of unresolvable pushes (files moved
    by a bank relink) used to leave a window "full" of failures and nothing
    playable — hence silence."""
    return [row for row in rows if row.status != RundownStatus.FAILED.value]


async def ensure_forecast(
    session: ormSession,
    beets: BeetsIntegration,
    now: datetime | None = None,
    horizon_min: int = 30,
    cursor: int | None = None,
    *,
    commit: bool = True,
) -> dict:
    """Persist a stable rundown window: append when short, never re-draw from now."""
    from radiotomate.scheduler.rundown import build_rundown, forecast_still_covers

    now = now_paris(now)
    version = await ensure_programming_version(session)
    rows = _coverage_rows(await _visible_items(session, now, horizon_min=horizon_min))
    target_ahead = max(15, int(horizon_min))
    if rows and forecast_still_covers(
        {"items": [item_to_payload(row) for row in rows]},
        now,
        min_ahead_min=target_ahead,
    ):
        return await _payload_from_visible(
            session,
            rows,
            now=now,
            horizon_min=horizon_min,
            version=version,
        )

    if not rows:
        forecast = await build_rundown(
            session,
            beets,
            now=now,
            horizon_min=horizon_min,
            cursor=cursor,
        )
        rows = _coverage_rows(
            await _visible_items(session, now, horizon_min=horizon_min)
        )
        if rows and forecast_still_covers(
            {"items": [item_to_payload(row) for row in rows]},
            now,
            min_ahead_min=target_ahead,
        ):
            payload = await _payload_from_visible(
                session,
                rows,
                now=now,
                horizon_min=horizon_min,
                version=version,
            )
            if commit:
                await session.commit()
            return payload
        await replace_forecast_items(
            session,
            version,
            list(forecast.get("items") or []),
            now=now,
        )
    else:
        last_end = _horizon_end(rows, now)
        start_from = last_end if last_end > now else now
        follow = await _cursor_after_items(session, rows)
        remaining_min = int(
            (now + timedelta(minutes=horizon_min) - start_from).total_seconds() // 60
        )
        forecast = await build_rundown(
            session,
            beets,
            now=start_from,
            horizon_min=max(15, remaining_min + 1),
            cursor=follow if follow is not None else cursor,
        )
        await append_forecast_items(
            session,
            version,
            list(forecast.get("items") or []),
            existing=_coverage_rows(
                await _visible_items(session, now, horizon_min=horizon_min)
            ),
        )

    if commit:
        await session.commit()
    rows = await _visible_items(session, now, horizon_min=horizon_min)
    return await _payload_from_visible(
        session,
        rows,
        now=now,
        horizon_min=horizon_min,
        version=version,
    )


async def materialize_rundown(
    session: ormSession,
    beets: BeetsIntegration,
    now: datetime | None = None,
    horizon_min: int = 30,
    cursor: int | None = None,
) -> dict:
    return await ensure_forecast(
        session,
        beets,
        now=now,
        horizon_min=horizon_min,
        cursor=cursor,
    )


async def _visible_items(
    session: ormSession,
    now: datetime,
    *,
    horizon_min: int = 30,
) -> list[RundownItem]:
    lookback = max(15, min(int(horizon_min), 60))
    cutoff = naive_datetime(now) - timedelta(minutes=lookback)
    visible = (
        set(REPLACEABLE_STATUSES)
        | ENGAGED_STATUSES
        | {
            RundownStatus.ON_AIR.value,
            RundownStatus.PLAYED.value,
            RundownStatus.FAILED.value,
        }
    )
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
        await session.execute(delete(RundownItem).where(RundownItem.id.in_(doomed_ids)))


async def reset_conducteur(
    session: ormSession,
    beets: BeetsIntegration,
    horizon_min: int = 30,
) -> dict:
    """Reset motif cursor, drop queued forecast, persist a fresh window from now."""
    await reset_sequencer(session, commit=False)
    await clear_live_forecast(session)
    await session.commit()
    payload = await ensure_forecast(session, beets, horizon_min=horizon_min)
    payload = dict(payload)
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
    await realign_rundown(session, item, now)


REALIGN_TOLERANCE = timedelta(seconds=5)


async def realign_rundown(
    session: ormSession,
    item: RundownItem,
    started: datetime,
) -> timedelta:
    """
    Anchor the rundown on the as-run: the item that just went on air takes the
    real start time and every later sequential item slides by the same delta.
    Anchored positions (fixed minute) and desk pins keep their own time.
    Returns the applied shift (zero when within tolerance).
    """
    if item.planned_at is None or item.when_mode == WhenMode.ANCHORED.value:
        return timedelta(0)
    if item.origin == DESK_ORIGIN:
        return timedelta(0)
    planned = naive_datetime(item.planned_at)
    started = naive_datetime(started)
    if planned is None or started is None:
        return timedelta(0)
    delta = started - planned
    if abs(delta) < REALIGN_TOLERANCE:
        return timedelta(0)
    following = list(
        await session.scalars(
            select(RundownItem).where(
                RundownItem.id != item.id,
                RundownItem.status.in_(
                    list(
                        REPLACEABLE_STATUSES
                        | (ENGAGED_STATUSES - {RundownStatus.ON_AIR.value})
                    )
                ),
                RundownItem.when_mode != WhenMode.ANCHORED.value,
                RundownItem.origin != DESK_ORIGIN,
                RundownItem.planned_at >= item.planned_at,
            )
        )
    )
    item.planned_at = started
    for row in following:
        if row.planned_at is not None:
            row.planned_at = naive_datetime(row.planned_at) + delta
    _log.info(
        "rundown realigned by %+.0fs on %s (%d following item(s))",
        delta.total_seconds(),
        item.id,
        len(following),
    )
    return delta


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


async def _next_sequence(session: ormSession) -> int:
    current = await session.scalar(
        select(RundownItem.sequence).order_by(RundownItem.sequence.desc()).limit(1)
    )
    return int(current or 0) + 1


# Desk edits live in their own module; re-exported for existing callers.
from radiotomate.scheduler.desk import (  # noqa: E402, F401
    attach_desk_pins,
    delete_desk_item,
    desk_queue_for,
    expire_past_desk_items,
    insert_desk_item,
    item_is_locked,
    list_active_desk_items,
    merge_desk_into_items,
    patch_desk_item,
    reorder_desk_items,
)

"""Live show overlays on top of Auto-DJ dayparts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from radiotomate.domain.autodj import require_expected_version
from radiotomate.domain.emission import (
    SCOPE_SESSION,
    SCOPE_WEEKLY,
    STATE_ON_AIR,
    STATE_WAITING,
    is_harbor_source,
    now_paris,
    reject_weekly_overlap,
    validate_title_artist,
    validate_weekly_window,
    weekly_contains,
    weekly_end_at,
    weekly_start_at,
)
from radiotomate.domain.errors import DomainNotFound, DomainValidationError
from radiotomate.models.emission import Emission

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession

_on_air_cache: dict[str, dict | None] = {"payload": None}


def cached_on_air_override() -> dict | None:
    return _on_air_cache["payload"]


def set_on_air_cache(payload: dict | None) -> None:
    _on_air_cache["payload"] = payload


def emission_payload(row: Emission) -> dict:
    payload = {
        "id": row.id,
        "version": row.version,
        "scope": row.scope,
        "title": row.title,
        "artist": row.artist,
    }
    if row.scope == SCOPE_WEEKLY:
        payload["day_of_week"] = row.day_of_week
        payload["start_minute"] = row.start_minute
        payload["end_minute"] = row.end_minute
    else:
        payload["starts_at"] = row.starts_at.isoformat() if row.starts_at else None
        payload["ends_at"] = row.ends_at.isoformat() if row.ends_at else None
    return payload


def live_emission_payload(current: dict) -> dict:
    payload = {
        "id": current["id"],
        "title": current["title"],
        "artist": current["artist"],
        "state": current["state"],
        "until": current.get("until"),
        "scope": current.get("scope"),
    }
    if current.get("start_minute") is not None:
        payload["start_minute"] = current["start_minute"]
    if current.get("day_of_week") is not None:
        payload["day_of_week"] = current["day_of_week"]
    return payload


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def expire_idle_sessions(
    session: ormSession,
    source: object,
    now: datetime | None = None,
) -> bool:
    local = now_paris(now)
    harbor = is_harbor_source(source)
    changed = False
    for row in await Emission.all_sessions(session):
        ended = False
        ends = _aware(row.ends_at)
        if ends is not None and now_paris(ends) <= local:
            ended = True
        if (not harbor) or ended:
            await session.delete(row)
            changed = True
    return changed


def _weekly_current(row: Emission, now: datetime, source: object) -> dict | None:
    if row.day_of_week is None or row.start_minute is None or row.end_minute is None:
        return None
    if not weekly_contains(now, row.day_of_week, row.start_minute, row.end_minute):
        return None
    state = STATE_ON_AIR if is_harbor_source(source) else STATE_WAITING
    return {
        "id": row.id,
        "title": row.title,
        "artist": row.artist,
        "state": state,
        "scope": SCOPE_WEEKLY,
        "until": weekly_end_at(now, row.end_minute).isoformat(),
        "day_of_week": row.day_of_week,
        "start_minute": row.start_minute,
    }


def _session_current(row: Emission, source: object) -> dict | None:
    if not is_harbor_source(source):
        return None
    until = row.ends_at.isoformat() if row.ends_at else None
    return {
        "id": row.id,
        "title": row.title,
        "artist": row.artist,
        "state": STATE_ON_AIR,
        "scope": SCOPE_SESSION,
        "until": until,
    }


def _next_weekly(rows: list[Emission], now: datetime) -> dict | None:
    upcoming: list[tuple[datetime, Emission]] = []
    for row in rows:
        if (
            row.day_of_week is None
            or row.start_minute is None
            or row.end_minute is None
        ):
            continue
        start_at = weekly_start_at(now, row.day_of_week, row.start_minute)
        upcoming.append((start_at, row))
    if not upcoming:
        return None
    start_at, row = min(upcoming, key=lambda item: item[0])
    until = weekly_end_at(start_at, row.end_minute or 0)
    return {
        "id": row.id,
        "title": row.title,
        "artist": row.artist,
        "scope": SCOPE_WEEKLY,
        "at": start_at.isoformat(),
        "until": until.isoformat(),
        "day_of_week": row.day_of_week,
        "start_minute": row.start_minute,
        "end_minute": row.end_minute,
    }


async def resolve_emissions(
    session: ormSession,
    source: object,
    now: datetime | None = None,
) -> tuple[dict | None, dict | None]:
    local = now_paris(now)
    weeklies = await Emission.all_weekly(session)
    sessions = await Emission.all_sessions(session)
    current = None
    if sessions:
        current = _session_current(sessions[0], source)
    if current is None:
        for row in weeklies:
            found = _weekly_current(row, local, source)
            if found is not None:
                current = found
                break
    nxt = _next_weekly(weeklies, local)
    return current, nxt


async def attach_live_emission(session: ormSession, snapshot: dict) -> bool:
    source = snapshot.get("source") or ""
    dirty = await expire_idle_sessions(session, source)
    current, _nxt = await resolve_emissions(session, source)
    if current and current["state"] == STATE_ON_AIR:
        snapshot["title"] = current["title"]
        snapshot["artist"] = current["artist"]
        set_on_air_cache({"title": current["title"], "artist": current["artist"]})
    else:
        set_on_air_cache(None)
    snapshot["emission"] = live_emission_payload(current) if current else None
    return dirty


async def overlay_metadata_if_on_air(session: ormSession, raw_md: dict) -> None:
    source = raw_md.get("source") or ""
    await expire_idle_sessions(session, source)
    current, _nxt = await resolve_emissions(session, source)
    if current and current["state"] == STATE_ON_AIR:
        raw_md["title"] = current["title"]
        raw_md["artist"] = current["artist"]
        set_on_air_cache({"title": current["title"], "artist": current["artist"]})
    else:
        set_on_air_cache(None)


async def list_emissions_payload(session: ormSession, source: object) -> dict:
    dirty = await expire_idle_sessions(session, source)
    weeklies = await Emission.all_weekly(session)
    current, nxt = await resolve_emissions(session, source)
    return {
        "emissions": [emission_payload(row) for row in weeklies],
        "current": live_emission_payload(current) if current else None,
        "next": nxt,
        "_dirty": dirty,
    }


async def create_weekly(session: ormSession, data: dict) -> Emission:
    title, artist = validate_title_artist(data.get("title"), data.get("artist"))
    day, start, end = validate_weekly_window(
        data.get("day_of_week"),
        data.get("start_minute"),
        data.get("end_minute"),
    )
    existing = [
        (row.id, int(row.day_of_week), int(row.start_minute), int(row.end_minute))
        for row in await Emission.all_weekly(session)
        if row.day_of_week is not None
        and row.start_minute is not None
        and row.end_minute is not None
    ]
    reject_weekly_overlap(
        existing,
        day_of_week=day,
        start_minute=start,
        end_minute=end,
    )
    row = Emission(
        scope=SCOPE_WEEKLY,
        title=title,
        artist=artist,
        day_of_week=day,
        start_minute=start,
        end_minute=end,
    )
    session.add(row)
    await session.flush()
    return row


async def update_weekly(session: ormSession, row: Emission, data: dict) -> Emission:
    if row.scope != SCOPE_WEEKLY:
        raise DomainValidationError(
            "Cette emission n'est pas un creneau hebdo.",
            field="scope",
        )
    require_expected_version(row.version, data.get("version"))
    title, artist = validate_title_artist(
        data.get("title", row.title),
        data.get("artist", row.artist),
    )
    day, start, end = validate_weekly_window(
        data.get("day_of_week", row.day_of_week),
        data.get("start_minute", row.start_minute),
        data.get("end_minute", row.end_minute),
    )
    existing = [
        (item.id, int(item.day_of_week), int(item.start_minute), int(item.end_minute))
        for item in await Emission.all_weekly(session)
        if item.day_of_week is not None
        and item.start_minute is not None
        and item.end_minute is not None
    ]
    reject_weekly_overlap(
        existing,
        day_of_week=day,
        start_minute=start,
        end_minute=end,
        ignore_id=row.id,
    )
    row.title = title
    row.artist = artist
    row.day_of_week = day
    row.start_minute = start
    row.end_minute = end
    await session.flush()
    return row


async def delete_weekly(session: ormSession, emission_id: int) -> None:
    row = await Emission.from_id(session, emission_id)
    if row is None or row.scope != SCOPE_WEEKLY:
        raise DomainNotFound("Émission introuvable.", field="id")
    await session.delete(row)


async def create_session(session: ormSession, data: dict, *, harbor: bool) -> Emission:
    if not harbor:
        raise DomainValidationError(
            "Connecte le harbor avant de nommer ce live.",
            field="scope",
        )
    title, artist = validate_title_artist(data.get("title"), data.get("artist"))
    for row in await Emission.all_sessions(session):
        await session.delete(row)
    ends_at = None
    raw_end = data.get("ends_at")
    if raw_end:
        try:
            ends_at = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
        except ValueError as exc:
            raise DomainValidationError("Fin invalide.", field="ends_at") from exc
    row = Emission(
        scope=SCOPE_SESSION,
        title=title,
        artist=artist,
        starts_at=datetime.now(timezone.utc),
        ends_at=ends_at,
    )
    session.add(row)
    await session.flush()
    return row


async def delete_session(session: ormSession) -> None:
    for row in await Emission.all_sessions(session):
        await session.delete(row)

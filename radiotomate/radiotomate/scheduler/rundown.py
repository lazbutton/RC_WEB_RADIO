"""
On-the-fly clock rundown (phase 3): list ≥ 30 min without pushing Liquidsoap.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from radiotomate.domain.execution import item_status_code
from radiotomate.enums import PositionKind
from radiotomate.models import AutoDJSlot, Clock
from radiotomate.scheduler.clock import (
    _load_cart,
    _next_available_sound,
    _pick_music,
    load_sequencer_cursor,
    next_hard_anchor,
    next_soft_anchor,
    now_paris,
    track_would_overflow_anchor,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession

    from radiotomate.beets import BeetsIntegration
    from radiotomate.models.clock import ClockPosition
    from radiotomate.models.sound import Sound

DEFAULT_MUSIC_SEC = 180.0
DEFAULT_CART_SEC = 8.0
MAX_ITEMS = 800
MIN_HORIZON_MIN = 15
DEFAULT_HORIZON_MIN = 30
MAX_HORIZON_MIN = 18 * 60
STATUS_PLANNED = "prévu"
STATUS_RESCUE = "secours"


def rest_of_day_minutes(now: datetime | None = None) -> int:
    """Minutes until midnight, at least 4 h so late evening still has lookahead."""
    now = now_paris(now)
    midnight = (now + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    until_midnight = max(1, int((midnight - now).total_seconds() // 60))
    return min(MAX_HORIZON_MIN, max(4 * 60, until_midnight))


def _hhmm(minute: int) -> str:
    if minute >= 1440:
        return "24:00"
    return f"{minute // 60:02d}:{minute % 60:02d}"


def daypart_label(slot: AutoDJSlot, slot_end: int) -> str:
    return f"{slot.display_time}–{_hhmm(slot_end)}"  # noqa: RUF001


def _duration_seconds(value: object, default: float) -> float:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    return n if n > 0 else default


def _resource_music(item: object) -> str:
    artist = str(getattr(item, "artist", "") or "").strip()
    title = str(getattr(item, "title", "") or "").strip()
    if artist and title:
        return f"{artist} — {title}"
    return title or artist or "musique"


def _media_path(value: object) -> str | None:
    raw = value
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    text = str(raw).strip()
    return text or None


def _queue_for(kind: str, when: str) -> str:
    if when == "anchored":
        return "carts"
    if kind == PositionKind.JINGLE.value:
        return "jingles"
    return "autodj"


def _item_payload(  # noqa: PLR0913
    *,
    at: datetime,
    kind: str,
    when: str,
    resource: str,
    queue: str,
    status: str,
    clock_name: str,
    daypart: str,
    duration: float,
    minute: int | None = None,
    sync: str | None = None,
    category: str | None = None,
    cart: str | None = None,
    path: str | None = None,
    clock_id: int | None = None,
    position_id: int | None = None,
    cart_id: int | None = None,
    sound_id: int | None = None,
    beets_id: int | None = None,
    fallback_used: bool = False,
) -> dict:
    payload = {
        "at": at.isoformat(),
        "kind": kind,
        "when": when,
        "resource": resource,
        "queue": queue,
        "status": status,
        "clock": clock_name,
        "daypart": daypart,
        "duration": duration,
        "clock_id": clock_id,
        "position_id": position_id,
        "cart_id": cart_id,
        "sound_id": sound_id,
        "beets_id": beets_id,
        "fallback_used": fallback_used,
    }
    if when == "anchored":
        payload["minute"] = minute
        payload["sync"] = sync
    if category:
        payload["category"] = category
    if cart:
        payload["cart"] = cart
    if path:
        payload["path"] = path
    payload["status_code"] = item_status_code(payload)
    return payload


async def _resolve_cart(
    session: ormSession,
    pos: ClockPosition,
    clock: Clock,
) -> tuple[Sound | None, object | None, bool]:
    references = [
        (pos.cart_id, pos.cart_title),
        (pos.fallback_cart_id, pos.fallback_cart_title),
        (clock.fallback_cart_id, clock.fallback_cart_title),
    ]
    primary_id, primary_title = references[0]
    for cart_id, title in references:
        if cart_id is None and not title:
            continue
        cart = await _load_cart(session, title, cart_id=cart_id)
        sound = _next_available_sound(cart)
        if sound and cart:
            rescue = (
                (primary_id is not None and cart.id != primary_id)
                or (primary_id is None and title != primary_title)
            )
            return sound, cart, rescue
    return None, None, False


async def _header(
    session: ormSession,
    now: datetime,
) -> tuple[str | None, str | None]:
    minute_of_day = now.hour * 60 + now.minute
    slot = await AutoDJSlot.from_time(session, minute_of_day, now.weekday())
    if slot is None or not slot.clock_id:
        return None, None
    clock = await Clock.from_id(session, slot.clock_id)
    nxt = await AutoDJSlot.next_after(session, slot)
    end = slot.daypart_end_minute(nxt)
    clock_name = clock.name if clock else None
    return clock_name, daypart_label(slot, end)


async def _prepare_cart(  # noqa: PLR0913
    session: ormSession,
    pos: ClockPosition,
    clock: Clock,
    *,
    at: datetime,
    when: str,
    daypart: str,
) -> tuple[dict, float] | None:
    sound, cart, rescue = await _resolve_cart(session, pos, clock)
    if sound is None or cart is None:
        return None
    resource = (sound.title or "").strip() or cart.title
    duration = _duration_seconds(sound.duration, DEFAULT_CART_SEC)
    payload = _item_payload(
        at=at,
        kind=pos.kind,
        when=when,
        resource=resource,
        queue=_queue_for(pos.kind, when),
        status=STATUS_RESCUE if rescue else STATUS_PLANNED,
        clock_name=clock.name,
        daypart=daypart,
        duration=duration,
        minute=pos.minute if when == "anchored" else None,
        sync=pos.sync if when == "anchored" else None,
        cart=cart.title,
        path=_media_path(getattr(sound, "path", None)),
        clock_id=clock.id,
        position_id=pos.id,
        cart_id=cart.id,
        sound_id=sound.id,
        fallback_used=rescue,
    )
    return payload, duration


async def _prepare_sequential(  # noqa: PLR0913
    session: ormSession,
    beets: BeetsIntegration,
    pos: ClockPosition,
    clock: Clock,
    *,
    at: datetime,
    daypart: str,
    picked_artists: set[str],
    picked_titles: set[str],
) -> tuple[dict, float, str | None, str | None] | None:
    """Return (payload, duration, artist, title) for a sequential motif step."""
    if pos.kind == PositionKind.MUSIQUE.value:
        item = await _pick_music(
            session,
            beets,
            pos,
            at,
            extra_artists=picked_artists,
            extra_titles=picked_titles,
        )
        if item is None:
            return None
        length = _duration_seconds(getattr(item, "length", 0), DEFAULT_MUSIC_SEC)
        category_name = pos.category.name if pos.category is not None else None
        payload = _item_payload(
            at=at,
            kind=pos.kind,
            when="sequential",
            resource=_resource_music(item),
            queue=_queue_for(pos.kind, "sequential"),
            status=STATUS_PLANNED,
            clock_name=clock.name,
            daypart=daypart,
            duration=length,
            category=category_name,
            path=_media_path(getattr(item, "path", None)),
            clock_id=clock.id,
            position_id=pos.id,
            beets_id=getattr(item, "id", None),
        )
        artist = str(getattr(item, "artist", "") or "") or None
        title = str(getattr(item, "title", "") or "") or None
        return payload, length, artist, title
    if pos.kind in {
        PositionKind.JINGLE.value,
        PositionKind.SON.value,
        PositionKind.PUB.value,
    }:
        prepared = await _prepare_cart(
            session,
            pos,
            clock,
            at=at,
            when="sequential",
            daypart=daypart,
        )
        if prepared is None:
            return None
        payload, duration = prepared
        return payload, duration, None, None
    return None


async def build_rundown(  # noqa: PLR0912, PLR0915
    session: ormSession,
    beets: BeetsIntegration,
    now: datetime | None = None,
    horizon_min: int = 30,
    cursor: int | None = None,
) -> dict:
    """
    Forecast the clock from ``now`` for at least ``horizon_min`` minutes.

    Does not push queues and does not mutate the live sequencer state.
    """
    now = now_paris(now)
    deadline = now + timedelta(minutes=horizon_min)
    hard_cap = now + timedelta(minutes=max(horizon_min * 2, 60))
    header_clock, header_daypart = await _header(session, now)
    saved_cursor, saved_clock_id = await load_sequencer_cursor(session)

    items: list[dict] = []
    t = now
    sim_cursor = 0
    sim_clock_id: int | None = None
    picked_artists: set[str] = set()
    picked_titles: set[str] = set()
    failures = 0

    while t < hard_cap and len(items) < MAX_ITEMS:
        last_at = datetime.fromisoformat(items[-1]["at"]) if items else None
        if last_at is not None and last_at >= deadline:
            break

        minute_of_day = t.hour * 60 + t.minute
        slot = await AutoDJSlot.from_time(session, minute_of_day, t.weekday())
        if slot is None or not slot.clock_id:
            t += timedelta(minutes=1)
            continue

        clock = await Clock.from_id(session, slot.clock_id)
        if clock is None:
            t += timedelta(minutes=1)
            continue

        if sim_clock_id is None:
            if cursor is not None:
                sim_cursor = max(0, cursor)
            elif saved_clock_id == clock.id:
                sim_cursor = saved_cursor
            else:
                sim_cursor = 0
            sim_clock_id = clock.id
        elif clock.id != sim_clock_id:
            sim_cursor = 0
            sim_clock_id = clock.id

        nxt = await AutoDJSlot.next_after(session, slot)
        slot_end = slot.daypart_end_minute(nxt)
        label = daypart_label(slot, slot_end)
        sequential = clock.sequential_positions()
        anchored = clock.anchored_positions()
        upcoming = next_hard_anchor(t, slot.minute, slot_end, anchored)
        upcoming_soft = next_soft_anchor(t, slot.minute, slot_end, anchored)

        prepared = None
        if sequential:
            pos = sequential[sim_cursor % len(sequential)]
            prepared = await _prepare_sequential(
                session,
                beets,
                pos,
                clock,
                at=t,
                daypart=label,
                picked_artists=picked_artists,
                picked_titles=picked_titles,
            )

        if not sequential and upcoming is not None:
            prepared = None
            overflow_anchor = True
        elif upcoming is not None and prepared is not None:
            overflow_anchor = track_would_overflow_anchor(
                t,
                0.0,
                prepared[1],
                upcoming[0].hour * 60 + upcoming[0].minute,
            )
        else:
            overflow_anchor = False

        if overflow_anchor and upcoming is not None:
            if (
                upcoming_soft is not None
                and upcoming_soft[0] < upcoming[0]
            ):
                skipped = await _prepare_cart(
                    session,
                    upcoming_soft[1],
                    clock,
                    at=upcoming_soft[0],
                    when="anchored",
                    daypart=label,
                )
                if skipped is not None:
                    payload, _duration = skipped
                    payload["status"] = "sauté"
                    payload["status_code"] = item_status_code(payload)
                    payload["reason"] = "dépassé par une ancre dure"
                    items.append(payload)
            anchor_dt, anchor_pos = upcoming
            anchored_item = await _prepare_cart(
                session,
                anchor_pos,
                clock,
                at=anchor_dt,
                when="anchored",
                daypart=label,
            )
            if anchored_item is None:
                t = max(t, anchor_dt + timedelta(seconds=1))
                failures += 1
                if failures > 20:
                    break
                continue
            payload, duration = anchored_item
            items.append(payload)
            t = anchor_dt + timedelta(seconds=duration)
            failures = 0
            continue

        if prepared is None:
            sim_cursor += 1
            failures += 1
            if not sequential or failures >= len(sequential) * 2:
                t += timedelta(seconds=1)
                failures = 0
            continue

        payload, duration, artist, title = prepared
        items.append(payload)
        if artist:
            picked_artists.add(artist)
        if title:
            picked_titles.add(title)
        t += timedelta(seconds=duration)
        sim_cursor += 1
        failures = 0
        if upcoming_soft is not None and t >= upcoming_soft[0]:
            glided = await _prepare_cart(
                session,
                upcoming_soft[1],
                clock,
                at=t,
                when="anchored",
                daypart=label,
            )
            if glided is not None:
                soft_payload, soft_duration = glided
                soft_payload["reason"] = "glissement"
                items.append(soft_payload)
                t += timedelta(seconds=soft_duration)

    return {
        "now": now.isoformat(),
        "horizon_min": horizon_min,
        "clock": header_clock,
        "daypart": header_daypart,
        "items": items,
    }

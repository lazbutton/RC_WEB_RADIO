"""
Clock sequencer: motif + hard anchors → existing Liquidsoap queues.

Jingles cut the program (current graph). Sequential jingles are only pushed when
the current track is nearly over, so the motif intercalates without eating a
title mid-way. Hard anchors still cut.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from random import choice
from time import perf_counter
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import select

from radiotomate.enums import CartMode, PlayoutAction, PositionKind, RundownStatus, WhenMode
from radiotomate.models import (
    AutoDJSlot,
    Cart,
    Clock,
    ClockPosition,
    MetadataLog,
    RundownItem,
    Setting,
    Sound,
)
from radiotomate.scheduler.metrics import runtime_metrics
from radiotomate.scheduler.outbox import dispatch_command, enqueue_command
from radiotomate.scheduler.playout import PlayoutGateway, gateway_for

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.orm import Session as ormSession

    from radiotomate.beets import BeetsIntegration

_log = logging.getLogger(__name__)


def _finish_tick(started: float, actions: list[str]) -> list[str]:
    runtime_metrics.tick_duration_seconds = perf_counter() - started
    return actions


PARIS = ZoneInfo("Europe/Paris")
ARTIST_WINDOW = timedelta(minutes=60)
ARTIST_LAST_N = 3
TITLE_LAST_N = 20
JINGLE_PREEMPT_REMAINING = 3.0
SETTING_CLOCK_SEQ_CURSOR = "clock_seq_cursor"
SETTING_CLOCK_SEQ_CLOCK_ID = "clock_seq_clock_id"
SETTING_CLOCK_SEQ_EPOCH = "clock_seq_epoch"
TARGET_QUEUE_DEPTH = 2
# Une seule avance dans la file jingles : fallback Liquidsoap les joue
# tant qu’il en reste, donc un stock de 2 bouche l’Auto-DJ.
JINGLE_QUEUE_DEPTH = 1
AUTODJ_BURST_REMAINING = 8.0
AUTODJ_BURST_DEPTH = 3
_PUSHABLE_STATUSES = {
    RundownStatus.PLANNED.value,
    RundownStatus.RESCUE.value,
}


@dataclass
class SequencerState:
    cursor: int = 0
    clock_id: int | None = None
    fired_anchors: set[tuple[str, int, int]] = field(default_factory=set)
    restored: bool = False
    persisted_snapshot: tuple[int, int | None, frozenset] | None = None
    active_cart_id: int | None = None
    epoch: int = 0


_state = SequencerState()


def reset_state() -> None:
    """Reset in-memory cursor (tests and producer reset)."""
    _state.cursor = 0
    _state.clock_id = None
    _state.fired_anchors.clear()
    _state.restored = False
    _state.persisted_snapshot = None
    _state.active_cart_id = None
    _state.epoch = 0


def note_carts_push(cart_id: int) -> None:
    """Remember which cart currently owns the carts Liquidsoap queue."""
    _state.active_cart_id = cart_id


def now_paris(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(PARIS)
    if now.tzinfo is None:
        return now.replace(tzinfo=PARIS)
    return now.astimezone(PARIS)


def now_from_live(live_data: dict, now: datetime | None = None) -> datetime:
    if now is not None:
        return now_paris(now)
    raw = live_data.get("time")
    if isinstance(raw, str) and raw:
        try:
            return now_paris(datetime.fromisoformat(raw))
        except ValueError:
            pass
    return now_paris()


def queue_empty(live_data: dict, key: str) -> bool:
    nxt = live_data.get(key)
    if not isinstance(nxt, dict):
        return False
    return nxt.get("rid") == -1


def queued_count(live_data: dict, queued_key: str, cue_key: str) -> int:
    raw = live_data.get(queued_key)
    if raw is not None and raw != "":
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    return 0 if queue_empty(live_data, cue_key) else 1


def remaining_seconds(live_data: dict) -> float:
    try:
        return float(live_data.get("remaining") or 0)
    except (TypeError, ValueError):
        return 0.0


def source_id(live_data: dict) -> str:
    raw = str(live_data.get("source") or "")
    if raw in {"jingles", "carts", "stream", "relay"}:
        return raw
    # Auto-DJ, files d’attente internes LS (`insert_initial*`,
    # `replay_metadata`, `programs`, …) : ne pas relancer les jingles.
    return "autodj"


def anchor_in_daypart(slot_start: int, slot_end: int, hour: int, minute: int) -> bool:
    """Civil HH:mm is in [slot_start, slot_end)."""
    t = hour * 60 + minute
    return slot_start <= t < slot_end


def next_hard_anchor_minute(
    now: datetime,
    slot_start: int,
    slot_end: int,
    anchored: list[ClockPosition],
) -> int | None:
    """Soonest future hard-anchor minute-of-day still in this daypart, or None."""
    found = next_hard_anchor(now, slot_start, slot_end, anchored)
    if found is None:
        return None
    dt, _pos = found
    return dt.hour * 60 + dt.minute


def next_hard_anchor(
    now: datetime,
    slot_start: int,
    slot_end: int,
    anchored: list[ClockPosition],
) -> tuple[datetime, ClockPosition] | None:
    """Soonest future hard anchor still in this daypart, or None."""
    return _next_anchor(now, slot_start, slot_end, anchored, hard=True)


def next_soft_anchor(
    now: datetime,
    slot_start: int,
    slot_end: int,
    anchored: list[ClockPosition],
) -> tuple[datetime, ClockPosition] | None:
    """Soonest future soft anchor still in this daypart, or None."""
    return _next_anchor(now, slot_start, slot_end, anchored, hard=False)


def _next_anchor(
    now: datetime,
    slot_start: int,
    slot_end: int,
    anchored: list[ClockPosition],
    *,
    hard: bool,
) -> tuple[datetime, ClockPosition] | None:
    soonest: datetime | None = None
    soonest_pos: ClockPosition | None = None
    for pos in anchored:
        if pos.minute is None:
            continue
        if hard and not pos.is_hard_sync:
            continue
        if not hard and not pos.is_soft_sync:
            continue
        for hour in range(24):
            mod = hour * 60 + pos.minute
            if not (slot_start <= mod < slot_end):
                continue
            candidate = now.replace(
                hour=hour,
                minute=pos.minute,
                second=0,
                microsecond=0,
            )
            if candidate <= now:
                continue
            if soonest is None or candidate < soonest:
                soonest = candidate
                soonest_pos = pos
    if soonest is None or soonest_pos is None:
        return None
    return soonest, soonest_pos


def _session_engine(session: ormSession):
    bind = session.get_bind()
    return getattr(bind, "engine", bind)


async def load_sequencer_epoch(session: ormSession) -> int:
    row = await Setting.from_key(session, SETTING_CLOCK_SEQ_EPOCH)
    if row is None or row.value in {"", None}:
        return 0
    try:
        return max(0, int(row.value))
    except (TypeError, ValueError):
        return 0


async def peek_committed_epoch(session: ormSession) -> int:
    """Read epoch from a fresh connection so a producer reset wins over this tick."""
    from sqlalchemy.ext.asyncio import AsyncSession

    engine = _session_engine(session)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as probe:
            return await load_sequencer_epoch(probe)
    except Exception:
        return await load_sequencer_epoch(session)


async def persist_sequencer_cursor(
    session: ormSession,
    *,
    commit: bool = True,
) -> None:
    """Write in-memory cursor so the interface can preview without the scheduler."""
    from radiotomate.scheduler.execution import persist_fired_anchors

    committed_epoch = await peek_committed_epoch(session)
    if _state.restored and committed_epoch != _state.epoch:
        _state.restored = False
        _state.epoch = committed_epoch
        return
    snapshot = (_state.cursor, _state.clock_id, frozenset(_state.fired_anchors))
    if snapshot == _state.persisted_snapshot:
        return
    await Setting.upsert(session, SETTING_CLOCK_SEQ_CURSOR, str(_state.cursor))
    clock_id = "" if _state.clock_id is None else str(_state.clock_id)
    await Setting.upsert(session, SETTING_CLOCK_SEQ_CLOCK_ID, clock_id)
    await persist_fired_anchors(session, _state.fired_anchors)
    if commit:
        await session.commit()
    _state.persisted_snapshot = snapshot


async def restore_sequencer_state(
    session: ormSession,
    *,
    force: bool = False,
) -> None:
    """Reload cursor and fired anchors after a restart or producer reset."""
    from radiotomate.scheduler.execution import load_fired_anchors

    epoch = await load_sequencer_epoch(session)
    if _state.restored and not force and _state.epoch == epoch:
        return
    _state.fired_anchors = await load_fired_anchors(session)
    cursor, clock_id = await load_sequencer_cursor(session)
    _state.cursor = cursor
    _state.clock_id = clock_id
    _state.epoch = epoch
    _state.persisted_snapshot = (
        _state.cursor,
        _state.clock_id,
        frozenset(_state.fired_anchors),
    )
    _state.restored = True


async def reset_sequencer(session: ormSession, *, commit: bool = True) -> None:
    """Start the motif at position 0 and bump epoch so the scheduler process follows."""
    from radiotomate.scheduler.execution import persist_fired_anchors

    epoch = await load_sequencer_epoch(session) + 1
    reset_state()
    _state.epoch = epoch
    _state.restored = True
    _state.persisted_snapshot = (0, None, frozenset())
    await Setting.upsert(session, SETTING_CLOCK_SEQ_EPOCH, str(epoch))
    await Setting.upsert(session, SETTING_CLOCK_SEQ_CURSOR, "0")
    await Setting.upsert(session, SETTING_CLOCK_SEQ_CLOCK_ID, "")
    await persist_fired_anchors(session, set())
    if commit:
        await session.commit()


async def advance_sequencer_cursor(session: ormSession) -> None:
    """Move the motif cursor one step and persist it (demo skip / playhead)."""
    now = now_paris()
    minute_of_day = now.hour * 60 + now.minute
    slot = await AutoDJSlot.from_time(session, minute_of_day, now.weekday())
    clock_id = slot.clock_id if slot is not None else None
    saved_cursor, saved_clock_id = await load_sequencer_cursor(session)
    if clock_id is not None and saved_clock_id == clock_id:
        _state.cursor = saved_cursor + 1
    else:
        _state.cursor = 1
    _state.clock_id = clock_id
    await persist_sequencer_cursor(session)


async def load_sequencer_cursor(session: ormSession) -> tuple[int, int | None]:
    """Return (cursor, clock_id) stored by the last scheduler tick, or (0, None)."""
    cursor_row = await Setting.from_key(session, SETTING_CLOCK_SEQ_CURSOR)
    clock_row = await Setting.from_key(session, SETTING_CLOCK_SEQ_CLOCK_ID)
    cursor = 0
    if cursor_row is not None:
        try:
            cursor = max(0, int(cursor_row.value))
        except (TypeError, ValueError):
            cursor = 0
    clock_id = None
    if clock_row is not None and clock_row.value not in {"", None}:
        try:
            clock_id = int(clock_row.value)
        except (TypeError, ValueError):
            clock_id = None
    return cursor, clock_id


def track_would_overflow_anchor(
    now: datetime,
    remaining: float,
    track_length: float,
    anchor_minute_of_day: int,
) -> bool:
    hour, minute = divmod(anchor_minute_of_day, 60)
    anchor_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if anchor_dt <= now:
        return False
    seconds_until = (anchor_dt - now).total_seconds()
    start_delay = max(0.0, remaining)
    return start_delay + track_length > seconds_until


def can_push_sequential_jingle(live_data: dict) -> bool:
    """Avoid cutting a title mid-way; hard anchors still cut separately.

    Ne jamais enchaîner un jingle pendant qu’un jingle/cart est à l’antenne :
    le fallback Liquidsoap resterait collé sur cette file. Un jingle séquentiel
    ne coupe pas non plus les titres Auto-DJ déjà en file : il attend que la
    file autodj soit vide (fin du titre en cours).
    """
    src = source_id(live_data)
    remaining = remaining_seconds(live_data)
    if src in {"jingles", "carts", "stream", "relay"}:
        return False
    if queued_count(live_data, "autodj_queued", "next_autodj") > 0:
        return False
    if remaining > JINGLE_PREEMPT_REMAINING:
        return False
    return True


def sequential_kind_cycle(clock: Clock) -> list[str]:
    return [p.kind for p in clock.sequential_positions()]


async def tick(  # noqa: PLR0912, PLR0915
    session: ormSession,
    live_data: dict,
    playout_client: AsyncClient,
    beets: BeetsIntegration,
    now: datetime | None = None,
) -> list[str]:
    """
    One scheduler heartbeat. Returns action labels for tests.
    """
    now = now_from_live(live_data, now)
    actions: list[str] = []
    started = perf_counter()
    await restore_sequencer_state(session)
    from radiotomate.scheduler.outbox import dispatch_pending_commands

    gateway = gateway_for(playout_client)
    await dispatch_pending_commands(session, gateway)
    await _refill_active_cart(session, live_data, gateway, actions)
    minute_of_day = now.hour * 60 + now.minute
    slot = await AutoDJSlot.from_time(session, minute_of_day, now.weekday())
    if slot is None or not slot.clock_id:
        _log.warning(
            "No clock on Auto-DJ slot at minute %s weekday %s; not filling queues",
            minute_of_day,
            now.weekday(),
        )
        await persist_sequencer_cursor(session)
        return _finish_tick(started, actions)

    clock = await Clock.from_id(session, slot.clock_id)
    if clock is None:
        _log.warning("Clock %s missing; not filling queues", slot.clock_id)
        await persist_sequencer_cursor(session)
        return _finish_tick(started, actions)

    from radiotomate.scheduler.execution import ensure_forecast

    clock_id = clock.id
    weekday = now.weekday()
    await ensure_forecast(session, beets, now=now)
    slot = await AutoDJSlot.from_time(session, minute_of_day, weekday)
    clock = await Clock.from_id(session, clock_id)
    if slot is None or clock is None:
        await persist_sequencer_cursor(session)
        return _finish_tick(started, actions)

    if _state.clock_id != clock.id:
        _state.clock_id = clock.id
        _state.cursor = 0

    today = now.date().isoformat()
    _state.fired_anchors = {k for k in _state.fired_anchors if k[0] == today}

    nxt = await AutoDJSlot.next_after(session, slot)
    slot_end = slot.daypart_end_minute(nxt)

    await _fire_due_anchors(
        session,
        live_data,
        gateway,
        clock,
        slot.minute,
        slot_end,
        now,
        actions,
    )
    await _fire_due_soft_anchors(
        session,
        live_data,
        gateway,
        clock,
        slot.minute,
        slot_end,
        now,
        actions,
    )

    sequential = clock.sequential_positions()
    if not sequential:
        await persist_sequencer_cursor(session)
        return _finish_tick(started, actions)

    anchored = clock.anchored_positions()
    next_anchor = next_hard_anchor_minute(now, slot.minute, slot_end, anchored)
    jingles_q = queued_count(live_data, "jingles_queued", "next_jingle")
    autodj_q = queued_count(live_data, "autodj_queued", "next_autodj")
    autodj_target = (
        AUTODJ_BURST_DEPTH
        if remaining_seconds(live_data) < AUTODJ_BURST_REMAINING
        else TARGET_QUEUE_DEPTH
    )
    live_data, jingles_q, autodj_q = await _fill_sequential_from_rundown(
        session,
        gateway,
        live_data,
        now,
        actions,
        next_anchor=next_anchor,
        jingles_q=jingles_q,
        autodj_q=autodj_q,
        autodj_target=autodj_target,
    )
    await persist_sequencer_cursor(session)
    return _finish_tick(started, actions)


def _meta_from_item(item: RundownItem) -> tuple[str, str]:
    details = dict(item.details or {})
    artist = str(details.get("artist") or details.get("cart") or "").strip()
    title = str(details.get("title") or "").strip()
    raw = (item.resource or "").strip()
    if (not artist or not title) and " — " in raw:
        left, right = raw.split(" — ", 1)
        artist = artist or left.strip()
        title = title or right.strip()
    if not title:
        title = raw or artist or "titre"
    return artist, title


def _is_jingle_item(item: RundownItem) -> bool:
    return item.queue == "jingles" or item.kind == PositionKind.JINGLE.value


async def _load_pushable_sequential(session: ormSession) -> list[RundownItem]:
    return list(
        await session.scalars(
            select(RundownItem)
            .where(
                RundownItem.status.in_(list(_PUSHABLE_STATUSES)),
                RundownItem.origin != "desk",
                RundownItem.when_mode != WhenMode.ANCHORED.value,
            )
            .order_by(RundownItem.planned_at, RundownItem.sequence)
        )
    )


async def _planned_anchor_item(
    session: ormSession,
    pos: ClockPosition,
    now: datetime,
) -> RundownItem | None:
    rows = list(
        await session.scalars(
            select(RundownItem)
            .where(
                RundownItem.status.in_(list(_PUSHABLE_STATUSES)),
                RundownItem.when_mode == WhenMode.ANCHORED.value,
                RundownItem.origin != "desk",
            )
            .order_by(RundownItem.planned_at, RundownItem.sequence)
        )
    )
    matched = [
        row
        for row in rows
        if pos.id is not None and row.clock_position_id == pos.id
    ]
    pool = matched or [
        row
        for row in rows
        if row.kind == pos.kind
        and (
            (row.planned_at is not None and row.planned_at.minute == pos.minute)
            or (row.details or {}).get("minute") == pos.minute
        )
    ]
    for row in pool:
        planned = row.planned_at
        if (
            planned is not None
            and planned.hour == now.hour
            and planned.minute == pos.minute
        ):
            return row
    return None


async def _push_existing_rundown_item(
    session: ormSession,
    gateway: PlayoutGateway,
    item: RundownItem,
    *,
    queue: str | None = None,
) -> bool:
    target = queue or item.queue or "autodj"
    path = (item.path or "").strip()
    if not path:
        return False
    artist, title = _meta_from_item(item)
    payload: dict = {"path": path, "radiotomate_item_id": item.id}
    if artist:
        payload["artist"] = artist
    if title:
        payload["title"] = title
    if item.beets_id:
        payload["beets_id"] = item.beets_id
    if item.sound_id:
        payload["radiotomate_sound_id"] = item.sound_id
        sound = await Sound.from_id(session, item.sound_id)
        if sound is not None and sound.gain is not None:
            payload["rg_track_gain"] = str(sound.gain)
    details = dict(item.details or {})
    if details.get("rg_track_gain") is not None:
        payload.setdefault("rg_track_gain", details["rg_track_gain"])
    command = await enqueue_command(
        session,
        action=PlayoutAction.QUEUE.value,
        queue=target,
        payload=payload,
        item=item,
    )
    payload["radiotomate_command_id"] = command.id
    command.payload = payload
    result = await dispatch_command(session, command, gateway)
    if not result.ok:
        _log.error(
            "Clock push of rundown %s to %s failed: %s",
            item.id,
            target,
            result.error,
        )
        return False
    if item.sound_id:
        sound = await Sound.from_id(session, item.sound_id)
        if sound is not None:
            sound.last_played = datetime.now()
    if target == "carts" and item.cart_id:
        note_carts_push(item.cart_id)
    _log.info("Clock pushed rundown %s %s to %s", item.kind, path, target)
    return True


async def _fill_sequential_from_rundown(  # noqa: PLR0913
    session: ormSession,
    gateway: PlayoutGateway,
    live_data: dict,
    now: datetime,
    actions: list[str],
    *,
    next_anchor: int | None,
    jingles_q: int,
    autodj_q: int,
    autodj_target: int,
) -> tuple[dict, int, int]:
    hold = False
    for _ in range(8):
        if hold:
            break
        pending = [
            item
            for item in await _load_pushable_sequential(session)
            if (item.path or "").strip()
        ]
        if not pending:
            break
        made_progress = False
        for item in pending:
            if item.status not in _PUSHABLE_STATUSES:
                continue
            if _is_jingle_item(item):
                if jingles_q >= JINGLE_QUEUE_DEPTH:
                    continue
                if autodj_q > 0 or not can_push_sequential_jingle(live_data):
                    continue
                if not await _push_existing_rundown_item(
                    session, gateway, item, queue="jingles"
                ):
                    continue
                actions.append("jingle")
                _state.cursor += 1
                jingles_q += 1
                live_data = {
                    **live_data,
                    "next_jingle": {"rid": 1},
                    "jingles_queued": jingles_q,
                }
                made_progress = True
                break
            if autodj_q >= autodj_target:
                break
            length = float(item.duration or 0)
            if next_anchor is not None and track_would_overflow_anchor(
                now,
                remaining_seconds(live_data),
                length,
                next_anchor,
            ):
                _log.info(
                    "Holding autodj track so hard anchor at minute %s can fire",
                    next_anchor,
                )
                hold = True
                break
            queue = item.queue if item.queue in {"autodj", "carts"} else "autodj"
            if not await _push_existing_rundown_item(
                session, gateway, item, queue=queue
            ):
                continue
            actions.append(
                "autodj" if item.kind == PositionKind.MUSIQUE.value else "autodj_cart"
            )
            _state.cursor += 1
            autodj_q += 1
            live_data = {
                **live_data,
                "next_autodj": {"rid": 1},
                "autodj_queued": autodj_q,
            }
            made_progress = True
            break
        if not made_progress:
            break
    return live_data, jingles_q, autodj_q


async def _enqueue_cart_sound(
    session: ormSession,
    gateway: PlayoutGateway,
    cart: Cart,
    sound: Sound,
) -> bool:
    queue = cart.playout_queue()
    result = await gateway.queue(
        queue,
        {
            "path": str(sound.path),
            "artist": cart.title,
            "title": sound.title,
            "radiotomate_sound_id": sound.id,
            "rg_track_gain": str(sound.gain),
        },
    )
    if not result.ok:
        _log.error("Cart chain push to %s failed: %s", queue, result.error)
        return False
    sound.last_played = datetime.now()
    if queue == "carts":
        note_carts_push(cart.id)
    return True


async def _refill_active_cart(
    session: ormSession,
    live_data: dict,
    gateway: PlayoutGateway,
    actions: list[str],
) -> None:
    src = source_id(live_data)
    carts_q = queued_count(live_data, "carts_queued", "next_cart")
    if src not in {"carts", "starting", ""} and carts_q <= 0:
        _state.active_cart_id = None
        return
    if _state.active_cart_id is None or carts_q >= TARGET_QUEUE_DEPTH:
        return
    cart = await Cart.from_id(session, _state.active_cart_id, load_sounds=True)
    if cart is None or cart.mode not in {CartMode.PLAYLIST, CartMode.PLAYLIST_LOOP}:
        return
    pushed_any = False
    while carts_q < TARGET_QUEUE_DEPTH:
        sound = _next_available_sound(cart)
        if sound is None:
            break
        if not await _enqueue_cart_sound(session, gateway, cart, sound):
            break
        carts_q += 1
        pushed_any = True
        actions.append("cart_chain")
    if pushed_any:
        await session.commit()


async def _fire_due_anchors(  # noqa: PLR0913
    session: ormSession,
    live_data: dict,
    gateway: PlayoutGateway,
    clock: Clock,
    slot_start: int,
    slot_end: int,
    now: datetime,
    actions: list[str],
) -> None:
    for pos in clock.anchored_positions():
        if pos.minute is None or not pos.is_hard_sync:
            continue
        if not anchor_in_daypart(slot_start, slot_end, now.hour, pos.minute):
            continue
        if now.minute != pos.minute:
            continue
        key = (now.date().isoformat(), now.hour, pos.minute)
        if key in _state.fired_anchors:
            continue
        planned = await _planned_anchor_item(session, pos, now)
        if planned is not None:
            pushed = await _push_existing_rundown_item(
                session,
                gateway,
                planned,
                queue="carts",
            )
        else:
            pushed = await _push_cart_to_queue(
                session,
                gateway,
                pos,
                clock,
                "carts",
            )
        if not pushed:
            continue
        _state.fired_anchors.add(key)
        actions.append(f"anchor:{pos.minute}")
        src = source_id(live_data)
        if src != "carts":
            skip = await gateway.skip()
            if skip.ok:
                actions.append("skip")
            else:
                _log.error("Hard-sync skip failed: %s", skip.error)


async def _fire_due_soft_anchors(  # noqa: PLR0913
    session: ormSession,
    live_data: dict,
    gateway: PlayoutGateway,
    clock: Clock,
    slot_start: int,
    slot_end: int,
    now: datetime,
    actions: list[str],
) -> None:
    next_hard = next_hard_anchor(now, slot_start, slot_end, clock.anchored_positions())
    for pos in clock.anchored_positions():
        if pos.minute is None or not pos.is_soft_sync:
            continue
        if not anchor_in_daypart(slot_start, slot_end, now.hour, pos.minute):
            continue
        occurrence = now.replace(minute=pos.minute, second=0, microsecond=0)
        if now < occurrence:
            continue
        key = (now.date().isoformat(), now.hour, pos.minute)
        if key in _state.fired_anchors:
            continue
        if next_hard is not None and next_hard[0] <= now:
            _state.fired_anchors.add(key)
            actions.append(f"soft-skip:{pos.minute}")
            continue
        remaining = remaining_seconds(live_data)
        src = source_id(live_data)
        if remaining > JINGLE_PREEMPT_REMAINING and src not in {"", "starting"}:
            continue
        planned = await _planned_anchor_item(session, pos, now)
        if planned is not None:
            pushed = await _push_existing_rundown_item(
                session,
                gateway,
                planned,
                queue="carts",
            )
        else:
            pushed = await _push_cart_to_queue(
                session,
                gateway,
                pos,
                clock,
                "carts",
                reason="glissement" if now > occurrence else None,
            )
        if not pushed:
            continue
        _state.fired_anchors.add(key)
        actions.append(f"soft:{pos.minute}")


async def _push_cart_to_queue(  # noqa: PLR0913
    session: ormSession,
    gateway: PlayoutGateway,
    pos: ClockPosition,
    clock: Clock,
    queue: str,
    reason: str | None = None,
) -> bool:
    references = [
        (pos.cart_id, pos.cart_title),
        (pos.fallback_cart_id, pos.fallback_cart_title),
        (clock.fallback_cart_id, clock.fallback_cart_title),
    ]
    sound: Sound | None = None
    cart: Cart | None = None
    for cart_id, title in references:
        if cart_id is None and not title:
            continue
        cart = await _load_cart(session, cart_id=cart_id, title=title)
        sound = _next_available_sound(cart)
        if sound:
            break
    if not sound or not cart:
        _log.warning(
            "Clock position %s: no sound (tried %s)",
            pos.id,
            references,
        )
        return False
    fallback_used = bool(cart.id != pos.cart_id) if pos.cart_id else bool(
        pos.cart_title and cart.title != pos.cart_title
    )
    payload = {
        "path": str(sound.path),
        "artist": cart.title,
        "title": sound.title,
        "radiotomate_sound_id": sound.id,
        "rg_track_gain": str(sound.gain),
    }
    item = await _record_push_item(
        session,
        kind=pos.kind,
        when=pos.when_mode,
        queue=queue,
        resource=(sound.title or "").strip() or cart.title,
        path=str(sound.path),
        clock=clock,
        pos=pos,
        cart_id=cart.id,
        sound_id=sound.id,
        fallback_used=fallback_used,
        reason=reason,
        sync=pos.sync,
    )
    payload["radiotomate_item_id"] = item.id
    command = await enqueue_command(
        session,
        action=PlayoutAction.QUEUE.value,
        queue=queue,
        payload=payload,
        item=item,
    )
    payload["radiotomate_command_id"] = command.id
    command.payload = payload
    result = await dispatch_command(session, command, gateway)
    if result.ok:
        _log.info(
            "Clock pushed %s %s:%s to %s",
            pos.kind,
            sound.id,
            sound.path,
            queue,
        )
        sound.last_played = datetime.now()
        if queue == "carts":
            note_carts_push(cart.id)
        return True
    _log.error("Clock push to %s failed: %s", queue, result.error)
    return False


async def _load_cart(
    session: ormSession,
    title: str | None = None,
    *,
    cart_id: int | None = None,
) -> Cart | None:
    if cart_id is not None:
        return await Cart.from_id(session, cart_id, load_sounds=True)
    if not title:
        return None
    cart = await Cart.from_title(session, title)
    if cart is None:
        return None
    return await Cart.from_id(session, cart.id, load_sounds=True)


def _next_available_sound(cart: Cart | None) -> Sound | None:
    if cart is None:
        return None
    try:
        sound = cart.next_sound()
    except IndexError:
        sound = None
    if sound is not None:
        return sound
    # Playlist one-shot is exhausted: wrap so clock jingl/pub carts keep filling.
    return cart.next_sound_playlist_loop()


async def _pick_music(  # noqa: PLR0913
    session: ormSession,
    beets: BeetsIntegration,
    pos: ClockPosition,
    now: datetime,
    extra_artists: set[str] | None = None,
    extra_titles: set[str] | None = None,
) -> object | None:
    category = pos.category
    queries: list[str] = []
    if category is not None:
        queries.append(category.query)
        if category.empty_query:
            queries.append(category.empty_query)
    queries.append("")
    history = await _music_history(session)
    forbidden_artists, forbidden_titles = _rule_sets(history, now)
    if extra_artists:
        forbidden_artists = forbidden_artists | extra_artists
    if extra_titles:
        forbidden_titles = forbidden_titles | extra_titles
    for query in queries:
        items = await beets.search(query)
        picked = _choose_item(items, forbidden_artists, forbidden_titles)
        if picked is not None:
            return picked
    return None


def _choose_item(items, forbidden_artists: set[str], forbidden_titles: set[str]):
    if not items:
        return None
    filtered = [
        item
        for item in items
        if str(getattr(item, "artist", "") or "") not in forbidden_artists
        and str(getattr(item, "title", "") or "") not in forbidden_titles
    ]
    pool = filtered or list(items)
    return choice(pool)


async def _music_history(session: ormSession) -> list[MetadataLog]:
    q = (
        select(MetadataLog)
        .where(MetadataLog.cart_id.is_(None))
        .order_by(MetadataLog.on_air.desc())
        .limit(max(TITLE_LAST_N, ARTIST_LAST_N) + 5)
    )
    return list(await session.scalars(q))


def _rule_sets(
    logs: list[MetadataLog],
    now: datetime,
) -> tuple[set[str], set[str]]:
    naive = now.replace(tzinfo=None) if now.tzinfo else now
    last_artists = [
        log.artist for log in logs[:ARTIST_LAST_N] if log.artist
    ]
    window_artists = []
    for log in logs:
        if not log.artist or log.on_air is None:
            continue
        on_air = log.on_air
        if on_air.tzinfo:
            on_air = on_air.replace(tzinfo=None)
        if naive - on_air <= ARTIST_WINDOW:
            window_artists.append(log.artist)
    artists = set(last_artists) | set(window_artists)
    titles = {log.title for log in logs[:TITLE_LAST_N] if log.title}
    return artists, titles


async def _push_autodj_item(
    session: ormSession,
    gateway: PlayoutGateway,
    item,
    pos: ClockPosition,
    clock: Clock,
) -> bool:
    next_path = item.path.decode() if isinstance(item.path, bytes) else str(item.path)
    artist = str(getattr(item, "artist", "") or "").strip()
    title = str(getattr(item, "title", "") or "").strip()
    if artist and title:
        resource = f"{artist} — {title}"
    else:
        resource = title or artist or "musique"
    payload = {
        "path": next_path,
        "beets_id": item.id,
        "rg_track_gain": item.rg_track_gain,
    }
    if artist:
        payload["artist"] = artist
    if title:
        payload["title"] = title
    rundown_item = await _record_push_item(
        session,
        kind=pos.kind,
        when=pos.when_mode,
        queue="autodj",
        resource=resource,
        path=next_path,
        clock=clock,
        pos=pos,
        beets_id=getattr(item, "id", None),
    )
    payload["radiotomate_item_id"] = rundown_item.id
    command = await enqueue_command(
        session,
        action=PlayoutAction.QUEUE.value,
        queue="autodj",
        payload=payload,
        item=rundown_item,
    )
    payload["radiotomate_command_id"] = command.id
    command.payload = payload
    result = await dispatch_command(session, command, gateway)
    if result.ok:
        _log.info("Clock pushed track %s to autodj", next_path)
        return True
    _log.error("Clock autodj push failed: %s", result.error)
    return False


async def _record_push_item(  # noqa: PLR0913
    session: ormSession,
    *,
    kind: str,
    when: str,
    queue: str,
    resource: str,
    path: str | None,
    clock: Clock,
    pos: ClockPosition,
    cart_id: int | None = None,
    sound_id: int | None = None,
    beets_id: int | None = None,
    fallback_used: bool = False,
    reason: str | None = None,
    sync: str | None = None,
):
    from radiotomate.scheduler.execution import record_live_item

    return await record_live_item(
        session,
        payload={
            "at": now_paris().isoformat(),
            "kind": kind,
            "when": when,
            "queue": queue,
            "resource": resource,
            "path": path,
            "clock": clock.name,
            "clock_id": clock.id,
            "position_id": pos.id,
            "cart_id": cart_id,
            "sound_id": sound_id,
            "beets_id": beets_id,
            "fallback_used": fallback_used,
            "reason": reason,
            "sync": sync,
            "duration": 0,
            "status": RundownStatus.RESERVED.value,
        },
        status=RundownStatus.RESERVED.value,
    )

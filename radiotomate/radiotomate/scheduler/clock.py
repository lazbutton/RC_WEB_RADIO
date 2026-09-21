"""
Clock sequencer: motif + hard anchors → existing Liquidsoap queues.

Jingles cut Auto-DJ, never live / relay / a cart on air. Sequential jingles are
only pushed when the current track is nearly over. Hard anchors still cut — except
while the harbor is on air: the clock pauses, skips nothing, and drops due
anchors so they are not dumped after the live.

Hard-sync must not skip the output bed: that consumes the next Auto-DJ request
before the cart is ready. After the pub/jingle is on air, skip autodj_queue only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter
from typing import TYPE_CHECKING

from sqlalchemy import select

from radiotomate.domain.emission import is_harbor_source
from radiotomate.enums import (
    CartMode,
    PlayoutAction,
    PositionKind,
    RundownStatus,
    WhenMode,
)
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
from radiotomate.scheduler.music_rules import (  # noqa: F401 — re-exported
    ARTIST_LAST_N,
    ARTIST_WINDOW,
    TITLE_LAST_N,
    _choose_item,
    _music_history,
    _pick_music,
    _rule_sets,
)
from radiotomate.scheduler.outbox import dispatch_command, enqueue_command
from radiotomate.scheduler.playout import PlayoutGateway, gateway_for
from radiotomate.scheduler.timing import (  # noqa: F401 — re-exported
    JINGLE_PREEMPT_REMAINING,
    PARIS,
    _next_anchor,
    anchor_in_daypart,
    can_push_sequential_jingle,
    next_hard_anchor,
    next_hard_anchor_minute,
    next_soft_anchor,
    now_from_live,
    now_paris,
    queue_empty,
    queued_count,
    remaining_seconds,
    sequential_kind_cycle,
    source_id,
    track_would_overflow_anchor,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.orm import Session as ormSession

    from radiotomate.beets import BeetsIntegration

_log = logging.getLogger(__name__)


def _finish_tick(started: float, actions: list[str]) -> list[str]:
    runtime_metrics.tick_duration_seconds = perf_counter() - started
    return actions


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
    pending_autodj_skip: bool = False
    pending_autodj_skip_minute: int | None = None


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
    _state.pending_autodj_skip = False
    _state.pending_autodj_skip_minute = None


def note_carts_push(cart_id: int) -> None:
    """Remember which cart currently owns the carts Liquidsoap queue."""
    _state.active_cart_id = cart_id


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


async def tick(  # noqa: PLR0915
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
    gateway = gateway_for(playout_client)
    harbor = is_harbor_source(live_data.get("source"))
    if harbor:
        _state.pending_autodj_skip = False
        _state.pending_autodj_skip_minute = None
    if not harbor:
        from radiotomate.scheduler.outbox import dispatch_pending_commands

        await dispatch_pending_commands(session, gateway)
        await _refill_active_cart(session, live_data, gateway, actions)
        await _skip_cut_autodj_if_ready(live_data, gateway, now, actions)
    minute_of_day = now.hour * 60 + now.minute
    slot = await AutoDJSlot.from_time(session, minute_of_day, now.weekday())
    if slot is None or not slot.clock_id:
        _log.warning(
            "No clock on Auto-DJ slot at minute %s weekday %s; not filling queues",
            minute_of_day,
            now.weekday(),
        )
        if harbor:
            actions.append("live_hold")
        await persist_sequencer_cursor(session)
        return _finish_tick(started, actions)

    clock = await Clock.from_id(session, slot.clock_id)
    if clock is None:
        _log.warning("Clock %s missing; not filling queues", slot.clock_id)
        if harbor:
            actions.append("live_hold")
        await persist_sequencer_cursor(session)
        return _finish_tick(started, actions)

    if harbor:
        actions.append("live_hold")
        today = now.date().isoformat()
        _state.fired_anchors = {k for k in _state.fired_anchors if k[0] == today}
        nxt = await AutoDJSlot.next_after(session, slot)
        slot_end = slot.daypart_end_minute(nxt)
        _hold_due_anchors(clock, slot.minute, slot_end, now, actions)
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


def _on_air_title(live_data: dict) -> str:
    return str(live_data.get("title") or "").strip()


def _on_air_artist(live_data: dict) -> str:
    return str(live_data.get("artist") or "").strip()


def _item_matches_on_air(item: RundownItem, live_data: dict) -> bool:
    on_id = str(live_data.get("radiotomate_sound_id") or "").strip()
    if on_id and item.sound_id is not None and str(item.sound_id) == on_id:
        return True
    artist, title = _meta_from_item(item)
    on_title = _on_air_title(live_data)
    on_artist = _on_air_artist(live_data)
    if not title or not on_title:
        return False
    return title == on_title and (not artist or not on_artist or artist == on_artist)


def _cue_from_item(item: RundownItem) -> dict:
    artist, title = _meta_from_item(item)
    cue: dict = {"title": title, "artist": artist, "rid": 1}
    try:
        duration = float(item.duration or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration > 0:
        cue["duration"] = duration
    if item.path:
        cue["initial_uri"] = item.path
    if item.sound_id is not None:
        cue["radiotomate_sound_id"] = str(item.sound_id)
    return cue


def _next_field_for_item(item: RundownItem) -> str:
    if _is_jingle_item(item):
        return "next_jingle"
    if item.queue == "carts":
        return "next_cart"
    return "next_autodj"


def _ls_cue_usable(value: object, live_data: dict) -> bool:
    """True if Liquidsoap already exposes a next different from on-air."""
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return False
    if not isinstance(parsed, dict):
        return False
    try:
        rid = int(parsed.get("rid", -1))
    except (TypeError, ValueError):
        rid = -1
    title = str(parsed.get("title") or "").strip()
    artist = str(parsed.get("artist") or "").strip()
    if rid < 0 and not title:
        return False
    on_title = _on_air_title(live_data)
    on_artist = _on_air_artist(live_data)
    same_title = bool(on_title) and title == on_title
    same_artist = not artist or not on_artist or artist == on_artist
    if same_title and same_artist:
        return False
    on_id = str(live_data.get("radiotomate_sound_id") or "").strip()
    cue_id = str(parsed.get("radiotomate_sound_id") or "").strip()
    if on_id and cue_id and on_id == cue_id:
        return False
    return bool(title or rid > 0)


async def peek_next_sequential(
    session: ormSession,
    live_data: dict,
) -> tuple[str, dict] | None:
    """Next planned sequential item that is not the title on air (UI only, no push)."""
    pending = await _load_pushable_sequential(session)
    for item in pending:
        if not (item.path or "").strip():
            continue
        if _item_matches_on_air(item, live_data):
            continue
        return _next_field_for_item(item), _cue_from_item(item)
    return None


async def overlay_rundown_next(session: ormSession, live_data: dict) -> None:
    """Fill empty/duplicate LS next_* with the next rundown item."""
    peeked = await peek_next_sequential(session, live_data)
    if not peeked:
        return
    field, cue = peeked
    if _ls_cue_usable(live_data.get(field), live_data):
        return
    live_data[field] = cue


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
        row for row in rows if pos.id is not None and row.clock_position_id == pos.id
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


async def _beets_track_gain(beets_id: int) -> str | None:
    """
    ReplayGain of a Beets track, computed on the spot when the import skipped it.
    Returns ``None`` outside an application context (unit tests) or on failure.
    """
    from radiotomate.beets import BeetsIntegration

    try:
        beets = BeetsIntegration.get()
    except (RuntimeError, KeyError):
        return None
    try:
        return await beets.ensure_item_gain(beets_id)
    except Exception:
        _log.exception("ReplayGain lookup failed for beets item %s", beets_id)
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
    if item.beets_id and payload.get("rg_track_gain") is None:
        gain = await _beets_track_gain(item.beets_id)
        if gain is not None:
            payload["rg_track_gain"] = gain
            item.details = {**details, "rg_track_gain": gain}
        else:
            _log.warning(
                "Beets item %s pushed without ReplayGain (rundown %s)",
                item.beets_id,
                item.id,
            )
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


async def _skip_cut_autodj_if_ready(
    live_data: dict,
    gateway: PlayoutGateway,
    now: datetime,
    actions: list[str],
) -> None:
    """Skip autodj_queue once the hard-sync cart is on air, not the output bed."""
    if not _state.pending_autodj_skip:
        return
    src = source_id(live_data)
    if src in {"stream", "relay"}:
        _state.pending_autodj_skip = False
        _state.pending_autodj_skip_minute = None
        return
    if src not in {"carts", "jingles"}:
        if (
            _state.pending_autodj_skip_minute is not None
            and now.minute != _state.pending_autodj_skip_minute
        ):
            _state.pending_autodj_skip = False
            _state.pending_autodj_skip_minute = None
        return
    skip = await gateway.skip({"queue": "autodj"})
    if skip.ok:
        _state.pending_autodj_skip = False
        _state.pending_autodj_skip_minute = None
        actions.append("skip")
        return
    if not skip.transient:
        _state.pending_autodj_skip = False
        _state.pending_autodj_skip_minute = None
        _log.error("Deferred autodj skip after hard-sync failed: %s", skip.error)


async def _remember_cut_autodj(
    session: ormSession,
    live_data: dict,
    now: datetime,
) -> None:
    """Keep the cut title out of the next Beets pick even if it never logged on_air."""
    artist = str(live_data.get("artist") or "").strip()
    title = str(live_data.get("title") or "").strip()
    if not artist and not title:
        return
    payload = {
        "source": "autodj",
        "artist": artist,
        "title": title,
        "album": str(live_data.get("album") or ""),
        "initial_uri": str(
            live_data.get("initial_uri") or live_data.get("source_url") or ""
        ),
        "on_air": now.replace(tzinfo=None).isoformat(sep=" ")
        if now.tzinfo
        else now.isoformat(sep=" "),
        "skipped": True,
        "skip_reason": "hard_sync",
    }
    item_id = live_data.get("radiotomate_item_id")
    if item_id:
        payload["radiotomate_item_id"] = item_id
    log = await MetadataLog.from_playout(session, payload)
    session.add(log)


def _hold_due_anchors(
    clock: Clock,
    slot_start: int,
    slot_end: int,
    now: datetime,
    actions: list[str],
) -> None:
    """Drop due anchors while harbor is on air so they are not dumped after the live."""
    for pos in clock.anchored_positions():
        if pos.minute is None or not (pos.is_hard_sync or pos.is_soft_sync):
            continue
        if not anchor_in_daypart(slot_start, slot_end, now.hour, pos.minute):
            continue
        occurrence = now.replace(minute=pos.minute, second=0, microsecond=0)
        if now < occurrence:
            continue
        key = (now.date().isoformat(), now.hour, pos.minute)
        if key in _state.fired_anchors:
            continue
        _state.fired_anchors.add(key)
        actions.append(f"anchor:{pos.minute}:hold")


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
        if src not in {"carts", "stream", "relay"}:
            await _remember_cut_autodj(session, live_data, now)
            _state.pending_autodj_skip = True
            _state.pending_autodj_skip_minute = now.minute


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
    fallback_used = (
        bool(cart.id != pos.cart_id)
        if pos.cart_id
        else bool(pos.cart_title and cart.title != pos.cart_title)
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

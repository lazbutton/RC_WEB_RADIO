"""Pure time helpers of the clock engine: Paris clock, live payload readers,
anchor arithmetic. No state, no DB, no Liquidsoap."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from radiotomate.domain.emission import is_harbor_source

if TYPE_CHECKING:
    from radiotomate.models import Clock, ClockPosition

PARIS = ZoneInfo("Europe/Paris")
JINGLE_PREEMPT_REMAINING = 3.0


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
    if is_harbor_source(raw):
        return "stream"
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

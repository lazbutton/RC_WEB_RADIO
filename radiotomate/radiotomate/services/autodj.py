"""Application services for clock and weekly slot mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select, update

from radiotomate.domain.autodj import (
    protect_midnight_slot,
    require_expected_version,
    validate_clock_rows,
    validate_position,
    validate_slot_timing,
)
from radiotomate.domain.errors import (
    DomainConflict,
    DomainNotFound,
    DomainValidationError,
)
from radiotomate.enums import WhenMode
from radiotomate.models import (
    AutoDJSlot,
    Cart,
    Clock,
    ClockPosition,
    MusicCategory,
    RundownItem,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession


def _cart_name(cart: Cart | None, legacy: str | None) -> str | None:
    if cart is not None:
        return cart.title
    return legacy or None


def position_payload(position: ClockPosition) -> dict:
    payload = {
        "id": position.id,
        "kind": position.kind,
        "when": position.when_mode,
        "cart": _cart_name(position.cart, position.cart_title),
        "cart_id": position.cart_id,
        "category": position.category.name if position.category else None,
        "fallback_cart": _cart_name(
            position.fallback_cart,
            position.fallback_cart_title,
        ),
        "fallback_cart_id": position.fallback_cart_id,
    }
    if position.when_mode == WhenMode.ANCHORED.value:
        payload["minute"] = position.minute
        payload["sync"] = (
            position.sync_enum.value if position.sync_enum else None
        )
    return payload


def clock_payload(clock: Clock) -> dict:
    return {
        "id": clock.id,
        "version": clock.version,
        "name": clock.name,
        "fallback_cart": _cart_name(
            clock.fallback_cart,
            clock.fallback_cart_title,
        ),
        "fallback_cart_id": clock.fallback_cart_id,
        "motif": [position_payload(row) for row in clock.sequential_positions()],
        "anchors": [position_payload(row) for row in clock.anchored_positions()],
    }


def slot_payload(slot: AutoDJSlot, clock_names: dict[int, str]) -> dict:
    return {
        "id": slot.id,
        "version": slot.version,
        "day_of_week": slot.day_of_week,
        "minute": slot.minute,
        "title": slot.title,
        "color": slot.color,
        "clock_id": slot.clock_id,
        "clock": clock_names.get(slot.clock_id) if slot.clock_id else None,
    }


async def _cart_from_title(
    session: ormSession,
    raw_title: object,
    *,
    field: str,
) -> Cart | None:
    title = str(raw_title or "").strip()
    if not title:
        return None
    cart = await Cart.from_title(session, title)
    if cart is None:
        raise DomainValidationError(f"Unknown cart: {title}", field=field)
    return cart


async def _cart_from_id(
    session: ormSession,
    raw_id: object,
    *,
    field: str,
) -> Cart | None:
    if raw_id in {None, ""}:
        return None
    try:
        cart_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"invalid {field}", field=field) from exc
    cart = await Cart.from_id(session, cart_id)
    if cart is None:
        raise DomainValidationError(f"Unknown cart_id: {cart_id}", field=field)
    return cart


async def resolve_cart_ref(
    session: ormSession,
    data: dict,
    *,
    id_field: str,
    title_field: str,
) -> Cart | None:
    title_given = title_field in data
    title = str(data.get(title_field) or "").strip() if title_given else ""
    if title_given and not title:
        return None
    raw_id = data.get(id_field) if id_field in data else None
    if raw_id not in {None, ""}:
        by_id = await _cart_from_id(session, raw_id, field=id_field)
        if not title_given or by_id.title.strip().lower() == title.lower():
            return by_id
    if title:
        return await _cart_from_title(session, title, field=title_field)
    return None


def _position_id(data: dict) -> int | None:
    raw = data.get("id")
    if raw in {None, ""}:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _fill_position(
    session: ormSession,
    position: ClockPosition,
    data: dict,
    when_mode: str,
    sort_order: int,
) -> None:
    kind, minute, sync = validate_position(data, when_mode)
    category = None
    category_name = str(data.get("category") or "").strip()
    if category_name:
        category = await MusicCategory.from_name(session, category_name)
        if category is None:
            raise DomainValidationError(
                f"unknown category: {category_name}",
                field="category",
            )
    cart = await resolve_cart_ref(
        session,
        data,
        id_field="cart_id",
        title_field="cart",
    )
    fallback = await resolve_cart_ref(
        session,
        data,
        id_field="fallback_cart_id",
        title_field="fallback_cart",
    )
    position.sort_order = sort_order
    position.kind = kind
    position.when_mode = when_mode
    position.minute = minute
    position.sync = sync
    position.category = category
    position.category_id = category.id if category else None
    position.cart = cart
    position.cart_id = cart.id if cart else None
    position.cart_title = cart.title if cart else None
    position.fallback_cart = fallback
    position.fallback_cart_id = fallback.id if fallback else None
    position.fallback_cart_title = fallback.title if fallback else None


async def _make_position(
    session: ormSession,
    data: dict,
    when_mode: str,
    sort_order: int,
) -> ClockPosition:
    position = ClockPosition()
    await _fill_position(session, position, data, when_mode, sort_order)
    return position


async def apply_clock(
    session: ormSession,
    clock: Clock,
    data: dict,
    *,
    creating: bool,
) -> Clock:
    require_expected_version(clock.version, data.get("version"))
    name = str(data.get("name") or clock.name or "").strip()
    if not name:
        raise DomainValidationError("name required", field="name")
    existing = await Clock.from_name(session, name)
    if existing is not None and existing.id != clock.id:
        raise DomainConflict(f"Clock {name} already exists", field="name")

    clock.name = name
    if "fallback_cart" in data or "fallback_cart_id" in data or creating:
        fallback = await resolve_cart_ref(
            session,
            data,
            id_field="fallback_cart_id",
            title_field="fallback_cart",
        )
        clock.fallback_cart = fallback
        clock.fallback_cart_id = fallback.id if fallback else None
        clock.fallback_cart_title = fallback.title if fallback else ""

    if creating or "motif" in data or "anchors" in data:
        motif, anchors = validate_clock_rows(
            data.get("motif") or [],
            data.get("anchors") or [],
        )
        await clock.awaitable_attrs.positions
        by_id = {
            pos.id: pos
            for pos in clock.positions
            if pos.id is not None and pos.clock_id == clock.id
        }
        kept_rows: list[ClockPosition] = []
        claimed: set[int] = set()
        order = 0
        groups = (
            (WhenMode.SEQUENTIAL.value, motif),
            (WhenMode.ANCHORED.value, anchors),
        )
        for when_mode, rows in groups:
            for row in rows:
                pos_id = _position_id(row)
                current = by_id.get(pos_id) if pos_id is not None else None
                if current is not None and current.id in claimed:
                    current = None
                if current is None:
                    current = next(
                        (
                            pos
                            for pos in clock.positions
                            if pos.id is not None
                            and pos.id not in claimed
                            and pos.when_mode == when_mode
                        ),
                        None,
                    )
                if current is None:
                    current = await _make_position(session, row, when_mode, order)
                    clock.positions.append(current)
                else:
                    await _fill_position(session, current, row, when_mode, order)
                if current.id is not None:
                    claimed.add(current.id)
                kept_rows.append(current)
                order += 1
        dropped_ids = [
            pos.id
            for pos in clock.positions
            if pos not in kept_rows and pos.id is not None
        ]
        if dropped_ids:
            await session.execute(
                update(RundownItem)
                .where(RundownItem.clock_position_id.in_(dropped_ids))
                .values(clock_position_id=None)
            )
            await session.flush()
        for pos in list(clock.positions):
            if pos not in kept_rows:
                clock.positions.remove(pos)
    return clock


async def create_clock(session: ormSession, data: dict) -> Clock:
    clock = Clock(name=f"new-{uuid4().hex[:12]}", fallback_cart_title="")
    session.add(clock)
    await session.flush()
    return await apply_clock(session, clock, data, creating=True)


async def delete_clock(session: ormSession, clock_id: int) -> None:
    clock = await Clock.from_id(session, clock_id)
    if clock is None:
        raise DomainNotFound("Clock not found")
    usage = await session.scalar(
        select(AutoDJSlot.id).where(AutoDJSlot.clock_id == clock.id).limit(1)
    )
    if usage is not None:
        raise DomainConflict(
            "This clock is assigned to a daypart and cannot be deleted."
        )
    await session.delete(clock)


async def save_slot(  # noqa: PLR0913
    session: ormSession,
    slot: AutoDJSlot,
    *,
    title: str,
    color: str,
    day_of_week: int,
    minute: int,
    clock_id: int | None,
    expected_version: object = None,
) -> AutoDJSlot:
    require_expected_version(slot.version, expected_version)
    validate_slot_timing(day_of_week, minute)
    if slot.id is not None:
        protect_midnight_slot(
            previous_day=slot.day_of_week,
            previous_minute=slot.minute,
            next_day=day_of_week,
            next_minute=minute,
        )
    slot.title = title.strip()
    slot.color = color
    slot.day_of_week = day_of_week
    slot.minute = minute
    slot.clock_id = clock_id
    existing = await AutoDJSlot.from_time(
        session,
        minute,
        day_of_week=day_of_week,
        exact=True,
    )
    if existing is not None and existing.id != slot.id:
        raise DomainConflict(
            "A slot is already scheduled at that time.",
            field="minute",
        )
    if clock_id is not None and await Clock.from_id(
        session,
        clock_id,
        load_positions=False,
    ) is None:
        raise DomainValidationError("unknown clock_id", field="clock_id")
    return slot


async def delete_slot(session: ormSession, slot_id: int) -> None:
    slot = await AutoDJSlot.from_id(session, slot_id)
    if slot is None:
        raise DomainNotFound("Auto-DJ slot not found")
    if slot.minute == 0:
        raise DomainConflict("The midnight slot is required and cannot be deleted.")
    await session.delete(slot)

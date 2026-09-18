"""Application services for clock and weekly slot mutations."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select

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
from radiotomate.models import AutoDJSlot, Cart, Clock, ClockPosition, MusicCategory

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
    if id_field in data:
        return await _cart_from_id(session, data.get(id_field), field=id_field)
    return await _cart_from_title(session, data.get(title_field), field=title_field)


async def _make_position(
    session: ormSession,
    data: dict,
    when_mode: str,
    sort_order: int,
) -> ClockPosition:
    kind, minute, sync = validate_position(data, when_mode)
    category_id = None
    category_name = data.get("category")
    if category_name:
        category = await MusicCategory.from_name(session, str(category_name))
        if category is None:
            raise DomainValidationError(
                f"unknown category: {category_name}",
                field="category",
            )
        category_id = category.id
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
    return ClockPosition(
        sort_order=sort_order,
        kind=kind,
        when_mode=when_mode,
        minute=minute,
        sync=sync,
        category_id=category_id,
        cart_id=cart.id if cart else None,
        cart_title=cart.title if cart else None,
        fallback_cart_id=fallback.id if fallback else None,
        fallback_cart_title=fallback.title if fallback else None,
    )


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
        clock.positions.clear()
        await session.flush()
        for order, row in enumerate(motif):
            clock.positions.append(
                await _make_position(
                    session,
                    row,
                    WhenMode.SEQUENTIAL.value,
                    order,
                )
            )
        offset = len(motif)
        for order, row in enumerate(anchors, start=offset):
            clock.positions.append(
                await _make_position(
                    session,
                    row,
                    WhenMode.ANCHORED.value,
                    order,
                )
            )
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

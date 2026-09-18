"""Application-level invariants for Auto-DJ administration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from werkzeug.exceptions import BadRequest, Conflict

from radiotomate.models import AutoDJSlot, Cart, Clock

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession


def assert_slot_deletable(slot: AutoDJSlot) -> None:
    if slot.minute == 0:
        raise Conflict("The midnight slot is required and cannot be deleted.")


def assert_slot_time_mutable(
    slot: AutoDJSlot,
    day_of_week: int,
    minute: int,
) -> None:
    if slot.minute == 0 and (
        day_of_week != slot.day_of_week or minute != slot.minute
    ):
        raise Conflict("The midnight slot cannot be moved.")


async def assert_clock_deletable(
    session: ormSession,
    clock: Clock,
) -> None:
    usage = await session.scalar(
        select(AutoDJSlot)
        .where(AutoDJSlot.clock_id == clock.id)
        .order_by(AutoDJSlot.day_of_week, AutoDJSlot.minute)
        .limit(1)
    )
    if usage is not None:
        raise Conflict(
            "This clock is assigned to a daypart and cannot be deleted."
        )


def _cart_titles(clock: Clock, data: dict, *, creating: bool) -> set[str]:
    titles: set[str] = set()
    if "fallback_cart" in data or creating:
        fallback = str(data.get("fallback_cart") or "").strip()
    else:
        fallback = clock.fallback_cart_title.strip()
    if fallback:
        titles.add(fallback)

    if creating or "motif" in data or "anchors" in data:
        rows = [*(data.get("motif") or []), *(data.get("anchors") or [])]
        for row in rows:
            if not isinstance(row, dict):
                continue
            for field in ("cart", "fallback_cart"):
                title = str(row.get(field) or "").strip()
                if title:
                    titles.add(title)
    else:
        for position in clock.positions:
            if position.cart_title:
                titles.add(position.cart_title)
            if position.fallback_cart_title:
                titles.add(position.fallback_cart_title)
    return titles


async def validate_clock_cart_references(
    session: ormSession,
    clock: Clock,
    data: dict,
    *,
    creating: bool,
) -> None:
    titles = _cart_titles(clock, data, creating=creating)
    if not titles:
        return
    found = set(
        await session.scalars(select(Cart.title).where(Cart.title.in_(titles)))
    )
    missing = sorted(titles - found)
    if missing:
        raise BadRequest(f"Unknown cart: {', '.join(missing)}")

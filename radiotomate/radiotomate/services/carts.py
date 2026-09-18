"""Application-level cart identity and integrity rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_, select, update

from radiotomate.domain.autodj import require_expected_version
from radiotomate.domain.errors import DomainConflict
from radiotomate.models import Clock, ClockPosition

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession

    from radiotomate.models import Cart


def require_cart_version(cart: Cart, expected: object) -> None:
    require_expected_version(cart.version, expected)


async def sync_cart_display_titles(session: ormSession, cart: Cart) -> None:
    """Keep legacy title columns aligned after a cart rename."""
    await session.execute(
        update(Clock)
        .where(Clock.fallback_cart_id == cart.id)
        .values(fallback_cart_title=cart.title)
    )
    await session.execute(
        update(ClockPosition)
        .where(ClockPosition.cart_id == cart.id)
        .values(cart_title=cart.title)
    )
    await session.execute(
        update(ClockPosition)
        .where(ClockPosition.fallback_cart_id == cart.id)
        .values(fallback_cart_title=cart.title)
    )


async def assert_cart_deletable(session: ormSession, cart: Cart) -> None:
    clock_id = await session.scalar(
        select(Clock.id).where(Clock.fallback_cart_id == cart.id).limit(1)
    )
    position_id = await session.scalar(
        select(ClockPosition.id)
        .where(
            or_(
                ClockPosition.cart_id == cart.id,
                ClockPosition.fallback_cart_id == cart.id,
            )
        )
        .limit(1)
    )
    if clock_id is not None or position_id is not None:
        raise DomainConflict(
            "This cart is referenced by a clock and cannot be deleted."
        )

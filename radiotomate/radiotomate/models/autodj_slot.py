from __future__ import annotations

import logging
from datetime import datetime
from random import randint

from sqlalchemy import Integer, String, select
from sqlalchemy.dialects.sqlite import DATETIME, JSON
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)
from sqlalchemy.orm import Session as ormSession

from radiotomate.db import Base

_log = logging.getLogger(__name__)


class AutoDJSlot(Base):
    """
    This entity represents one slot in the auto-DJ's schedule.

    This is a weekly schedule, accurate to the minute. Each slot defines the auto-DJ's
    constraints during its `day_of_week` (monday is 0) starting from its n-th `minute`.
    Midnight is `minute = 0`, noon is `minute = 720`, and there is at most one slot per
    day of week and per minute.

    The application assumes that the schedule's coverage is always complete, ie. there
    is always at least one row per day-of-week having `minute = 0` - those are the only
    entries whose `minute` column cannot be edited.

    Color and title are display options, that the user can set to improve its calendar's
    readability.
    """

    __tablename__ = "autodj_slots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    minute: Mapped[int] = mapped_column(Integer, nullable=False)
    constraints: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON),
        nullable=False,
        default={},
    )
    color: Mapped[str] = mapped_column(String, nullable=False, default="")
    title: Mapped[str] = mapped_column(String, nullable=False, default="")
    created: Mapped[datetime] = mapped_column(
        DATETIME, default=datetime.now, nullable=False
    )
    modified: Mapped[datetime] = mapped_column(
        DATETIME,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )

    @classmethod
    async def all(cls, session: ormSession) -> list[AutoDJSlot]:
        """
        Return all slots, sorted by day of week and minute
        """
        return await session.scalars(
            select(AutoDJSlot).order_by(AutoDJSlot.day_of_week, AutoDJSlot.minute)
        )

    @classmethod
    async def from_id(cls, session: ormSession, slot_id: int) -> AutoDJSlot | None:
        return await session.scalar(select(AutoDJSlot).filter(AutoDJSlot.id == slot_id))

    @classmethod
    async def from_time(
        cls,
        session: ormSession,
        minute: int,
        day_of_week: int | None = None,
        exact=False,
    ) -> AutoDJSlot | None:
        """
        Return the first slot that matches given time constraints, if any.

        If ``day_of_week`` is not provided, search any day that matches given time.

        If ``exact`` is ``False`` (by default), search for the current slot, ie. the
        first one having given ``minute`` or less.
        """
        q = select(AutoDJSlot)
        if exact:
            q = q.filter(AutoDJSlot.minute == minute)
        else:
            q = q.filter(AutoDJSlot.minute <= minute)
        if day_of_week is not None:
            q = q.filter(AutoDJSlot.day_of_week == day_of_week)
        q = q.order_by(AutoDJSlot.minute.desc())
        return await session.scalar(q)

    @classmethod
    async def current_filter(cls, session: ormSession) -> str:
        """
        Return a Beets expression matching the AutoDJ constraint at current time

        Returns an empty stringif something goes wrong.
        """
        now = datetime.now()
        minute = now.minute + 60 * now.hour
        slot = await cls.from_time(session, minute, now.weekday())
        if slot:
            return slot.pick_filter()
        else:
            _log.warning("no AutoDJSlot found !!")
            return ""

    def pick_filter(self) -> str:
        cumulated = 0
        max_weight = self.constraints.get("totalweight", 0)
        filters = self.constraints.get("filters", [])
        if not filters:
            return ""
        weight = randint(0, max_weight - 1)
        for f in filters:
            cumulated += f[0]
            if len(f) == 2 and weight < cumulated:
                return f[1]
        _log.warning(
            "Consistency error: picked weight %r, did not match any in %r",
            weight,
            filters,
        )
        return ""

    @property
    def display_time(self) -> str:
        hour = self.minute // 60
        minute = self.minute % 60
        return f"{hour:02d}:{minute:02d}"

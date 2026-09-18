from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path  # noqa: TC003
from random import choice
from typing import ClassVar

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Boolean, Integer, Select, String, case, delete, func, select
from sqlalchemy.dialects.sqlite import DATETIME
from sqlalchemy.orm import (
    Mapped,
    Session,
    mapped_column,
    query_expression,
    relationship,
    selectinload,
    with_expression,
)

from radiotomate.db import Base
from radiotomate.enums import CartMode, ScheduleMode
from radiotomate.models.sound import Sound

_log = logging.getLogger(__name__)

# Sound carts having this value as Cart.url should be pushed to the auto-dj queue
# instead of the usual carts queue
URL_TO_AUTODJ_QUEUE = "autodj"
JINGLES_QUEUE = "jingles"
CARTS_QUEUE = "carts"


class Cart(Base):
    """
    Sound cart: contains sounds that can be scheduled to play.

    This class does **not** manage the cart's folder, it's only stored in
    ``path`` for future reference.
    """

    __tablename__ = "carts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    path: Mapped[Path]  # should be null only while creating the cart's folder
    url: Mapped[str]
    average_duration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_duration: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(String, nullable=False, default="")
    created: Mapped[datetime] = mapped_column(DATETIME, default=datetime.now)
    modified: Mapped[datetime] = mapped_column(
        DATETIME,
        default=datetime.now,
        onupdate=datetime.now,
    )

    mode: Mapped[CartMode]
    schedule_mode: Mapped[ScheduleMode]
    schedule_correct: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # all schedule filter columns default to * to ease tests
    schedule_year: Mapped[str] = mapped_column(String, nullable=False, default="*")
    schedule_month: Mapped[str] = mapped_column(String, nullable=False, default="*")
    schedule_day: Mapped[str] = mapped_column(String, nullable=False, default="*")
    schedule_week: Mapped[str] = mapped_column(String, nullable=False, default="*")

    # as in APScheduler, 0 is monday
    schedule_day_of_week: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="*",
    )
    schedule_hour: Mapped[str] = mapped_column(String, nullable=False, default="*")
    schedule_minute: Mapped[str] = mapped_column(String, nullable=False, default="*")
    schedule_second: Mapped[str] = mapped_column(String, nullable=False, default="*")

    sounds: Mapped[list[Sound]] = relationship(
        lazy=True,
        back_populates="cart",
        order_by="Sound.rank",
        cascade="save-update, merge, expunge, delete",
    )
    sounds_count: Mapped[int] = query_expression()
    inactive_sounds_count: Mapped[int] = query_expression()

    __mapper_args__: ClassVar[dict] = {"version_id_col": version}

    @classmethod
    def add_query_expressions(cls, query: Select) -> Select:
        """
        This is an alternative to `q.options(selectinload(Cart.sounds))`: it adds
        mapped query_expression to a query selecting from Cart, including the outer
        join and group by cart.
        """
        return (
            query.join_from(Cart, Cart.sounds, isouter=True)
            .group_by(Cart.id)
            .options(
                with_expression(Cart.sounds_count, func.count(Cart.sounds)),
                with_expression(
                    Cart.inactive_sounds_count,
                    func.sum(
                        case(
                            (Sound.active.is_(False), 1),
                            (Sound.gain.is_(None), 1),
                            else_=0,
                        )
                    ),
                ),
            )
        )

    def postfill_query_expressions(self) -> None:
        """
        Compute query-expression fields when Cart.sounds is fully loaded
        """
        self.sounds_count = len(self.sounds)

        def inactive(sound: Sound) -> int:
            if not sound.available:
                return 1
            else:
                return 0

        self.inactive_sounds_count = sum(map(inactive, self.sounds))

    @classmethod
    async def all(
        cls,
        session: Session,
        schedule_mode: ScheduleMode | None = None,
        load_sounds: bool = False,
        load_uploaders: bool = False,
    ) -> list[Cart]:
        """
        Returns all carts, with sounds_count pre-computed.

        If ``schedule_mode`` is provided, will only select carts having the given value.

        Associated sounds are join-loaded if ``load_sounds``.
        """
        q = select(Cart)
        if schedule_mode:
            q = q.filter(Cart.schedule_mode == schedule_mode)
        if load_sounds:
            if load_uploaders:
                q = q.options(selectinload(Cart.sounds).selectinload(Sound.uploader))
            else:
                q = q.options(selectinload(Cart.sounds))
        else:
            q = cls.add_query_expressions(q)
        carts = (await session.scalars(q)).all()
        if load_sounds:
            for cart in carts:
                cart.postfill_query_expressions()
        return carts

    @classmethod
    async def from_title(cls, session: Session, title: str) -> Cart | None:
        return await session.scalar(select(Cart).filter(Cart.title == title))

    @classmethod
    async def from_id(
        cls,
        session: Session,
        cart_id: int,
        load_sounds: bool = False,
        load_uploaders: bool = False,
    ) -> Cart | None:
        """
        Returns a cart. `sounds_count` is pre-computed, or associated sounds
        are preloaded if `load_sounds` is True.
        `load_uploaders` requires `load_sounds` and will also pre-load
        `cart.sound[i].uploader`.
        """
        q = select(Cart)
        if load_sounds:
            if load_uploaders:
                q = q.options(selectinload(Cart.sounds).selectinload(Sound.uploader))
            else:
                q = q.options(selectinload(Cart.sounds))
        else:
            q = cls.add_query_expressions(q)
        q = q.filter(Cart.id == cart_id)
        cart = await session.scalar(q)
        if cart and load_sounds:
            cart.postfill_query_expressions()
        return cart

    @classmethod
    async def delete(cls, session: Session, cart_id: int):
        await session.execute(delete(Cart).where(Cart.id == cart_id))

    @property
    def schedule_repr(self) -> str:
        return "[{},{}] at {} {} {} {} {} {} {} {}".format(
            self.schedule_mode,
            "OK" if self.schedule_correct else "error",
            self.schedule_year,
            self.schedule_month,
            self.schedule_day,
            self.schedule_week,
            self.schedule_day_of_week,
            self.schedule_hour,
            self.schedule_minute,
            self.schedule_second,
        )

    _JOURS_COURTS = ("Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim.")

    def schedule_summary(self) -> str:
        """
        Compact next-occurrence label for the carts table.
        Simple timed carts: ``Mer. 14:03``. Advanced: next fire time or condensed cron.
        """
        if self.schedule_mode is ScheduleMode.JINGLES:
            base = "Jingles"
        elif not self.schedule_is_advanced:
            try:
                dow = int(self.schedule_day_of_week)
                hour = int(self.schedule_hour)
                minute = int(self.schedule_minute)
                base = f"{self._JOURS_COURTS[dow]} {hour:02d}:{minute:02d}"
            except (ValueError, IndexError):
                base = self._schedule_cron_short()
        else:
            base = self._next_fire_display() or self._schedule_cron_short()
        if not self.schedule_correct:
            return f"{base} · erreur"
        return base

    def _schedule_cron_short(self) -> str:
        return (
            f"{self.schedule_day_of_week} "
            f"{self.schedule_hour}:{self.schedule_minute}"
        )

    def _next_fire_display(self) -> str | None:
        try:
            nxt = self.to_timed_trigger().next()
        except Exception:
            return None
        if nxt is None:
            return None
        return f"proch. {nxt.strftime('%d/%m %H:%M')}"

    @property
    def schedule_is_advanced(self) -> bool:
        if (
            self.schedule_mode is ScheduleMode.TIMED
            and self.schedule_year == "*"
            and self.schedule_month == "*"
            and self.schedule_day == "*"
            and self.schedule_week == "*"
            and self.schedule_second == "0"
        ):
            try:
                _ = int(self.schedule_day_of_week)
                _ = int(self.schedule_hour)
                _ = int(self.schedule_minute)
                return False
            except ValueError:
                return True
        return self.schedule_mode is ScheduleMode.TIMED

    def to_timed_trigger(self) -> CronTrigger:
        """
        Returns a CronTrigger object from a cart's schedule columns, when it's `timed`.
        Raises an IndexError if the schedule is invalid.

        Note: APScheduler's week days are 0-7 for sun,mon,tue,wed,thu,fri,sat, sun
        """
        return CronTrigger(
            year=self.schedule_year,
            month=self.schedule_month,
            day=self.schedule_day,
            week=self.schedule_week,
            day_of_week=self.schedule_day_of_week,
            hour=self.schedule_hour,
            minute=self.schedule_minute,
            second=self.schedule_second,
        )

    def recompute_duration(self):
        self.average_duration = sum([s.duration for s in self.sounds]) // len(
            self.sounds,
        )

    def playout_queue(self) -> str:
        """Liquidsoap queue used by « Diffuser maintenant » / pads."""
        if self.url == URL_TO_AUTODJ_QUEUE:
            return URL_TO_AUTODJ_QUEUE
        if self.schedule_mode is ScheduleMode.JINGLES:
            return JINGLES_QUEUE
        return CARTS_QUEUE

    def next_sound(self, for_display=False) -> Sound | None:
        """
        ``for_display`` tells if the method is called to display the next sound
        or (by default) if it's called to actually play the sound. This avoids
        displaying a false information for carts playing randomly.
        """
        if for_display and self.mode is CartMode.RANDOM:
            return None
        implem = getattr(self, "next_sound_" + self.mode.value)
        return implem()

    def next_sound_playlist(self) -> Sound | None:
        for sound in self.sounds:
            if sound.available and not sound.last_played:
                return sound

    def next_sound_playlist_loop(self) -> Sound | None:
        last_played = datetime(2000, 1, 1)
        last_played_rank = None
        for sound in self.sounds:
            if sound.last_played and sound.last_played > last_played:
                last_played_rank = sound.rank
                last_played = sound.last_played

        if last_played_rank:
            for sound in self.sounds:
                if sound.available and sound.rank > last_played_rank:
                    return sound

        for sound in self.sounds:
            if sound.available:
                return sound

        return None

    def next_sound_random(self) -> Sound | None:
        available_sounds = [s for s in self.sounds if s.available]
        return choice(available_sounds)

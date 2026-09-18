from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path  # noqa: TC003

from sqlalchemy import Boolean, ForeignKey, Integer, String, func, or_, select
from sqlalchemy.dialects.sqlite import DATETIME
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from radiotomate.db import Base

_log = logging.getLogger(__name__)


class Sound(Base):
    """
    A sound file in a cart.
    """

    __tablename__ = "sounds"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cart_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("carts.id"),
        nullable=False,
        index=True,
    )
    rank: Mapped[int] = mapped_column(Integer, default=1)
    path: Mapped[Path]
    duration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created: Mapped[datetime] = mapped_column(
        DATETIME,
        default=datetime.now,
        nullable=False,
    )
    last_played: Mapped[datetime] = mapped_column(DATETIME, nullable=True)
    gain: Mapped[float]
    peak: Mapped[float]
    uploader_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))

    cart: Mapped[Cart] = relationship(lazy="joined", back_populates="sounds")  # noqa: F821

    uploader: Mapped[User] = relationship()  # noqa: F821

    @property
    def available(self) -> bool:
        """
        The active field is explicitely set by the user, while gain should be computed
        soon by our scheduler's analyzer.

        Note that this logic is also implemented for Cart's query expressions.
        """
        return self.active and self.gain is not None

    @classmethod
    async def from_id(cls, session: Session, sound_id: int) -> Sound | None:
        return await session.scalar(select(Sound).filter(Sound.id == sound_id))

    @classmethod
    async def next_rank(cls, session: Session, cart_id: int) -> int:
        """
        Returns the next rank for a cart's sounds.
        """
        q = select(func.max(Sound.rank).filter(Sound.cart_id == cart_id))
        result = await session.scalar(q)
        if result is None:
            return 1
        else:
            return result + 1

    @classmethod
    async def update_ranks(cls, session: Session, cart_id: int) -> list[Sound]:
        """
        Updates the ranks of all sounds in a cart, ensuring they're numbered from 1.
        Returns all sounds.
        """
        q = select(Sound).filter(Sound.cart_id == cart_id).order_by(Sound.rank)
        sounds = list(await session.scalars(q))
        rank = 1
        for sound in sounds:
            if sound.rank != rank:
                sound.rank = rank
            rank += 1
        return sounds

    @classmethod
    async def change_path_prefix(
        cls,
        session: Session,
        cart_id: int,
        old: Path,
        new: Path,
    ):
        """
        Updates path when ``cart_id`` is moved from ``old`` to ``new``.
        """
        q = select(Sound).filter(Sound.cart_id == cart_id)
        sounds = list(await session.scalars(q))
        for sound in sounds:
            try:
                sound.path = new / sound.path.relative_to(old)
            except ValueError:
                # Banque /media : le fichier n'est pas dans le dossier du cart.
                continue

    @classmethod
    async def ids_without_gain(cls, session: Session) -> list[int]:
        """
        Returns the list of Sound.id that are missing replaingain fields
        """
        q = select(Sound.id).filter(or_(Sound.gain == None, Sound.peak == None))  # noqa: E711
        return list(await session.scalars(q))

from __future__ import annotations

from sqlalchemy import Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import Session as ormSession

from radiotomate.db import Base


class MusicCategory(Base):
    """
    Named Beets query, reusable as a clock position target.
    """

    __tablename__ = "music_categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    query: Mapped[str] = mapped_column(String, nullable=False)
    empty_query: Mapped[str] = mapped_column(String, nullable=False, default="")

    @classmethod
    async def from_id(
        cls,
        session: ormSession,
        category_id: int,
    ) -> MusicCategory | None:
        return await session.scalar(
            select(MusicCategory).filter(MusicCategory.id == category_id)
        )

    @classmethod
    async def from_name(cls, session: ormSession, name: str) -> MusicCategory | None:
        return await session.scalar(
            select(MusicCategory).filter(MusicCategory.name == name)
        )

    @classmethod
    async def all(cls, session: ormSession) -> list[MusicCategory]:
        rows = await session.scalars(
            select(MusicCategory).order_by(MusicCategory.name)
        )
        return list(rows)

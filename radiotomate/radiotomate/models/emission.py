from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from sqlalchemy import Integer, String, select
from sqlalchemy.dialects.sqlite import DATETIME
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import Session as ormSession

from radiotomate.db import Base

SCOPE_WEEKLY = "weekly"
SCOPE_SESSION = "session"


class Emission(Base):
    """Live show overlay: weekly grid window or Antenne harbor session."""

    __tablename__ = "emissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    artist: Mapped[str] = mapped_column(String, nullable=False, default="")
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created: Mapped[datetime] = mapped_column(
        DATETIME, default=datetime.now, nullable=False
    )
    modified: Mapped[datetime] = mapped_column(
        DATETIME,
        nullable=False,
        default=datetime.now,
        onupdate=datetime.now,
    )

    __mapper_args__: ClassVar[dict] = {"version_id_col": version}

    @classmethod
    async def from_id(cls, session: ormSession, emission_id: int) -> Emission | None:
        return await session.scalar(select(Emission).filter(Emission.id == emission_id))

    @classmethod
    async def all_weekly(cls, session: ormSession) -> list[Emission]:
        rows = await session.scalars(
            select(Emission)
            .filter(Emission.scope == SCOPE_WEEKLY)
            .order_by(Emission.day_of_week, Emission.start_minute)
        )
        return list(rows)

    @classmethod
    async def all_sessions(cls, session: ormSession) -> list[Emission]:
        rows = await session.scalars(
            select(Emission).filter(Emission.scope == SCOPE_SESSION)
        )
        return list(rows)

"""Persistent intent and command journal for the antenna scheduler."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, select
from sqlalchemy.dialects.sqlite import DATETIME, JSON
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import Session as ormSession

from radiotomate.db import Base
from radiotomate.enums import CommandStatus, RundownStatus

if TYPE_CHECKING:
    from radiotomate.models.cart import Cart
    from radiotomate.models.clock import Clock, ClockPosition
    from radiotomate.models.sound import Sound


class ProgrammingVersion(Base):
    """Immutable fingerprint of the clocks/dayparts used to build a rundown."""

    __tablename__ = "programming_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created: Mapped[datetime] = mapped_column(
        DATETIME,
        nullable=False,
        default=datetime.now,
    )
    source: Mapped[str] = mapped_column(String, nullable=False, default="autodj")
    description: Mapped[str] = mapped_column(String, nullable=False, default="")


class RundownItem(Base):
    """One durable, versioned intention in the rolling rundown."""

    __tablename__ = "rundown_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    programming_version_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("programming_versions.id"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_at: Mapped[datetime] = mapped_column(DATETIME, nullable=False, index=True)
    duration: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    when_mode: Mapped[str] = mapped_column(String, nullable=False)
    sync: Mapped[str | None] = mapped_column(String, nullable=True)
    queue: Mapped[str] = mapped_column(String, nullable=False)
    resource: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str | None] = mapped_column(String, nullable=True)
    clock_name: Mapped[str | None] = mapped_column(String, nullable=True)
    daypart: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON),
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default=RundownStatus.PLANNED.value,
        index=True,
    )
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    origin: Mapped[str] = mapped_column(String, nullable=False, default="clock")
    clock_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("clocks.id"),
        nullable=True,
    )
    clock_position_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("clock_positions.id"),
        nullable=True,
    )
    cart_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("carts.id"),
        nullable=True,
    )
    sound_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("sounds.id"),
        nullable=True,
    )
    beets_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fallback_used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created: Mapped[datetime] = mapped_column(
        DATETIME,
        nullable=False,
        default=datetime.now,
    )
    reserved_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    programming_version: Mapped[ProgrammingVersion] = relationship()
    clock: Mapped[Clock | None] = relationship(foreign_keys=[clock_id])
    clock_position: Mapped[ClockPosition | None] = relationship(
        foreign_keys=[clock_position_id],
    )
    cart: Mapped[Cart | None] = relationship(foreign_keys=[cart_id])
    sound: Mapped[Sound | None] = relationship(foreign_keys=[sound_id])
    commands: Mapped[list[PlayoutCommand]] = relationship(
        back_populates="rundown_item",
    )

    __mapper_args__: ClassVar[dict] = {"version_id_col": version}

    @classmethod
    async def from_id(
        cls,
        session: ormSession,
        item_id: str,
    ) -> RundownItem | None:
        return await session.scalar(select(cls).where(cls.id == item_id))


class PlayoutCommand(Base):
    """Transactional outbox row for one idempotent playout action."""

    __tablename__ = "playout_commands"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    rundown_item_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("rundown_items.id"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    queue: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON),
        nullable=False,
        default=dict,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default=CommandStatus.PENDING.value,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DATETIME,
        nullable=False,
        default=datetime.now,
    )
    lease_until: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created: Mapped[datetime] = mapped_column(
        DATETIME,
        nullable=False,
        default=datetime.now,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DATETIME, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DATETIME,
        nullable=True,
    )

    rundown_item: Mapped[RundownItem | None] = relationship(
        back_populates="commands",
    )

    @classmethod
    async def from_idempotency_key(
        cls,
        session: ormSession,
        key: str,
    ) -> PlayoutCommand | None:
        return await session.scalar(
            select(cls).where(cls.idempotency_key == key)
        )

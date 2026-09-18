from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload
from sqlalchemy.orm import Session as ormSession

from radiotomate.db import Base
from radiotomate.enums import PositionKind, SyncMode, WhenMode
from radiotomate.models.music_category import MusicCategory  # noqa: TC001

if TYPE_CHECKING:
    from radiotomate.models.cart import Cart


class Clock(Base):
    """
    Named reusable recipe: sequential motif, optional anchors, fallback cart.
    """

    __tablename__ = "clocks"
    DEFAULT_NAME = "24/24 Rotation habillée"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    fallback_cart_title: Mapped[str] = mapped_column(String, nullable=False, default="")
    fallback_cart_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("carts.id"),
        nullable=True,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    positions: Mapped[list[ClockPosition]] = relationship(
        back_populates="clock",
        order_by="ClockPosition.sort_order",
        cascade="save-update, merge, expunge, delete, delete-orphan",
    )
    fallback_cart: Mapped[Cart | None] = relationship(
        foreign_keys=[fallback_cart_id],
    )

    __mapper_args__: ClassVar[dict] = {"version_id_col": version}

    @classmethod
    async def from_id(
        cls,
        session: ormSession,
        clock_id: int,
        load_positions: bool = True,
    ) -> Clock | None:
        q = select(Clock).filter(Clock.id == clock_id)
        if load_positions:
            q = q.options(
                selectinload(Clock.positions).selectinload(ClockPosition.category),
                selectinload(Clock.positions).selectinload(ClockPosition.cart),
                selectinload(Clock.positions).selectinload(
                    ClockPosition.fallback_cart
                ),
                selectinload(Clock.fallback_cart),
            )
        return await session.scalar(q)

    @classmethod
    async def all(
        cls,
        session: ormSession,
        load_positions: bool = False,
    ) -> list[Clock]:
        q = select(Clock).order_by(Clock.name)
        if load_positions:
            q = q.options(
                selectinload(Clock.positions).selectinload(ClockPosition.category),
                selectinload(Clock.positions).selectinload(ClockPosition.cart),
                selectinload(Clock.positions).selectinload(
                    ClockPosition.fallback_cart
                ),
                selectinload(Clock.fallback_cart),
            )
        rows = await session.scalars(q)
        return list(rows)

    @classmethod
    async def from_name(cls, session: ormSession, name: str) -> Clock | None:
        return await session.scalar(select(Clock).filter(Clock.name == name))

    def sequential_positions(self) -> list[ClockPosition]:
        return [
            p
            for p in self.positions
            if p.when_mode == WhenMode.SEQUENTIAL.value
        ]

    def anchored_positions(self) -> list[ClockPosition]:
        return [
            p for p in self.positions if p.when_mode == WhenMode.ANCHORED.value
        ]


class ClockPosition(Base):
    """
    One slot in a clock: a *quoi*, sequential or anchored.
    """

    __tablename__ = "clock_positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clock_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    when_mode: Mapped[str] = mapped_column(String, nullable=False)
    minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sync: Mapped[str | None] = mapped_column(String, nullable=True)
    category_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("music_categories.id"),
        nullable=True,
    )
    cart_title: Mapped[str | None] = mapped_column(String, nullable=True)
    fallback_cart_title: Mapped[str | None] = mapped_column(String, nullable=True)
    cart_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("carts.id"),
        nullable=True,
        index=True,
    )
    fallback_cart_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("carts.id"),
        nullable=True,
        index=True,
    )

    clock: Mapped[Clock] = relationship(back_populates="positions")
    category: Mapped[MusicCategory | None] = relationship()
    cart: Mapped[Cart | None] = relationship(foreign_keys=[cart_id])
    fallback_cart: Mapped[Cart | None] = relationship(
        foreign_keys=[fallback_cart_id],
    )

    @property
    def kind_enum(self) -> PositionKind:
        return PositionKind(self.kind)

    @property
    def when_enum(self) -> WhenMode:
        return WhenMode(self.when_mode)

    @property
    def sync_enum(self) -> SyncMode | None:
        if not self.sync:
            if self.kind == PositionKind.PUB.value:
                return SyncMode.DURE
            if self.kind == PositionKind.SON.value:
                return SyncMode.MOLLE
            return None
        return SyncMode(self.sync)

    @property
    def is_hard_sync(self) -> bool:
        return self.sync_enum is SyncMode.DURE

    @property
    def is_soft_sync(self) -> bool:
        return self.sync_enum is SyncMode.MOLLE

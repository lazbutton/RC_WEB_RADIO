from __future__ import annotations

import logging
import secrets
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, select
from sqlalchemy.dialects.sqlite import DATETIME
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import Session as ormSession

from radiotomate.db import Base

_log = logging.getLogger(__name__)


class Session(Base):
    """
    User session data - keeps track of who identified when, on which user-agent
    and from what IP address. Also notes when was the last action.

    Quart's session cookie only stores the ID of a line in this table, this
    ensure we can log out an user on the server side if their account is
    compromised.
    """

    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        nullable=False,
        default=secrets.token_hex,
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    user_agent: Mapped[str] = mapped_column(String)
    latest_address: Mapped[str] = mapped_column(String)
    created: Mapped[datetime] = mapped_column(
        DATETIME,
        default=datetime.now,
        nullable=False,
    )
    latest_action: Mapped[datetime] = mapped_column(
        DATETIME,
        default=datetime.now,
        onupdate=datetime.now,
        nullable=False,
    )

    user: Mapped[User] = relationship(lazy="joined", back_populates="sessions")  # noqa: F821

    @classmethod
    async def from_id(cls, db_session: ormSession, session_id: int) -> Session | None:
        return await db_session.scalar(select(Session).filter(Session.id == session_id))

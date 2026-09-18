from __future__ import annotations

import logging
from datetime import datetime

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import Integer, String, delete, select
from sqlalchemy.dialects.sqlite import DATETIME, JSON
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import Session as ormSession
from werkzeug.datastructures import MultiDict  # noqa: TC002

from radiotomate.db import Base

_log = logging.getLogger(__name__)

PERMISSION_NAMES = ("admin", "stream", "live", "carts", "not_audio", "autodj")


class User(Base):
    """
    User info and permissions
    """

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str] = mapped_column(String, nullable=False, default="")
    created: Mapped[datetime] = mapped_column(DATETIME, default=datetime.now)
    modified: Mapped[datetime] = mapped_column(
        DATETIME,
        default=datetime.now,
        onupdate=datetime.now,
    )
    permissions: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON),
        default={},
    )  # https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#sqlalchemy.dialects.sqlite.JSON

    sessions: Mapped[list[Session]] = relationship(  # noqa: F821
        lazy=True,
        back_populates="user",
        cascade="save-update, merge, expunge, delete",
    )

    @classmethod
    async def all(cls, session: ormSession) -> list[User]:
        return await session.scalars(select(User).order_by(User.username))

    @classmethod
    async def from_username(cls, session: ormSession, username: str) -> User | None:
        return await session.scalar(select(User).filter(User.username == username))

    @classmethod
    async def from_id(cls, session: ormSession, user_id: int) -> User | None:
        return await session.scalar(select(User).filter(User.id == user_id))

    @classmethod
    async def check(
        cls,
        session: ormSession,
        username: str,
        password: str,
    ) -> User | None:
        user = await cls.from_username(session, username)
        if user:
            ph = PasswordHasher()
            try:
                if ph.verify(user.password, password):
                    if ph.check_needs_rehash(user.password):
                        user.password = ph.hash(password)
                        await session.flush()
                    return user
            except VerifyMismatchError:
                return None
            except Exception as e:
                _log.exception(e)
        return None

    @classmethod
    async def delete(cls, session: ormSession, user_id: int):
        await session.execute(delete(User).where(User.id == user_id))

    def update_password(self, new_password):
        ph = PasswordHasher()
        self.password = ph.hash(new_password)

    def update_permissions(self, form: MultiDict):
        permissions = {}
        for perm in PERMISSION_NAMES:
            if form.get(f"can_{perm}") == "true":
                permissions[perm] = True
        self.permissions = permissions

    def update_permissions_map(self, perms: dict | None):
        permissions = {}
        raw = perms or {}
        for perm in PERMISSION_NAMES:
            value = raw.get(perm, raw.get(f"can_{perm}"))
            if value in (True, "true", "1", 1):
                permissions[perm] = True
        self.permissions = permissions

    def permissions_payload(self) -> dict[str, bool]:
        return {perm: bool(self.permissions.get(perm)) for perm in PERMISSION_NAMES}

    def can_admin(self) -> bool:
        return self.permissions.get("admin", False)

    def can_stream(self) -> bool:
        return self.can_admin() or self.permissions.get("stream", False)

    def can_live(self) -> bool:
        return self.can_admin() or self.permissions.get("live", False)

    def can_carts(self) -> bool:
        return self.can_admin() or self.permissions.get("carts", False)

    def can_not_audio(self) -> bool:
        return self.permissions.get("not_audio", False)

    def can_autodj(self) -> bool:
        return self.can_admin() or self.permissions.get("autodj", False)


class LoggedOutUser:
    """
    This object fills current_user.user when the user is not authenticated.
    This simplifies authorization checks by providing all ``can_*()`` methods
    """

    def __init__(self):
        self.id = None
        self.username = None
        self.permissions = {}

    def can_admin(self) -> bool:
        return False

    def can_stream(self) -> bool:
        return False

    def can_live(self) -> bool:
        return False

    def can_carts(self) -> bool:
        return False

    def can_not_audio(self) -> bool:
        return False

    def can_autodj(self) -> bool:
        return False

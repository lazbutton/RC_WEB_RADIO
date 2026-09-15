from __future__ import annotations

import logging

from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from radiotomate.db import Base

_log = logging.getLogger(__name__)


class Setting(Base):
    """
    In-dabatase settings.

    Those settings can be edited from the web interface by
    admins. Any setting that would not require a webapp restart can be stored
    here, otherwise it should be in the YAML file. However, they will be loaded
    in the same dict as those in the YAML file, and they might override them!

    Also note that sub-dicts are created when the key contains a dot.
    """

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)

    @classmethod
    async def from_key(cls, session: Session, key: str) -> Setting | None:
        return await session.scalar(select(Setting).filter(Setting.key == key))

    @classmethod
    async def load_all(cls, session: Session) -> dict:
        settings = {}
        rows = await session.scalars(select(Setting))
        for setting in rows:
            settings[setting.key] = setting.value
        return settings

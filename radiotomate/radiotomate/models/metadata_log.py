from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, select
from sqlalchemy.dialects.sqlite import DATETIME, JSON
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import Session as ormSession

from radiotomate.db import Base
from radiotomate.models.sound import Sound

_log = logging.getLogger(__name__)


class MetadataLog(Base):
    """
    Metadata saved from the playout process: this is what actually got broadcasted.
    """

    __tablename__ = "metadata_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    on_air: Mapped[datetime] = mapped_column(DATETIME, default=datetime.now, index=True)
    source: Mapped[str] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(String)
    cart_id: Mapped[int] = mapped_column(Integer, ForeignKey("carts.id"))
    artist: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    album: Mapped[str] = mapped_column(String)
    extra: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON),
        default={},
        nullable=False,
    )  # https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#sqlalchemy.dialects.sqlite.JSON

    cart: Mapped[Cart] = relationship()  # noqa: F821

    @classmethod
    async def from_playout(cls, session: ormSession, md: dict) -> MetadataLog:  # noqa: PLR0912
        """
        Transform the JSON metadata from the playout process into a MetadataLog object.
        """
        parsed = MetadataLog()
        if "on_air" in md:
            if md["on_air"]:
                parsed.on_air = datetime.fromisoformat(md["on_air"])
            del md["on_air"]
        if "radiotomate_sound_id" in md:
            if md["radiotomate_sound_id"]:
                sound = await Sound.from_id(session, md["radiotomate_sound_id"])
                if sound:
                    sound.last_played = parsed.on_air
                    parsed.cart = sound.cart
            del md["radiotomate_sound_id"]
        if "source" in md:
            if md["source"]:
                parsed.source = md["source"]
            del md["source"]
        if "source_url" in md:
            if md["source_url"]:
                parsed.source_url = md["source_url"]
            del md["source_url"]
        if "initial_uri" in md:
            if md["initial_uri"]:
                parsed.source_url = md["initial_uri"]
            del md["initial_uri"]
        if "artist" in md:
            if md["artist"]:
                parsed.artist = md["artist"]
            del md["artist"]
        if "title" in md:
            if md["title"]:
                parsed.title = md["title"]
            del md["title"]
        if "album" in md:
            if md["album"]:
                parsed.album = md["album"]
            del md["album"]
        parsed.extra = md
        return parsed

    @classmethod
    async def get(
        cls,
        session: ormSession,
        limit: int | None = None,
    ) -> list[MetadataLog]:
        """
        Returns a list of MetadataLog objects, ordered by `on_air` DESC.
        """
        q = select(MetadataLog)
        if limit is not None:
            q = q.limit(limit)
        q = q.order_by(MetadataLog.on_air.desc())
        return list(await session.scalars(q))

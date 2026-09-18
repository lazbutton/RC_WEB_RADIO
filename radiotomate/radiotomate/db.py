from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

from quart import Quart, current_app, g, request, request_tearing_down
from sqlalchemy import Dialect, String, event
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    async_engine_from_config,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase, registry
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.types import TypeDecorator

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import DBAPICursor


class PathLike(TypeDecorator):
    """
    Allows to map columns as `Path` objects containing absolute paths, while
    storing strings relative to `base_path`.

    This reduces the DB size and let the user move the `DATA_ROOT` folder, they
    should onyl update the configuration. However applications must copy
    `base_path` from their configuration when starting.

    Paths under ``MEDIA_ROOT`` (banque Nasgul, hors ``DATA_ROOT``) are stored
    with a ``media:`` prefix so carts can reference shared files in place.
    """

    impl = String
    base_path: Path = None
    MEDIA_PREFIX = "media:"

    @staticmethod
    def media_root() -> Path:
        return Path(os.environ.get("MEDIA_ROOT", "/media")).resolve()

    def process_bind_param(self, value: Path | None, dialect: Dialect) -> str:
        """Convert an `Path` value to a string for the database."""
        if value:
            if not self.base_path:
                raise RuntimeError("Application factory must set PathLike.base_path")
            resolved = Path(value).resolve()
            try:
                return str(resolved.relative_to(self.base_path.resolve()))
            except ValueError:
                try:
                    rel = resolved.relative_to(self.media_root()).as_posix()
                except ValueError as exc:
                    raise ValueError(
                        f"{resolved} is outside DATA_ROOT and MEDIA_ROOT",
                    ) from exc
                return f"{self.MEDIA_PREFIX}{rel}"
        return value

    def process_result_value(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> Path | None:
        """Restore a string from the database to a `Path`."""
        if value is not None:
            if not self.base_path:
                raise RuntimeError("Application factory must set PathLike.base_path")
            if value.startswith(self.MEDIA_PREFIX):
                return self.media_root() / value[len(self.MEDIA_PREFIX) :]
            return self.base_path / value
        return value


class Base(AsyncAttrs, DeclarativeBase):
    registry = registry(
        type_annotation_map={
            Path: PathLike,
        },
    )


class QuartAlchemy:
    """
    This expect an app whose config contain a `ALCHEMY_ENGINE_CONFIG`
    key with a SQLAlchemy engine configuration: a dict that should
    contain at least a `url` key. For other options see SQLAlchemy's
    `async_engine_from_config`.
    The config may also contain a `ALCHEMY_SESSION_CONFIG`, associated
    to a dict matching `async_sessionmaker`'s parameters (we'll set
    `expire_on_commit=False` by default).

    Activate the extension with
    ```
    db = QuartAlchemy(app)
    ```

    Then, you'll have a per-request session object in the `g` object.
    Don't forget to `commit()`:
    ```
    g.dbsession.add(user)
    await g.dbsession.commit()
    ```

    This session is not open when serving from the `static` route.

    Outside of requests contexts, use its `session` attribute to get an
    `AsyncSession` object. For example:

    ```
    async with db.session() as session:
        session.add(SomeObject(data="object"))
        session.add(SomeOtherObject(name="other object"))
        await session.commit()
    ```

    The context manager can also handle the transaction with:

    ```
    async with db.session.begin() as session:
        session.add(SomeObject(data="object"))
    ```

    You may also access the engine directly, typically to create tables:

    ```
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    ```

    As we're using async drivers, even when running in a synchronous context
    you'll need to wrap such code in a an asynchronous function:

    ```
    async def create_all(engine):
        async with engine.connect() as conn:
            conn.run_sync(Base.metadata.create_all)
    asyncio.run(create_all(db.engine))
    ```

    """

    EXTENSION_NAME = "sqlalchemy"

    def __init__(self, app: Quart | None = None):
        self.engine = None
        self.session = None
        self.engine_config = None
        self.session_config = None
        self.in_memory = False
        if app is not None:
            self.init_app(app)

    @classmethod
    def get(cls) -> QuartAlchemy:
        """
        Find the instance wrapped in ``current_app``
        """
        return current_app.extensions[cls.EXTENSION_NAME]

    def init_app(self, app: Quart) -> None:
        if self.EXTENSION_NAME in app.extensions:
            raise RuntimeError(
                "An sqlalchemy extension has already been registered on this app",
            )
        app.extensions[self.EXTENSION_NAME] = self

        self.init_from_config(
            app.config.get("ALCHEMY_ENGINE_CONFIG"),
            app.config.get("ALCHEMY_SESSION_CONFIG", {}),
        )
        app.after_serving(self.after_serving)
        app.before_request(self.before_request)
        request_tearing_down.connect(self.teardown_request, app)

    @staticmethod
    def sqlite_pragmas(cursor: DBAPICursor) -> None:
        """
        This ensures we use the same PRAGMA statement in all contexts:
         * when initilizing SQLAlchemy
         * when testing

        This list is inspired from https://gcollazo.com/optimal-sqlite-settings-for-django/
        and linked articles
        """
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")  # 5s
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA mmap_size=134217728")  # 128MB
        cursor.execute("PRAGMA cache_size=10000")  # 10k pages

    def init_from_config(self, engine_config: dict, session_config: dict):
        """
        This really starts the SQLAlchemy engine and ensures that ``self.session()``
        will return something useful later on.
        When not binding to a Quart app, calling this method is enough.
        """
        self.engine_config = {
            "pool_size": 10,
            "max_overflow": 10,
            "pool_recycle": 3600,
            "poolclass": AsyncAdaptedQueuePool,
        }
        self.engine_config.update(engine_config)
        self.engine = async_engine_from_config(self.engine_config, prefix="")

        @event.listens_for(self.engine.sync_engine, "connect")
        def do_connect(dbapi_connection, connection_record):
            # cf. https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#enabling-non-legacy-sqlite-transactional-modes-with-the-sqlite3-or-aiosqlite-driver
            # we should put "connect_args": {"autocommit": False}, in engine_config,
            # but that would trigger errors in sqlite_pragmas like
            # "cannot change into wal mode from within a transaction"
            # so we're left with the legacy mode:
            dbapi_connection.isolation_level = None
            # this can't be changed until AsyncAdapt_aiosqlite_connection has no
            # `autocommit` attribute - if that ever happens, we could apply the
            # documented trick that temporarly sets autocommit while calling
            # sqlite_pragmas

            cursor = dbapi_connection.cursor()
            self.sqlite_pragmas(cursor)
            cursor.close()

        self.session_config = {
            "expire_on_commit": False,
            "bind": self.engine,
        }
        self.session_config.update(session_config)
        self.session = async_sessionmaker(**self.session_config)

    async def before_request(self) -> None:
        if request.endpoint != "static":
            g.dbsession = self.session()

    async def teardown_request(self, sender, **extra) -> None:
        # extra may include "exc" if an exception occurred

        # AttributeError happens when serving the static endpoint
        with contextlib.suppress(AttributeError):
            await g.dbsession.close()

    async def after_serving(self) -> None:
        if self.engine:
            await self.engine.dispose()

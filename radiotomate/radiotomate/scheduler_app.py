"""
The scheduler's ASGI app factory
"""

import logging
from asyncio import CancelledError
from inspect import isawaitable
from pathlib import Path
from typing import Any, Callable

from apscheduler import AsyncScheduler, Job
from apscheduler.datastores.sqlalchemy import SQLAlchemyDataStore
from apscheduler.eventbrokers.local import LocalEventBroker
from apscheduler.executors.async_ import AsyncJobExecutor
from httpx import AsyncClient
from quart_auth import QuartAuth
from werkzeug.exceptions import HTTPException

import radiotomate.models
from radiotomate.auth import RadiotomateAuth
from radiotomate.beets import BeetsIntegration
from radiotomate.db import PathLike, QuartAlchemy
from radiotomate.quart import CustomQuart, ShutdownError
from radiotomate.scheduler import (
    analyzer,
    harbor,
    live,
    metadata_log,
    schedule,
    version,
)

_log = logging.getLogger(__name__)


class APSChedulerMiddleware:
    def __init__(self, asgi_app, scheduler):
        self.asgi_app = asgi_app
        self.scheduler = scheduler

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            try:
                async with self.scheduler:
                    await self.scheduler.start_in_background()
                    return await self.asgi_app(scope, receive, send)
            except (ShutdownError, CancelledError):
                pass
        else:
            return await self.asgi_app(scope, receive, send)


class RadiotomateAPSChedulerDataStore(SQLAlchemyDataStore):
    def get_table_definitions(self):
        upstream_metadata = super().get_table_definitions()
        for table in upstream_metadata.tables.values():
            table.name = "apscheduler_" + table.name
        return upstream_metadata


class RadiotomateJobExecutor(AsyncJobExecutor):
    """
    like AsyncJobExecutor, but provides a DB session as the first argument and
    the current app as the second argument.
    """

    def __init__(self, app: CustomQuart, db: QuartAlchemy):
        self.app = app
        self.db = db

    async def run_job(self, func: Callable[..., Any], job: Job) -> Any:
        async with self.db.session() as session:
            retval = func(session, self.app, *job.args, **job.kwargs)
            if isawaitable(retval):
                retval = await retval
            return retval


def errors_as_text(exc: Exception) -> str:
    if not isinstance(exc, HTTPException):
        exc.code = 500
    return (str(exc), exc.code)


def app_factory(config: dict, beets: BeetsIntegration) -> CustomQuart:
    app = CustomQuart(__name__)
    app.config.from_mapping(
        {
            "ALCHEMY_ENGINE_CONFIG": config["db"],
            "DATA_ROOT": Path(config["data"]["root"]),
            "PLAYOUT_TOKEN": config["playout_process_config"]["token"],
            "PLAYOUT_CLIENT": AsyncClient(
                base_url="http://127.0.0.1:6833",
                headers={
                    "X-Auth-Token": config["playout_process_config"]["token"],
                },
            ),
            "RELAY_METADATA_TO": config.get("metadata_log", {}).get("relay_to", []),
        },
    )
    app.register_error_handler(Exception, errors_as_text)

    app.register_blueprint(analyzer.blueprint)
    app.register_blueprint(harbor.blueprint)
    app.register_blueprint(live.blueprint)
    app.register_blueprint(metadata_log.blueprint)
    app.register_blueprint(version.blueprint)
    app.register_blueprint(schedule.blueprint)

    PathLike.base_path = app.config["DATA_ROOT"]
    db = QuartAlchemy(app)

    beets.init_app(app)

    @app.before_serving
    async def load_settings():
        async with db.session() as session:
            dbsettings = await radiotomate.models.Setting.load_all(session)
            app.config.from_mapping(dbsettings)
        app.add_background_task(beets.background_analyzer, db)

    auth_manager = QuartAuth()
    auth_manager.user_class = RadiotomateAuth
    auth_manager.init_app(app)

    app.scheduler = AsyncScheduler(
        data_store=RadiotomateAPSChedulerDataStore(db.engine),
        event_broker=LocalEventBroker(),
        job_executors={"async": RadiotomateJobExecutor(app, db)},
        max_concurrent_jobs=1,
    )
    app.asgi_app = APSChedulerMiddleware(app.asgi_app, app.scheduler)

    return app

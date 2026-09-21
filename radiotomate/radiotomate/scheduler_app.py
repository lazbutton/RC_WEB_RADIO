"""
The scheduler's ASGI app factory
"""

import logging
from pathlib import Path

from httpx import AsyncClient
from quart_auth import QuartAuth
from werkzeug.exceptions import HTTPException

import radiotomate.models
from radiotomate.auth import RadiotomateAuth
from radiotomate.beets import BeetsIntegration
from radiotomate.db import PathLike, QuartAlchemy
from radiotomate.quart import CustomQuart
from radiotomate.scheduler import (
    alerts,
    analyzer,
    bank_sync,
    harbor,
    health,
    live,
    metadata_log,
    metrics,
    queue_cleaner,
    retention,
    schedule,
    version,
)
from radiotomate.scheduler.timers import Timers

_log = logging.getLogger(__name__)


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
            "RELAY_METADATA_RETRY": config.get("metadata_log", {}).get(
                "relay_retry",
                {},
            ),
            "HEALTH_HEARTBEAT_MAX_AGE": config.get("health", {}).get(
                "heartbeat_max_age_seconds",
                5,
            ),
            "QUEUE_CLEAN_ENABLED": bool(
                config.get("queue_cleaner", {}).get("enabled", True)
            ),
            "QUEUE_CLEAN_MAX_AGE": config.get("queue_cleaner", {}).get(
                "max_age_seconds",
                600,
            ),
            "QUEUE_CLEAN_INTERVAL": config.get("queue_cleaner", {}).get(
                "interval_seconds",
                60,
            ),
            "RETENTION_ENABLED": bool(config.get("retention", {}).get("enabled", True)),
            "RETENTION_INTERVAL": config.get("retention", {}).get(
                "interval_seconds", 3600
            ),
            "RETENTION_RUNDOWN_DAYS": config.get("retention", {}).get(
                "rundown_days", 7
            ),
            "RETENTION_COMMANDS_HOURS": config.get("retention", {}).get(
                "commands_hours", 24
            ),
            "RETENTION_METADATA_DAYS": config.get("retention", {}).get(
                "metadata_days", 90
            ),
            "METRICS_SNAPSHOT_INTERVAL": config.get("metrics", {}).get(
                "snapshot_interval_seconds", 30
            ),
            "ALERTS": config.get("alerts", {}) or {},
        },
    )
    app.register_error_handler(Exception, errors_as_text)

    app.register_blueprint(health.blueprint)
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
        if app.config.get("QUEUE_CLEAN_ENABLED", True):
            app.add_background_task(queue_cleaner.loop, app)
        app.add_background_task(bank_sync.loop, app)
        if app.config.get("RETENTION_ENABLED", True):
            app.add_background_task(retention.loop, app)
        app.add_background_task(metrics.snapshot_loop, app)
        app.add_background_task(alerts.loop, app)

    auth_manager = QuartAuth()
    auth_manager.user_class = RadiotomateAuth
    auth_manager.init_app(app)

    # One-shot timers (max_duration skips). Recurring carts are the clock's.
    app.timers = Timers()

    return app

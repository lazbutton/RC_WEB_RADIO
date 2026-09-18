"""Unauthenticated liveness, readiness and JSON metrics for the scheduler."""

from __future__ import annotations

from quart import Blueprint, current_app, jsonify

from radiotomate.health import database_is_ready
from radiotomate.scheduler.metrics import runtime_metrics
from radiotomate.scheduler.watchdog import Watchdog

blueprint = Blueprint("health", __name__)

DEFAULT_HEARTBEAT_MAX_AGE = 5.0


def _heartbeat_max_age() -> float:
    raw = current_app.config.get(
        "HEALTH_HEARTBEAT_MAX_AGE",
        DEFAULT_HEARTBEAT_MAX_AGE,
    )
    try:
        return max(1.0, min(60.0, float(raw)))
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_MAX_AGE


def _playout_connected() -> bool:
    if runtime_metrics.playout_connected:
        return True
    return Watchdog.liquidsoap_version not in {"", "disconnected"}


@blueprint.get("/health/live")
async def live():
    return jsonify({"status": "live"})


@blueprint.get("/health/ready")
async def ready():
    db_ok = await database_is_ready()
    max_age = _heartbeat_max_age()
    heartbeat_age = runtime_metrics.heartbeat_age()
    heartbeat_ok = heartbeat_age is not None and heartbeat_age <= max_age
    playout_ok = _playout_connected()
    ready_ok = db_ok and heartbeat_ok and playout_ok
    payload = {
        "ready": ready_ok,
        "db": db_ok,
        "playout": playout_ok,
        "heartbeat_ok": heartbeat_ok,
        "heartbeat_age_seconds": heartbeat_age,
    }
    return jsonify(payload), 200 if ready_ok else 503


@blueprint.get("/metrics.json")
async def metrics():
    return jsonify(runtime_metrics.snapshot())

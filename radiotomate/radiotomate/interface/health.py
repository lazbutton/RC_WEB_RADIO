"""Unauthenticated liveness and readiness for the interface process."""

from quart import Blueprint, jsonify

from radiotomate.health import database_is_ready

blueprint = Blueprint("health", __name__)


@blueprint.get("/health/live")
async def live():
    return jsonify({"status": "live"})


@blueprint.get("/health/ready")
async def ready():
    db_ok = await database_is_ready()
    payload = {"ready": db_ok, "db": db_ok}
    return jsonify(payload), 200 if db_ok else 503

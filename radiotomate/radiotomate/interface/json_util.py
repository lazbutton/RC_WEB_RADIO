"""Helpers for cookie-session JSON admin routes."""

from quart import jsonify, request
from werkzeug.exceptions import BadRequest

from radiotomate.auth import current_user


def forbidden_unless(permission: str):
    user = getattr(current_user, "user", None)
    checker = getattr(user, "can_" + permission, None) if user is not None else None
    if checker is None or not checker():
        return jsonify({"error": "forbidden"}), 403
    return None


async def read_json_object() -> dict:
    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequest("JSON object required")
    return data

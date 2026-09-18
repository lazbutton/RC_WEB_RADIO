"""CSRF protection for cookie-authenticated interface mutations."""

from __future__ import annotations

import hashlib
import hmac

from quart import Blueprint, current_app, jsonify, request
from quart_auth import current_user
from werkzeug.exceptions import Forbidden

from radiotomate.auth import login_required

blueprint = Blueprint("csrf", __name__)

HEADER_NAME = "X-CSRF-Token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT_ENDPOINTS = {"login.log_in", "login.log_in_json"}


def csrf_token() -> str:
    auth_id = str(getattr(current_user, "auth_id", "") or "")
    if not auth_id:
        return ""
    secret = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(secret, auth_id.encode(), hashlib.sha256).hexdigest()


async def protect_csrf() -> None:
    if not current_app.config.get("CSRF_ENABLED", True):
        return
    if request.method not in UNSAFE_METHODS:
        return
    if request.endpoint in EXEMPT_ENDPOINTS:
        return
    if not await current_user.is_authenticated:
        return
    supplied = request.headers.get(HEADER_NAME, "")
    expected = csrf_token()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise Forbidden("csrf_invalid")


@blueprint.get("/csrf.json")
@login_required
async def get_csrf():
    return jsonify({"csrf_token": csrf_token()})

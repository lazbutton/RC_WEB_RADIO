"""
The interface's ASGI app factory
"""

import json
import logging
from pathlib import Path
from traceback import extract_tb

from quart import current_app, jsonify, request
from quart_auth import QuartAuth, Unauthorized, current_user
from werkzeug.exceptions import HTTPException

import radiotomate.models
from radiotomate.auth import RadiotomateAuth, redirect_to_login
from radiotomate.beets import BeetsIntegration
from radiotomate.db import PathLike, QuartAlchemy
from radiotomate.domain import DomainError
from radiotomate.interface import (
    account,
    autodj,
    carts,
    csrf,
    health,
    home,
    library,
    live,
    login,
    spa,
    users,
)
from radiotomate.quart import CustomQuart
from radiotomate.templates import RADIOTOMATE_HELPERS

_log = logging.getLogger(__name__)


async def htmx_errors_as_messages(exc: Exception):
    if isinstance(exc, DomainError):
        if request.path.endswith(".json"):
            payload = {
                "error": exc.message,
                "code": exc.code,
                "message": exc.message,
            }
            if exc.field:
                payload["field"] = exc.field
            return jsonify(payload), exc.status_code
        exc.code = exc.status_code
        exc.description = exc.message
    if request.path.endswith(".json"):
        if isinstance(exc, HTTPException):
            return jsonify({"error": exc.description or exc.name}), exc.code
        _log.exception("JSON admin error")
        return jsonify({"error": "server_error"}), 500
    if isinstance(exc, (HTTPException, DomainError)):
        radiotomate_message = {
            "text": exc.description,
            "icon": "warning",
        }
    else:
        # At this point we're out of the exception handling context, so the usual
        # log.exception() won't show the stack.
        message = (
            "".join(extract_tb(exc.__traceback__).format())
            + f"{exc.__class__.__name__}: {exc}"
        )
        _log.error(message)
        if not current_app.debug:
            message = "Please check the logs for more information"
        radiotomate_message = {
            "title": "Unhandled exception",
            "text": message,
            "icon": "error",
        }
        exc.code = 500

    if request.headers.get("HX-Request") == "true":
        headers = {
            "HX-Trigger": json.dumps({"radiotomateMessage": radiotomate_message}),
        }
        return (radiotomate_message["text"], exc.code, headers)
    else:
        return (f"<pre>{radiotomate_message['text']}</pre>", exc.code)


def app_factory(config: dict, reload: bool, beets: BeetsIntegration):
    app = CustomQuart(__name__)
    app.config.from_mapping(
        {
            "SECRET_KEY": config["interface"]["cookie_salt"],
            "TEMPLATES_AUTO_RELOAD": reload,
            "ALCHEMY_ENGINE_CONFIG": config["db"],
            "QUART_AUTH_COOKIE_NAME": "session",
            "QUART_AUTH_COOKIE_SAMESITE": "Strict",
            "QUART_AUTH_COOKIE_SECURE": bool(
                config.get("interface", {}).get("cookie_secure", False)
            ),
            "QUART_AUTH_SALT": config["interface"]["cookie_salt"],
            "CSRF_ENABLED": bool(
                config.get("interface", {}).get("csrf_enabled", True)
            ),
            "MAX_CONTENT_LENGTH": 10 * 1024 * 1024 * 1024,  # 10GB,
            "DATA_ROOT": Path(config["data"]["root"]),
            "PLAYOUT_TOKEN": config["playout_process_config"]["token"],
            "CONSOLE_DIST": config.get("interface", {}).get("console_dist") or None,
        },
    )

    app.register_blueprint(health.blueprint)
    app.register_blueprint(home.blueprint)
    app.register_blueprint(users.blueprint)
    app.register_blueprint(account.blueprint)
    app.register_blueprint(login.blueprint)
    app.register_blueprint(carts.blueprint)
    app.register_blueprint(live.blueprint)
    app.register_blueprint(autodj.blueprint)
    app.register_blueprint(library.blueprint)
    app.register_blueprint(csrf.blueprint)
    spa.register(app)

    app.jinja_env.globals.update(
        **RADIOTOMATE_HELPERS,
        csrf_token=csrf.csrf_token,
    )
    app.register_error_handler(Exception, htmx_errors_as_messages)
    app.register_error_handler(Unauthorized, redirect_to_login)

    PathLike.base_path = app.config["DATA_ROOT"]
    db = QuartAlchemy(app)

    beets.init_app(app)

    @app.before_serving
    async def on_start():
        # load settings
        async with db.session() as session:
            dbsettings = await radiotomate.models.Setting.load_all(session)
            app.config.from_mapping(dbsettings)

        # start the live data watch
        app.add_background_task(live.watch_livedata_task)

    auth_manager = QuartAuth()
    auth_manager.user_class = RadiotomateAuth
    auth_manager.init_app(app)

    @app.before_request
    @app.before_websocket
    async def preload_user():
        """
        Preloads a complete Session and User object.

        Except when serving from the static endpoint, because it does not open
        a DB session.
        """
        if request.endpoint in {"static", "health.live", "health.ready"}:
            return
        read_only = request.method in ("GET", "HEAD", "OPTIONS")
        await current_user.preload_attributes(update_latest_action=not read_only)

    app.before_request(csrf.protect_csrf)

    return app

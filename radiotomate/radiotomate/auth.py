"""
This module contains our extensions to Quart-Auth
"""

from collections.abc import Awaitable
from datetime import datetime
from functools import wraps
from typing import Callable, ParamSpec, TypeVar

from quart import current_app, g, redirect, request, url_for
from quart_auth import (
    AuthUser,
    Unauthorized,
    current_user,
    login_required,  # noqa: F401 so modules can import login_required from here
    logout_user,
)

from radiotomate.models import LoggedOutUser, Session, User

T = TypeVar("T")
P = ParamSpec("P")


async def redirect_to_login(*_):
    return redirect(url_for("login.log_in"))


class RadiotomateAuth(AuthUser):
    """
    This preloads two attributes to `current_user`:
    * user: the user object
    * session: the session object
    it also updates the session object, to track users' activity.

    Note that it requires a before_request hook
    """

    def __init__(self, session_id):
        super().__init__(session_id)
        self.user: User = None
        self.session: Session = None

    async def preload_attributes(self, update_latest_action=True):
        if self.auth_id:
            self.session = await Session.from_id(g.dbsession, self.auth_id)
            if self.session:
                if not self.session.active:
                    logout_user()
                    raise Unauthorized("This session has been closed")
                self.user = self.session.user
                if self.session.latest_address != request.remote_addr:
                    self.session.latest_address = request.remote_addr
                if update_latest_action:
                    self.session.latest_action = datetime.now()
                await g.dbsession.commit()
            else:
                logout_user()
                raise Unauthorized("Invalid session")
        else:
            self.session = None
            self.user = LoggedOutUser()


def permission_required(permission: str) -> Callable[P, Awaitable[T]]:
    """
    Checks current users' permissions. This is a more specialized variant of
    QuartAuth's ``@login_required``.
    """

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            if not await current_user.is_authenticated:
                raise Unauthorized()
            else:
                checker = getattr(current_user.user, "can_" + permission)
                if not checker():
                    raise Unauthorized()
                else:
                    return await current_app.ensure_async(func)(*args, **kwargs)

        return wrapper

    return decorator


def token_required(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """A decorator to restrict route access between radiotomate's processes.

    This should be used to wrap a view function to ensure the request contains
    the correct token in the X-Auth-Token header.
    The value is configured in playout_process_config.token in the config file.

    Otherwise raise `quart.exceptions.Unauthorized`.
    """

    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        if (
            not request.headers.get("X-Auth-Token")
            == current_app.config["PLAYOUT_TOKEN"]
        ):
            raise Unauthorized()
        else:
            return await current_app.ensure_async(func)(*args, **kwargs)

    return wrapper

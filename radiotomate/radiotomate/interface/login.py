import asyncio
import logging

from quart import Blueprint, g, redirect, render_template, request, url_for
from quart_auth import current_user, login_user, logout_user
from werkzeug.exceptions import BadRequest, Unauthorized

from radiotomate.auth import RadiotomateAuth
from radiotomate.models import Session, User

_log = logging.getLogger(__name__)

blueprint = Blueprint("login", __name__, template_folder="templates")


@blueprint.get("/login")
async def index():
    return await render_template("login/index.jinja")


@blueprint.post("/login")
async def log_in():
    form = await request.form
    username = form.get("username")
    password = form.get("password")

    if not username:
        raise BadRequest("Please provide an username")
    if not password:
        raise BadRequest("Please provide a password")

    user = await User.check(g.dbsession, username, password)
    if not user:
        await asyncio.sleep(1)  # good luck, bruteforcers !
        raise Unauthorized("Incorrect username or password")
    session = Session(
        user_id=user.id,
        user_agent=request.headers.get("User-Agent"),
        latest_address=request.remote_addr,
    )
    g.dbsession.add(session)
    await g.dbsession.commit()

    login_user(RadiotomateAuth(session.id))
    return (
        "",
        302,
        {
            "HX-Redirect": url_for("home.index"),
        },
    )


@blueprint.route("/logout")
async def log_out():
    current_user.session.active = False
    await g.dbsession.commit()
    logout_user()
    return redirect(url_for("login.log_in"))

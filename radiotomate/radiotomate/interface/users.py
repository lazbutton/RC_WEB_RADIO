import logging

from quart import Blueprint, g, render_template, request, url_for
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from radiotomate.auth import permission_required
from radiotomate.models import User

_log = logging.getLogger(__name__)

blueprint = Blueprint("users", __name__, template_folder="templates")


@blueprint.get("/users")
@permission_required("admin")
async def index():
    users = await User.all(g.dbsession)
    return await render_template("users/index.jinja", users=users)


@blueprint.get("/users/add")
@permission_required("admin")
async def add_form():
    return await render_template("users/user_form.jinja")


@blueprint.post("/users")
@permission_required("admin")
async def add():
    form = await request.form
    username = form.get("username")
    if username:
        username = username.strip()

    if not username:
        raise BadRequest("Please provide an username")
    if not form.get("password"):
        raise BadRequest("Please provide a password")

    if await User.from_username(g.dbsession, username):
        raise Conflict(f"User {username} already exists")

    notes = form.get("notes")
    user = User(username=username, notes=notes)
    user.update_password(form["password"])
    user.update_permissions(form)

    g.dbsession.add(user)
    await g.dbsession.commit()
    return (
        "",
        200,
        {
            "HX-Redirect": url_for("users.index"),
        },
    )


@blueprint.get("/users/<int:user_id>")
@permission_required("admin")
async def get(user_id: int):
    user = await User.from_id(g.dbsession, user_id)
    if not user:
        raise NotFound(f"User {user_id} not found")
    return await render_template("users/user_form.jinja", user=user)


@blueprint.put("/users/<int:user_id>")
@permission_required("admin")
async def edit(user_id: int):
    form = await request.form
    user = await User.from_id(g.dbsession, user_id)
    if not user:
        raise NotFound(f"User {user_id} not found")

    username = form.get("username")
    if username:
        username = username.strip()
    if not username:
        raise BadRequest("Please provide an username")
    if username != user.username and await User.from_username(g.dbsession, username):
        raise Conflict(f"User {username} already exists")
    user.username = username

    user.notes = form.get("notes")
    if form.get("password"):
        user.update_password(form["password"])

    user.update_permissions(form)

    await g.dbsession.commit()
    return (
        "",
        200,
        {
            "HX-Redirect": url_for("users.index"),
        },
    )


@blueprint.delete("/users/<int:user_id>")
@permission_required("admin")
async def delete(user_id: int):
    user = await User.from_id(g.dbsession, user_id)
    if not user:
        raise NotFound(f"User {user_id} not found")
    await g.dbsession.delete(user)
    await g.dbsession.commit()
    return ""

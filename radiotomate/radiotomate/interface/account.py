from quart import Blueprint, g, render_template, request, url_for
from werkzeug.exceptions import BadRequest

from radiotomate.auth import current_user, login_required

blueprint = Blueprint("account", __name__, template_folder="templates")


@blueprint.get("/account")
@login_required
async def index():
    return await render_template("account/index.jinja")


@blueprint.put("/account")
@login_required
async def update():
    form = await request.form
    password = form.get("password") or ""
    if not password.strip():
        raise BadRequest("Veuillez saisir un mot de passe")
    current_user.user.update_password(password)
    await g.dbsession.commit()
    return (
        "",
        200,
        {
            "HX-Redirect": url_for("account.index"),
        },
    )

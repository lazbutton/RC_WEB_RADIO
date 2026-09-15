import logging

from quart import Blueprint, render_template

from radiotomate.auth import login_required

_log = logging.getLogger(__name__)

blueprint = Blueprint("home", __name__, template_folder="templates")
watching_clients = set()


@blueprint.get("/")
@login_required
async def index():
    return await render_template("home/index.jinja")

import logging

from quart import Blueprint, render_template, send_from_directory
from quart_auth import Unauthorized, current_user

from radiotomate.interface.spa import console_dist

_log = logging.getLogger(__name__)

blueprint = Blueprint("home", __name__, template_folder="templates")
watching_clients = set()


@blueprint.get("/")
async def index():
    dist = console_dist()
    if dist is not None:
        return await send_from_directory(dist, "index.html")
    if not await current_user.is_authenticated:
        raise Unauthorized()
    return await render_template("home/index.jinja")

import logging

from quart import Blueprint, send_file
from werkzeug.exceptions import NotFound

from radiotomate.auth import login_required
from radiotomate.beets import BeetsIntegration

_log = logging.getLogger(__name__)

blueprint = Blueprint("library", __name__, template_folder="templates")


@blueprint.get("/library/download/<int:item_id>")
@login_required
async def download(item_id: int):
    beets = BeetsIntegration.get()
    item = beets.lib.get_item(item_id)
    if item is None:
        raise NotFound()
    return await send_file(
        item.filepath,
        mimetype="audio/mpeg",
        conditional=True,
        as_attachment=True,
        attachment_filename=item.filepath.name,
    )

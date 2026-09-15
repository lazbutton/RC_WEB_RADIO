import logging

import httpx
from quart import Blueprint, current_app, g, request

from radiotomate.auth import token_required
from radiotomate.models import MetadataLog

_log = logging.getLogger(__name__)

blueprint = Blueprint("metadata_log", __name__)


async def relay_metadata(raw_md):
    async with httpx.AsyncClient() as client:
        for target in current_app.config["RELAY_METADATA_TO"]:
            if not isinstance(target, dict):
                _log.warning("incorrect format for relay_to block: %s", str(target))
                continue
            url = target.get("url")
            data = target.get("add_field", {})
            data.update(raw_md)
            add_header = target.get("add_header")
            try:
                _ = client.post(url, data=data, headers=add_header)
            except Exception as e:
                _log.error("Cannot relay metadata to %s : %s", url, e)


@blueprint.post("/metadata_log")
@token_required
async def post_metadata_log():
    raw_md = await request.get_json()

    if current_app.config["RELAY_METADATA_TO"]:
        # POST a copy of the dict because from_playout modifies it
        current_app.add_background_task(relay_metadata, dict(raw_md))

    md = await MetadataLog.from_playout(g.dbsession, raw_md)
    g.dbsession.add(md)
    await g.dbsession.commit()

    return "", 200

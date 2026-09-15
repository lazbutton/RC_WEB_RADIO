"""
This module defines endpoint that trigger sounds' replaygain analysis.

It relies on beets' replaygain plug-in, with a nicer thread pool defined in
`BeetsIntegration`.
"""

import logging

from quart import Blueprint, request
from werkzeug.exceptions import BadRequest

from radiotomate.auth import token_required
from radiotomate.beets import BeetsIntegration

_log = logging.getLogger(__name__)

blueprint = Blueprint("analyzer", __name__)


@blueprint.post("/analyzer")
@token_required
async def analyzer_post():
    """
    Start a replay gain analysis on given Sound.
    """
    if not request.is_json:
        raise BadRequest("JSON object expected")
    data = await request.get_json()
    beets = BeetsIntegration.get()
    try:
        for sound_id in data["sound_ids"]:
            await beets.analyze_soon(sound_id)
    except (KeyError, ValueError) as e:
        raise BadRequest(
            "We expect a JSON object with a single field 'sound_ids',"
            "an interger list"
        ) from e
    return {}

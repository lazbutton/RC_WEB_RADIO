import logging

from quart import Blueprint

from radiotomate.scheduler.watchdog import Watchdog
from radiotomate.version import get_version

_log = logging.getLogger(__name__)

blueprint = Blueprint("version", __name__)


@blueprint.get("/version")
async def version():
    return {
        "radiotomate": get_version(),
        "liquidsoap": Watchdog.liquidsoap_version,
    }

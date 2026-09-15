import logging
from importlib.metadata import version

_log = logging.getLogger(__name__)


def get_version() -> str:
    """
    Get Radiotomate's version from package metadata
    """
    try:
        return version("radiotomate")
    except Exception as excn:
        _log.warning(excn)
    return "demo"

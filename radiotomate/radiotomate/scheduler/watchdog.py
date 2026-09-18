import asyncio
import logging
from typing import Optional

from quart import Quart

from radiotomate.quart import ShutdownError, or_shutdown
from radiotomate.scheduler.metrics import runtime_metrics

_log = logging.getLogger(__name__)


class Watchdog:
    """
    This registers as an application extension, to ensure only one instance will
    ever exists. It expects a completely configured scheduler app, and
    provides the `liquidsoap_version` attribute, updated every 10 seconds.
    It will also close PLAYOUT_CLIENT.
    """

    liquidsoap_version = "disconnected"

    def __init__(self, app: Optional[Quart] = None):
        self.client = None
        self.app = None
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Quart) -> None:
        if "radiotomate_scheduler_watchdog" in app.extensions:
            raise RuntimeError(
                "radiotomate_scheduler_watchdog already registered on this app",
            )
        app.extensions["radiotomate_scheduler_watchdog"] = self
        self.app = app

        self.client = app.config["PLAYOUT_CLIENT"]
        app.before_serving(self.start_watchdog)
        app.after_serving(self.close_client)

    async def start_watchdog(self):
        self.app.add_background_task(self.watchdog)

    async def watchdog(self):
        """
        This is a watchdog that will send a GET request to the playout process'
        /live endpoint every 10 seconds, it will update liquidsoap_version and
        log accordingly.
        """
        previous_exception = None
        while True:
            try:
                r = await self.client.get("/version")
                if r.status_code == 200:
                    version = r.json()["version"]
                else:
                    version = "disconnected"
            except ShutdownError:
                break
            except Exception as e:
                if _log.isEnabledFor(logging.DEBUG):
                    if previous_exception and isinstance(e, previous_exception):
                        _log.debug("Caught again: %s", e)
                    else:
                        _log.exception(e)
                    previous_exception = e.__class__
                version = "disconnected"
            if Watchdog.liquidsoap_version != version:
                if version == "disconnected":
                    _log.warning("Could not connect to playout process")
                else:
                    _log.info(
                        "Connected to playout process - using liquidsoap %s",
                        version,
                    )
                Watchdog.liquidsoap_version = version
            runtime_metrics.playout_connected = version != "disconnected"
            try:
                await or_shutdown(asyncio.sleep(10))
            except (ShutdownError, asyncio.CancelledError):
                break

    async def close_client(self):
        await self.client.aclose()

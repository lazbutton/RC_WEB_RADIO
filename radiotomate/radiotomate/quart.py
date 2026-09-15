"""
This modules contains our extensions to Quart
"""

import asyncio
import logging
import signal
from collections.abc import Awaitable, Coroutine
from typing import Callable

from hypercorn import Config
from hypercorn.asyncio import serve
from hypercorn.utils import ShutdownError
from quart import Quart

_log = logging.getLogger(__name__)


class ShutdownManager:
    """
    We have to handle interruptions/signals ourselves to implement ``or_shutdown``,
    and per [Hypercorn documentation](https://hypercorn.readthedocs.io/en/latest/how_to_guides/api_usage.html#graceful-shutdown)
    """

    _instance = None
    _task = None
    _event = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._task = None
            cls._instance._event = asyncio.Event()
        return cls._instance

    @property
    def event(self):
        return self._event

    async def wait(self):
        await self._event.wait()

    @property
    def task(self):
        if self._task is None:
            self._task = asyncio.create_task(
                self._event.wait(),
                name="RadiotomateShutdownManager",
            )
        return self._task

    def trigger_shutdown(self, *_):
        self._event.set()

    def setup_signal_handler(self):
        """
        This is copied from Hypercorn's trigger setup, but with our own Event.
        """
        loop = asyncio.get_event_loop()
        for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, signal_name):
                try:
                    loop.add_signal_handler(
                        getattr(signal, signal_name),
                        self.trigger_shutdown,
                    )
                except NotImplementedError:
                    # Add signal handler may not be implemented on Windows
                    signal.signal(getattr(signal, signal_name), self.trigger_shutdown)


async def or_shutdown(coro: Coroutine):
    """
    Awaits for ``coro`` and return its result, or raises ``ShutdownError`` if a
    shutdown signal was received.
    """
    coro_task = asyncio.create_task(coro)
    try:
        await asyncio.wait(
            (coro_task, ShutdownManager().task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if coro_task.done():
            if exn := coro_task.exception():
                raise exn
            return coro_task.result()
    finally:
        if not coro_task.done():
            coro_task.cancel()
            raise ShutdownError()


class CustomQuart(Quart):
    jinja_options = {  # noqa: RUF012
        "autoescape": True,
    }

    def run_task(  # noqa: PLR0913
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        debug: bool | None = None,
        ca_certs: str | None = None,
        certfile: str | None = None,
        keyfile: str | None = None,
        shutdown_trigger: Callable[..., Awaitable[None]] | None = None,
    ) -> Coroutine[None, None, None]:
        """
        We need to override Quart's ``run_task`` because it's great to have its
        reloader, but the original method hardcodes a few things in the configuration.

        We also need to plug ShutdownManager behind ``shutdown_trigger``
        because the provided trigger will also stop the reloader. That's why
        when reloading we don't need to call
        ``ShutdownManager().setup_signal_handler()``.
        """
        config = self.make_basic_hypercorn_config()
        config.bind = [f"{host}:{port}"]
        config.ca_certs = ca_certs
        config.certfile = certfile
        config.keyfile = keyfile
        if debug is not None:
            self.debug = debug

        async def wrap_trigger():
            await shutdown_trigger()
            ShutdownManager().trigger_shutdown()

        return serve(self, config, shutdown_trigger=wrap_trigger)

    @classmethod
    def make_basic_hypercorn_config(cls) -> Config:
        config = Config()
        config.access_log_format = "%(h)s %(r)s %(s)s %(b)s %(L)s"
        config.accesslog = logging.getLogger("hypercorn.access")
        config.errorlog = config.accesslog
        return config

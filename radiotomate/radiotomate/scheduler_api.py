"""
Scheduler API
=============

Orders sent to the scheduler app are wrapped in an object that can be mocked
for tests or the demo mode, where the scheduler process does not exist.
"""

from __future__ import annotations

import json
import logging
import random
from asyncio import sleep
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from radiotomate.db import QuartAlchemy
from radiotomate.models import Sound
from radiotomate.quart import ShutdownError, or_shutdown

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from radiotomate.quart import CustomQuart

_log = logging.getLogger(__name__)


class Scheduler:
    """
    This is a singleton: use `Scheduler.get()`.
    It should be instantiated with `Scheduler.init(config, app)` when starting
    the application.
    """

    _instance = None

    client: httpx.AsyncClient = None

    @classmethod
    def get(cls) -> Scheduler:
        if cls._instance is None:
            raise RuntimeError("Scheduler is not initialized")
        return cls._instance

    @classmethod
    def init(cls, config, app: CustomQuart, demo=False) -> Scheduler:
        if cls._instance is not None:
            raise RuntimeError("init has already been called")
        if demo:
            implementation = SchedulerDemo
        else:
            implementation = cls
        cls._instance = implementation(config, app)
        app.after_serving(cls.after_serving)
        return cls._instance

    @classmethod
    async def after_serving(cls) -> None:
        if cls._instance:
            await cls._instance.client.aclose()

    def __init__(self, config, app: CustomQuart):
        self.client = httpx.AsyncClient(
            base_url="http://127.0.0.1:6822",
            headers={
                "X-Auth-Token": config["playout_process_config"]["token"],
            },
        )

    async def skip(self):
        await self.client.delete("/live")

    async def live(self) -> AsyncGenerator[dict, None]:
        sleeptime = 10
        while True:
            try:
                if sleeptime:
                    await or_shutdown(sleep(sleeptime))
                    sleeptime = 0
                async with self.client.stream("GET", "/live") as response:
                    async for line in response.aiter_bytes():
                        if response.status_code == 200 and line.startswith(
                            b"event: message\ndata: ",
                        ):
                            yield json.loads(line[21:])
            except ShutdownError:
                break
            except httpx.ReadTimeout:
                # avoid the verbose _log.exception when playout process is not started
                _log.debug(
                    "No live info received from scheduler, is the player started ?",
                )
                sleeptime = 10
            except httpx.ConnectError:
                # avoids the verbose _log.exception when working only on the interface
                _log.debug(
                    "Cannot connect to %s - retrying in 10 seconds",
                    self.client.base_url,
                )
                sleeptime = 10
            except Exception as e:
                if _log.isEnabledFor(logging.DEBUG):
                    _log.exception(e)
                sleeptime = 3

    async def update_schedule(self, cart_id: int):
        _log.debug("Updating schedule for cart %d", cart_id)
        result = await self.client.put(f"/schedule/{cart_id}")
        if result.status_code != 200:
            _log.error(
                "Error while updating schedule: %s %s",
                result.status_code,
                result.text,
            )

    async def push_cart(self, cart_id: int):
        _log.debug("Pushing cart %d now", cart_id)
        result = await self.client.post(f"/schedule/{cart_id}/now")
        if result.status_code != 200:
            _log.error(
                "Error while pushing cart %d: %s %s",
                cart_id,
                result.status_code,
                result.text,
            )
            raise RuntimeError(result.text or f"HTTP {result.status_code}")

    async def queue_analysis(self, sound_ids: list[int]):
        _log.debug("Queueing sounds for analysis: %r", sound_ids)
        result = await self.client.post(
            "/analyzer",
            json={
                "sound_ids": sound_ids,
            },
        )
        if result.status_code != 200:
            _log.error(
                "Queueing sounds (ids: %r) for analysis: %s %s",
                sound_ids,
                result.status_code,
                result.text,
            )


WORDS = list(
    {
        # From Tetsuo Kogawa' radioart manifesto https://radioart.jp
        w
        for w in """
    What is radioart? Who is radioart? Popular meaning of “radio” has been a
    receiving tool of radio signals There is and can be radioart using such a
    tool More positively radioart would be involved in wireless transmission
    However such a transmission remains in the function of broadcasting
    Radio station broadcasts Broadcast means 'cast broadly'
    Sometimes broadcasting is done not so broadly It is called "narrowcasting"
    But it still does cast Broadcasting has been seeking for more and more
    broader range of transmission toward nation-wide worldwide and space-wide
    (satellite) broadcasting""".split()
        if len(w) >= 3
    },
)


def artistic_generator():
    return " ".join([WORDS[random.randint(0, len(WORDS) - 1)] for i in range(2)])


class SchedulerDemo(Scheduler):
    """
    Demo/tests mode: in that case there is no scheduler process, so we override
    everything.

    In that case the `client` instance actualy points to the interface app,
    so this can emulate playing metadata.
    """

    TRACK_LENGTH = 180

    def __init__(self, config, app: CustomQuart):
        self.client = httpx.AsyncClient(
            base_url="http://127.0.0.1:6811",
            headers={
                "X-Auth-Token": config["playout_process_config"]["token"],
            },
        )
        self._started_at = datetime.now().replace(microsecond=0)
        self._on_air = datetime.now().replace(microsecond=0)
        self._i = 0
        self._metadata = self.generate_metadata()
        self.app = app

    async def live(self) -> AsyncGenerator[dict, None]:
        while True:
            elapsed = (datetime.now() - self._on_air).total_seconds()
            remaining = self.TRACK_LENGTH - elapsed
            if remaining < 0.0:
                await self.skip()
                remaining = 0.0
            md = self._metadata
            md["uptime"] = self.uptime()
            md["time"] = datetime.now().replace(microsecond=0).isoformat()
            md["remaining"] = str(remaining)
            md["elapsed"] = str(elapsed)
            yield md
            try:
                await or_shutdown(sleep(1))
            except ShutdownError:
                break

    async def skip(self):
        self._on_air = datetime.now().replace(microsecond=0)
        self._metadata = self.generate_metadata()

    def uptime(self) -> str:
        uptime = datetime.now().replace(microsecond=0) - self._started_at
        return str(uptime)

    def generate_metadata(self) -> dict:
        """
        generates something different each call
        """
        sources = ["live", "autodj", "carts", "relay", "stream"]
        albums = ["Radiotomate Rocks", "Better hygiene with Liquidsoap"]

        self._i += 1
        title = artistic_generator()
        artist = artistic_generator()
        album = albums[self._i % len(albums)]
        filename = (
            f"/home/radio/Music/{album}/"
            + artist.replace(" ", "_")
            + "-"
            + title.replace(" ", "_")
            + ".flac"
        )
        return {
            "artist": artist,
            "title": title,
            "source": sources[self._i % len(sources)],
            "status": "simulating",
            "initial_uri": filename,
            "album": album,
            "editor": "Pytest",
            "year": self._on_air.year - (self._i % 10),
            "tracknumber": str(self._i % 8),
            "uptime": self.uptime(),
            "on_air": self._on_air.isoformat(),
            "next_cart": {
                "title": artistic_generator(),
                "artist": "Cart démo",
                "rid": 11,
            },
            "next_autodj": {
                "title": artistic_generator(),
                "artist": artistic_generator(),
                "rid": 12,
            },
            "next_jingle": (
                {"rid": -1}
                if self._i % 3 == 0
                else {"title": "Habillage", "artist": "NTR", "rid": 13}
            ),
        }

    async def update_schedule(self, cart_id: int):
        _log.debug("Fake-update of schedule for cart %d", cart_id)

    async def push_cart(self, cart_id: int):
        _log.debug("Fake-push cart %d now", cart_id)

    async def queue_analysis(self, sound_ids: list[int]):
        _log.debug("Queueing sounds for analysis: %r", sound_ids)
        self.app.add_background_task(self._fake_analysis, sound_ids)

    async def _fake_analysis(self, sound_ids):
        await sleep(0.1)
        db = QuartAlchemy.get()
        async with db.session() as session:
            for sound_id in sound_ids:
                sound = await Sound.from_id(session, sound_id)
                if sound:
                    sound.gain = -1
                    sound.peak = 0.5
            await session.commit()

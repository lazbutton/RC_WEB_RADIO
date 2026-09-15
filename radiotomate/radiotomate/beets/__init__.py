# ruff: noqa: E402 — patch ioctl before importing beets.ui (Python 3.14)
"""
This module contains classes and wrappers to interface more easily with Beets.
"""

from __future__ import annotations

import logging
from asyncio import CancelledError, Queue, get_running_loop, sleep
from optparse import Values
from random import randint
from typing import TYPE_CHECKING

from radiotomate.beets.compat import patch_beets_term_ioctl

patch_beets_term_ioctl()

from beets.dbcore import sort_from_strings
from beets.library import Item, Library
from beets.ui import _setup
from beetsplug.replaygain import ReplayGainPlugin
from quart import Quart, current_app

from radiotomate.models.sound import Sound

if TYPE_CHECKING:
    from radiotomate.db import QuartAlchemy

_log = logging.getLogger(__name__)


class SoundToItemProxy(Item):
    "Masquerades one of our Sound into a beet's Item"

    def __init__(self, sound: Sound):
        super().__init__()
        self._sound = sound
        self.path = str(sound.path.absolute()).encode()
        self.rg_track_gain = sound.gain
        self.rg_track_peak = sound.peak

    def store(self, fields=None):
        self._sound.gain = self.rg_track_gain
        self._sound.peak = self.rg_track_peak


class BeetsIntegration:
    """
    This class wraps a Beets library and provides high-level helpers for our AutoDJ and
    replaygain analysis (this implies that replaygain level is set in the Beets
    configuration).

    It can also register as a Quart extension, so its instance can be easily found by
    views and awaited.
    """

    EXTENSION_NAME = "RadiotomateBeetsIntegration"

    # background analysis are started only after this delay, so the application and
    # playout process have all CPU available while starting
    BACKGROUND_ANALYSIS_DELAY = 30.0

    def __init__(self, lib: Library):
        self.lib: Library = lib
        self.rg = ReplayGainPlugin()
        self.analyzer_q = Queue()
        # logging.getLogger("beets.replaygain").setLevel(logging.DEBUG)

    @classmethod
    def default(cls) -> BeetsIntegration:
        """
        Instantiates Beets using its default config path
        """
        options = Values(
            {
                "config": None,  # could be a config path
                "exclude": None,
                "plugins": None,
            }
        )
        subcommands, lib = _setup(options, None)
        return BeetsIntegration(lib)

    @classmethod
    def get(cls) -> BeetsIntegration:
        """
        Find the instance wrapped in ``current_app``
        """
        return current_app.extensions[cls.EXTENSION_NAME]

    def init_app(self, app: Quart) -> None:
        if self.EXTENSION_NAME in app.extensions:
            raise RuntimeError(
                "An sqlalchemy extension has already been registered on this app",
            )
        app.extensions[self.EXTENSION_NAME] = self

    async def random_pick(self, query) -> Item | None:
        """
        Like "beet random [query]", returns the path ot a track matching given query
        """

        def fetch_items(q):
            return list(self.lib.items(q))

        loop = get_running_loop()
        objs = await loop.run_in_executor(None, fetch_items, query)
        if objs:
            index = randint(0, len(objs) - 1)
            obj = objs[index]
            return obj
        else:
            return None

    async def search(self, query) -> list[Item]:
        """
        Like "beet [query]", returns matching Items
        """

        def fetch_items(q):
            sort = sort_from_strings(Item, ["artist+"])
            return list(self.lib.items(q, sort=sort))

        loop = get_running_loop()
        objs = await loop.run_in_executor(None, fetch_items, query)
        return objs

    async def background_analyzer(self, db: QuartAlchemy):
        """
        This should be started as a background task
        """
        await sleep(self.BACKGROUND_ANALYSIS_DELAY)
        async with db.session() as session:
            ids = await Sound.ids_without_gain(session)
            for i in ids:
                await self.analyzer_q.put(i)
            if ids:
                _log.info(
                    "Queue'd %d sounds catching up their replaygain analysis", len(ids)
                )

        sound_id = None
        while True:
            try:
                sound_id = await self.analyzer_q.get()
                async with db.session() as dbsession:
                    sound = await Sound.from_id(dbsession, sound_id)
                    if sound:
                        await self._do_analyze_rg(sound)
                        await dbsession.commit()
            except CancelledError:
                return
            except Exception:
                _log.exception("while attempting sound_id=%r", sound_id)

    async def _do_analyze_rg(self, sound: Sound):
        """
        Fill ReplayGain columns on sound
        """
        proxied = SoundToItemProxy(sound)
        loop = get_running_loop()
        await loop.run_in_executor(None, self.rg.handle_track, proxied, False)

    async def analyze_soon(self, sound_id: int):
        """
        Add a sound to the replaygain analysis queue
        """
        await self.analyzer_q.put(sound_id)

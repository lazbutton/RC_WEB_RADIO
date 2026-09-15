# ruff: noqa: E402 - not all imports are at top of file because of the "responses" mock
"""
This module contains a mock Beets library, that should be used only for tests and demo
mode.
"""

import logging
import sys
from asyncio import Queue
from unittest.mock import MagicMock

from radiotomate.beets.compat import patch_beets_term_ioctl
from radiotomate.models.sound import Sound

patch_beets_term_ioctl()

# we mock the `responses` module entirely, it's imported by Beets' TestHelper but not
# used in our case
sys.modules["responses"] = MagicMock()

from beets.test.helper import TestHelper

from radiotomate.beets import BeetsIntegration
from radiotomate.scheduler_api import artistic_generator

# Test library will contain 6 tracks of this artist.
FAKE_ARTIST = "Camille Tester"


class BeetsMockIntegration(BeetsIntegration):
    BACKGROUND_ANALYSIS_DELAY = 0.0

    def __init__(self):
        """
        As opposed to :py:class:`radiotomate.beets.BeetsIntegration`, this constructor
        can be called directly. However, please call :py:meth:`teardown` before closing
        the application. Note that it does not call its super constructor!

        We re-use Beets' own fixture, but also pre-fill with some test content.

        The relay gain analysis goes trough our background task, but does not really
        call the ReplayGainPlugin.
        """
        TestHelper.db_on_disk = True
        helper = TestHelper()
        helper.setup_beets()
        logging.getLogger("beets").setLevel(logging.INFO)  # defaults to verbose
        self.helper = helper
        self.lib = helper.lib
        self.rg = None
        self.analyzer_q = Queue()

        for _ in range(5):
            helper.add_item(artist=FAKE_ARTIST, title=artistic_generator())

        for _ in range(100):
            helper.add_item(artist=artistic_generator(), title=artistic_generator())

    def teardown(self):
        self.helper.teardown_beets()

    async def _do_analyze_rg(self, sound: Sound):
        sound.gain = -1.0
        sound.peak = -1.0

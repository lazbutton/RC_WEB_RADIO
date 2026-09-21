# ruff: noqa: E402 - not all imports are at top of file because of the "responses" mock
"""
This module contains a mock Beets library, that should be used only for tests and demo
mode.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import sys
from asyncio import Queue
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003
from random import choice
from unittest.mock import MagicMock

from radiotomate.beets.compat import patch_beets_term_ioctl
from radiotomate.models.sound import Sound  # noqa: TC001

patch_beets_term_ioctl()

# we mock the `responses` module entirely, it's imported by Beets' TestHelper but not
# used in our case
sys.modules["responses"] = MagicMock()

from beets.test.helper import TestHelper

from radiotomate.beets import BeetsIntegration
from radiotomate.scheduler_api import artistic_generator

# Test library will contain 6 tracks of this artist.
FAKE_ARTIST = "Camille Tester"
LEADING_INDEX = re.compile(r"^\d+\s+")
SKIP_CART = re.compile(r"jingle|pub", re.IGNORECASE)


@dataclass
class SeedItem:
    artist: str
    title: str
    length: float
    path: bytes
    grouping: str = "rotation"


def split_sound_title(raw: str) -> tuple[str, str]:
    text = LEADING_INDEX.sub("", (raw or "").strip())
    if " - " not in text:
        return "", text
    artist, title = text.split(" - ", 1)
    return artist.strip(), LEADING_INDEX.sub("", title.strip())


def probe_id3(path: Path) -> tuple[str, str, float]:
    try:
        raw = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(path),
            ],
            timeout=6,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        return "", "", 0.0
    try:
        fmt = json.loads(raw).get("format") or {}
    except json.JSONDecodeError:
        return "", "", 0.0
    tags = fmt.get("tags") or {}
    lower = {str(k).lower(): str(v).strip() for k, v in tags.items()}
    artist = lower.get("artist") or ""
    title = LEADING_INDEX.sub("", lower.get("title") or "")
    if artist and title.lower().startswith(artist.lower() + " - "):
        title = title[len(artist) + 3 :].strip()
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return artist, title, duration


def seed_items_from_root(root: Path) -> list[SeedItem]:
    """Load autodj demo tracks from carts on disk (not jingles / pubs)."""
    db = root / "radiotomate.db"
    if not db.is_file():
        return []
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            """
            SELECT s.path, s.title, s.duration, c.title
            FROM sounds s
            JOIN carts c ON c.id = s.cart_id
            WHERE s.active = 1
            ORDER BY c.id, s.rank
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    items: list[SeedItem] = []
    seen: set[str] = set()
    for rel, raw_title, duration, cart_title in rows:
        if SKIP_CART.search(str(cart_title or "")):
            continue
        path = root / str(rel)
        if not path.is_file():
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            length = float(duration or 0)
        except (TypeError, ValueError):
            length = 0.0
        artist, title = split_sound_title(str(raw_title or path.stem))
        if not artist or not title or title.lower().endswith((".wav", ".mp3", ".flac")):
            p_artist, p_title, p_duration = probe_id3(path)
            artist = p_artist or artist
            title = p_title or title
            if p_duration > 0:
                length = p_duration
        if not title:
            title = path.stem
        items.append(
            SeedItem(
                artist=artist,
                title=title,
                length=length if length > 0 else 180.0,
                path=str(path).encode(),
            )
        )
    return items


class BeetsMockIntegration(BeetsIntegration):
    BACKGROUND_ANALYSIS_DELAY = 0.0

    def __init__(self, seed_root: Path | None = None):
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
        self._seed_items = seed_items_from_root(seed_root) if seed_root else []

        if self._seed_items:
            return

        for i in range(5):
            helper.add_item(
                artist=FAKE_ARTIST,
                title=artistic_generator(),
                grouping="rotation",
                path=f"/media/10-rotation/camille-{i}.mp3",
            )

        for i in range(100):
            helper.add_item(
                artist=artistic_generator(),
                title=artistic_generator(),
                grouping="rotation",
                path=f"/media/10-rotation/track-{i}.mp3",
            )

    def teardown(self):
        self.helper.teardown_beets()

    async def search(self, query) -> list:
        if self._seed_items:
            return list(self._seed_items)
        return await super().search(query)

    async def random_pick(self, query):
        if self._seed_items:
            return choice(self._seed_items)
        return await super().random_pick(query)

    async def _do_analyze_rg(self, sound: Sound):
        sound.gain = -1.0
        sound.peak = -1.0

    async def _do_analyze_item(self, item):
        item.rg_track_gain = -1.0
        item.rg_track_peak = -1.0
        item.store()

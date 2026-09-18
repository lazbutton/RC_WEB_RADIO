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
import urllib.error
import urllib.request
from asyncio import Lock, sleep
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from radiotomate.beets import BeetsIntegration
from radiotomate.db import QuartAlchemy
from radiotomate.domain.execution import rundown_summary
from radiotomate.models import Cart, Sound
from radiotomate.quart import ShutdownError, or_shutdown
from radiotomate.scheduler.clock import (
    advance_sequencer_cursor,
    now_paris,
)
from radiotomate.scheduler.execution import published_rundown
from radiotomate.scheduler.rundown import (
    DEFAULT_CART_SEC,
    DEFAULT_MUSIC_SEC,
    build_rundown,
)

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
    def reset_instance(cls) -> None:
        cls._instance = None

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

    async def live_rundown(self, session, beets, horizon_min: int = 30) -> dict:
        return await published_rundown(session, beets, horizon_min=horizon_min)

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

    async def push_sound(self, cart_id: int, sound_id: int):
        _log.debug("Pushing cart %d sound %d now", cart_id, sound_id)
        result = await self.client.post(f"/schedule/{cart_id}/sounds/{sound_id}/now")
        if result.status_code != 200:
            _log.error(
                "Error while pushing sound %d of cart %d: %s %s",
                sound_id,
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


def demo_cue_path(data_root: Path) -> Path | None:
    ops_data = data_root.resolve().parent.parent / "ops" / "local" / "data"
    if ops_data.is_dir():
        return ops_data / "playout.json"
    return None


class SchedulerDemo(Scheduler):
    """
    Demo/tests mode: no scheduler process. Now / skip / fire follow the clock rundown.
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
        self.app = app
        self._lock = Lock()
        self._items: list[dict] = []
        self._forecast: list[dict] = []
        self._forecast_horizon = 0
        self._played: list[dict] = []
        self._rundown_meta: dict = {"horizon_min": 30, "clock": None, "daypart": None}
        self._override: dict | None = None
        self._track_length = float(self.TRACK_LENGTH)
        self._metadata = self._placeholder_metadata()
        self._bootstrapped = False
        self._data_root = Path(config.get("data", {}).get("root") or ".")
        self._relay_to = list(config.get("metadata_log", {}).get("relay_to") or [])
        self._cue_path = demo_cue_path(self._data_root)
        self._last_broadcast: tuple | None = None

    def _placeholder_metadata(self) -> dict:
        return {
            "artist": "",
            "title": "Pas d'horloge",
            "source": "autodj",
            "kind": "musique",
            "status": "simulating",
            "initial_uri": "",
            "album": "",
            "editor": "demo",
            "uptime": self.uptime(),
            "on_air": self._on_air.isoformat(),
            "next_cart": {"rid": -1},
            "next_autodj": {"rid": -1},
            "next_jingle": {"rid": -1},
        }

    def uptime(self) -> str:
        uptime = datetime.now().replace(microsecond=0) - self._started_at
        return str(uptime)

    def _split_resource(self, resource: str) -> tuple[str, str]:
        if " — " in resource:
            artist, title = resource.split(" — ", 1)
            return artist.strip(), title.strip()
        return "", resource.strip()

    def _empty_cue(self) -> dict:
        return {"rid": -1}

    def _cue_from_item(self, item: dict, rid: int) -> dict:
        artist, title = self._split_resource(str(item.get("resource") or ""))
        return {
            "title": title,
            "artist": artist or str(item.get("cart") or item.get("category") or ""),
            "rid": rid,
            "initial_uri": "",
        }

    def _cues_from(self, items: list[dict]) -> dict:
        next_autodj = self._empty_cue()
        next_jingle = self._empty_cue()
        next_cart = self._empty_cue()
        for index, item in enumerate(items, start=1):
            cue = self._cue_from_item(item, index)
            queue = item.get("queue")
            if queue == "autodj" and next_autodj.get("rid") == -1:
                next_autodj = cue
            elif queue == "jingles" and next_jingle.get("rid") == -1:
                next_jingle = cue
            elif queue == "carts" and next_cart.get("rid") == -1:
                next_cart = cue
        return {
            "next_autodj": next_autodj,
            "next_jingle": next_jingle,
            "next_cart": next_cart,
        }

    def _duration_for(self, item: dict) -> float:
        try:
            value = float(item.get("duration") or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
        if item.get("kind") == "musique":
            return DEFAULT_MUSIC_SEC
        return DEFAULT_CART_SEC

    def _abs_media(self, raw: object) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        path = Path(text)
        if path.is_file():
            return str(path)
        joined = self._data_root / text
        if joined.is_file():
            return str(joined)
        return text

    def _broadcast_now(self) -> None:
        started = datetime.now(timezone.utc)
        body = {
            "artist": self._metadata.get("artist") or "",
            "title": self._metadata.get("title") or "",
            "album": self._metadata.get("album") or "",
            "duration": self._track_length,
            "started_at": started.isoformat(),
            "source": self._metadata.get("source") or "autodj",
            "SOURCE_NAME": "new-trad-radio",
            "path": self._metadata.get("initial_uri") or "",
            "on_air": self._on_air.isoformat(),
        }
        key = (body["artist"], body["title"], body["path"], body["on_air"])
        if key == self._last_broadcast:
            return
        self._last_broadcast = key
        if self._cue_path is not None:
            try:
                self._cue_path.parent.mkdir(parents=True, exist_ok=True)
                self._cue_path.write_text(
                    json.dumps(body, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                _log.debug("demo playout cue: %s", exc)
        for target in self._relay_to:
            if not isinstance(target, dict):
                continue
            url = target.get("url")
            if not url:
                continue
            payload = dict(target.get("add_field") or {})
            payload.update(body)
            headers = {"Content-Type": "application/json"}
            extra = target.get("add_header") or {}
            headers.update(extra)
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    method="POST",
                    headers=headers,
                )
                with urllib.request.urlopen(req, timeout=2) as response:
                    response.read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                _log.debug("demo nowplaying relay: %s", exc)

    def _apply_current(self) -> None:
        cues = self._cues_from(self._items[1:] if self._items else [])
        if self._override is not None:
            self._metadata = {**self._override, **cues}
            self._publish()
            self._broadcast_now()
            return
        if not self._items:
            self._track_length = float(self.TRACK_LENGTH)
            self._metadata = {**self._placeholder_metadata(), **cues}
            self._publish()
            self._broadcast_now()
            return
        item = self._items[0]
        artist, title = self._split_resource(str(item.get("resource") or ""))
        queue = str(item.get("queue") or "autodj")
        self._track_length = self._duration_for(item)
        self._metadata = {
            "artist": artist or str(item.get("cart") or item.get("category") or ""),
            "title": title or "—",
            "source": queue,
            "kind": str(item.get("kind") or "musique"),
            "status": "simulating",
            "initial_uri": self._abs_media(item.get("path")),
            "album": str(item.get("clock") or ""),
            "editor": "demo",
            "uptime": self.uptime(),
            "on_air": self._on_air.isoformat(),
            **cues,
        }
        self._publish()
        self._broadcast_now()

    def _publish(self) -> None:
        from radiotomate.interface.live import normalize_live, set_live_snapshot

        elapsed = (datetime.now() - self._on_air).total_seconds()
        remaining = max(0.0, self._track_length - elapsed)
        md = {
            **self._metadata,
            "uptime": self.uptime(),
            "time": datetime.now().replace(microsecond=0).isoformat(),
            "on_air": self._on_air.isoformat(),
            "remaining": str(remaining),
            "elapsed": str(elapsed),
        }
        self._metadata = md
        set_live_snapshot(normalize_live(md))

    async def _load_items(self, session, horizon_min: int = 30) -> None:
        beets = BeetsIntegration.get()
        data = await build_rundown(session, beets, horizon_min=horizon_min)
        self._items = list(data.get("items") or [])
        self._forecast = []
        self._forecast_horizon = 0
        self._rundown_meta = {
            "horizon_min": data.get("horizon_min", horizon_min),
            "clock": data.get("clock"),
            "daypart": data.get("daypart"),
        }

    def _display_items(self, forecast: list[dict]) -> list[dict]:
        live = list(self._items)
        if not live:
            return forecast
        if forecast and forecast[0].get("resource") == live[0].get("resource"):
            return forecast
        last = str(live[-1].get("at") or "")
        tail = [row for row in forecast if str(row.get("at") or "") > last]
        return live + tail

    def _archive_current(self) -> None:
        if not self._items:
            return
        item = dict(self._items[0])
        started = self._on_air.isoformat()
        if self._played and self._played[-1].get("at") == started:
            return
        elapsed = max(1.0, (datetime.now() - self._on_air).total_seconds())
        item["status"] = "joué"
        item["status_code"] = "played"
        item["at"] = started
        item["duration"] = round(elapsed, 2)
        self._played.append(item)
        del self._played[:-12]

    async def live_rundown(self, session, beets, horizon_min: int = 30) -> dict:
        async with self._lock:
            await self._bootstrap()
            if self._forecast_horizon < horizon_min or not self._forecast:
                data = await build_rundown(session, beets, horizon_min=horizon_min)
                self._forecast = self._display_items(list(data.get("items") or []))
                self._forecast_horizon = horizon_min
                self._rundown_meta["horizon_min"] = horizon_min
                if data.get("clock"):
                    self._rundown_meta["clock"] = data.get("clock")
                if data.get("daypart"):
                    self._rundown_meta["daypart"] = data.get("daypart")
            upcoming = list(self._forecast or self._items)
            if upcoming:
                current = dict(upcoming[0])
                current["status"] = "à l'antenne"
                current["status_code"] = "on_air"
                upcoming[0] = current
            items = [dict(row) for row in self._played] + upcoming
            clock = self._rundown_meta.get("clock")
            daypart = self._rundown_meta.get("daypart")
            header = upcoming[0] if upcoming else (items[0] if items else None)
            if header:
                clock = header.get("clock") or clock
                daypart = header.get("daypart") or daypart
            return {
                "now": now_paris().isoformat(),
                "horizon_min": self._rundown_meta.get("horizon_min") or horizon_min,
                "clock": clock,
                "daypart": daypart,
                "items": items,
                "summary": rundown_summary(items),
            }

    async def _bootstrap(self) -> None:
        if self._bootstrapped:
            return
        db = QuartAlchemy.get()
        async with db.session() as session:
            await self._load_items(session)
        self._apply_current()
        self._bootstrapped = True

    async def live(self) -> AsyncGenerator[dict, None]:
        while True:
            async with self._lock:
                await self._bootstrap()
                elapsed = (datetime.now() - self._on_air).total_seconds()
                remaining = self._track_length - elapsed
                if remaining < 0.0:
                    await self._skip_unlocked()
                    elapsed = 0.0
                    remaining = self._track_length
                md = {
                    **self._metadata,
                    "uptime": self.uptime(),
                    "time": datetime.now().replace(microsecond=0).isoformat(),
                    "on_air": self._on_air.isoformat(),
                    "remaining": str(max(0.0, remaining)),
                    "elapsed": str(max(0.0, elapsed)),
                }
                self._metadata = md
            yield md
            try:
                await or_shutdown(sleep(1))
            except ShutdownError:
                break

    async def skip(self):
        async with self._lock:
            await self._skip_unlocked()

    async def _skip_unlocked(self):
        self._archive_current()
        self._on_air = datetime.now().replace(microsecond=0)
        self._override = None
        db = QuartAlchemy.get()
        async with db.session() as session:
            await advance_sequencer_cursor(session)
            if self._items:
                self._items.pop(0)
            if self._forecast:
                self._forecast.pop(0)
            if len(self._items) < 24:
                await self._load_items(session, horizon_min=30)
        self._apply_current()

    async def _set_now_from_sound(self, cart: Cart, sound: Sound):
        queue = cart.playout_queue()
        kind = "jingle" if queue == "jingles" else "son"
        duration = float(sound.duration or DEFAULT_CART_SEC)
        raw = str(sound.title or "").strip()
        if " - " in raw:
            artist, title = (part.strip() for part in raw.split(" - ", 1))
        else:
            artist, title = cart.title, raw or cart.title
        self._on_air = datetime.now().replace(microsecond=0)
        self._track_length = duration if duration > 0 else DEFAULT_CART_SEC
        self._override = {
            "artist": artist,
            "title": title,
            "source": queue,
            "kind": kind,
            "status": "simulating",
            "initial_uri": self._abs_media(sound.path),
            "album": cart.title,
            "editor": "demo",
            "uptime": self.uptime(),
            "on_air": self._on_air.isoformat(),
        }
        self._apply_current()

    async def update_schedule(self, cart_id: int):
        _log.debug("Fake-update of schedule for cart %d", cart_id)

    async def push_cart(self, cart_id: int):
        db = QuartAlchemy.get()
        async with db.session() as session:
            cart = await Cart.from_id(session, cart_id, load_sounds=True)
            if not cart:
                raise RuntimeError(f"cart #{cart_id} not found")
            try:
                sound = cart.next_sound()
            except IndexError:
                sound = None
            if sound is None and cart.sounds:
                sound = cart.sounds[0]
            if sound is None:
                raise RuntimeError(f"cart #{cart_id} has no sound")
            async with self._lock:
                self._archive_current()
                await self._set_now_from_sound(cart, sound)

    async def push_sound(self, cart_id: int, sound_id: int):
        db = QuartAlchemy.get()
        async with db.session() as session:
            cart = await Cart.from_id(session, cart_id, load_sounds=True)
            if not cart:
                raise RuntimeError(f"cart #{cart_id} not found")
            sound = next((row for row in cart.sounds if row.id == sound_id), None)
            if sound is None:
                raise RuntimeError(f"sound #{sound_id} not in cart #{cart_id}")
            async with self._lock:
                self._archive_current()
                await self._set_now_from_sound(cart, sound)

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

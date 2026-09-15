"""
This module processes information sent every second by the playout process:
 * metadata of whatever is currently playing
 * scheduling information about chat is currently playing (source, remaining, etc.)
 * information about queues state - when the music or jingle queues are empty,
   we push new content from here
"""

import json
import logging
import time
from asyncio import Lock, Queue, sleep
from random import shuffle

from quart import Blueprint, current_app, make_response, request
from werkzeug.exceptions import BadRequest

from radiotomate.auth import token_required
from radiotomate.beets import BeetsIntegration
from radiotomate.db import QuartAlchemy
from radiotomate.enums import ScheduleMode
from radiotomate.models import AutoDJSlot, Cart
from radiotomate.quart import ShutdownError, or_shutdown

blueprint = Blueprint("live", __name__)
watching_clients = set()

_flag_jingles_update_running = Lock()
_flag_autodj_update_running = Lock()

_log = logging.getLogger(__name__)


@blueprint.get("/live")
@token_required
async def get():
    queue = Queue()
    watching_clients.add(queue)

    if len(watching_clients) > 1:
        _log.warning("Another client was already connected to /live")

    async def send_now_playing():
        try:
            while True:
                data = await or_shutdown(queue.get())
                yield b"event: message\ndata: " + data + b"\n\n"
        except ShutdownError:
            pass
        except Exception as exc:
            _log.exception(exc)
        finally:
            watching_clients.remove(queue)
            _log.debug(
                "shutting down a /live stream - %d remaining",
                len(watching_clients),
            )

    response = await make_response(
        send_now_playing(),
        {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Transfer-Encoding": "chunked",
        },
    )
    response.timeout = None
    return response


async def _notify_clients(data):
    for queue in list(watching_clients):
        await queue.put(data)


async def _push_jingle():
    async with _flag_jingles_update_running:
        started = time.perf_counter()
        db = QuartAlchemy.get()
        async with db.session() as session:
            carts = await Cart.all(
                session, schedule_mode=ScheduleMode.JINGLES, load_sounds=True
            )
        shuffle(carts)
        while carts:
            cart = carts.pop()
            sound = cart.next_sound()
            if sound:
                response = await current_app.config["PLAYOUT_CLIENT"].post(
                    "/queue/jingles",
                    json={
                        "path": str(sound.path),
                        "artist": "",
                        "title": "",
                        "radiotomate_sound_id": sound.id,
                        "rg_track_gain": str(sound.gain),
                    },
                )
                if response.status_code == 200:
                    result = response.json()
                    _log.info(
                        "Pushed jingle %d sound %s:%s as RID %s, in %.03fs",
                        cart.id,
                        sound.id,
                        sound.path,
                        result,
                        time.perf_counter() - started,
                    )
                else:
                    try:
                        result = response.json()
                    except json.JSONDecodeError:
                        result = response.text
                    _log.error(
                        "Error while pushing jingle %d sound %s:%s: %s, in %.03fs",
                        cart.id,
                        sound.id,
                        sound.path,
                        result,
                        time.perf_counter() - started,
                    )
                return
        _log.warning("We should queue a jingle, but none is available.")
        await sleep(10)


async def _push_autodj():
    async with _flag_autodj_update_running:
        started = time.perf_counter()
        beets = BeetsIntegration.get()
        db = QuartAlchemy.get()
        async with db.session() as session:
            trackfilter = await AutoDJSlot.current_filter(session)
        next_item = await beets.random_pick(trackfilter)
        if not next_item:
            next_item = await beets.random_pick("")
            if next_item:
                msg = (
                    "Auto-DJ filter %r could not select any track!"
                    "falling back to full random"
                )
                _log.warning(msg, trackfilter)
        if next_item:
            next_path = next_item.path.decode()
            response = await current_app.config["PLAYOUT_CLIENT"].post(
                "/queue/autodj",
                json={
                    "path": next_path,
                    "beets_id": next_item.id,
                    "rg_track_gain": next_item.rg_track_gain,
                },
            )
            if response.status_code == 200:
                result = response.json()
                _log.info(
                    "Pushed track %s to autodj as RID %s, in %.03fs",
                    next_path,
                    result,
                    time.perf_counter() - started,
                )
            else:
                try:
                    result = response.json()
                except json.JSONDecodeError:
                    result = response.text
                _log.error(
                    "Error while pushing track %s to autodj: %s, in %.03fs",
                    next_path,
                    result,
                    time.perf_counter() - started,
                )
        else:
            _log.warning("We should queue to autodj, but no track is selectable")
            await sleep(10)


async def _check_queues(data):
    live_data = json.loads(data)
    if (
        "next_jingle" in live_data
        and live_data["next_jingle"].get("rid") == -1
        and not _flag_jingles_update_running.locked()
    ):
        current_app.add_background_task(_push_jingle)

    if (
        "next_autodj" in live_data
        and live_data["next_autodj"].get("rid") == -1
        and not _flag_autodj_update_running.locked()
    ):
        current_app.add_background_task(_push_autodj)


@blueprint.post("/live")
@token_required
async def post():
    if not request.is_json:
        raise BadRequest("JSON object expected")
    data = await request.get_data()
    current_app.add_background_task(_notify_clients, data)
    current_app.add_background_task(_check_queues, data)
    return "", 200


# don't log "POST /live" to access log because a message per second would be too noisy
class LiveFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return message.find("POST /live") == -1


logging.getLogger("hypercorn.access").addFilter(LiveFilter())


@blueprint.delete("/live")
@token_required
async def skip():
    client = current_app.config["PLAYOUT_CLIENT"]
    await client.delete("/live")
    return "", 200

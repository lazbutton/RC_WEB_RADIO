"""
This module processes information sent every second by the playout process:
 * metadata of whatever is currently playing
 * scheduling information about chat is currently playing (source, remaining, etc.)
 * information about queues state - when the music or jingle queues are empty,
   we push new content from here
"""

import json
import logging
from asyncio import Lock, Queue
from time import perf_counter

from quart import Blueprint, current_app, make_response, request
from werkzeug.exceptions import BadRequest

from radiotomate.auth import token_required
from radiotomate.beets import BeetsIntegration
from radiotomate.db import QuartAlchemy
from radiotomate.quart import ShutdownError, or_shutdown
from radiotomate.scheduler.clock import tick as clock_tick
from radiotomate.scheduler.metrics import runtime_metrics

blueprint = Blueprint("live", __name__)
watching_clients = set()

_flag_sequencer_running = Lock()

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


async def _run_sequencer(live_data: dict):
    started = perf_counter()
    try:
        async with _flag_sequencer_running:
            beets = BeetsIntegration.get()
            db = QuartAlchemy.get()
            client = current_app.config["PLAYOUT_CLIENT"]
            async with db.session() as session:
                await clock_tick(session, live_data, client, beets)
    finally:
        runtime_metrics.tick_duration_seconds = perf_counter() - started


async def _check_queues(data):
    live_data = json.loads(data)
    if _flag_sequencer_running.locked():
        runtime_metrics.tick_skipped_total += 1
        return
    current_app.add_background_task(_run_sequencer, live_data)


@blueprint.post("/live")
@token_required
async def post():
    if not request.is_json:
        raise BadRequest("JSON object expected")
    data = await request.get_data()
    runtime_metrics.heartbeat()
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
    from radiotomate.scheduler.playout import gateway_for

    gateway = gateway_for(current_app.config["PLAYOUT_CLIENT"])
    await gateway.skip()
    return "", 200

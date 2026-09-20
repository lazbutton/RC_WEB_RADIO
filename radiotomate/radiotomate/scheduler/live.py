"""
This module processes information sent every second by the playout process:
 * metadata of whatever is currently playing
 * scheduling information about chat is currently playing (source, remaining, etc.)
 * information about queues state - when the music or jingle queues are empty,
   we push new content from here
"""

from __future__ import annotations

import json
import logging
from asyncio import Lock, Queue
from time import monotonic, perf_counter

import httpx
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
_next_sig_lock = Lock()
_last_next_sig: tuple | None = None
_last_clock_at = 0.0

_log = logging.getLogger(__name__)


def _parse_cue(value: object) -> dict | None:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict):
        return None
    try:
        rid = int(parsed.get("rid", -1))
    except (TypeError, ValueError):
        rid = -1
    title = str(parsed.get("title") or "").strip()
    artist = str(parsed.get("artist") or "").strip()
    if rid < 0 and not title:
        return None
    cue = {"title": title, "artist": artist, "rid": rid}
    album = str(parsed.get("album") or "").strip()
    if album:
        cue["album"] = album
    uri = str(parsed.get("initial_uri") or "").strip()
    if uri:
        cue["initial_uri"] = uri
    try:
        duration = float(parsed.get("duration"))
    except (TypeError, ValueError):
        duration = 0.0
    if duration > 0:
        cue["duration"] = duration
    return cue


def _cue_sig(value: object) -> tuple[int, str]:
    cue = _parse_cue(value)
    if not cue:
        return (-1, "")
    return (int(cue["rid"]), str(cue["title"]))


def _clock_fields(live_data: dict) -> dict:
    out: dict = {}
    for key in ("remaining", "elapsed", "duration"):
        try:
            value = float(live_data.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0 or key != "duration":
            out[key] = value
    return out


async def _post_sidecar(payload: dict) -> None:
    targets = current_app.config.get("RELAY_METADATA_TO") or []
    if not targets:
        return
    timeout = 2.0
    retry = current_app.config.get("RELAY_METADATA_RETRY") or {}
    try:
        timeout = max(0.1, min(30.0, float(retry.get("timeout_seconds", timeout))))
    except (TypeError, ValueError):
        pass
    async with httpx.AsyncClient(timeout=timeout) as client:
        for target in targets:
            if not isinstance(target, dict):
                continue
            url = target.get("url")
            if not isinstance(url, str) or not url:
                continue
            headers = dict(target.get("add_header") or {})
            try:
                response = await client.post(url, json=payload, headers=headers)
                if not (200 <= response.status_code < 300):
                    _log.warning("sidecar relay %s -> %s", url, response.status_code)
            except Exception as exc:
                _log.warning("sidecar relay failed %s: %s", url, exc)


async def _relay_next_if_changed(live_data: dict) -> None:
    global _last_next_sig, _last_clock_at
    targets = current_app.config.get("RELAY_METADATA_TO") or []
    if not targets:
        return
    sig = (
        _cue_sig(live_data.get("next_autodj")),
        _cue_sig(live_data.get("next_jingle")),
        _cue_sig(live_data.get("next_cart")),
    )
    send_next = False
    send_clock = False
    async with _next_sig_lock:
        if sig != _last_next_sig:
            _last_next_sig = sig
            send_next = True
        now = monotonic()
        if send_next or now - _last_clock_at >= 3.0:
            _last_clock_at = now
            send_clock = True
    if not send_clock:
        return
    payload = _clock_fields(live_data)
    if send_next:
        payload["next_autodj"] = _parse_cue(live_data.get("next_autodj"))
        payload["next_jingle"] = _parse_cue(live_data.get("next_jingle"))
        payload["next_cart"] = _parse_cue(live_data.get("next_cart"))
        _log.info("relaying next cues sig=%s targets=%d", sig, len(targets))
    if not payload:
        return
    await _post_sidecar(payload)


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
    current_app.add_background_task(_relay_next_if_changed, live_data)
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

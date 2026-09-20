import json
import logging
import math
from asyncio import Queue
from datetime import datetime
from http.client import HTTPException

from hypercorn.utils import ShutdownError
from quart import Blueprint, g, jsonify, make_response, request
from quart_auth import current_user

from radiotomate.auth import login_required, permission_required
from radiotomate.interface.autodj import invalidate_conducteur_cache
from radiotomate.quart import or_shutdown
from radiotomate.scheduler_api import Scheduler
from radiotomate.templates import render_macro as general_render_macro

_log = logging.getLogger(__name__)

blueprint = Blueprint("live", __name__, template_folder="templates")
watching_clients = set()
_latest_live: dict[str, dict | None] = {"payload": None}


async def render_macro(macro, **kwargs) -> str:
    return await general_render_macro("live/macros.jinja", macro, **kwargs)


def set_live_snapshot(payload: dict | None) -> None:
    _latest_live["payload"] = payload


def get_live_snapshot() -> dict:
    cached = _latest_live["payload"]
    if cached is None:
        return {"status": "offline"}
    return cached


def _float_field(value: object, default: float = 0.0) -> float:
    try:
        number = float(value if value is not None else default)
    except (TypeError, ValueError, OverflowError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _cue(value: object) -> dict | None:
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
    if rid < 0:
        return None
    cue = {
        "title": str(parsed.get("title") or "").strip(),
        "artist": str(parsed.get("artist") or "").strip(),
        "rid": rid,
        "initial_uri": str(parsed.get("initial_uri") or ""),
    }
    duration = _float_field(parsed.get("duration"), 0.0)
    if duration > 0:
        cue["duration"] = duration
    return cue


def normalize_live(md: dict) -> dict:
    raw_status = str(md.get("status") or "")
    status = "simulating" if raw_status == "simulating" else "playing"
    source = str(md.get("source") or "")
    if "insert_initial_track_mark" in source.lower():
        source = ""
    return {
        "status": status,
        "source": source,
        "artist": str(md.get("artist") or ""),
        "title": str(md.get("title") or ""),
        "album": str(md.get("album") or ""),
        "kind": str(md.get("kind") or ""),
        "remaining": _float_field(md.get("remaining")),
        "elapsed": _float_field(md.get("elapsed")),
        "duration": _float_field(md.get("duration")),
        "on_air": str(md.get("on_air") or md.get("time") or ""),
        "next_autodj": _cue(md.get("next_autodj")),
        "next_jingle": _cue(md.get("next_jingle")),
        "next_cart": _cue(md.get("next_cart")),
        "autodj_queued": _queued_field(md.get("autodj_queued")),
        "jingles_queued": _queued_field(md.get("jingles_queued")),
        "carts_queued": _queued_field(md.get("carts_queued")),
    }


def _queued_field(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


@blueprint.get("/live")
@login_required
async def get():
    if "text/event-stream" not in request.accept_mimetypes:
        raise HTTPException(400, "text/event-stream not supported")

    queue = Queue()
    watching_clients.add(queue)

    async def send_now_playing():
        try:
            while True:
                data = await or_shutdown(queue.get())
                yield f"event: message\ndata: {data}\n\n".encode()
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


@blueprint.get("/live.json")
@login_required
async def live_json():
    snapshot = dict(get_live_snapshot())
    from radiotomate.services.emissions import attach_live_emission

    dirty = await attach_live_emission(g.dbsession, snapshot)
    if dirty:
        await g.dbsession.commit()
    return jsonify(snapshot)


async def watch_livedata_task():
    async for md in Scheduler.get().live():
        try:
            remaining = round(_float_field(md.get("remaining", 0.0)))
        except OverflowError:  # liquidsoap may send "inf"
            remaining = 0
        set_live_snapshot(normalize_live(md))
        if md.get("elapsed"):
            md["elapsed"] = round(float(md.get("elapsed")))
        if md.get("time"):
            time_dt = datetime.fromisoformat(md.get("time"))
            md["date"] = time_dt.strftime("%d/%m/%Y")
            md["time"] = time_dt.strftime("%H:%M:%S")
        for key in ("next_cart", "next_autodj", "next_jingle"):
            val = md.get(key)
            if isinstance(val, str):
                try:
                    md[key] = json.loads(val)
                except json.JSONDecodeError:
                    md[key] = {"rid": -1}
        rendered = await render_macro("nowplayling", data=md, remaining=remaining)
        rendered = rendered.replace("\n", " ")  # \n\n is a separator for SSE
        for queue in list(watching_clients):
            await queue.put(rendered)


@blueprint.delete("/live")
@permission_required("live")
async def skip():
    await Scheduler.get().skip()
    invalidate_conducteur_cache()
    return "", 200


@blueprint.delete("/live.json")
@login_required
async def skip_json():
    if not current_user.user.can_live():
        return jsonify({"error": "forbidden"}), 403
    await Scheduler.get().skip()
    invalidate_conducteur_cache()
    return jsonify({"ok": True})

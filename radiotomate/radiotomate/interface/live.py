import json
import logging
from asyncio import Queue
from datetime import datetime
from http.client import HTTPException

from hypercorn.utils import ShutdownError
from quart import Blueprint, make_response, request

from radiotomate.auth import login_required, permission_required
from radiotomate.quart import or_shutdown
from radiotomate.scheduler_api import Scheduler
from radiotomate.templates import render_macro as general_render_macro

_log = logging.getLogger(__name__)

blueprint = Blueprint("live", __name__, template_folder="templates")
watching_clients = set()


async def render_macro(macro, **kwargs) -> str:
    return await general_render_macro("live/macros.jinja", macro, **kwargs)


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


async def watch_livedata_task():
    async for md in Scheduler.get().live():
        try:
            remaining = round(float(md.get("remaining", 0.0)))
        except OverflowError:  # liquidsoap may send "inf"
            remaining = 0
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
    await (Scheduler.get()).skip()
    return "", 200

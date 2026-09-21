"""
Scheduling-related endpoints and tasks.

Note that in this module Cart.id is usually a string, because it is also used as a
Schedule ID and APScheduler prefers strings.
"""

import logging
from datetime import datetime, timedelta

from apscheduler import CoalescePolicy, ScheduleLookupError
from apscheduler.triggers.date import DateTrigger
from quart import Blueprint, current_app, g, request
from sqlalchemy.orm import Session as ormSession
from werkzeug.exceptions import BadRequest

from radiotomate.auth import token_required
from radiotomate.enums import CartMode
from radiotomate.models import Cart, Sound
from radiotomate.scheduler.playout import PlayoutGateway, gateway_for

_log = logging.getLogger(__name__)

blueprint = Blueprint("schedule", __name__)


async def skip_cart(session: ormSession, current_app, sound_id: int | None = None):
    _log.debug("starting skip_cart(%r)", sound_id)
    gateway = gateway_for(current_app.config["PLAYOUT_CLIENT"])
    if sound_id is None:
        params = {"relay": 1}
    else:
        params = {"radiotomate_sound_id": sound_id}
    result = await gateway.skip(params)
    if result.status_code == 204:
        _log.info("No max duration to enforce for sound#%r", sound_id)
    elif not result.ok:
        _log.error(
            "Enforcing max duration on sound#%r failed: %s",
            sound_id,
            result.error,
        )


async def push_cart(session: ormSession, current_app, cart_id: int):
    _log.debug("starting push_cart(%d)", cart_id)
    gateway = gateway_for(current_app.config["PLAYOUT_CLIENT"])
    cart = await Cart.from_id(session, cart_id, load_sounds=True)
    if not cart:
        _log.warning(f"Scheduled cart #{cart_id} not found")
        return
    if cart.mode == CartMode.RELAY:
        await _push_cart_relay(cart, gateway, current_app)
    else:
        await _push_cart_sound(cart, gateway, current_app, session)


async def push_named_sound(
    session: ormSession,
    current_app,
    cart_id: int,
    sound_id: int,
):
    _log.debug("starting push_named_sound(%d, %d)", cart_id, sound_id)
    gateway = gateway_for(current_app.config["PLAYOUT_CLIENT"])
    cart = await Cart.from_id(session, cart_id, load_sounds=True)
    if not cart:
        _log.warning("Cart #%d not found", cart_id)
        return False
    sound = next((row for row in cart.sounds if row.id == sound_id), None)
    if not sound:
        _log.warning("Sound #%d not in cart #%d", sound_id, cart_id)
        return False
    await _enqueue_sound(cart, sound, gateway, current_app)
    await session.commit()
    return True


async def push_bank_path(
    session: ormSession,
    current_app,
    path: str,
    *,
    artist: str,
    title: str,
    kind: str = "son",
) -> bool:
    _ = session
    gateway = gateway_for(current_app.config["PLAYOUT_CLIENT"])
    if kind == "jingle":
        queue = "jingles"
    elif kind == "musique":
        queue = "autodj"
    else:
        queue = "carts"
    result = await gateway.queue(
        queue,
        {
            "path": path,
            "artist": artist,
            "title": title,
        },
    )
    if result.ok:
        _log.info("Pushed bank path %s on %s as RID %s", path, queue, result.payload)
        return True
    _log.error("Error while pushing bank path %s: %s", path, result.error)
    return False


async def _push_cart_sound(
    cart: Cart,
    gateway: PlayoutGateway,
    current_app,
    session: ormSession,
):
    pushed = 0
    for _ in range(2):
        sound = cart.next_sound()
        if not sound:
            break
        await _enqueue_sound(cart, sound, gateway, current_app)
        pushed += 1
    if not pushed:
        _log.warning("Scheduled cart %d:%s has no next sound", cart.id, cart.title)
        return
    await session.commit()


async def _enqueue_sound(
    cart: Cart,
    sound: Sound,
    gateway: PlayoutGateway,
    current_app,
):
    queue = cart.playout_queue()
    result = await gateway.queue(
        queue,
        {
            "path": str(sound.path),
            "artist": cart.title,
            "title": sound.title,
            "radiotomate_sound_id": sound.id,
            "rg_track_gain": str(sound.gain),
        },
    )
    if result.ok:
        _log.info(
            "Pushed cart %d sound %s:%s as RID %s",
            cart.id,
            sound.id,
            sound.path,
            result.payload,
        )
        sound.last_played = datetime.now()
        if queue == "carts":
            from radiotomate.scheduler.clock import note_carts_push

            note_carts_push(cart.id)
        if cart.max_duration:
            await current_app.scheduler.remove_schedule(str(-cart.id))
            when = datetime.now() + timedelta(seconds=cart.max_duration)
            await current_app.scheduler.add_schedule(
                skip_cart,
                DateTrigger(when),
                id=str(-cart.id),
                args=[int(sound.id)],
                coalesce=CoalescePolicy.latest,
                misfire_grace_time=10.0,
            )
    else:
        _log.error(
            "Error while pushing cart %d sound %s:%s: %s",
            cart.id,
            sound.id,
            sound.path,
            result.error,
        )


async def _push_cart_relay(cart: Cart, gateway: PlayoutGateway, current_app):
    result = await gateway.relay_start(cart.url)
    if result.ok:
        _log.info(
            "Pushed relay cart %d: %s | status: %s",
            cart.id,
            cart.url,
            result.payload,
        )
        if cart.max_duration:
            # remove a potential previous entry
            await current_app.scheduler.remove_schedule(str(-cart.id))
            when = datetime.now() + timedelta(seconds=cart.max_duration)
            await current_app.scheduler.add_schedule(
                skip_cart,
                DateTrigger(when),
                id=str(-cart.id),
                coalesce=CoalescePolicy.latest,
                misfire_grace_time=10.0,
            )
        else:
            _log.warning(
                "relay cart %d has no maximum duration! might play forever.", cart.id
            )
    else:
        _log.error("Error while pushing relay cart %d:%s: %s", cart.id, result.error)


@blueprint.put("/schedule/<cart_id>")
@token_required
async def post_schedule(cart_id: str):
    try:
        cart = await Cart.from_id(g.dbsession, int(cart_id))
    except ValueError:
        return f"cart #{cart_id} not found", 404
    if not cart:
        return f"cart #{cart_id} not found", 404

    await current_app.scheduler.remove_schedule(cart_id)

    if not cart.schedule_mode.uses_cron:
        # Clock / jingles carts have no cron: the PUT only drops a stale job
        # (e.g. a cart that just left TIMED mode).
        cart.schedule_correct = True
        _log.info(
            "Cart #%s is %s: no APScheduler job", cart_id, cart.schedule_mode.value
        )
        return "", 200

    try:
        trigger = cart.to_timed_trigger()
    except ValueError:
        message = f"Invalid cron expression for cart #{cart_id}: {cart.schedule_repr}"
        _log.error(message)
        cart.schedule_correct = False
        await g.dbsession.commit()
        raise BadRequest(message) from None

    await current_app.scheduler.add_schedule(
        push_cart,
        trigger,
        id=cart_id,
        args=[int(cart_id)],
        coalesce=CoalescePolicy.latest,
        misfire_grace_time=10.0,
    )

    cart.schedule_correct = True
    return "", 200


@blueprint.get("/schedule/<cart_id>")
@token_required
async def get_schedule(cart_id: str):
    try:
        schedule = await current_app.scheduler.get_schedule(cart_id)
        return {
            "next_time": schedule.next_fire_time.isoformat(),
        }
    except ScheduleLookupError:
        return f"cart #{cart_id} not scheduled", 404


@blueprint.post("/schedule/<cart_id>/now")
@token_required
async def push_now(cart_id: str):
    try:
        cid = int(cart_id)
    except ValueError:
        return f"cart #{cart_id} not found", 404
    cart = await Cart.from_id(g.dbsession, cid)
    if not cart:
        return f"cart #{cart_id} not found", 404
    await push_cart(g.dbsession, current_app, cid)
    return "", 200


@blueprint.post("/schedule/<cart_id>/sounds/<sound_id>/now")
@token_required
async def push_sound_now(cart_id: str, sound_id: str):
    try:
        cid = int(cart_id)
        sid = int(sound_id)
    except ValueError:
        return "cart or sound not found", 404
    ok = await push_named_sound(g.dbsession, current_app, cid, sid)
    if not ok:
        return "cart or sound not found", 404
    return "", 200


@blueprint.post("/queues/flush")
@token_required
async def flush_playout_queues():
    gateway = gateway_for(current_app.config["PLAYOUT_CLIENT"])
    result = await gateway.flush_queues()
    payload = result.payload if isinstance(result.payload, dict) else {}
    if not result.ok:
        return {"ok": False, "error": result.error or "playout_unavailable"}, 502
    return {"ok": True, "queues": payload}


@blueprint.post("/schedule/path/now")
@token_required
async def push_path_now():
    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequest("JSON object required")
    path = str(data.get("path") or "").strip()
    if not path:
        raise BadRequest("path required")
    ok = await push_bank_path(
        g.dbsession,
        current_app,
        path,
        artist=str(data.get("artist") or ""),
        title=str(data.get("title") or ""),
        kind=str(data.get("kind") or "son"),
    )
    if not ok:
        return "playout rejected path", 502
    return "", 200

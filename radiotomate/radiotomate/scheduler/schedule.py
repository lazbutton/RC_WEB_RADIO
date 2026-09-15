"""
Scheduling-related endpoints and tasks.

Note that in this module Cart.id is usually a string, because it is also used as a
Schedule ID and APScheduler prefers strings.
"""

import json
import logging
from datetime import datetime, timedelta

from apscheduler import CoalescePolicy, ScheduleLookupError
from apscheduler.triggers.date import DateTrigger
from httpx import AsyncClient
from quart import Blueprint, current_app, g
from sqlalchemy.orm import Session as ormSession
from werkzeug.exceptions import BadRequest

from radiotomate.auth import token_required
from radiotomate.enums import CartMode, ScheduleMode
from radiotomate.models import Cart
from radiotomate.models.cart import URL_TO_AUTODJ_QUEUE

_log = logging.getLogger(__name__)

blueprint = Blueprint("schedule", __name__)


async def skip_cart(session: ormSession, current_app, sound_id: int | None = None):
    _log.debug("starting skip_cart(%r)", sound_id)
    playout_client: AsyncClient = current_app.config["PLAYOUT_CLIENT"]
    if sound_id is None:
        params = {"relay": 1}
    else:
        params = {"radiotomate_sound_id": sound_id}
    response = await playout_client.delete("/live", params=params)
    if response.status_code == 204:
        _log.info("No max duration to enforce for sound#%r", sound_id)
    elif response.status_code != 200:
        msg = response.text
        _log.error("Enforcing max duration on sound#%r failed: %s", sound_id, msg)


async def push_cart(session: ormSession, current_app, cart_id: int):
    _log.debug("starting push_cart(%d)", cart_id)
    playout_client = current_app.config["PLAYOUT_CLIENT"]
    cart = await Cart.from_id(session, cart_id, load_sounds=True)
    if not cart:
        _log.warning(f"Scheduled cart #{cart_id} not found")
        return
    if cart.mode == CartMode.RELAY:
        await _push_cart_relay(cart, playout_client, current_app)
    else:
        await _push_cart_sound(cart, playout_client, current_app)


async def _push_cart_sound(cart: Cart, playout_client: AsyncClient, current_app):
    sound = cart.next_sound()
    if sound:
        queue = "carts"
        if cart.url == URL_TO_AUTODJ_QUEUE:
            queue = URL_TO_AUTODJ_QUEUE
        response = await playout_client.post(
            f"/queue/{queue}",
            json={
                "path": str(sound.path),
                "artist": cart.title,
                "title": sound.title,
                "radiotomate_sound_id": sound.id,
                "rg_track_gain": str(sound.gain),
            },
        )
        if response.status_code == 200:
            result = response.json()
            _log.info(
                "Pushed cart %d sound %s:%s as RID %s",
                cart.id,
                sound.id,
                sound.path,
                result,
            )
            if cart.max_duration:
                # remove a potential previous entry
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
            try:
                result = response.json()
            except json.JSONDecodeError:
                result = response.text
            _log.error(
                "Error while pushing cart %d sound %s:%s: %s",
                cart.id,
                sound.id,
                sound.path,
                result,
            )
    else:
        _log.warning("Scheduled cart %d:%s has no next sound", cart.id, cart.title)


async def _push_cart_relay(cart: Cart, playout_client: AsyncClient, current_app):
    response = await playout_client.post("/relay", params={"url": cart.url})
    if response.status_code == 200:
        result = response.json()
        _log.info(
            "Pushed relay cart %d: %s | status: %s",
            cart.id,
            cart.url,
            result,
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
        try:
            result = response.json()
        except json.JSONDecodeError:
            result = response.text
        _log.error("Error while pushing relay cart %d:%s: %s", cart.id, result)


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

    try:
        if cart.schedule_mode is ScheduleMode.TIMED:
            trigger = cart.to_timed_trigger()
        else:
            message = f"Requested to schedule the non-timed cart #{cart_id}: "
            message += cart.schedule_repr
            _log.error(message)
            raise BadRequest(message)
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

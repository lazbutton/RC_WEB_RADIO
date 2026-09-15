import json
import logging
import shutil
from urllib.parse import urlparse

import mutagen
from quart import (
    Blueprint,
    current_app,
    g,
    render_template,
    request,
    send_file,
    url_for,
)
from quart_auth import Unauthorized
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from radiotomate.auth import current_user, login_required, permission_required
from radiotomate.enums import CartMode, DayNames, ScheduleMode
from radiotomate.interface import safe_int, safe_path, stream_form
from radiotomate.models import Cart, Sound
from radiotomate.models.cart import URL_TO_AUTODJ_QUEUE
from radiotomate.scheduler_api import Scheduler
from radiotomate.templates import render_macro as general_render_macro

_log = logging.getLogger(__name__)

blueprint = Blueprint("carts", __name__, template_folder="templates")


async def render_macro(macro, **kwargs) -> str:
    return await general_render_macro("carts/macros.jinja", macro, **kwargs)


async def poke_scheduler(cart_id: int) -> None:
    await Scheduler.get().update_schedule(cart_id)


def apply_schedule(cart: Cart, form: dict) -> Cart:
    """
    Transform form data ("cron formula" fields) into a cart's schedule dict.
    We PUT it to the scheduler process, that may invalidate the dict. At least, it will
    cancel any existing schedule.

    This returns the Cart to allow chaining.
    """
    try:
        cart.schedule_mode = ScheduleMode[form.get("schedule")]
    except ValueError:
        raise BadRequest(f"schedule_mode should be in {list(ScheduleMode)}") from None

    def get_or(field, default):
        received = form.get(field)
        if received:
            received = received.strip()
        if received:
            return received
        else:
            return default

    if cart.schedule_mode is ScheduleMode.JINGLES:
        cart.schedule_minute = get_or("minute", "*")
        cart.schedule_second = get_or("second", "*")
    else:
        cart.schedule_minute = get_or("minute", "0")
        cart.schedule_second = get_or("second", "0")

    cart.schedule_year = get_or("year", "*")
    cart.schedule_month = get_or("month", "*")
    cart.schedule_day = get_or("day", "*")
    cart.schedule_week = get_or("week", "*")
    cart.schedule_day_of_week = get_or("day_of_week", "*")
    cart.schedule_hour = get_or("hour", "*")

    try:
        cart.to_timed_trigger()
        cart.schedule_correct = True
    except Exception as e:
        cart.schedule_correct = False
        _log.warning("incorrect schedule %s, got %r", cart.schedule_repr, e)

    return cart


def apply_max_duration(cart: Cart, form: dict) -> None:
    """
    Attempt to convert the minutes/seconds inputs into a cart's max_duration field.
    If values are not correct, the field is left as it was.
    """
    minutes = safe_int(form.get("max_duration_minutes"), 0, positive=True)
    seconds = safe_int(form.get("max_duration_seconds"), 0, positive=True)
    if minutes == 0 and seconds == 0:
        cart.max_duration = None
    else:
        seconds += 60 * minutes
        cart.max_duration = seconds
    if cart.mode is CartMode.RELAY and cart.max_duration is None:
        raise BadRequest("Stream relays should have a maximum duration")


def apply_url(cart: Cart, form: dict) -> None:
    """
    Cart.url is also used by non-relay carts, to flag if the sounds cart should push
    to the auto-dj queue (cf. the ``URL_TO_AUTODJ_QUEUE`` constant).
    """
    cart.url = None
    if cart.mode is CartMode.RELAY:
        cart.url = form.get("url")
        parsed = urlparse(cart.url)
        if not parsed.scheme or not parsed.hostname or not parsed.path:
            raise BadRequest("This does not look like a complete URL")
    else:
        flag = form.get("queue")
        if flag == URL_TO_AUTODJ_QUEUE:
            cart.url = URL_TO_AUTODJ_QUEUE


@blueprint.get("/carts")
@login_required
async def index():
    carts = await Cart.all(g.dbsession)
    return await render_template("carts/index.jinja", carts=carts)


@blueprint.get("/carts/add")
@permission_required("carts")
async def add_form():
    return await render_template(
        "carts/cart_form.jinja", is_advanced="false", day_names=DayNames
    )


@blueprint.post("/carts")
@permission_required("carts")
async def add():
    form = await request.form
    title = form.get("title")
    if title:
        title = title.strip()
    if not title:
        raise BadRequest("Please provide a title")

    if await Cart.from_title(g.dbsession, title):
        raise Conflict(f"Cart {title} already exists")

    cart = Cart(title=title, notes=form.get("notes"))

    try:
        mode = form.get("mode")
        cart.mode = CartMode[mode]
    except KeyError:
        raise BadRequest(f"Incorrect mode value: {mode}") from None
    apply_max_duration(cart, form)
    apply_schedule(cart, form)
    apply_url(cart, form)

    g.dbsession.add(cart)
    await g.dbsession.commit()

    cart_root = current_app.config["DATA_ROOT"] / "carts"
    cart.path = safe_path(cart_root, title, prefix=str(cart.id))
    cart.path.mkdir(parents=True)
    await g.dbsession.commit()

    if cart.schedule_mode is ScheduleMode.TIMED:
        # do this even if not schedule_correct because it will at least remove that
        # cart from the schedule
        current_app.add_background_task(poke_scheduler, cart.id)

    return (
        "",
        200,
        {
            "HX-Redirect": url_for("carts.index"),
        },
    )


@blueprint.get("/carts/<int:cart_id>")
@permission_required("carts")
async def edit_form(cart_id: int):
    cart = await Cart.from_id(g.dbsession, cart_id)
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")
    coming_from = request.args.get("coming_from")
    return await render_template(
        "carts/cart_form.jinja",
        cart=cart,
        coming_from=coming_from,
        is_advanced=str(cart.schedule_is_advanced).lower(),
        day_names=DayNames,
    )


@blueprint.put("/carts/<int:cart_id>")
@permission_required("carts")
async def edit(cart_id: int):
    form = await request.form
    cart = await Cart.from_id(g.dbsession, cart_id)
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")

    title = form.get("title")
    if title:
        title = title.strip()
    if not title:
        raise BadRequest("Please provide a title")
    if title != cart.title:
        if await Cart.from_title(g.dbsession, title):
            raise Conflict(f"Cart {title} already exists")
        cart.title = title
        cart_root = current_app.config["DATA_ROOT"] / "carts"
        new_path = safe_path(cart_root, cart.title, prefix=str(cart.id))
        if new_path != cart.path:
            cart.path.rename(new_path)
            await Sound.change_path_prefix(
                g.dbsession,
                cart.id,
                old=cart.path,
                new=new_path,
            )
            cart.path = new_path

    cart.notes = form.get("notes")

    try:
        mode = form["mode"]
        try:
            cart.mode = CartMode[mode]
        except KeyError:
            raise BadRequest(f"Incorrect mode value: {mode}") from None
    except KeyError:  # `mode` is disabled for relays, so browser don't post it
        pass

    apply_max_duration(cart, form)
    apply_schedule(cart, form)
    apply_url(cart, form)
    await g.dbsession.commit()

    if cart.schedule_mode is ScheduleMode.TIMED:
        # do this even if not schedule_correct because it will at least remove that
        # cart from the schedule
        current_app.add_background_task(poke_scheduler, cart.id)

    if form.get("coming_from") == "sounds":
        redirect_to = url_for("carts.sounds_list", cart_id=cart_id)
    else:
        redirect_to = url_for("carts.index")

    return (
        "",
        200,
        {
            "HX-Redirect": redirect_to,
        },
    )


@blueprint.delete("/carts/<int:cart_id>")
@permission_required("carts")
async def delete(cart_id: int):
    cart = await Cart.from_id(g.dbsession, cart_id)
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")
    shutil.rmtree(cart.path)
    await g.dbsession.delete(cart)
    await g.dbsession.commit()
    return ""


@blueprint.post("/carts/<int:cart_id>/now")
@permission_required("carts")
async def push_now(cart_id: int):
    if not current_user.user.can_live():
        raise Unauthorized()
    cart = await Cart.from_id(g.dbsession, cart_id)
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")
    try:
        await Scheduler.get().push_cart(cart_id)
    except RuntimeError as exc:
        raise BadRequest(str(exc)) from exc
    return (
        "",
        200,
        {
            "HX-Trigger": json.dumps(
                {
                    "radiotomateMessage": {
                        "text": "Cart envoyé à l'antenne",
                        "icon": "success",
                    }
                }
            ),
        },
    )


@blueprint.get("/carts/<int:cart_id>/sounds")
@login_required
async def sounds_list(cart_id: int):
    cart = await Cart.from_id(
        g.dbsession, cart_id, load_sounds=True, load_uploaders=True
    )
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")
    next_sound = cart.next_sound(for_display=True)
    next_id = next_sound.id if next_sound else None
    return await render_template("carts/sounds.jinja", cart=cart, next_id=next_id)


async def _render_next_tags(cart_id: int, cart: Cart = None):
    """
    Renders the `next_tag` macro for all sounds in the cart. This will update
    (out-of-band) all tags' in the page.

    If `cart` is provided, it will be used instead of fetching the cart from
    the database.
    """
    if not cart:
        cart = await Cart.from_id(g.dbsession, cart_id, load_sounds=True)
    next_sound = cart.next_sound(for_display=True)
    next_id = next_sound.id if next_sound else None
    result = []
    for sound in cart.sounds:
        result.append(await render_macro("next_tag", sound=sound, next_id=next_id))
    return "\n".join(result)


@blueprint.post("/carts/<int:cart_id>/sounds")
@permission_required("carts")
async def add_sounds(cart_id: int):
    """
    This can be called from the sounds list, but also from carts list, when
    the cart is empty.
    """
    from_list = request.args.get("from_list", default=False)
    cart = await Cart.from_id(g.dbsession, cart_id)
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")
    form = await stream_form(cart.path)
    added_sounds = []
    rank = await Sound.next_rank(g.dbsession, cart_id)
    for uploaded_file in form.getall("sounds"):
        metadata = mutagen.File(uploaded_file["uploaded_to"])
        if metadata is None:
            raise BadRequest(
                f"{uploaded_file['filename']} does not seem to be an audio file.",
            )
        sound = Sound(
            cart_id=cart_id,
            rank=rank,
            title=uploaded_file["filename"],
            path=uploaded_file["uploaded_to"],
            duration=int(metadata.info.length),
            uploader_id=current_user.user.id,
        )
        added_sounds.append(sound)
        g.dbsession.add(sound)
        rank += 1
    await g.dbsession.commit()

    await Scheduler.get().queue_analysis([s.id for s in added_sounds])

    g.dbsession.expire(cart)
    cart = await Cart.from_id(g.dbsession, cart_id, load_sounds=True)
    cart.recompute_duration()
    await g.dbsession.commit()
    if from_list:
        next_sound = cart.next_sound(for_display=True)
        next_id = next_sound.id if next_sound else None
        result = await render_macro(
            "sound_lines",
            sounds=added_sounds,
            next_id=next_id,
            mode=cart.mode,
            editable=current_user.user.can_carts(),
        )
        result += await _render_next_tags(None, cart)
        result += await render_macro("cart_summary", cart=cart)
        return result
    else:
        return (
            "",
            200,
            {
                "HX-Redirect": url_for("carts.sounds_list", cart_id=cart_id),
            },
        )


@blueprint.delete("/carts/<int:cart_id>/sounds/<int:sound_id>")
@permission_required("carts")
async def delete_sound(cart_id: int, sound_id: int):
    sound = await Sound.from_id(g.dbsession, sound_id)
    if not sound:
        raise NotFound(f"Sound {sound_id} not found")
    if not sound.cart.id == cart_id:
        raise NotFound(f"Sound {sound_id} is not in cart {cart_id}")

    sound.path.unlink()
    await g.dbsession.delete(sound)
    await g.dbsession.flush()

    cart = await Cart.from_id(g.dbsession, cart_id, load_sounds=True)
    result = await _render_next_tags(cart_id, cart=cart)
    result += await render_macro("cart_summary", cart=cart)

    sounds = await Sound.update_ranks(g.dbsession, cart_id)
    for sound in sounds:
        result += await render_macro("sound_rank", sound=sound)

    await g.dbsession.commit()
    return result


@blueprint.get("/carts/<int:cart_id>/sounds/<int:sound_id>")
@login_required
async def download_sound(cart_id: int, sound_id: int):
    sound = await Sound.from_id(g.dbsession, sound_id)
    if not sound:
        raise NotFound(f"Sound {sound_id} not found")
    if not sound.cart.id == cart_id:
        raise NotFound(f"Sound {sound_id} is not in cart {cart_id}")

    return await send_file(
        sound.path,
        mimetype="audio/mpeg",
        conditional=True,
        as_attachment=True,
        attachment_filename=sound.path.name,
    )


@blueprint.put("/carts/<int:cart_id>/sounds/<int:sound_id>")
@permission_required("carts")
async def edit_sound(cart_id: int, sound_id: int):
    sound = await Sound.from_id(g.dbsession, sound_id)
    if not sound:
        raise NotFound(f"Sound {sound_id} not found")
    if not sound.cart.id == cart_id:
        raise NotFound(f"Sound {sound_id} is not in cart {cart_id}")

    form = await request.form

    title = form.get("title")
    if title is not None:
        title = title.strip()
        if title:
            sound.title = title
        else:
            raise BadRequest("Please provide a title")

    active = form.get("active")
    if active is not None:
        sound.active = active == "true"

    await g.dbsession.commit()
    cart = await Cart.from_id(g.dbsession, cart_id, load_sounds=True)
    result = ""
    if title is not None:
        next_sound = cart.next_sound(for_display=True)
        next_id = next_sound.id if next_sound else None
        result += await render_macro(
            "sound_lines",
            sounds=[sound],
            next_id=next_id,
            mode=cart.mode,
            editable=current_user.user.can_carts(),
        )
    if active is not None:
        result += await render_macro("cart_summary", cart=cart)
        result += await _render_next_tags(None, cart=cart)

    return result, 200


@blueprint.post("/carts/<int:cart_id>")
@permission_required("carts")
async def sort_sounds(cart_id: int):
    result = ""

    form = await request.form
    required_ranks = form.getlist("rank")
    rank = 1
    for sound_id in required_ranks:
        sound = await Sound.from_id(g.dbsession, int(sound_id))
        if not sound:
            raise NotFound(f"Sound {sound_id} not found")
        if not sound.cart.id == cart_id:
            raise BadRequest(f"Sound {sound_id} is not in cart {cart_id}")
        sound.rank = rank
        result += await render_macro("sound_rank", sound=sound)
        rank += 1
    await g.dbsession.commit()

    result += await _render_next_tags(cart_id)

    return result, 200

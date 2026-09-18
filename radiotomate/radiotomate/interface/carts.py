import contextlib
import json
import logging
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

import mutagen
from quart import (
    Blueprint,
    current_app,
    g,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)
from quart_auth import Unauthorized
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from radiotomate.auth import current_user, login_required, permission_required
from radiotomate.enums import CartMode, DayNames, ScheduleMode
from radiotomate.interface import safe_int, safe_path, stream_form
from radiotomate.interface.json_util import forbidden_unless, read_json_object
from radiotomate.interface.spa import console_dist
from radiotomate.models import Cart, Sound
from radiotomate.models.cart import URL_TO_AUTODJ_QUEUE
from radiotomate.scheduler_api import Scheduler
from radiotomate.services.carts import (
    assert_cart_deletable,
    require_cart_version,
    sync_cart_display_titles,
)
from radiotomate.templates import render_macro as general_render_macro

_log = logging.getLogger(__name__)

blueprint = Blueprint("carts", __name__, template_folder="templates")


def media_root() -> Path:
    return Path(os.environ.get("MEDIA_ROOT", "/media")).resolve()


def path_in_media_bank(path: Path) -> bool:
    try:
        resolved = path.resolve()
        root = media_root()
        return resolved == root or root in resolved.parents
    except (OSError, RuntimeError):
        return False


def unlink_cart_file(path: Path | None) -> None:
    """Delete a file copied into /data/carts. Never touch the shared media bank."""
    if not path:
        return
    if path_in_media_bank(path):
        return
    path.unlink(missing_ok=True)


def resolve_bank_path(raw: str) -> Path:
    root = media_root()
    candidate = Path(raw)
    path = candidate if candidate.is_absolute() else (root / candidate)
    resolved = path.resolve()
    if root not in resolved.parents and resolved != root:
        raise BadRequest("path is outside the media bank")
    rel = resolved.relative_to(root).as_posix()
    top = rel.split("/", 1)[0]
    if top in {"00-inbox", "90-trash"}:
        raise BadRequest("inbox/trash cannot be attached to a cart")
    if not resolved.is_file():
        raise BadRequest(f"missing file: {rel}")
    return resolved


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
async def index():
    dist = console_dist()
    if dist is not None:
        return await send_from_directory(dist, "index.html")
    if not await current_user.is_authenticated:
        raise Unauthorized()
    carts = await Cart.all(g.dbsession)
    return await render_template("carts/index.jinja", carts=carts)


def _enum_from_json(enum_cls, raw, label: str):
    text = str(raw or "").strip()
    if not text:
        raise BadRequest(f"{label} required")
    try:
        return enum_cls(text)
    except ValueError:
        pass
    try:
        return enum_cls[text]
    except KeyError as exc:
        raise BadRequest(f"Incorrect {label}: {text}") from exc


def _sound_json(sound: Sound) -> dict:
    uploader = None
    if getattr(sound, "uploader", None) is not None:
        uploader = sound.uploader.username
    return {
        "id": sound.id,
        "rank": sound.rank,
        "title": sound.title,
        "duration": sound.duration,
        "active": sound.active,
        "available": sound.available,
        "gain": sound.gain,
        "peak": sound.peak,
        "last_played": sound.last_played.isoformat() if sound.last_played else None,
        "uploader": uploader,
    }


def _cart_queue(cart: Cart) -> str | None:
    if cart.mode is CartMode.RELAY:
        return None
    if cart.url == URL_TO_AUTODJ_QUEUE:
        return URL_TO_AUTODJ_QUEUE
    return "carts"


def _cart_json(cart: Cart) -> dict:
    nxt = None
    if cart.mode is not CartMode.RELAY:
        nxt = cart.next_sound(for_display=True)
    return {
        "id": cart.id,
        "version": cart.version,
        "title": cart.title,
        "notes": cart.notes or "",
        "mode": cart.mode.value if cart.mode else None,
        "url": cart.url if cart.mode is CartMode.RELAY else None,
        "queue": _cart_queue(cart),
        "max_duration": cart.max_duration,
        "average_duration": cart.average_duration,
        "schedule_mode": cart.schedule_mode.value if cart.schedule_mode else None,
        "schedule_correct": cart.schedule_correct,
        "schedule_summary": cart.schedule_summary(),
        "schedule_advanced": cart.schedule_is_advanced,
        "schedule_year": cart.schedule_year,
        "schedule_month": cart.schedule_month,
        "schedule_day": cart.schedule_day,
        "schedule_week": cart.schedule_week,
        "schedule_day_of_week": cart.schedule_day_of_week,
        "schedule_hour": cart.schedule_hour,
        "schedule_minute": cart.schedule_minute,
        "schedule_second": cart.schedule_second,
        "next_sound_id": nxt.id if nxt else None,
        "sounds": [_sound_json(sound) for sound in cart.sounds],
    }


async def _load_cart_json(cart_id: int) -> Cart | None:
    return await Cart.from_id(
        g.dbsession, cart_id, load_sounds=True, load_uploaders=True
    )


def _poke_if_timed(cart: Cart) -> None:
    if cart.schedule_mode is not ScheduleMode.TIMED:
        return
    try:
        Scheduler.get()
    except RuntimeError:
        return
    current_app.add_background_task(poke_scheduler, cart.id)


def _apply_cart_json(cart: Cart, data: dict) -> None:  # noqa: PLR0912
    if "mode" in data:
        cart.mode = _enum_from_json(CartMode, data.get("mode"), "mode")
    if "notes" in data:
        cart.notes = str(data.get("notes") or "")
    schedule_keys = {
        "schedule_mode",
        "schedule_year",
        "schedule_month",
        "schedule_day",
        "schedule_week",
        "schedule_day_of_week",
        "schedule_hour",
        "schedule_minute",
        "schedule_second",
    }
    if schedule_keys & data.keys():
        sched_name = ScheduleMode.TIMED.name
        if cart.schedule_mode:
            sched_name = cart.schedule_mode.name
        form = {
            "schedule": sched_name,
            "year": cart.schedule_year,
            "month": cart.schedule_month,
            "day": cart.schedule_day,
            "week": cart.schedule_week,
            "day_of_week": cart.schedule_day_of_week,
            "hour": cart.schedule_hour,
            "minute": cart.schedule_minute,
            "second": cart.schedule_second,
        }
        if "schedule_mode" in data:
            form["schedule"] = _enum_from_json(
                ScheduleMode, data.get("schedule_mode"), "schedule_mode"
            ).name
        mapping = {
            "schedule_year": "year",
            "schedule_month": "month",
            "schedule_day": "day",
            "schedule_week": "week",
            "schedule_day_of_week": "day_of_week",
            "schedule_hour": "hour",
            "schedule_minute": "minute",
            "schedule_second": "second",
        }
        for json_key, form_key in mapping.items():
            if json_key in data and data[json_key] is not None:
                form[form_key] = str(data[json_key])
        if form["schedule"] == ScheduleMode.TIMED.name:
            if "schedule_minute" not in data and form.get("minute") in ("", "*"):
                form["minute"] = "0"
            if "schedule_second" not in data and form.get("second") in ("", "*"):
                form["second"] = "0"
        apply_schedule(cart, form)
    if "max_duration" in data:
        raw = data.get("max_duration")
        if raw in (None, "", 0):
            cart.max_duration = None
        else:
            cart.max_duration = int(raw)
        if cart.mode is CartMode.RELAY and cart.max_duration is None:
            raise BadRequest("Stream relays should have a maximum duration")
    if cart.mode is CartMode.RELAY:
        if "url" in data:
            apply_url(cart, {"url": data.get("url")})
    elif "queue" in data or "url" in data:
        queue = data.get("queue")
        if queue is None and data.get("url") == URL_TO_AUTODJ_QUEUE:
            queue = URL_TO_AUTODJ_QUEUE
        apply_url(cart, {"queue": queue})


async def _rename_cart_title(cart: Cart, title: str) -> None:
    if title == cart.title:
        return
    if await Cart.from_title(g.dbsession, title):
        raise Conflict(f"Cart {title} already exists")
    cart.title = title
    if not cart.path:
        return
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
    await sync_cart_display_titles(g.dbsession, cart)


@blueprint.get("/carts.json")
@login_required
async def carts_json():
    carts = await Cart.all(g.dbsession, load_sounds=True, load_uploaders=True)
    return jsonify({"carts": [_cart_json(cart) for cart in carts]})


@blueprint.post("/carts.json")
@login_required
async def create_cart_json():
    deny = forbidden_unless("carts")
    if deny:
        return deny
    data = await read_json_object()
    title = str(data.get("title") or "").strip()
    if not title:
        raise BadRequest("Please provide a title")
    if await Cart.from_title(g.dbsession, title):
        raise Conflict(f"Cart {title} already exists")
    cart = Cart(title=title, notes=str(data.get("notes") or ""), url="")
    mode_raw = str(data.get("mode") or CartMode.PLAYLIST.value)
    cart.mode = _enum_from_json(CartMode, mode_raw, "mode")
    schedule_raw = str(data.get("schedule_mode") or ScheduleMode.TIMED.value)
    cart.schedule_mode = _enum_from_json(ScheduleMode, schedule_raw, "schedule_mode")
    _apply_cart_json(cart, data)
    g.dbsession.add(cart)
    await g.dbsession.commit()
    cart_root = current_app.config["DATA_ROOT"] / "carts"
    cart.path = safe_path(cart_root, title, prefix=str(cart.id))
    cart.path.mkdir(parents=True)
    await g.dbsession.commit()
    _poke_if_timed(cart)
    cart = await _load_cart_json(cart.id)
    return jsonify({"cart": _cart_json(cart)}), 201


@blueprint.put("/carts/<int:cart_id>.json")
@login_required
async def update_cart_json(cart_id: int):
    deny = forbidden_unless("carts")
    if deny:
        return deny
    cart = await _load_cart_json(cart_id)
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")
    data = await read_json_object()
    require_cart_version(cart, data.get("version"))
    if "title" in data:
        title = str(data.get("title") or "").strip()
        if not title:
            raise BadRequest("Please provide a title")
        await _rename_cart_title(cart, title)
    _apply_cart_json(cart, data)
    await g.dbsession.commit()
    _poke_if_timed(cart)
    cart = await _load_cart_json(cart.id)
    return jsonify({"cart": _cart_json(cart)})


@blueprint.delete("/carts/<int:cart_id>.json")
@login_required
async def delete_cart_json(cart_id: int):
    deny = forbidden_unless("carts")
    if deny:
        return deny
    cart = await Cart.from_id(g.dbsession, cart_id)
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")
    await assert_cart_deletable(g.dbsession, cart)
    if cart.path:
        shutil.rmtree(cart.path, ignore_errors=True)
    await g.dbsession.delete(cart)
    await g.dbsession.commit()
    return jsonify({"ok": True})


async def _ingest_json_uploads(cart_id: int, uploads: list) -> list[Sound]:
    """Keep audio files; skip junk from a dropped folder (.DS_Store, images…)."""
    added: list[Sound] = []
    rank = await Sound.next_rank(g.dbsession, cart_id)
    for uploaded_file in uploads:
        path = uploaded_file["uploaded_to"]
        try:
            metadata = mutagen.File(path)
        except Exception:
            metadata = None
        info = getattr(metadata, "info", None)
        length = getattr(info, "length", None)
        if metadata is None or length is None:
            with contextlib.suppress(OSError):
                unlink_cart_file(path)
            continue
        sound = Sound(
            cart_id=cart_id,
            rank=rank,
            title=uploaded_file["filename"],
            path=path,
            duration=int(length),
            uploader_id=current_user.user.id,
        )
        added.append(sound)
        g.dbsession.add(sound)
        rank += 1
    return added


@blueprint.post("/carts/<int:cart_id>/sounds.json")
@login_required
async def add_sounds_json(cart_id: int):
    deny = forbidden_unless("carts")
    if deny:
        return deny
    cart = await Cart.from_id(g.dbsession, cart_id)
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")
    payload = await request.get_json(silent=True)
    if isinstance(payload, dict) and payload.get("paths"):
        raw_paths = payload.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise BadRequest("paths list required")
        uploads = []
        for raw in raw_paths:
            path = resolve_bank_path(str(raw))
            uploads.append({"uploaded_to": path, "filename": path.name})
        added_sounds = await _ingest_json_uploads(cart_id, uploads)
    else:
        form = await stream_form(cart.path)
        uploads = form.getall("sounds") or form.getall("file")
        if not uploads:
            raise BadRequest("No sound file uploaded")
        added_sounds = await _ingest_json_uploads(cart_id, uploads)
    if not added_sounds:
        raise BadRequest("No audio file uploaded")
    await g.dbsession.commit()
    try:
        await Scheduler.get().queue_analysis([s.id for s in added_sounds])
    except RuntimeError:
        _log.debug("Scheduler is not initialized; skipping analysis queue")
    cart = await _load_cart_json(cart_id)
    return jsonify({"cart": _cart_json(cart)}), 201


@blueprint.post("/carts/<int:cart_id>/sounds/delete.json")
@login_required
async def delete_sounds_json(cart_id: int):
    deny = forbidden_unless("carts")
    if deny:
        return deny
    cart = await Cart.from_id(g.dbsession, cart_id)
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")
    data = await read_json_object()
    raw_ids = data.get("ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise BadRequest("ids list required")
    seen: set[int] = set()
    for raw in raw_ids:
        try:
            sound_id = int(raw)
        except (TypeError, ValueError) as exc:
            raise BadRequest("invalid sound id") from exc
        if sound_id in seen:
            continue
        seen.add(sound_id)
        sound = await Sound.from_id(g.dbsession, sound_id)
        if not sound or sound.cart_id != cart_id:
            raise NotFound(f"Sound {sound_id} not found")
        if sound.path:
            unlink_cart_file(sound.path)
        await g.dbsession.delete(sound)
    await g.dbsession.flush()
    await Sound.update_ranks(g.dbsession, cart_id)
    await g.dbsession.commit()
    cart = await _load_cart_json(cart_id)
    return jsonify({"cart": _cart_json(cart)})


@blueprint.delete("/carts/<int:cart_id>/sounds/<int:sound_id>.json")
@login_required
async def delete_sound_json(cart_id: int, sound_id: int):
    deny = forbidden_unless("carts")
    if deny:
        return deny
    sound = await Sound.from_id(g.dbsession, sound_id)
    if not sound or sound.cart_id != cart_id:
        raise NotFound(f"Sound {sound_id} not found")
    if sound.path:
        unlink_cart_file(sound.path)
    await g.dbsession.delete(sound)
    await g.dbsession.flush()
    await Sound.update_ranks(g.dbsession, cart_id)
    await g.dbsession.commit()
    cart = await _load_cart_json(cart_id)
    return jsonify({"cart": _cart_json(cart)})


@blueprint.put("/carts/<int:cart_id>/sounds/<int:sound_id>.json")
@login_required
async def update_sound_json(cart_id: int, sound_id: int):
    deny = forbidden_unless("carts")
    if deny:
        return deny
    sound = await Sound.from_id(g.dbsession, sound_id)
    if not sound or sound.cart_id != cart_id:
        raise NotFound(f"Sound {sound_id} not found")
    data = await read_json_object()
    if "title" in data:
        title = str(data.get("title") or "").strip()
        if not title:
            raise BadRequest("Please provide a title")
        sound.title = title
    if "active" in data:
        sound.active = bool(data.get("active"))
    await g.dbsession.commit()
    cart = await _load_cart_json(cart_id)
    return jsonify({"cart": _cart_json(cart), "sound": _sound_json(sound)})


@blueprint.put("/carts/<int:cart_id>/sounds/ranks.json")
@login_required
async def update_sound_ranks_json(cart_id: int):
    deny = forbidden_unless("carts")
    if deny:
        return deny
    cart = await Cart.from_id(g.dbsession, cart_id)
    if not cart:
        raise NotFound(f"Cart {cart_id} not found")
    data = await read_json_object()
    ranks = data.get("ranks")
    if not isinstance(ranks, list) or not ranks:
        raise BadRequest("ranks list required")
    rank = 1
    for raw_id in ranks:
        try:
            sound_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise BadRequest("invalid sound id") from exc
        sound = await Sound.from_id(g.dbsession, sound_id)
        if not sound:
            raise NotFound(f"Sound {sound_id} not found")
        if sound.cart_id != cart_id:
            raise BadRequest(f"Sound {sound_id} is not in cart {cart_id}")
        sound.rank = rank
        rank += 1
    await g.dbsession.commit()
    cart = await _load_cart_json(cart_id)
    return jsonify({"cart": _cart_json(cart)})


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
        await sync_cart_display_titles(g.dbsession, cart)

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
    await assert_cart_deletable(g.dbsession, cart)
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


@blueprint.post("/carts/<int:cart_id>/now.json")
@login_required
async def push_now_json(cart_id: int):
    if not current_user.user.can_live():
        return jsonify({"error": "forbidden"}), 403
    cart = await Cart.from_id(g.dbsession, cart_id)
    if not cart:
        return jsonify({"error": "not_found"}), 404
    try:
        await Scheduler.get().push_cart(cart_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@blueprint.post("/carts/<int:cart_id>/sounds/<int:sound_id>/now.json")
@login_required
async def push_sound_now_json(cart_id: int, sound_id: int):
    if not current_user.user.can_live():
        return jsonify({"error": "forbidden"}), 403
    cart = await Cart.from_id(g.dbsession, cart_id, load_sounds=True)
    if not cart:
        return jsonify({"error": "not_found"}), 404
    sound = next((row for row in cart.sounds if row.id == sound_id), None)
    if not sound:
        return jsonify({"error": "not_found"}), 404
    try:
        await Scheduler.get().push_sound(cart_id, sound_id)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


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

    unlink_cart_file(sound.path)
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

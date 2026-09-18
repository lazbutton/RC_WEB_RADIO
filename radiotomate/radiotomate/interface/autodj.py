import logging
from datetime import datetime
from random import randint, sample

from quart import Blueprint, g, jsonify, render_template, request, url_for
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from radiotomate.auth import login_required, permission_required
from radiotomate.beets import BeetsIntegration
from radiotomate.enums import DayNames
from radiotomate.interface import safe_int
from radiotomate.interface.json_util import forbidden_unless, read_json_object
from radiotomate.models import AutoDJSlot, Clock, MusicCategory
from radiotomate.scheduler.execution import published_rundown
from radiotomate.scheduler.rundown import (
    DEFAULT_HORIZON_MIN,
    MAX_HORIZON_MIN,
    MIN_HORIZON_MIN,
)
from radiotomate.scheduler_api import Scheduler
from radiotomate.services.autodj import (
    apply_clock,
    clock_payload,
    create_clock,
    delete_clock,
    delete_slot,
    save_slot,
    slot_payload,
)

_log = logging.getLogger(__name__)

blueprint = Blueprint("autodj", __name__, template_folder="templates")

REM_PER_HOUR = 4
MIN_PER_DAY = 24 * 60


def random_color():
    """
    Create a mid-saturated color
    """
    r = randint(100, 200)
    g = randint(100, 200)
    b = randint(100, 200)
    return f"#{r:02x}{g:02x}{b:02x}"


def process_filters(form: MultiDict, slot: AutoDJSlot):
    """
    Transform POSTed filters into a ``slot.constraints`` dict.

    This dict will contain the total of filters' weights, and a list of tuples: first
    item is the filter's weight, second  is the filter formular. Thus a weighted
    selector only has to pick an integer in [0, max], then iterate in the
    list to find the first couple having a cumulated weight greater than picked number.
    """
    weights = form.getlist("filters[][weight]")
    filters = form.getlist("filters[][filter]")
    totalweight = 0
    constraints = {
        "filters": [],
    }
    for i in range(len(weights)):
        query = filters[i].strip()
        weight = max(1, safe_int(weights[i], 1, True))
        totalweight += weight
        constraints["filters"].append([weight, query])
    constraints["totalweight"] = totalweight
    slot.constraints = constraints


async def _clock_choices():
    clocks = await Clock.all(g.dbsession)
    default = next((c for c in clocks if c.name == Clock.DEFAULT_NAME), None)
    if default is None and clocks:
        default = clocks[0]
    return clocks, default


def _clock_id_from_form(form: MultiDict, clocks: list[Clock], default: Clock | None):
    allowed = {c.id for c in clocks}
    parsed = safe_int(form.get("clock_id"), default.id if default else None, True)
    if parsed in allowed:
        return parsed
    return default.id if default else None


def _clock_payload(clock: Clock) -> dict:
    return clock_payload(clock)


@blueprint.get("/autodj")
@login_required
async def index():
    slots_per_day = {}
    for d in range(7):
        slots_per_day[d] = []
    for slot in await AutoDJSlot.all(g.dbsession):
        slots_per_day[slot.day_of_week].append(slot)
    for d in range(7):
        day = slots_per_day[d]
        day.append(None)  # because the loop updates "previous" - should be popped after
        previous = None

        # we want each entry to measure at least 1 REM high. In order to keep next ones
        # as much aligned as possible, we cumulate the created overflow, to maybe make
        # following longer slots thinner.
        overflow = 0
        for slot in day:
            if previous:
                if slot:
                    ending = slot.minute
                else:
                    ending = MIN_PER_DAY
                h = REM_PER_HOUR * ((ending - previous.minute) / 60)
                if h < 1:
                    overflow += 1 - h
                    h = 1
                else:
                    margin = h - 1
                    if overflow <= margin:
                        h -= overflow
                        overflow = 0
                    else:
                        h = 1
                        overflow -= margin

                previous.height = h

            previous = slot
        day.pop()  # remove the fake entry

    return await render_template(
        "autodj/index.jinja",
        slots_per_day=slots_per_day,
        day_names=DayNames,
        REM_PER_HOUR=REM_PER_HOUR,
    )


async def _conducteur_payload() -> dict:
    beets = BeetsIntegration.get()
    horizon = safe_int(request.args.get("horizon"), DEFAULT_HORIZON_MIN, True)
    horizon = horizon or DEFAULT_HORIZON_MIN
    horizon = min(MAX_HORIZON_MIN, max(MIN_HORIZON_MIN, horizon))
    try:
        scheduler = Scheduler.get()
    except RuntimeError:
        return await published_rundown(g.dbsession, beets, horizon_min=horizon)
    return await scheduler.live_rundown(g.dbsession, beets, horizon_min=horizon)


@blueprint.get("/autodj/conducteur.json")
@login_required
async def conducteur_json():
    return jsonify(await _conducteur_payload())


@blueprint.get("/autodj/clocks.json")
@login_required
async def clocks_json():
    clocks = await Clock.all(g.dbsession, load_positions=True)
    return jsonify({"clocks": [_clock_payload(c) for c in clocks]})


@blueprint.get("/autodj/slots.json")
@login_required
async def slots_json():
    clocks = await Clock.all(g.dbsession)
    names = {c.id: c.name for c in clocks}
    slots = [slot_payload(slot, names) for slot in await AutoDJSlot.all(g.dbsession)]
    return jsonify({"slots": slots})


@blueprint.get("/autodj/categories.json")
@login_required
async def categories_json():
    beets = BeetsIntegration.get()
    categories = []
    for cat in await MusicCategory.all(g.dbsession):
        matches = await beets.search(cat.query)
        categories.append(
            {
                "id": cat.id,
                "name": cat.name,
                "query": cat.query,
                "empty_query": cat.empty_query,
                "count": len(matches),
            }
        )
    return jsonify({"categories": categories})


def _slot_payload(slot: AutoDJSlot, names: dict[int, str]) -> dict:
    return slot_payload(slot, names)


async def _clock_names() -> dict[int, str]:
    clocks = await Clock.all(g.dbsession)
    return {c.id: c.name for c in clocks}


async def _apply_clock_fields(clock: Clock, data: dict, *, creating: bool) -> None:
    await apply_clock(g.dbsession, clock, data, creating=creating)


@blueprint.post("/autodj/clocks.json")
@login_required
async def create_clock_json():
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    data = await read_json_object()
    clock = await create_clock(g.dbsession, data)
    await g.dbsession.commit()
    clock = await Clock.from_id(g.dbsession, clock.id, load_positions=True)
    return jsonify({"clock": _clock_payload(clock)}), 201


@blueprint.put("/autodj/clocks/<int:clock_id>.json")
@login_required
async def update_clock_json(clock_id: int):
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    clock = await Clock.from_id(g.dbsession, clock_id, load_positions=True)
    if not clock:
        raise NotFound("Clock not found")
    data = await read_json_object()
    await _apply_clock_fields(clock, data, creating=False)
    await g.dbsession.commit()
    clock = await Clock.from_id(g.dbsession, clock.id, load_positions=True)
    return jsonify({"clock": _clock_payload(clock)})


@blueprint.delete("/autodj/clocks/<int:clock_id>.json")
@login_required
async def delete_clock_json(clock_id: int):
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    await delete_clock(g.dbsession, clock_id)
    await g.dbsession.commit()
    return jsonify({"ok": True})


@blueprint.post("/autodj/slots.json")
@login_required
async def create_slot_json():
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    data = await read_json_object()
    title = str(data.get("title") or "").strip()
    color = str(data.get("color") or "")
    if len(color) != 7:
        color = random_color()
    try:
        day_of_week = int(data.get("day_of_week"))
        minute = int(data.get("minute"))
    except (TypeError, ValueError) as exc:
        raise BadRequest("day_of_week and minute required") from exc
    clocks, default_clock = await _clock_choices()
    allowed = {c.id for c in clocks}
    clock_id = data.get("clock_id", default_clock.id if default_clock else None)
    if clock_id is not None:
        clock_id = int(clock_id)
        if clock_id not in allowed:
            raise BadRequest("unknown clock_id")
    slot = AutoDJSlot(constraints={"filters": [], "totalweight": 0})
    g.dbsession.add(slot)
    await save_slot(
        g.dbsession,
        slot,
        title=title,
        color=color,
        day_of_week=day_of_week,
        minute=minute,
        clock_id=clock_id,
    )
    await g.dbsession.commit()
    names = await _clock_names()
    return jsonify({"slot": _slot_payload(slot, names)}), 201


@blueprint.put("/autodj/slots/<int:slot_id>.json")
@login_required
async def update_slot_json(slot_id: int):
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    slot = await AutoDJSlot.from_id(g.dbsession, slot_id)
    if not slot:
        raise NotFound("Auto-DJ slot not found")
    data = await read_json_object()
    title = (
        str(data.get("title") or "").strip()
        if "title" in data
        else slot.title
    )
    next_color = slot.color
    color = data.get("color")
    if isinstance(color, str) and len(color) == 7:
        next_color = color
    day_of_week = int(data.get("day_of_week", slot.day_of_week))
    minute = int(data.get("minute", slot.minute))
    clock_id = slot.clock_id
    if "clock_id" in data:
        clocks, default_clock = await _clock_choices()
        allowed = {c.id for c in clocks}
        clock_id = data.get("clock_id")
        if clock_id is None:
            clock_id = default_clock.id if default_clock else None
        else:
            clock_id = int(clock_id)
            if clock_id not in allowed:
                raise BadRequest("unknown clock_id")
    await save_slot(
        g.dbsession,
        slot,
        title=title,
        color=next_color,
        day_of_week=day_of_week,
        minute=minute,
        clock_id=clock_id,
        expected_version=data.get("version"),
    )
    await g.dbsession.commit()
    names = await _clock_names()
    return jsonify({"slot": _slot_payload(slot, names)})


@blueprint.delete("/autodj/slots/<int:slot_id>.json")
@login_required
async def delete_slot_json(slot_id: int):
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    await delete_slot(g.dbsession, slot_id)
    await g.dbsession.commit()
    return jsonify({"ok": True})


def _category_payload(cat: MusicCategory, count: int) -> dict:
    return {
        "id": cat.id,
        "name": cat.name,
        "query": cat.query,
        "empty_query": cat.empty_query,
        "count": count,
    }


async def _category_count(cat: MusicCategory) -> int:
    beets = BeetsIntegration.get()
    matches = await beets.search(cat.query)
    return len(matches)


@blueprint.post("/autodj/categories.json")
@login_required
async def create_category_json():
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    data = await read_json_object()
    name = str(data.get("name") or "").strip()
    query = str(data.get("query") or "").strip()
    if not name or not query:
        raise BadRequest("name and query required")
    if await MusicCategory.from_name(g.dbsession, name):
        raise Conflict(f"Category {name} already exists")
    cat = MusicCategory(
        name=name,
        query=query,
        empty_query=str(data.get("empty_query") or ""),
    )
    g.dbsession.add(cat)
    await g.dbsession.commit()
    payload = _category_payload(cat, await _category_count(cat))
    return jsonify({"category": payload}), 201


@blueprint.put("/autodj/categories/<int:category_id>.json")
@login_required
async def update_category_json(category_id: int):
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    cat = await MusicCategory.from_id(g.dbsession, category_id)
    if not cat:
        raise NotFound("Category not found")
    data = await read_json_object()
    if "name" in data:
        name = str(data.get("name") or "").strip()
        if not name:
            raise BadRequest("name required")
        existing = await MusicCategory.from_name(g.dbsession, name)
        if existing and existing.id != cat.id:
            raise Conflict(f"Category {name} already exists")
        cat.name = name
    if "query" in data:
        query = str(data.get("query") or "").strip()
        if not query:
            raise BadRequest("query required")
        cat.query = query
    if "empty_query" in data:
        cat.empty_query = str(data.get("empty_query") or "")
    await g.dbsession.commit()
    return jsonify({"category": _category_payload(cat, await _category_count(cat))})


@blueprint.delete("/autodj/categories/<int:category_id>.json")
@login_required
async def delete_category_json(category_id: int):
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    cat = await MusicCategory.from_id(g.dbsession, category_id)
    if not cat:
        raise NotFound("Category not found")
    await g.dbsession.delete(cat)
    await g.dbsession.commit()
    return jsonify({"ok": True})


@blueprint.get("/autodj/testquery.json")
@login_required
async def testquery_json():
    query = request.args.get("query") or ""
    beets = BeetsIntegration.get()
    results = await beets.search(query)
    sample = []
    for item in results[:10]:
        sample.append(
            {
                "artist": str(getattr(item, "artist", "") or ""),
                "title": str(getattr(item, "title", "") or ""),
            }
        )
    return jsonify({"count": len(results), "sample": sample})


@blueprint.get("/autodj/conducteur")
@login_required
async def conducteur():
    payload = await _conducteur_payload()
    now_dt = datetime.fromisoformat(payload["now"])
    items = []
    for item in payload["items"]:
        at = datetime.fromisoformat(item["at"])
        row = dict(item)
        row["at_label"] = at.strftime("%H:%M:%S")
        if item.get("when") == "anchored" and item.get("minute") is not None:
            row["when_label"] = f":{int(item['minute']):02d}"
        else:
            row["when_label"] = "séquentiel"
        items.append(row)
    return await render_template(
        "autodj/conducteur.jinja",
        now_label=now_dt.strftime("%H:%M:%S"),
        clock=payload.get("clock") or "—",
        daypart=payload.get("daypart") or "—",
        horizon_min=payload.get("horizon_min", 30),
        items=items,
    )


@blueprint.get("/autodj/add")
@permission_required("autodj")
async def add_form():
    clocks, default_clock = await _clock_choices()
    return await render_template(
        "autodj/slot_form.jinja",
        color=random_color(),
        day_names=DayNames,
        clocks=clocks,
        default_clock=default_clock,
    )


@blueprint.post("/autodj")
@permission_required("autodj")
async def add():
    form = await request.form
    title = form.get("title")
    color = form.get("color")
    if len(color) != 7:
        color = random_color()
    hour = min(23, safe_int(form.get("hour"), 0, True))
    minute = 60 * hour + min(59, safe_int(form.get("minute"), 0, True))

    day_option = form.get("day_of_week")
    existing = None
    if not day_option:
        raise BadRequest("Please choose a day of week")
    elif day_option == "*":
        existing = await AutoDJSlot.from_time(g.dbsession, minute, exact=True)
        days_of_week = range(7)
    else:
        day_of_week = min(6, safe_int(day_option, 0, True))
        existing = await AutoDJSlot.from_time(
            g.dbsession, minute, day_of_week=day_of_week, exact=True
        )
        days_of_week = [day_of_week]

    if existing:
        if existing.title:
            hint = f"({existing.title}) "
        else:
            hint = ""
        raise Conflict(f"A slot {hint}is already scheduled at that time.")

    clocks, default_clock = await _clock_choices()
    clock_id = _clock_id_from_form(form, clocks, default_clock)
    for d in days_of_week:
        slot = AutoDJSlot(constraints={"filters": [], "totalweight": 0})
        process_filters(form, slot)
        g.dbsession.add(slot)
        await save_slot(
            g.dbsession,
            slot,
            title=title,
            color=color,
            day_of_week=d,
            minute=minute,
            clock_id=clock_id,
        )

    await g.dbsession.commit()
    return (
        "",
        200,
        {
            "HX-Redirect": url_for("autodj.index"),
        },
    )


@blueprint.get("/autodj/<int:slot_id>")
@permission_required("autodj")
async def get(slot_id: int):
    slot = await AutoDJSlot.from_id(g.dbsession, slot_id)
    if not slot:
        raise NotFound("Auto-DJ slot not found")
    clocks, default_clock = await _clock_choices()
    return await render_template(
        "autodj/slot_form.jinja",
        slot=slot,
        color=slot.color,
        day_names=DayNames,
        clocks=clocks,
        default_clock=default_clock,
    )


@blueprint.put("/autodj/<int:slot_id>")
@permission_required("autodj")
async def edit(slot_id: int):
    slot = await AutoDJSlot.from_id(g.dbsession, slot_id)
    if not slot:
        raise NotFound("Auto-DJ slot not found")

    form = await request.form
    title = form.get("title")
    color = form.get("color")
    if len(color) != 7:
        color = slot.color
    hour = min(23, safe_int(form.get("hour"), 0, True))
    minute = 60 * hour + min(59, safe_int(form.get("minute"), 0, True))
    day_of_week = min(6, safe_int(form.get("day_of_week"), 0, True))

    clocks, default_clock = await _clock_choices()
    clock_id = _clock_id_from_form(form, clocks, default_clock)

    process_filters(form, slot)
    await save_slot(
        g.dbsession,
        slot,
        title=title,
        color=color,
        day_of_week=day_of_week,
        minute=minute,
        clock_id=clock_id,
    )

    await g.dbsession.commit()
    return (
        "",
        200,
        {
            "HX-Redirect": url_for("autodj.index"),
        },
    )


@blueprint.delete("/autodj/<int:slot_id>")
@permission_required("autodj")
async def delete(slot_id: int):
    await delete_slot(g.dbsession, slot_id)
    await g.dbsession.commit()

    return (
        "",
        200,
        {
            "HX-Redirect": url_for("autodj.index"),
        },
    )


@blueprint.get("/autodj/testquery")
@login_required
async def testquery():
    query = request.args.get("filters[][filter]")
    beets = BeetsIntegration.get()
    results = await beets.search(query)
    nb_results = len(results)
    if nb_results > 10:
        results = sample(results, k=10)
    return await render_template(
        "autodj/testquery.jinja",
        query=query,
        results=results,
        nb_results=nb_results,
    )

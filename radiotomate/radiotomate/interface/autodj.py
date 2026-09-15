import logging
from random import randint, sample

from quart import Blueprint, g, render_template, request, url_for
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import BadRequest, Conflict, NotFound

from radiotomate.auth import login_required, permission_required
from radiotomate.beets import BeetsIntegration
from radiotomate.enums import DayNames
from radiotomate.interface import safe_int
from radiotomate.models import AutoDJSlot

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


@blueprint.get("/autodj/add")
@permission_required("autodj")
async def add_form():
    return await render_template(
        "autodj/slot_form.jinja", color=random_color(), day_names=DayNames
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

    for d in days_of_week:
        slot = AutoDJSlot(title=title, color=color, day_of_week=d, minute=minute)
        process_filters(form, slot)
        g.dbsession.add(slot)

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
    return await render_template(
        "autodj/slot_form.jinja", slot=slot, color=slot.color, day_names=DayNames
    )


@blueprint.put("/autodj/<int:slot_id>")
@permission_required("autodj")
async def edit(slot_id: int):
    slot = await AutoDJSlot.from_id(g.dbsession, slot_id)
    if not slot:
        raise NotFound("Auto-DJ slot not found")

    form = await request.form
    slot.title = form.get("title")
    color = form.get("color")
    if len(color) == 7:
        slot.color = color
    hour = min(23, safe_int(form.get("hour"), 0, True))
    minute = 60 * hour + min(59, safe_int(form.get("minute"), 0, True))
    day_of_week = min(6, safe_int(form.get("day_of_week"), 0, True))

    existing = await AutoDJSlot.from_time(
        g.dbsession, minute, day_of_week=day_of_week, exact=True
    )
    if existing and existing.id != slot.id:
        if existing.title:
            hint = f"({existing.title}) "
        else:
            hint = ""
        raise Conflict(f"A slot {hint}is already scheduled at that time.")

    slot.minute = minute
    slot.day_of_week = day_of_week

    process_filters(form, slot)

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
    slot = await AutoDJSlot.from_id(g.dbsession, slot_id)
    if not slot:
        raise NotFound("Auto-DJ slot not found")
    await g.dbsession.delete(slot)
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

"""JSON admin routes for live show overlays."""

from __future__ import annotations

from quart import Blueprint, g, jsonify

from radiotomate.auth import login_required
from radiotomate.domain.emission import is_harbor_source
from radiotomate.domain.errors import DomainNotFound, DomainValidationError
from radiotomate.interface.json_util import forbidden_unless, read_json_object
from radiotomate.interface.live import get_live_snapshot
from radiotomate.models.emission import Emission
from radiotomate.services.emissions import (
    SCOPE_SESSION,
    SCOPE_WEEKLY,
    create_session,
    create_weekly,
    delete_session,
    delete_weekly,
    emission_payload,
    list_emissions_payload,
    update_weekly,
)

blueprint = Blueprint("emissions", __name__)


def _live_source() -> str:
    return str(get_live_snapshot().get("source") or "")


@blueprint.get("/antenne/emissions.json")
@login_required
async def emissions_json():
    payload = await list_emissions_payload(g.dbsession, _live_source())
    dirty = payload.pop("_dirty", False)
    if dirty:
        await g.dbsession.commit()
    return jsonify(payload)


@blueprint.post("/antenne/emissions.json")
@login_required
async def create_emission_json():
    data = await read_json_object()
    scope = str(data.get("scope") or "").strip()
    if not scope:
        scope = SCOPE_WEEKLY if "day_of_week" in data else SCOPE_SESSION
    if scope == SCOPE_SESSION:
        deny = forbidden_unless("live")
        if deny:
            return deny
        row = await create_session(
            g.dbsession,
            data,
            harbor=is_harbor_source(_live_source()),
        )
        await g.dbsession.commit()
        return jsonify({"emission": emission_payload(row)}), 201
    if scope != SCOPE_WEEKLY:
        raise DomainValidationError(
            "Le type doit etre weekly ou session.",
            field="scope",
        )
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    row = await create_weekly(g.dbsession, data)
    await g.dbsession.commit()
    return jsonify({"emission": emission_payload(row)}), 201


@blueprint.put("/antenne/emissions/<int:emission_id>.json")
@login_required
async def update_emission_json(emission_id: int):
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    row = await Emission.from_id(g.dbsession, emission_id)
    if row is None:
        raise DomainNotFound("Émission introuvable.", field="id")
    data = await read_json_object()
    row = await update_weekly(g.dbsession, row, data)
    await g.dbsession.commit()
    return jsonify({"emission": emission_payload(row)})


@blueprint.delete("/antenne/emissions/session.json")
@login_required
async def delete_session_json():
    deny = forbidden_unless("live")
    if deny:
        return deny
    await delete_session(g.dbsession)
    await g.dbsession.commit()
    return jsonify({"ok": True})


@blueprint.delete("/antenne/emissions/<int:emission_id>.json")
@login_required
async def delete_emission_json(emission_id: int):
    row = await Emission.from_id(g.dbsession, emission_id)
    if row is None:
        raise DomainNotFound("Émission introuvable.", field="id")
    if row.scope == SCOPE_SESSION:
        deny = forbidden_unless("live")
        if deny:
            return deny
        await delete_session(g.dbsession)
        await g.dbsession.commit()
        return jsonify({"ok": True})
    deny = forbidden_unless("autodj")
    if deny:
        return deny
    await delete_weekly(g.dbsession, emission_id)
    await g.dbsession.commit()
    return jsonify({"ok": True})

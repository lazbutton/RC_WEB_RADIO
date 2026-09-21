from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from regie.kernel.api import Problem, require
from regie.kernel.jobs import P_USER
from regie.modules.events.service import COVERAGE_KINDS, EventsService


class CoverIn(BaseModel):
    kind: str = "annonce"
    assignee_id: str | None = None
    notes: str = ""
    mail_id: int | None = None


class CoveragePatch(BaseModel):
    kind: str | None = None
    status: str | None = None
    assignee_id: str | None = None
    notes: str | None = None


def build_router(service: EventsService) -> APIRouter:
    r = APIRouter(prefix="/api/v1/events", tags=["events"])
    read = require("events", "read")
    write = require("events", "write")
    k = service.kernel

    @r.get("")
    def list_events(start: str | None = None, end: str | None = None, q: str = "", radio: int = 0, organizer_id: str = "", venue_id: str = "", limit: int = 300, _user: dict = Depends(read)):
        rows = service.list(start=start, end=end, q=q, only_radio=bool(radio), organizer_id=organizer_id, venue_id=venue_id, limit=max(1, min(1000, limit)))
        covers = service.coverage_for([row["id"] for row in rows])
        for row in rows:
            row["coverage"] = covers.get(row["id"], [])
        state = k.connectors.state.get("outlive")
        return {"ok": True, "events": rows, "connector": {"status": state.get("status"), "last_ok_at": state.get("last_ok_at"), "last_error": state.get("last_error")}, "coverage_kinds": list(COVERAGE_KINDS)}

    @r.post("/refresh", status_code=202)
    def refresh(_user: dict = Depends(write)):
        if not service.connector.configured():
            raise Problem(503, "Outlive non configuré (clé partenaire ou clé anonyme).")
        return {"ok": True, "job": k.jobs.submit("events.refresh", {}, P_USER, dedupe=True)}

    @r.get("/coverage")
    def coverage(status: str = "", _user: dict = Depends(read)):
        where, params = ("status = %s", [status]) if status else ("", [])
        rows = service.coverage.list(where, params, 500)
        events = {e["id"]: e for e in service.many(list({row["event_id"] for row in rows}))}
        for row in rows:
            row["event"] = events.get(row["event_id"])
        return {"ok": True, "coverage": rows}

    @r.patch("/coverage/{cov_id}")
    def patch_coverage(cov_id: int, body: CoveragePatch, _user: dict = Depends(write)):
        data: dict[str, Any] = {key: value for key, value in body.model_dump().items() if value is not None}
        if data.get("kind") and data["kind"] not in COVERAGE_KINDS:
            raise Problem(400, "type de couverture inconnu")
        if data.get("status") and data["status"] not in {"idea", "planned", "done", "skipped"}:
            raise Problem(400, "statut inconnu")
        row = service.update_coverage(cov_id, data)
        if row is None:
            raise Problem(404, "couverture introuvable")
        return {"ok": True, "coverage": row}

    @r.get("/{event_id}")
    def get_event(event_id: str, _user: dict = Depends(read)):
        row = service.get(event_id)
        if not row:
            raise Problem(404, "événement introuvable")
        links = k.links.of("event", event_id)
        summaries = k.registry.summaries([(str(l["other_kind"]), str(l["other_id"])) for l in links])
        for link in links:
            link["other"] = summaries.get(f"{link['other_kind']}:{link['other_id']}")
        return {"ok": True, "event": row, "coverage": service.coverage_for([event_id]).get(event_id, []), "links": links, "actions": k.actions.recent(10, entity_kind="event", entity_id=event_id)}

    @r.post("/{event_id}/cover")
    def cover(event_id: str, body: CoverIn, user: dict = Depends(write)):
        if body.kind not in COVERAGE_KINDS:
            raise Problem(400, "type de couverture inconnu")
        action = k.actions.perform("events.cover", [event_id], {k2: v for k2, v in body.model_dump().items() if v is not None}, actor_id=user["id"], label=f"Couvrir : {body.kind}")
        if action.get("status") == "failed":
            raise Problem(400, action.get("error") or "couverture impossible")
        return {"ok": True, "action": action, "coverage": service.coverage_for([event_id]).get(event_id, [])}

    return r

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from regie.kernel.api import Problem, require
from regie.modules.radio.service import CHANNELS, GUEST_FLOW, RadioService


class DataIn(BaseModel):
    data: dict[str, Any]


class StatusIn(BaseModel):
    status: str


class TickIn(BaseModel):
    done: bool = True
    text: str | None = None


def build_router(service: RadioService) -> APIRouter:
    r = APIRouter(prefix="/api/v1/radio", tags=["radio"])
    read = require("radio", "read")
    write = require("radio", "write")
    k = service.kernel

    # invités

    @r.get("/guests")
    def guests(_user: dict = Depends(read)):
        return {"ok": True, "pipeline": service.guest_pipeline(), "flow": {key: sorted(value) for key, value in GUEST_FLOW.items()}}

    @r.post("/guests", status_code=201)
    def create_guest(body: DataIn, user: dict = Depends(write)):
        try:
            return {"ok": True, "guest": service.create_guest(body.data, actor=user["id"])}
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc

    @r.get("/guests/{guest_id}")
    def guest(guest_id: int, _user: dict = Depends(read)):
        row = service.guests.get(guest_id)
        if not row:
            raise Problem(404, "invité introuvable")
        return {"ok": True, "guest": row, "links": k.links.of("guest", guest_id), "actions": k.actions.recent(10, entity_kind="guest", entity_id=str(guest_id))}

    @r.patch("/guests/{guest_id}")
    def patch_guest(guest_id: int, body: DataIn, _user: dict = Depends(write)):
        row = service.update_guest(guest_id, body.data)
        if not row:
            raise Problem(404, "invité introuvable")
        return {"ok": True, "guest": row}

    @r.post("/guests/{guest_id}/status")
    def guest_status(guest_id: int, body: StatusIn, user: dict = Depends(write)):
        if body.status not in GUEST_FLOW:
            raise Problem(400, "étape inconnue")
        action = k.actions.perform("radio.guest_status", [str(guest_id)], {"status": body.status}, actor_id=user["id"], label=f"Invité → {body.status}")
        if action.get("status") == "failed":
            raise Problem(409, action.get("error") or "changement refusé")
        return {"ok": True, "action": action, "guest": service.guests.get(guest_id)}

    @r.post("/guests/{guest_id}/authorization.pdf")
    def guest_pdf(guest_id: int, _user: dict = Depends(write)):
        try:
            rel, data = service.guest_authorization_pdf(guest_id)
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc
        return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{rel.rsplit("/", 1)[-1]}"', "X-Regie-Path": rel})

    # réservations

    @r.get("/resources")
    def resources(_user: dict = Depends(read)):
        return {"ok": True, "resources": service.resources.list("", [], 100)}

    @r.post("/resources", status_code=201)
    def create_resource(body: DataIn, _user: dict = Depends(require("radio", "admin"))):
        if not body.data.get("name"):
            raise Problem(400, "nom requis")
        return {"ok": True, "resource": service.resources.create(body.data)}

    @r.get("/bookings")
    def bookings(start: str, end: str, _user: dict = Depends(read)):
        return {"ok": True, "bookings": service.bookings_between(start, end)}

    @r.post("/bookings", status_code=201)
    def book(body: DataIn, user: dict = Depends(write)):
        try:
            return {"ok": True, "booking": service.book(body.data, user["id"])}
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        except PermissionError as exc:
            raise Problem(409, str(exc)) from exc

    @r.delete("/bookings/{booking_id}")
    def cancel(booking_id: int, user: dict = Depends(write)):
        try:
            ok = service.cancel_booking(booking_id, user)
        except PermissionError as exc:
            raise Problem(403, str(exc)) from exc
        if not ok:
            raise Problem(404, "réservation introuvable")
        return {"ok": True}

    # volontaires, partenariats

    @r.get("/volunteers")
    def volunteers(_user: dict = Depends(read)):
        rows = service.volunteers.list("", [], 300)
        contacts = k.modules.get("contacts")
        people = {p["id"]: p for p in contacts.people.many([row["person_id"] for row in rows])} if contacts else {}
        for row in rows:
            row["person"] = people.get(row["person_id"])
        return {"ok": True, "volunteers": rows}

    @r.post("/volunteers", status_code=201)
    def create_volunteer(body: DataIn, _user: dict = Depends(write)):
        if not body.data.get("person_id"):
            raise Problem(400, "personne requise")
        return {"ok": True, "volunteer": service.create_volunteer(body.data)}

    @r.patch("/volunteers/{volunteer_id}")
    def patch_volunteer(volunteer_id: int, body: DataIn, _user: dict = Depends(write)):
        row = service.volunteers.update(volunteer_id, body.data)
        if not row:
            raise Problem(404, "volontaire introuvable")
        return {"ok": True, "volunteer": row}

    @r.post("/volunteers/{volunteer_id}/attestation.pdf")
    def volunteer_pdf(volunteer_id: int, _user: dict = Depends(write)):
        try:
            rel, data = service.volunteer_attestation(volunteer_id)
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc
        return Response(data, media_type="application/pdf", headers={"X-Regie-Path": rel})

    @r.get("/partnerships")
    def partnerships(_user: dict = Depends(read)):
        rows = service.partnerships.list("", [], 300)
        contacts = k.modules.get("contacts")
        orgs = {o["id"]: o for o in contacts.organizations.many([row["organization_id"] for row in rows])} if contacts else {}
        for row in rows:
            row["organization"] = orgs.get(row["organization_id"])
        return {"ok": True, "partnerships": rows}

    @r.post("/partnerships", status_code=201)
    def create_partnership(body: DataIn, _user: dict = Depends(write)):
        if not body.data.get("organization_id") or not body.data.get("title"):
            raise Problem(400, "structure et titre requis")
        return {"ok": True, "partnership": service.create_partnership(body.data)}

    @r.patch("/partnerships/{partnership_id}")
    def patch_partnership(partnership_id: int, body: DataIn, _user: dict = Depends(write)):
        row = service.partnerships.update(partnership_id, body.data)
        if not row:
            raise Problem(404, "partenariat introuvable")
        return {"ok": True, "partnership": row}

    # conducteur

    @r.get("/rundowns")
    def rundowns(show_id: int | None = None, _user: dict = Depends(read)):
        where, params = ("show_id = %s", [show_id]) if show_id else ("", [])
        return {"ok": True, "rundowns": service.rundowns.list(where, params, 100)}

    @r.post("/rundowns", status_code=201)
    def create_rundown(body: DataIn, user: dict = Depends(write)):
        return {"ok": True, "rundown": service.save_rundown(body.data, actor=user["id"])}

    @r.get("/rundowns/{rundown_id}")
    def rundown(rundown_id: int, _user: dict = Depends(read)):
        row = service.rundowns.get(rundown_id)
        if not row:
            raise Problem(404, "conducteur introuvable")
        row["total_s"] = sum(float(i.get("duration_s") or 0) for i in row.get("items") or [])
        return {"ok": True, "rundown": row}

    @r.put("/rundowns/{rundown_id}")
    def update_rundown(rundown_id: int, body: DataIn, _user: dict = Depends(write)):
        try:
            return {"ok": True, "rundown": service.save_rundown(body.data, rundown_id)}
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc

    # valorisation, écoute

    @r.get("/promotions")
    def promotions(entity_kind: str, entity_id: str, _user: dict = Depends(read)):
        if not k.registry.has(entity_kind):
            raise Problem(400, "type inconnu")
        return {"ok": True, "promotions": service.ensure_promotions(entity_kind, entity_id), "channels": list(CHANNELS)}

    @r.post("/promotions/{promotion_id}")
    def tick(promotion_id: int, body: TickIn, user: dict = Depends(write)):
        row = service.tick_promotion(promotion_id, body.done, user["id"], body.text)
        if not row:
            raise Problem(404, "sortie introuvable")
        return {"ok": True, "promotion": row}

    @r.get("/listening")
    def listening(days: int = 7, _user: dict = Depends(read)):
        return {"ok": True, **service.listening_summary(max(1, min(90, days))), "configured": bool(k.setting("radio.icecast_url"))}

    return r

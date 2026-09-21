from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from regie.kernel.api import Problem, require
from regie.modules.contacts.service import KINDS, ContactsService


class EntityIn(BaseModel):
    data: dict[str, Any]


class MergeIn(BaseModel):
    kind: str = "person"
    keep: int
    others: list[int]


class AffiliationIn(BaseModel):
    person_id: int
    organization_id: int
    role: str = ""


class InteractionIn(BaseModel):
    kind: str = "note"
    person_id: int | None = None
    organization_id: int | None = None
    summary: str = ""
    at: str | None = None


class ImportIn(BaseModel):
    vcards: str = ""


class FromMailIn(BaseModel):
    ids: list[int]


def build_router(service: ContactsService) -> APIRouter:
    r = APIRouter(prefix="/api/v1/contacts", tags=["contacts"])
    read = require("contacts", "read")
    write = require("contacts", "write")
    k = service.kernel

    def _kind(kind: str) -> str:
        if kind not in KINDS:
            raise Problem(404, f"type inconnu : {kind}")
        return kind

    @r.get("/duplicates")
    def duplicates(kind: str = "person", _user: dict = Depends(read)):
        return {"ok": True, "groups": service.duplicates(_kind(kind))}

    @r.post("/merge")
    def merge(body: MergeIn, user: dict = Depends(write)):
        try:
            action = service.merge(_kind(body.kind), body.keep, body.others, actor=user["id"])
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        if action.get("status") == "failed":
            raise Problem(400, action.get("error") or "fusion impossible")
        return {"ok": True, "action": action}

    @r.post("/from-mail")
    def from_mail(body: FromMailIn, user: dict = Depends(write)):
        action = k.actions.perform("contacts.create_from_mail", [str(i) for i in body.ids], {}, actor_id=user["id"])
        if action.get("status") == "failed":
            raise Problem(400, action.get("error") or "création impossible")
        people = service.people.many(action.get("after", {}).get("people") or [])
        return {"ok": True, "action": action, "people": people}

    @r.post("/import/vcards")
    def import_vcards(body: ImportIn, _user: dict = Depends(write)):
        if not body.vcards.strip():
            raise Problem(400, "aucune vCard")
        return {"ok": True, **service.import_vcards(body.vcards)}

    @r.post("/import/senders", status_code=202)
    def import_senders(_user: dict = Depends(write)):
        return {"ok": True, "job": k.jobs.submit("contacts.import_senders", {"min_count": 2}, 1, dedupe=True)}

    @r.get("/export.vcf", response_class=PlainTextResponse)
    def export_vcf(_user: dict = Depends(read)):
        return PlainTextResponse(service.export_vcards(), media_type="text/vcard; charset=utf-8", headers={"Content-Disposition": "attachment; filename=regie-contacts.vcf"})

    @r.get("/export.csv", response_class=PlainTextResponse)
    def export_csv(kind: str = "person", _user: dict = Depends(read)):
        return PlainTextResponse(service.export_csv(_kind(kind)), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=regie-{kind}.csv"})

    @r.post("/affiliations", status_code=201)
    def affiliate(body: AffiliationIn, _user: dict = Depends(write)):
        return {"ok": True, "affiliation": service.affiliate(body.person_id, body.organization_id, body.role)}

    @r.delete("/affiliations/{affiliation_id}")
    def unaffiliate(affiliation_id: int, _user: dict = Depends(write)):
        return {"ok": service.unaffiliate(affiliation_id)}

    @r.post("/interactions", status_code=201)
    def interaction(body: InteractionIn, user: dict = Depends(write)):
        if not body.person_id and not body.organization_id:
            raise Problem(400, "personne ou structure requise")
        service.record_interaction(body.kind, person_id=body.person_id, organization_id=body.organization_id, summary=body.summary, at=body.at, actor=user["id"])
        return {"ok": True}

    @r.get("/{kind}")
    def list_kind(kind: str, q: str = "", limit: int = 200, offset: int = 0, _user: dict = Depends(read)):
        rows = service.list(_kind(kind), q, max(1, min(500, limit)), max(0, offset))
        return {"ok": True, "kind": kind, "items": rows, "total": service.tables[kind].count("merged_into IS NULL")}

    @r.post("/{kind}", status_code=201)
    def create(kind: str, body: EntityIn, user: dict = Depends(write)):
        try:
            return {"ok": True, "entity": service.create(_kind(kind), body.data, actor=user["id"])}
        except Exception as exc:
            raise Problem(400, str(exc)[:200]) from exc

    @r.get("/{kind}/{ident}")
    def get(kind: str, ident: int, _user: dict = Depends(read)):
        view = service.view_360(_kind(kind), ident)
        if view is None:
            raise Problem(404, "fiche introuvable")
        return {"ok": True, **view}

    @r.patch("/{kind}/{ident}")
    def patch(kind: str, ident: int, body: EntityIn, _user: dict = Depends(write)):
        row = service.update(_kind(kind), ident, body.data)
        if row is None:
            raise Problem(404, "fiche introuvable")
        return {"ok": True, "entity": row}

    @r.delete("/{kind}/{ident}")
    def delete(kind: str, ident: int, request: Request, _user: dict = Depends(require("contacts", "admin"))):
        if _kind(kind) == "person":
            ok = service.erase_person(ident)
        else:
            ok = service.delete(kind, ident)
        if not ok:
            raise Problem(404, "fiche introuvable")
        return {"ok": True}

    return r

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from regie.kernel.api import Problem, current_user, require
from regie.kernel.jobs import P_USER
from regie.modules.calendar.service import CalendarService


class EventIn(BaseModel):
    account_id: int
    title: str
    starts_at: str
    ends_at: str | None = None
    all_day: bool = False
    description: str = ""
    location: str = ""


class EventPatch(BaseModel):
    title: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    all_day: bool | None = None
    description: str | None = None
    location: str | None = None


class IcsIn(BaseModel):
    url: str
    label: str = ""
    shared: bool = False


class AccountPatch(BaseModel):
    label: str | None = None
    color: str | None = None
    shared: bool | None = None
    enabled: bool | None = None
    calendar_id: str | None = None
    ics_url: str | None = None


def build_router(service: CalendarService) -> APIRouter:
    r = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])
    read = require("calendar", "read")
    write = require("calendar", "write")
    k = service.kernel

    @r.get("/accounts")
    def accounts(user: dict = Depends(read)):
        return {"ok": True, "accounts": service.accounts(user["id"] if user["role"] != "admin" else None), "google_ready": service.google.configured()}

    @r.get("/google/authorize")
    def authorize(request: Request, shared: int = 0, label: str = "", user: dict = Depends(write)):
        try:
            return {"ok": True, "url": service.begin_oauth(user["id"], bool(shared), label)}
        except RuntimeError as exc:
            raise Problem(503, str(exc)) from exc

    @r.get("/google/callback")
    def callback(state: str = "", code: str = "", error: str = "", user: dict = Depends(current_user)):
        if error or not code:
            return RedirectResponse("/planning/week?google=refused", status_code=303)
        try:
            service.finish_oauth(state, code)
        except PermissionError as exc:
            raise Problem(400, str(exc)) from exc
        except RuntimeError as exc:
            raise Problem(502, str(exc)) from exc
        return RedirectResponse("/planning/week?google=ok", status_code=303)

    @r.post("/accounts/ics", status_code=201)
    def add_ics(body: IcsIn, user: dict = Depends(write)):
        if not body.url.startswith("http"):
            raise Problem(400, "URL ICS invalide")
        return {"ok": True, "account": service.add_ics(user["id"], body.url, body.label, body.shared)}

    @r.patch("/accounts/{account_id}")
    def patch_account(account_id: int, body: AccountPatch, user: dict = Depends(write)):
        account = service.account(account_id)
        if not account:
            raise Problem(404, "agenda introuvable")
        if user["role"] != "admin" and str(account.get("user_id")) != user["id"]:
            raise Problem(403, "cet agenda n'est pas le tien")
        return {"ok": True, "account": service.update_account(account_id, **body.model_dump())}

    @r.delete("/accounts/{account_id}")
    def delete_account(account_id: int, user: dict = Depends(write)):
        account = service.account(account_id)
        if not account:
            raise Problem(404, "agenda introuvable")
        if user["role"] != "admin" and str(account.get("user_id")) != user["id"]:
            raise Problem(403, "cet agenda n'est pas le tien")
        return {"ok": service.remove_account(account_id)}

    @r.get("/accounts/{account_id}/calendars")
    def calendars(account_id: int, _user: dict = Depends(read)):
        try:
            return {"ok": True, "calendars": service.calendars_of(account_id)}
        except Exception as exc:
            raise Problem(502, str(exc)[:200]) from exc

    @r.post("/accounts/{account_id}/sync", status_code=202)
    def sync(account_id: int, _user: dict = Depends(write)):
        if not service.account(account_id):
            raise Problem(404, "agenda introuvable")
        return {"ok": True, "job": k.jobs.submit("calendar.sync", {"account_id": account_id}, P_USER, dedupe=True)}

    @r.post("/accounts/{account_id}/confirm-bulk", status_code=202)
    def confirm_bulk(account_id: int, _user: dict = Depends(write)):
        if not service.account(account_id):
            raise Problem(404, "agenda introuvable")
        return {"ok": True, "job": service.confirm_bulk(account_id)}

    @r.get("/events")
    def events(start: str, end: str, user: dict = Depends(read)):
        return {"ok": True, "events": service.range(start, end, None if user["role"] == "admin" else user["id"])}

    @r.post("/events", status_code=201)
    def create(body: EventIn, user: dict = Depends(write)):
        try:
            return {"ok": True, "event": service.create(body.account_id, body.model_dump(), actor=user["id"])}
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc
        except (ValueError, PermissionError) as exc:
            raise Problem(400, str(exc)) from exc

    @r.get("/events/{event_id}")
    def get_event(event_id: int, _user: dict = Depends(read)):
        row = service.get(event_id)
        if not row:
            raise Problem(404, "rendez-vous introuvable")
        return {"ok": True, "event": row, "history": service.history(event_id), "links": k.links.of("calendar_event", event_id)}

    @r.patch("/events/{event_id}")
    def patch_event(event_id: int, body: EventPatch, user: dict = Depends(write)):
        row = service.update(event_id, {key: value for key, value in body.model_dump().items() if value is not None}, actor=user["id"])
        if not row:
            raise Problem(404, "rendez-vous introuvable")
        return {"ok": True, "event": row}

    @r.delete("/events/{event_id}")
    def delete_event(event_id: int, user: dict = Depends(write)):
        if not service.get(event_id):
            raise Problem(404, "rendez-vous introuvable")
        return {"ok": True, "action": k.actions.perform("calendar.delete", [str(event_id)], {}, actor_id=user["id"])}

    return r

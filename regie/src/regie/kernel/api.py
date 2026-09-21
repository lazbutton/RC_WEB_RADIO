from __future__ import annotations

import asyncio
import queue
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from regie.kernel.core import Kernel
from regie.kernel.events import sse_format

COOKIE = "regie"


class Problem(HTTPException):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(status_code=status, detail={"error": message, **extra})


def kernel_of(request: Request) -> Kernel:
    return request.app.state.kernel


def current_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise Problem(401, "auth")
    return user


def require(module: str, level: str):
    """Dépendance FastAPI : vérifie le niveau de permission sur un module."""

    def _dep(request: Request) -> dict[str, Any]:
        user = current_user(request)
        if not kernel_of(request).auth.allows(user, module, level):
            raise Problem(403, f"accès {level} refusé sur {module}")
        return user

    return _dep


def require_admin(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if user.get("role") != "admin":
        raise Problem(403, "réservé aux administrateurs")
    return user


# --- modèles ------------------------------------------------------------------------


class LoginIn(BaseModel):
    email: str = ""
    password: str = ""


class MeIn(BaseModel):
    name: str | None = None
    prefs: dict[str, Any] | None = None
    password: str | None = None
    current_password: str | None = None


class UserIn(BaseModel):
    email: str
    name: str = ""
    password: str
    role: str = "membre"


class UserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    disabled: bool | None = None
    password: str | None = None


class PermissionIn(BaseModel):
    role: str
    module: str
    level: str


class LinkIn(BaseModel):
    src_kind: str
    src_id: str
    dst_kind: str
    dst_id: str
    role: str = ""


class ActionIn(BaseModel):
    kind: str
    ids: list[str]
    params: dict[str, Any] = {}
    label: str | None = None


class ReadIn(BaseModel):
    ids: list[int] | None = None


class PushIn(BaseModel):
    subscription: dict[str, Any]


class SettingsPatch(BaseModel):
    values: dict[str, str]


class ScanIn(BaseModel):
    path: str = ""


# --- routeur noyau -------------------------------------------------------------------


def build_router() -> APIRouter:
    r = APIRouter(prefix="/api/v1")

    # auth

    @r.post("/auth/login")
    def login(body: LoginIn, request: Request):
        k = kernel_of(request)
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
        try:
            result = k.auth.login(body.email, body.password, ip, request.headers.get("user-agent", ""))
        except PermissionError as exc:
            raise Problem(429, str(exc)) from exc
        if not result:
            raise Problem(401, "Identifiants invalides.")
        token, user = result
        response = JSONResponse({"ok": True, "user": user})
        response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=request.url.scheme == "https", max_age=60 * 60 * 24 * k.settings.regie_session_days, path="/")
        k.metrics.inc("logins")
        return response

    @r.post("/auth/logout")
    def logout(request: Request):
        token = request.cookies.get(COOKIE, "")
        if token:
            kernel_of(request).auth.logout(token)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @r.get("/me")
    def me(request: Request, user: dict = Depends(current_user)):
        k = kernel_of(request)
        return {"ok": True, "user": user, "permissions": k.auth.matrix().get(user["role"], {}), "unread_notifications": k.notifications.unread_count(user["id"])}

    @r.patch("/me")
    def patch_me(body: MeIn, request: Request, user: dict = Depends(current_user)):
        k = kernel_of(request)
        if body.password is not None:
            row = k.auth.user_by_email(user["email"])
            from regie.kernel.auth import verify_password

            if not row or not verify_password(body.current_password or "", str(row.get("password_hash") or "")):
                raise Problem(400, "Mot de passe actuel incorrect.")
        try:
            updated = k.auth.update_user(user["id"], name=body.name, prefs=body.prefs, password=body.password)
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        return {"ok": True, "user": updated}

    # utilisateurs et permissions (admin)

    @r.get("/users")
    def users(request: Request, _admin: dict = Depends(require_admin)):
        return {"ok": True, "users": kernel_of(request).auth.users()}

    @r.post("/users", status_code=201)
    def create_user(body: UserIn, request: Request, _admin: dict = Depends(require_admin)):
        k = kernel_of(request)
        if k.auth.user_by_email(body.email):
            raise Problem(409, "Cette adresse a déjà un compte.")
        try:
            return {"ok": True, "user": k.auth.create_user(body.email, body.name, body.password, body.role)}
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc

    @r.patch("/users/{user_id}")
    def patch_user(user_id: str, body: UserPatch, request: Request, admin: dict = Depends(require_admin)):
        k = kernel_of(request)
        if user_id == admin["id"] and body.disabled:
            raise Problem(400, "Impossible de se désactiver soi-même.")
        try:
            updated = k.auth.update_user(user_id, name=body.name, role=body.role, disabled=body.disabled, password=body.password)
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        if not updated:
            raise Problem(404, "utilisateur introuvable")
        return {"ok": True, "user": updated}

    @r.get("/permissions")
    def permissions(request: Request, _user: dict = Depends(current_user)):
        return {"ok": True, "matrix": kernel_of(request).auth.matrix()}

    @r.put("/permissions")
    def put_permission(body: PermissionIn, request: Request, _admin: dict = Depends(require_admin)):
        try:
            kernel_of(request).auth.set_permission(body.role, body.module, body.level)
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        return {"ok": True, "matrix": kernel_of(request).auth.matrix()}

    # registre, liens, actions

    @r.get("/registry")
    def registry(request: Request, _user: dict = Depends(current_user)):
        k = kernel_of(request)
        return {"ok": True, "kinds": k.registry.public(), "actions": k.actions.kinds(), "modules": sorted(k.modules)}

    @r.get("/links/{kind}/{ident}")
    def links_of(kind: str, ident: str, request: Request, _user: dict = Depends(current_user)):
        k = kernel_of(request)
        rows = k.links.of(kind, ident)
        summaries = k.registry.summaries([(str(r["other_kind"]), str(r["other_id"])) for r in rows])
        for row in rows:
            row["other"] = summaries.get(f"{row['other_kind']}:{row['other_id']}")
        return {"ok": True, "links": rows}

    @r.post("/links", status_code=201)
    def add_link(body: LinkIn, request: Request, user: dict = Depends(current_user)):
        k = kernel_of(request)
        for kind in (body.src_kind, body.dst_kind):
            if not k.registry.has(kind):
                raise Problem(400, f"type inconnu : {kind}")
        link_id = k.links.link(body.src_kind, body.src_id, body.dst_kind, body.dst_id, body.role, actor=user["id"])
        k.outbox.emit("link.created", body.model_dump())
        return {"ok": True, "id": link_id}

    @r.delete("/links")
    def remove_link(body: LinkIn, request: Request, _user: dict = Depends(current_user)):
        k = kernel_of(request)
        removed = k.links.unlink(body.src_kind, body.src_id, body.dst_kind, body.dst_id, body.role or None)
        if removed:
            k.outbox.emit("link.removed", body.model_dump())
        return {"ok": True, "removed": removed}

    @r.get("/actions/kinds")
    def action_kinds(request: Request, _user: dict = Depends(current_user)):
        return {"ok": True, "kinds": kernel_of(request).actions.kinds()}

    @r.get("/actions")
    def actions_list(request: Request, limit: int = 40, module: str | None = None, entity_kind: str | None = None, entity_id: str | None = None, _user: dict = Depends(current_user)):
        return {"ok": True, "actions": kernel_of(request).actions.recent(max(1, min(200, limit)), module, entity_kind, entity_id)}

    @r.post("/actions")
    def perform(body: ActionIn, request: Request, user: dict = Depends(current_user)):
        k = kernel_of(request)
        try:
            spec = k.actions.spec(body.kind)
        except KeyError as exc:
            raise Problem(400, str(exc)) from exc
        if not k.auth.allows(user, spec.module, "write"):
            raise Problem(403, f"écriture refusée sur {spec.module}")
        try:
            result = k.actions.perform(body.kind, body.ids, body.params, actor_id=user["id"], label=body.label)
        except (ValueError, KeyError) as exc:
            raise Problem(400, str(exc)) from exc
        except PermissionError as exc:
            raise Problem(503, str(exc)) from exc
        k.metrics.inc("actions", kind=body.kind)
        return JSONResponse({"ok": True, "action": result, "job_id": result.get("job_id")}, status_code=202 if result.get("job_id") else 200)

    @r.post("/actions/{action_id}/undo")
    def undo(action_id: int, request: Request, user: dict = Depends(current_user)):
        k = kernel_of(request)
        try:
            result = k.actions.undo(action_id, actor_id=user["id"])
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        return JSONResponse({"ok": True, **result}, status_code=202 if result.get("job_id") else 200)

    # jobs et flux

    @r.get("/jobs")
    def jobs(request: Request, limit: int = 30, _user: dict = Depends(current_user)):
        return {"ok": True, "jobs": kernel_of(request).jobs.recent(max(1, min(200, limit)))}

    @r.get("/jobs/{job_id}")
    def job(job_id: int, request: Request, _user: dict = Depends(current_user)):
        found = kernel_of(request).jobs.get(job_id)
        if not found:
            raise Problem(404, "job introuvable")
        return {"ok": True, "job": found}

    @r.post("/jobs/{job_id}/retry")
    def retry(job_id: int, request: Request, _admin: dict = Depends(require_admin)):
        return {"ok": kernel_of(request).jobs.retry(job_id)}

    @r.post("/jobs/{job_id}/cancel")
    def cancel(job_id: int, request: Request, _admin: dict = Depends(require_admin)):
        return {"ok": kernel_of(request).jobs.cancel(job_id)}

    @r.get("/stream")
    async def events(request: Request, once: int = 0, user: dict = Depends(current_user)):
        k = kernel_of(request)
        raw_last = request.headers.get("last-event-id") or request.query_params.get("last") or "0"
        try:
            last_id = int(raw_last)
        except ValueError:
            last_id = 0
        if once:
            body = "".join(sse_format(e) for e in k.bus.since(last_id, user["id"]))
            return PlainTextResponse(body or ": empty\n\n", media_type="text/event-stream")
        q = k.bus.subscribe(user_id=user["id"])

        def _next(timeout: float) -> dict[str, Any] | None:
            try:
                return q.get(timeout=timeout)
            except queue.Empty:
                return None

        async def gen():
            try:
                yield ": hello\n\n"
                for event in k.bus.since(last_id, user["id"]):
                    yield sse_format(event)
                while True:
                    if await request.is_disconnected():
                        break
                    event = await asyncio.to_thread(_next, 15.0)
                    yield ": ping\n\n" if event is None else sse_format(event)
            finally:
                k.bus.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # recherche, notifications, fichiers

    @r.get("/search")
    def search(request: Request, q: str = "", kinds: str = "", limit: int = 40, user: dict = Depends(current_user)):
        k = kernel_of(request)
        wanted = [part for part in kinds.split(",") if part] or None
        allowed_kinds = [kind.kind for kind in k.registry.all() if k.auth.allows(user, kind.module, "read")]
        wanted = [kind for kind in (wanted or allowed_kinds) if kind in allowed_kinds]
        hits = k.search.query(q, wanted, max(1, min(100, limit))) if q.strip() else []
        summaries = k.registry.summaries([(h["kind"], h["id"]) for h in hits])
        for hit in hits:
            hit["entity"] = summaries.get(f"{hit['kind']}:{hit['id']}")
        k.metrics.inc("searches")
        return {"ok": True, "q": q, "hits": hits, "indexed": k.search.count()}

    @r.get("/notifications")
    def notifications(request: Request, unread: int = 0, limit: int = 40, user: dict = Depends(current_user)):
        k = kernel_of(request)
        return {"ok": True, "notifications": k.notifications.list(user["id"], bool(unread), max(1, min(200, limit))), "unread": k.notifications.unread_count(user["id"])}

    @r.post("/notifications/read")
    def read(body: ReadIn, request: Request, user: dict = Depends(current_user)):
        return {"ok": True, "marked": kernel_of(request).notifications.mark_read(user["id"], body.ids)}

    @r.get("/notifications/vapid")
    def vapid(request: Request, _user: dict = Depends(current_user)):
        return {"ok": True, "public_key": kernel_of(request).settings.vapid_public_key}

    @r.post("/notifications/push", status_code=201)
    def push(body: PushIn, request: Request, user: dict = Depends(current_user)):
        try:
            kernel_of(request).notifications.subscribe_push(user["id"], body.subscription, request.headers.get("user-agent", ""))
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        return {"ok": True}

    @r.get("/files")
    def files(request: Request, path: str = "", _user: dict = Depends(require("files", "read"))):
        k = kernel_of(request)
        if not k.files.available:
            raise Problem(503, "Médiathèque non montée.")
        try:
            return {"ok": True, **k.files.list(path)}
        except FileNotFoundError:
            raise Problem(404, "dossier introuvable")
        except PermissionError as exc:
            raise Problem(403, str(exc))

    @r.get("/files/stat")
    def file_stat(request: Request, path: str, _user: dict = Depends(require("files", "read"))):
        k = kernel_of(request)
        try:
            info = k.files.stat(path)
        except FileNotFoundError:
            raise Problem(404, "fichier introuvable")
        except PermissionError as exc:
            raise Problem(403, str(exc))
        info["indexed"] = k.files.indexed(path)
        info["links"] = k.links.of("file", path)
        return {"ok": True, "file": info}

    @r.get("/files/raw")
    def file_raw(request: Request, path: str, _user: dict = Depends(require("files", "read"))):
        k = kernel_of(request)
        try:
            real: Path = k.files.open_path(path)
        except FileNotFoundError:
            raise Problem(404, "fichier introuvable")
        except PermissionError as exc:
            raise Problem(403, str(exc))
        info = k.files.describe(real)
        return FileResponse(real, media_type=info["mime"], headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(real.name)}"})

    @r.post("/files/scan", status_code=202)
    def file_scan(body: ScanIn, request: Request, _user: dict = Depends(require("files", "write"))):
        k = kernel_of(request)
        return {"ok": True, "job": k.jobs.submit("files.scan", {"path": body.path}, 3, dedupe=True)}

    # état, réglages

    @r.post("/connectors/{system}/reset")
    def reset_connector(system: str, request: Request, _admin: dict = Depends(require_admin)):
        k = kernel_of(request)
        if not k.connectors.has(system):
            raise Problem(404, "connecteur inconnu")
        k.connectors.state.set_status(system, "idle")
        return {"ok": True, "state": k.connectors.state.get(system)}

    @r.get("/status")
    def status(request: Request, _user: dict = Depends(current_user)):
        return {"ok": True, **kernel_of(request).status()}

    @r.get("/metrics", response_class=PlainTextResponse)
    def metrics(request: Request, _user: dict = Depends(current_user)):
        return kernel_of(request).metrics.prometheus()

    @r.get("/settings")
    def settings(request: Request, _user: dict = Depends(require("settings", "read"))):
        k = kernel_of(request)
        values = {key: value for key, value in k.settings_map().items() if not key.endswith("_token") and not key.endswith("_secret")}
        return {"ok": True, "settings": values, "modules": {name: getattr(mod, "settings_view", lambda: {})() for name, mod in k.modules.items()}}

    @r.patch("/settings")
    def patch_settings(body: SettingsPatch, request: Request, _user: dict = Depends(require("settings", "write"))):
        k = kernel_of(request)
        for key, value in body.values.items():
            if not key or len(key) > 80:
                raise Problem(400, "clé invalide")
            k.set_setting(key, str(value)[:4000])
        k.outbox.emit("settings.changed", {"keys": list(body.values)})
        return {"ok": True}

    return r

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from regie.kernel.api import Problem, current_user, require
from regie.kernel.jobs import P_USER
from regie.modules.mail.classify import CATEGORIES, CATEGORY_LABELS, usage_line
from regie.modules.mail.feed import atom_xml, markdown as feed_markdown, select as feed_select
from regie.modules.mail.serialize import index_body_text, serialize_action, serialize_hit, serialize_item
from regie.modules.mail.service import ACTION_LABELS, IMAP_KINDS, MailService
from regie.modules.mail import notion

QUEUE_LIMIT = 120
HISTORY_LIMIT = 80


class ActionIn(BaseModel):
    kind: str
    ids: list[int]
    folder: str | None = None


class NotionIn(BaseModel):
    note: str = ""


class AdoptIn(BaseModel):
    queue: bool = True


class SettingsIn(BaseModel):
    extra_prompt: str = ""
    signature: str = ""
    mark_read_on_open: bool | None = None
    density: str | None = None
    notion_token: str | None = None


def build_router(service: MailService) -> APIRouter:
    r = APIRouter(prefix="/api/v1/mail", tags=["mail"])
    k = service.kernel
    read = require("mail", "read")
    write = require("mail", "write")

    def status_payload(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        snap = snapshot or {}
        return {
            "incident": service.incident(),
            "imap_ok": service.imap_ok,
            "imap_ready": service.session is not None,
            "llm_ready": service.settings.llm_ready,
            "last_scan_at": service.last_scan_at,
            "counts": snap.get("counts") if snap else service.store.counts(),
            "cat_counts": snap.get("cat_counts") if snap else {},
            "unread": snap.get("unread", 0) if snap else service.store.unread_count(),
            "labels": dict(CATEGORY_LABELS),
            "categories": list(CATEGORIES),
            "token_missing": False,
            "last_scan_stats": dict(service.last_scan_stats),
            "last_scan_usage": usage_line(service.last_scan_stats),
            "notion_ready": bool(service.notion_token()),
            "index": dict(service.last_index),
            "can_move": bool(service.session.snapshot().get("can_move")) if service.session else False,
            "mark_read_on_open": service.setting("mark_read_on_open") == "1",
            "density": service.setting("density") or "comfortable",
            "treated_today": service.store.treated_today(),
            "folders": service.folders(),
        }

    @r.get("/queue")
    def queue(request: Request, id: str | None = None, cat: str | None = None, status: str | None = None, _user: dict = Depends(read)):
        done = status == "done"
        snapshot = service.store.queue_snapshot(done, HISTORY_LIMIT if done else QUEUE_LIMIT)
        tag_source = f"{snapshot['version']}|{service.last_scan_at}|{snapshot['unread']}|{done}|{cat or ''}|{id or ''}"
        etag = 'W/"' + hashlib.sha1(tag_source.encode("utf-8")).hexdigest()[:20] + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        items = snapshot["items"]
        chosen = cat if cat in CATEGORIES else ""
        pool = [item for item in items if not chosen or item["category"] == chosen]
        source = pool or items
        selected = next((item for item in source if str(item["id"]) == str(id or "")), source[0]) if source else None
        if id and str(id).isdigit() and (selected is None or str(selected["id"]) != str(id)):
            wanted = service.store.get_item(int(id))
            if wanted and all(int(i["id"]) != int(wanted["id"]) for i in items):
                items = [wanted, *items]
                selected = wanted
        signature = service.signature
        payload = status_payload(snapshot)
        payload.update({"ok": True, "items": [serialize_item(item, signature) for item in items], "selected_id": selected["id"] if selected else None, "cat": chosen, "status": "done" if done else "proposed", "version": snapshot["version"]})
        k.metrics.inc("mail_queue")
        return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "private, no-cache"})

    @r.get("/items/{item_id}")
    def item(item_id: int, _user: dict = Depends(read)):
        found = service.store.get_item(item_id)
        if not found:
            raise Problem(404, "mail introuvable")
        return {"ok": True, "item": serialize_item(found, service.signature), "links": k.links.of("mail", item_id)}

    @r.get("/search")
    def search(q: str = "", limit: int = 40, _user: dict = Depends(read)):
        needle = (q or "").strip()
        indexed = service.store.index_count()
        if len(needle) < 2:
            return {"ok": True, "q": needle, "hits": [], "indexed": indexed}
        hits = k.search.query(needle, ["mail_index"], max(1, min(100, limit)))
        rows = {int(row["id"]): row for row in service.store.index_rows([int(h["id"]) for h in hits])}
        item_ids = [int(row["item_id"]) for row in rows.values() if row.get("item_id")]
        items = {int(i["id"]): i for i in service.store.get_items(item_ids)}
        signature = service.signature
        out = []
        for hit in hits:
            row = rows.get(int(hit["id"]))
            if not row:
                continue
            out.append(serialize_hit(row, items.get(int(row["item_id"])) if row.get("item_id") else None, signature, hit=str(hit.get("hit") or "")))
        return {"ok": True, "q": needle, "hits": out, "indexed": indexed}

    @r.get("/mails/{index_id}")
    def mail(index_id: int, _user: dict = Depends(read)):
        row = service.store.index_get(index_id)
        if not row:
            raise Problem(404, "mail introuvable")
        found = service.store.get_item(int(row["item_id"])) if row.get("item_id") else None
        return {"ok": True, "hit": serialize_hit(row, found, service.signature), "body": index_body_text(row)}

    @r.post("/mails/{index_id}/adopt")
    def adopt(index_id: int, body: AdoptIn, _user: dict = Depends(write)):
        found = service.adopt(index_id, body.queue)
        if found is None:
            raise Problem(404, "mail introuvable")
        return {"ok": True, "item": serialize_item(found, service.signature)}

    @r.get("/status")
    def status(_user: dict = Depends(read)):
        return {"ok": True, **status_payload(), "worker": {**service.status(), "jobs": k.jobs.snapshot()}, "actions": [serialize_action(a) for a in k.actions.recent(10, module="mail")]}

    @r.post("/scan")
    def scan(wait: int = 0, user: dict = Depends(write)):
        if wait:
            result = service.scan()
            if result.get("ok"):
                return {"ok": True, **{key: result.get(key) for key in ("seen", "created", "retried", "auto_moved", "haiku_calls", "sonnet_calls")}}
            raise Problem(400, result.get("error") or "relevé impossible")
        job = k.jobs.submit("mail.scan", {"manual": True}, P_USER, dedupe=True, created_by=user["id"])
        k.jobs.submit("mail.flags", {}, P_USER, dedupe=True)
        return JSONResponse({"ok": True, "job_id": job["id"]}, status_code=202)

    @r.post("/actions")
    def actions(body: ActionIn, user: dict = Depends(write)):
        if body.kind not in ACTION_LABELS:
            raise Problem(400, "action inconnue")
        clean_ids = sorted({int(i) for i in body.ids if i})
        items = service.store.get_items(clean_ids)
        if not items:
            raise Problem(404, "aucun mail")
        if body.kind in IMAP_KINDS and service.session is None:
            raise Problem(503, "IMAP non configuré")
        label = ACTION_LABELS[body.kind] + (f" · {len(items)} mails" if len(items) > 1 else "")
        params = {"folder": body.folder[:200]} if body.folder else {}
        try:
            action = k.actions.perform(f"mail.{body.kind}", [str(i) for i in clean_ids], params, actor_id=user["id"], label=label)
        except PermissionError as exc:
            raise Problem(503, str(exc)) from exc
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        fresh = service.store.get_items(clean_ids)
        job_id = action.get("job_id")
        return JSONResponse({"ok": True, "action": serialize_action(action), "job_id": job_id, "items": [serialize_item(i, service.signature) for i in fresh]}, status_code=202 if job_id else 200)

    @r.get("/actions")
    def actions_list(limit: int = 30, _user: dict = Depends(read)):
        return {"ok": True, "actions": [serialize_action(a) for a in k.actions.recent(max(1, min(200, limit)), module="mail")]}

    @r.post("/actions/{action_id}/undo")
    def undo(action_id: int, user: dict = Depends(write)):
        try:
            result = k.actions.undo(action_id, actor_id=user["id"])
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc
        except (ValueError, PermissionError) as exc:
            raise Problem(400, str(exc)) from exc
        ids = [int(i) for i in result.get("item_ids") or [] if str(i).isdigit()]
        fresh = service.store.get_items(ids) if ids else []
        return JSONResponse({"ok": True, "action": serialize_action(result) if result.get("id") else None, "job_id": result.get("job_id"), "items": [serialize_item(i, service.signature) for i in fresh]}, status_code=202 if result.get("job_id") else 200)

    @r.post("/items/{item_id}/brief")
    def brief(item_id: int, wait: int = 0, user: dict = Depends(write)):
        if not service.store.get_item(item_id):
            raise Problem(404, "mail introuvable")
        if not service.settings.llm_ready:
            raise Problem(503, "Claude non configuré")
        if wait:
            try:
                result = k.jobs.call("mail.brief", {"item_id": item_id}, P_USER, timeout=150)
            except Exception as exc:
                raise Problem(400, str(exc)) from exc
            return {"ok": True, "item": result.get("item")}
        job = k.jobs.submit("mail.brief", {"item_id": item_id}, P_USER, dedupe=True, created_by=user["id"])
        return JSONResponse({"ok": True, "job_id": job["id"]}, status_code=202)

    @r.post("/items/{item_id}/notion")
    def to_notion(item_id: int, body: NotionIn, wait: int = 0, user: dict = Depends(write)):
        if not service.store.get_item(item_id):
            raise Problem(404, "mail introuvable")
        if not service.notion_token():
            raise Problem(503, "Jeton Notion manquant (Réglages).")
        if wait:
            try:
                result = k.jobs.call("mail.notion", {"item_id": item_id, "note": body.note}, P_USER, timeout=240)
            except Exception as exc:
                raise Problem(400, str(exc)) from exc
            return {"ok": True, **result}
        job = k.jobs.submit("mail.notion", {"item_id": item_id, "note": body.note}, P_USER, dedupe=True, created_by=user["id"])
        return JSONResponse({"ok": True, "job_id": job["id"]}, status_code=202)

    @r.get("/items/{item_id}/attachments/{n}")
    def attachment(item_id: int, n: int, _user: dict = Depends(read)):
        if not service.store.get_item(item_id):
            raise Problem(404, "mail introuvable")
        try:
            result = k.jobs.call("mail.attachment", {"item_id": item_id, "n": n}, P_USER, timeout=120)
        except (RuntimeError, TimeoutError) as exc:
            raise Problem(400, str(exc)) from exc
        path = Path(result["path"])
        filename = result.get("filename") or path.name
        return FileResponse(path, media_type=result.get("mime") or "application/octet-stream", headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"})

    def _feed_items() -> list[dict[str, Any]]:
        rows = service.store.list_items(status="proposed", limit=80)
        return feed_select([serialize_item(item, service.signature) for item in rows])

    @r.get("/feed.atom")
    def feed_atom(request: Request):
        href = str(request.base_url).rstrip("/") + "/api/v1/mail/feed.atom"
        return Response(atom_xml(_feed_items(), self_href=href), media_type="application/atom+xml; charset=utf-8", headers={"Cache-Control": "private, no-store"})

    @r.get("/feed.md")
    def feed_md():
        return PlainTextResponse(feed_markdown(_feed_items()), media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "private, no-store"})

    @r.get("/settings")
    def settings(probe: int = 0, _user: dict = Depends(read)):
        session = service.session.snapshot() if service.session else None
        imap_info = None
        if session:
            imap_info = {"ok": bool(session.get("connected")) and not session.get("last_error"), "connected": session.get("connected"), "folder_count": session.get("folder_count"), "inbox_messages": session.get("inbox_messages"), "inbox_unseen": session.get("inbox_unseen"), "last_ok": session.get("last_ok"), "error": session.get("last_error"), "capabilities": session.get("capabilities"), "can_move": session.get("can_move"), "folders": session.get("folders")}
        notion_status = None
        token = service.notion_token()
        if token and probe:
            try:
                notion_status = notion.probe_notion(token, service.settings.notion_database_id, service.settings.notion_project_id)
            except Exception as exc:
                notion_status = {"ok": False, "taches": False, "projets": False, "radio_campus": False, "view_only": False, "detail": str(exc)[:400]}
        return {
            "ok": True,
            **status_payload(),
            "extra_prompt": service.setting("extra_prompt"),
            "signature": service.signature,
            "auto_mode": False,
            "imap": imap_info,
            "imap_user": service.settings.imap_user,
            "imap_host": service.settings.imap_host,
            "imap_port": service.settings.imap_port,
            "notion_token_set": bool(token),
            "notion_status": notion_status,
            "anthropic_model": service.settings.anthropic_model,
            "anthropic_fast_model": service.settings.anthropic_fast_model,
            "anthropic_effort": service.settings.anthropic_effort,
            "feed_md": "/api/v1/mail/feed.md",
            "feed_atom": "/api/v1/mail/feed.atom",
            "feed_token": service.feed_token(),
            "worker": {**service.status(), "jobs": k.jobs.snapshot()},
            "scan_days": 30,
        }

    @r.post("/settings")
    def save_settings(body: SettingsIn, _user: dict = Depends(write)):
        service.set_setting("extra_prompt", body.extra_prompt.strip())
        service.set_setting("signature", body.signature.strip()[:SIGNATURE_MAX_LEN])
        if body.mark_read_on_open is not None:
            service.set_setting("mark_read_on_open", "1" if body.mark_read_on_open else "0")
        if body.density in {"comfortable", "compact"}:
            service.set_setting("density", body.density)
        if body.notion_token is not None and body.notion_token.strip():
            k.secrets.put("notion", "token", body.notion_token.strip()[: notion.TOKEN_MAX])
        return {"ok": True}

    return r


SIGNATURE_MAX_LEN = 800

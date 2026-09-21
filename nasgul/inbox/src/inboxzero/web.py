from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import queue
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from inboxzero import db, notion
from inboxzero.brand import label, load_brand
from inboxzero.classify import CATEGORIES, CATEGORY_LABELS, usage_line
from inboxzero.config import Settings, get_settings
from inboxzero.feed import atom_xml, markdown as feed_markdown, select as feed_select
from inboxzero.imaputil import FOLDER_CHILDREN, FOLDER_PARENT
from inboxzero.jobs import P_ACTION, P_SCAN, P_USER, sse_format
from inboxzero.search import fts_query
from inboxzero.serialize import serialize_action, serialize_hit, serialize_item
from inboxzero.worker import ACTION_LABELS, IMAP_KINDS, INVERSE, Worker, run_in_background

log = logging.getLogger("inboxzero.web")
ROOT = Path(__file__).parent
SPA_DIR = ROOT / "static" / "app"
COOKIE = "inboxzero"
FEED_PATHS = {"/api/feed.atom", "/api/feed.md"}
LOGIN_WINDOW_SEC = 60.0
LOGIN_MAX_FAILS = 8
QUEUE_LIMIT = 120
HISTORY_LIMIT = 80
ACTION_KINDS = set(ACTION_LABELS)

app = FastAPI(title=label("inbox", "title"), docs_url=None, redoc_url=None)
worker: Worker | None = None
_login_fails: dict[str, list[float]] = defaultdict(list)


class LoginIn(BaseModel):
    token: str = ""


class SettingsIn(BaseModel):
    extra_prompt: str = ""
    auto_mode: bool = False
    signature: str = ""
    notion_token: str | None = None
    mark_read_on_open: bool | None = None
    density: str | None = None


class NotionIn(BaseModel):
    note: str = ""


class ActionIn(BaseModel):
    ids: list[int]
    kind: str
    folder: str | None = None


class AdoptIn(BaseModel):
    queue: bool = False


def current_worker() -> Worker:
    if worker is None:
        raise RuntimeError("worker absent")
    return worker


def _token_ok(got: str, expected: str) -> bool:
    if not expected or not got:
        return False
    if len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)


def _bearer(request: Request) -> str:
    raw = request.headers.get("authorization") or ""
    kind, _, rest = raw.partition(" ")
    if kind.lower() != "bearer":
        return ""
    return rest.strip()


def logged_in(request: Request, settings: Settings) -> bool:
    if _token_ok(request.cookies.get(COOKIE, ""), settings.inboxzero_token):
        return True
    if request.url.path in FEED_PATHS:
        return _token_ok(_bearer(request), settings.inboxzero_token)
    return False


def _public(path: str) -> bool:
    if path in {"/health", "/api/login", "/brand.json"}:
        return True
    if path.startswith("/assets/"):
        return True
    return not path.startswith("/api")


def _set_session(response: JSONResponse, token: str) -> JSONResponse:
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return response


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return ""


def _login_blocked(ip: str) -> bool:
    now = time.monotonic()
    hits = [stamp for stamp in _login_fails[ip] if now - stamp < LOGIN_WINDOW_SEC]
    _login_fails[ip] = hits
    return len(hits) >= LOGIN_MAX_FAILS


def _note_login_fail(ip: str) -> None:
    _login_fails[ip].append(time.monotonic())


def _clear_login_fails(ip: str) -> None:
    _login_fails.pop(ip, None)


def _incident(settings: Settings, w: Worker) -> str | None:
    if w.last_error:
        return w.last_error
    if not settings.imap_ready:
        return "IMAP non configuré"
    if w.imap_ok is False:
        return "IMAP à vérifier"
    return None


def _error(message: str, status: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, **extra}, status_code=status)


def _status_payload(settings: Settings, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    w = current_worker()
    snap = snapshot or {}
    settings_map = snap.get("settings") or {}
    notion_token = settings_map.get("notion_token") if settings_map else db.get_setting(settings.db_path, "notion_token")
    return {
        "incident": _incident(settings, w),
        "imap_ok": w.imap_ok,
        "imap_ready": settings.imap_ready,
        "llm_ready": settings.llm_ready,
        "last_scan_at": w.last_scan_at,
        "counts": snap.get("counts") if snap else db.counts(settings.db_path),
        "cat_counts": snap.get("cat_counts") if snap else db.counts_by_category(settings.db_path),
        "unread": snap.get("unread", 0) if snap else db.unread_count(settings.db_path),
        "labels": dict(CATEGORY_LABELS),
        "categories": list(CATEGORIES),
        "token_missing": not bool(settings.inboxzero_token),
        "last_scan_stats": dict(getattr(w, "last_scan_stats", None) or {}),
        "last_scan_usage": usage_line(getattr(w, "last_scan_stats", None)),
        "notion_ready": settings.notion_ready(notion_token or ""),
        "index": dict(getattr(w, "last_index", None) or {}),
        "can_move": bool(w.session.snapshot().get("can_move")) if w.session else False,
        "mark_read_on_open": (settings_map.get("mark_read_on_open") if settings_map else db.get_setting(settings.db_path, "mark_read_on_open")) == "1",
        "density": (settings_map.get("density") if settings_map else db.get_setting(settings.db_path, "density")) or "comfortable",
        "treated_today": db.treated_today(settings.db_path, datetime.now(timezone.utc).strftime("%Y-%m-%d")),
        "folders": {key: f"{FOLDER_PARENT}/{name}" for key, name in FOLDER_CHILDREN.items()},
    }


@app.on_event("startup")
def startup() -> None:
    global worker
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    db.init_db(settings.db_path)
    db.jobs_reset_stale(settings.db_path)
    worker = run_in_background(settings)


@app.on_event("shutdown")
def shutdown() -> None:
    if worker:
        worker.stop()


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    settings = get_settings()
    if _public(request.url.path):
        return await call_next(request)
    if not logged_in(request, settings):
        return JSONResponse({"error": "auth"}, status_code=401)
    return await call_next(request)


@app.get("/health")
def health():
    settings = get_settings()
    counts = db.counts(settings.db_path)
    pending = int(counts.get("proposed") or 0)
    return {"ok": True, "pending": pending}


@app.get("/brand.json")
def brand_json():
    return load_brand()


@app.post("/api/login")
async def api_login(body: LoginIn, request: Request):
    settings = get_settings()
    ip = _client_ip(request)
    if _login_blocked(ip):
        return JSONResponse(
            {"ok": False, "error": "Trop d'essais. Réessaie dans une minute."},
            status_code=429,
        )
    if not _token_ok(body.token, settings.inboxzero_token):
        _note_login_fail(ip)
        return JSONResponse({"ok": False, "error": "Jeton invalide."}, status_code=401)
    _clear_login_fails(ip)
    return _set_session(JSONResponse({"ok": True}), body.token)


@app.post("/api/logout")
def api_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response


# --- file ---------------------------------------------------------------------------


@app.get("/api/queue")
def api_queue(request: Request, id: str | None = None, cat: str | None = None, status: str | None = None):
    settings = get_settings()
    done = status == "done"
    snapshot = db.queue_snapshot(settings.db_path, done, HISTORY_LIMIT if done else QUEUE_LIMIT)
    w = current_worker()
    tag_source = f"{snapshot['version']}|{w.last_scan_at}|{snapshot['unread']}|{done}|{cat or ''}|{id or ''}"
    etag = 'W/"' + hashlib.sha1(tag_source.encode("utf-8")).hexdigest()[:20] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    items = snapshot["items"]
    chosen = cat if cat in CATEGORIES else ""
    pool = [item for item in items if not chosen or item["category"] == chosen]
    selected = None
    source = pool or items
    if source:
        selected = next((item for item in source if str(item["id"]) == str(id or "")), source[0])
    if id and selected is not None and str(selected["id"]) != str(id):
        wanted = db.get_item(settings.db_path, int(id)) if str(id).isdigit() else None
        if wanted and wanted not in items:
            items = [wanted, *items]
            selected = wanted
    signature = snapshot["settings"].get("signature") or ""
    payload = _status_payload(settings, snapshot)
    payload.update(
        {
            "items": [serialize_item(item, signature) for item in items],
            "selected_id": selected["id"] if selected else None,
            "cat": chosen,
            "status": "done" if done else "proposed",
            "version": snapshot["version"],
        }
    )
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "private, no-cache"})


@app.get("/api/items/{item_id}")
def api_item(item_id: int):
    settings = get_settings()
    item = db.get_item(settings.db_path, item_id)
    if not item:
        return _error("mail introuvable", 404)
    return {"ok": True, "item": serialize_item(item, db.get_setting(settings.db_path, "signature"))}


@app.get("/api/search")
def api_search(q: str = ""):
    settings = get_settings()
    needle = (q or "").strip()
    w = current_worker()
    indexed = int((w.last_index or {}).get("indexed") or 0) or db.mail_index_count(settings.db_path)
    try:
        inbox = int((w.last_index or {}).get("inbox") or 0) or int(db.get_setting(settings.db_path, "mail_index_inbox") or 0)
    except (TypeError, ValueError):
        inbox = 0
    if not fts_query(needle):
        return {"q": needle, "hits": [], "indexed": indexed, "inbox": inbox}
    signature = db.get_setting(settings.db_path, "signature")
    rows = db.search_mails(settings.db_path, needle, limit=40)
    item_ids = [_row_item_id(row) for row in rows]
    items = {int(item["id"]): item for item in db.get_items(settings.db_path, [i for i in item_ids if i])}
    hits = [serialize_hit(row, items.get(_row_item_id(row)), signature) for row in rows]
    return {"q": needle, "hits": hits, "indexed": indexed, "inbox": inbox}


def _row_item_id(row: dict[str, Any]) -> int:
    try:
        value = row.get("item_id")
        if value in (None, ""):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


@app.get("/api/mails/{index_id}")
def api_mail(index_id: int):
    settings = get_settings()
    row = db.mail_index_get(settings.db_path, index_id)
    if not row:
        return _error("mail introuvable", 404)
    item = db.get_item(settings.db_path, int(row["item_id"])) if row.get("item_id") else None
    body = index_body_text(row)
    return {"ok": True, "hit": serialize_hit({**row, "hit": ""}, item, db.get_setting(settings.db_path, "signature")), "body": body}


def index_body_text(row: dict[str, Any]) -> str:
    """The FTS body starts with sender and subject; hand back only the mail text."""
    body = str(row.get("body") or "")
    for prefix in (str(row.get("sender") or ""), str(row.get("subject") or "")):
        prefix = prefix.strip()
        if prefix and body.lstrip().startswith(prefix):
            body = body.lstrip()[len(prefix) :]
    return body.strip()


@app.post("/api/mails/{index_id}/adopt")
def api_adopt(index_id: int, body: AdoptIn):
    """Turn an indexed mail (older than the triage window) into an item: récap, actions, file."""
    settings = get_settings()
    row = db.mail_index_get(settings.db_path, index_id)
    if not row:
        return _error("mail introuvable", 404)
    item = db.get_item(settings.db_path, int(row["item_id"])) if row.get("item_id") else None
    if item is None:
        item = db.get_item_by_uid(settings.db_path, str(row["uidvalidity"]), str(row["imap_uid"]))
    if item is None:
        item_id = db.insert_item(
            settings.db_path,
            {
                "imap_uid": str(row["imap_uid"]),
                "uidvalidity": str(row["uidvalidity"]),
                "message_id": row.get("message_id") or "",
                "sender": row.get("sender") or "",
                "subject": row.get("subject") or "",
                "excerpt": row.get("body") or "",
                "category": "read",
                "reason": "Ajouté depuis la recherche.",
                "confidence": 0.5,
                "status": "proposed" if body.queue else "skipped",
                "mailed_at": row.get("mailed_at") or "",
                "folder": row.get("folder") or "INBOX",
            },
        )
        item = db.get_item(settings.db_path, item_id)
    elif body.queue and item.get("status") in {"skipped", "gone"}:
        db.set_status(settings.db_path, int(item["id"]), "proposed")
        item = db.get_item(settings.db_path, int(item["id"]))
    current_worker().bus.publish("queue", {"reason": "adopt"})
    return {"ok": True, "item": serialize_item(item, db.get_setting(settings.db_path, "signature")) if item else None}


# --- événements ------------------------------------------------------------------------


def _next_event(q: queue.Queue, timeout: float) -> dict[str, Any] | None:
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


@app.get("/api/events")
async def api_events(request: Request, once: int = 0):
    w = current_worker()
    bus = w.bus
    raw_last = request.headers.get("last-event-id") or request.query_params.get("last") or "0"
    try:
        last_id = int(raw_last)
    except ValueError:
        last_id = 0
    if once:
        body = "".join(sse_format(event) for event in bus.since(last_id))
        return PlainTextResponse(body or ": empty\n\n", media_type="text/event-stream")
    q = bus.subscribe()

    async def gen():
        try:
            yield ": hello\n\n"
            for event in bus.since(last_id):
                yield sse_format(event)
            while True:
                if await request.is_disconnected():
                    break
                event = await asyncio.to_thread(_next_event, q, 15.0)
                if event is None:
                    yield ": ping\n\n"
                else:
                    yield sse_format(event)
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/api/jobs/{job_id}")
def api_job(job_id: int):
    w = current_worker()
    live = w.runner.get(job_id)
    if live:
        return {"ok": True, "job": live.public()}
    stored = db.job_get(get_settings().db_path, job_id)
    if not stored:
        return _error("job introuvable", 404)
    return {"ok": True, "job": stored}


@app.get("/api/status")
def api_status():
    settings = get_settings()
    w = current_worker()
    return {"ok": True, **_status_payload(settings), "worker": w.status(), "actions": [serialize_action(a) for a in db.actions_recent(settings.db_path, 10)]}


# --- relevé -----------------------------------------------------------------------------


@app.post("/api/scan")
def api_scan(wait: int = 0):
    w = current_worker()
    if wait:
        result = w.scan()
        if result.get("ok"):
            return {"ok": True, **{k: result.get(k) for k in ("seen", "created", "retried", "auto_moved", "haiku_calls", "sonnet_calls")}}
        return _error(result.get("error") or "relevé impossible")
    job = w.runner.submit("scan", {"manual": True}, P_USER, dedupe=True)
    w.runner.submit("flags", {}, P_USER, dedupe=True)
    return JSONResponse({"ok": True, "job_id": job.id}, status_code=202)


# --- actions --------------------------------------------------------------------------------


def _perform_action(kind: str, ids: list[int], folder: str | None = None, label_text: str | None = None) -> dict[str, Any] | JSONResponse:
    settings = get_settings()
    w = current_worker()
    if kind not in ACTION_KINDS:
        return _error("action inconnue")
    clean_ids = sorted({int(i) for i in ids if i})
    items = db.get_items(settings.db_path, clean_ids)
    if not items:
        return _error("aucun mail", 404)
    if kind in IMAP_KINDS and not settings.imap_ready:
        return _error("IMAP non configuré", 503)
    if kind == "archive" and w.session is not None and w.session.connected and not w.session.snapshot().get("can_move"):
        return _error("Le serveur IMAP ne sait pas déplacer (ni MOVE ni UIDPLUS).", 503)
    before = w.snapshot_before(items)
    after: dict[str, Any] = {}
    if folder:
        after["folder"] = folder[:200]
    count = len(items)
    label_value = label_text or (ACTION_LABELS[kind] + (f" · {count} mails" if count > 1 else ""))
    action_id = db.action_create(settings.db_path, kind, clean_ids, before, after, label_value)
    w.apply_local(kind, items, after)
    if kind == "later":
        for item in items:
            db.memory_forget(settings.db_path, item.get("sender") or "")
    job_id = None
    if kind in IMAP_KINDS:
        job = w.runner.submit("action", {"action_id": action_id}, P_ACTION)
        job_id = job.id
    else:
        db.action_update(settings.db_path, action_id, status="done")
    action = db.action_get(settings.db_path, action_id) or {"id": action_id, "kind": kind}
    packed = serialize_action(action)
    if job_id is None:
        w.bus.publish("action", {"action": packed, "status": "done"})
        w.bus.publish("queue", {"reason": "action", "kind": kind})
    signature = db.get_setting(settings.db_path, "signature")
    fresh = db.get_items(settings.db_path, clean_ids)
    return {"ok": True, "action": packed, "job_id": job_id, "items": [serialize_item(item, signature) for item in fresh]}


@app.post("/api/actions")
def api_actions(body: ActionIn):
    if not body.ids:
        return _error("aucun mail")
    result = _perform_action(body.kind, body.ids, body.folder)
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse(result, status_code=202 if result.get("job_id") else 200)


@app.get("/api/actions")
def api_actions_list(limit: int = 30):
    settings = get_settings()
    rows = db.actions_recent(settings.db_path, max(1, min(200, limit)))
    return {"ok": True, "actions": [serialize_action(row) for row in rows]}


@app.post("/api/actions/{action_id}/undo")
def api_undo(action_id: int):
    settings = get_settings()
    original = db.action_get(settings.db_path, action_id)
    if not original:
        return _error("action introuvable", 404)
    if original.get("status") not in {"pending", "done"}:
        return _error("action déjà annulée ou échouée")
    kind = str(original.get("kind") or "")
    inverse = INVERSE.get(kind)
    if not inverse:
        return _error("action non annulable")
    ids = [int(i) for i in original.get("item_ids") or []]
    before: dict[str, Any] = original.get("before") or {}
    if kind in {"seen", "unseen", "flag", "unflag"}:
        field = "seen" if kind in {"seen", "unseen"} else "flagged"
        value = kind in {"seen", "flag"}
        ids = [i for i in ids if bool((before.get(str(i)) or {}).get(field, not value)) != value] or ids
    result = _perform_action(inverse, ids, None, f"Annulé : {original.get('label') or ACTION_LABELS.get(kind, kind)}")
    if isinstance(result, JSONResponse):
        return result
    db.action_update(settings.db_path, action_id, status="undone")
    current_worker().bus.publish("action", {"action": serialize_action(db.action_get(settings.db_path, action_id) or original), "status": "undone"})
    return JSONResponse({**result, "undone_id": action_id}, status_code=202 if result.get("job_id") else 200)


# --- récap / Notion / pièces ---------------------------------------------------------------------


@app.post("/api/items/{item_id}/brief")
def api_brief(item_id: int, wait: int = 0):
    settings = get_settings()
    w = current_worker()
    item = db.get_item(settings.db_path, item_id)
    if not item:
        return _error("mail introuvable", 404)
    if not settings.llm_ready:
        return _error("Claude non configuré", 503)
    if wait:
        try:
            result = w.runner.call("brief", {"item_id": item_id}, P_USER, timeout=150)
        except Exception as exc:
            return _error(str(exc))
        return {"ok": True, "item": result.get("item")}
    job = w.runner.submit("brief", {"item_id": item_id}, P_USER, dedupe=True)
    return JSONResponse({"ok": True, "job_id": job.id}, status_code=202)


@app.post("/api/items/{item_id}/notion")
def api_notion(item_id: int, body: NotionIn, wait: int = 0):
    settings = get_settings()
    w = current_worker()
    item = db.get_item(settings.db_path, item_id)
    if not item:
        return _error("mail introuvable", 404)
    token = settings.notion_secret(db.get_setting(settings.db_path, "notion_token"))
    if not token:
        return _error("Jeton Notion manquant (Réglages).", 503)
    if wait:
        try:
            result = w.runner.call("notion", {"item_id": item_id, "note": body.note}, P_USER, timeout=240)
        except Exception as exc:
            return _error(str(exc))
        return {"ok": True, **result}
    job = w.runner.submit("notion", {"item_id": item_id, "note": body.note}, P_USER, dedupe=True)
    return JSONResponse({"ok": True, "job_id": job.id}, status_code=202)


@app.get("/api/items/{item_id}/attachments/{n}")
def api_attachment(item_id: int, n: int):
    settings = get_settings()
    w = current_worker()
    item = db.get_item(settings.db_path, item_id)
    if not item:
        return _error("mail introuvable", 404)
    try:
        result = w.runner.call("attachment", {"item_id": item_id, "n": n}, P_USER, timeout=120)
    except (RuntimeError, TimeoutError) as exc:
        return _error(str(exc))
    path = Path(result["path"])
    filename = result.get("filename") or path.name
    headers = {"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"}
    return FileResponse(path, media_type=result.get("mime") or "application/octet-stream", headers=headers)


# --- flux ------------------------------------------------------------------------------------


def _feed_items(settings: Settings) -> list[dict[str, Any]]:
    signature = db.get_setting(settings.db_path, "signature")
    rows = db.list_items(settings.db_path, status="proposed", limit=80)
    return feed_select([serialize_item(item, signature) for item in rows])


@app.get("/api/feed.atom")
def api_feed_atom(request: Request):
    settings = get_settings()
    items = _feed_items(settings)
    href = str(request.base_url).rstrip("/") + "/api/feed.atom"
    xml = atom_xml(items, self_href=href)
    return Response(
        xml,
        media_type="application/atom+xml; charset=utf-8",
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/api/feed.md")
def api_feed_md():
    settings = get_settings()
    body = feed_markdown(_feed_items(settings))
    return PlainTextResponse(
        body,
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "private, no-store"},
    )


# --- réglages ------------------------------------------------------------------------------------


@app.get("/api/settings")
def api_settings(probe: int = 0):
    settings = get_settings()
    w = current_worker()
    stored = db.get_settings_map(settings.db_path)
    session = w.session.snapshot() if w.session else None
    imap_info: dict[str, Any] | None = None
    if session:
        imap_info = {
            "ok": bool(session.get("connected")) and not session.get("last_error"),
            "connected": session.get("connected"),
            "folder_count": session.get("folder_count"),
            "inbox_messages": session.get("inbox_messages"),
            "inbox_unseen": session.get("inbox_unseen"),
            "last_ok": session.get("last_ok"),
            "error": session.get("last_error"),
            "capabilities": session.get("capabilities"),
            "can_move": session.get("can_move"),
            "folders": session.get("folders"),
        }
    notion_status = None
    notion_secret = settings.notion_secret(stored.get("notion_token") or "")
    if notion_secret and probe:
        try:
            notion_status = notion.probe_notion(notion_secret, settings.notion_database_id, settings.notion_project_id)
        except Exception as exc:
            notion_status = {"ok": False, "taches": False, "projets": False, "radio_campus": False, "view_only": False, "detail": str(exc)[:400]}
    return {
        **_status_payload(settings),
        "extra_prompt": stored.get("extra_prompt") or "",
        "signature": stored.get("signature") or "",
        "auto_mode": False,
        "mark_read_on_open": stored.get("mark_read_on_open") == "1",
        "density": stored.get("density") or "comfortable",
        "imap": imap_info,
        "imap_user": settings.imap_user,
        "imap_host": settings.imap_host,
        "imap_port": settings.imap_port,
        "notion_token_set": settings.notion_ready(stored.get("notion_token") or ""),
        "notion_status": notion_status,
        "anthropic_model": settings.anthropic_model,
        "anthropic_fast_model": settings.anthropic_fast_model,
        "anthropic_effort": settings.anthropic_effort,
        "feed_md": "/api/feed.md",
        "feed_atom": "/api/feed.atom",
        "worker": w.status(),
        "scan_days": 30,
    }


@app.post("/api/settings")
def api_save_settings(body: SettingsIn):
    settings = get_settings()
    db.set_setting(settings.db_path, "extra_prompt", body.extra_prompt.strip())
    db.set_setting(settings.db_path, "signature", body.signature.strip()[: db.SIGNATURE_MAX])
    db.set_setting(settings.db_path, "auto_mode", "0")
    if body.mark_read_on_open is not None:
        db.set_setting(settings.db_path, "mark_read_on_open", "1" if body.mark_read_on_open else "0")
    if body.density in {"comfortable", "compact"}:
        db.set_setting(settings.db_path, "density", body.density)
    if body.notion_token is not None:
        token = body.notion_token.strip()[: notion.TOKEN_MAX]
        if token:
            db.set_setting(settings.db_path, "notion_token", token)
    return {"ok": True}


# --- SPA -------------------------------------------------------------------------------------------


def _spa_index() -> HTMLResponse:
    index = SPA_DIR / "index.html"
    if not index.is_file():
        return HTMLResponse("UI absente", status_code=503)
    return FileResponse(index)


assets_dir = SPA_DIR / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path.startswith("api/") or full_path == "health":
        return JSONResponse({"error": "not found"}, status_code=404)
    if full_path:
        candidate = (SPA_DIR / full_path).resolve()
        try:
            candidate.relative_to(SPA_DIR.resolve())
        except ValueError:
            return JSONResponse({"error": "not found"}, status_code=404)
        if candidate.is_file():
            return FileResponse(candidate)
    return _spa_index()

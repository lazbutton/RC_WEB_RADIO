from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from inboxzero.attachments import names, parse_list
from inboxzero.classify import (
    SONNET_EXCERPT,
    _anthropic_text,
    clip,
    useful_links,
)
from inboxzero.threads import visible_body

log = logging.getLogger("inboxzero.notion")

NOTION_URL = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2025-09-03"
DATABASE_ID = "71b5299c-c049-4581-9540-31510d690601"
INLINE_DATABASE_ID = "3d63b6b3-c9bf-806b-ab24-f582b57e2ad7"
PROJETS_DATABASE_ID = "962a4aa4-b60e-474c-8d9d-73bd1a68e78d"
DATA_SOURCE_ID = "d01bbc98-e036-419d-9e14-29045e7db211"
PROJECT_ID = "3d63b6b3-c9bf-8059-967c-f38aea00c84b"
TACHES_URL = "https://app.notion.com/p/71b5299cc0494581954031510d690601"
STATUS_PROGRAMME = "Programmé"
NOTE_MAX = 800
TOKEN_MAX = 500
TITLE_MAX = 120

VIEW_ONLY_MSG = (
    "Nasgul voit la vue Tâches dans Radio Campus, pas la vraie base. "
    "Dans le panneau Nasgul, clique « + Ajouter des pages et des bases de données », "
    "puis choisis la Tâches du workspace (hors Radio Campus). "
    f"Ou ouvre {TACHES_URL} → ••• → Connexions → Nasgul."
)

CONTEXTE = ("Ordinateur", "Téléphone", "Maison", "Déplacement", "Extérieur")
IMPORTANCE = ("Essentielle", "Importante", "Normale", "Optionnelle")
URGENCE = ("Aujourd’hui", "Cette semaine", "Ce mois-ci", "Sans urgence")
PRIORITE = ("Basse", "Moyenne", "Haute")
DUREE = ("5 min", "15 min", "30 min", "1 h", "2 h +")
ENERGIE = ("Faible", "Moyenne", "Forte")
RECURRENCE = ("Comptant", "Occasionnel", "Quand je veux", "Par semaine", "Par mois")

SUBJECT_PREFIX = re.compile(r"^(re|fw|fwd|tr|enc|aw|sv)\s*:\s*", re.I)
ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
FR_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")

HYDRATE_PROMPT = """Tu prépares une tâche Notion pour Radio Campus Orléans (volontaire musique).
Réponds UNIQUEMENT en JSON compact, sans markdown :
{"title":"","resultat":"","pourquoi":"","contexte":["Ordinateur"],"importance":"Normale","urgence":"Sans urgence","priorite":"Moyenne","duree":"30 min","energie":"Moyenne","recurrence":"Occasionnel","echeance":null,"planifiee":null,"cursor":false,"icon":"📻"}

Règles :
- title : court, français, style « Interview Orange Pression ». Pas de Fwd/Re.
- resultat : ce qu’il faut avoir fait, 1 ou 2 phrases. N’invente rien.
- pourquoi : pourquoi c’est important. Si une note utilisateur est fournie, appuie-toi dessus.
- contexte : sous-ensemble de Ordinateur, Téléphone, Maison, Déplacement, Extérieur. Mail / recherche = Ordinateur.
- importance : Essentielle | Importante | Normale | Optionnelle
- urgence : Aujourd’hui | Cette semaine | Ce mois-ci | Sans urgence
- priorite : Basse | Moyenne | Haute
- duree : 5 min | 15 min | 30 min | 1 h | 2 h +
- energie : Faible | Moyenne | Forte
- recurrence : Comptant | Occasionnel | Quand je veux | Par semaine | Par mois
- echeance / planifiee : YYYY-MM-DD seulement si une date est dans le mail, le récap ou la note. Sinon null.
- cursor : true seulement si la note ou le mail demande un travail dans Cursor / code.
- icon : un emoji. Interviews / musique : 📻 ou 🎹.
N’invente aucun lien, date, personne ou engagement.
"""


def paris_tz():
    try:
        return ZoneInfo("Europe/Paris")
    except Exception:
        return timezone(timedelta(hours=2))


def today_paris() -> str:
    return datetime.now(paris_tz()).date().isoformat()


def notion_uuid(raw: str) -> str:
    hexed = re.sub(r"[^0-9a-fA-F]", "", raw or "")
    if len(hexed) != 32:
        return (raw or "").strip()
    return f"{hexed[0:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:32]}"


def strip_subject(subject: str) -> str:
    text = (subject or "").strip()
    while True:
        nxt = SUBJECT_PREFIX.sub("", text).strip()
        if nxt == text:
            break
        text = nxt
    return text or "Tâche Radio Campus"


def _pick(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip()
    if text in allowed:
        return text
    lowered = text.casefold()
    for option in allowed:
        if option.casefold() == lowered:
            return option
    return default


def _pick_many(value: Any, allowed: tuple[str, ...], default: tuple[str, ...]) -> list[str]:
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, list):
        raw = value
    else:
        raw = list(default)
    out: list[str] = []
    for item in raw:
        chosen = _pick(item, allowed, "")
        if chosen and chosen not in out:
            out.append(chosen)
    return out or list(default)


def _valid_iso_date(value: Any) -> str | None:
    text = str(value or "").strip()[:10]
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text):
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def extract_date(*blobs: str) -> str | None:
    year_now = datetime.now(paris_tz()).year
    joined = "\n".join(blobs)
    iso = ISO_DATE.search(joined)
    if iso:
        return _valid_iso_date(f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}")
    found = FR_DATE.search(joined)
    if not found:
        return None
    day = int(found.group(1))
    month = int(found.group(2))
    year_raw = found.group(3)
    if year_raw:
        year = int(year_raw)
        if year < 100:
            year += 2000
    else:
        year = year_now
        candidate = datetime(year, month, day, tzinfo=paris_tz()).date()
        if candidate < datetime.now(paris_tz()).date() - timedelta(days=30):
            year += 1
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def urgence_for(due: str | None) -> str:
    if not due:
        return "Sans urgence"
    try:
        target = datetime.strptime(due, "%Y-%m-%d").date()
    except ValueError:
        return "Sans urgence"
    delta = (target - datetime.now(paris_tz()).date()).days
    if delta <= 0:
        return "Aujourd’hui"
    if delta <= 7:
        return "Cette semaine"
    if delta <= 31:
        return "Ce mois-ci"
    return "Sans urgence"


def parse_task(raw: str) -> dict[str, Any]:
    blob = (raw or "").strip()
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?", "", blob).strip()
        blob = re.sub(r"```$", "", blob).strip()
    match = re.search(r"\{.*\}", blob, re.S)
    if not match:
        raise ValueError("tâche IA sans JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("tâche IA invalide")
    return data


def fallback_task(
    *,
    subject: str,
    recap: str,
    excerpt: str,
    note: str,
) -> dict[str, Any]:
    title = strip_subject(subject)[:TITLE_MAX]
    recap_text = (recap or "").strip()
    resultat = clip(recap_text or visible_body(excerpt) or title, 400)
    pourquoi = clip((note or "").strip(), 400)
    due = extract_date(note, recap_text, excerpt)
    cursor = "cursor" in f"{note} {recap_text}".casefold()
    return {
        "title": title,
        "resultat": resultat,
        "pourquoi": pourquoi,
        "contexte": ["Ordinateur"],
        "importance": "Normale",
        "urgence": urgence_for(due),
        "priorite": "Moyenne",
        "duree": "30 min",
        "energie": "Moyenne",
        "recurrence": "Occasionnel",
        "echeance": due,
        "planifiee": today_paris(),
        "cursor": cursor,
        "icon": "📻",
    }


def normalize_task(data: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    title = strip_subject(str(data.get("title") or fallback["title"]))[:TITLE_MAX]
    due = _valid_iso_date(data.get("echeance")) or fallback.get("echeance")
    planned = _valid_iso_date(data.get("planifiee")) or fallback.get("planifiee") or today_paris()
    icon = str(data.get("icon") or fallback["icon"]).strip()[:4] or "📻"
    return {
        "title": title,
        "resultat": clip(str(data.get("resultat") or fallback["resultat"]), 400),
        "pourquoi": clip(str(data.get("pourquoi") or fallback["pourquoi"]), 400),
        "contexte": _pick_many(data.get("contexte"), CONTEXTE, ("Ordinateur",)),
        "importance": _pick(data.get("importance"), IMPORTANCE, fallback["importance"]),
        "urgence": _pick(data.get("urgence"), URGENCE, urgence_for(due)),
        "priorite": _pick(data.get("priorite"), PRIORITE, fallback["priorite"]),
        "duree": _pick(data.get("duree"), DUREE, fallback["duree"]),
        "energie": _pick(data.get("energie"), ENERGIE, fallback["energie"]),
        "recurrence": _pick(data.get("recurrence"), RECURRENCE, fallback["recurrence"]),
        "echeance": due,
        "planifiee": planned,
        "cursor": bool(data.get("cursor", fallback["cursor"])),
        "icon": icon,
    }


def hydrate_task(
    *,
    item: dict[str, Any],
    note: str = "",
    api_key: str = "",
    model: str = "",
    extra_prompt: str = "",
    workspace_id: str = "",
    effort: str = "high",
) -> dict[str, Any]:
    note = (note or "").strip()[:NOTE_MAX]
    fallback = fallback_task(
        subject=item.get("subject") or "",
        recap=item.get("summary") or item.get("reason") or "",
        excerpt=item.get("excerpt") or "",
        note=note,
    )
    if not api_key:
        return fallback
    payload = {
        "from": item.get("sender") or "",
        "subject": item.get("subject") or "",
        "recap": clip(item.get("summary") or item.get("reason") or "", 800),
        "body": clip(visible_body(item.get("excerpt") or ""), SONNET_EXCERPT),
        "note": note,
        "today": today_paris(),
    }
    system = HYDRATE_PROMPT
    if extra_prompt.strip():
        system += "\n\nConsignes en plus :\n" + extra_prompt.strip()[:800]
    try:
        raw, _usage = _anthropic_text(
            api_key=api_key,
            model=model or "claude-sonnet-5",
            system=system,
            user=json.dumps(payload, ensure_ascii=False),
            max_tokens=4000,
            timeout=90.0,
            workspace_id=workspace_id,
            effort=effort,
        )
        return normalize_task(parse_task(raw), fallback)
    except Exception as exc:
        log.warning("hydratation Notion en repli : %s", exc)
        return fallback


def _text(content: str, href: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": (content or "")[:2000]}
    if href:
        payload["link"] = {"url": href[:2000]}
    return {"type": "text", "text": payload}


def _paragraph(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [_text(text)] if text else []},
    }


def _heading(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": [_text(text)]},
    }


def _bullet(text: str, href: str | None = None) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [_text(text, href)]},
    }


NOTION_FILE_MAX = 20 * 1024 * 1024
FILE_UPLOADS_URL = "https://api.notion.com/v1/file_uploads"


def _media_block(kind: str, upload_id: str, filename: str) -> dict[str, Any]:
    caption = [_text(filename)] if filename else []
    inner: dict[str, Any] = {
        "type": "file_upload",
        "file_upload": {"id": upload_id},
        "caption": caption,
    }
    if kind == "pdf":
        return {"object": "block", "type": "pdf", "pdf": inner}
    if kind == "image":
        return {"object": "block", "type": "image", "image": inner}
    if kind == "audio":
        return {"object": "block", "type": "audio", "audio": inner}
    inner["name"] = filename[:200] or "piece"
    return {"object": "block", "type": "file", "file": inner}


def upload_file(token: str, path: Path, filename: str, content_type: str = "") -> str:
    dest = Path(path)
    if not dest.is_file():
        raise RuntimeError("pièce introuvable pour Notion")
    size = dest.stat().st_size
    if size > NOTION_FILE_MAX:
        raise RuntimeError("pièce trop volumineuse pour Notion")
    name = (filename or dest.name or "piece")[:200]
    mime = (content_type or "application/octet-stream").split(";")[0].strip() or "application/octet-stream"
    created = httpx.post(
        FILE_UPLOADS_URL,
        headers=notion_headers(token),
        json={"filename": name, "content_type": mime},
        timeout=30.0,
    )
    if created.status_code >= 400:
        raise RuntimeError(notion_error_message(created))
    data = created.json() if created.content else {}
    upload_id = str((data or {}).get("id") or "").strip()
    send_url = str((data or {}).get("upload_url") or "").strip() or f"{FILE_UPLOADS_URL}/{upload_id}/send"
    if not upload_id:
        raise RuntimeError("Notion n’a pas renvoyé d’identifiant de fichier.")
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
    }
    with dest.open("rb") as fh:
        sent = httpx.post(
            send_url,
            headers=headers,
            files={"file": (name, fh, mime)},
            timeout=90.0,
        )
    if sent.status_code >= 400:
        raise RuntimeError(notion_error_message(sent))
    return upload_id


def page_children(
    item: dict[str, Any],
    task: dict[str, Any],
    note: str,
    uploads: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    recap = (item.get("summary") or item.get("reason") or "").strip()
    excerpt = visible_body(item.get("excerpt") or "")
    draft = (item.get("draft") or "").strip()
    links = useful_links("\n".join([note, recap, excerpt, draft]))
    blocks: list[dict[str, Any]] = []
    if recap:
        blocks.append(_paragraph(recap))
    if note.strip():
        blocks.append(_heading("Note"))
        blocks.append(_paragraph(note.strip()[:NOTE_MAX]))
    if links:
        blocks.append(_heading("Ressources"))
        for url in links[:8]:
            blocks.append(_bullet(url, url))
    sender = (item.get("sender") or "").strip()
    subject = (item.get("subject") or "").strip()
    if sender or subject:
        blocks.append(_heading("Mail"))
        if sender:
            blocks.append(_bullet(f"De : {sender}"))
        if subject:
            blocks.append(_bullet(f"Objet : {subject}"))
    if draft:
        blocks.append(_heading("Brouillon"))
        blocks.append(_paragraph(draft[:1900]))
    atts = parse_list(item.get("attachments"))
    packed = [row for row in (uploads or []) if isinstance(row, dict)]
    if packed or atts:
        blocks.append(_heading("Pièces"))
        if packed:
            for row in packed[:12]:
                name = str(row.get("filename") or "pièce")[:120]
                uid = str(row.get("file_upload_id") or "").strip()
                kind = str(row.get("kind") or "file")
                if uid:
                    blocks.append(_media_block(kind, uid, name))
                else:
                    blocks.append(_bullet(name))
        else:
            for name in names(atts)[:12]:
                blocks.append(_bullet(name))
        excerpts = [
            str(row.get("text") or "").strip()
            for row in atts
            if str(row.get("kind") or "") == "pdf" and str(row.get("text") or "").strip()
        ]
        if excerpts:
            blocks.append(_heading("Extrait PDF"))
            blocks.append(_paragraph(excerpts[0][:800]))
    if not blocks:
        blocks.append(_paragraph(task["title"]))
    return blocks[:90]


def page_properties(task: dict[str, Any], database_id: str = DATABASE_ID, project_id: str = PROJECT_ID) -> dict[str, Any]:
    del database_id
    props: dict[str, Any] = {
        "Nom de la tâche": {"title": [_text(task["title"])]},
        "État": {"status": {"name": STATUS_PROGRAMME}},
        "Projet": {"relation": [{"id": notion_uuid(project_id)}]},
        "Contexte": {"multi_select": [{"name": name} for name in task["contexte"]]},
        "Importance": {"select": {"name": task["importance"]}},
        "Urgence": {"select": {"name": task["urgence"]}},
        "Priorité": {"select": {"name": task["priorite"]}},
        "Durée": {"select": {"name": task["duree"]}},
        "Énergie": {"select": {"name": task["energie"]}},
        "Récurrence": {"select": {"name": task["recurrence"]}},
        "À faire avec Cursor": {"checkbox": bool(task["cursor"])},
        "Prochaine action": {"checkbox": False},
        "Résultat attendu": {"rich_text": [_text(task["resultat"])] if task.get("resultat") else []},
        "Pourquoi important": {"rich_text": [_text(task["pourquoi"])] if task.get("pourquoi") else []},
        "Planifiée le": {"date": {"start": task.get("planifiee") or today_paris()}},
    }
    if task.get("echeance"):
        props["Échéance"] = {"date": {"start": task["echeance"]}}
    return props


def notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _get_json(token: str, path: str) -> tuple[int, dict[str, Any]]:
    response = httpx.get(
        f"https://api.notion.com{path}",
        headers=notion_headers(token),
        timeout=20.0,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return response.status_code, payload


def _first_data_source(payload: dict[str, Any]) -> str:
    for source in payload.get("data_sources") or []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or "").strip()
        if source_id:
            return notion_uuid(source_id)
    return ""


def probe_notion(
    token: str,
    database_id: str = DATABASE_ID,
    project_id: str = PROJECT_ID,
) -> dict[str, Any]:
    if not (token or "").strip():
        return {
            "ok": False,
            "taches": False,
            "projets": False,
            "radio_campus": False,
            "view_only": False,
            "detail": "Jeton manquant.",
        }
    secret = token.strip()
    db_status, db_payload = _get_json(secret, f"/v1/databases/{notion_uuid(database_id or DATABASE_ID)}")
    inline_status, inline_payload = _get_json(secret, f"/v1/databases/{INLINE_DATABASE_ID}")
    projets_status, projets_payload = _get_json(secret, f"/v1/databases/{PROJETS_DATABASE_ID}")
    page_status, _page = _get_json(secret, f"/v1/pages/{notion_uuid(project_id or PROJECT_ID)}")
    taches = bool(_first_data_source(db_payload if db_status == 200 else {}) or _first_data_source(inline_payload if inline_status == 200 else {}))
    view_only = inline_status == 200 and not taches
    projets = projets_status == 200 and bool(_first_data_source(projets_payload))
    radio = page_status == 200
    if taches and projets:
        detail = "Tâches et Projets accessibles."
    elif any(code in {401, 403} for code in (db_status, inline_status, projets_status)):
        detail = "Jeton Notion refusé. Recopie le jeton Nasgul depuis le panneau d’intégration."
    elif view_only:
        detail = VIEW_ONLY_MSG
    else:
        detail = VIEW_ONLY_MSG
    return {
        "ok": taches and projets,
        "taches": taches,
        "projets": projets,
        "radio_campus": radio,
        "view_only": view_only,
        "detail": detail,
    }


def discover_data_source_id(token: str, database_id: str, fallback: str = DATA_SOURCE_ID) -> str:
    del fallback
    seen: list[str] = []
    view_only = False
    auth_fail = False
    for raw in (database_id, DATABASE_ID, INLINE_DATABASE_ID):
        db_id = notion_uuid(raw)
        if not db_id or db_id in seen:
            continue
        seen.append(db_id)
        status, payload = _get_json(token, f"/v1/databases/{db_id}")
        if status in {401, 403}:
            auth_fail = True
            continue
        if status >= 400:
            log.warning("Notion GET database %s : %s", db_id, str(payload.get("message") or status)[:300])
            continue
        source_id = _first_data_source(payload)
        if source_id:
            return source_id
        view_only = True
    if auth_fail:
        raise RuntimeError("Jeton Notion refusé. Recrée une intégration interne et colle le jeton Nasgul.")
    raise RuntimeError(VIEW_ONLY_MSG)


def page_payload(
    item: dict[str, Any],
    task: dict[str, Any],
    note: str = "",
    database_id: str = DATABASE_ID,
    project_id: str = PROJECT_ID,
    data_source_id: str = DATA_SOURCE_ID,
    uploads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "parent": {
            "type": "data_source_id",
            "data_source_id": notion_uuid(data_source_id or DATA_SOURCE_ID),
        },
        "icon": {"type": "emoji", "emoji": task.get("icon") or "📻"},
        "properties": page_properties(task, database_id, project_id),
        "children": page_children(item, task, note, uploads),
    }
    return payload


def notion_error_message(response: httpx.Response) -> str:
    message = (response.text or "").strip()[:400]
    try:
        payload = response.json()
        message = str(payload.get("message") or message)
    except Exception:
        pass
    lowered = message.lower()
    if response.status_code in {401, 403}:
        return "Jeton Notion refusé. Recrée une intégration interne et partage Tâches + Projets."
    if "shared with your integration" in lowered or "could not find data_source" in lowered:
        return VIEW_ONLY_MSG
    if "does not contain any data sources" in lowered:
        return VIEW_ONLY_MSG
    if "not a database" in lowered or "data_source" in lowered:
        return f"Notion refuse l’ID du conteneur Tâches. {message}"[:400]
    return f"Notion HTTP {response.status_code}: {message}"[:400]


def create_page(token: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    response = httpx.post(
        NOTION_URL,
        headers=notion_headers(token),
        json=payload,
        timeout=timeout,
    )
    if response.status_code >= 400:
        log.warning("Notion POST pages : %s", (response.text or "")[:400])
        raise RuntimeError(notion_error_message(response))
    data = response.json()
    url = str(data.get("url") or "").strip()
    if not url:
        raise RuntimeError("Notion n’a pas renvoyé d’URL.")
    return {"url": url, "id": str(data.get("id") or "")}


def add_item_to_notion(
    *,
    item: dict[str, Any],
    note: str = "",
    token: str,
    api_key: str = "",
    model: str = "",
    extra_prompt: str = "",
    workspace_id: str = "",
    effort: str = "high",
    database_id: str = DATABASE_ID,
    project_id: str = PROJECT_ID,
    data_source_id: str = DATA_SOURCE_ID,
    uploads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not (token or "").strip():
        raise RuntimeError("Notion : colle un jeton d’intégration dans Réglages.")
    existing = (item.get("notion_url") or "").strip()
    if existing:
        return {"url": existing, "title": strip_subject(item.get("subject") or "")}
    task = hydrate_task(
        item=item,
        note=note,
        api_key=api_key,
        model=model,
        extra_prompt=extra_prompt,
        workspace_id=workspace_id,
        effort=effort,
    )
    source_id = discover_data_source_id(token.strip(), database_id, data_source_id or DATA_SOURCE_ID)
    log.info("Notion data_source_id=%s", source_id)
    payload = page_payload(
        item,
        task,
        note,
        database_id,
        project_id,
        source_id,
        uploads,
    )
    created = create_page(token.strip(), payload)
    created["title"] = task["title"]
    return created

from __future__ import annotations

from typing import Any

from inboxzero import db, ui
from inboxzero.attachments import public_rows
from inboxzero.sanitize import text as sanitize_text
from inboxzero.threads import item_needs_brief, unescape_newlines, visible_body


def serialize_item(item: dict[str, Any], signature: str = "") -> dict[str, Any]:
    sender = item.get("sender") or ""
    created = item.get("created_at") or ""
    mailed = (item.get("mailed_at") or "").strip()
    when = mailed or created
    excerpt_clean = item.get("excerpt_clean") or ""
    summary_full = item.get("summary_full") or ""
    if not excerpt_clean or not summary_full:
        derived = db.derived_fields(item)
        excerpt_clean = excerpt_clean or derived["excerpt_clean"]
        summary_full = summary_full or derived["summary_full"]
    return {
        "id": item["id"],
        "imap_uid": item.get("imap_uid") or "",
        "uidvalidity": item.get("uidvalidity") or "",
        "message_id": item.get("message_id") or "",
        "in_reply_to": item.get("in_reply_to") or "",
        "subject": item.get("subject") or "(sans objet)",
        "sender": sender,
        "sender_name": ui.sender_name(sender),
        "sender_email": ui.sender_email(sender),
        "excerpt": unescape_newlines(excerpt_clean),
        "category": item["category"],
        "reason": item.get("reason") or "",
        "error": item.get("error"),
        "draft": sanitize_text(db.apply_signature(item.get("draft") or "", signature)),
        "summary": summary_full,
        "needs_brief": item_needs_brief(item),
        "status": item.get("status") or "proposed",
        "mailed_at": mailed,
        "created_at": created,
        "created_rel": ui.relative_time(when),
        "notion_url": (item.get("notion_url") or "").strip(),
        "attachments": public_rows(item.get("attachments")),
        "seen": bool(item.get("seen", 1)),
        "flagged": bool(item.get("flagged", 0)),
        "folder": item.get("folder") or "INBOX",
    }


def serialize_hit(row: dict[str, Any], item: dict[str, Any] | None = None, signature: str = "") -> dict[str, Any]:
    packed = serialize_item(item, signature) if item else None
    sender = packed["sender"] if packed else (row.get("sender") or "")
    mailed = packed["mailed_at"] if packed else (row.get("mailed_at") or "").strip()
    raw_body = str(row.get("body") or "")
    if not packed:
        for prefix in (str(row.get("sender") or "").strip(), str(row.get("subject") or "").strip()):
            if prefix and raw_body.lstrip().startswith(prefix):
                raw_body = raw_body.lstrip()[len(prefix) :]
    excerpt = packed["excerpt"] if packed else sanitize_text(visible_body(raw_body))
    hit = sanitize_text((row.get("hit") or "").replace("\n", " ")).strip()
    folder = packed["folder"] if packed else (row.get("folder") or "INBOX")
    return {
        "id": int(row["id"]),
        "item_id": packed["id"] if packed else row.get("item_id"),
        "subject": packed["subject"] if packed else ((row.get("subject") or "").strip() or "(sans objet)"),
        "sender": sender,
        "sender_name": packed["sender_name"] if packed else ui.sender_name(sender),
        "excerpt": excerpt[:900],
        "hit": hit[:280],
        "category": packed["category"] if packed else "",
        "status": packed["status"] if packed else "",
        "seen": packed["seen"] if packed else True,
        "flagged": packed["flagged"] if packed else False,
        "folder": folder,
        "mailed_at": mailed,
        "created_rel": packed["created_rel"] if packed else ui.relative_time(mailed),
        "item": packed,
    }


def serialize_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(action["id"]),
        "kind": action.get("kind") or "",
        "label": action.get("label") or "",
        "item_ids": [int(i) for i in (action.get("item_ids") or [])],
        "status": action.get("status") or "pending",
        "error": action.get("error") or "",
        "created_at": action.get("created_at") or "",
        "created_rel": ui.relative_time(action.get("created_at") or ""),
        "done_at": action.get("done_at"),
        "undone_at": action.get("undone_at"),
        "after": action.get("after") or {},
        "reversible": (action.get("status") in {"pending", "done"}) and action.get("kind") not in {"restore", "unlater"},
    }

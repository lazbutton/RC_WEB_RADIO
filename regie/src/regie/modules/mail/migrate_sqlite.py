"""Reprise des données d'Inbox Zero (SQLite) dans Régie (Postgres). Idempotent : rejouable sans doublon.

    python -m regie import-inboxzero /chemin/inboxzero.db
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from regie.kernel import db
from regie.kernel.core import Kernel
from regie.modules.mail.store import MailStore, derived_fields

SETTINGS_KEYS = {"extra_prompt": "mail.extra_prompt", "signature": "mail.signature", "mark_read_on_open": "mail.mark_read_on_open", "density": "mail.density", "scan_last_uid": "mail.scan_last_uid", "scan_uidvalidity": "mail.scan_uidvalidity", "mail_index_uidvalidity": "mail.index_uidvalidity", "mail_index_inbox": "mail.index_inbox"}


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(int(value)) if str(value).isdigit() else bool(value)


def import_sqlite(kernel: Kernel, path: str) -> dict[str, int]:
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    store = MailStore(kernel.dsn, kernel.org_id)
    counts = {"items": 0, "index": 0, "memory": 0, "settings": 0, "actions": 0}
    id_map: dict[int, int] = {}

    with db.connect(kernel.dsn) as conn:
        for row in src.execute("SELECT * FROM items ORDER BY id"):
            item = dict(row)
            derived = derived_fields(item)
            try:
                attachments = json.loads(item.get("attachments") or "[]")
            except ValueError:
                attachments = []
            inserted = db.fetch_one(
                conn,
                """
                INSERT INTO mail_items (org_id, imap_uid, uidvalidity, message_id, in_reply_to, sender, subject, excerpt, excerpt_clean, category, reason,
                  confidence, status, error, draft, summary, summary_full, mailed_at, notion_url, attachments, seen, flagged, folder, folder_uid, created_at, decided_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULLIF(%s, '')::timestamptz, %s, %s, %s, %s, %s, %s, COALESCE(NULLIF(%s, '')::timestamptz, now()), NULLIF(%s, '')::timestamptz)
                ON CONFLICT (org_id, uidvalidity, imap_uid) DO UPDATE SET status = EXCLUDED.status, folder = EXCLUDED.folder, folder_uid = EXCLUDED.folder_uid,
                  seen = EXCLUDED.seen, flagged = EXCLUDED.flagged, notion_url = EXCLUDED.notion_url, draft = EXCLUDED.draft, summary = EXCLUDED.summary, summary_full = EXCLUDED.summary_full
                RETURNING id
                """,
                (
                    kernel.org_id,
                    str(item.get("imap_uid") or ""),
                    str(item.get("uidvalidity") or ""),
                    (item.get("message_id") or "")[:500],
                    (item.get("in_reply_to") or "")[:500],
                    item.get("sender") or "",
                    item.get("subject") or "",
                    item.get("excerpt") or "",
                    derived["excerpt_clean"],
                    item.get("category") or "read",
                    item.get("reason") or "",
                    float(item.get("confidence") or 0),
                    item.get("status") or "proposed",
                    item.get("error"),
                    (item.get("draft") or "")[:4000],
                    (item.get("summary") or "")[:1200],
                    derived["summary_full"],
                    item.get("mailed_at") or "",
                    (item.get("notion_url") or "")[:500],
                    db.J(attachments if isinstance(attachments, list) else []),
                    _bool(item.get("seen"), True),
                    _bool(item.get("flagged"), False),
                    item.get("folder") or "INBOX",
                    item.get("folder_uid") or "",
                    item.get("created_at") or "",
                    item.get("decided_at") or "",
                ),
            )
            if inserted:
                id_map[int(item["id"])] = int(inserted["id"])
                counts["items"] += 1

        for row in src.execute("SELECT * FROM sender_memory"):
            db.execute(
                conn,
                "INSERT INTO mail_sender_memory (org_id, sender_email, category, hits) VALUES (%s, %s, %s, %s) ON CONFLICT (org_id, sender_email) DO UPDATE SET category = EXCLUDED.category, hits = GREATEST(mail_sender_memory.hits, EXCLUDED.hits)",
                (kernel.org_id, row["sender_email"], row["category"], int(row["hits"] or 0)),
            )
            counts["memory"] += 1

        for row in src.execute("SELECT key, value FROM settings"):
            target = SETTINGS_KEYS.get(str(row["key"]))
            if target:
                kernel.set_setting(target, str(row["value"] or ""))
                counts["settings"] += 1
            elif str(row["key"]) == "notion_token" and row["value"]:
                kernel.secrets.put("notion", "token", str(row["value"]))

        try:
            index_rows = list(src.execute("SELECT * FROM mail_index"))
        except sqlite3.OperationalError:
            index_rows = []
        packed = []
        for row in index_rows:
            data = dict(row)
            packed.append({"imap_uid": data.get("imap_uid"), "uidvalidity": data.get("uidvalidity"), "folder": data.get("folder") or "INBOX", "message_id": data.get("message_id") or "", "sender": data.get("sender") or "", "subject": data.get("subject") or "", "body": data.get("body") or "", "summary": data.get("summary") or "", "mailed_at": data.get("mailed_at") or "", "id": id_map.get(int(data["item_id"])) if data.get("item_id") else None})

    rows = store.upsert_index(packed)
    counts["index"] = len(rows)
    service = kernel.modules.get("mail")
    if service is not None:
        service._index_rows_for_search(rows)
        service._index_rows_for_search(store.seed_index_from_items())

    try:
        actions = list(src.execute("SELECT * FROM actions ORDER BY id"))
    except sqlite3.OperationalError:
        actions = []
    with db.connect(kernel.dsn) as conn:
        for row in actions:
            data = dict(row)
            try:
                ids = [str(id_map.get(int(i), i)) for i in json.loads(data.get("item_ids") or "[]")]
            except ValueError:
                ids = []
            db.execute(
                conn,
                "INSERT INTO actions (org_id, module, kind, label, entity_kind, entity_ids, before, after, status, error, created_at, done_at, undone_at) VALUES (%s, 'mail', %s, %s, 'mail', %s, %s, %s, %s, %s, COALESCE(NULLIF(%s, '')::timestamptz, now()), NULLIF(%s, '')::timestamptz, NULLIF(%s, '')::timestamptz)",
                (kernel.org_id, f"mail.{data.get('kind')}", data.get("label") or "", db.J(ids), db.J(_json(data.get("before"))), db.J(_json(data.get("after"))), data.get("status") if data.get("status") in {"pending", "done", "failed", "undone"} else "done", data.get("error") or "", data.get("created_at") or "", data.get("done_at") or "", data.get("undone_at") or ""),
            )
            counts["actions"] += 1
    src.close()
    return counts


def _json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}

"""Persistance du module Mails sur Postgres (port de l'ancien db.py SQLite d'Inbox Zero)."""

from __future__ import annotations

from typing import Any

from regie.kernel import db
from regie.modules.mail.attachments import parse_list
from regie.modules.mail.classify import with_research_links
from regie.modules.mail.sanitize import text as sanitize_text
from regie.modules.mail.threads import short_summary, thread_key, unescape_newlines, visible_body
from regie.modules.mail.ui import sender_email as parse_email

EXCERPT_CLEAN_MAX = 6000
SUMMARY_FULL_MAX = 1600
MEMORY_HITS = 2
SIGNATURE_MAX = 800
ORDER = " ORDER BY COALESCE(mailed_at, created_at) DESC, id DESC"


def apply_signature(draft: str, signature: str) -> str:
    body = (draft or "").rstrip()
    sig = (signature or "").strip()[:SIGNATURE_MAX]
    if not body or not sig:
        return body
    if body.endswith(sig):
        return body
    return f"{body}\n\n{sig}"


def derived_fields(item: dict[str, Any]) -> dict[str, str]:
    raw_excerpt = sanitize_text(unescape_newlines(item.get("excerpt") or ""))
    summary = sanitize_text((item.get("summary") or "").strip()) or (item.get("reason") or "").strip()
    if (item.get("category") or "") not in {"newsletters", "spam"}:
        summary = sanitize_text(with_research_links(summary, raw_excerpt))
    return {"excerpt_clean": sanitize_text(visible_body(raw_excerpt))[:EXCERPT_CLEAN_MAX], "summary_full": summary[:SUMMARY_FULL_MAX]}


def _row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Ligne prête pour le code hérité : dates ISO, attachments en liste."""
    if row is None:
        return None
    out = db.jsonable(row) or {}
    out["id"] = int(out["id"])
    out["attachments"] = list(out.get("attachments") or [])
    out["seen"] = 1 if out.get("seen", True) else 0
    out["flagged"] = 1 if out.get("flagged") else 0
    out["mailed_at"] = out.get("mailed_at") or ""
    return out


def _ts(value: Any) -> Any:
    if value in (None, ""):
        return None
    return value


class MailStore:
    def __init__(self, dsn: str, org_id: str) -> None:
        self.dsn = dsn
        self.org_id = org_id

    # --- items -----------------------------------------------------------------------

    def insert_item(self, item: dict[str, Any]) -> int:
        derived = derived_fields(item)
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                """
                INSERT INTO mail_items (org_id, imap_uid, uidvalidity, message_id, in_reply_to, sender, subject, excerpt, excerpt_clean,
                  category, reason, confidence, status, error, draft, summary, summary_full, mailed_at, attachments, seen, flagged, folder)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    self.org_id,
                    str(item["imap_uid"]),
                    str(item["uidvalidity"]),
                    (item.get("message_id") or "")[:500],
                    (item.get("in_reply_to") or "")[:500],
                    item.get("sender") or "",
                    item.get("subject") or "",
                    item.get("excerpt") or "",
                    derived["excerpt_clean"],
                    item["category"],
                    item.get("reason") or "",
                    float(item.get("confidence") or 0),
                    item.get("status") or "proposed",
                    item.get("error"),
                    (item.get("draft") or "")[:4000],
                    (item.get("summary") or "")[:1200],
                    derived["summary_full"],
                    _ts(item.get("mailed_at")),
                    db.J(parse_list(item.get("attachments"))),
                    bool(item.get("seen", True)),
                    bool(item.get("flagged", False)),
                    str(item.get("folder") or "INBOX"),
                ),
            )
        assert row is not None
        return int(row["id"])

    def update_item(self, item_id: int, item: dict[str, Any]) -> None:
        current = self.get_item(item_id) or {}
        merged = {**current, **{k: v for k, v in item.items() if k != "attachments"}}
        derived = derived_fields(merged)
        atts = parse_list(item.get("attachments")) if "attachments" in item else None
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                """
                UPDATE mail_items SET sender = %s, subject = %s, excerpt = %s, excerpt_clean = %s, category = %s, reason = %s,
                  confidence = %s, status = %s, error = %s, draft = %s, summary = %s, summary_full = %s,
                  mailed_at = COALESCE(%s, mailed_at), attachments = COALESCE(%s, attachments), in_reply_to = CASE WHEN %s <> '' THEN %s ELSE in_reply_to END
                WHERE id = %s
                """,
                (
                    item.get("sender") or "",
                    item.get("subject") or "",
                    item.get("excerpt") or "",
                    derived["excerpt_clean"],
                    item["category"],
                    item.get("reason") or "",
                    float(item.get("confidence") or 0),
                    item.get("status") or "proposed",
                    item.get("error"),
                    (item.get("draft") or "")[:4000],
                    (item.get("summary") or "")[:1200],
                    derived["summary_full"],
                    _ts(item.get("mailed_at")),
                    db.J(atts) if atts is not None else None,
                    (item.get("in_reply_to") or "")[:500],
                    (item.get("in_reply_to") or "")[:500],
                    item_id,
                ),
            )

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            return _row(db.fetch_one(conn, "SELECT * FROM mail_items WHERE id = %s", (item_id,)))

    def get_items(self, ids: list[Any]) -> list[dict[str, Any]]:
        wanted: list[int] = []
        for value in ids:
            try:
                wanted.append(int(value))
            except (TypeError, ValueError):
                continue
        if not wanted:
            return []
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM mail_items WHERE id = ANY(%s)", (wanted,))
        by_id = {int(r["id"]): _row(r) for r in rows}
        return [by_id[i] for i in wanted if i in by_id]  # type: ignore[misc]

    def get_item_by_uid(self, uidvalidity: str, imap_uid: str) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            return _row(db.fetch_one(conn, "SELECT * FROM mail_items WHERE org_id = %s AND uidvalidity = %s AND imap_uid = %s", (self.org_id, uidvalidity, imap_uid)))

    def items_by_uids(self, uidvalidity: str, uids: list[str]) -> dict[str, dict[str, Any]]:
        if not uids:
            return {}
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM mail_items WHERE org_id = %s AND uidvalidity = %s AND imap_uid = ANY(%s)", (self.org_id, uidvalidity, [str(u) for u in uids]))
        return {str(r["imap_uid"]): _row(r) for r in rows}  # type: ignore[misc]

    def list_items(self, status: str | None = "proposed", statuses: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM mail_items WHERE org_id = %s"
        args: list[Any] = [self.org_id]
        if statuses:
            sql += " AND status = ANY(%s)"
            args.append(statuses)
        elif status:
            sql += " AND status = %s"
            args.append(status)
        sql += ORDER + " LIMIT %s"
        args.append(limit)
        with db.connect(self.dsn) as conn:
            return [_row(r) for r in db.fetch_all(conn, sql, args)]  # type: ignore[misc]

    def queue_snapshot(self, done: bool, limit: int) -> dict[str, Any]:
        with db.connect(self.dsn) as conn:
            statuses = ["moved", "skipped"] if done else ["proposed"]
            rows = db.fetch_all(conn, "SELECT * FROM mail_items WHERE org_id = %s AND status = ANY(%s)" + ORDER + " LIMIT %s", (self.org_id, statuses, limit))
            counts = {r["status"]: int(r["n"]) for r in db.fetch_all(conn, "SELECT status, COUNT(*) AS n FROM mail_items WHERE org_id = %s GROUP BY status", (self.org_id,))}
            cats = {r["category"]: int(r["n"]) for r in db.fetch_all(conn, "SELECT category, COUNT(*) AS n FROM mail_items WHERE org_id = %s AND status = 'proposed' GROUP BY category", (self.org_id,))}
            unread = db.scalar(conn, "SELECT COUNT(*) FROM mail_items WHERE org_id = %s AND status = 'proposed' AND NOT seen", (self.org_id,))
            version = db.fetch_one(conn, "SELECT COALESCE(MAX(id), 0) AS m, COUNT(*) AS n, COALESCE(MAX(decided_at)::text, '') AS d FROM mail_items WHERE org_id = %s", (self.org_id,))
        return {"items": [_row(r) for r in rows], "counts": counts, "cat_counts": cats, "unread": int(unread or 0), "version": f"{version['m']}-{version['n']}-{version['d']}" if version else "0"}

    def set_status(self, item_id: int, status: str, error: str | None = None) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "UPDATE mail_items SET status = %s, error = %s, decided_at = now() WHERE id = %s", (status, error, item_id))

    def set_status_many(self, ids: list[int], status: str) -> None:
        if ids:
            with db.connect(self.dsn) as conn:
                db.execute(conn, "UPDATE mail_items SET status = %s, decided_at = now() WHERE id = ANY(%s)", (status, [int(i) for i in ids]))

    def set_error(self, item_id: int, error: str | None) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "UPDATE mail_items SET error = %s WHERE id = %s", (error, item_id))

    def set_draft(self, item_id: int, draft: str) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "UPDATE mail_items SET draft = %s WHERE id = %s", (draft[:4000], item_id))

    def set_summary(self, item_id: int, summary: str) -> None:
        item = self.get_item(item_id)
        if not item:
            return
        item["summary"] = summary[:1200]
        derived = derived_fields(item)
        with db.connect(self.dsn) as conn:
            db.execute(conn, "UPDATE mail_items SET summary = %s, summary_full = %s WHERE id = %s", (summary[:1200], derived["summary_full"], item_id))
            db.execute(conn, "UPDATE mail_index SET summary = %s WHERE item_id = %s", (summary[:1200], item_id))

    def set_notion_url(self, item_id: int, url: str) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "UPDATE mail_items SET notion_url = %s WHERE id = %s", ((url or "")[:500], item_id))

    def set_attachments(self, item_id: int, rows: Any) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "UPDATE mail_items SET attachments = %s WHERE id = %s", (db.J(parse_list(rows)), item_id))

    def set_flags(self, ids: list[int], seen: bool | None = None, flagged: bool | None = None) -> None:
        if not ids:
            return
        with db.connect(self.dsn) as conn:
            if seen is not None:
                db.execute(conn, "UPDATE mail_items SET seen = %s WHERE id = ANY(%s)", (seen, [int(i) for i in ids]))
            if flagged is not None:
                db.execute(conn, "UPDATE mail_items SET flagged = %s WHERE id = ANY(%s)", (flagged, [int(i) for i in ids]))

    def sync_flags(self, uidvalidity: str, flags: dict[str, tuple[bool, bool]]) -> int:
        changed = 0
        with db.connect(self.dsn) as conn:
            for uid, (seen, flagged) in flags.items():
                changed += db.execute(
                    conn,
                    "UPDATE mail_items SET seen = %s, flagged = %s WHERE org_id = %s AND uidvalidity = %s AND imap_uid = %s AND folder = 'INBOX' AND (seen <> %s OR flagged <> %s)",
                    (seen, flagged, self.org_id, uidvalidity, str(uid), seen, flagged),
                )
        return changed

    def set_folder(self, item_id: int, folder: str, folder_uid: str, status: str | None = None) -> None:
        with db.connect(self.dsn) as conn:
            if status:
                db.execute(conn, "UPDATE mail_items SET folder = %s, folder_uid = %s, status = %s, decided_at = now() WHERE id = %s", (folder[:200], (folder_uid or "")[:64], status, item_id))
            else:
                db.execute(conn, "UPDATE mail_items SET folder = %s, folder_uid = %s WHERE id = %s", (folder[:200], (folder_uid or "")[:64], item_id))
            row = db.fetch_one(conn, "SELECT imap_uid, uidvalidity FROM mail_items WHERE id = %s", (item_id,))
            if row:
                uid = folder_uid if (folder != "INBOX" and folder_uid) else row["imap_uid"]
                db.execute(
                    conn,
                    "DELETE FROM mail_index WHERE org_id = %s AND uidvalidity = %s AND folder = %s AND imap_uid = %s AND item_id IS DISTINCT FROM %s",
                    (self.org_id, row["uidvalidity"], folder[:200], uid, item_id),
                )
                db.execute(conn, "UPDATE mail_index SET folder = %s, imap_uid = %s WHERE item_id = %s", (folder[:200], uid, item_id))

    def restore_inbox(self, item_id: int, new_uid: str) -> None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT imap_uid, uidvalidity FROM mail_items WHERE id = %s", (item_id,))
            if not row:
                return
            uid = (new_uid or row["imap_uid"] or "").strip()
            db.execute(conn, "DELETE FROM mail_items WHERE org_id = %s AND uidvalidity = %s AND imap_uid = %s AND id <> %s", (self.org_id, row["uidvalidity"], uid, item_id))
            db.execute(conn, "UPDATE mail_items SET imap_uid = %s, folder = 'INBOX', folder_uid = '', status = 'proposed', decided_at = NULL WHERE id = %s", (uid, item_id))
            db.execute(conn, "DELETE FROM mail_index WHERE org_id = %s AND uidvalidity = %s AND folder = 'INBOX' AND imap_uid = %s AND item_id IS DISTINCT FROM %s", (self.org_id, row["uidvalidity"], uid, item_id))
            db.execute(conn, "UPDATE mail_index SET folder = 'INBOX', imap_uid = %s WHERE item_id = %s", (uid, item_id))

    def mark_gone(self, uidvalidity: str, present: set[str]) -> list[int]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT id, imap_uid FROM mail_items WHERE org_id = %s AND uidvalidity = %s AND folder = 'INBOX' AND status IN ('proposed', 'skipped')", (self.org_id, uidvalidity))
            gone = [int(r["id"]) for r in rows if str(r["imap_uid"]) not in present]
            if gone:
                db.execute(conn, "UPDATE mail_items SET status = 'gone', decided_at = now() WHERE id = ANY(%s)", (gone,))
        return gone

    def inbox_uids_for_status(self, uidvalidity: str, statuses: tuple[str, ...] = ("proposed", "skipped")) -> dict[str, int]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT id, imap_uid FROM mail_items WHERE org_id = %s AND uidvalidity = %s AND folder = 'INBOX' AND status = ANY(%s)", (self.org_id, uidvalidity, list(statuses)))
        return {str(r["imap_uid"]): int(r["id"]) for r in rows}

    def counts(self) -> dict[str, int]:
        with db.connect(self.dsn) as conn:
            return {r["status"]: int(r["n"]) for r in db.fetch_all(conn, "SELECT status, COUNT(*) AS n FROM mail_items WHERE org_id = %s GROUP BY status", (self.org_id,))}

    def unread_count(self) -> int:
        with db.connect(self.dsn) as conn:
            return int(db.scalar(conn, "SELECT COUNT(*) FROM mail_items WHERE org_id = %s AND status = 'proposed' AND NOT seen", (self.org_id,)) or 0)

    def treated_today(self) -> int:
        with db.connect(self.dsn) as conn:
            return int(db.scalar(conn, "SELECT COUNT(*) FROM mail_items WHERE org_id = %s AND status IN ('moved', 'skipped') AND decided_at >= date_trunc('day', now() AT TIME ZONE 'Europe/Paris') AT TIME ZONE 'Europe/Paris'", (self.org_id,)) or 0)

    def frequent_senders(self, min_count: int = 2, limit: int = 200) -> list[dict[str, Any]]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(
                conn,
                "SELECT sender, COUNT(*) AS n, MAX(COALESCE(mailed_at, created_at)) AS last_at FROM mail_items WHERE org_id = %s AND category <> 'spam' GROUP BY sender HAVING COUNT(*) >= %s ORDER BY n DESC LIMIT %s",
                (self.org_id, min_count, limit),
            )
        return [db.jsonable(r) or {} for r in rows]

    def items_from(self, email: str, limit: int = 20) -> list[dict[str, Any]]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM mail_items WHERE org_id = %s AND lower(sender) LIKE %s" + ORDER + " LIMIT %s", (self.org_id, f"%{email.lower()}%", limit))
        return [_row(r) for r in rows]  # type: ignore[misc]

    # --- mémoire expéditeur, fils ------------------------------------------------------------

    def memory_lookup(self, sender: str) -> dict[str, Any] | None:
        email = (parse_email(sender) or sender or "").strip().lower()
        if not email:
            return None
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT category, hits FROM mail_sender_memory WHERE org_id = %s AND sender_email = %s", (self.org_id, email))
        if not row or int(row["hits"] or 0) < MEMORY_HITS:
            return None
        return {"category": row["category"], "reason": "Expéditeur déjà classé comme ça.", "summary": "", "confidence": 0.86, "via": "memory"}

    def memory_remember(self, sender: str, category: str) -> None:
        email = (parse_email(sender) or sender or "").strip().lower()
        if not email or category not in {"todo", "waiting", "read", "newsletters", "spam"}:
            return
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                """
                INSERT INTO mail_sender_memory (org_id, sender_email, category, hits) VALUES (%s, %s, %s, 1)
                ON CONFLICT (org_id, sender_email) DO UPDATE SET
                  hits = CASE WHEN mail_sender_memory.category = EXCLUDED.category THEN mail_sender_memory.hits + 1 ELSE 1 END,
                  category = EXCLUDED.category, updated_at = now()
                """,
                (self.org_id, email, category),
            )

    def memory_forget(self, sender: str) -> None:
        email = (parse_email(sender) or sender or "").strip().lower()
        if email:
            with db.connect(self.dsn) as conn:
                db.execute(conn, "DELETE FROM mail_sender_memory WHERE org_id = %s AND sender_email = %s", (self.org_id, email))

    def category_for_thread(self, subject: str, skip_uid: str = "", rows: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        key = thread_key(subject)
        if not key:
            return None
        rows = rows if rows is not None else self.list_items(statuses=["proposed", "moved"], limit=80)
        for row in rows:
            if skip_uid and str(row.get("imap_uid") or "") == str(skip_uid):
                continue
            if thread_key(row.get("subject") or "") != key:
                continue
            return {"category": row["category"], "reason": "Même fil déjà classé.", "summary": short_summary(row.get("excerpt") or "", row.get("reason") or ""), "confidence": min(0.84, float(row.get("confidence") or 0.7) + 0.05), "via": "thread"}
        return None

    # --- index INBOX ----------------------------------------------------------------------------

    def upsert_index(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with db.connect(self.dsn) as conn:
            for item in rows:
                uid = str(item.get("imap_uid") or "").strip()
                validity = str(item.get("uidvalidity") or "").strip()
                if not uid or not validity:
                    continue
                folder = str(item.get("folder") or "INBOX")
                if folder != "INBOX" and item.get("folder_uid"):
                    uid = str(item["folder_uid"])
                item_id = item.get("id") or item.get("item_id")
                row = db.fetch_one(
                    conn,
                    """
                    INSERT INTO mail_index (org_id, imap_uid, uidvalidity, folder, message_id, sender, subject, body, summary, mailed_at, item_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (org_id, uidvalidity, folder, imap_uid) DO UPDATE SET message_id = EXCLUDED.message_id, sender = EXCLUDED.sender,
                      subject = EXCLUDED.subject, body = EXCLUDED.body, summary = EXCLUDED.summary,
                      mailed_at = COALESCE(EXCLUDED.mailed_at, mail_index.mailed_at), item_id = COALESCE(EXCLUDED.item_id, mail_index.item_id)
                    RETURNING *
                    """,
                    (self.org_id, uid, validity, folder, (item.get("message_id") or "")[:500], (item.get("sender") or "")[:400], (item.get("subject") or "")[:500], (item.get("body") or item.get("excerpt") or "")[:12000], (item.get("summary") or "")[:1200], _ts(item.get("mailed_at")), int(item_id) if item_id not in (None, "") else None),
                )
                if row:
                    out.append(db.jsonable(row) or {})
        return out

    def seed_index_from_items(self) -> list[dict[str, Any]]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT i.* FROM mail_items i LEFT JOIN mail_index x ON x.item_id = i.id WHERE i.org_id = %s AND x.id IS NULL", (self.org_id,))
        packed = []
        for r in rows:
            item = _row(r) or {}
            packed.append({**item, "body": item.get("excerpt") or "", "item_id": item["id"]})
        return self.upsert_index(packed)

    def indexed_uids(self, uidvalidity: str, folder: str = "INBOX") -> set[str]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT imap_uid FROM mail_index WHERE org_id = %s AND uidvalidity = %s AND folder = %s", (self.org_id, uidvalidity, folder))
        return {str(r["imap_uid"]) for r in rows}

    def index_count(self) -> int:
        with db.connect(self.dsn) as conn:
            return int(db.scalar(conn, "SELECT COUNT(*) FROM mail_index WHERE org_id = %s", (self.org_id,)) or 0)

    def index_get(self, index_id: int) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            return db.jsonable(db.fetch_one(conn, "SELECT * FROM mail_index WHERE id = %s", (index_id,)))

    def index_rows(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM mail_index WHERE id = ANY(%s)", ([int(i) for i in ids],))
        return [db.jsonable(r) or {} for r in rows]

    def clear_index(self) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "DELETE FROM mail_index WHERE org_id = %s", (self.org_id,))

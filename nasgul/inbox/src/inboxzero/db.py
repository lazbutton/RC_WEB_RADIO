from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  imap_uid TEXT NOT NULL,
  uidvalidity TEXT NOT NULL,
  message_id TEXT,
  sender TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '',
  excerpt TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'proposed',
  error TEXT,
  draft TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  mailed_at TEXT NOT NULL DEFAULT '',
  notion_url TEXT NOT NULL DEFAULT '',
  attachments TEXT NOT NULL DEFAULT '',
  in_reply_to TEXT NOT NULL DEFAULT '',
  seen INTEGER NOT NULL DEFAULT 1,
  flagged INTEGER NOT NULL DEFAULT 0,
  folder TEXT NOT NULL DEFAULT 'INBOX',
  folder_uid TEXT NOT NULL DEFAULT '',
  excerpt_clean TEXT NOT NULL DEFAULT '',
  summary_full TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  decided_at TEXT,
  UNIQUE (uidvalidity, imap_uid)
);

CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

CREATE TABLE IF NOT EXISTS sender_memory (
  sender_email TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mail_index (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  imap_uid TEXT NOT NULL,
  uidvalidity TEXT NOT NULL,
  folder TEXT NOT NULL DEFAULT 'INBOX',
  message_id TEXT NOT NULL DEFAULT '',
  sender TEXT NOT NULL DEFAULT '',
  subject TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  mailed_at TEXT NOT NULL DEFAULT '',
  item_id INTEGER,
  UNIQUE (uidvalidity, folder, imap_uid)
);

CREATE INDEX IF NOT EXISTS idx_mail_index_item ON mail_index(item_id);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 2,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued',
  progress TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  item_ids TEXT NOT NULL DEFAULT '[]',
  before TEXT NOT NULL DEFAULT '{}',
  after TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  done_at TEXT,
  undone_at TEXT
);
"""

DEFAULTS = {
    "auto_mode": "0",
    "auto_move_newsletters": "0",
    "auto_move_spam": "0",
    "extra_prompt": "",
    "signature": "",
    "notion_token": "",
    "mark_read_on_open": "0",
    "density": "comfortable",
}

MEMORY_HITS = 2
SIGNATURE_MAX = 800
EXCERPT_CLEAN_MAX = 6000
SUMMARY_FULL_MAX = 1600

ITEM_COLUMNS = {
    "draft": "TEXT NOT NULL DEFAULT ''",
    "summary": "TEXT NOT NULL DEFAULT ''",
    "mailed_at": "TEXT NOT NULL DEFAULT ''",
    "notion_url": "TEXT NOT NULL DEFAULT ''",
    "attachments": "TEXT NOT NULL DEFAULT ''",
    "in_reply_to": "TEXT NOT NULL DEFAULT ''",
    "seen": "INTEGER NOT NULL DEFAULT 1",
    "flagged": "INTEGER NOT NULL DEFAULT 0",
    "folder": "TEXT NOT NULL DEFAULT 'INBOX'",
    "folder_uid": "TEXT NOT NULL DEFAULT ''",
    "excerpt_clean": "TEXT NOT NULL DEFAULT ''",
    "summary_full": "TEXT NOT NULL DEFAULT ''",
}


def apply_signature(draft: str, signature: str) -> str:
    body = (draft or "").rstrip()
    sig = (signature or "").strip()[:SIGNATURE_MAX]
    if not body or not sig:
        return body
    if body.endswith(sig):
        return body
    return f"{body}\n\n{sig}"


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class _ThreadState(threading.local):
    def __init__(self) -> None:
        self.conns: dict[str, sqlite3.Connection] = {}
        self.depth: dict[str, int] = {}


_state = _ThreadState()
_wal_done: set[str] = set()
_wal_lock = threading.Lock()


def _open(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    with _wal_lock:
        if db_path not in _wal_done:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
            _wal_done.add(db_path)
    return conn


def _conn(db_path: str) -> sqlite3.Connection:
    conn = _state.conns.get(db_path)
    if conn is None:
        conn = _open(db_path)
        _state.conns[db_path] = conn
    return conn


def close_thread_connections() -> None:
    for conn in _state.conns.values():
        try:
            conn.close()
        except sqlite3.Error:
            pass
    _state.conns.clear()
    _state.depth.clear()


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """One connection per thread; nested calls share the outer transaction."""
    conn = _conn(db_path)
    depth = _state.depth.get(db_path, 0)
    _state.depth[db_path] = depth + 1
    outer = depth == 0
    try:
        if outer:
            conn.execute("BEGIN")
        yield conn
        if outer:
            conn.execute("COMMIT")
    except BaseException:
        if outer and conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        _state.depth[db_path] = depth
        if conn.in_transaction and outer:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass


transaction = connect


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    for name, ddl in ITEM_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {ddl}")


def _ensure_mail_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS mail_fts USING fts5(
          sender,
          subject,
          body,
          summary,
          content='mail_index',
          content_rowid='id',
          tokenize='unicode61 remove_diacritics 2'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS mail_index_ai AFTER INSERT ON mail_index BEGIN
          INSERT INTO mail_fts(rowid, sender, subject, body, summary)
          VALUES (new.id, new.sender, new.subject, new.body, new.summary);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS mail_index_ad AFTER DELETE ON mail_index BEGIN
          INSERT INTO mail_fts(mail_fts, rowid, sender, subject, body, summary)
          VALUES ('delete', old.id, old.sender, old.subject, old.body, old.summary);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS mail_index_au AFTER UPDATE ON mail_index BEGIN
          INSERT INTO mail_fts(mail_fts, rowid, sender, subject, body, summary)
          VALUES ('delete', old.id, old.sender, old.subject, old.body, old.summary);
          INSERT INTO mail_fts(rowid, sender, subject, body, summary)
          VALUES (new.id, new.sender, new.subject, new.body, new.summary);
        END
        """
    )


def _backfill_derived(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT * FROM items WHERE (excerpt_clean = '' AND excerpt != '') OR (summary_full = '' AND (summary != '' OR reason != ''))"
    ).fetchall()
    for row in rows:
        item = dict(row)
        derived = derived_fields(item)
        conn.execute(
            "UPDATE items SET excerpt_clean = ?, summary_full = ? WHERE id = ?",
            (derived["excerpt_clean"], derived["summary_full"], item["id"]),
        )


def init_db(db_path: str) -> None:
    conn = _conn(db_path)
    if conn.in_transaction:
        conn.execute("COMMIT")
    conn.executescript(SCHEMA)
    with connect(db_path) as tx:
        _ensure_columns(tx)
        tx.execute("CREATE INDEX IF NOT EXISTS idx_items_status_mailed ON items(status, mailed_at)")
        _ensure_mail_index(tx)
        for key, value in DEFAULTS.items():
            tx.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )
        _backfill_derived(tx)


def jobs_reset_stale(db_path: str) -> int:
    """At process start: jobs left queued/running by a previous process will never finish."""
    with connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'failed', error = 'redémarrage', finished_at = ? WHERE status IN ('queued', 'running')",
            (utcnow(),),
        )
        return int(cur.rowcount or 0)


def unread_count(db_path: str) -> int:
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM items WHERE status = 'proposed' AND seen = 0").fetchone()
    return int(row["n"] if row else 0)


def derived_fields(item: dict[str, Any]) -> dict[str, str]:
    from inboxzero.classify import with_research_links
    from inboxzero.sanitize import text as sanitize_text
    from inboxzero.threads import visible_body

    raw_excerpt = sanitize_text(item.get("excerpt") or "")
    summary = sanitize_text((item.get("summary") or "").strip()) or (item.get("reason") or "").strip()
    category = item.get("category") or ""
    if category not in {"newsletters", "spam"}:
        summary = sanitize_text(with_research_links(summary, raw_excerpt))
    return {
        "excerpt_clean": sanitize_text(visible_body(raw_excerpt))[:EXCERPT_CLEAN_MAX],
        "summary_full": summary[:SUMMARY_FULL_MAX],
    }


def get_setting(db_path: str, key: str) -> str:
    with connect(db_path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else DEFAULTS.get(key, "")


def set_setting(db_path: str, key: str, value: str) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_settings_map(db_path: str) -> dict[str, str]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    out = dict(DEFAULTS)
    out.update({r["key"]: r["value"] for r in rows})
    return out


def known_uid(db_path: str, uidvalidity: str, imap_uid: str) -> bool:
    return get_item_by_uid(db_path, uidvalidity, imap_uid) is not None


def get_item_by_uid(db_path: str, uidvalidity: str, imap_uid: str) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM items WHERE uidvalidity = ? AND imap_uid = ?",
            (uidvalidity, imap_uid),
        ).fetchone()
    return dict(row) if row else None


def items_by_uids(db_path: str, uidvalidity: str, uids: list[str]) -> dict[str, dict[str, Any]]:
    wanted = [str(uid) for uid in uids if str(uid).strip()]
    if not wanted:
        return {}
    out: dict[str, dict[str, Any]] = {}
    with connect(db_path) as conn:
        for start in range(0, len(wanted), 400):
            chunk = wanted[start : start + 400]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT * FROM items WHERE uidvalidity = ? AND imap_uid IN ({placeholders})",
                [uidvalidity, *chunk],
            ).fetchall()
            for row in rows:
                out[str(row["imap_uid"])] = dict(row)
    return out


def update_item(db_path: str, item_id: int, item: dict[str, Any]) -> None:
    with connect(db_path) as conn:
        current = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        merged = dict(current) if current else {}
        merged.update({k: v for k, v in item.items() if k != "attachments"})
        derived = derived_fields(merged)
        conn.execute(
            """
            UPDATE items SET
              sender = ?, subject = ?, excerpt = ?, category = ?, reason = ?,
              confidence = ?, status = ?, error = ?, draft = ?, summary = ?,
              mailed_at = CASE WHEN ? != '' THEN ? ELSE mailed_at END,
              attachments = CASE WHEN ? != '' THEN ? ELSE attachments END,
              in_reply_to = CASE WHEN ? != '' THEN ? ELSE in_reply_to END,
              excerpt_clean = ?, summary_full = ?
            WHERE id = ?
            """,
            (
                item.get("sender") or "",
                item.get("subject") or "",
                item.get("excerpt") or "",
                item["category"],
                item.get("reason") or "",
                float(item.get("confidence") or 0),
                item.get("status") or "proposed",
                item.get("error"),
                item.get("draft") or "",
                item.get("summary") or "",
                item.get("mailed_at") or "",
                item.get("mailed_at") or "",
                _attachments_value(item),
                _attachments_value(item),
                (item.get("in_reply_to") or "")[:500],
                (item.get("in_reply_to") or "")[:500],
                derived["excerpt_clean"],
                derived["summary_full"],
                item_id,
            ),
        )
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row:
            _upsert_mail_index_conn(conn, dict(row))


def insert_item(db_path: str, item: dict[str, Any]) -> int:
    derived = derived_fields(item)
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO items (
              imap_uid, uidvalidity, message_id, sender, subject, excerpt,
              category, reason, confidence, status, error, draft, summary,
              mailed_at, attachments, in_reply_to, seen, flagged, folder,
              excerpt_clean, summary_full, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["imap_uid"],
                item["uidvalidity"],
                item.get("message_id"),
                item.get("sender") or "",
                item.get("subject") or "",
                item.get("excerpt") or "",
                item["category"],
                item.get("reason") or "",
                float(item.get("confidence") or 0),
                item.get("status") or "proposed",
                item.get("error"),
                item.get("draft") or "",
                item.get("summary") or "",
                item.get("mailed_at") or "",
                _attachments_value(item),
                (item.get("in_reply_to") or "")[:500],
                1 if item.get("seen", 1) else 0,
                1 if item.get("flagged") else 0,
                str(item.get("folder") or "INBOX"),
                derived["excerpt_clean"],
                derived["summary_full"],
                utcnow(),
            ),
        )
        item_id = int(cur.lastrowid)
        _upsert_mail_index_conn(conn, {**item, "id": item_id})
        return item_id


ORDER_SQL = " ORDER BY COALESCE(NULLIF(mailed_at, ''), created_at) DESC, id DESC"


def list_items(
    db_path: str,
    status: str | None = "proposed",
    statuses: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM items"
    args: list[Any] = []
    if statuses:
        placeholders = ",".join("?" * len(statuses))
        sql += f" WHERE status IN ({placeholders})"
        args.extend(statuses)
    elif status:
        sql += " WHERE status = ?"
        args.append(status)
    sql += ORDER_SQL + " LIMIT ?"
    args.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def queue_snapshot(db_path: str, done: bool, limit: int) -> dict[str, Any]:
    """Items, counters and settings in a single transaction."""
    with connect(db_path) as conn:
        if done:
            rows = conn.execute(
                "SELECT * FROM items WHERE status IN ('moved', 'skipped')" + ORDER_SQL + " LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM items WHERE status = 'proposed'" + ORDER_SQL + " LIMIT ?",
                (limit,),
            ).fetchall()
        counts = {
            r["status"]: int(r["n"])
            for r in conn.execute("SELECT status, COUNT(*) AS n FROM items GROUP BY status").fetchall()
        }
        cat_counts = {
            r["category"]: int(r["n"])
            for r in conn.execute(
                "SELECT category, COUNT(*) AS n FROM items WHERE status = 'proposed' GROUP BY category"
            ).fetchall()
        }
        unread = conn.execute(
            "SELECT COUNT(*) AS n FROM items WHERE status = 'proposed' AND seen = 0"
        ).fetchone()
        settings_rows = conn.execute("SELECT key, value FROM settings").fetchall()
        version = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS m, COUNT(*) AS n FROM items"
        ).fetchone()
        actions_version = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM actions").fetchone()
    settings_map = dict(DEFAULTS)
    settings_map.update({r["key"]: r["value"] for r in settings_rows})
    return {
        "items": [dict(r) for r in rows],
        "counts": counts,
        "cat_counts": cat_counts,
        "unread": int(unread["n"] if unread else 0),
        "settings": settings_map,
        "version": f"{version['m']}-{version['n']}-{actions_version['m']}",
    }


def get_item(db_path: str, item_id: int) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def get_items(db_path: str, ids: list[int]) -> list[dict[str, Any]]:
    wanted: list[int] = []
    for item_id in ids:
        try:
            value = int(item_id)
        except (TypeError, ValueError):
            continue
        if value:
            wanted.append(value)
    if not wanted:
        return []
    placeholders = ",".join("?" * len(wanted))
    with connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM items WHERE id IN ({placeholders})", wanted).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    return [by_id[item_id] for item_id in wanted if item_id in by_id]


def set_status(db_path: str, item_id: int, status: str, error: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE items SET status = ?, error = ?, decided_at = ? WHERE id = ?",
            (status, error, utcnow(), item_id),
        )


def set_status_many(db_path: str, item_ids: list[int], status: str) -> None:
    if not item_ids:
        return
    with connect(db_path) as conn:
        now = utcnow()
        for item_id in item_ids:
            conn.execute(
                "UPDATE items SET status = ?, decided_at = ? WHERE id = ?",
                (status, now, int(item_id)),
            )


def set_draft(db_path: str, item_id: int, draft: str) -> None:
    with connect(db_path) as conn:
        conn.execute("UPDATE items SET draft = ? WHERE id = ?", (draft[:4000], item_id))


def set_summary(db_path: str, item_id: int, summary: str) -> None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return
        item = dict(row)
        item["summary"] = summary[:1200]
        derived = derived_fields(item)
        conn.execute(
            "UPDATE items SET summary = ?, summary_full = ? WHERE id = ?",
            (summary[:1200], derived["summary_full"], item_id),
        )
        conn.execute(
            "UPDATE mail_index SET summary = ? WHERE item_id = ?",
            (summary[:1200], item_id),
        )


def set_mailed_at(db_path: str, item_id: int, mailed_at: str) -> None:
    with connect(db_path) as conn:
        conn.execute("UPDATE items SET mailed_at = ? WHERE id = ?", ((mailed_at or "")[:64], item_id))


def set_notion_url(db_path: str, item_id: int, url: str) -> None:
    with connect(db_path) as conn:
        conn.execute("UPDATE items SET notion_url = ? WHERE id = ?", ((url or "")[:500], item_id))


def set_attachments(db_path: str, item_id: int, raw: str) -> None:
    with connect(db_path) as conn:
        conn.execute("UPDATE items SET attachments = ? WHERE id = ?", ((raw or "")[:20000], item_id))


def set_in_reply_to(db_path: str, item_id: int, value: str) -> None:
    with connect(db_path) as conn:
        conn.execute("UPDATE items SET in_reply_to = ? WHERE id = ?", ((value or "")[:500], item_id))


def set_flags(db_path: str, item_ids: list[int], seen: bool | None = None, flagged: bool | None = None) -> None:
    if not item_ids:
        return
    with connect(db_path) as conn:
        for item_id in item_ids:
            if seen is not None:
                conn.execute("UPDATE items SET seen = ? WHERE id = ?", (1 if seen else 0, int(item_id)))
            if flagged is not None:
                conn.execute("UPDATE items SET flagged = ? WHERE id = ?", (1 if flagged else 0, int(item_id)))


def sync_flags(db_path: str, uidvalidity: str, flags: dict[str, tuple[bool, bool]]) -> int:
    """flags: uid -> (seen, flagged). Returns number of changed rows."""
    if not flags:
        return 0
    changed = 0
    with connect(db_path) as conn:
        for uid, (seen, flagged) in flags.items():
            cur = conn.execute(
                """
                UPDATE items SET seen = ?, flagged = ?
                WHERE uidvalidity = ? AND imap_uid = ? AND folder = 'INBOX' AND (seen != ? OR flagged != ?)
                """,
                (1 if seen else 0, 1 if flagged else 0, uidvalidity, str(uid), 1 if seen else 0, 1 if flagged else 0),
            )
            changed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return changed


def set_folder(db_path: str, item_id: int, folder: str, folder_uid: str, status: str | None = None) -> None:
    with connect(db_path) as conn:
        if status:
            conn.execute(
                "UPDATE items SET folder = ?, folder_uid = ?, status = ?, decided_at = ? WHERE id = ?",
                (folder[:200], (folder_uid or "")[:64], status, utcnow(), item_id),
            )
        else:
            conn.execute(
                "UPDATE items SET folder = ?, folder_uid = ? WHERE id = ?",
                (folder[:200], (folder_uid or "")[:64], item_id),
            )
        row = conn.execute("SELECT imap_uid FROM items WHERE id = ?", (item_id,)).fetchone()
        if row:
            index_uid = folder_uid if (folder != "INBOX" and folder_uid) else row["imap_uid"]
            conn.execute(
                "DELETE FROM mail_index WHERE item_id = ? AND id NOT IN (SELECT MIN(id) FROM mail_index WHERE item_id = ?)",
                (item_id, item_id),
            )
            conn.execute(
                "UPDATE OR IGNORE mail_index SET folder = ?, imap_uid = ? WHERE item_id = ?",
                (folder[:200], index_uid, item_id),
            )


def restore_inbox(db_path: str, item_id: int, new_uid: str) -> None:
    """Back from an archive folder: the message got a fresh INBOX uid."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT imap_uid, uidvalidity FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return
        uid = (new_uid or row["imap_uid"] or "").strip()
        clash = conn.execute(
            "SELECT id FROM items WHERE uidvalidity = ? AND imap_uid = ? AND id != ?",
            (row["uidvalidity"], uid, item_id),
        ).fetchone()
        if clash:
            conn.execute("DELETE FROM items WHERE id = ?", (clash["id"],))
            conn.execute("DELETE FROM mail_index WHERE item_id = ?", (clash["id"],))
        conn.execute(
            "UPDATE items SET imap_uid = ?, folder = 'INBOX', folder_uid = '', status = 'proposed', decided_at = NULL WHERE id = ?",
            (uid, item_id),
        )
        conn.execute("DELETE FROM mail_index WHERE uidvalidity = ? AND folder = 'INBOX' AND imap_uid = ? AND item_id != ?", (row["uidvalidity"], uid, item_id))
        conn.execute(
            "UPDATE OR IGNORE mail_index SET folder = 'INBOX', imap_uid = ? WHERE item_id = ?",
            (uid, item_id),
        )


def mark_gone(db_path: str, uidvalidity: str, present_uids: set[str]) -> list[int]:
    """Items that left INBOX behind our back (Roundcube) leave the queue."""
    gone: list[int] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, imap_uid FROM items WHERE uidvalidity = ? AND folder = 'INBOX' AND status IN ('proposed', 'skipped')",
            (uidvalidity,),
        ).fetchall()
        now = utcnow()
        for row in rows:
            if str(row["imap_uid"]) in present_uids:
                continue
            conn.execute(
                "UPDATE items SET status = 'gone', decided_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            gone.append(int(row["id"]))
    return gone


def inbox_uids_for_status(db_path: str, uidvalidity: str, statuses: tuple[str, ...] = ("proposed", "skipped")) -> dict[str, int]:
    placeholders = ",".join("?" * len(statuses))
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT id, imap_uid FROM items WHERE uidvalidity = ? AND folder = 'INBOX' AND status IN ({placeholders})",
            (uidvalidity, *statuses),
        ).fetchall()
    return {str(row["imap_uid"]): int(row["id"]) for row in rows}


def _attachments_value(item: dict[str, Any]) -> str:
    from inboxzero.attachments import encode_list

    if "attachments" not in item:
        return ""
    return encode_list(item.get("attachments"))


def _memory_email(sender_email: str) -> str:
    from inboxzero.ui import sender_email as parse_email

    return (parse_email(sender_email) or sender_email or "").strip().lower()


def memory_lookup(db_path: str, sender_email: str) -> dict[str, Any] | None:
    email = _memory_email(sender_email)
    if not email:
        return None
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT category, hits FROM sender_memory WHERE sender_email = ?",
            (email,),
        ).fetchone()
    if not row or int(row["hits"] or 0) < MEMORY_HITS:
        return None
    return {
        "category": row["category"],
        "reason": "Expéditeur déjà classé comme ça.",
        "summary": "",
        "confidence": 0.86,
        "via": "memory",
    }


def memory_remember(db_path: str, sender_email: str, category: str) -> None:
    email = _memory_email(sender_email)
    if not email or category not in {"todo", "waiting", "read", "newsletters", "spam"}:
        return
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sender_memory(sender_email, category, hits, updated_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(sender_email) DO UPDATE SET
              hits = CASE WHEN sender_memory.category = excluded.category THEN sender_memory.hits + 1 ELSE 1 END,
              category = excluded.category,
              updated_at = excluded.updated_at
            """,
            (email, category, utcnow()),
        )


def memory_forget(db_path: str, sender_email: str) -> None:
    email = _memory_email(sender_email)
    if not email:
        return
    with connect(db_path) as conn:
        conn.execute("DELETE FROM sender_memory WHERE sender_email = ?", (email,))


def category_for_thread(
    db_path: str,
    subject: str,
    skip_uid: str = "",
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    from inboxzero.threads import short_summary, thread_key

    key = thread_key(subject)
    if not key:
        return None
    if rows is None:
        rows = list_items(db_path, statuses=["proposed", "moved"], limit=80)
    for row in rows:
        if skip_uid and str(row.get("imap_uid") or "") == str(skip_uid):
            continue
        if thread_key(row.get("subject") or "") != key:
            continue
        return {
            "category": row["category"],
            "reason": "Même fil déjà classé.",
            "summary": short_summary(row.get("excerpt") or "", row.get("reason") or ""),
            "confidence": min(0.84, float(row.get("confidence") or 0.7) + 0.05),
            "via": "thread",
        }
    return None


def counts(db_path: str) -> dict[str, int]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM items GROUP BY status").fetchall()
    return {r["status"]: int(r["n"]) for r in rows}


def counts_by_category(db_path: str, status: str = "proposed") -> dict[str, int]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM items WHERE status = ? GROUP BY category",
            (status,),
        ).fetchall()
    return {r["category"]: int(r["n"]) for r in rows}


def treated_today(db_path: str, day_prefix: str) -> int:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM items WHERE status IN ('moved', 'skipped') AND decided_at LIKE ?",
            (f"{day_prefix}%",),
        ).fetchone()
    return int(row["n"] if row else 0)


# --- recherche -------------------------------------------------------------


def _upsert_mail_index_conn(conn: sqlite3.Connection, item: dict[str, Any], folder: str | None = None) -> None:
    from inboxzero.search import index_body

    uid = str(item.get("imap_uid") or "").strip()
    validity = str(item.get("uidvalidity") or "").strip()
    if not uid or not validity:
        return
    folder_name = folder or str(item.get("folder") or "INBOX")
    if folder_name != "INBOX" and item.get("folder_uid"):
        uid = str(item.get("folder_uid"))
    item_id = item.get("id") or item.get("item_id")
    try:
        packed_id = int(item_id) if item_id not in (None, "") else None
    except (TypeError, ValueError):
        packed_id = None
    if packed_id is not None:
        conn.execute(
            "DELETE FROM mail_index WHERE item_id = ? AND NOT (uidvalidity = ? AND folder = ? AND imap_uid = ?)",
            (packed_id, validity, folder_name, uid),
        )
    conn.execute(
        """
        INSERT INTO mail_index (
          imap_uid, uidvalidity, folder, message_id, sender, subject, body, summary, mailed_at, item_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(uidvalidity, folder, imap_uid) DO UPDATE SET
          message_id = excluded.message_id,
          sender = excluded.sender,
          subject = excluded.subject,
          body = excluded.body,
          summary = excluded.summary,
          mailed_at = CASE WHEN excluded.mailed_at != '' THEN excluded.mailed_at ELSE mail_index.mailed_at END,
          item_id = COALESCE(excluded.item_id, mail_index.item_id)
        """,
        (
            uid,
            validity,
            folder_name,
            str(item.get("message_id") or "")[:500],
            str(item.get("sender") or "")[:400],
            str(item.get("subject") or "")[:500],
            index_body(item),
            str(item.get("summary") or "")[:1200],
            str(item.get("mailed_at") or "")[:64],
            packed_id,
        ),
    )


def upsert_mail_index(db_path: str, item: dict[str, Any], folder: str = "INBOX") -> None:
    with connect(db_path) as conn:
        _upsert_mail_index_conn(conn, item, folder=folder)


def upsert_mail_index_many(db_path: str, items: list[dict[str, Any]], folder: str = "INBOX") -> int:
    with connect(db_path) as conn:
        for item in items:
            _upsert_mail_index_conn(conn, item, folder=folder)
    return len(items)


def seed_mail_index_from_items(db_path: str) -> int:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT items.* FROM items
            LEFT JOIN mail_index ON mail_index.item_id = items.id
            WHERE mail_index.id IS NULL
            """
        ).fetchall()
        for row in rows:
            _upsert_mail_index_conn(conn, dict(row))
        return len(rows)


def indexed_uids(db_path: str, uidvalidity: str, folder: str = "INBOX") -> set[str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT imap_uid FROM mail_index WHERE uidvalidity = ? AND folder = ?",
            (uidvalidity, folder),
        ).fetchall()
    return {str(row["imap_uid"]) for row in rows}


def mail_index_count(db_path: str) -> int:
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM mail_index").fetchone()
    return int(row["n"] if row else 0)


def mail_index_get(db_path: str, index_id: int) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM mail_index WHERE id = ?", (index_id,)).fetchone()
    return dict(row) if row else None


def clear_mail_index(db_path: str) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM mail_index")


def search_mails(db_path: str, query: str, limit: int = 40) -> list[dict[str, Any]]:
    from inboxzero.search import fts_query

    match = fts_query(query)
    if not match:
        return []
    sql = """
        SELECT mail_index.*, snippet(mail_fts, 2, '', '', ' … ', 18) AS hit,
               items.status AS item_status, items.category AS item_category,
               items.seen AS item_seen, items.flagged AS item_flagged
        FROM mail_fts
        JOIN mail_index ON mail_index.id = mail_fts.rowid
        LEFT JOIN items ON items.id = mail_index.item_id
        WHERE mail_fts MATCH ?
        ORDER BY rank, mail_index.mailed_at DESC
        LIMIT ?
    """
    with connect(db_path) as conn:
        try:
            rows = conn.execute(sql, (match, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(row) for row in rows]


# --- jobs ---------------------------------------------------------------------


def job_create(db_path: str, kind: str, payload: dict[str, Any] | None = None, priority: int = 2) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO jobs(kind, priority, payload, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
            (kind, int(priority), json.dumps(payload or {}, ensure_ascii=False), utcnow()),
        )
        return int(cur.lastrowid)


def job_update(
    db_path: str,
    job_id: int,
    *,
    status: str | None = None,
    progress: str | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    sets: list[str] = []
    args: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        args.append(status)
        if status == "running":
            sets.append("started_at = ?")
            args.append(utcnow())
        if status in {"done", "failed"}:
            sets.append("finished_at = ?")
            args.append(utcnow())
    if progress is not None:
        sets.append("progress = ?")
        args.append(progress[:300])
    if result is not None:
        sets.append("result = ?")
        args.append(json.dumps(result, ensure_ascii=False)[:20000])
    if error is not None:
        sets.append("error = ?")
        args.append(error[:800])
    if not sets:
        return
    args.append(job_id)
    with connect(db_path) as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)


def job_get(db_path: str, job_id: int) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _job_row(row) if row else None


def _job_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key in ("payload", "result"):
        raw = item.get(key) or ""
        try:
            item[key] = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            item[key] = {}
    return item


def jobs_recent(db_path: str, limit: int = 20) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_job_row(row) for row in rows]


def jobs_prune(db_path: str, keep: int = 500) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "DELETE FROM jobs WHERE status IN ('done', 'failed') AND id NOT IN (SELECT id FROM jobs ORDER BY id DESC LIMIT ?)",
            (keep,),
        )


# --- actions -------------------------------------------------------------------


def action_create(
    db_path: str,
    kind: str,
    item_ids: list[int],
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    label: str = "",
) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO actions(kind, label, item_ids, before, after, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                kind,
                label[:200],
                json.dumps([int(i) for i in item_ids]),
                json.dumps(before or {}, ensure_ascii=False),
                json.dumps(after or {}, ensure_ascii=False),
                utcnow(),
            ),
        )
        return int(cur.lastrowid)


def action_update(
    db_path: str,
    action_id: int,
    *,
    status: str | None = None,
    error: str | None = None,
    after: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    label: str | None = None,
) -> None:
    sets: list[str] = []
    args: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        args.append(status)
        if status == "done":
            sets.append("done_at = ?")
            args.append(utcnow())
        if status == "undone":
            sets.append("undone_at = ?")
            args.append(utcnow())
    if error is not None:
        sets.append("error = ?")
        args.append(error[:800])
    if after is not None:
        sets.append("after = ?")
        args.append(json.dumps(after, ensure_ascii=False))
    if before is not None:
        sets.append("before = ?")
        args.append(json.dumps(before, ensure_ascii=False))
    if label is not None:
        sets.append("label = ?")
        args.append(label[:200])
    if not sets:
        return
    args.append(action_id)
    with connect(db_path) as conn:
        conn.execute(f"UPDATE actions SET {', '.join(sets)} WHERE id = ?", args)


def _action_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key, default in (("item_ids", []), ("before", {}), ("after", {})):
        raw = item.get(key) or ""
        try:
            item[key] = json.loads(raw) if raw else default
        except json.JSONDecodeError:
            item[key] = default
    return item


def action_get(db_path: str, action_id: int) -> dict[str, Any] | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    return _action_row(row) if row else None


def actions_recent(db_path: str, limit: int = 30) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_action_row(row) for row in rows]

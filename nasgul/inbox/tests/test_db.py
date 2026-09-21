from pathlib import Path
import sqlite3

from inboxzero import db


def test_init_db_stores_draft(tmp_path: Path):
    path = str(tmp_path / "inbox.db")
    db.init_db(path)
    item_id = db.insert_item(
        path,
        {
            "imap_uid": "1",
            "uidvalidity": "9",
            "category": "todo",
            "draft": "Salut",
            "summary": "Il faut répondre à Viviane.",
        },
    )
    row = db.get_item(path, item_id)
    assert row is not None
    assert row["draft"] == "Salut"
    assert row["summary"] == "Il faut répondre à Viviane."
    db.set_summary(path, item_id, "Il faut répondre.")
    assert db.get_item(path, item_id)["summary"] == "Il faut répondre."
    db.set_draft(path, item_id, "Coucou")
    assert db.get_item(path, item_id)["draft"] == "Coucou"
    db.set_notion_url(path, item_id, "https://www.notion.so/abc")
    assert db.get_item(path, item_id)["notion_url"] == "https://www.notion.so/abc"
    db.set_attachments(path, item_id, '[{"n":0,"filename":"dossier.pdf","kind":"pdf"}]')
    assert "dossier.pdf" in db.get_item(path, item_id)["attachments"]
    found = db.get_item_by_uid(path, "9", "1")
    assert found is not None
    assert found["id"] == item_id
    db.update_item(
        path,
        item_id,
        {
            "category": "waiting",
            "reason": "reprise",
            "confidence": 0.8,
            "status": "proposed",
            "error": None,
            "draft": "Nouveau",
            "sender": "a@b.test",
            "subject": "Sujet",
            "excerpt": "Corps",
        },
    )
    updated = db.get_item(path, item_id)
    assert updated["category"] == "waiting"
    assert updated["draft"] == "Nouveau"
    assert updated["error"] is None


def test_migrates_missing_draft_column(tmp_path: Path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE items (
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
          created_at TEXT NOT NULL,
          decided_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    db.init_db(path)
    conn = sqlite3.connect(path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    conn.close()
    assert "draft" in cols
    assert "summary" in cols
    assert "mailed_at" in cols
    assert "notion_url" in cols
    assert "attachments" in cols
    assert "in_reply_to" in cols


def test_list_items_newest_mail_first(tmp_path: Path):
    path = str(tmp_path / "inbox.db")
    db.init_db(path)
    db.insert_item(
        path,
        {
            "imap_uid": "1",
            "uidvalidity": "9",
            "category": "read",
            "subject": "Ancien",
            "mailed_at": "2026-09-01T08:00:00+00:00",
        },
    )
    db.insert_item(
        path,
        {
            "imap_uid": "2",
            "uidvalidity": "9",
            "category": "todo",
            "subject": "Récent",
            "mailed_at": "2026-09-18T12:00:00+00:00",
        },
    )
    listed = db.list_items(path, status="proposed", limit=50)
    assert [row["subject"] for row in listed] == ["Récent", "Ancien"]
    db.set_mailed_at(path, listed[1]["id"], "2026-09-18T13:00:00+00:00")
    listed = db.list_items(path, status="proposed", limit=50)
    assert [row["subject"] for row in listed] == ["Ancien", "Récent"]


def test_counts_by_category(tmp_path: Path):
    path = str(tmp_path / "inbox.db")
    db.init_db(path)
    db.insert_item(path, {"imap_uid": "1", "uidvalidity": "9", "category": "todo"})
    db.insert_item(path, {"imap_uid": "2", "uidvalidity": "9", "category": "read"})
    db.insert_item(path, {"imap_uid": "3", "uidvalidity": "9", "category": "read"})
    moved_id = db.insert_item(path, {"imap_uid": "4", "uidvalidity": "9", "category": "spam"})
    db.set_status(path, moved_id, "moved")
    counts = db.counts_by_category(path)
    assert counts == {"todo": 1, "read": 2}
    assert db.counts(path)["proposed"] == 3
    done = db.list_items(path, statuses=["moved", "skipped"], limit=50)
    assert len(done) == 1
    assert done[0]["status"] == "moved"


def test_apply_signature():
    assert db.apply_signature("Salut", "") == "Salut"
    assert db.apply_signature("Salut", "   ") == "Salut"
    assert db.apply_signature("", "—\nLaz") == ""
    signed = db.apply_signature("Salut", "—\nLaz")
    assert signed == "Salut\n\n—\nLaz"
    assert db.apply_signature(signed, "—\nLaz") == signed
    assert db.apply_signature("Salut\n\n—\nLaz\n", "—\nLaz") == "Salut\n\n—\nLaz"
    clipped = db.apply_signature("Ok", "x" * 900)
    assert clipped.endswith("x" * 800)
    assert len(clipped) == 2 + 2 + 800


def test_init_db_inserts_empty_signature(tmp_path: Path):
    path = str(tmp_path / "inbox.db")
    db.init_db(path)
    assert db.get_setting(path, "signature") == ""
    db.set_setting(path, "signature", "—\nLaz")
    db.init_db(path)
    assert db.get_setting(path, "signature") == "—\nLaz"


def test_sender_memory_needs_two_hits(tmp_path: Path):
    path = str(tmp_path / "inbox.db")
    db.init_db(path)
    assert db.memory_lookup(path, "news@x.test") is None
    db.memory_remember(path, "News <news@x.test>", "newsletters")
    assert db.memory_lookup(path, "news@x.test") is None
    db.memory_remember(path, "news@x.test", "newsletters")
    found = db.memory_lookup(path, "news@x.test")
    assert found is not None
    assert found["category"] == "newsletters"
    db.memory_remember(path, "news@x.test", "read")
    assert db.memory_lookup(path, "news@x.test") is None
    db.memory_remember(path, "news@x.test", "read")
    assert db.memory_lookup(path, "news@x.test")["category"] == "read"
    db.memory_forget(path, "news@x.test")
    assert db.memory_lookup(path, "news@x.test") is None

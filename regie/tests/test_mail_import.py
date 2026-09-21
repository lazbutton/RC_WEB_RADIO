from __future__ import annotations

import sqlite3


def test_import_from_inboxzero_sqlite_is_idempotent(kernel, tmp_path):
    from regie.modules import load_modules
    from regie.modules.mail.migrate_sqlite import import_sqlite

    load_modules(kernel, ["regie.modules.mail"])
    path = tmp_path / "inboxzero.db"
    src = sqlite3.connect(path)
    src.executescript(
        """
        CREATE TABLE items (id INTEGER PRIMARY KEY, imap_uid TEXT, uidvalidity TEXT, message_id TEXT, in_reply_to TEXT, sender TEXT, subject TEXT, excerpt TEXT,
          category TEXT, reason TEXT, confidence REAL, status TEXT, error TEXT, draft TEXT, summary TEXT, mailed_at TEXT, notion_url TEXT, attachments TEXT,
          seen INTEGER, flagged INTEGER, folder TEXT, folder_uid TEXT, created_at TEXT, decided_at TEXT);
        CREATE TABLE sender_memory (sender_email TEXT PRIMARY KEY, category TEXT, hits INTEGER, updated_at TEXT);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE mail_index (id INTEGER PRIMARY KEY, imap_uid TEXT, uidvalidity TEXT, folder TEXT, message_id TEXT, sender TEXT, subject TEXT, body TEXT, summary TEXT, mailed_at TEXT, item_id INTEGER);
        CREATE TABLE actions (id INTEGER PRIMARY KEY, kind TEXT, item_ids TEXT, before TEXT, after TEXT, label TEXT, status TEXT, error TEXT, created_at TEXT, done_at TEXT, undone_at TEXT);
        INSERT INTO items VALUES (1, '100', '7', '<a@test>', '', 'Viviane <v@test>', 'Dossier', 'Corps du mail', 'todo', 'Demande', 0.9, 'proposed', NULL, '', 'Résumé', '2026-09-10T10:00:00+00:00', '', '[{"n":1,"filename":"dp.pdf","kind":"pdf","size":1000}]', 0, 1, 'INBOX', '', '2026-09-10T10:05:00+00:00', NULL);
        INSERT INTO items VALUES (2, '101', '7', '<b@test>', '', 'Label <l@test>', 'Promo', 'Newsletter', 'newsletters', 'Pub', 0.95, 'moved', NULL, '', '', '2026-09-11T10:00:00+00:00', '', '[]', 1, 0, 'Inbox Zero/Newsletters', '3', '2026-09-11T10:05:00+00:00', '2026-09-11T11:00:00+00:00');
        INSERT INTO sender_memory VALUES ('l@test', 'newsletters', 3, '');
        INSERT INTO settings VALUES ('signature', '— Laz'), ('notion_token', 'ntn_x'), ('scan_last_uid', '101');
        INSERT INTO mail_index VALUES (1, '100', '7', 'INBOX', '<a@test>', 'Viviane <v@test>', 'Dossier', 'Corps du mail', 'Résumé', '2026-09-10T10:00:00+00:00', 1);
        INSERT INTO mail_index VALUES (2, '50', '7', 'INBOX', '<c@test>', 'Ancien <o@test>', 'Vieux mail', 'Un vieux corps', '', '2025-01-01T10:00:00+00:00', NULL);
        INSERT INTO actions VALUES (1, 'archive', '[2]', '{}', '{"moved":{"2":"Inbox Zero/Newsletters:3"}}', 'Archivé', 'done', '', '2026-09-11T11:00:00+00:00', '2026-09-11T11:00:01+00:00', NULL);
        """
    )
    src.commit()
    src.close()

    counts = import_sqlite(kernel, str(path))
    assert counts["items"] == 2 and counts["memory"] == 1 and counts["actions"] == 1
    store = kernel.modules["mail"].store
    items = store.list_items(statuses=["proposed", "moved"])
    dossier = next(i for i in items if i["subject"] == "Dossier")
    assert dossier["seen"] == 0 and dossier["flagged"] == 1 and dossier["attachments"][0]["filename"] == "dp.pdf"
    promo = next(i for i in items if i["subject"] == "Promo")
    assert promo["folder"] == "Inbox Zero/Newsletters" and promo["status"] == "moved"
    assert kernel.setting("mail.signature") == "— Laz"
    assert kernel.secrets.get("notion", "token") == "ntn_x"
    assert store.index_count() == 3  # 2 lignes reprises + « Promo » ressemé depuis les items
    assert kernel.search.query("vieux")[0]["kind"] == "mail_index"
    again = import_sqlite(kernel, str(path))
    assert again["items"] == 2
    assert len(store.list_items(statuses=["proposed", "moved"])) == 2, "rejouable sans doublon"
    assert kernel.actions.recent(10, module="mail")[0]["kind"] == "mail.archive"

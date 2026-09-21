from __future__ import annotations

from unittest.mock import patch

from inboxzero import db, web
from inboxzero.config import get_settings
from inboxzero.jobs import JobRunner
from inboxzero.web import serialize_item
from inboxzero.worker import Worker

from fake_imap import FakeSession, make_mail


def wait_job(client, job_id: int, timeout: float = 5.0) -> dict:
    worker = web.worker
    assert worker is not None
    job = worker.runner.get(job_id)
    assert job is not None, f"job {job_id} inconnu"
    assert job.done.wait(timeout), f"job {job_id} trop long"
    return job.public()


def _client(tmp_path, monkeypatch, session: FakeSession | None = None, imap: bool = True):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("INBOXZERO_TOKEN", "secret")
    monkeypatch.setenv("INBOXZERO_DATA", str(tmp_path))
    if imap:
        monkeypatch.setenv("IMAP_USER", "u")
        monkeypatch.setenv("IMAP_PASSWORD", "p")
    get_settings.cache_clear()
    web._login_fails.clear()
    fake = session if session is not None else FakeSession()

    def factory(settings):
        runner = JobRunner(settings.db_path, persist=True)
        worker = Worker(settings, session=fake, runner=runner)
        worker.runner.start()
        return worker

    monkeypatch.setattr(web, "run_in_background", factory)
    client = TestClient(web.app)
    client.fake_session = fake  # type: ignore[attr-defined]
    return client


def _seed(path: str, **extra) -> int:
    base = {"imap_uid": "11", "uidvalidity": "1", "category": "read", "sender": "Viviane <viviane@test>", "subject": "Hello", "excerpt": "Corps"}
    base.update(extra)
    return db.insert_item(path, base)


def test_queue_requires_auth(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/queue").status_code == 401
        assert client.post("/api/actions", json={"ids": [1], "kind": "seen"}).status_code == 401


def test_brand_json_public(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        data = client.get("/brand.json").json()
        assert data["suite"]["name"] == "BUTTON"
        assert data["infra"]["smbShare"] == "BUTTON-Media"


def test_health_is_public_and_minimal(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        web.worker.last_error = "IMAP timeout secret"
        payload = client.get("/health").json()
        assert payload["ok"] is True
        assert "IMAP timeout secret" not in str(payload)


def test_login_rate_limit(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        for _ in range(web.LOGIN_MAX_FAILS):
            assert client.post("/api/login", json={"token": "nope"}).status_code == 401
        blocked = client.post("/api/login", json={"token": "nope"})
        assert blocked.status_code == 429
        assert client.post("/api/login", json={"token": "secret"}).status_code == 429


def test_login_and_queue_json_with_etag(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.post("/api/login", json={"token": "nope"}).status_code == 401
        assert client.post("/api/login", json={"token": "secret"}).status_code == 200
        path = get_settings().db_path
        _seed(path, seen=False)
        res = client.get("/api/queue")
        payload = res.json()
        assert payload["items"][0]["sender_name"] == "Viviane"
        assert payload["items"][0]["subject"] == "Hello"
        assert payload["items"][0]["seen"] is False
        assert payload["items"][0]["flagged"] is False
        assert payload["items"][0]["folder"] == "INBOX"
        assert "confidence" not in payload["items"][0]
        assert payload["cat_counts"]["read"] == 1
        assert payload["unread"] == 1
        assert "(" in payload["items"][0]["created_rel"]
        assert payload["counts"].get("moved", 0) == 0
        assert payload["status"] == "proposed"
        assert payload["folders"]["newsletters"] == "Inbox Zero/Newsletters"
        etag = res.headers["etag"]
        again = client.get("/api/queue", headers={"If-None-Match": etag})
        assert again.status_code == 304
        _seed(path, imap_uid="12", subject="Nouveau")
        changed = client.get("/api/queue", headers={"If-None-Match": etag})
        assert changed.status_code == 200
        assert len(changed.json()["items"]) == 2


def test_queue_deep_link_id_included(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        moved = _seed(path, imap_uid="21", subject="Archivé")
        db.set_status(path, moved, "moved")
        _seed(path, imap_uid="22", subject="Encore")
        payload = client.get(f"/api/queue?id={moved}").json()
        assert payload["selected_id"] == moved
        assert any(item["id"] == moved for item in payload["items"])
        single = client.get(f"/api/items/{moved}").json()
        assert single["item"]["status"] == "moved"


def test_settings_roundtrip_without_probe(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        data = client.get("/api/settings").json()
        assert data["auto_mode"] is False
        assert data["anthropic_model"] == "claude-sonnet-5"
        assert data["imap"]["can_move"] is True
        assert data["scan_days"] == 30
        assert data["mark_read_on_open"] is False
        assert data["density"] == "comfortable"
        saved = client.post(
            "/api/settings",
            json={"extra_prompt": "fédé", "signature": "—\nLaz\nRadio Campus Orléans 88.3", "mark_read_on_open": True, "density": "compact"},
        )
        assert saved.status_code == 200
        again = client.get("/api/settings").json()
        assert again["signature"] == "—\nLaz\nRadio Campus Orléans 88.3"
        assert again["extra_prompt"] == "fédé"
        assert again["mark_read_on_open"] is True
        assert again["density"] == "compact"
        client.post("/api/settings", json={"extra_prompt": "fédé", "signature": "x" * 900})
        assert len(client.get("/api/settings").json()["signature"]) == 800
        assert client.get("/api/queue").json()["density"] == "compact"


def test_scan_returns_job_and_history_view(tmp_path, monkeypatch):
    session = FakeSession()
    session.add(make_mail("5", subject="Relevé", sender="Emma <emma@test>"), seen=False)
    with _client(tmp_path, monkeypatch, session=session) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        scanned = client.post("/api/scan")
        assert scanned.status_code == 202
        job = wait_job(client, scanned.json()["job_id"])
        assert job["status"] == "done"
        assert job["result"]["created"] == 1
        queue = client.get("/api/queue").json()
        assert queue["items"][0]["subject"] == "Relevé"
        assert queue["items"][0]["seen"] is False
        assert db.get_setting(path, "scan_last_uid") == "5"
        moved = _seed(path, imap_uid="21", subject="Traité")
        db.set_status(path, moved, "moved")
        done = client.get("/api/queue?status=done").json()
        assert done["status"] == "done"
        assert [item["subject"] for item in done["items"]] == ["Traité"]
        sync = client.post("/api/scan?wait=1")
        assert sync.status_code == 200
        assert sync.json()["created"] == 0


def test_later_action_and_undo(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        a = _seed(path, imap_uid="31", category="todo", subject="A")
        b = _seed(path, imap_uid="32", category="todo", subject="B")
        res = client.post("/api/actions", json={"ids": [a, b], "kind": "later"})
        assert res.status_code == 200
        body = res.json()
        assert body["action"]["kind"] == "later"
        assert body["action"]["status"] == "done"
        assert body["job_id"] is None
        assert {item["status"] for item in body["items"]} == {"skipped"}
        assert client.get("/api/queue").json()["items"] == []
        history = client.get("/api/actions").json()["actions"]
        assert history[0]["label"] == "Plus tard · 2 mails"
        undo = client.post(f"/api/actions/{body['action']['id']}/undo")
        assert undo.status_code == 200
        assert db.get_item(path, a)["status"] == "proposed"
        assert db.get_item(path, b)["status"] == "proposed"
        assert client.get(f"/api/actions").json()["actions"][1]["status"] == "undone"
        twice = client.post(f"/api/actions/{body['action']['id']}/undo")
        assert twice.status_code == 400
        bad = client.post("/api/actions", json={"ids": [a], "kind": "explode"})
        assert bad.status_code == 400
        empty = client.post("/api/actions", json={"ids": [], "kind": "later"})
        assert empty.status_code == 400


def test_flag_action_hits_imap_then_undo(tmp_path, monkeypatch):
    session = FakeSession()
    session.add(make_mail("41", subject="Drapeau"), seen=False)
    with _client(tmp_path, monkeypatch, session=session) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        item_id = _seed(path, imap_uid="41", subject="Drapeau", seen=False)
        res = client.post("/api/actions", json={"ids": [item_id], "kind": "flag"})
        assert res.status_code == 202
        body = res.json()
        assert body["items"][0]["flagged"] is True
        job = wait_job(client, body["job_id"])
        assert job["status"] == "done", job
        assert session.folders["INBOX"]["41"].flagged is True
        assert db.action_get(path, body["action"]["id"])["status"] == "done"
        seen = client.post("/api/actions", json={"ids": [item_id], "kind": "seen"})
        wait_job(client, seen.json()["job_id"])
        assert session.folders["INBOX"]["41"].seen is True
        assert db.get_item(path, item_id)["seen"] == 1
        undo = client.post(f"/api/actions/{seen.json()['action']['id']}/undo")
        assert undo.status_code == 202
        wait_job(client, undo.json()["job_id"])
        assert session.folders["INBOX"]["41"].seen is False
        assert db.get_item(path, item_id)["seen"] == 0
        events = client.get("/api/events?once=1").text
        assert "event: action" in events
        assert "event: item" in events


def test_archive_moves_to_category_folder_and_restore(tmp_path, monkeypatch):
    session = FakeSession()
    session.add(make_mail("51", subject="Promo", sender="Label <label@test>", message_id="<promo@test>"))
    session.add(make_mail("52", subject="Dossier", sender="Alliage <a@test>", message_id="<dossier@test>"))
    with _client(tmp_path, monkeypatch, session=session) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        news = _seed(path, imap_uid="51", category="newsletters", subject="Promo", sender="Label <label@test>", message_id="<promo@test>")
        todo = _seed(path, imap_uid="52", category="todo", subject="Dossier", sender="Alliage <a@test>", message_id="<dossier@test>")
        res = client.post("/api/actions", json={"ids": [news, todo], "kind": "archive"})
        assert res.status_code == 202
        body = res.json()
        assert {item["status"] for item in body["items"]} == {"moved"}
        assert client.get("/api/queue").json()["items"] == []
        job = wait_job(client, body["job_id"])
        assert job["status"] == "done", job
        stored_news = db.get_item(path, news)
        assert stored_news["folder"] == "Inbox Zero/Newsletters"
        assert stored_news["folder_uid"] == "1"
        assert "51" not in session.folders["INBOX"]
        assert "1" in session.folders["Inbox Zero/Newsletters"]
        assert db.get_item(path, todo)["folder"] == "Inbox Zero/A faire"
        with db.connect(path) as conn:
            hits = conn.execute("SELECT hits, category FROM sender_memory WHERE sender_email = 'label@test'").fetchone()
        assert hits["category"] == "newsletters"
        found = client.get("/api/search", params={"q": "promo"}).json()["hits"]
        assert found and found[0]["folder"] == "Inbox Zero/Newsletters"
        assert found[0]["status"] == "moved"
        history = client.get("/api/queue?status=done").json()
        assert {item["subject"] for item in history["items"]} == {"Promo", "Dossier"}
        undo = client.post(f"/api/actions/{body['action']['id']}/undo")
        assert undo.status_code == 202
        wait_job(client, undo.json()["job_id"])
        restored = db.get_item(path, news)
        assert restored["status"] == "proposed"
        assert restored["folder"] == "INBOX"
        assert restored["imap_uid"] in session.folders["INBOX"]
        assert session.folders["Inbox Zero/Newsletters"] == {}
        assert len(client.get("/api/queue").json()["items"]) == 2


def test_archive_failure_reverts_local_state(tmp_path, monkeypatch):
    session = FakeSession(fail={"move"})
    session.add(make_mail("61", subject="Fragile"))
    with _client(tmp_path, monkeypatch, session=session) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        item_id = _seed(path, imap_uid="61", category="read", subject="Fragile")
        res = client.post("/api/actions", json={"ids": [item_id], "kind": "archive"})
        assert res.status_code == 202
        job = wait_job(client, res.json()["job_id"])
        assert job["status"] == "failed"
        assert db.get_item(path, item_id)["status"] == "proposed"
        action = db.action_get(path, res.json()["action"]["id"])
        assert action["status"] == "failed"
        assert "panne" in action["error"]


def test_archive_refused_without_move_capability(tmp_path, monkeypatch):
    session = FakeSession(caps=())
    with _client(tmp_path, monkeypatch, session=session) as client:
        client.post("/api/login", json={"token": "secret"})
        item_id = _seed(get_settings().db_path, imap_uid="71")
        res = client.post("/api/actions", json={"ids": [item_id], "kind": "archive"})
        assert res.status_code == 503
        later = client.post("/api/actions", json={"ids": [item_id], "kind": "later"})
        assert later.status_code == 200


def test_serialize_item_scrubs_secret_urls():
    row = serialize_item(
        {
            "id": 11,
            "subject": "Reset",
            "sender": "Ada <ada@x>",
            "excerpt": "Lien https://mail.test/reset?token=abc123 et https://lespechus.fr/",
            "category": "todo",
            "reason": "r",
            "summary": "Voir https://app.test/?invite=xyz",
            "error": None,
            "draft": "Voici https://site.test/login?key=secret",
        }
    )
    assert "[lien retiré]" in row["excerpt"]
    assert "[lien retiré]" in row["summary"]
    assert "[lien retiré]" in row["draft"]
    assert "token=" not in row["excerpt"]
    assert "https://lespechus.fr/" in row["excerpt"]
    row = serialize_item(
        {
            "id": 1,
            "subject": "S",
            "sender": "Ada <ada@x>",
            "excerpt": "Première phrase. Deuxième phrase utile. Trop tard.",
            "category": "todo",
            "reason": "r",
            "error": None,
            "draft": "d",
            "confidence": 0.9,
        }
    )
    assert row["sender_name"] == "Ada"
    assert row["sender_email"] == "ada@x"
    assert "confidence" not in row
    assert row["summary"] == "r"
    assert row["seen"] is True
    stored = serialize_item(
        {
            "id": 2,
            "subject": "S",
            "sender": "Ada <ada@x>",
            "excerpt": "Extrait long",
            "category": "read",
            "reason": "r",
            "summary": "Claude a résumé ça.",
            "error": None,
            "draft": "",
            "mailed_at": "2026-09-17T14:00:00+00:00",
            "created_at": "2026-09-18T13:00:00+00:00",
        }
    )
    assert stored["summary"] == "Claude a résumé ça."
    assert stored["created_rel"]


def test_serialize_item_appends_research_links_from_excerpt():
    row = serialize_item(
        {
            "id": 8,
            "subject": "Fwd: Pêchus",
            "sender": "Lou <l@x>",
            "excerpt": "Hello.\n" + ("bla " * 400) + "https://www.lespechus.com/",
            "category": "todo",
            "reason": "r",
            "summary": "Lou te passe Les Pêchus.",
            "error": None,
            "draft": "",
        }
    )
    assert "Les Pêchus" in row["summary"]
    assert "https://www.lespechus.com/" in row["summary"]
    news = serialize_item(
        {
            "id": 9,
            "subject": "Digest",
            "sender": "News <n@x>",
            "excerpt": "Écoute https://pechus.bandcamp.com/album/demo",
            "category": "newsletters",
            "reason": "liste",
            "summary": "Newsletter label.",
            "error": None,
            "draft": "",
        }
    )
    assert "bandcamp" not in news["summary"]


def test_serialize_item_peels_quoted_excerpt():
    row = serialize_item(
        {
            "id": 3,
            "subject": "S",
            "sender": "Viviane <v@x>",
            "excerpt": (
                "C’est possible mardi 29/09 ! Viviane > Le 15 sept. 2026 à 20:58, "
                "Bob <bob@x> a écrit : > Hello To: cc@x"
            ),
            "category": "todo",
            "reason": "r",
            "error": None,
            "draft": "",
            "summary": "Récap Claude",
        }
    )
    assert "29/09" in row["excerpt"]
    assert "a écrit" not in row["excerpt"]
    assert "cc@x" not in row["excerpt"]
    assert row["summary"] == "Récap Claude"


def test_serialize_item_appends_signature_once():
    payload = {
        "id": 4,
        "subject": "S",
        "sender": "Ada <ada@x>",
        "excerpt": "Corps",
        "category": "todo",
        "reason": "r",
        "error": None,
        "draft": "Salut Viviane,",
        "summary": "À répondre",
    }
    row = serialize_item(payload, "—\nLaz")
    assert row["draft"] == "Salut Viviane,\n\n—\nLaz"
    again = serialize_item({**payload, "draft": row["draft"]}, "—\nLaz")
    assert again["draft"] == row["draft"]
    empty = serialize_item(payload, "")
    assert empty["draft"] == "Salut Viviane,"


def test_precomputed_fields_are_stored_and_served(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        item_id = _seed(
            path,
            imap_uid="42",
            category="todo",
            subject="Mardi",
            excerpt="C’est possible mardi https://mail.test/reset?token=abc > Le 15 sept. 2026 à 20:58, Bob a écrit : > Hello",
            summary="Voir mardi",
            draft="Salut Viviane,",
        )
        db.set_setting(path, "signature", "—\nLaz")
        stored = db.get_item(path, item_id)
        assert stored["excerpt_clean"].startswith("C’est possible mardi")
        assert "a écrit" not in stored["excerpt_clean"]
        assert "token=" not in stored["excerpt_clean"]
        assert stored["summary_full"] == "Voir mardi"
        payload = client.get("/api/queue").json()
        assert payload["items"][0]["draft"] == "Salut Viviane,\n\n—\nLaz"
        assert payload["items"][0]["excerpt"] == stored["excerpt_clean"]
        db.set_summary(path, item_id, "Nouveau récap")
        assert db.get_item(path, item_id)["summary_full"] == "Nouveau récap"


def test_brief_job_updates_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        item_id = _seed(path, imap_uid="77", category="todo", subject="Fwd: Alliage", excerpt="idée invitée Mag culturel", draft="Faux brouillon", summary="idée invitée Mag culturel")

        def fake_brief(**kwargs):
            assert kwargs["subject"] == "Fwd: Alliage"
            return {"summary": "Récap test", "draft": "Brouillon test"}, {"input_tokens": 10, "output_tokens": 5}

        with patch("inboxzero.worker.brief_with_anthropic", side_effect=fake_brief):
            payload = client.post(f"/api/items/{item_id}/brief?wait=1").json()
            assert payload["ok"] is True
            assert payload["item"]["summary"] == "Récap test"
            assert payload["item"]["draft"] == "Brouillon test"
            assert payload["item"]["status"] == "proposed"
            queued = client.post(f"/api/items/{item_id}/brief")
            assert queued.status_code == 202
            wait_job(client, queued.json()["job_id"])
        stored = db.get_item(path, item_id)
        assert stored["summary"] == "Récap test"
        assert web.worker.last_scan_stats["sonnet_calls"] >= 1
        events = client.get("/api/events?once=1").text
        assert "event: item" in events
        assert client.post("/api/items/9999/brief").status_code == 404


def test_brief_refused_without_claude(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        item_id = _seed(get_settings().db_path, imap_uid="78")
        assert client.post(f"/api/items/{item_id}/brief").status_code == 503


def test_notion_creates_task_without_moving(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        db.set_setting(path, "notion_token", "ntn_test")
        item_id = _seed(path, imap_uid="88", category="todo", sender="Lou <lou@test>", subject="Fwd: Musées", excerpt="https://museesorleans.fr/", summary="Lou te passe les musées.")

        def fake_add(**kwargs):
            assert kwargs["note"] == "Préparer la recherche"
            assert kwargs["token"] == "ntn_test"
            assert kwargs["item"]["id"] == item_id
            return {"url": "https://www.notion.so/created", "title": "Musées"}

        with patch("inboxzero.notion.add_item_to_notion", side_effect=fake_add):
            payload = client.post(f"/api/items/{item_id}/notion?wait=1", json={"note": "Préparer la recherche"}).json()
        assert payload["ok"] is True
        assert payload["url"] == "https://www.notion.so/created"
        assert payload["item"]["notion_url"] == "https://www.notion.so/created"
        stored = db.get_item(path, item_id)
        assert stored["status"] == "proposed"
        queue = client.get("/api/queue").json()
        assert queue["notion_ready"] is True
        assert queue["items"][0]["notion_url"] == "https://www.notion.so/created"
        with patch(
            "inboxzero.web.notion.probe_notion",
            return_value={"ok": True, "taches": True, "projets": True, "radio_campus": True, "view_only": False, "detail": "Tâches et Projets accessibles."},
        ):
            settings = client.get("/api/settings?probe=1").json()
        assert settings["notion_status"]["ok"] is True
        assert "ntn_test" not in str(settings)
        assert client.get("/api/settings").json()["notion_status"] is None
        client.post("/api/settings", json={"extra_prompt": "x", "signature": ""})
        assert db.get_setting(path, "notion_token") == "ntn_test"
        assert client.post("/api/items/9999/notion", json={"note": ""}).status_code == 404


def test_notion_uploads_thread_attachments(tmp_path, monkeypatch):
    from test_attachments import PDF, FakeAtt, FakeMsg

    session = FakeSession()
    mail = make_mail("90", subject="Dossier", sender="Alliage <a@test>")
    session.add(mail, message=FakeMsg([FakeAtt("dossier.pdf", PDF, "application/pdf")]))
    with _client(tmp_path, monkeypatch, session=session) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        db.set_setting(path, "notion_token", "ntn_test")
        item_id = _seed(
            path,
            imap_uid="90",
            category="todo",
            sender="Alliage <a@test>",
            subject="Dossier",
            excerpt="ci-joint",
            summary="Dossier Alliage.",
            attachments=[{"n": 0, "filename": "dossier.pdf", "kind": "pdf", "size": len(PDF), "content_type": "application/pdf", "status": "listed"}],
        )
        captured: dict = {}

        def fake_upload(token, dest, filename, content_type=""):
            captured["uploaded"] = (filename, dest.read_bytes()[:4])
            assert token == "ntn_test"
            return "up-pdf-1"

        def fake_add(**kwargs):
            captured["uploads"] = kwargs.get("uploads")
            return {"url": "https://www.notion.so/with-file", "title": "Dossier"}

        with patch("inboxzero.notion.upload_file", side_effect=fake_upload), patch("inboxzero.notion.add_item_to_notion", side_effect=fake_add):
            payload = client.post(f"/api/items/{item_id}/notion?wait=1", json={"note": ""}).json()
        assert payload["ok"] is True, payload
        assert captured["uploaded"] == ("dossier.pdf", b"%PDF")
        assert captured["uploads"][0]["file_upload_id"] == "up-pdf-1"
        assert ("message", "90", "INBOX") in session.calls


def test_adopt_indexed_mail_creates_item(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        db.upsert_mail_index(path, {"imap_uid": "7", "uidvalidity": "1", "message_id": "<old@test>", "sender": "Vieux <old@test>", "subject": "Ancien dossier", "excerpt": "Un mail de l’an dernier.", "mailed_at": "2025-03-01T10:00:00+00:00"})
        hit = client.get("/api/search", params={"q": "ancien"}).json()["hits"][0]
        assert hit["item_id"] is None
        detail = client.get(f"/api/mails/{hit['id']}").json()
        assert "an dernier" in detail["body"]
        adopted = client.post(f"/api/mails/{hit['id']}/adopt", json={"queue": False}).json()
        assert adopted["item"]["status"] == "skipped"
        assert adopted["item"]["subject"] == "Ancien dossier"
        queued = client.post(f"/api/mails/{hit['id']}/adopt", json={"queue": True}).json()
        assert queued["item"]["id"] == adopted["item"]["id"]
        assert queued["item"]["status"] == "proposed"
        again = client.get("/api/search", params={"q": "ancien"}).json()["hits"][0]
        assert again["item_id"] == adopted["item"]["id"]
        assert client.get("/api/mails/9999").status_code == 404


def test_status_and_jobs_endpoints(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        status = client.get("/api/status").json()
        assert status["ok"] is True
        assert "jobs" in status["worker"]
        assert status["worker"]["session"]["can_move"] is True
        scanned = client.post("/api/scan").json()
        wait_job(client, scanned["job_id"])
        job = client.get(f"/api/jobs/{scanned['job_id']}").json()["job"]
        assert job["kind"] == "scan"
        assert job["status"] == "done"
        assert client.get("/api/jobs/999999").status_code == 404
        assert client.post("/api/items/1/confirm").status_code in {404, 405}
        assert client.post("/api/items/batch", json={"ids": [1], "action": "confirm"}).status_code in {404, 405}

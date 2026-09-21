"""Parité du module Mails avec Inbox Zero v2, sur le noyau Régie (jobs persistants, journal d'actions, recherche Postgres)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fake_imap import FakeSession, make_mail
from regie.kernel import db


@pytest.fixture
def mail_client(dsn, media_root):
    """Application avec le module Mails branché sur une fausse boîte IMAP ; les jobs s'exécutent via drain()."""
    from regie.app import create_app
    from regie.config import get_settings
    from regie.kernel.core import Kernel
    from regie.modules import load_modules

    def make(session: FakeSession | None = None, imap: bool = True):
        fake = session if session is not None else FakeSession()
        settings = get_settings().model_copy(update={"regie_run_workers": False})
        kernel = Kernel(settings)
        load_modules(kernel, ["regie.modules.mail"])
        service = kernel.modules["mail"]
        service.session = fake if imap else None
        app = create_app(settings, start_workers=False, kernel=kernel)
        kernel.auth.ensure_default_permissions()
        if not kernel.auth.user_by_email("laz@test"):
            kernel.auth.create_user("laz@test", "Laz", "motdepasse-solide", "admin")
        client = TestClient(app, headers={"X-Regie": "1"})
        client.__enter__()
        assert client.post("/api/v1/auth/login", json={"email": "laz@test", "password": "motdepasse-solide"}).status_code == 200
        client.kernel = kernel  # type: ignore[attr-defined]
        client.service = service  # type: ignore[attr-defined]
        client.fake = fake  # type: ignore[attr-defined]
        return client

    made: list[TestClient] = []

    def factory(**kwargs):
        client = make(**kwargs)
        made.append(client)
        return client

    yield factory
    for client in made:
        client.__exit__(None, None, None)


def seed(service, **extra) -> int:
    base = {"imap_uid": "11", "uidvalidity": "1", "category": "read", "sender": "Viviane <viviane@test>", "subject": "Hello", "excerpt": "Corps"}
    base.update(extra)
    item_id = service.store.insert_item(base)
    service._index_item(item_id, base)
    return item_id


def run_job(client, job_id: int) -> dict:
    client.kernel.jobs.drain()
    job = client.kernel.jobs.get(job_id)
    assert job is not None
    return job


def test_queue_json_etag_and_deep_link(mail_client):
    client = mail_client()
    service = client.service
    seed(service, seen=False)
    res = client.get("/api/v1/mail/queue")
    payload = res.json()
    first = payload["items"][0]
    assert first["sender_name"] == "Viviane" and first["subject"] == "Hello"
    assert first["seen"] is False and first["flagged"] is False and first["folder"] == "INBOX"
    assert "confidence" not in first
    assert payload["cat_counts"]["read"] == 1 and payload["unread"] == 1
    assert payload["folders"]["newsletters"] == "Inbox Zero/Newsletters"
    etag = res.headers["etag"]
    assert client.get("/api/v1/mail/queue", headers={"If-None-Match": etag}).status_code == 304
    seed(service, imap_uid="12", subject="Nouveau")
    changed = client.get("/api/v1/mail/queue", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and len(changed.json()["items"]) == 2
    moved = seed(service, imap_uid="21", subject="Archivé")
    service.store.set_status(moved, "moved")
    deep = client.get(f"/api/v1/mail/queue?id={moved}").json()
    assert deep["selected_id"] == moved and any(item["id"] == moved for item in deep["items"])
    assert client.get(f"/api/v1/mail/items/{moved}").json()["item"]["status"] == "moved"
    assert client.get("/api/v1/mail/items/9999").status_code == 404


def test_scan_creates_items_and_history_view(mail_client):
    session = FakeSession()
    session.add(make_mail("5", subject="Relevé", sender="Emma <emma@test>"), seen=False)
    client = mail_client(session=session)
    service = client.service
    scanned = client.post("/api/v1/mail/scan")
    assert scanned.status_code == 202
    job = run_job(client, scanned.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["created"] == 1
    queue = client.get("/api/v1/mail/queue").json()
    assert queue["items"][0]["subject"] == "Relevé" and queue["items"][0]["seen"] is False
    assert service.setting("scan_last_uid") == "5"
    moved = seed(service, imap_uid="21", subject="Traité")
    service.store.set_status(moved, "moved")
    done = client.get("/api/v1/mail/queue?status=done").json()
    assert [item["subject"] for item in done["items"]] == ["Traité"]
    sync = client.post("/api/v1/mail/scan?wait=1")
    assert sync.status_code == 200 and sync.json()["created"] == 0
    hits = client.get("/api/v1/mail/search", params={"q": "relev"}).json()["hits"]
    assert hits and hits[0]["subject"] == "Relevé" and hits[0]["item_id"]
    global_hits = client.get("/api/v1/search", params={"q": "relev"}).json()["hits"]
    assert global_hits[0]["kind"] == "mail_index"


def test_later_action_and_undo_through_kernel_journal(mail_client):
    client = mail_client()
    service = client.service
    a = seed(service, imap_uid="31", category="todo", subject="A")
    b = seed(service, imap_uid="32", category="todo", subject="B")
    res = client.post("/api/v1/mail/actions", json={"ids": [a, b], "kind": "later"})
    assert res.status_code == 200
    body = res.json()
    assert body["action"]["kind"] == "later" and body["action"]["status"] == "done" and body["job_id"] is None
    assert {item["status"] for item in body["items"]} == {"skipped"}
    assert client.get("/api/v1/mail/queue").json()["items"] == []
    history = client.get("/api/v1/mail/actions").json()["actions"]
    assert history[0]["label"] == "Plus tard · 2 mails" and history[0]["reversible"] is True
    kernel_view = client.get("/api/v1/actions?module=mail").json()["actions"]
    assert kernel_view[0]["kind"] == "mail.later"
    undo = client.post(f"/api/v1/mail/actions/{body['action']['id']}/undo")
    assert undo.status_code == 200
    assert service.store.get_item(a)["status"] == "proposed" and service.store.get_item(b)["status"] == "proposed"
    assert client.get("/api/v1/mail/actions").json()["actions"][1]["status"] == "undone"
    assert client.post(f"/api/v1/mail/actions/{body['action']['id']}/undo").status_code == 400
    assert client.post("/api/v1/mail/actions", json={"ids": [a], "kind": "explode"}).status_code == 400
    assert client.post("/api/v1/mail/actions", json={"ids": [], "kind": "later"}).status_code == 404


def test_flag_and_seen_hit_imap_then_undo(mail_client):
    session = FakeSession()
    session.add(make_mail("41", subject="Drapeau"), seen=False)
    client = mail_client(session=session)
    service = client.service
    item_id = seed(service, imap_uid="41", subject="Drapeau", seen=False)
    res = client.post("/api/v1/mail/actions", json={"ids": [item_id], "kind": "flag"})
    assert res.status_code == 202
    body = res.json()
    assert body["items"][0]["flagged"] is True, "optimiste avant IMAP"
    assert run_job(client, body["job_id"])["status"] == "done"
    assert session.folders["INBOX"]["41"].flagged is True
    assert client.kernel.actions.get(body["action"]["id"])["status"] == "done"
    seen = client.post("/api/v1/mail/actions", json={"ids": [item_id], "kind": "seen"})
    run_job(client, seen.json()["job_id"])
    assert session.folders["INBOX"]["41"].seen is True and service.store.get_item(item_id)["seen"] == 1
    undo = client.post(f"/api/v1/mail/actions/{seen.json()['action']['id']}/undo")
    assert undo.status_code == 202
    run_job(client, undo.json()["job_id"])
    assert session.folders["INBOX"]["41"].seen is False and service.store.get_item(item_id)["seen"] == 0
    events = client.get("/api/v1/stream?once=1").text
    assert "event: action" in events and "event: item" in events


def test_archive_moves_to_category_folder_and_restore(mail_client):
    session = FakeSession()
    session.add(make_mail("51", subject="Promo", sender="Label <label@test>", message_id="<promo@test>"))
    session.add(make_mail("52", subject="Dossier", sender="Alliage <a@test>", message_id="<dossier@test>"))
    client = mail_client(session=session)
    service = client.service
    news = seed(service, imap_uid="51", category="newsletters", subject="Promo", sender="Label <label@test>", message_id="<promo@test>")
    todo = seed(service, imap_uid="52", category="todo", subject="Dossier", sender="Alliage <a@test>", message_id="<dossier@test>")
    res = client.post("/api/v1/mail/actions", json={"ids": [news, todo], "kind": "archive"})
    assert res.status_code == 202
    body = res.json()
    assert {item["status"] for item in body["items"]} == {"moved"}
    assert client.get("/api/v1/mail/queue").json()["items"] == []
    assert run_job(client, body["job_id"])["status"] == "done"
    stored = service.store.get_item(news)
    assert stored["folder"] == "Inbox Zero/Newsletters" and stored["folder_uid"] == "1"
    assert "51" not in session.folders["INBOX"] and "1" in session.folders["Inbox Zero/Newsletters"]
    assert service.store.get_item(todo)["folder"] == "Inbox Zero/A faire"
    with db.connect(client.kernel.dsn) as conn:
        memory = db.fetch_one(conn, "SELECT hits, category FROM mail_sender_memory WHERE sender_email = 'label@test'")
    assert memory["category"] == "newsletters"
    found = client.get("/api/v1/mail/search", params={"q": "promo"}).json()["hits"]
    assert found and found[0]["folder"] == "Inbox Zero/Newsletters" and found[0]["status"] == "moved"
    history = client.get("/api/v1/mail/queue?status=done").json()
    assert {item["subject"] for item in history["items"]} == {"Promo", "Dossier"}
    undo = client.post(f"/api/v1/mail/actions/{body['action']['id']}/undo")
    assert undo.status_code == 202
    run_job(client, undo.json()["job_id"])
    restored = service.store.get_item(news)
    assert restored["status"] == "proposed" and restored["folder"] == "INBOX"
    assert restored["imap_uid"] in session.folders["INBOX"]
    assert session.folders["Inbox Zero/Newsletters"] == {}
    assert len(client.get("/api/v1/mail/queue").json()["items"]) == 2


def test_archive_failure_reverts_local_state(mail_client):
    session = FakeSession(fail={"move"})
    session.add(make_mail("61", subject="Fragile"))
    client = mail_client(session=session)
    service = client.service
    item_id = seed(service, imap_uid="61", category="read", subject="Fragile")
    res = client.post("/api/v1/mail/actions", json={"ids": [item_id], "kind": "archive"})
    assert res.status_code == 202
    client.kernel.jobs.drain()
    action = client.kernel.actions.get(res.json()["action"]["id"])
    assert action["status"] == "failed" and "panne" in action["error"]
    assert service.store.get_item(item_id)["status"] == "proposed"


def test_archive_refused_without_move_capability_or_imap(mail_client):
    client = mail_client(session=FakeSession(caps=()))
    item_id = seed(client.service, imap_uid="71")
    assert client.post("/api/v1/mail/actions", json={"ids": [item_id], "kind": "archive"}).status_code == 503
    assert client.post("/api/v1/mail/actions", json={"ids": [item_id], "kind": "later"}).status_code == 200
    offline = mail_client(imap=False)
    other = seed(offline.service, imap_uid="72")
    assert offline.post("/api/v1/mail/actions", json={"ids": [other], "kind": "flag"}).status_code == 503
    assert offline.get("/api/v1/mail/queue").json()["incident"] == "IMAP non configuré"


def test_adopt_indexed_mail_creates_item(mail_client):
    client = mail_client()
    service = client.service
    rows = service.store.upsert_index([{"imap_uid": "7", "uidvalidity": "1", "message_id": "<old@test>", "sender": "Vieux <old@test>", "subject": "Ancien dossier", "body": "Un mail de l’an dernier.", "mailed_at": "2025-03-01T10:00:00+00:00"}])
    service._index_rows_for_search(rows)
    hit = client.get("/api/v1/mail/search", params={"q": "ancien"}).json()["hits"][0]
    assert hit["item_id"] is None
    detail = client.get(f"/api/v1/mail/mails/{hit['id']}").json()
    assert "an dernier" in detail["body"]
    adopted = client.post(f"/api/v1/mail/mails/{hit['id']}/adopt", json={"queue": False}).json()
    assert adopted["item"]["status"] == "skipped" and adopted["item"]["subject"] == "Ancien dossier"
    queued = client.post(f"/api/v1/mail/mails/{hit['id']}/adopt", json={"queue": True}).json()
    assert queued["item"]["id"] == adopted["item"]["id"] and queued["item"]["status"] == "proposed"
    again = client.get("/api/v1/mail/search", params={"q": "ancien"}).json()["hits"][0]
    assert again["item_id"] == adopted["item"]["id"]
    assert client.get("/api/v1/mail/mails/9999").status_code == 404


def test_status_settings_feed_and_permissions(mail_client):
    client = mail_client()
    service = client.service
    seed(service, imap_uid="81", category="todo", subject="Dossier de presse")
    status = client.get("/api/v1/mail/status").json()
    assert status["ok"] is True and status["worker"]["session"]["can_move"] is True
    saved = client.post("/api/v1/mail/settings", json={"extra_prompt": "Toujours en français", "signature": "— Laz", "mark_read_on_open": True, "density": "compact", "notion_token": "ntn_secret"})
    assert saved.status_code == 200
    settings = client.get("/api/v1/mail/settings").json()
    assert settings["signature"] == "— Laz" and settings["density"] == "compact" and settings["notion_token_set"] is True
    assert client.kernel.secrets.get("notion", "token") == "ntn_secret"
    token = settings["feed_token"]
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/v1/mail/feed.md").status_code == 401
    feed = anonymous.get("/api/v1/mail/feed.md", headers={"Authorization": f"Bearer {token}"})
    assert feed.status_code == 200 and "Dossier de presse" in feed.text
    atom = anonymous.get("/api/v1/mail/feed.atom", params={"key": token})
    assert atom.status_code == 200 and atom.text.startswith("<?xml")
    assert client.get("/api/v1/mail/items/1/brief").status_code in {404, 405}
    assert client.post(f"/api/v1/mail/items/{1}/brief").status_code == 503  # Claude non configuré
    registry = client.get("/api/v1/registry").json()
    assert {k["kind"] for k in registry["kinds"]} >= {"mail", "mail_index"}
    assert any(a["kind"] == "mail.archive" and a["async"] for a in registry["actions"])
    client.kernel.auth.create_user("invite@test", "Invité", "mot-de-passe-invite", "invite")
    guest = TestClient(client.app, headers={"X-Regie": "1"})
    guest.post("/api/v1/auth/login", json={"email": "invite@test", "password": "mot-de-passe-invite"})
    assert guest.get("/api/v1/mail/queue").status_code == 403

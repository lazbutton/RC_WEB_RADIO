from xml.etree.ElementTree import fromstring

from inboxzero import db
from inboxzero.config import get_settings
from inboxzero.feed import atom_xml, markdown, select

from test_web import _client


def test_select_keeps_todo_waiting_proposed():
    rows = [
        {"id": 1, "status": "proposed", "category": "todo", "subject": "Alliage"},
        {"id": 2, "status": "proposed", "category": "waiting", "subject": "Musées"},
        {"id": 3, "status": "proposed", "category": "newsletters", "subject": "Digest"},
        {"id": 4, "status": "moved", "category": "todo", "subject": "Déjà parti"},
        {"id": 5, "status": "proposed", "category": "read", "subject": "À lire"},
        {"id": 6, "status": "proposed", "category": "spam", "subject": "Promo"},
    ]
    picked = select(rows)
    assert [row["subject"] for row in picked] == ["Alliage", "Musées"]


def test_atom_is_parseable():
    xml = atom_xml(
        [
            {
                "id": 22,
                "subject": "Alliage",
                "sender_name": "Viviane",
                "sender_email": "viviane@test",
                "category": "todo",
                "summary": "Dossier de presse.",
                "mailed_at": "2026-09-18T10:00:00+00:00",
                "attachments": [{"filename": "dossier.pdf"}],
            }
        ],
        self_href="http://test/api/feed.atom",
    )
    root = fromstring(xml)
    assert root.tag.endswith("feed")
    assert "Alliage" in xml
    assert "dossier.pdf" in xml


def test_markdown_lists_attachments_and_draft():
    body = markdown(
        [
            {
                "id": 1,
                "subject": "Musées",
                "sender_name": "Emma",
                "category": "waiting",
                "summary": "Programme JEP.",
                "draft": "Salut Emma,",
                "attachments": [{"filename": "JEP26.pdf"}],
            }
        ]
    )
    assert body.startswith("# File À faire / En attente")
    assert "Musées" in body
    assert "JEP26.pdf" in body
    assert "Salut Emma," in body


def test_feed_requires_auth(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/feed.md").status_code == 401
        assert client.get("/api/feed.atom").status_code == 401
        assert client.get("/api/feed.md?token=nope").status_code == 401


def test_feed_cookie_and_bearer(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        path = get_settings().db_path
        db.init_db(path)
        db.insert_item(
            path,
            {
                "imap_uid": "22",
                "uidvalidity": "1",
                "category": "todo",
                "sender": "Viviane <viviane@test>",
                "subject": "Alliage",
                "summary": "Dossier Alliage.",
                "attachments": [{"n": 0, "filename": "dossier.pdf", "kind": "pdf"}],
            },
        )
        db.insert_item(
            path,
            {
                "imap_uid": "12",
                "uidvalidity": "1",
                "category": "newsletters",
                "sender": "Agenda <a@test>",
                "subject": "Newsletter",
                "summary": "Digest.",
            },
        )
        db.insert_item(
            path,
            {
                "imap_uid": "19",
                "uidvalidity": "1",
                "category": "waiting",
                "sender": "Emma <emma@test>",
                "subject": "Musées",
                "summary": "Programme musées.",
            },
        )
        moved = db.insert_item(
            path,
            {
                "imap_uid": "99",
                "uidvalidity": "1",
                "category": "todo",
                "subject": "Déjà déplacé",
            },
        )
        db.set_status(path, moved, "moved")

        query = client.get("/api/feed.md?token=secret")
        assert query.status_code == 401

        bearer = client.get("/api/feed.md", headers={"Authorization": "Bearer secret"})
        assert bearer.status_code == 200
        assert "Alliage" in bearer.text
        assert "Musées" in bearer.text
        assert "Newsletter" not in bearer.text
        assert "Déjà déplacé" not in bearer.text
        assert "secret" not in bearer.text
        assert "/" in bearer.text or "2026" in bearer.text or "Quand" in bearer.text

        client.post("/api/login", json={"token": "secret"})
        cookie = client.get("/api/feed.atom")
        assert cookie.status_code == 200
        assert cookie.headers["content-type"].startswith("application/atom+xml")
        root = fromstring(cookie.text)
        titles = [node.text for node in root.findall("{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}title")]
        assert "Alliage" in titles
        assert "Musées" in titles
        assert "Newsletter" not in titles

        settings = client.get("/api/settings").json()
        assert settings["feed_md"] == "/api/feed.md"
        assert settings["feed_atom"] == "/api/feed.atom"
        assert "secret" not in str(settings)
        assert settings.get("inboxzero_token") is None

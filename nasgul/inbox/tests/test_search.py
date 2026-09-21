from inboxzero import db
from inboxzero.search import fts_query, index_body
from inboxzero.web import serialize_hit

from test_web import _client


def test_fts_query_prefixes_and_strips_operators():
    assert fts_query("a") == ""
    assert fts_query("vi") == "vi*"
    assert fts_query("Viviane formation") == "Viviane* AND formation*"
    assert fts_query('AND "orléans"') == '"AND"* AND orléans*'
    assert fts_query("near*") == '"near"*'


def test_index_body_includes_pdf_name(tmp_path):
    blob = index_body(
        {
            "sender": "Viviane <viviane@x>",
            "subject": "Dossier Alliage",
            "excerpt": "Saison culturelle",
            "attachments": '[{"n":0,"filename":"dossier.pdf","kind":"pdf","text":"PORTÉS PAR LES ÉMOTIONS"}]',
        }
    )
    assert "dossier.pdf" in blob
    assert "ÉMOTIONS" in blob


def test_search_finds_sender_subject_and_prefix(tmp_path):
    path = str(tmp_path / "inbox.db")
    db.init_db(path)
    db.insert_item(
        path,
        {
            "imap_uid": "11",
            "uidvalidity": "9",
            "category": "todo",
            "sender": "Viviane Berreur <viviane@radio.test>",
            "subject": "Programmes formations volontaires",
            "excerpt": "Catalogue civique et citoyenne Orléans",
            "summary": "Choisir une formation.",
        },
    )
    db.insert_item(
        path,
        {
            "imap_uid": "12",
            "uidvalidity": "9",
            "category": "read",
            "sender": "Erwann <erwann@radio.test>",
            "subject": "Console studio",
            "excerpt": "Entraînement console jeudi",
        },
    )
    found = db.search_mails(path, "vivi")
    assert [row["subject"] for row in found] == ["Programmes formations volontaires"]
    orleans = db.search_mails(path, "orleans")
    assert orleans and "civique" in (orleans[0]["body"] or "").lower()
    none = db.search_mails(path, "xylophone")
    assert none == []


def test_search_api_returns_hits(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        db.insert_item(
            str(tmp_path / "inboxzero.db"),
            {
                "imap_uid": "21",
                "uidvalidity": "1",
                "category": "todo",
                "sender": "Lou Martinat <volontairelocal@orleans.radiocampus.org>",
                "subject": "Chronique Ecoute ta fac",
                "excerpt": "Lou envoie sa chronique prévue demain.",
                "summary": "Relire la chronique de Lou.",
            },
        )
        empty = client.get("/api/search")
        assert empty.status_code == 200
        assert empty.json()["hits"] == []
        assert empty.json()["indexed"] >= 1
        data = client.get("/api/search", params={"q": "chronique"}).json()
        assert data["indexed"] >= 1
        assert len(data["hits"]) == 1
        hit = data["hits"][0]
        assert "Chronique" in hit["subject"]
        assert hit["item_id"]
        assert hit["item"]["category"] == "todo"
        assert "Lou" in hit["sender_name"]


def test_search_requires_auth(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/search", params={"q": "lou"}).status_code == 401


def test_serialize_hit_without_item():
    row = serialize_hit(
        {
            "id": 4,
            "item_id": None,
            "sender": "Agenda <agenda@x>",
            "subject": "ROMANCE",
            "body": "Sortie d'album",
            "hit": "Sortie d'album",
            "mailed_at": "2026-09-18T11:40:00+00:00",
        }
    )
    assert row["item"] is None
    assert row["subject"] == "ROMANCE"
    assert "album" in row["excerpt"]

from __future__ import annotations

import struct
from unittest.mock import patch

from inboxzero import db
from inboxzero.attachments import inspect, list_from_message, payload_for_n
from inboxzero.config import get_settings
from inboxzero.imaputil import FetchedMail
from inboxzero.jobs import JobRunner
from inboxzero.worker import Worker

from fake_imap import FakeSession
from test_web import _client


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def tiny_wav(samples: int = 800, rate: int = 8000) -> bytes:
    data = b"\x00\x00" * samples
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        rate,
        rate * 2,
        2,
        16,
        b"data",
        len(data),
    )
    return header + data


class FakeAtt:
    def __init__(
        self,
        filename: str,
        payload: bytes,
        content_type: str = "",
        disposition: str = "attachment",
        cid: str | None = None,
    ) -> None:
        self.filename = filename
        self.payload = payload
        self.content_type = content_type
        self.content_disposition = disposition
        self.content_id = cid
        self.size = len(payload)


class FakeMsg:
    def __init__(self, attachments: list[FakeAtt]) -> None:
        self.attachments = attachments


class FakeBox:
    def logout(self) -> None:
        return None


def test_inspect_pdf_jpeg_wav_ok():
    pdf = inspect(PDF, "dossier.pdf", "application/pdf")
    assert pdf["ok"] is True
    assert pdf["kind"] == "pdf"
    jpeg = inspect(JPEG, "photo.jpg", "image/jpeg")
    assert jpeg["ok"] is True
    assert jpeg["kind"] == "image"
    wav = inspect(tiny_wav(), "clip.wav", "audio/wav")
    assert wav["ok"] is True
    assert wav["kind"] == "audio"


def test_inspect_mz_renamed_mp3_refused():
    result = inspect(b"MZ" + b"\x00" * 80, "clip.mp3", "audio/mpeg")
    assert result["ok"] is False
    assert "exécutable" in result["error"]


def test_inspect_zip_polyglot_refused():
    zip_bytes = b"PK\x03\x04" + b"\x00" * 40
    as_mp3 = inspect(zip_bytes, "clip.mp3", "audio/mpeg")
    assert as_mp3["ok"] is False
    as_pdf = inspect(zip_bytes, "dossier.pdf", "application/pdf")
    assert as_pdf["ok"] is False


def test_inspect_double_extension_refused():
    result = inspect(tiny_wav(), "clip.mp3.exe", "audio/mpeg")
    assert result["ok"] is False
    assert "dangereuse" in result["error"]


def test_inspect_pdf_javascript_text_only():
    payload = b"%PDF-1.4\n/JavaScript\ntrailer\n%%EOF\n"
    result = inspect(payload, "x.pdf", "application/pdf")
    assert result["ok"] is True
    assert "script" in result["note"]


def test_list_skips_tiny_inline_images():
    msg = FakeMsg(
        [
            FakeAtt("logo.png", PNG, "image/png", disposition="inline", cid="logo@x"),
            FakeAtt("dossier.pdf", PDF, "application/pdf"),
        ]
    )
    rows = list_from_message(msg)
    assert len(rows) == 1
    assert rows[0]["filename"] == "dossier.pdf"
    assert rows[0]["n"] == 0
    assert payload_for_n(msg, 0).startswith(b"%PDF")


def test_scan_refreshes_attachments_without_reclassify(tmp_path, monkeypatch):
    monkeypatch.setenv("INBOXZERO_DATA", str(tmp_path))
    monkeypatch.setenv("IMAP_USER", "u")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    get_settings.cache_clear()
    settings = get_settings()
    db.init_db(settings.db_path)
    item_id = db.insert_item(
        settings.db_path,
        {
            "imap_uid": "5",
            "uidvalidity": "1",
            "category": "todo",
            "sender": "Alliage <a@test>",
            "subject": "Dossier",
            "excerpt": "ci-joint",
        },
    )
    mail = FetchedMail(
        uid="5",
        uidvalidity="1",
        message_id="",
        sender="Alliage <a@test>",
        subject="Dossier",
        excerpt="ci-joint",
        headers={},
        attachments=[
            {
                "n": 0,
                "filename": "dossier.pdf",
                "kind": "pdf",
                "size": 12,
                "content_type": "application/pdf",
                "status": "listed",
                "error": "",
                "text": "",
                "note": "",
            }
        ],
    )
    seen: list = []

    def classify(self, pending, extra, thread_rows=None):
        seen.extend(pending)
        return []

    monkeypatch.setattr(Worker, "_classify_pending", classify)
    session = FakeSession()
    session.add(mail, seen=False, flagged=True)
    worker = Worker(settings, session=session, runner=JobRunner(settings.db_path, persist=False))
    out = worker.scan(limit=10)
    assert out["ok"] is True
    assert seen == []
    row = db.get_item(settings.db_path, item_id)
    assert row is not None
    assert row["status"] == "proposed"
    flags = worker.runner.run_inline("flags", {})
    assert flags["changed"] == 1
    row = db.get_item(settings.db_path, item_id)
    assert row["seen"] == 0
    assert row["flagged"] == 1
    get_settings.cache_clear()


def test_scan_classifies_new_mail_and_moves_watermark(tmp_path, monkeypatch):
    monkeypatch.setenv("INBOXZERO_DATA", str(tmp_path))
    monkeypatch.setenv("IMAP_USER", "u")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    db.init_db(settings.db_path)
    session = FakeSession()
    session.add(
        FetchedMail(uid="7", uidvalidity="1", message_id="<n7@test>", sender="Label <news@label.test>", subject="Newsletter", excerpt="Unsubscribe here", headers={"list-unsubscribe": "<mailto:x>"}),
        seen=False,
    )
    session.add(FetchedMail(uid="8", uidvalidity="1", message_id="<n8@test>", sender="Emma <emma@test>", subject="Interview", excerpt="Peux-tu venir jeudi ?", headers={}))
    worker = Worker(settings, session=session, runner=JobRunner(settings.db_path, persist=False))
    out = worker.scan()
    assert out["ok"] is True
    assert out["created"] == 2
    assert db.get_setting(settings.db_path, "scan_last_uid") == "8"
    rows = db.list_items(settings.db_path, status="proposed", limit=10)
    assert {row["imap_uid"] for row in rows} == {"7", "8"}
    news = next(row for row in rows if row["imap_uid"] == "7")
    assert news["category"] == "newsletters"
    assert news["seen"] == 0
    again = worker.scan()
    assert again["created"] == 0
    assert any(call[0] == "search" and "UID 9:*" in call[1] for call in session.calls)
    indexed = worker.index_inbox()
    assert indexed["indexed"] >= 2
    session.folders["INBOX"].pop("8")
    worker.index_inbox()
    assert db.get_item(settings.db_path, next(row["id"] for row in rows if row["imap_uid"] == "8"))["status"] == "gone"
    get_settings.cache_clear()


def test_get_other_skips_imap(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAP_USER", "u")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        db.init_db(path)
        item_id = db.insert_item(
            path,
            {
                "imap_uid": "9",
                "uidvalidity": "1",
                "category": "todo",
                "attachments": [
                    {
                        "n": 0,
                        "filename": "x.zip",
                        "kind": "other",
                        "size": 12,
                        "content_type": "application/zip",
                        "status": "listed",
                    }
                ],
            },
        )

        res = client.get(f"/api/items/{item_id}/attachments/0")
        assert res.status_code == 400
        assert "pris en charge" in res.json()["error"]
        assert not any(call[0] == "message" for call in client.fake_session.calls)


def test_get_pdf_peeks_and_caches(tmp_path, monkeypatch):
    session = FakeSession()
    session.add(
        FetchedMail(uid="10", uidvalidity="1", message_id="", sender="A <a@test>", subject="Dossier", excerpt="", headers={}),
        message=FakeMsg([FakeAtt("dossier.pdf", PDF, "application/pdf")]),
    )
    with _client(tmp_path, monkeypatch, session=session) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        db.init_db(path)
        item_id = db.insert_item(
            path,
            {
                "imap_uid": "10",
                "uidvalidity": "1",
                "category": "todo",
                "summary": "Dossier Alliage.",
                "attachments": [
                    {
                        "n": 0,
                        "filename": "dossier.pdf",
                        "kind": "pdf",
                        "size": len(PDF),
                        "content_type": "application/pdf",
                        "status": "listed",
                    }
                ],
            },
        )
        res = client.get(f"/api/items/{item_id}/attachments/0")
        assert ("message", "10", "INBOX") in session.calls
        assert res.status_code == 200
        assert res.content.startswith(b"%PDF")
        stored = db.get_item(path, item_id)
        assert stored is not None
        assert '"status":"ok"' in stored["attachments"]
        assert stored["status"] == "proposed"
        cached = tmp_path / "attachments" / str(item_id)
        assert any(cached.iterdir())
        session.fail.add("message")
        again = client.get(f"/api/items/{item_id}/attachments/0")
        assert again.status_code == 200


def test_get_mz_mp3_refused(tmp_path, monkeypatch):
    session = FakeSession()
    session.add(
        FetchedMail(uid="11", uidvalidity="1", message_id="", sender="A <a@test>", subject="Clip", excerpt="", headers={}),
        message=FakeMsg([FakeAtt("clip.mp3", b"MZ" + b"\x00" * 80, "audio/mpeg")]),
    )
    with _client(tmp_path, monkeypatch, session=session) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        db.init_db(path)
        item_id = db.insert_item(
            path,
            {
                "imap_uid": "11",
                "uidvalidity": "1",
                "category": "todo",
                "attachments": [
                    {
                        "n": 0,
                        "filename": "clip.mp3",
                        "kind": "audio",
                        "size": 90,
                        "content_type": "audio/mpeg",
                        "status": "listed",
                    }
                ],
            },
        )
        res = client.get(f"/api/items/{item_id}/attachments/0")
        assert res.status_code == 400
        assert "exécutable" in res.json()["error"]
        stored = db.get_item(path, item_id)
        assert stored is not None
        assert '"status":"refus"' in stored["attachments"]


def test_queue_exposes_attachment_names(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/api/login", json={"token": "secret"})
        path = get_settings().db_path
        db.init_db(path)
        db.insert_item(
            path,
            {
                "imap_uid": "12",
                "uidvalidity": "1",
                "category": "todo",
                "sender": "Alliage <a@test>",
                "subject": "Dossier",
                "attachments": [
                    {
                        "n": 0,
                        "filename": "dossier.pdf",
                        "kind": "pdf",
                        "size": 12,
                        "content_type": "application/pdf",
                        "status": "listed",
                    }
                ],
            },
        )
        payload = client.get("/api/queue").json()
        atts = payload["items"][0]["attachments"]
        assert atts[0]["filename"] == "dossier.pdf"
        assert atts[0]["kind"] == "pdf"
        assert "path" not in atts[0]

from unittest.mock import patch

import httpx

from regie.modules.mail.notion import (
    DATA_SOURCE_ID,
    PROJECT_ID,
    STATUS_PROGRAMME,
    add_item_to_notion,
    discover_data_source_id,
    extract_date,
    fallback_task,
    normalize_task,
    page_payload,
    parse_task,
    probe_notion,
    strip_subject,
)


def test_strip_subject_drops_fwd():
    assert strip_subject("Fwd: Interview Orange Pression") == "Interview Orange Pression"
    assert strip_subject("Re: Fwd: Hello") == "Hello"


def test_extract_date_french_and_iso():
    assert extract_date("c’est mardi 29/09/2026 à 12h") == "2026-09-29"
    assert extract_date("deadline 2026-10-03") == "2026-10-03"


def test_fallback_task_hydrates_from_mail():
    task = fallback_task(
        subject="Fwd: Interview Pêchus",
        recap="Lou te passe une idée invitée Pêchus pour mardi 29/09/2026.\nhttps://lespechus.fr/",
        excerpt="Voir https://lespechus.fr/",
        note="Préparer les questions, 30 min.",
    )
    assert task["title"] == "Interview Pêchus"
    assert "Pêchus" in task["resultat"]
    assert "Préparer les questions" in task["pourquoi"]
    assert task["echeance"] == "2026-09-29"
    assert task["urgence"] in {"Aujourd’hui", "Cette semaine", "Ce mois-ci", "Sans urgence"}
    assert task["contexte"] == ["Ordinateur"]
    assert task["planifiee"]


def test_parse_and_normalize_keeps_allowed_values():
    raw = """```json
{"title":"Interview Pêchus","resultat":"Préparer l'interview","pourquoi":"Note utile","contexte":["Ordinateur"],"importance":"Importante","urgence":"Cette semaine","priorite":"Haute","duree":"1 h","energie":"Forte","recurrence":"Occasionnel","echeance":"2026-09-29","planifiee":"2026-09-19","cursor":false,"icon":"🎹"}
```"""
    fallback = fallback_task(subject="x", recap="", excerpt="", note="")
    task = normalize_task(parse_task(raw), fallback)
    assert task["title"] == "Interview Pêchus"
    assert task["importance"] == "Importante"
    assert task["duree"] == "1 h"
    assert task["icon"] == "🎹"
    bad = normalize_task({"title": "A", "urgence": "hier", "contexte": ["Bureau"]}, fallback)
    assert bad["urgence"] in {"Aujourd’hui", "Cette semaine", "Ce mois-ci", "Sans urgence"}
    assert "Ordinateur" in bad["contexte"]


def test_page_payload_is_programme_and_radio_campus():
    item = {
        "sender": "Lou <volontairelocal@test>",
        "subject": "Fwd: Interview Pêchus",
        "summary": "Lou te passe Pêchus. https://lespechus.fr/",
        "excerpt": "Dossier https://lespechus.fr/",
        "draft": "",
    }
    task = fallback_task(
        subject=item["subject"],
        recap=item["summary"],
        excerpt=item["excerpt"],
        note="Questions à préparer",
    )
    payload = page_payload(item, task, "Questions à préparer")
    props = payload["properties"]
    assert payload["parent"]["type"] == "data_source_id"
    assert payload["parent"]["data_source_id"] == DATA_SOURCE_ID
    assert props["État"]["status"]["name"] == STATUS_PROGRAMME
    assert props["Projet"]["relation"][0]["id"] == PROJECT_ID
    assert props["Nom de la tâche"]["title"][0]["text"]["content"] == "Interview Pêchus"
    assert "Questions à préparer" in props["Pourquoi important"]["rich_text"][0]["text"]["content"]
    children = payload["children"]
    blob = str(children)
    assert "https://lespechus.fr/" in blob
    assert "Questions à préparer" in blob
    assert "Programmé" not in [c.get("type") for c in children]


def test_page_payload_includes_attachment_names():
    item = {
        "sender": "Alliage <a@test>",
        "subject": "Dossier",
        "summary": "Dossier ci-joint.",
        "excerpt": "Voir PJ",
        "draft": "",
        "attachments": '[{"n":0,"filename":"dossier-alliage.pdf","kind":"pdf","text":"Expo du 12 au 20","status":"ok"}]',
    }
    task = fallback_task(subject=item["subject"], recap=item["summary"], excerpt=item["excerpt"], note="")
    payload = page_payload(item, task, "")
    blob = str(payload["children"])
    assert "Pièces" in blob
    assert "dossier-alliage.pdf" in blob
    assert "Expo du 12 au 20" in blob
    assert "00-inbox" not in blob
    assert "BUTTON" not in blob


def test_page_payload_embeds_uploaded_files():
    item = {
        "sender": "Alliage <a@test>",
        "subject": "Dossier",
        "summary": "Dossier ci-joint.",
        "excerpt": "Voir PJ",
        "draft": "",
        "attachments": '[{"n":0,"filename":"dossier.pdf","kind":"pdf"}]',
    }
    task = fallback_task(subject=item["subject"], recap=item["summary"], excerpt=item["excerpt"], note="")
    payload = page_payload(
        item,
        task,
        "",
        uploads=[{"kind": "pdf", "filename": "dossier.pdf", "file_upload_id": "up-pdf-1"}],
    )
    types = [block.get("type") for block in payload["children"]]
    assert "pdf" in types
    pdf = next(block for block in payload["children"] if block.get("type") == "pdf")
    assert pdf["pdf"]["file_upload"]["id"] == "up-pdf-1"
    assert "dossier.pdf" in str(pdf)


def test_upload_file_create_then_send(tmp_path):
    from regie.modules.mail.notion import upload_file

    path = tmp_path / "press.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, files=None, timeout=None):
        calls.append({"url": str(url), "json": json, "files": files is not None})
        if str(url).rstrip("/").endswith("file_uploads"):
            return httpx.Response(
                200,
                json={
                    "id": "up-1",
                    "upload_url": "https://api.notion.com/v1/file_uploads/up-1/send",
                },
            )
        return httpx.Response(200, json={"id": "up-1", "status": "uploaded"})

    with patch("regie.modules.mail.notion.httpx.post", side_effect=fake_post):
        assert upload_file("ntn_x", path, "press.pdf", "application/pdf") == "up-1"
    assert calls[0]["json"]["filename"] == "press.pdf"
    assert calls[1]["files"] is True
    assert "up-1/send" in calls[1]["url"]


def test_add_item_skips_duplicate_and_keeps_status():
    item = {
        "id": 1,
        "subject": "Hello",
        "summary": "Récap",
        "excerpt": "Corps",
        "notion_url": "https://www.notion.so/abc",
        "status": "proposed",
    }
    out = add_item_to_notion(item=item, note="", token="ntn_test")
    assert out["url"] == "https://www.notion.so/abc"


def test_add_item_posts_to_notion():
    captured: dict[str, object] = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(200, json={"url": "https://www.notion.so/created", "id": "abc"})

    def fake_get(url, headers=None, timeout=None):
        captured["get_url"] = url
        return httpx.Response(200, json={"data_sources": [{"id": DATA_SOURCE_ID, "name": "Tâches"}]})

    item = {
        "sender": "Lou <l@test>",
        "subject": "Fwd: Musées",
        "summary": "Lou te passe les musées. https://museesorleans.fr/",
        "excerpt": "https://museesorleans.fr/",
        "draft": "",
        "status": "proposed",
    }
    with (
        patch("regie.modules.mail.notion.httpx.post", side_effect=fake_post),
        patch("regie.modules.mail.notion.httpx.get", side_effect=fake_get),
    ):
        out = add_item_to_notion(item=item, note="Préparer la recherche", token="ntn_secret")
    assert out["url"] == "https://www.notion.so/created"
    assert captured["url"] == "https://api.notion.com/v1/pages"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer ntn_secret"
    assert headers["Notion-Version"] == "2025-09-03"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["properties"]["État"]["status"]["name"] == "Programmé"
    assert payload["properties"]["Projet"]["relation"][0]["id"] == PROJECT_ID
    assert payload["parent"]["data_source_id"] == DATA_SOURCE_ID
    assert "Préparer la recherche" in str(payload["children"])


def test_discover_view_only_raises_instead_of_fallback():
    def fake_get(url, headers=None, timeout=None):
        if "71b5299c" in url:
            return httpx.Response(404, json={"message": "not found", "code": "object_not_found"})
        if "3d63b6b3-c9bf-806b" in url:
            return httpx.Response(200, json={"object": "database", "is_inline": True, "data_sources": []})
        return httpx.Response(404, json={"message": "not found"})

    with patch("regie.modules.mail.notion.httpx.get", side_effect=fake_get):
        try:
            discover_data_source_id("ntn_x", "71b5299c-c049-4581-9540-31510d690601")
        except RuntimeError as exc:
            assert "vue Tâches" in str(exc)
            assert "Radio Campus" in str(exc)
        else:
            raise AssertionError("discover should fail on the Radio Campus view")


def test_probe_marks_inline_taches_as_view_only():
    def fake_get(url, headers=None, timeout=None):
        if "71b5299c" in url:
            return httpx.Response(404, json={"message": "not found"})
        if "3d63b6b3-c9bf-806b" in url:
            return httpx.Response(200, json={"object": "database", "is_inline": True, "data_sources": []})
        if "962a4aa4" in url:
            return httpx.Response(200, json={"object": "database", "data_sources": [{"id": "b77dec21-c130-4f81-aff8-d80b7c0ae1e6"}]})
        if "/v1/pages/" in url:
            return httpx.Response(200, json={"object": "page"})
        return httpx.Response(404, json={"message": "not found"})

    with patch("regie.modules.mail.notion.httpx.get", side_effect=fake_get):
        status = probe_notion("ntn_x")
    assert status["ok"] is False
    assert status["taches"] is False
    assert status["projets"] is True
    assert status["view_only"] is True
    assert "vue Tâches" in status["detail"]

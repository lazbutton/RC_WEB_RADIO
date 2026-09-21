from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from regie.connectors.outlive import FakeOutlive, map_event

RC_ORG = "9d6d803d-4a7e-4118-beba-1744a1159b99"
SAMPLE = [
    {
        "id": "e1", "title": "Jasmine Not Jafar + guests", "description": "Soirée rap.", "date": "2026-10-10T20:30:00+00:00", "end_time": "23:30",
        "category": "concert", "status": "approved", "archived": False, "address": "1 rue Alexandre Avisse", "image_url": "https://img/e1.jpg",
        "external_url": "https://lastrolabe.org/jasmine", "price_min": 8, "price_max": 12, "agenda_types": ["concert"], "updated_at": "2026-09-01T10:00:00+00:00",
        "locations": {"id": "loc-astro", "name": "L'Astrolabe", "address": "1 rue Alexandre Avisse", "latitude": 47.9, "longitude": 1.9, "capacity": 900, "website_url": "https://lastrolabe.org"},
        "cities": {"name": "Orléans"},
        "event_organizers": [{"organizers": {"id": "org-astro", "name": "L'Astrolabe", "website_url": "https://lastrolabe.org"}, "locations": None}],
        "event_artists": [{"artists": {"id": "art-1", "name": "Jasmine Not Jafar"}}],
        "champ_futur": "ignoré",
    },
    {
        "id": "e2", "title": "Émission en public", "date": "2026-10-12T18:00:00+00:00", "end_date": "2026-10-12T20:00:00+00:00", "category": "radio",
        "status": "approved", "archived": False, "is_pay_what_you_want": True, "agenda_types": ["radio"], "locations": None, "cities": None,
        "event_organizers": [{"organizers": {"id": RC_ORG, "name": "Radio Campus Orléans"}, "locations": None}], "event_artists": [],
    },
    {"id": "e3", "title": "Annulé", "date": "2026-10-13T18:00:00+00:00", "status": "approved", "archived": True, "category": "concert"},
    {"id": "e4", "title": "Sans date", "category": "concert"},
]


def test_map_event_against_frozen_sample():
    row, warnings = map_event(SAMPLE[0], RC_ORG)
    assert row["title"] == "Jasmine Not Jafar + guests"
    assert row["ends_at"].startswith("2026-10-10T23:30")
    assert row["venue_name"] == "L'Astrolabe" and row["venue_id"] == "loc-astro"
    assert row["organizer_id"] == "org-astro" and row["city"] == "Orléans"
    assert row["artists"] == [{"id": "art-1", "name": "Jasmine Not Jafar"}]
    assert row["price"] == "8–12 €" and row["is_radio_campus"] is False
    assert any("champ_futur" in w for w in warnings), "champ inconnu signalé, jamais bloquant"
    radio, _ = map_event(SAMPLE[1], RC_ORG)
    assert radio["is_radio_campus"] is True and radio["price"] == "prix libre"
    assert "lieu inconnu" in radio["warnings"]
    with pytest.raises(ValueError):
        map_event(SAMPLE[3])


@pytest.fixture
def stack(dsn, media_root):
    from regie.app import create_app
    from regie.config import get_settings
    from regie.kernel.core import Kernel
    from regie.modules import load_modules
    from regie.modules import events as events_module

    settings = get_settings().model_copy(update={"regie_run_workers": False})
    kernel = Kernel(settings)
    load_modules(kernel, ["regie.modules.mail", "regie.modules.contacts"])
    fake = FakeOutlive(SAMPLE, RC_ORG)
    kernel.modules["events"] = events_module.setup(kernel, connector=fake)
    app = create_app(settings, start_workers=False, kernel=kernel)
    kernel.auth.ensure_default_permissions()
    kernel.auth.create_user("laz@test", "Laz", "motdepasse-solide", "admin")
    client = TestClient(app, headers={"X-Regie": "1"})
    client.__enter__()
    client.post("/api/v1/auth/login", json={"email": "laz@test", "password": "motdepasse-solide"})
    client.kernel = kernel  # type: ignore[attr-defined]
    client.fake = fake  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


def test_outlive_refresh_caches_links_and_covers(stack):
    client = stack
    kernel = client.kernel
    contacts = kernel.modules["contacts"]
    astro = contacts.create("organization", {"name": "L’Astrolabe", "kind": "salle", "emails": ["prog@lastrolabe.org"]})
    place = contacts.create("place", {"name": "L'Astrolabe", "city": "Orléans"})
    result = kernel.modules["events"].refresh()
    assert result["upserted"] == 2 and result["gone"] == 0 and result["linked"] >= 2  # e3 archivé n'a jamais été mis en cache
    listed = client.get("/api/v1/events", params={"start": "2026-10-01", "end": "2026-10-31"}).json()
    assert [e["id"] for e in listed["events"]] == ["e1", "e2"]
    assert listed["connector"]["status"] == "ok"
    radio_only = client.get("/api/v1/events", params={"radio": 1}).json()["events"]
    assert [e["id"] for e in radio_only] == ["e2"]
    detail = client.get("/api/v1/events/e1").json()
    kinds = {(l["other_kind"], l["role"]) for l in detail["links"]}
    assert ("organization", "organizer") in kinds and ("place", "venue") in kinds
    assert contacts.organizations.get(astro["id"])["outlive_organizer_id"] == "org-astro"
    assert contacts.places.get(place["id"])["outlive_location_id"] == "loc-astro"
    assert client.get("/api/v1/search", params={"q": "jasmine"}).json()["hits"][0]["kind"] == "event"
    covered = client.post("/api/v1/events/e1/cover", json={"kind": "interview", "notes": "Interview avant le concert"})
    assert covered.status_code == 200
    assert covered.json()["coverage"][0]["status"] == "planned"
    again = client.post("/api/v1/events/e1/cover", json={"kind": "interview"})
    assert len(again.json()["coverage"]) == 1, "une couverture par type"
    cov_id = covered.json()["coverage"][0]["id"]
    assert client.patch(f"/api/v1/events/coverage/{cov_id}", json={"status": "done"}).json()["coverage"]["status"] == "done"
    assert client.patch(f"/api/v1/events/coverage/{cov_id}", json={"status": "nope"}).status_code == 400
    undo = client.post(f"/api/v1/actions/{covered.json()['action']['id']}/undo")
    assert undo.status_code == 200
    assert client.get("/api/v1/events/e1").json()["coverage"] == []
    assert client.get("/api/v1/events/coverage").json()["coverage"] == []
    org_view = client.get(f"/api/v1/contacts/organization/{astro['id']}").json()
    assert org_view["events"][0]["id"] == "e1"
    assert client.post("/api/v1/events/refresh").status_code == 202
    assert client.get("/api/v1/events/zzz").status_code == 404


def test_contacts_crud_duplicates_merge_undo_and_export(stack):
    client = stack
    kernel = client.kernel
    created = client.post("/api/v1/contacts/person", json={"data": {"first_name": "Viviane", "last_name": "Berreur", "emails": ["Viviane@Orleans.RadioCampus.org"], "job_title": "Coordinatrice"}})
    assert created.status_code == 201
    viv = created.json()["entity"]
    assert viv["display_name"] == "Viviane Berreur" and viv["emails"] == ["viviane@orleans.radiocampus.org"]
    dup = client.post("/api/v1/contacts/person", json={"data": {"display_name": "viviane berreur", "emails": ["v.berreur@gmail.com"], "notes": "ancienne fiche"}}).json()["entity"]
    org = client.post("/api/v1/contacts/organization", json={"data": {"name": "Radio Campus Orléans", "kind": "asso", "emails": ["contact@orleans.radiocampus.org"]}}).json()["entity"]
    assert org["domains"] == ["orleans.radiocampus.org"]
    client.post("/api/v1/contacts/affiliations", json={"person_id": dup["id"], "organization_id": org["id"], "role": "bénévole"})
    groups = client.get("/api/v1/contacts/duplicates").json()["groups"]
    assert any(g["reason"] == "même nom" and set(g["ids"]) == {viv["id"], dup["id"]} for g in groups)
    listed = client.get("/api/v1/contacts/person", params={"q": "berreur"}).json()
    assert listed["total"] == 2
    merged = client.post("/api/v1/contacts/merge", json={"kind": "person", "keep": viv["id"], "others": [dup["id"]]})
    assert merged.status_code == 200, merged.text
    view = client.get(f"/api/v1/contacts/person/{viv['id']}").json()
    assert set(view["entity"]["emails"]) == {"viviane@orleans.radiocampus.org", "v.berreur@gmail.com"}
    assert view["entity"]["notes"] == "ancienne fiche"
    assert view["affiliations"][0]["organization_name"] == "Radio Campus Orléans"
    assert client.get(f"/api/v1/contacts/person/{dup['id']}").json()["merged_into"] == viv["id"]
    assert client.get("/api/v1/contacts/person").json()["total"] == 1
    assert client.get("/api/v1/search", params={"q": "berreur"}).json()["hits"][0]["id"] == str(viv["id"])
    undo = client.post(f"/api/v1/actions/{merged.json()['action']['id']}/undo")
    assert undo.status_code == 200
    assert client.get("/api/v1/contacts/person").json()["total"] == 2
    restored = client.get(f"/api/v1/contacts/person/{dup['id']}").json()
    assert restored["entity"]["merged_into"] is None and restored["entity"]["emails"] == ["v.berreur@gmail.com"]
    assert any(l["other_kind"] == "organization" for l in restored["links"])
    original = client.get(f"/api/v1/contacts/person/{viv['id']}").json()["entity"]
    assert original["emails"] == ["viviane@orleans.radiocampus.org"] and original["notes"] == ""
    vcf = client.get("/api/v1/contacts/export.vcf")
    assert vcf.status_code == 200 and "BEGIN:VCARD" in vcf.text and "viviane@orleans.radiocampus.org" in vcf.text
    csv_text = client.get("/api/v1/contacts/export.csv").text
    assert "display_name" in csv_text.splitlines()[0]
    imported = client.post("/api/v1/contacts/import/vcards", json={"vcards": "BEGIN:VCARD\nVERSION:3.0\nUID:abc-1\nFN:Lou Martin\nN:Martin;Lou;;;\nEMAIL:lou@orleans.radiocampus.org\nORG:Radio Campus Orléans\nTITLE:Volontaire\nEND:VCARD\n"}).json()
    assert imported["created"] == 1
    again = client.post("/api/v1/contacts/import/vcards", json={"vcards": "BEGIN:VCARD\nVERSION:3.0\nUID:abc-1\nFN:Lou Martin\nEMAIL:lou@orleans.radiocampus.org\nEND:VCARD\n"}).json()
    assert again["updated"] == 1 and again["created"] == 0
    lou = kernel.modules["contacts"].person_by_email("lou@orleans.radiocampus.org")
    assert lou["carddav_uid"] == "abc-1"
    assert client.get("/api/v1/contacts/organization").json()["total"] == 1, "ORG connu réutilisé"
    assert client.delete(f"/api/v1/contacts/person/{lou['id']}").status_code == 200
    assert client.get(f"/api/v1/contacts/person/{lou['id']}").status_code == 404
    assert client.get("/api/v1/contacts/robot").status_code == 404


def test_create_from_mail_and_auto_link(stack):
    client = stack
    kernel = client.kernel
    mail = kernel.modules["mail"]
    contacts = kernel.modules["contacts"]
    org = contacts.create("organization", {"name": "Alliage", "kind": "salle", "emails": ["prog@alliage-olivet.org"]})
    item_id = mail.store.insert_item({"imap_uid": "301", "uidvalidity": "1", "category": "todo", "sender": "Camille Durand <camille@alliage-olivet.org>", "subject": "Dossier de presse saison", "excerpt": "Bonjour", "mailed_at": "2026-09-15T09:00:00+00:00"})
    res = client.post("/api/v1/contacts/from-mail", json={"ids": [item_id]})
    assert res.status_code == 200
    person = res.json()["people"][0]
    assert person["display_name"] == "Camille Durand" and person["source"] == "mail"
    links = client.get(f"/api/v1/links/mail/{item_id}").json()["links"]
    assert {(l["other_kind"], l["role"]) for l in links} == {("person", "sender"), ("organization", "sender_org")}
    view = client.get(f"/api/v1/contacts/person/{person['id']}").json()
    assert view["interactions"][0]["kind"] == "mail" and view["mails"][0]["subject"] == "Dossier de presse saison"
    assert client.get(f"/api/v1/mail/items/{item_id}").json()["links"]
    undo = client.post(f"/api/v1/actions/{res.json()['action']['id']}/undo")
    assert undo.status_code == 200
    assert client.get(f"/api/v1/contacts/person/{person['id']}").status_code == 404
    contacts.create("person", {"display_name": "Camille Durand", "emails": ["camille@alliage-olivet.org"]})
    other = mail.store.insert_item({"imap_uid": "302", "uidvalidity": "1", "category": "read", "sender": "camille@alliage-olivet.org", "subject": "Relance", "excerpt": "Re"})
    kernel.outbox.emit("mail.received", {"id": other, "sender": "camille@alliage-olivet.org"})
    kernel.outbox.deliver_pending()
    kernel.jobs.drain()
    auto = client.get(f"/api/v1/links/mail/{other}").json()["links"]
    assert {l["other_kind"] for l in auto} == {"person", "organization"}
    assert org["id"] in {int(l["other_id"]) for l in auto if l["other_kind"] == "organization"}
    mail.store.insert_item({"imap_uid": "303", "uidvalidity": "1", "category": "read", "sender": "Nouveau <nouveau@structure.org>", "subject": "1", "excerpt": ""})
    mail.store.insert_item({"imap_uid": "304", "uidvalidity": "1", "category": "read", "sender": "Nouveau <nouveau@structure.org>", "subject": "2", "excerpt": ""})
    assert client.post("/api/v1/contacts/import/senders").status_code == 202
    kernel.jobs.drain()
    assert contacts.person_by_email("nouveau@structure.org")["display_name"] == "Nouveau"

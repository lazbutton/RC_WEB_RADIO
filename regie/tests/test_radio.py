from __future__ import annotations

from regie.modules.radio.pdf import simple_pdf


def test_pdf_generator_produces_valid_document():
    data = simple_pdf("Attestation", ["Ligne une avec des accents : éàü", "Une très longue ligne " * 12], footer="pied")
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert b"/Type /Page" in data and b"Attestation" in data
    from pypdf import PdfReader
    import io

    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1 and "Attestation" in (reader.pages[0].extract_text() or "")


def test_guest_pipeline_authorization_and_pdf(app_client, media_root):
    client = app_client
    kernel = client.kernel
    contacts = kernel.modules["contacts"]
    (media_root / "00-inbox").mkdir(exist_ok=True)
    person = contacts.create("person", {"display_name": "Jasmine Not Jafar", "emails": ["jnj@test"]})
    shows = kernel.modules["shows"]
    show = shows.create_show({"name": "Hph"})
    created = client.post("/api/v1/radio/guests", json={"data": {"person_id": person["id"], "topic": "Nouvel album", "show_id": show["id"], "planned_on": "2026-10-09"}})
    assert created.status_code == 201
    guest = created.json()["guest"]
    assert guest["name"] == "Jasmine Not Jafar" and guest["status"] == "idee"
    pipeline = client.get("/api/v1/radio/guests").json()["pipeline"]
    assert [g["id"] for g in pipeline["idee"]] == [guest["id"]]
    assert client.post(f"/api/v1/radio/guests/{guest['id']}/status", json={"status": "venu"}).status_code == 409, "on ne saute pas d'étape"
    assert client.post(f"/api/v1/radio/guests/{guest['id']}/status", json={"status": "contacte"}).status_code == 200
    confirmed = client.post(f"/api/v1/radio/guests/{guest['id']}/status", json={"status": "confirme"})
    assert confirmed.json()["guest"]["status"] == "confirme"
    blocked = client.post(f"/api/v1/radio/guests/{guest['id']}/status", json={"status": "venu"})
    assert blocked.status_code == 409 and "autorisation" in blocked.json()["detail"]["error"]
    pdf = client.post(f"/api/v1/radio/guests/{guest['id']}/authorization.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    rel = pdf.headers["x-regie-path"]
    assert kernel.files.open_path(rel).is_file() and rel.startswith("00-inbox/regie-documents/autorisations/")
    assert client.patch(f"/api/v1/radio/guests/{guest['id']}", json={"data": {"authorization_status": "signed"}}).json()["guest"]["authorization_signed_at"]
    came = client.post(f"/api/v1/radio/guests/{guest['id']}/status", json={"status": "venu"})
    assert came.status_code == 200
    undo = client.post(f"/api/v1/actions/{came.json()['action']['id']}/undo")
    assert undo.status_code == 200 and service_guest(kernel, guest["id"])["status"] == "confirme"
    links = client.get(f"/api/v1/radio/guests/{guest['id']}").json()["links"]
    assert {(l["other_kind"], l["role"]) for l in links} >= {("person", "person"), ("file", "autorisation")}
    assert client.get("/api/v1/search", params={"q": "jasmine"}).json()["hits"]


def service_guest(kernel, guest_id: int):
    return kernel.modules["radio"].guests.get(guest_id)


def test_bookings_volunteers_partnerships_rundown_promotions_and_listening(app_client, media_root):
    client = app_client
    kernel = client.kernel
    radio = kernel.modules["radio"]
    contacts = kernel.modules["contacts"]
    studio = client.post("/api/v1/radio/resources", json={"data": {"name": "Studio A", "kind": "studio"}}).json()["resource"]
    first = client.post("/api/v1/radio/bookings", json={"data": {"resource_id": studio["id"], "title": "Enregistrement HPH", "starts_at": "2026-09-24T18:00:00+00:00", "ends_at": "2026-09-24T20:00:00+00:00"}})
    assert first.status_code == 201
    clash = client.post("/api/v1/radio/bookings", json={"data": {"resource_id": studio["id"], "title": "Autre", "starts_at": "2026-09-24T19:00:00+00:00", "ends_at": "2026-09-24T21:00:00+00:00"}})
    assert clash.status_code == 409 and "Laz" in clash.json()["detail"]["error"]
    after = client.post("/api/v1/radio/bookings", json={"data": {"resource_id": studio["id"], "title": "Suivant", "starts_at": "2026-09-24T20:00:00+00:00", "ends_at": "2026-09-24T21:00:00+00:00"}})
    assert after.status_code == 201
    listed = client.get("/api/v1/radio/bookings", params={"start": "2026-09-24T00:00:00+00:00", "end": "2026-09-25T00:00:00+00:00"}).json()["bookings"]
    assert [b["title"] for b in listed] == ["Enregistrement HPH", "Suivant"] and listed[0]["resource_name"] == "Studio A"
    assert client.delete(f"/api/v1/radio/bookings/{after.json()['booking']['id']}").status_code == 200
    lou = contacts.create("person", {"display_name": "Lou Martin", "emails": ["lou@test"]})
    volunteer = client.post("/api/v1/radio/volunteers", json={"data": {"person_id": lou["id"], "kind": "service_civique", "mission": "Programmation musicale", "start_date": "2026-09-01", "end_date": "2027-04-30", "hours_per_week": 24}})
    assert volunteer.status_code == 201
    listed_v = client.get("/api/v1/radio/volunteers").json()["volunteers"]
    assert listed_v[0]["person"]["display_name"] == "Lou Martin"
    pdf = client.post(f"/api/v1/radio/volunteers/{volunteer.json()['volunteer']['id']}/attestation.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and "attestations/" in pdf.headers["x-regie-path"]
    astro = contacts.create("organization", {"name": "L'Astrolabe", "kind": "salle"})
    partnership = client.post("/api/v1/radio/partnerships", json={"data": {"organization_id": astro["id"], "title": "Échange de visibilité saison 26-27", "kind": "echange", "terms": "Logo sur les affiches, spots antenne."}})
    assert partnership.status_code == 201
    assert client.get("/api/v1/radio/partnerships").json()["partnerships"][0]["organization"]["name"] == "L'Astrolabe"
    assert client.get(f"/api/v1/contacts/organization/{astro['id']}").json()["links"][0]["other_kind"] == "partnership"
    rundown = client.post("/api/v1/radio/rundowns", json={"data": {"title": "HPH 11/09", "aired_on": "2026-09-11", "items": [{"kind": "jingle", "title": "Ouverture", "duration_s": 30}, {"kind": "parole", "title": "Interview Jasmine", "duration_s": 900}, {"kind": "musique", "title": "Titre 1", "duration_s": 240}]}})
    assert rundown.status_code == 201
    saved = rundown.json()["rundown"]
    assert saved["total_s"] == 1170 and saved["items"][1]["at_s"] == 30.0 and saved["items"][2]["at_s"] == 930.0
    updated = client.put(f"/api/v1/radio/rundowns/{saved['id']}", json={"data": {"items": saved["items"][:2], "status": "pret"}}).json()["rundown"]
    assert updated["total_s"] == 930 and updated["status"] == "pret"
    shows = kernel.modules["shows"]
    show = shows.create_show({"name": "Hph"})
    episode = shows.episodes.create({"show_id": show["id"], "title": "Hph", "master_path": "40-emissions/Hph/Entière/x.mp3", "duration_s": 100})
    podcast = shows.create_podcast({"episode_id": episode["id"], "title": "Jasmine en interview", "rights_status": "ok"})
    promos = client.get("/api/v1/radio/promotions", params={"entity_kind": "podcast", "entity_id": str(podcast["id"])}).json()["promotions"]
    assert len(promos) == 6 and "Jasmine en interview" in promos[0]["text_proposal"]
    insta = next(p for p in promos if p["channel"] == "instagram")
    ticked = client.post(f"/api/v1/radio/promotions/{insta['id']}", json={"done": True, "text": "Texte final posté"}).json()["promotion"]
    assert ticked["done"] and ticked["done_by"] == client.kernel.auth.users()[0]["id"] and ticked["text_proposal"] == "Texte final posté"
    assert client.get("/api/v1/radio/promotions", params={"entity_kind": "nope", "entity_id": "1"}).status_code == 400
    assert radio.record_icecast({"icestats": {"source": [{"listenurl": "http://icecast/live.mp3", "listeners": 12, "listener_peak": 40, "title": "HPH"}, {"listenurl": "http://icecast/low", "listeners": 3, "listener_peak": 9}]}}) == 2
    listening = client.get("/api/v1/radio/listening").json()
    assert {row["mount"]: row["listeners"] for row in listening["latest"]} == {"live.mp3": 12, "low": 3} and listening["configured"] is False

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from regie.connectors.google import FakeGoogle
from regie.kernel import db


@pytest.fixture
def stack(dsn, media_root):
    from regie.app import create_app
    from regie.config import get_settings
    from regie.kernel.core import Kernel
    from regie.modules import load_modules
    from regie.modules import calendar as calendar_module

    settings = get_settings().model_copy(update={"regie_run_workers": False})
    kernel = Kernel(settings)
    load_modules(kernel, ["regie.modules.mail", "regie.modules.contacts", "regie.modules.planning"])
    fake = FakeGoogle()
    kernel.modules["calendar"] = calendar_module.setup(kernel, connector=fake)
    app = create_app(settings, start_workers=False, kernel=kernel)
    kernel.auth.ensure_default_permissions()
    laz = kernel.auth.create_user("laz@test", "Laz", "motdepasse-solide", "admin")
    lou = kernel.auth.create_user("lou@test", "Lou", "motdepasse-de-lou", "membre")
    client = TestClient(app, headers={"X-Regie": "1"})
    client.__enter__()
    client.post("/api/v1/auth/login", json={"email": "laz@test", "password": "motdepasse-solide"})
    client.kernel = kernel  # type: ignore[attr-defined]
    client.fake = fake  # type: ignore[attr-defined]
    client.laz = laz  # type: ignore[attr-defined]
    client.lou = lou  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


def test_tasks_comments_checklist_and_notifications(stack):
    client = stack
    lou_id = client.lou["id"]
    created = client.post("/api/v1/planning/tasks", json={"title": "Monter l'interview", "assignees": [lou_id], "due_at": "2026-09-22T10:00:00+00:00", "checklist": [{"text": "Écouter", "done": False}, {"text": "Couper", "done": False}], "tags": ["podcast"]})
    assert created.status_code == 201
    task = created.json()["task"]
    assert task["checklist_total"] == 2 and task["status"] == "todo"
    assert client.kernel.notifications.unread_count(lou_id) == 1, "Lou est prévenue de l'assignation"
    checked = client.post(f"/api/v1/planning/tasks/{task['id']}/check", json={"index": 0, "done": True}).json()["task"]
    assert checked["checklist_done"] == 1
    moved = client.patch(f"/api/v1/planning/tasks/{task['id']}", json={"status": "doing"}).json()["task"]
    assert moved["status"] == "doing"
    second = client.post("/api/v1/planning/tasks", json={"title": "Autre", "status": "doing"}).json()["task"]
    assert client.post("/api/v1/planning/tasks/reorder", json={"status": "doing", "ids": [second["id"], task["id"]]}).status_code == 200
    doing = client.get("/api/v1/planning/tasks", params={"status": "doing"}).json()["tasks"]
    assert [t["id"] for t in doing] == [second["id"], task["id"]]
    comment = client.post("/api/v1/planning/comments", json={"entity_kind": "task", "entity_id": str(task["id"]), "body": "Je m'en occupe demain"})
    assert comment.status_code == 201
    assert client.kernel.notifications.unread_count(lou_id) == 2
    detail = client.get(f"/api/v1/planning/tasks/{task['id']}").json()
    assert detail["comments"][0]["author_name"] == "Laz"
    assert client.post("/api/v1/planning/comments", json={"entity_kind": "ghost", "entity_id": "1", "body": "x"}).status_code == 400
    done = client.post(f"/api/v1/planning/tasks/{task['id']}/done").json()["action"]
    assert client.get(f"/api/v1/planning/tasks/{task['id']}").json()["task"]["status"] == "done"
    client.post(f"/api/v1/actions/{done['id']}/undo")
    assert client.get(f"/api/v1/planning/tasks/{task['id']}").json()["task"]["status"] == "doing"
    assert client.get("/api/v1/search", params={"q": "interview"}).json()["hits"][0]["kind"] == "task"
    mail = client.kernel.modules["mail"]
    item_id = mail.store.insert_item({"imap_uid": "501", "uidvalidity": "1", "category": "todo", "sender": "X <x@test>", "subject": "Envoyer le conducteur", "excerpt": "Merci", "summary": "Il faut envoyer le conducteur avant vendredi."})
    from_mail = client.post("/api/v1/planning/tasks/from-mail", json={"ids": [item_id], "due_at": "2026-09-25T12:00:00+00:00"})
    assert from_mail.status_code == 200
    new_task = from_mail.json()["tasks"][0]
    assert new_task["title"] == "Envoyer le conducteur" and "conducteur" in new_task["description"]
    linked = client.get("/api/v1/planning/tasks", params={"entity_kind": "mail", "entity_id": str(item_id)}).json()["tasks"]
    assert [t["id"] for t in linked] == [new_task["id"]]
    client.post(f"/api/v1/actions/{from_mail.json()['action']['id']}/undo")
    assert client.get(f"/api/v1/planning/tasks/{new_task['id']}").status_code == 404
    lou = TestClient(client.app, headers={"X-Regie": "1"})
    lou.post("/api/v1/auth/login", json={"email": "lou@test", "password": "motdepasse-de-lou"})
    today = lou.get("/api/v1/planning/today").json()
    assert [t["id"] for t in today["tasks"]["mine"]] == [task["id"]]
    assert today["notifications"] and today["mail"]["proposed"] == 1
    assert lou.put("/api/v1/planning/availability", json={"slots": [{"weekday": 1, "start_time": "09:00", "end_time": "12:30"}]}).json()["slots"][0]["end_time"] == "12:30"
    absence = lou.post("/api/v1/planning/absences", json={"start_date": "2026-12-20", "end_date": "2026-12-31", "reason": "vacances"}).json()["absence"]
    week = client.get("/api/v1/planning/week", params={"start": "2026-12-21T00:00:00+00:00"}).json()
    assert week["absences"][0]["name"] == "Lou"
    assert lou.delete(f"/api/v1/planning/absences/{absence['id']}").json()["ok"] is True


def test_google_two_way_sync_conflicts_and_bulk_guard(stack):
    client = stack
    kernel = client.kernel
    fake: FakeGoogle = client.fake
    calendar = kernel.modules["calendar"]
    url = client.get("/api/v1/calendar/google/authorize", params={"label": "Laz"}).json()["url"]
    state = url.split("state=")[1]
    assert client.get("/api/v1/calendar/google/callback", params={"state": state, "code": "laz"}, follow_redirects=False).status_code == 303
    accounts = client.get("/api/v1/calendar/accounts").json()["accounts"]
    assert len(accounts) == 1 and accounts[0]["has_token"] is True and accounts[0]["email"] == "laz@gmail.com"
    account_id = accounts[0]["id"]
    assert kernel.secrets.get("google", f"refresh:{account_id}") == "refresh-laz"
    # 1. Lou crée un rendez-vous dans Google → il arrive dans Régie.
    fake.remote_add("primary", "Réunion volontaires", "2026-09-23T10:00:00+00:00", "2026-09-23T11:00:00+00:00", external_id="g-reunion")
    kernel.jobs.drain()
    events = client.get("/api/v1/calendar/events", params={"start": "2026-09-20T00:00:00+00:00", "end": "2026-09-30T00:00:00+00:00"}).json()["events"]
    assert [e["title"] for e in events] == ["Réunion volontaires"]
    reunion = events[0]
    # 2. Régie crée un rendez-vous → il part dans Google en une passe.
    created = client.post("/api/v1/calendar/events", json={"account_id": account_id, "title": "Enregistrement HPH", "starts_at": "2026-09-24T18:00:00+00:00", "ends_at": "2026-09-24T20:00:00+00:00"})
    assert created.status_code == 201
    kernel.jobs.drain()
    pushed = client.get(f"/api/v1/calendar/events/{created.json()['event']['id']}").json()["event"]
    assert pushed["external_id"] and pushed["external_id"] in fake.calendars["primary"]
    assert fake.calendars["primary"][pushed["external_id"]]["summary"] == "Enregistrement HPH"
    assert kernel.connectors.refs.get("google", "calendar_event", pushed["id"])["external_id"] == pushed["external_id"]
    # 3. Modification côté Régie, puis modification plus récente côté Google : la dernière gagne, l'autre reste en historique.
    client.patch(f"/api/v1/calendar/events/{reunion['id']}", json={"title": "Réunion volontaires (salle 2)"})
    fake.remote_add("primary", "Réunion volontaires (visio)", "2026-09-23T10:00:00+00:00", "2026-09-23T11:00:00+00:00", external_id="g-reunion", updated="2030-01-01T00:00:00Z")
    kernel.jobs.submit("calendar.sync", {"account_id": account_id}, 1)
    kernel.jobs.drain()
    detail = client.get(f"/api/v1/calendar/events/{reunion['id']}").json()
    assert detail["event"]["title"] == "Réunion volontaires (visio)"
    origins = [h["origin"] for h in detail["history"]]
    assert "regie" in origins and "regie-lost" in origins, "la version perdue est conservée"
    # 4. Suppression dans Google → disparaît de Régie.
    fake.remote_delete("primary", "g-reunion")
    kernel.jobs.submit("calendar.sync", {"account_id": account_id}, 1)
    kernel.jobs.drain()
    titles = [e["title"] for e in client.get("/api/v1/calendar/events", params={"start": "2026-09-20T00:00:00+00:00", "end": "2026-09-30T00:00:00+00:00"}).json()["events"]]
    assert titles == ["Enregistrement HPH"]
    # 5. Jeton de synchronisation expiré → relecture complète sans doublon.
    fake.expire_next_token = True
    kernel.jobs.submit("calendar.sync", {"account_id": account_id}, 1)
    kernel.jobs.drain()
    with db.connect(kernel.dsn) as conn:
        assert db.scalar(conn, "SELECT COUNT(*) FROM calendar_events WHERE account_id = %s AND deleted_at IS NULL", (account_id,)) == 1
    # 6. Garde-fou : 25 modifications en masse sont retenues jusqu'à confirmation.
    for index in range(25):
        calendar.create(account_id, {"title": f"Masse {index}", "starts_at": f"2026-10-{(index % 28) + 1:02d}T09:00:00+00:00"}, actor=client.laz["id"])
    kernel.jobs.drain()
    account = calendar.account(account_id)
    assert account["status"] == "needs_confirmation" and account["pending_bulk"] == 25
    assert sum(1 for k2 in fake.calendars["primary"] if fake.calendars["primary"][k2]["summary"].startswith("Masse")) == 0
    assert kernel.notifications.unread_count(client.laz["id"]) >= 1
    assert client.post(f"/api/v1/calendar/accounts/{account_id}/confirm-bulk").status_code == 202
    kernel.jobs.drain()
    assert sum(1 for k2 in fake.calendars["primary"] if fake.calendars["primary"][k2]["summary"].startswith("Masse")) == 25
    assert calendar.account(account_id)["pending_bulk"] == 0
    # 7. Suppression depuis Régie (action annulable) → supprimée chez Google, puis restaurée.
    deleted = client.delete(f"/api/v1/calendar/events/{pushed['id']}").json()["action"]
    kernel.jobs.drain()
    assert fake.calendars["primary"][pushed["external_id"]]["status"] == "cancelled"
    client.post(f"/api/v1/actions/{deleted['id']}/undo")
    kernel.jobs.drain()
    assert fake.calendars["primary"][pushed["external_id"]]["status"] == "confirmed"
    assert client.get(f"/api/v1/calendar/accounts/{account_id}/calendars").json()["calendars"][0]["id"] == "primary"


def test_ics_fallback_is_read_only(stack, monkeypatch):
    client = stack
    kernel = client.kernel
    calendar = kernel.modules["calendar"]
    ics = b"""BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:ics-1\r\nSUMMARY:Plateau Volontaire Musique\r\nDTSTART:20260925T170000Z\r\nDTEND:20260925T180000Z\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:ics-2\r\nSUMMARY:Journee entiere\r\nDTSTART;VALUE=DATE:20260926\r\nDTEND;VALUE=DATE:20260927\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""
    monkeypatch.setattr(calendar, "_fetch_ics", lambda url: ics)
    added = client.post("/api/v1/calendar/accounts/ics", json={"url": "https://calendar.google.com/x/basic.ics", "label": "Volontaire musique", "shared": True})
    assert added.status_code == 201
    kernel.jobs.drain()
    events = client.get("/api/v1/calendar/events", params={"start": "2026-09-24T00:00:00+00:00", "end": "2026-09-28T00:00:00+00:00"}).json()["events"]
    assert {e["title"] for e in events} == {"Plateau Volontaire Musique", "Journee entiere"}
    assert any(e["all_day"] for e in events)
    refused = client.post("/api/v1/calendar/events", json={"account_id": added.json()["account"]["id"], "title": "x", "starts_at": "2026-09-25T10:00:00+00:00"})
    assert refused.status_code == 400
    week = client.get("/api/v1/planning/week", params={"start": "2026-09-21T00:00:00+00:00"}).json()
    assert len(week["appointments"]) == 2 and week["accounts"][0]["provider"] == "ics"

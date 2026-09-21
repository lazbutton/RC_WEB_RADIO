from __future__ import annotations

from fastapi.testclient import TestClient


def test_auth_cookie_csrf_and_me(app_client: TestClient):
    me = app_client.get("/api/v1/me").json()
    assert me["user"]["email"] == "laz@test" and me["user"]["role"] == "admin"
    assert me["permissions"]["mail"] == "admin"
    anonymous = TestClient(app_client.app)
    assert anonymous.get("/api/v1/me").status_code == 401
    assert anonymous.get("/health").json()["ok"] is True
    no_header = TestClient(app_client.app, cookies=app_client.cookies)
    assert no_header.post("/api/v1/actions", json={"kind": "x", "ids": ["1"]}).status_code == 403
    bad_origin = app_client.post("/api/v1/links", json={"src_kind": "a", "src_id": "1", "dst_kind": "b", "dst_id": "2"}, headers={"Origin": "https://evil.test"})
    assert bad_origin.status_code == 403
    res = app_client.get("/api/v1/registry")
    assert res.status_code == 200
    assert res.headers["x-request-id"]
    assert app_client.post("/api/v1/auth/logout").status_code == 200
    assert app_client.get("/api/v1/me").status_code == 401


def test_users_and_permissions_admin_only(app_client: TestClient):
    created = app_client.post("/api/v1/users", json={"email": "lou@test", "name": "Lou", "password": "mot-de-passe-lou", "role": "membre"})
    assert created.status_code == 201
    dup = app_client.post("/api/v1/users", json={"email": "lou@test", "name": "Lou", "password": "mot-de-passe-lou"})
    assert dup.status_code == 409
    users = app_client.get("/api/v1/users").json()["users"]
    assert {u["email"] for u in users} == {"laz@test", "lou@test"}
    lou = TestClient(app_client.app, headers={"X-Regie": "1"})
    assert lou.post("/api/v1/auth/login", json={"email": "lou@test", "password": "mot-de-passe-lou"}).status_code == 200
    assert lou.get("/api/v1/users").status_code == 403
    assert lou.get("/api/v1/settings").json()["ok"] is True
    assert lou.patch("/api/v1/settings", json={"values": {"x": "1"}}).status_code == 403
    assert app_client.put("/api/v1/permissions", json={"role": "membre", "module": "settings", "level": "write"}).status_code == 200
    assert lou.patch("/api/v1/settings", json={"values": {"density": "compact"}}).status_code == 200
    assert app_client.get("/api/v1/settings").json()["settings"]["density"] == "compact"
    assert lou.patch("/api/v1/me", json={"name": "Lou M."}).json()["user"]["name"] == "Lou M."
    bad = lou.patch("/api/v1/me", json={"password": "nouveau-mot-de-passe", "current_password": "faux"})
    assert bad.status_code == 400
    app_client.patch(f"/api/v1/users/{created.json()['user']['id']}", json={"disabled": True})
    assert lou.get("/api/v1/me").status_code == 401


def test_actions_jobs_links_search_events_roundtrip(app_client: TestClient):
    kernel = app_client.kernel  # type: ignore[attr-defined]
    from regie.kernel.actions import ActionSpec
    from regie.kernel.registry import EntityKind

    store = {"1": {"id": "1", "title": "Alpha", "flag": False}}
    kernel.registry.register(EntityKind(kind="thing", module="mail", label="Chose", fetch=lambda ids: [store[i] for i in ids if i in store], summarize=lambda r: {"title": r["title"], "subtitle": ""}))
    kernel.actions.register(ActionSpec(kind="thing.flag", module="mail", entity_kind="thing", label="Drapeau", apply=lambda ctx: [store[i].__setitem__("flag", True) for i in ctx.ids] and {}, revert=lambda ctx: [store[i].__setitem__("flag", False) for i in ctx.ids], snapshot=lambda ids: {i: store[i]["flag"] for i in ids}))
    kernel.search.index("thing", "1", "Alpha", "", "corps alpha")
    done = app_client.post("/api/v1/actions", json={"kind": "thing.flag", "ids": ["1"]})
    assert done.status_code == 200 and store["1"]["flag"] is True
    action_id = done.json()["action"]["id"]
    assert app_client.get("/api/v1/actions?entity_kind=thing&entity_id=1").json()["actions"][0]["id"] == action_id
    assert app_client.post(f"/api/v1/actions/{action_id}/undo").status_code == 200 and store["1"]["flag"] is False
    assert app_client.post("/api/v1/actions", json={"kind": "nope", "ids": ["1"]}).status_code == 400
    link = app_client.post("/api/v1/links", json={"src_kind": "thing", "src_id": "1", "dst_kind": "thing", "dst_id": "1", "role": "self"})
    assert link.status_code == 201
    links = app_client.get("/api/v1/links/thing/1").json()["links"]
    assert links and links[0]["other"]["title"] == "Alpha"
    assert app_client.post("/api/v1/links", json={"src_kind": "ghost", "src_id": "1", "dst_kind": "thing", "dst_id": "1"}).status_code == 400
    search = app_client.get("/api/v1/search", params={"q": "alph"}).json()
    assert search["hits"][0]["entity"]["title"] == "Alpha"
    kernel.jobs.register("noop", lambda ctx: {"ok": True}, "maintenance")
    job = kernel.jobs.submit("noop", {})
    assert app_client.get(f"/api/v1/jobs/{job['id']}").json()["job"]["status"] == "queued"
    assert app_client.get("/api/v1/jobs").json()["jobs"][0]["kind"] == "noop"
    kernel.outbox.emit("thing.changed", {"id": "1"})
    kernel.outbox.deliver_pending()
    events = app_client.get("/api/v1/stream?once=1").text
    assert "event: action" in events and "event: thing.changed" in events
    status = app_client.get("/api/v1/status").json()
    assert "jobs" in status and "modules" in status and "connectors" in status
    metrics = app_client.get("/api/v1/metrics").text
    assert "regie_http_requests" in metrics


def test_files_endpoints_respect_whitelist(app_client: TestClient, media_root):
    (media_root / "40-emissions" / "Etf").mkdir()
    (media_root / "40-emissions" / "Etf" / "2026-09-18_etf_entiere.mp3").write_bytes(b"ID3" + b"\0" * 10)
    root = app_client.get("/api/v1/files").json()
    assert {d["name"] for d in root["dirs"]} >= {"00-inbox", "40-emissions"}
    listing = app_client.get("/api/v1/files", params={"path": "40-emissions/Etf"}).json()
    assert listing["files"][0]["kind"] == "audio" and listing["writable"] is True
    raw = app_client.get("/api/v1/files/raw", params={"path": "40-emissions/Etf/2026-09-18_etf_entiere.mp3"})
    assert raw.status_code == 200 and raw.content.startswith(b"ID3")
    assert app_client.get("/api/v1/files/raw", params={"path": "../secret"}).status_code == 403
    assert app_client.get("/api/v1/files", params={"path": "nope"}).status_code == 404
    scan = app_client.post("/api/v1/files/scan", json={"path": "40-emissions"})
    assert scan.status_code == 202
    app_client.kernel.jobs.drain()  # type: ignore[attr-defined]
    stat = app_client.get("/api/v1/files/stat", params={"path": "40-emissions/Etf/2026-09-18_etf_entiere.mp3"}).json()["file"]
    assert stat["indexed"]["kind"] == "audio"


def test_openapi_lists_all_routes(app_client: TestClient):
    spec = app_client.get("/openapi.json").json()
    paths = set(spec["paths"])
    assert {"/api/v1/auth/login", "/api/v1/actions", "/api/v1/stream", "/api/v1/search", "/api/v1/files/raw"} <= paths

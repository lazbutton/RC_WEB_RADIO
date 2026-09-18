from io import BytesIO
from pathlib import Path

from sqlalchemy.orm import Session as ormSession
from werkzeug.datastructures import FileStorage

from radiotomate.beets import BeetsIntegration
from radiotomate.interface_app import app_factory as interface_factory
from radiotomate.models import User
from radiotomate.scheduler_api import Scheduler
from tests.scheduler.test_studio_json import _interface_client, _login

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def _mp3_upload(mp3: Path) -> FileStorage:
    return FileStorage(
        stream=BytesIO(mp3.read_bytes()),
        filename=mp3.name,
        content_type="audio/mpeg",
    )


async def _admin_client(  # noqa: PLR0913
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    username: str,
    permissions: dict | None = None,
):
    user = User(username=username)
    user.update_password(users_password)
    user.update_permissions_map(
        {"admin": True} if permissions is None else permissions
    )
    dbsession.add(user)
    await dbsession.commit()
    client = _interface_client(app_configration, beets_integration)
    await _login(client, username, users_password)
    return client


async def test_login_json(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    user = User(username="json-login")
    user.update_password(users_password)
    dbsession.add(user)
    await dbsession.commit()
    client = _interface_client(app_configration, beets_integration)
    bad = await client.post(
        "/login.json",
        json={"username": "json-login", "password": "nope"},
    )
    assert bad.status_code == 401
    ok = await client.post(
        "/login.json",
        json={"username": "json-login", "password": users_password},
    )
    assert ok.status_code == 200
    payload = await ok.get_json()
    assert payload["ok"] is True
    clocks = await client.get("/autodj/clocks.json")
    assert clocks.status_code == 200


async def test_clock_json_roundtrip(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_ntr_cart,
    pubs_cart,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "clock-admin",
    )
    created = await client.post(
        "/autodj/clocks.json",
        json={
            "name": "Atelier test",
            "fallback_cart": "Jingles NTR",
            "motif": [
                {
                    "kind": "jingle",
                    "cart": "Jingles NTR",
                    "category": None,
                    "fallback_cart": "Jingles NTR",
                }
            ],
            "anchors": [
                {
                    "kind": "pub",
                    "cart": "Pubs",
                    "category": None,
                    "fallback_cart": "Jingles NTR",
                    "minute": 20,
                    "sync": "dure",
                }
            ],
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    clock = (await created.get_json())["clock"]
    assert clock["name"] == "Atelier test"
    assert clock["motif"][0]["kind"] == "jingle"
    assert clock["anchors"][0]["minute"] == 20

    updated = await client.put(
        f"/autodj/clocks/{clock['id']}.json",
        json={
            "name": "Atelier test",
            "fallback_cart": "Jingles NTR",
            "motif": clock["motif"]
            + [
                {
                    "kind": "musique",
                    "cart": None,
                    "category": "Rotation",
                    "fallback_cart": "Jingles NTR",
                }
            ],
            "anchors": clock["anchors"],
        },
    )
    assert updated.status_code == 200
    body = await updated.get_json()
    assert len(body["clock"]["motif"]) == 2

    deleted = await client.delete(f"/autodj/clocks/{clock['id']}.json")
    assert deleted.status_code == 200


async def test_clock_json_rejects_unknown_cart(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "clock-invalid-cart",
    )
    response = await client.post(
        "/autodj/clocks.json",
        json={
            "name": "Horloge invalide",
            "fallback_cart": "Cart absent",
            "motif": [
                {
                    "kind": "jingle",
                    "cart": "Autre cart absent",
                    "category": None,
                    "fallback_cart": None,
                }
            ],
            "anchors": [],
        },
    )
    assert response.status_code == 400
    assert "Unknown cart" in (await response.get_json())["error"]


async def test_clock_json_rejects_delete_when_used(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "clock-used",
    )
    slots = (await (await client.get("/autodj/slots.json")).get_json())["slots"]
    used_clock_id = next(slot["clock_id"] for slot in slots if slot["clock_id"])

    response = await client.delete(f"/autodj/clocks/{used_clock_id}.json")
    assert response.status_code == 409
    assert "assigned to a daypart" in (await response.get_json())["error"]


async def test_midnight_slot_is_immutable(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "midnight-guard",
    )
    slots = (await (await client.get("/autodj/slots.json")).get_json())["slots"]
    midnight = next(slot for slot in slots if slot["minute"] == 0)

    deleted = await client.delete(f"/autodj/slots/{midnight['id']}.json")
    assert deleted.status_code == 409

    moved = await client.put(
        f"/autodj/slots/{midnight['id']}.json",
        json={"day_of_week": midnight["day_of_week"], "minute": 60},
    )
    assert moved.status_code == 409

    renamed = await client.put(
        f"/autodj/slots/{midnight['id']}.json",
        json={"title": "Nuit protégée"},
    )
    assert renamed.status_code == 200


async def test_clock_json_forbidden_without_autodj(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "clock-guest",
        permissions={},
    )
    response = await client.post(
        "/autodj/clocks.json",
        json={"name": "Nope", "motif": [], "anchors": []},
    )
    assert response.status_code == 403


async def test_cart_json_forbidden_without_carts(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "cart-guest",
        permissions={},
    )
    response = await client.post("/carts.json", json={"title": "Nope"})
    assert response.status_code == 403
    bulk = await client.post("/carts/1/sounds/delete.json", json={"ids": [1]})
    assert bulk.status_code == 403


async def test_slot_and_category_json(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "slot-admin",
    )
    clocks = await (await client.get("/autodj/clocks.json")).get_json()
    clock_id = clocks["clocks"][0]["id"]
    created = await client.post(
        "/autodj/slots.json",
        json={
            "title": "Test 03h",
            "day_of_week": 2,
            "minute": 185,
            "clock_id": clock_id,
            "color": "#abcdef",
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    slot = (await created.get_json())["slot"]
    patched = await client.put(
        f"/autodj/slots/{slot['id']}.json",
        json={"title": "Test 03h bis"},
    )
    assert patched.status_code == 200
    assert (await patched.get_json())["slot"]["title"] == "Test 03h bis"
    deleted = await client.delete(f"/autodj/slots/{slot['id']}.json")
    assert deleted.status_code == 200

    cat = await client.post(
        "/autodj/categories.json",
        json={"name": "JSON Cat", "query": "grouping:rotation", "empty_query": ""},
    )
    assert cat.status_code == 201, await cat.get_data(as_text=True)
    cat_id = (await cat.get_json())["category"]["id"]
    gone = await client.delete(f"/autodj/categories/{cat_id}.json")
    assert gone.status_code == 200


async def test_cart_and_user_json(  # noqa: PLR0915
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    user = User(username="cart-admin")
    user.update_password(users_password)
    user.update_permissions_map({"admin": True})
    dbsession.add(user)
    await dbsession.commit()

    Scheduler.reset_instance()
    iface = interface_factory(app_configration, False, beets_integration)
    iface.config["INTERFACE_NAME"] = "New Trad Radio"
    Scheduler.init(app_configration, iface, demo=True)
    try:
        async with iface.test_app():
            client = iface.test_client()
            await _login(client, "cart-admin", users_password)
            cart = await client.post(
                "/carts.json",
                json={
                    "title": "JSON Cart",
                    "mode": "playlist",
                    "schedule_mode": "timed",
                },
            )
            assert cart.status_code == 201, await cart.get_data(as_text=True)
            cart_id = (await cart.get_json())["cart"]["id"]
            mp3 = ASSETS / "ohradiotomateoh.mp3"
            uploaded = await client.post(
                f"/carts/{cart_id}/sounds.json",
                files={"sounds": _mp3_upload(mp3)},
            )
            assert uploaded.status_code == 201, await uploaded.get_data(as_text=True)
            sounds = (await uploaded.get_json())["cart"]["sounds"]
            assert len(sounds) == 1
            edited = await client.put(
                f"/carts/{cart_id}/sounds/{sounds[0]['id']}.json",
                json={"title": "ID test", "active": False},
            )
            assert edited.status_code == 200, await edited.get_data(as_text=True)
            edited_sound = (await edited.get_json())["sound"]
            assert edited_sound["title"] == "ID test"
            assert edited_sound["active"] is False
            patched = await client.put(
                f"/carts/{cart_id}.json",
                json={
                    "mode": "random",
                    "queue": "autodj",
                    "schedule_mode": "timed",
                    "schedule_day_of_week": "2",
                    "schedule_hour": "14",
                    "schedule_minute": "3",
                    "notes": "atelier",
                },
            )
            assert patched.status_code == 200, await patched.get_data(as_text=True)
            cart_body = (await patched.get_json())["cart"]
            assert cart_body["mode"] == "random"
            assert cart_body["queue"] == "autodj"
            assert cart_body["schedule_hour"] == "14"
            assert cart_body["notes"] == "atelier"
            uploaded2 = await client.post(
                f"/carts/{cart_id}/sounds.json",
                files={"sounds": _mp3_upload(mp3)},
            )
            assert uploaded2.status_code == 201, await uploaded2.get_data(as_text=True)
            ids = [row["id"] for row in (await uploaded2.get_json())["cart"]["sounds"]]
            assert len(ids) == 2
            ranked = await client.put(
                f"/carts/{cart_id}/sounds/ranks.json",
                json={"ranks": list(reversed(ids))},
            )
            assert ranked.status_code == 200, await ranked.get_data(as_text=True)
            ranked_ids = [
                row["id"] for row in (await ranked.get_json())["cart"]["sounds"]
            ]
            assert ranked_ids == list(reversed(ids))
            gone_sound = await client.delete(
                f"/carts/{cart_id}/sounds/{sounds[0]['id']}.json",
            )
            assert gone_sound.status_code == 200
            gone = await client.delete(f"/carts/{cart_id}.json")
            assert gone.status_code == 200

            created_user = await client.post(
                "/users.json",
                json={
                    "username": "json-user",
                    "password": "json-user-password-ok",
                    "permissions": {"carts": True},
                },
            )
            assert created_user.status_code == 201, await created_user.get_data(
                as_text=True
            )
            user_id = (await created_user.get_json())["user"]["id"]
            listed = await (await client.get("/users.json")).get_json()
            assert any(row["username"] == "json-user" for row in listed["users"])
            deleted = await client.delete(f"/users/{user_id}.json")
            assert deleted.status_code == 200
    finally:
        Scheduler.reset_instance()


async def test_cart_sounds_bulk_json(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "cart-bulk",
    )
    created = await client.post(
        "/carts.json",
        json={"title": "Bulk Cart", "mode": "playlist", "schedule_mode": "timed"},
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    cart_id = (await created.get_json())["cart"]["id"]
    mp3 = ASSETS / "ohradiotomateoh.mp3"
    payload = mp3.read_bytes()
    first = await client.post(
        f"/carts/{cart_id}/sounds.json",
        files={"sounds": FileStorage(BytesIO(payload), "one.mp3", "audio/mpeg")},
    )
    assert first.status_code == 201, await first.get_data(as_text=True)
    skipped = await client.post(
        f"/carts/{cart_id}/sounds.json",
        files={
            "sounds": FileStorage(BytesIO(b"not audio"), "readme.txt", "text/plain"),
        },
    )
    assert skipped.status_code == 400
    second = await client.post(
        f"/carts/{cart_id}/sounds.json",
        files={"sounds": FileStorage(BytesIO(payload), "two.mp3", "audio/mpeg")},
    )
    assert second.status_code == 201, await second.get_data(as_text=True)
    sounds = (await second.get_json())["cart"]["sounds"]
    assert [row["title"] for row in sounds] == ["one.mp3", "two.mp3"]
    ids = [row["id"] for row in sounds]
    deleted = await client.post(
        f"/carts/{cart_id}/sounds/delete.json",
        json={"ids": ids},
    )
    assert deleted.status_code == 200, await deleted.get_data(as_text=True)
    assert (await deleted.get_json())["cart"]["sounds"] == []


async def test_spa_serves_console_dist(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    tmp_path: Path,
):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>spa-admin</title>",
        encoding="utf-8",
    )
    (dist / "logo-ntr.svg").write_text("<svg></svg>", encoding="utf-8")
    (assets / "app.js").write_text("ok", encoding="utf-8")
    iface = interface_factory(app_configration, False, beets_integration)
    iface.config["CONSOLE_DIST"] = str(dist)
    client = iface.test_client()
    page = await client.get("/antenne")
    assert page.status_code == 200
    assert b"spa-admin" in await page.get_data()
    home = await client.get("/")
    assert home.status_code == 200
    assert b"spa-admin" in await home.get_data()
    js = await client.get("/assets/app.js")
    assert js.status_code == 200
    assert await js.get_data() == b"ok"
    carts = await client.get("/carts")
    assert carts.status_code == 200
    assert b"spa-admin" in await carts.get_data()

from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy.orm import Session as ormSession
from werkzeug.datastructures import FileStorage

from radiotomate.beets import BeetsIntegration
from radiotomate.interface_app import app_factory as interface_factory
from radiotomate.models import User
from radiotomate.models.execution import ProgrammingVersion, RundownItem
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
    jingles_cart,
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
            "fallback_cart": "Jingles",
            "motif": [
                {
                    "kind": "jingle",
                    "cart": "Jingles",
                    "category": None,
                    "fallback_cart": "Jingles",
                }
            ],
            "anchors": [
                {
                    "kind": "pub",
                    "cart": "Pubs",
                    "category": None,
                    "fallback_cart": "Jingles",
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
            "fallback_cart": "Jingles",
            "motif": clock["motif"]
            + [
                {
                    "kind": "musique",
                    "cart": None,
                    "category": "Rotation",
                    "fallback_cart": "Jingles",
                }
            ],
            "anchors": clock["anchors"],
        },
    )
    assert updated.status_code == 200
    body = await updated.get_json()
    assert len(body["clock"]["motif"]) == 2

    retarget = await client.put(
        f"/autodj/clocks/{clock['id']}.json",
        json={
            "name": "Atelier test",
            "fallback_cart": "Jingles",
            "motif": [
                {
                    "id": body["clock"]["motif"][0]["id"],
                    "kind": "son",
                    "cart": "Pubs",
                    "category": None,
                    "fallback_cart": "Jingles",
                },
                body["clock"]["motif"][1],
            ],
            "anchors": body["clock"]["anchors"],
        },
    )
    assert retarget.status_code == 200, await retarget.get_data(as_text=True)
    saved = (await retarget.get_json())["clock"]
    assert saved["motif"][0]["id"] == body["clock"]["motif"][0]["id"]
    assert saved["motif"][0]["kind"] == "son"
    assert saved["motif"][0]["cart"] == "Pubs"
    assert saved["motif"][0]["cart_id"] == pubs_cart.id
    assert saved["motif"][1]["category"] == "Rotation"

    deleted = await client.delete(f"/autodj/clocks/{clock['id']}.json")
    assert deleted.status_code == 200


async def test_clock_put_appends_while_rundown_references_position(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "clock-fk-admin",
    )
    created = await client.post(
        "/autodj/clocks.json",
        json={
            "name": "FK clock",
            "fallback_cart": "Jingles",
            "motif": [
                {
                    "kind": "jingle",
                    "cart": "Jingles",
                    "category": None,
                    "fallback_cart": None,
                }
            ],
            "anchors": [],
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    clock = (await created.get_json())["clock"]
    position_id = clock["motif"][0]["id"]
    dbsession.add(
        ProgrammingVersion(id="pv-clock-fk", source="test", description="fk")
    )
    await dbsession.flush()
    dbsession.add(
        RundownItem(
            id="ri-clock-fk",
            programming_version_id="pv-clock-fk",
            sequence=0,
            planned_at=datetime.now(),
            duration=1,
            kind="jingle",
            when_mode="sequential",
            queue="jingles",
            resource="Jingles",
            clock_id=clock["id"],
            clock_position_id=position_id,
        )
    )
    await dbsession.commit()
    updated = await client.put(
        f"/autodj/clocks/{clock['id']}.json",
        json={
            "name": "FK clock",
            "fallback_cart": "Jingles",
            "motif": clock["motif"]
            + [
                {
                    "kind": "musique",
                    "cart": None,
                    "category": "Rotation",
                    "fallback_cart": None,
                }
            ],
            "anchors": [],
        },
    )
    assert updated.status_code == 200, await updated.get_data(as_text=True)
    body = await updated.get_json()
    assert [row["kind"] for row in body["clock"]["motif"]] == ["jingle", "musique"]
    assert body["clock"]["motif"][0]["id"] == position_id


async def test_clock_put_removes_position_referenced_by_rundown(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "clock-fk-remove-admin",
    )
    created = await client.post(
        "/autodj/clocks.json",
        json={
            "name": "FK remove clock",
            "fallback_cart": "Jingles",
            "motif": [
                {
                    "kind": "jingle",
                    "cart": "Jingles",
                    "category": None,
                    "fallback_cart": None,
                },
                {
                    "kind": "musique",
                    "cart": None,
                    "category": "Rotation",
                    "fallback_cart": None,
                },
            ],
            "anchors": [],
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    clock = (await created.get_json())["clock"]
    jingle_id = clock["motif"][0]["id"]
    music = clock["motif"][1]
    dbsession.add(
        ProgrammingVersion(id="pv-clock-fk-rm", source="test", description="fk-rm")
    )
    await dbsession.flush()
    dbsession.add(
        RundownItem(
            id="ri-clock-fk-rm",
            programming_version_id="pv-clock-fk-rm",
            sequence=0,
            planned_at=datetime.now(),
            duration=1,
            kind="jingle",
            when_mode="sequential",
            queue="jingles",
            resource="Jingles",
            clock_id=clock["id"],
            clock_position_id=jingle_id,
        )
    )
    await dbsession.commit()
    updated = await client.put(
        f"/autodj/clocks/{clock['id']}.json",
        json={
            "name": "FK remove clock",
            "fallback_cart": "Jingles",
            "motif": [music],
            "anchors": [],
        },
    )
    assert updated.status_code == 200, await updated.get_data(as_text=True)
    body = await updated.get_json()
    assert [row["kind"] for row in body["clock"]["motif"]] == ["musique"]
    assert body["clock"]["motif"][0]["id"] == music["id"]
    dbsession.expire_all()
    leftover = await RundownItem.from_id(dbsession, "ri-clock-fk-rm")
    assert leftover is not None
    assert leftover.clock_position_id is None


async def test_clock_put_without_ids_reuses_rundown_position(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "clock-noid-admin",
    )
    created = await client.post(
        "/autodj/clocks.json",
        json={
            "name": "FK noid clock",
            "fallback_cart": "Jingles",
            "motif": [
                {
                    "kind": "jingle",
                    "cart": "Jingles",
                    "category": None,
                    "fallback_cart": None,
                }
            ],
            "anchors": [],
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    clock = (await created.get_json())["clock"]
    position_id = clock["motif"][0]["id"]
    dbsession.add(
        ProgrammingVersion(id="pv-clock-noid", source="test", description="noid")
    )
    await dbsession.flush()
    dbsession.add(
        RundownItem(
            id="ri-clock-noid",
            programming_version_id="pv-clock-noid",
            sequence=0,
            planned_at=datetime.now(),
            duration=1,
            kind="jingle",
            when_mode="sequential",
            queue="jingles",
            resource="Jingles",
            clock_id=clock["id"],
            clock_position_id=position_id,
        )
    )
    await dbsession.commit()
    updated = await client.put(
        f"/autodj/clocks/{clock['id']}.json",
        json={
            "name": "FK noid clock",
            "fallback_cart": "Jingles",
            "motif": [
                {
                    "kind": "jingle",
                    "cart": "Jingles",
                    "category": None,
                    "fallback_cart": None,
                }
            ],
            "anchors": [],
        },
    )
    assert updated.status_code == 200, await updated.get_data(as_text=True)
    body = (await updated.get_json())["clock"]
    assert body["motif"][0]["id"] == position_id
    assert body["motif"][0]["cart"] == "Jingles"
    dbsession.expire_all()
    linked = await RundownItem.from_id(dbsession, "ri-clock-noid")
    assert linked is not None
    assert linked.clock_position_id == position_id


async def test_clock_put_fallback_while_rundown_references_position(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
    pubs_cart,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "clock-fallback-admin",
    )
    created = await client.post(
        "/autodj/clocks.json",
        json={
            "name": "FK fallback clock",
            "fallback_cart": "Jingles",
            "motif": [
                {
                    "kind": "jingle",
                    "cart": "Jingles",
                    "category": None,
                    "fallback_cart": None,
                }
            ],
            "anchors": [],
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    clock = (await created.get_json())["clock"]
    position = clock["motif"][0]
    dbsession.add(
        ProgrammingVersion(id="pv-clock-fb", source="test", description="fallback")
    )
    await dbsession.flush()
    dbsession.add(
        RundownItem(
            id="ri-clock-fb",
            programming_version_id="pv-clock-fb",
            sequence=0,
            planned_at=datetime.now(),
            duration=1,
            kind="jingle",
            when_mode="sequential",
            queue="jingles",
            resource="Jingles",
            clock_id=clock["id"],
            clock_position_id=position["id"],
        )
    )
    await dbsession.commit()
    clock_updated = await client.put(
        f"/autodj/clocks/{clock['id']}.json",
        json={
            "name": clock["name"],
            "fallback_cart": "Pubs",
            "fallback_cart_id": None,
            "motif": clock["motif"],
            "anchors": clock["anchors"],
        },
    )
    assert clock_updated.status_code == 200, await clock_updated.get_data(as_text=True)
    clock_body = (await clock_updated.get_json())["clock"]
    assert clock_body["fallback_cart"] == "Pubs"
    assert clock_body["fallback_cart_id"] == pubs_cart.id
    assert clock_body["motif"][0]["id"] == position["id"]

    position_updated = await client.put(
        f"/autodj/clocks/{clock['id']}.json",
        json={
            "name": clock["name"],
            "fallback_cart": "Pubs",
            "motif": [
                {
                    **position,
                    "fallback_cart": "Jingles",
                    "fallback_cart_id": None,
                }
            ],
            "anchors": [],
        },
    )
    assert position_updated.status_code == 200, await position_updated.get_data(
        as_text=True
    )
    position_body = (await position_updated.get_json())["clock"]
    assert position_body["motif"][0]["id"] == position["id"]
    assert position_body["motif"][0]["fallback_cart"] == "Jingles"
    dbsession.expire_all()
    linked = await RundownItem.from_id(dbsession, "ri-clock-fb")
    assert linked is not None
    assert linked.clock_position_id == position["id"]


async def test_clock_put_prefers_fallback_title_over_stale_id(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
    pubs_cart,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "clock-stale-fb",
    )
    created = await client.post(
        "/autodj/clocks.json",
        json={
            "name": "Stale fallback clock",
            "fallback_cart": "Jingles",
            "motif": [
                {
                    "kind": "jingle",
                    "cart": "Jingles",
                    "category": None,
                    "fallback_cart": None,
                }
            ],
            "anchors": [],
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    clock = (await created.get_json())["clock"]
    updated = await client.put(
        f"/autodj/clocks/{clock['id']}.json",
        json={
            "name": clock["name"],
            "fallback_cart": "Pubs",
            "fallback_cart_id": jingles_cart.id,
            "motif": clock["motif"],
            "anchors": clock["anchors"],
        },
    )
    assert updated.status_code == 200, await updated.get_data(as_text=True)
    body = (await updated.get_json())["clock"]
    assert body["fallback_cart"] == "Pubs"
    assert body["fallback_cart_id"] == pubs_cart.id

    cleared = await client.put(
        f"/autodj/clocks/{clock['id']}.json",
        json={
            "name": clock["name"],
            "fallback_cart": "",
            "fallback_cart_id": pubs_cart.id,
            "motif": clock["motif"],
            "anchors": clock["anchors"],
        },
    )
    assert cleared.status_code == 200, await cleared.get_data(as_text=True)
    cleared_body = (await cleared.get_json())["clock"]
    assert cleared_body["fallback_cart"] in {None, ""}
    assert cleared_body["fallback_cart_id"] is None


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
    iface.config["INTERFACE_NAME"] = "BUTTON"
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


async def test_cart_sounds_delta_json(
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
        "cart-delta",
    )
    created = await client.post(
        "/carts.json",
        json={"title": "Delta Cart", "mode": "playlist", "schedule_mode": "timed"},
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    cart_id = (await created.get_json())["cart"]["id"]
    mp3 = ASSETS / "ohradiotomateoh.mp3"
    first = await client.post(
        f"/carts/{cart_id}/sounds.json?delta=1",
        files={"sounds": _mp3_upload(mp3)},
    )
    assert first.status_code == 201, await first.get_data(as_text=True)
    first_body = await first.get_json()
    assert first_body["cart"]["delta"] is True
    assert len(first_body["cart"]["sounds"]) == 1
    second = await client.post(
        f"/carts/{cart_id}/sounds.json?delta=1",
        files={
            "sounds": FileStorage(
                BytesIO(mp3.read_bytes()),
                "two.mp3",
                "audio/mpeg",
            ),
        },
    )
    assert second.status_code == 201, await second.get_data(as_text=True)
    second_body = await second.get_json()
    assert [row["title"] for row in second_body["cart"]["sounds"]] == ["two.mp3"]
    listed = await (await client.get("/carts.json")).get_json()
    cart = next(row for row in listed["carts"] if row["id"] == cart_id)
    assert [row["title"] for row in cart["sounds"]] == ["ohradiotomateoh.mp3", "two.mp3"]


async def test_cart_attach_bank_path_json(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    media = tmp_path / "media"
    jingles = media / "30-habillage" / "jingles"
    jingles.mkdir(parents=True)
    mp3 = ASSETS / "ohradiotomateoh.mp3"
    bank_file = jingles / "jingle_button_ouverture.mp3"
    bank_file.write_bytes(mp3.read_bytes())
    inbox = media / "00-inbox" / "rotation"
    inbox.mkdir(parents=True)
    (inbox / "secret.mp3").write_bytes(mp3.read_bytes())
    monkeypatch.setenv("MEDIA_ROOT", str(media))

    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "cart-bank",
    )
    created = await client.post(
        "/carts.json",
        json={"title": "Bank Cart", "mode": "playlist", "schedule_mode": "timed"},
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    cart_id = (await created.get_json())["cart"]["id"]
    attached = await client.post(
        f"/carts/{cart_id}/sounds.json",
        json={"paths": ["30-habillage/jingles/jingle_button_ouverture.mp3"]},
    )
    assert attached.status_code == 201, await attached.get_data(as_text=True)
    sounds = (await attached.get_json())["cart"]["sounds"]
    assert len(sounds) == 1
    assert sounds[0]["path"].endswith("jingle_button_ouverture.mp3")
    blocked = await client.post(
        f"/carts/{cart_id}/sounds.json",
        json={"paths": ["00-inbox/rotation/secret.mp3"]},
    )
    assert blocked.status_code == 400
    gone = await client.delete(f"/carts/{cart_id}/sounds/{sounds[0]['id']}.json")
    assert gone.status_code == 200
    assert bank_file.is_file()


async def test_cart_attach_bank_folder_json(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    media = tmp_path / "media"
    jingles = media / "30-habillage" / "jingles"
    jingles.mkdir(parents=True)
    mp3 = ASSETS / "ohradiotomateoh.mp3"
    (jingles / "one.mp3").write_bytes(mp3.read_bytes())
    (jingles / "two.mp3").write_bytes(mp3.read_bytes())
    (jingles / "readme.txt").write_text("nope", encoding="utf-8")
    inbox = media / "00-inbox" / "habillage" / "jingles"
    inbox.mkdir(parents=True)
    (inbox / "secret.mp3").write_bytes(mp3.read_bytes())
    monkeypatch.setenv("MEDIA_ROOT", str(media))

    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "cart-bank-folder",
    )
    listed = await (await client.get("/media/bank.json")).get_json()
    assert listed["available"] is True
    assert any(
        row["path"] == "30-habillage/jingles" and row["count"] == 2
        for row in listed["folders"]
    )
    created = await client.post(
        "/carts.json",
        json={"title": "Folder Cart", "mode": "playlist", "schedule_mode": "timed"},
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    cart_id = (await created.get_json())["cart"]["id"]
    attached = await client.post(
        f"/carts/{cart_id}/sounds.json?delta=1",
        json={"folder": "30-habillage/jingles"},
    )
    assert attached.status_code == 201, await attached.get_data(as_text=True)
    body = await attached.get_json()
    assert body["cart"]["delta"] is True
    assert [row["title"] for row in body["cart"]["sounds"]] == ["one.mp3", "two.mp3"]
    blocked = await client.post(
        f"/carts/{cart_id}/sounds.json",
        json={"folder": "00-inbox/habillage/jingles"},
    )
    assert blocked.status_code == 400
    assert (jingles / "one.mp3").is_file()


async def test_cart_attach_rotation_folders_json(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    media = tmp_path / "media"
    jazz = media / "10-rotation" / "jazz"
    soul = media / "10-rotation" / "soul"
    jazz.mkdir(parents=True)
    soul.mkdir(parents=True)
    mp3 = ASSETS / "ohradiotomateoh.mp3"
    (jazz / "coltrane.mp3").write_bytes(mp3.read_bytes())
    (soul / "franklin.mp3").write_bytes(mp3.read_bytes())
    monkeypatch.setenv("MEDIA_ROOT", str(media))

    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "cart-rotation-folders",
    )
    listed = await (await client.get("/media/bank.json")).get_json()
    paths = {row["path"] for row in listed["folders"]}
    assert "10-rotation/jazz" not in paths
    assert "10-rotation/soul" not in paths
    created = await client.post(
        "/carts.json",
        json={"title": "Mix Cart", "mode": "playlist", "schedule_mode": "timed"},
    )
    cart_id = (await created.get_json())["cart"]["id"]
    page = await client.get(f"/carts/{cart_id}/sounds")
    html = await page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Depuis la banque" in html
    assert "Copier dans le cart" in html
    attached = await client.post(
        f"/carts/{cart_id}/sounds.json?delta=1",
        json={"folders": ["10-rotation/jazz", "10-rotation/soul"]},
    )
    assert attached.status_code == 400
    blocked_file = await client.post(
        f"/carts/{cart_id}/sounds.json",
        json={"paths": ["10-rotation/jazz/coltrane.mp3"]},
    )
    assert blocked_file.status_code == 400


async def test_cart_bank_folder_sync_once(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from radiotomate.models import Cart, User
    from radiotomate.services.carts import sync_bank_folder

    media = tmp_path / "media"
    jingles = media / "30-habillage" / "jingles"
    jingles.mkdir(parents=True)
    mp3 = ASSETS / "ohradiotomateoh.mp3"
    (jingles / "one.mp3").write_bytes(mp3.read_bytes())
    monkeypatch.setenv("MEDIA_ROOT", str(media))

    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "cart-bank-sync",
    )
    created = await client.post(
        "/carts.json",
        json={"title": "Finder Cart", "mode": "playlist", "schedule_mode": "timed"},
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    cart_id = (await created.get_json())["cart"]["id"]
    assert (await created.get_json())["cart"]["bank_folder"] == ""
    blocked = await client.put(
        f"/carts/{cart_id}.json",
        json={"bank_folder": "10-rotation"},
    )
    assert blocked.status_code == 400
    inbox = await client.put(
        f"/carts/{cart_id}.json",
        json={"bank_folder": "00-inbox/habillage/jingles"},
    )
    assert inbox.status_code == 400
    mapped = await client.put(
        f"/carts/{cart_id}.json",
        json={"bank_folder": "30-habillage/jingles"},
    )
    assert mapped.status_code == 200, await mapped.get_data(as_text=True)
    assert (await mapped.get_json())["cart"]["bank_folder"] == "30-habillage/jingles"

    dbsession.expire_all()
    cart = await Cart.from_id(dbsession, cart_id, load_sounds=True)
    user = await User.from_username(dbsession, "cart-bank-sync")
    assert cart is not None and user is not None
    first = await sync_bank_folder(dbsession, cart, user.id)
    await dbsession.commit()
    assert [sound.title for sound in first] == ["one.mp3"]
    second = await sync_bank_folder(dbsession, cart, user.id)
    await dbsession.commit()
    assert second == []
    dbsession.expire_all()
    cart = await Cart.from_id(dbsession, cart_id, load_sounds=True)
    assert cart is not None
    assert [sound.title for sound in cart.sounds] == ["one.mp3"]
    assert "30-habillage/jingles/one.mp3" in str(cart.sounds[0].path)


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
    (dist / "logo.svg").write_text("<svg></svg>", encoding="utf-8")
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


async def test_conducteur_post_refresh_and_reset(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
):
    from radiotomate.enums import CommandStatus, PlayoutAction, RundownStatus
    from radiotomate.models import PlayoutCommand, Setting
    from radiotomate.scheduler.clock import (
        SETTING_CLOCK_SEQ_CLOCK_ID,
        SETTING_CLOCK_SEQ_CURSOR,
        SETTING_CLOCK_SEQ_EPOCH,
    )

    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "conducteur-rebuild-admin",
    )
    await Setting.upsert(dbsession, SETTING_CLOCK_SEQ_CURSOR, "1")
    await Setting.upsert(dbsession, SETTING_CLOCK_SEQ_CLOCK_ID, "1")
    await dbsession.commit()
    dbsession.add(
        ProgrammingVersion(id="pv-conducteur-reset", source="test", description="reset")
    )
    await dbsession.flush()
    dbsession.add(
        RundownItem(
            id="ri-conducteur-planned",
            programming_version_id="pv-conducteur-reset",
            sequence=0,
            planned_at=datetime.now(),
            duration=1,
            kind="musique",
            when_mode="sequential",
            queue="autodj",
            resource="titre prévu",
            status=RundownStatus.PLANNED.value,
        )
    )
    dbsession.add(
        RundownItem(
            id="ri-conducteur-onair",
            programming_version_id="pv-conducteur-reset",
            sequence=1,
            planned_at=datetime.now(),
            duration=1,
            kind="musique",
            when_mode="sequential",
            queue="autodj",
            resource="titre à l'antenne",
            status=RundownStatus.ON_AIR.value,
        )
    )
    dbsession.add(
        RundownItem(
            id="ri-conducteur-queued",
            programming_version_id="pv-conducteur-reset",
            sequence=2,
            planned_at=datetime.now(),
            duration=8,
            kind="jingle",
            when_mode="sequential",
            queue="jingles",
            resource="jingle en file",
            status=RundownStatus.IN_QUEUE.value,
        )
    )
    dbsession.add(
        PlayoutCommand(
            id="pc-conducteur-queued",
            rundown_item_id="ri-conducteur-queued",
            action=PlayoutAction.QUEUE.value,
            queue="jingles",
            payload={"path": "/tmp/jingle.mp3"},
            idempotency_key="reset-queued-jingle",
            status=CommandStatus.PENDING.value,
        )
    )
    await dbsession.commit()

    refresh = await client.post("/autodj/conducteur.json", json={})
    assert refresh.status_code == 200, await refresh.get_data(as_text=True)
    refreshed = await refresh.get_json()
    assert refreshed["action"] == "refresh"
    assert refreshed["items"]
    dbsession.expire_all()
    cursor_row = await Setting.from_key(dbsession, SETTING_CLOCK_SEQ_CURSOR)
    assert cursor_row is not None
    assert int(cursor_row.value) == 1

    reset = await client.post(
        "/autodj/conducteur.json?horizon=30",
        json={"reset": True},
    )
    assert reset.status_code == 200, await reset.get_data(as_text=True)
    payload = await reset.get_json()
    assert payload["action"] == "reset"
    assert payload["items"]
    assert payload["items"][0]["id"] == "ri-conducteur-onair"
    planned = [
        item
        for item in payload["items"]
        if item.get("status_code") == "planned" and item.get("origin") != "desk"
    ]
    assert planned
    assert planned[0]["kind"] == "jingle"
    dbsession.expire_all()
    cursor_row = await Setting.from_key(dbsession, SETTING_CLOCK_SEQ_CURSOR)
    clock_row = await Setting.from_key(dbsession, SETTING_CLOCK_SEQ_CLOCK_ID)
    epoch_row = await Setting.from_key(dbsession, SETTING_CLOCK_SEQ_EPOCH)
    assert cursor_row is not None
    assert int(cursor_row.value) == 0
    assert clock_row is not None
    assert clock_row.value == ""
    assert epoch_row is not None
    assert int(epoch_row.value) >= 1
    assert await dbsession.get(RundownItem, "ri-conducteur-planned") is None
    assert await dbsession.get(RundownItem, "ri-conducteur-queued") is None
    on_air = await dbsession.get(RundownItem, "ri-conducteur-onair")
    assert on_air is not None
    assert on_air.status == RundownStatus.ON_AIR.value
    queued_cmd = await dbsession.get(PlayoutCommand, "pc-conducteur-queued")
    assert queued_cmd is not None
    assert queued_cmd.status == CommandStatus.EXPIRED.value
    assert queued_cmd.rundown_item_id is None


async def test_conducteur_post_forbidden_without_autodj(  # noqa: PLR0913
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
        "conducteur-no-autodj",
        permissions={"live": True},
    )
    response = await client.post("/autodj/conducteur.json", json={"reset": True})
    assert response.status_code == 403

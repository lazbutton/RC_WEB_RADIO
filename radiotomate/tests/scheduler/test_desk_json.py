from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as ormSession

from radiotomate.beets import BeetsIntegration
from radiotomate.domain.errors import DomainConflict
from radiotomate.enums import RundownStatus
from radiotomate.interface_app import app_factory as interface_factory
from radiotomate.models import Sound
from radiotomate.models.execution import ProgrammingVersion, RundownItem
from radiotomate.scheduler.execution import (
    DESK_ORIGIN,
    delete_desk_item,
    insert_desk_item,
    replace_forecast_items,
)
from radiotomate.scheduler_api import Scheduler
from tests.scheduler.test_admin_json import _admin_client


async def test_replace_forecast_keeps_desk_pins(dbsession: ormSession):
    dbsession.add(
        ProgrammingVersion(id="pv-desk-keep", source="test", description="desk")
    )
    await dbsession.flush()
    later = datetime.now() + timedelta(minutes=10)
    dbsession.add(
        RundownItem(
            id="ri-desk-pin",
            programming_version_id="pv-desk-keep",
            sequence=0,
            planned_at=later,
            duration=12,
            kind="son",
            when_mode="sequential",
            queue="carts",
            resource="Pin pupitre",
            path="/media/10-rotation/pin.mp3",
            status=RundownStatus.PLANNED.value,
            origin=DESK_ORIGIN,
        )
    )
    dbsession.add(
        RundownItem(
            id="ri-clock-planned",
            programming_version_id="pv-desk-keep",
            sequence=1,
            planned_at=later,
            duration=180,
            kind="musique",
            when_mode="sequential",
            queue="autodj",
            resource="Titre horloge",
            status=RundownStatus.PLANNED.value,
            origin="clock",
        )
    )
    await dbsession.commit()
    version = await dbsession.get(ProgrammingVersion, "pv-desk-keep")
    await replace_forecast_items(dbsession, version, [], now=datetime.now())
    await dbsession.commit()
    assert await dbsession.get(RundownItem, "ri-desk-pin") is not None
    assert await dbsession.get(RundownItem, "ri-clock-planned") is None


async def test_delete_clock_planned_item(dbsession: ormSession):
    dbsession.add(
        ProgrammingVersion(id="pv-clock-delete", source="test", description="del")
    )
    await dbsession.flush()
    dbsession.add(
        RundownItem(
            id="ri-clock-delete",
            programming_version_id="pv-clock-delete",
            sequence=0,
            planned_at=datetime.now() + timedelta(minutes=12),
            duration=180,
            kind="musique",
            when_mode="sequential",
            queue="autodj",
            resource="BUTTON Test — Rotation Recette",
            path="/media/10-rotation/BUTTON_Test_-_Rotation_Recette.mp3",
            status=RundownStatus.PLANNED.value,
            origin="clock",
        )
    )
    await dbsession.commit()
    await delete_desk_item(dbsession, "ri-clock-delete")
    assert await dbsession.get(RundownItem, "ri-clock-delete") is None


async def test_delete_hard_anchor_conflicts(dbsession: ormSession):
    dbsession.add(
        ProgrammingVersion(id="pv-desk-anchor", source="test", description="anchor")
    )
    await dbsession.flush()
    dbsession.add(
        RundownItem(
            id="ri-hard-anchor",
            programming_version_id="pv-desk-anchor",
            sequence=0,
            planned_at=datetime.now() + timedelta(minutes=5),
            duration=20,
            kind="pub",
            when_mode="anchored",
            queue="carts",
            resource="Pub :20",
            status=RundownStatus.PLANNED.value,
            origin="clock",
        )
    )
    await dbsession.commit()
    with pytest.raises(DomainConflict) as caught:
        await delete_desk_item(dbsession, "ri-hard-anchor")
    assert caught.value.status_code == 409
    assert await dbsession.get(RundownItem, "ri-hard-anchor") is not None


async def test_desk_insert_survives_preview_refresh(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
):
    _ = jingles_cart
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "desk-insert-admin",
    )
    created = await client.post(
        "/autodj/conducteur/items.json",
        json={
            "path": "/media/10-rotation/pupitre-pin.mp3",
            "title": "Pin pupitre",
            "artist": "Desk",
            "kind": "son",
            "duration": 12,
        },
    )
    assert created.status_code == 200, await created.get_data(as_text=True)
    payload = await created.get_json()
    desk_hits = [
        item
        for item in payload["items"]
        if item.get("origin") == DESK_ORIGIN
        and item.get("path") == "/media/10-rotation/pupitre-pin.mp3"
    ]
    assert desk_hits, payload["items"][:6]
    pin_id = desk_hits[0]["id"]

    refresh = await client.post("/autodj/conducteur.json", json={})
    assert refresh.status_code == 200
    refreshed = await refresh.get_json()
    assert any(item.get("id") == pin_id for item in refreshed["items"])

    preview = await client.get("/autodj/conducteur.json?horizon=30")
    assert preview.status_code == 200
    shown = await preview.get_json()
    assert any(item.get("id") == pin_id for item in shown["items"])


async def test_desk_delete_anchor_json_409(
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
        "desk-anchor-admin",
    )
    dbsession.add(
        ProgrammingVersion(id="pv-desk-http-anchor", source="test", description="a")
    )
    await dbsession.flush()
    dbsession.add(
        RundownItem(
            id="ri-http-anchor",
            programming_version_id="pv-desk-http-anchor",
            sequence=0,
            planned_at=datetime.now() + timedelta(minutes=8),
            duration=20,
            kind="pub",
            when_mode="anchored",
            queue="carts",
            resource="Pub ancrée",
            status=RundownStatus.PLANNED.value,
            origin="clock",
        )
    )
    await dbsession.commit()
    response = await client.delete("/autodj/conducteur/items/ri-http-anchor.json")
    assert response.status_code == 409


async def test_skip_keeps_following_desk_pins(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "desk-skip-admin",
    )
    await insert_desk_item(
        dbsession,
        {
            "path": "/media/10-rotation/after-skip.mp3",
            "title": "Après skip",
            "artist": "Desk",
            "kind": "son",
            "duration": 9,
        },
    )
    pin = await dbsession.scalar(
        select(RundownItem).where(
            RundownItem.origin == DESK_ORIGIN,
            RundownItem.path == "/media/10-rotation/after-skip.mp3",
        )
    )
    assert pin is not None
    pin_id = pin.id

    Scheduler.reset_instance()
    iface = interface_factory(app_configration, False, beets_integration)
    Scheduler.init(app_configration, iface, demo=True)
    try:
        async with iface.test_app():
            client = iface.test_client()
            login = await client.post(
                "/login",
                form={"username": "desk-skip-admin", "password": users_password},
            )
            assert login.status_code == 302
            skipped = await client.delete("/live.json")
            assert skipped.status_code == 200, await skipped.get_data(as_text=True)
            shown = await (await client.get("/autodj/conducteur.json")).get_json()
            assert any(item.get("id") == pin_id for item in shown["items"])
    finally:
        Scheduler.reset_instance()


async def test_desk_insert_resolves_cart_sound(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
):
    sound = (await dbsession.scalars(select(Sound).limit(1))).first()
    assert sound is not None
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "desk-sound-admin",
    )
    created = await client.post(
        "/autodj/conducteur/items.json",
        json={
            "path": str(sound.path),
            "title": sound.title,
            "kind": "jingle",
            "duration": sound.duration,
        },
    )
    assert created.status_code == 200, await created.get_data(as_text=True)
    payload = await created.get_json()
    hit = next(item for item in payload["items"] if item.get("origin") == DESK_ORIGIN)
    assert hit["sound_id"] == sound.id
    assert hit["cart_id"] == jingles_cart.id

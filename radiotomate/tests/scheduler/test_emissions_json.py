from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session as ormSession

from radiotomate.beets import BeetsIntegration
from radiotomate.domain.emission import ranges_overlap, weekly_contains
from radiotomate.interface import live as live_mod
from radiotomate.models import User
from tests.scheduler.test_admin_json import _admin_client
from tests.scheduler.test_studio_json import _interface_client, _login

PARIS = ZoneInfo("Europe/Paris")


def _today() -> int:
    return datetime.now(PARIS).weekday()


async def test_weekly_overlap_and_contains():
    now = datetime(2026, 9, 23, 20, 30, tzinfo=PARIS)  # mercredi
    assert weekly_contains(now, 2, 20 * 60, 22 * 60)
    assert not weekly_contains(now, 2, 22 * 60, 23 * 60)
    assert ranges_overlap(20 * 60, 22 * 60, 21 * 60, 23 * 60)
    assert not ranges_overlap(18 * 60, 20 * 60, 20 * 60, 22 * 60)


async def test_emissions_weekly_waiting_does_not_override_title(
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
        "emission-wait",
    )
    created = await client.post(
        "/antenne/emissions.json",
        json={
            "scope": "weekly",
            "title": "Hop Pop Hop",
            "artist": "avec l'equipe",
            "day_of_week": _today(),
            "start_minute": 0,
            "end_minute": 1440,
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    live_mod.set_live_snapshot(
        live_mod.normalize_live(
            {
                "status": "playing",
                "source": "autodj",
                "artist": "BUTTON",
                "title": "On air",
            }
        )
    )
    playing = await (await client.get("/live.json")).get_json()
    assert playing["title"] == "On air"
    assert playing["artist"] == "BUTTON"
    assert playing["emission"]["state"] == "waiting"
    assert playing["emission"]["title"] == "Hop Pop Hop"
    live_mod.set_live_snapshot(None)


async def test_emissions_weekly_on_air_overrides_title(
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
        "emission-air",
    )
    created = await client.post(
        "/antenne/emissions.json",
        json={
            "scope": "weekly",
            "title": "Hop Pop Hop",
            "artist": "avec l'equipe",
            "day_of_week": _today(),
            "start_minute": 0,
            "end_minute": 1440,
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    live_mod.set_live_snapshot(
        live_mod.normalize_live(
            {
                "status": "playing",
                "source": "stream",
                "artist": "BUTT",
                "title": "encoder",
            }
        )
    )
    playing = await (await client.get("/live.json")).get_json()
    assert playing["title"] == "Hop Pop Hop"
    assert playing["artist"] == "avec l'equipe"
    assert playing["emission"]["state"] == "on_air"
    live_mod.set_live_snapshot(None)


async def test_emissions_weekly_overlap_conflict(
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
        "emission-overlap",
    )
    first = await client.post(
        "/antenne/emissions.json",
        json={
            "scope": "weekly",
            "title": "A",
            "artist": "phrase A",
            "day_of_week": 2,
            "start_minute": 20 * 60,
            "end_minute": 22 * 60,
        },
    )
    assert first.status_code == 201, await first.get_data(as_text=True)
    second = await client.post(
        "/antenne/emissions.json",
        json={
            "scope": "weekly",
            "title": "B",
            "artist": "phrase B",
            "day_of_week": 2,
            "start_minute": 21 * 60,
            "end_minute": 23 * 60,
        },
    )
    assert second.status_code == 409
    body = await second.get_json()
    assert "chevauche" in body["error"]


async def test_emissions_session_then_expires_when_source_leaves(
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
        "emission-session",
    )
    live_mod.set_live_snapshot(
        live_mod.normalize_live(
            {
                "status": "playing",
                "source": "stream",
                "artist": "BUTT",
                "title": "encoder",
            }
        )
    )
    created = await client.post(
        "/antenne/emissions.json",
        json={
            "scope": "session",
            "title": "Direct surprise",
            "artist": "en plateau",
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    playing = await (await client.get("/live.json")).get_json()
    assert playing["emission"]["state"] == "on_air"
    assert playing["title"] == "Direct surprise"

    live_mod.set_live_snapshot(
        live_mod.normalize_live(
            {
                "status": "playing",
                "source": "autodj",
                "artist": "BUTTON",
                "title": "Retour rotation",
            }
        )
    )
    after = await (await client.get("/live.json")).get_json()
    assert after["title"] == "Retour rotation"
    assert after.get("emission") is None
    listed = await (await client.get("/antenne/emissions.json")).get_json()
    assert listed["current"] is None
    live_mod.set_live_snapshot(None)


async def test_emissions_session_forbidden_without_live(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    user = User(username="emission-nolive")
    user.update_password(users_password)
    user.update_permissions({"can_autodj": "true"})
    dbsession.add(user)
    await dbsession.commit()
    client = _interface_client(app_configration, beets_integration)
    await _login(client, "emission-nolive", users_password)
    live_mod.set_live_snapshot(
        live_mod.normalize_live({"status": "playing", "source": "stream"})
    )
    response = await client.post(
        "/antenne/emissions.json",
        json={"scope": "session", "title": "X", "artist": "Y"},
    )
    assert response.status_code == 403
    live_mod.set_live_snapshot(None)


async def test_metadata_log_relay_uses_on_air_emission(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    auth: dict,
):
    client = await _admin_client(
        app_configration,
        beets_integration,
        dbsession,
        users_password,
        "emission-relay",
    )
    created = await client.post(
        "/antenne/emissions.json",
        json={
            "scope": "weekly",
            "title": "Hop Pop Hop",
            "artist": "avec l'equipe",
            "day_of_week": _today(),
            "start_minute": 0,
            "end_minute": 1440,
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)

    raw_app.config["RELAY_METADATA_TO"] = [
        {"url": "https://website.radio/playlist/add_item"}
    ]
    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_relay:
        async with raw_app.test_app():
            scheduler = raw_app.test_client()
            result = await scheduler.post(
                "/metadata_log",
                json={
                    "source": "stream",
                    "artist": "BUTT",
                    "title": "encoder",
                    "on_air": datetime.now().isoformat(),
                },
                headers=auth,
            )
            assert result.status_code == 200, await result.get_data(as_text=True)

    posted = mock_relay.call_args.kwargs["data"]
    assert posted["title"] == "Hop Pop Hop"
    assert posted["artist"] == "avec l'equipe"
    assert posted["source"] == "stream"


async def test_emissions_do_not_change_slots_or_clocks(
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
        "emission-slots",
    )
    before_slots = await (await client.get("/autodj/slots.json")).get_json()
    before_clocks = await (await client.get("/autodj/clocks.json")).get_json()
    created = await client.post(
        "/antenne/emissions.json",
        json={
            "scope": "weekly",
            "title": "Hop Pop Hop",
            "artist": "avec l'equipe",
            "day_of_week": 2,
            "start_minute": 20 * 60,
            "end_minute": 22 * 60,
        },
    )
    assert created.status_code == 201, await created.get_data(as_text=True)
    after_slots = await (await client.get("/autodj/slots.json")).get_json()
    after_clocks = await (await client.get("/autodj/clocks.json")).get_json()
    assert after_slots == before_slots
    assert after_clocks == before_clocks

from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.orm import Session as ormSession

from radiotomate.beets import BeetsIntegration
from radiotomate.interface import live as live_mod
from radiotomate.models import Sound, User
from radiotomate.scheduler_api import Scheduler
from tests.scheduler.test_studio_json import _interface_client, _login


def test_normalize_live_cues_and_simulating():
    payload = live_mod.normalize_live(
        {
            "status": "simulating",
            "source": "autodj",
            "artist": "Demo",
            "title": "Now",
            "kind": "musique",
            "remaining": "12.4",
            "elapsed": "3.1",
            "next_autodj": {"title": "Next", "artist": "A", "rid": 2},
            "next_jingle": {"rid": -1},
            "next_cart": '{"title": "Spot", "artist": "Pub", "rid": 5}',
        }
    )
    assert payload["status"] == "simulating"
    assert payload["kind"] == "musique"
    assert payload["remaining"] == 12.4
    assert payload["next_jingle"] is None
    assert payload["next_autodj"]["title"] == "Next"
    assert payload["next_cart"]["title"] == "Spot"


def test_normalize_live_ignores_initial_track_mark():
    payload = live_mod.normalize_live(
        {
            "status": "playing",
            "source": "INSERT_INITIAL_TRACK_MARK",
            "title": "dummy",
            "jingles_queued": "2",
            "autodj_queued": "3",
            "carts_queued": "1",
        }
    )
    assert payload["source"] == "autodj"
    assert payload["jingles_queued"] == 2
    assert payload["autodj_queued"] == 3
    assert payload["carts_queued"] == 1


async def test_live_json_unauthenticated(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
):
    live_mod.set_live_snapshot(None)
    client = _interface_client(app_configration, beets_integration)
    response = await client.get("/live.json")
    assert response.status_code == 401
    delete = await client.delete("/live.json")
    assert delete.status_code == 401
    post = await client.post("/carts/1/now.json")
    assert post.status_code == 401


async def test_live_json_offline_and_snapshot(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    live_mod.set_live_snapshot(None)
    user = User(username="live-json-read")
    user.update_password(users_password)
    dbsession.add(user)
    await dbsession.commit()
    client = _interface_client(app_configration, beets_integration)
    await _login(client, "live-json-read", users_password)
    offline = await (await client.get("/live.json")).get_json()
    assert offline["status"] == "offline"

    live_mod.set_live_snapshot(
        live_mod.normalize_live(
            {
                "status": "playing",
                "source": "autodj",
                "artist": "BUTTON",
                "title": "On air",
                "remaining": 8,
                "elapsed": 2,
                "next_autodj": {"title": "Suite", "artist": "B", "rid": 3},
            }
        )
    )
    playing = await (await client.get("/live.json")).get_json()
    assert playing["status"] == "playing"
    assert playing["title"] == "On air"
    assert playing["next_autodj"]["title"] == "Suite"
    live_mod.set_live_snapshot(None)


async def test_skip_json_forbidden_without_live(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    user = User(username="live-json-nope")
    user.update_password(users_password)
    dbsession.add(user)
    await dbsession.commit()
    client = _interface_client(app_configration, beets_integration)
    await _login(client, "live-json-nope", users_password)
    response = await client.delete("/live.json")
    assert response.status_code == 403
    payload = await response.get_json()
    assert payload["error"] == "forbidden"


async def test_skip_json_ok_with_scheduler_mock(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    user = User(username="live-json-skip")
    user.update_password(users_password)
    user.update_permissions({"can_live": "true"})
    dbsession.add(user)
    await dbsession.commit()
    client = _interface_client(app_configration, beets_integration)
    await _login(client, "live-json-skip", users_password)
    mock = MagicMock()
    mock.skip = AsyncMock()
    with patch.object(Scheduler, "get", return_value=mock):
        response = await client.delete("/live.json")
    assert response.status_code == 200
    assert (await response.get_json())["ok"] is True
    mock.skip.assert_awaited_once()


async def test_push_now_json_404_and_200(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
):
    user = User(username="live-json-fire")
    user.update_password(users_password)
    user.update_permissions({"can_live": "true"})
    dbsession.add(user)
    await dbsession.commit()
    client = _interface_client(app_configration, beets_integration)
    await _login(client, "live-json-fire", users_password)

    missing = await client.post("/carts/99999/now.json")
    assert missing.status_code == 404

    mock = MagicMock()
    mock.push_cart = AsyncMock()
    with patch.object(Scheduler, "get", return_value=mock):
        ok = await client.post(f"/carts/{jingles_cart.id}/now.json")
    assert ok.status_code == 200
    assert (await ok.get_json())["ok"] is True
    mock.push_cart.assert_awaited_once_with(jingles_cart.id)


async def test_push_sound_now_json(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
):
    user = User(username="live-json-pad")
    user.update_password(users_password)
    user.update_permissions({"can_live": "true"})
    dbsession.add(user)
    await dbsession.commit()
    sound = await dbsession.scalar(
        select(Sound).where(Sound.cart_id == jingles_cart.id)
    )
    assert sound is not None
    client = _interface_client(app_configration, beets_integration)
    await _login(client, "live-json-pad", users_password)

    missing = await client.post(f"/carts/{jingles_cart.id}/sounds/99999/now.json")
    assert missing.status_code == 404

    mock = MagicMock()
    mock.push_sound = AsyncMock()
    with patch.object(Scheduler, "get", return_value=mock):
        ok = await client.post(
            f"/carts/{jingles_cart.id}/sounds/{sound.id}/now.json",
        )
    assert ok.status_code == 200
    assert (await ok.get_json())["ok"] is True
    mock.push_sound.assert_awaited_once_with(jingles_cart.id, sound.id)


async def test_now_json_conflict_when_harbor(  # noqa: PLR0913
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
    jingles_cart,
):
    user = User(username="live-json-harbor")
    user.update_password(users_password)
    user.update_permissions({"can_live": "true"})
    dbsession.add(user)
    await dbsession.commit()
    client = _interface_client(app_configration, beets_integration)
    await _login(client, "live-json-harbor", users_password)
    live_mod.set_live_snapshot(
        {
            "status": "playing",
            "source": "stream",
            "title": "Hop Pop Hop",
            "artist": "Marie",
        }
    )
    mock = MagicMock()
    mock.push_cart = AsyncMock()
    mock.push_sound = AsyncMock()
    mock.push_path = AsyncMock()
    try:
        with patch.object(Scheduler, "get", return_value=mock):
            cart = await client.post(f"/carts/{jingles_cart.id}/now.json")
            now = await client.post(
                "/autodj/conducteur/now.json",
                json={"path": "/media/x.mp3", "title": "X", "artist": "Y"},
            )
    finally:
        live_mod.set_live_snapshot(None)
    assert cart.status_code == 409
    assert (await cart.get_json())["code"] == "harbor_live"
    assert now.status_code == 409
    mock.push_cart.assert_not_called()
    mock.push_path.assert_not_called()


async def test_antenne_state_and_flush_json(
    raw_app,
    app_configration: dict,
    beets_integration: BeetsIntegration,
    dbsession: ormSession,
    users_password: str,
):
    user = User(username="antenne-state")
    user.update_password(users_password)
    user.update_permissions({"can_live": "true"})
    dbsession.add(user)
    await dbsession.commit()
    client = _interface_client(app_configration, beets_integration)

    anonymous = await client.get("/antenne/state.json")
    assert anonymous.status_code == 401

    await _login(client, "antenne-state", users_password)
    mock = MagicMock()
    mock.state = AsyncMock(
        return_value={"playout": {"reachable": True}, "incidents": []}
    )
    mock.flush_queues = AsyncMock()
    with patch.object(Scheduler, "get", return_value=mock):
        state = await client.get("/antenne/state.json")
        assert state.status_code == 200
        assert (await state.get_json())["playout"]["reachable"] is True

        mock.state = AsyncMock(return_value=None)
        down = await client.get("/antenne/state.json")
        assert down.status_code == 502

        flushed = await client.post("/antenne/flush.json")
        assert flushed.status_code == 200
        mock.flush_queues.assert_awaited_once()


async def test_antenne_state_demo_scheduler():
    from radiotomate.scheduler_api import SchedulerDemo

    demo = SchedulerDemo.__new__(SchedulerDemo)
    demo._played = [
        {
            "at": "2026-09-21T21:00:00",
            "queue": "autodj",
            "artist": "nowave",
            "title": "Dahlia",
        },
    ]
    body = await demo.state()
    assert body["demo"] is True
    assert body["engine"]["heartbeat_ok"] is True
    assert body["listeners"][0]["mount"] == "button.mp3"
    assert body["asrun"][0]["title"] == "Dahlia"

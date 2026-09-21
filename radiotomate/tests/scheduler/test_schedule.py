from asyncio import sleep
from unittest.mock import patch

import httpx
from quart.testing import QuartClient
from sqlalchemy.orm import Session as ormSession

from radiotomate.enums import CartMode, ScheduleMode
from radiotomate.interface.carts import apply_schedule
from radiotomate.models import Cart, Sound
from radiotomate.models.cart import URL_TO_AUTODJ_QUEUE


def test_schedule_summary():
    cart = Cart(
        schedule_mode=ScheduleMode.JINGLES,
        schedule_correct=True,
    )
    assert cart.schedule_summary() == "Jingles"
    cart.schedule_correct = False
    assert "erreur" in cart.schedule_summary()

    cart = Cart(
        schedule_mode=ScheduleMode.TIMED,
        schedule_correct=True,
        schedule_year="*",
        schedule_month="*",
        schedule_day="*",
        schedule_week="*",
        schedule_day_of_week="2",
        schedule_hour="14",
        schedule_minute="3",
        schedule_second="0",
    )
    assert cart.schedule_summary() == "Mer. 14:03"


async def test_schedule_is_advanced():
    form = {
        "schedule": ScheduleMode.TIMED.name,
        "year": "",
        "month": "",
        "day": "",
        "week": "",
        "day_of_week": "3",
        "hour": "8",
        "minute": "12",
        "second": "",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is False

    # this is what's actually sent by the default simple form
    form = {
        "schedule": ScheduleMode.TIMED.name,
        "day_of_week": "0",
        "hour": "0",
        "minute": "0",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is False

    form = {
        "schedule": ScheduleMode.JINGLES.name,
        "year": "",
        "month": "",
        "day": "",
        "week": "",
        "day_of_week": "1",
        "hour": "8",
        "minute": "0",
        "second": "",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is False

    form = {
        "schedule": ScheduleMode.TIMED.name,
        "year": "",
        "month": "",
        "day": "",
        "week": "",
        "day_of_week": "*",
        "hour": "1",
        "minute": "12",
        "second": "",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is True

    form = {
        "schedule": ScheduleMode.TIMED.name,
        "year": "",
        "month": "",
        "day": "",
        "week": "",
        "day_of_week": "3",
        "hour": "*",
        "minute": "0",
        "second": "",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is True

    form = {
        "schedule": ScheduleMode.TIMED.name,
        "year": "",
        "month": "",
        "day": "",
        "week": "",
        "day_of_week": "3",
        "hour": "8",
        "minute": "*/10",
        "second": "",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is True

    form = {
        "schedule": ScheduleMode.TIMED.name,
        "year": "",
        "month": "",
        "day": "",
        "week": "",
        "day_of_week": "3",
        "hour": "8",
        "minute": "0",
        "second": "30",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is True

    form = {
        "schedule": ScheduleMode.TIMED.name,
        "year": "2022",
        "month": "",
        "day": "",
        "week": "",
        "day_of_week": "3",
        "hour": "8",
        "minute": "12",
        "second": "",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is True

    form = {
        "schedule": ScheduleMode.TIMED.name,
        "year": "",
        "month": "12",
        "day": "",
        "week": "",
        "day_of_week": "3",
        "hour": "8",
        "minute": "12",
        "second": "",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is True

    form = {
        "schedule": ScheduleMode.TIMED.name,
        "year": "",
        "month": "",
        "day": "1",
        "week": "",
        "day_of_week": "3",
        "hour": "8",
        "minute": "12",
        "second": "",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is True

    form = {
        "schedule": ScheduleMode.TIMED.name,
        "year": "",
        "month": "",
        "day": "",
        "week": "1",
        "day_of_week": "3",
        "hour": "8",
        "minute": "12",
        "second": "",
    }
    cart = apply_schedule(Cart(), form)
    assert cart.schedule_is_advanced is True


async def test_schedule(
    client: QuartClient,
    dbsession: ormSession,
    fake_cart: Cart,
    fake_sound: Sound,
    auth: dict,
):
    # Non-cron carts: the PUT only removes any stale job and succeeds.
    for mode in (ScheduleMode.JINGLES, ScheduleMode.CLOCK):
        fake_cart.schedule_mode = mode
        await dbsession.commit()
        result = await client.put(f"/schedule/{fake_cart.id}", headers=auth)
        result_text = (await result.data).decode()
        assert result.status_code == 200, "got non-200 response:" + result_text
        result = await client.get(f"/schedule/{fake_cart.id}", headers=auth)
        assert result.status_code == 404

    result = await client.put("/schedule/123456789", headers=auth)
    assert result.status_code == 404
    result = await client.put("/schedule/lol", headers=auth)
    assert result.status_code == 404

    # Retired cron mode: the PUT is still accepted, nothing is armed.
    fake_cart.schedule_mode = ScheduleMode.TIMED
    await dbsession.commit()
    result = await client.put(f"/schedule/{fake_cart.id}", headers=auth)
    assert result.status_code == 200
    result = await client.get(f"/schedule/{fake_cart.id}", headers=auth)
    assert result.status_code == 404
    result = await client.get("/schedule/lol", headers=auth)
    assert result.status_code == 404


async def test_push_now(
    client: QuartClient,
    fake_cart: Cart,
    fake_sound: Sound,
    auth: dict,
):
    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_client:
        result = await client.post(f"/schedule/{fake_cart.id}/now", headers=auth)
        result_text = (await result.data).decode()
        assert result.status_code == 200, "got non-OK response:" + result_text
        mock_client.assert_called_once_with(
            "/queue/carts",
            json={
                "path": str(fake_sound.path),
                "artist": fake_cart.title,
                "title": fake_sound.title,
                "radiotomate_sound_id": fake_sound.id,
                "rg_track_gain": str(fake_sound.gain),
            },
        )

    result = await client.post("/schedule/123456789/now", headers=auth)
    assert result.status_code == 404
    result = await client.post("/schedule/lol/now", headers=auth)
    assert result.status_code == 404


async def test_push_named_sound(
    client: QuartClient,
    fake_cart: Cart,
    fake_sound: Sound,
    fake_sound2: Sound,
    auth: dict,
):
    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_client:
        result = await client.post(
            f"/schedule/{fake_cart.id}/sounds/{fake_sound2.id}/now",
            headers=auth,
        )
        assert result.status_code == 200
        mock_client.assert_called_once_with(
            "/queue/carts",
            json={
                "path": str(fake_sound2.path),
                "artist": fake_cart.title,
                "title": fake_sound2.title,
                "radiotomate_sound_id": fake_sound2.id,
                "rg_track_gain": str(fake_sound2.gain),
            },
        )

    missing = await client.post(
        f"/schedule/{fake_cart.id}/sounds/99999/now",
        headers=auth,
    )
    assert missing.status_code == 404


async def test_timed_playlist_enqueues_two_sounds(  # noqa: PLR0913
    client: QuartClient,
    dbsession: ormSession,
    fake_cart: Cart,
    fake_sound: Sound,
    fake_sound2: Sound,
    auth: dict,
):
    fake_sound.rank = 1
    fake_sound2.rank = 2
    fake_sound2.gain = -1.0
    fake_sound2.peak = -0.5
    await dbsession.commit()
    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_client:
        result = await client.post(f"/schedule/{fake_cart.id}/now", headers=auth)
        assert result.status_code == 200
        assert mock_client.call_count == 2
        titles = [call.kwargs["json"]["title"] for call in mock_client.call_args_list]
        assert titles == [fake_sound.title, fake_sound2.title]


async def test_schedule_no_sound_enabled(
    client: QuartClient,
    dbsession: ormSession,
    fake_cart: Cart,
    fake_sound: Sound,
    auth: dict,
):
    fake_sound.active = False
    await dbsession.commit()

    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_client:
        result = await client.post(f"/schedule/{fake_cart.id}/now", headers=auth)
        assert result.status_code == 200, (await result.data).decode()
        mock_client.assert_not_called()


async def test_schedule_no_sound_analyzed(
    client: QuartClient,
    dbsession: ormSession,
    fake_cart: Cart,
    fake_sound: Sound,
    auth: dict,
):
    fake_sound.gain = None
    await dbsession.commit()

    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_client:
        result = await client.post(f"/schedule/{fake_cart.id}/now", headers=auth)
        assert result.status_code == 200, (await result.data).decode()
        mock_client.assert_not_called()


async def test_sound_max_duration(
    client: QuartClient,
    dbsession: ormSession,
    fake_cart: Cart,
    fake_sound: Sound,
    auth: dict,
):
    """
    A sounds cart with a max_duration asks the playout to skip the pushed sound
    once the time is over (in-process timer, no APScheduler).
    """
    fake_cart.max_duration = 1
    await dbsession.commit()

    with (
        patch(
            "httpx.AsyncClient.delete",
            return_value=httpx.Response(200),
        ) as mock_client_delete,
        patch(
            "httpx.AsyncClient.post",
            return_value=httpx.Response(200, json={"OK": 1}),
        ),
    ):
        result = await client.post(f"/schedule/{fake_cart.id}/now", headers=auth)
        assert result.status_code == 200, (await result.data).decode()
        armed = await client.get(f"/schedule/{fake_cart.id}", headers=auth)
        assert armed.status_code == 200
        assert "next_time" in await armed.json

        await sleep(1.5)  # the timer fires after max_duration
        mock_client_delete.assert_called_once_with(
            "/live",
            params={
                "radiotomate_sound_id": fake_sound.id,
            },
        )
    assert (
        await client.get(f"/schedule/{fake_cart.id}", headers=auth)
    ).status_code == 404


async def test_relay(
    client: QuartClient,
    dbsession: ormSession,
    fake_cart: Cart,
    auth: dict,
):
    """
    A relay cart POSTs /relay when pushed and DELETEs /live after max_duration.
    """
    fake_cart.mode = CartMode.RELAY
    fake_cart.url = "https://radiotomate.trying.to.test.a.stream.lol/something.flac"
    fake_cart.max_duration = 1
    await dbsession.commit()

    with (
        patch(
            "httpx.AsyncClient.delete",
            return_value=httpx.Response(200),
        ) as mock_client_delete,
        patch(
            "httpx.AsyncClient.post",
            return_value=httpx.Response(200, json={"OK": 1}),
        ) as mock_client_post,
    ):
        result = await client.post(f"/schedule/{fake_cart.id}/now", headers=auth)
        assert result.status_code == 200, (await result.data).decode()

        await sleep(1.5)
        mock_client_post.assert_called_once_with(
            "/relay",
            params={
                "url": fake_cart.url,
            },
        )
        mock_client_delete.assert_called_once_with(
            "/live",
            params={
                "relay": 1,
            },
        )


async def test_schedule_to_autodj(
    client: QuartClient,
    dbsession: ormSession,
    fake_cart: Cart,
    fake_sound: Sound,
    auth: dict,
):
    fake_cart.url = URL_TO_AUTODJ_QUEUE
    await dbsession.commit()

    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_client:
        result = await client.post(f"/schedule/{fake_cart.id}/now", headers=auth)
        assert result.status_code == 200, (await result.data).decode()
        mock_client.assert_called_once_with(
            "/queue/autodj",
            json={
                "path": str(fake_sound.path),
                "artist": fake_cart.title,
                "title": fake_sound.title,
                "radiotomate_sound_id": fake_sound.id,
                "rg_track_gain": str(fake_sound.gain),
            },
        )


async def test_queues_flush(
    client: QuartClient,
    auth: dict,
):
    from unittest.mock import AsyncMock

    from radiotomate.scheduler.playout import PlayoutGateway

    payload = {
        "autodj": {"removed": 1, "kept": 1},
        "jingles": {"removed": 5, "kept": 0},
        "carts": {"removed": 0, "kept": 0},
    }
    with patch.object(
        PlayoutGateway,
        "_call",
        new=AsyncMock(return_value=httpx.Response(200, json=payload)),
    ):
        result = await client.post("/queues/flush", headers=auth)
    assert result.status_code == 200, await result.get_data(as_text=True)
    body = await result.get_json()
    assert body["ok"] is True
    assert body["queues"]["jingles"]["removed"] == 5

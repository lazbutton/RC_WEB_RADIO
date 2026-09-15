import json
from collections import Counter
from datetime import datetime
from unittest.mock import patch

import httpx
from quart.testing import QuartClient
from sqlalchemy.orm import Session as ormSession

from radiotomate.enums import ScheduleMode
from radiotomate.interface.autodj import MultiDict, process_filters
from radiotomate.models import AutoDJSlot, Cart, Sound
from radiotomate.quart import CustomQuart


async def test_live(client: QuartClient, auth: dict):
    async with client.request("/live", headers=auth) as connection:
        # first simulate a Liquidsoap heartbeat
        fake_md = {
            "artist": "Johnny Tester",
            "title": "Radio killed the radio star (a capella)",
            "source": "unittests",
            "remaining": "176.2344",
            "elapsed": "3.212",
            "time": datetime.now().isoformat(),
        }

        await client.post("/live", headers=auth, json=fake_md)

        # now on the receiver side
        response = await connection.receive()
        assert connection.status_code == 200, "got non-OK response:" + response.decode()
        data = response.split(b"\ndata: ")[1]
        assert json.loads(data) == fake_md
        await connection.disconnect()


def fake_md() -> dict:
    return {
        "artist": "Bonnie Tester",
        "title": "Jingle bells",
        "source": "unittests",
        "remaining": "176.2344",
        "elapsed": "3.1415",
        "time": datetime.now().isoformat(),
    }


async def test_react_to_empty_jingle_queue(
    raw_app: CustomQuart,
    dbsession: ormSession,
    fake_cart: Cart,
    fake_sound: Sound,
    auth: dict,
):
    "normal case: push a jingle"
    fake_cart.schedule_mode = ScheduleMode.JINGLES
    await dbsession.commit()

    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_liquidsoap:
        async with raw_app.test_app():
            client = raw_app.test_client()
            metadata = fake_md()
            metadata["next_jingle"] = {"rid": -1}
            result = await client.post("/live", headers=auth, json=metadata)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text
        mock_liquidsoap.assert_called_once_with(
            "/queue/jingles",
            json={
                "path": str(fake_sound.path),
                "artist": "",
                "title": "",
                "radiotomate_sound_id": fake_sound.id,
                "rg_track_gain": str(str(fake_sound.gain)),
            },
        )


async def test_react_to_empty_jingle_queue_robust(
    raw_app: CustomQuart,
    dbsession: ormSession,
    fake_cart: Cart,
    fake_sound: Sound,
    auth: dict,
):
    "No jingle is enabled: should not crash"
    fake_cart.schedule_mode = ScheduleMode.JINGLES
    fake_sound.active = False
    await dbsession.commit()

    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_liquidsoap:
        async with raw_app.test_app():
            client = raw_app.test_client()
            metadata = fake_md()
            metadata["next_jingle"] = {"rid": -1}
            result = await client.post("/live", headers=auth, json=metadata)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text
        mock_liquidsoap.assert_not_called()


async def test_react_to_empty_jingle_queue_but_analyzing(
    raw_app: CustomQuart,
    dbsession: ormSession,
    fake_cart: Cart,
    fake_sound: Sound,
    auth: dict,
):
    """
    No jingle has been analyzed yet: should not crash.

    Be careful to unset gain after the application start. Otherwise the sound will be
    picked during background analyzer's start, as it tries to catch-up missed analysis.
    """
    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_liquidsoap:
        async with raw_app.test_app():
            fake_cart.schedule_mode = ScheduleMode.JINGLES
            fake_sound.gain = None
            await dbsession.commit()

            client = raw_app.test_client()
            metadata = fake_md()
            metadata["next_jingle"] = {"rid": -1}
            result = await client.post("/live", headers=auth, json=metadata)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text
        mock_liquidsoap.assert_not_called()


async def test_react_to_empty_music_queue(
    raw_app: CustomQuart,
    auth: dict,
):
    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_liquidsoap:
        async with raw_app.test_app():
            client = raw_app.test_client()
            metadata = fake_md()
            metadata["next_autodj"] = {"rid": -1}
            result = await client.post("/live", headers=auth, json=metadata)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text
        mock_liquidsoap.assert_called_once()
        args, kwargs = mock_liquidsoap.call_args
        assert args == ("/queue/autodj",)
        assert "path" in kwargs["json"]
        assert "beets_id" in kwargs["json"]
        assert "rg_track_gain" in kwargs["json"]


async def test_pick_filter(
    raw_app: CustomQuart,
    dbsession: ormSession,
):
    slot = await AutoDJSlot.from_time(dbsession, 0)
    constraints = MultiDict()
    constraints.add("filters[][weight]", "1")
    constraints.add("filters[][filter]", "one")
    constraints.add("filters[][weight]", "2")
    constraints.add("filters[][filter]", "two")
    constraints.add("filters[][weight]", "3")
    constraints.add("filters[][filter]", "three")
    process_filters(constraints, slot)
    await dbsession.commit()
    counts = Counter(slot.pick_filter() for i in range(1000))
    assert (counts["one"] + counts["two"] + counts["three"]) == 1000

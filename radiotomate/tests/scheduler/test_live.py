import asyncio
import json
from collections import Counter
from unittest.mock import patch

import httpx
from quart.testing import QuartClient
from sqlalchemy.orm import Session as ormSession

from radiotomate.interface.autodj import MultiDict, process_filters
from radiotomate.models import AutoDJSlot, Cart
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
            "time": "2026-09-14T00:30:00+02:00",
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
        "remaining": "0",
        "elapsed": "3.1415",
        "time": "2026-09-14T00:30:00+02:00",
    }


async def test_react_to_empty_jingle_queue(
    raw_app: CustomQuart,
    dbsession: ormSession,
    jingles_cart: Cart,
    auth: dict,
):
    "normal case: clock motif pushes Jingles"
    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_liquidsoap:
        async with raw_app.test_app():
            client = raw_app.test_client()
            metadata = fake_md()
            metadata["next_jingle"] = {"rid": -1}
            metadata["next_autodj"] = {"rid": -1}
            result = await client.post("/live", headers=auth, json=metadata)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text
            await asyncio.sleep(0.05)
        queues = [c.args[0] for c in mock_liquidsoap.call_args_list]
        assert "/queue/jingles" in queues
        jingle = next(
            c for c in mock_liquidsoap.call_args_list if c.args[0] == "/queue/jingles"
        )
        assert jingle.kwargs["json"]["artist"] == "Jingles"


async def test_react_to_empty_jingle_queue_robust(
    raw_app: CustomQuart,
    dbsession: ormSession,
    jingles_cart: Cart,
    auth: dict,
):
    "No jingle is enabled: should not crash"
    from sqlalchemy import select as sel

    from radiotomate.models.sound import Sound as SoundModel

    sound = await dbsession.scalar(
        sel(SoundModel).filter(SoundModel.cart_id == jingles_cart.id)
    )
    sound.active = False
    await dbsession.commit()

    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_liquidsoap:
        async with raw_app.test_app():
            client = raw_app.test_client()
            metadata = fake_md()
            metadata["next_jingle"] = {"rid": -1}
            metadata["next_autodj"] = {"rid": 1}
            result = await client.post("/live", headers=auth, json=metadata)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text
            await asyncio.sleep(0.05)
        queues = [c.args[0] for c in mock_liquidsoap.call_args_list]
        assert "/queue/jingles" not in queues


async def test_react_to_empty_jingle_queue_but_analyzing(
    raw_app: CustomQuart,
    dbsession: ormSession,
    jingles_cart: Cart,
    auth: dict,
):
    """
    No jingle has been analyzed yet: should not crash.

    Be careful to unset gain after the application start. Otherwise the sound will be
    picked during background analyzer's start, as it tries to catch-up missed analysis.
    """
    from sqlalchemy import update

    from radiotomate.models.sound import Sound as SoundModel

    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_liquidsoap:
        async with raw_app.test_app():
            await asyncio.sleep(0.15)
            await dbsession.execute(
                update(SoundModel)
                .where(SoundModel.cart_id == jingles_cart.id)
                .values(gain=None)
            )
            await dbsession.commit()

            client = raw_app.test_client()
            metadata = fake_md()
            metadata["next_jingle"] = {"rid": -1}
            metadata["next_autodj"] = {"rid": 1}
            result = await client.post("/live", headers=auth, json=metadata)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text
            await asyncio.sleep(0.05)
        queues = [c.args[0] for c in mock_liquidsoap.call_args_list]
        assert "/queue/jingles" not in queues


async def test_react_to_empty_music_queue(
    raw_app: CustomQuart,
    jingles_cart: Cart,
    auth: dict,
):
    with patch(
        "httpx.AsyncClient.post",
        return_value=httpx.Response(200, json={"OK": 1}),
    ) as mock_liquidsoap:
        async with raw_app.test_app():
            client = raw_app.test_client()
            metadata = fake_md()
            metadata["next_jingle"] = {"rid": -1}
            metadata["next_autodj"] = {"rid": -1}
            result = await client.post("/live", headers=auth, json=metadata)
            result_text = (await result.data).decode()
            assert result.status_code == 200, "got non-OK response:" + result_text
            await asyncio.sleep(0.05)
        queues = [c.args[0] for c in mock_liquidsoap.call_args_list]
        assert "/queue/autodj" in queues
        autodj = next(
            c for c in mock_liquidsoap.call_args_list if c.args[0] == "/queue/autodj"
        )
        assert "path" in autodj.kwargs["json"]
        assert "beets_id" in autodj.kwargs["json"]
        assert "rg_track_gain" in autodj.kwargs["json"]


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

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


async def test_harbor_disconnect_flushes_jingles_and_autodj(raw_app: CustomQuart):
    from radiotomate.scheduler import live as sched_live
    from radiotomate.scheduler.playout import PlayoutResult

    class FakeGateway:
        def __init__(self):
            self.flushed: list[list[str] | None] = []
            self.skipped = 0

        async def flush_queues(self, queues=None):
            self.flushed.append(list(queues) if queues else None)
            return PlayoutResult(ok=True)

        async def skip(self, params=None):
            self.skipped += 1
            return PlayoutResult(ok=True)

    fake = FakeGateway()
    with patch("radiotomate.scheduler.playout.gateway_for", return_value=fake):
        async with raw_app.app_context():
            sched_live.reset_playout_source_memory()
            await sched_live._on_playout_source({"source": "stream"})
            await sched_live._on_playout_source({"source": "autodj"})
            sched_live.reset_playout_source_memory()
    assert fake.flushed == [["jingles", "autodj"]]
    assert fake.skipped == 1


async def _seed_next_rundown(dbsession: ormSession, spec: dict) -> None:
    from datetime import datetime, timedelta

    from radiotomate.enums import RundownStatus
    from radiotomate.models.execution import ProgrammingVersion, RundownItem

    dbsession.add(ProgrammingVersion(id=spec["vid"], source="test", description="peek"))
    await dbsession.flush()
    dbsession.add(
        RundownItem(
            id=spec["rid"],
            programming_version_id=spec["vid"],
            sequence=0,
            planned_at=datetime.now() + timedelta(minutes=2),
            duration=90,
            kind=spec["kind"],
            when_mode="sequential",
            queue=spec["queue"],
            resource=f"nowave — {spec['title']}",
            path=spec["path"],
            details={"title": spec["title"], "artist": "nowave"},
            status=RundownStatus.PLANNED.value,
            origin="clock",
        ),
    )
    await dbsession.commit()


async def test_overlay_rundown_next_when_ls_empty(dbsession: ormSession):
    from radiotomate.scheduler.clock import overlay_rundown_next

    await _seed_next_rundown(dbsession, {
        "vid": "pv-next-empty",
        "rid": "ri-next-empty",
        "kind": "jingle",
        "queue": "jingles",
        "title": "Gerbera",
        "path": "/data/carts/1Jingles/nowave-Gerbera.wav",
    })
    live = {
        "title": "nowave - Ancolie.wav",
        "artist": "Jingles",
        "next_jingle": {"rid": -1},
        "next_autodj": {"rid": -1},
        "next_cart": {"rid": -1},
    }
    await overlay_rundown_next(dbsession, live)
    assert live["next_jingle"]["title"] == "Gerbera"
    assert live["next_jingle"]["artist"] == "nowave"
    assert live["next_jingle"]["rid"] == 1
    assert live["next_autodj"]["rid"] == -1


async def test_overlay_rundown_next_replaces_on_air_duplicate(dbsession: ormSession):
    from radiotomate.scheduler.clock import overlay_rundown_next

    await _seed_next_rundown(dbsession, {
        "vid": "pv-next-dup",
        "rid": "ri-next-dup",
        "kind": "jingle",
        "queue": "jingles",
        "title": "Poinsetta",
        "path": "/data/carts/1Jingles/nowave-Poinsetta.wav",
    })
    live = {
        "title": "nowave - Ancolie.wav",
        "artist": "Jingles",
        "next_jingle": {
            "title": "nowave - Ancolie.wav",
            "artist": "Jingles",
            "rid": 6,
        },
        "next_autodj": {"rid": -1},
    }
    await overlay_rundown_next(dbsession, live)
    assert live["next_jingle"]["title"] == "Poinsetta"


async def test_overlay_rundown_next_keeps_real_ls_cue(dbsession: ormSession):
    from radiotomate.scheduler.clock import overlay_rundown_next

    await _seed_next_rundown(dbsession, {
        "vid": "pv-next-keep",
        "rid": "ri-next-keep",
        "kind": "musique",
        "queue": "autodj",
        "title": "Peeked",
        "path": "/media/peeked.mp3",
    })
    live = {
        "title": "Now",
        "artist": "A",
        "next_autodj": {"title": "Suite", "artist": "B", "rid": 3},
        "next_jingle": {"rid": -1},
    }
    await overlay_rundown_next(dbsession, live)
    assert live["next_autodj"]["title"] == "Suite"
    assert live["next_autodj"]["rid"] == 3

from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session as ormSession

from radiotomate.beets import BeetsIntegration
from radiotomate.models import AutoDJSlot, Clock, ClockPosition, MusicCategory
from radiotomate.models.cart import Cart
from radiotomate.scheduler.clock import (
    PARIS,
    anchor_in_daypart,
    next_hard_anchor_minute,
    sequential_kind_cycle,
    tick,
    track_would_overflow_anchor,
)

NIGHT = "2026-09-14T00:30:00+02:00"
JOURNEE_20 = "2026-09-14T10:20:05+02:00"
EVENING_20 = "2026-09-14T18:20:05+02:00"
SHORT_20 = "2026-09-14T10:20:05+02:00"


def _client() -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=httpx.Response(200, json={"ok": 1}))
    client.delete = AsyncMock(return_value=httpx.Response(200))
    return client


def _live(time: str, **queues) -> dict:
    data = {
        "source": "starting",
        "remaining": "0",
        "time": time,
        "next_jingle": {"rid": -1},
        "next_autodj": {"rid": -1},
        "next_cart": {"rid": -1},
    }
    data.update(queues)
    return data


def test_anchor_in_daypart():
    assert anchor_in_daypart(600, 1080, 10, 20) is True
    assert anchor_in_daypart(600, 615, 10, 20) is False
    assert anchor_in_daypart(600, 1080, 18, 20) is False
    assert anchor_in_daypart(1080, 1440, 18, 20) is True


def test_track_would_overflow_anchor():
    now = datetime(2026, 9, 14, 10, 10, tzinfo=PARIS)
    assert track_would_overflow_anchor(now, 0, 120, 10 * 60 + 20) is False
    assert track_would_overflow_anchor(now, 0, 700, 10 * 60 + 20) is True


def test_next_hard_anchor_minute():
    now = datetime(2026, 9, 14, 10, 19, tzinfo=PARIS)
    pos20 = ClockPosition(minute=20, sync="dure", kind="pub", when_mode="anchored")
    pos40 = ClockPosition(minute=40, sync="dure", kind="pub", when_mode="anchored")
    assert next_hard_anchor_minute(now, 600, 1080, [pos20, pos40]) == 10 * 60 + 20


async def test_seed_packet_a(dbsession: ormSession):
    n_clocks = await dbsession.scalar(select(func.count()).select_from(Clock))
    n_slots = await dbsession.scalar(select(func.count()).select_from(AutoDJSlot))
    n_cats = await dbsession.scalar(select(func.count()).select_from(MusicCategory))
    assert n_clocks == 2
    assert n_cats == 1
    assert n_slots == 21
    clock_row = await dbsession.scalar(
        select(Clock).filter(Clock.name == "24/24 Rotation habillée")
    )
    clock = await Clock.from_id(dbsession, clock_row.id)
    assert sequential_kind_cycle(clock) == ["jingle", "musique", "musique", "musique"]
    journee_row = await dbsession.scalar(
        select(Clock).filter(Clock.name == "Journée pubs")
    )
    journee = await Clock.from_id(dbsession, journee_row.id)
    assert [p.minute for p in journee.anchored_positions()] == [20, 40]


async def test_motif_jingle_then_three_music(
    dbsession: ormSession,
    jingles_ntr_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    actions = await tick(dbsession, _live(NIGHT), client, beets_integration)
    assert actions == ["jingle", "autodj"]
    queues = [c.args[0] for c in client.post.call_args_list]
    assert queues == ["/queue/jingles", "/queue/autodj"]
    assert client.post.call_args_list[0].kwargs["json"]["artist"] == "Jingles NTR"

    filled_jingle = _live(NIGHT, next_jingle={"rid": 1}, next_autodj={"rid": -1})
    assert await tick(dbsession, filled_jingle, client, beets_integration) == ["autodj"]
    assert await tick(dbsession, filled_jingle, client, beets_integration) == ["autodj"]
    both_empty = _live(NIGHT)
    assert (await tick(dbsession, both_empty, client, beets_integration))[0] == "jingle"


async def test_pub_at_1020_in_daypart(
    dbsession: ormSession,
    jingles_ntr_cart: Cart,
    pubs_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    live = _live(JOURNEE_20, next_jingle={"rid": 1}, next_autodj={"rid": 1})
    actions = await tick(dbsession, live, client, beets_integration)
    assert "anchor:20" in actions
    assert "skip" in actions
    payload = client.post.call_args.kwargs["json"]
    assert client.post.call_args.args[0] == "/queue/carts"
    assert payload["artist"] == "Pubs"
    assert payload["title"] == "Spot test"
    assert payload["path"].endswith("spot.mp3")


async def test_no_pub_when_daypart_too_short(
    dbsession: ormSession,
    jingles_ntr_cart: Cart,
    pubs_cart: Cart,
    beets_integration: BeetsIntegration,
):
    clock = await dbsession.scalar(
        select(Clock).filter(Clock.name == "24/24 Rotation habillée")
    )
    dbsession.add(
        AutoDJSlot(
            day_of_week=0,
            minute=10 * 60 + 15,
            clock_id=clock.id,
            title="court",
            constraints={},
        )
    )
    await dbsession.commit()
    client = _client()
    actions = await tick(
        dbsession,
        _live(SHORT_20, next_jingle={"rid": 1}, next_autodj={"rid": 1}),
        client,
        beets_integration,
    )
    assert not any(a.startswith("anchor:") for a in actions)
    client.post.assert_not_called()


async def test_no_pub_at_1820(
    dbsession: ormSession,
    jingles_ntr_cart: Cart,
    pubs_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    actions = await tick(
        dbsession,
        _live(EVENING_20, next_jingle={"rid": 1}, next_autodj={"rid": 1}),
        client,
        beets_integration,
    )
    assert not any(a.startswith("anchor:") for a in actions)
    client.post.assert_not_called()


async def test_empty_pubs_uses_jingle_fallback(
    dbsession: ormSession,
    jingles_ntr_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    actions = await tick(
        dbsession,
        _live(JOURNEE_20, next_jingle={"rid": 1}, next_autodj={"rid": 1}),
        client,
        beets_integration,
    )
    assert "anchor:20" in actions
    payload = client.post.call_args.kwargs["json"]
    assert payload["artist"] == "Jingles NTR"


async def test_no_clock_id_does_not_fill(
    dbsession: ormSession,
    jingles_ntr_cart: Cart,
    beets_integration: BeetsIntegration,
):
    slot = await AutoDJSlot.from_time(dbsession, 30, 0)
    assert slot is not None
    slot.clock_id = None
    await dbsession.commit()
    client = _client()
    with patch.object(beets_integration, "random_pick", new_callable=AsyncMock) as rp:
        actions = await tick(dbsession, _live(NIGHT), client, beets_integration)
        rp.assert_not_called()
    assert actions == []
    client.post.assert_not_called()


async def test_night_uses_clock_not_random_pick(
    dbsession: ormSession,
    jingles_ntr_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    with patch.object(beets_integration, "random_pick", new_callable=AsyncMock) as rp:
        actions = await tick(dbsession, _live(NIGHT), client, beets_integration)
        rp.assert_not_called()
    assert actions == ["jingle", "autodj"]
    assert not any(a.startswith("anchor:") for a in actions)
    queues = [c.args[0] for c in client.post.call_args_list]
    assert queues == ["/queue/jingles", "/queue/autodj"]
    assert "beets_id" in client.post.call_args_list[1].kwargs["json"]

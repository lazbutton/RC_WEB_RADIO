from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session as ormSession

from radiotomate.beets import BeetsIntegration
from radiotomate.enums import CartMode, ScheduleMode
from radiotomate.models import AutoDJSlot, Clock, ClockPosition, MusicCategory
from radiotomate.models.cart import Cart
from radiotomate.models.sound import Sound
from radiotomate.scheduler.clock import (
    PARIS,
    anchor_in_daypart,
    next_hard_anchor_minute,
    note_carts_push,
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
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    actions = await tick(dbsession, _live(NIGHT), client, beets_integration)
    assert actions[0] == "jingle"
    assert actions.count("autodj") >= 2
    queues = [c.args[0] for c in client.post.call_args_list]
    assert queues[0] == "/queue/jingles"
    assert queues.count("/queue/autodj") >= 2
    assert client.post.call_args_list[0].kwargs["json"]["artist"] == "Jingles"

    filled = _live(
        NIGHT,
        remaining="180",
        next_jingle={"rid": 1},
        next_autodj={"rid": 1},
        jingles_queued=2,
        autodj_queued=1,
    )
    follow = _client()
    assert await tick(dbsession, filled, follow, beets_integration) == ["autodj"]


async def test_autodj_fills_to_depth_two(
    dbsession: ormSession,
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    live = _live(
        NIGHT,
        remaining="180",
        next_jingle={"rid": 1},
        next_autodj={"rid": -1},
        jingles_queued=2,
        autodj_queued=0,
    )
    actions = await tick(dbsession, live, client, beets_integration)
    assert actions == ["autodj", "autodj"]
    already = _live(
        NIGHT,
        remaining="180",
        next_jingle={"rid": 1},
        next_autodj={"rid": 1},
        jingles_queued=2,
        autodj_queued=2,
    )
    client2 = _client()
    assert await tick(dbsession, already, client2, beets_integration) == []
    client2.post.assert_not_called()


async def test_does_not_stack_jingles_while_autodj_is_queued(
    dbsession: ormSession,
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    live = _live(
        NIGHT,
        source="autodj",
        remaining="180",
        next_jingle={"rid": 1},
        next_autodj={"rid": 1},
        jingles_queued=1,
        autodj_queued=2,
    )
    assert await tick(dbsession, live, client, beets_integration) == []
    client.post.assert_not_called()


async def test_replay_metadata_source_does_not_refill_jingles(
    dbsession: ormSession,
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    actions = await tick(
        dbsession,
        _live(
            NIGHT,
            source="replay_metadata",
            remaining="180",
            next_jingle={"rid": -1},
            next_autodj={"rid": 1},
            jingles_queued=0,
            autodj_queued=2,
        ),
        client,
        beets_integration,
    )
    assert "jingle" not in actions
    client.post.assert_not_called()


async def test_insert_initial_source_does_not_refill_jingles(
    dbsession: ormSession,
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    actions = await tick(
        dbsession,
        _live(
            NIGHT,
            source="insert_initial_track_mark.6",
            remaining="180",
            next_jingle={"rid": -1},
            next_autodj={"rid": 1},
            jingles_queued=0,
            autodj_queued=2,
        ),
        client,
        beets_integration,
    )
    assert "jingle" not in actions
    client.post.assert_not_called()


async def test_does_not_chain_jingles_when_current_jingle_ends(
    dbsession: ormSession,
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    actions = await tick(
        dbsession,
        _live(
            NIGHT,
            source="jingles",
            remaining="0.2",
            next_jingle={"rid": -1},
            next_autodj={"rid": 1},
            jingles_queued=0,
            autodj_queued=2,
        ),
        client,
        beets_integration,
    )
    assert "jingle" not in actions
    assert all(not str(c.args[0]).endswith("/jingles") for c in client.post.call_args_list)


async def test_autodj_fills_while_cart_is_on_air(
    dbsession: ormSession,
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    actions = await tick(
        dbsession,
        _live(
            NIGHT,
            source="carts",
            remaining="180",
            next_jingle={"rid": -1},
            next_autodj={"rid": -1},
            next_cart={"rid": 1},
            jingles_queued=0,
            autodj_queued=0,
            carts_queued=1,
        ),
        client,
        beets_integration,
    )
    assert "jingle" not in actions
    assert actions.count("autodj") >= 2


async def test_pub_at_1020_in_daypart(
    dbsession: ormSession,
    jingles_cart: Cart,
    pubs_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    live = _live(
        JOURNEE_20,
        remaining="180",
        next_jingle={"rid": 1},
        next_autodj={"rid": 1},
        jingles_queued=2,
        autodj_queued=3,
    )
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
    jingles_cart: Cart,
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
        _live(
            SHORT_20,
            remaining="180",
            next_jingle={"rid": 1},
            next_autodj={"rid": 1},
            jingles_queued=2,
            autodj_queued=3,
        ),
        client,
        beets_integration,
    )
    assert not any(a.startswith("anchor:") for a in actions)
    client.post.assert_not_called()


async def test_no_pub_at_1820(
    dbsession: ormSession,
    jingles_cart: Cart,
    pubs_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    actions = await tick(
        dbsession,
        _live(
            EVENING_20,
            remaining="180",
            next_jingle={"rid": 1},
            next_autodj={"rid": 1},
            jingles_queued=2,
            autodj_queued=3,
        ),
        client,
        beets_integration,
    )
    assert not any(a.startswith("anchor:") for a in actions)
    client.post.assert_not_called()


async def test_empty_pubs_uses_jingle_fallback(
    dbsession: ormSession,
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    actions = await tick(
        dbsession,
        _live(
            JOURNEE_20,
            remaining="180",
            next_jingle={"rid": 1},
            next_autodj={"rid": 1},
            jingles_queued=2,
            autodj_queued=3,
        ),
        client,
        beets_integration,
    )
    assert "anchor:20" in actions
    payload = client.post.call_args.kwargs["json"]
    assert payload["artist"] == "Jingles"


async def test_no_clock_id_does_not_fill(
    dbsession: ormSession,
    jingles_cart: Cart,
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
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    client = _client()
    with patch.object(beets_integration, "random_pick", new_callable=AsyncMock) as rp:
        actions = await tick(dbsession, _live(NIGHT), client, beets_integration)
        rp.assert_not_called()
    assert actions[0] == "jingle"
    assert "autodj" in actions
    queues = [c.args[0] for c in client.post.call_args_list]
    assert queues[0] == "/queue/jingles"
    assert "/queue/autodj" in queues
    autodj_payload = next(
        call.kwargs["json"]
        for call in client.post.call_args_list
        if call.args[0] == "/queue/autodj"
    )
    assert "beets_id" in autodj_payload
    assert autodj_payload.get("artist")
    assert autodj_payload.get("title")
    assert not str(autodj_payload["title"]).lower().endswith(".mp3")


async def test_tick_skips_unchanged_cursor_persist(
    dbsession: ormSession,
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    live = _live(
        NIGHT,
        remaining="180",
        next_jingle={"rid": 1},
        next_autodj={"rid": 1},
        jingles_queued=2,
        autodj_queued=3,
    )
    await tick(dbsession, live, _client(), beets_integration)
    with patch(
        "radiotomate.scheduler.clock.Setting.upsert",
        new_callable=AsyncMock,
    ) as upsert:
        assert await tick(dbsession, live, _client(), beets_integration) == []
        upsert.assert_not_called()


async def test_active_cart_playlist_refills_on_tick(
    dbsession: ormSession,
    raw_app,
    jingles_cart: Cart,
    beets_integration: BeetsIntegration,
):
    cartpath = raw_app.config["DATA_ROOT"] / "gender-minorities"
    cart = Cart(
        title="Prog Gender Minorities",
        path=cartpath,
        mode=CartMode.PLAYLIST_LOOP,
        schedule_mode=ScheduleMode.TIMED,
    )
    dbsession.add(cart)
    await dbsession.flush()
    for rank, title in enumerate(("Un", "Deux", "Trois"), start=1):
        dbsession.add(
            Sound(
                cart_id=cart.id,
                path=cartpath / f"{rank}.mp3",
                duration=10,
                title=title,
                rank=rank,
                gain=-1.0,
                peak=-0.5,
            )
        )
    await dbsession.commit()
    note_carts_push(cart.id)
    client = _client()
    actions = await tick(
        dbsession,
        _live(
            NIGHT,
            source="carts",
            remaining="180",
            next_jingle={"rid": 1},
            next_autodj={"rid": 1},
            next_cart={"rid": -1},
            jingles_queued=2,
            autodj_queued=3,
            carts_queued=0,
        ),
        client,
        beets_integration,
    )
    assert actions.count("cart_chain") == 2
    carts_posts = [
        call for call in client.post.call_args_list if call.args[0] == "/queue/carts"
    ]
    assert len(carts_posts) == 2
    titles = [call.kwargs["json"]["title"] for call in carts_posts]
    assert titles == ["Un", "Deux"]

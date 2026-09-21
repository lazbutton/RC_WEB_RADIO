from datetime import datetime
from unittest.mock import AsyncMock

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as ormSession

from radiotomate.beets import BeetsIntegration
from radiotomate.domain.execution import item_status_code, rundown_summary
from radiotomate.enums import CommandStatus
from radiotomate.models import (
    Clock,
    MetadataLog,
    PlayoutCommand,
    RundownItem,
    Setting,
)
from radiotomate.scheduler.clock import PARIS, reset_state, tick
from radiotomate.scheduler.execution import (
    SETTING_FIRED_ANCHORS,
    ensure_forecast,
    materialize_rundown,
    published_rundown,
    reset_conducteur,
)
from radiotomate.scheduler.outbox import dispatch_command, enqueue_command
from radiotomate.scheduler.playout import PlayoutGateway
from radiotomate.services.carts import sync_cart_display_titles
from tests.scheduler.test_clock import JOURNEE_20, NIGHT, _client, _live


def test_rundown_summary_counts_upcoming_rescue_and_skip():
    summary = rundown_summary(
        [
            {"kind": "musique", "status_code": "played", "when": "sequential"},
            {"kind": "jingle", "status_code": "skipped"},
            {"kind": "musique", "status_code": "planned", "when": "sequential"},
            {
                "kind": "pub",
                "status_code": "planned",
                "when": "anchored",
                "minute": 20,
                "at": "2026-09-16T10:20:00",
                "resource": "Pub locale",
                "sync": "dure",
            },
            {"kind": "son", "fallback_used": True, "status_code": "rescue"},
        ]
    )
    assert summary["counts"]["items"] == 3
    assert summary["counts"]["musique"] == 1
    assert summary["counts"]["jingle"] == 0
    assert summary["counts"]["pub"] == 1
    assert summary["counts"]["son"] == 1
    assert summary["counts"]["secours"] == 1
    assert summary["counts"]["sauté"] == 1
    assert summary["next_anchor"]["minute"] == 20
    assert summary["next_anchor"]["resource"] == "Pub locale"


def test_item_status_code_maps_manquant():
    assert item_status_code({"status": "manquant"}) == "failed"


async def test_clock_accepts_cart_id(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
):
    from radiotomate.services.autodj import create_clock

    clock = await create_clock(
        dbsession,
        {
            "name": "IDs stables",
            "fallback_cart_id": jingles_cart.id,
            "motif": [
                {
                    "kind": "jingle",
                    "cart_id": jingles_cart.id,
                }
            ],
            "anchors": [
                {
                    "kind": "pub",
                    "cart_id": pubs_cart.id,
                    "fallback_cart_id": jingles_cart.id,
                    "minute": 15,
                    "sync": "molle",
                }
            ],
        },
    )
    await dbsession.commit()
    loaded = await Clock.from_id(dbsession, clock.id)
    assert loaded.fallback_cart_id == jingles_cart.id
    assert loaded.fallback_cart_title == "Jingles"
    assert loaded.positions[0].cart_id == jingles_cart.id
    assert loaded.anchored_positions()[0].cart_id == pubs_cart.id
    assert loaded.anchored_positions()[0].is_soft_sync is True


async def test_rename_cart_updates_clock_titles(
    dbsession: ormSession,
    jingles_cart,
):
    clock = await Clock.from_name(dbsession, "24/24 Rotation habillée")
    clock = await Clock.from_id(dbsession, clock.id)
    clock.fallback_cart_id = jingles_cart.id
    clock.fallback_cart_title = jingles_cart.title
    for pos in clock.positions:
        if pos.cart_title == jingles_cart.title or pos.kind == "jingle":
            pos.cart_id = jingles_cart.id
            pos.cart_title = jingles_cart.title
    await dbsession.commit()
    jingles_cart.title = "Jingles renommés"
    await sync_cart_display_titles(dbsession, jingles_cart)
    await dbsession.commit()
    clock = await Clock.from_id(dbsession, clock.id)
    assert clock.fallback_cart_title == "Jingles renommés"
    jingle = next(pos for pos in clock.positions if pos.cart_id == jingles_cart.id)
    assert jingle.cart_title == "Jingles renommés"


async def test_materialize_rundown_persists_items(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
    beets_integration: BeetsIntegration,
):
    data = await materialize_rundown(
        dbsession,
        beets_integration,
        now=datetime(2026, 9, 15, 10, 19, tzinfo=PARIS),
        cursor=0,
    )
    assert data["programming_version"]
    assert data["items"]
    assert data["items"][0]["id"]
    assert data["items"][0]["status_code"] in {"planned", "rescue", "skipped"}
    rows = list(await dbsession.scalars(select(RundownItem)))
    assert len(rows) == len(data["items"])


async def test_published_rundown_is_stable_source(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
    beets_integration: BeetsIntegration,
):
    first = await published_rundown(
        dbsession,
        beets_integration,
        now=datetime(2026, 9, 15, 10, 19, tzinfo=PARIS),
    )
    second = await published_rundown(
        dbsession,
        beets_integration,
        now=datetime(2026, 9, 15, 10, 19, tzinfo=PARIS),
    )
    assert first["programming_version"] == second["programming_version"]
    assert [item["id"] for item in first["items"]] == [
        item["id"] for item in second["items"]
    ]
    assert {item["kind"] for item in first["items"]} == {
        item["kind"] for item in second["items"]
    }


async def test_tick_records_commands_and_survives_restart(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
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
    commands = list(await dbsession.scalars(select(PlayoutCommand)))
    assert commands
    assert commands[0].status == CommandStatus.ACKNOWLEDGED.value
    items = list(await dbsession.scalars(select(RundownItem)))
    assert items
    assert any(item.cart_id == pubs_cart.id for item in items)

    reset_state()
    client2 = _client()
    again = await tick(dbsession, live, client2, beets_integration)
    assert "anchor:20" not in again
    client2.post.assert_not_called()
    stored = await Setting.from_key(dbsession, SETTING_FIRED_ANCHORS)
    assert stored is not None
    assert "20" in stored.value


async def test_outbox_does_not_resend_acknowledged(
    dbsession: ormSession,
    jingles_cart,
):
    from radiotomate.scheduler.execution import record_live_item

    item = await record_live_item(
        dbsession,
        payload={
            "at": datetime.now().isoformat(),
            "kind": "jingle",
            "when": "sequential",
            "queue": "jingles",
            "resource": "ID BUTTON",
            "path": "/tmp/id.mp3",
            "duration": 8,
        },
    )
    payload = {"path": "/tmp/id.mp3", "radiotomate_sound_id": 1}
    first = await enqueue_command(
        dbsession,
        action="queue",
        queue="jingles",
        payload=payload,
        item=item,
    )
    second = await enqueue_command(
        dbsession,
        action="queue",
        queue="jingles",
        payload=payload,
        item=item,
    )
    assert first.id == second.id
    client = _client()
    gateway = PlayoutGateway(client, max_attempts=1)
    result = await dispatch_command(dbsession, first, gateway)
    assert result.ok
    client.post.assert_called_once()
    again = await dispatch_command(dbsession, first, gateway)
    assert again.ok
    assert client.post.call_count == 1


async def test_playout_gateway_retries_transient():
    client = AsyncMock()
    client.post = AsyncMock(
        side_effect=[
            httpx.ConnectError("down"),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    gateway = PlayoutGateway(client, max_attempts=3, backoff=(0, 0))
    result = await gateway.queue("autodj", {"path": "/tmp/a.mp3"})
    assert result.ok
    assert result.attempts == 2
    assert client.post.call_count == 2


async def test_soft_sync_waits_then_fires_without_skip(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
    beets_integration: BeetsIntegration,
):
    clock = await Clock.from_name(dbsession, "Journée pubs")
    clock = await Clock.from_id(dbsession, clock.id)
    for pos in clock.anchored_positions():
        if pos.minute == 20:
            pos.sync = "molle"
    await dbsession.commit()

    client = _client()
    waiting = await tick(
        dbsession,
        _live(
            JOURNEE_20,
            source="autodj",
            remaining="12",
            next_jingle={"rid": 1},
            next_autodj={"rid": 1},
            jingles_queued=2,
            autodj_queued=3,
        ),
        client,
        beets_integration,
    )
    assert not any(item.startswith("soft:") for item in waiting)
    client.post.assert_not_called()

    ready = await tick(
        dbsession,
        _live(
            "2026-09-14T10:21:00+02:00",
            source="autodj",
            remaining="1",
            next_jingle={"rid": 1},
            next_autodj={"rid": 1},
            jingles_queued=2,
            autodj_queued=3,
        ),
        client,
        beets_integration,
    )
    assert "soft:20" in ready
    assert "skip" not in ready
    assert client.post.call_args.args[0] == "/queue/carts"


async def test_soft_anchor_glides_in_rundown(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
    beets_integration: BeetsIntegration,
):
    from radiotomate.scheduler.rundown import build_rundown

    clock = await Clock.from_name(dbsession, "Journée pubs")
    clock = await Clock.from_id(dbsession, clock.id)
    for pos in clock.anchored_positions():
        if pos.minute == 20:
            pos.sync = "molle"
    await dbsession.commit()
    data = await build_rundown(
        dbsession,
        beets_integration,
        now=datetime(2026, 9, 14, 10, 10, tzinfo=PARIS),
        cursor=0,
    )
    soft = [
        item
        for item in data["items"]
        if item.get("when") == "anchored" and item.get("minute") == 20
    ]
    assert soft
    assert soft[0].get("reason") == "glissement"


async def test_as_run_links_rundown_item(
    dbsession: ormSession,
    jingles_cart,
    beets_integration: BeetsIntegration,
):
    from radiotomate.scheduler.execution import reconcile_as_run

    await tick(dbsession, _live(NIGHT), _client(), beets_integration)
    item = await dbsession.scalar(select(RundownItem).limit(1))
    assert item is not None
    log = MetadataLog(
        on_air=datetime.now(),
        source="autodj",
        title=item.resource.split(" — ")[-1],
        artist="",
        album="",
        extra={"radiotomate_item_id": item.id},
    )
    log.rundown_item_id = item.id
    dbsession.add(log)
    await reconcile_as_run(dbsession, log)
    await dbsession.commit()
    refreshed = await RundownItem.from_id(dbsession, item.id)
    assert refreshed.status == "on_air"
    assert log.rundown_item_id == item.id


async def test_ensure_forecast_keeps_ids_across_calls(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
    beets_integration: BeetsIntegration,
):
    now = datetime(2026, 9, 14, 0, 30, tzinfo=PARIS)
    first = await ensure_forecast(
        dbsession,
        beets_integration,
        now=now,
        cursor=0,
    )
    second = await ensure_forecast(
        dbsession,
        beets_integration,
        now=now,
        cursor=0,
    )
    assert first["items"]
    assert [item["id"] for item in first["items"]] == [
        item["id"] for item in second["items"]
    ]
    assert [item["path"] for item in first["items"]] == [
        item["path"] for item in second["items"]
    ]


async def test_tick_pushes_persisted_rundown_path(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
    beets_integration: BeetsIntegration,
):
    now = datetime(2026, 9, 14, 0, 30, tzinfo=PARIS)
    data = await ensure_forecast(
        dbsession,
        beets_integration,
        now=now,
        cursor=0,
    )
    music = next(
        item
        for item in data["items"]
        if item.get("kind") == "musique" and item.get("path")
    )
    client = _client()
    await tick(
        dbsession,
        _live(
            NIGHT,
            remaining="180",
            next_jingle={"rid": 1},
            next_autodj={"rid": -1},
            jingles_queued=2,
            autodj_queued=0,
        ),
        client,
        beets_integration,
    )
    autodj_paths = [
        call.kwargs["json"]["path"]
        for call in client.post.call_args_list
        if call.args[0] == "/queue/autodj"
    ]
    assert autodj_paths
    assert autodj_paths[0] == music["path"]


async def test_reset_conducteur_rebuilds_ids(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
    beets_integration: BeetsIntegration,
):
    now = datetime(2026, 9, 14, 0, 30, tzinfo=PARIS)
    first = await ensure_forecast(
        dbsession,
        beets_integration,
        now=now,
        cursor=0,
    )
    first_ids = {item["id"] for item in first["items"]}
    payload = await reset_conducteur(dbsession, beets_integration)
    second_ids = {item["id"] for item in payload["items"]}
    assert first_ids
    assert payload["action"] == "reset"
    assert first_ids.isdisjoint(second_ids)


async def test_as_run_realigns_following_items(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
    beets_integration: BeetsIntegration,
):
    """The item going on air takes the real start; later sequential items slide."""
    from datetime import timedelta

    from radiotomate.scheduler.execution import reconcile_as_run

    now = datetime(2026, 9, 14, 0, 30, tzinfo=PARIS)
    payload = await ensure_forecast(dbsession, beets_integration, now=now, cursor=0)
    ids = [item["id"] for item in payload["items"] if item.get("origin") != "desk"]
    assert len(ids) >= 3
    first = await RundownItem.from_id(dbsession, ids[0])
    second = await RundownItem.from_id(dbsession, ids[1])
    before_first = first.planned_at
    before_second = second.planned_at

    late = before_first + timedelta(minutes=7)  # antenna is 7 min behind the plan
    log = MetadataLog(
        on_air=late,
        source="autodj",
        title=first.resource.split(" — ")[-1],
        artist="",
        album="",
        extra={"radiotomate_item_id": first.id},
    )
    dbsession.add(log)
    await reconcile_as_run(dbsession, log)
    await dbsession.commit()

    first = await RundownItem.from_id(dbsession, ids[0])
    second = await RundownItem.from_id(dbsession, ids[1])
    assert first.status == "on_air"
    assert first.planned_at == late
    assert second.planned_at == before_second + timedelta(minutes=7)


async def test_ensure_forecast_ignores_failed_coverage(
    dbsession: ormSession,
    jingles_cart,
    pubs_cart,
    beets_integration: BeetsIntegration,
):
    """A window full of failed pushes must be re-forecast, not treated as covered."""
    from sqlalchemy import update

    now = datetime(2026, 9, 14, 0, 30, tzinfo=PARIS)
    await ensure_forecast(dbsession, beets_integration, now=now, cursor=0)
    await dbsession.execute(update(RundownItem).values(status="failed"))
    await dbsession.commit()
    payload = await ensure_forecast(dbsession, beets_integration, now=now, cursor=0)
    playable = [i for i in payload["items"] if i.get("status_code") not in {"failed"}]
    assert playable, payload["items"][:2]
    fresh = await dbsession.scalar(
        select(RundownItem).where(RundownItem.status == "planned").limit(1)
    )
    assert fresh is not None

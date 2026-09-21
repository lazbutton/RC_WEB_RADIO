from unittest.mock import AsyncMock, patch

import httpx

from radiotomate.quart import CustomQuart
from radiotomate.scheduler.metrics import runtime_metrics
from radiotomate.scheduler.playout import PlayoutGateway
from radiotomate.scheduler.queue_cleaner import clamp_interval, clamp_max_age, run_clean


def test_queue_cleaner_clamps_bounds():
    assert clamp_max_age(1) == 30
    assert clamp_max_age(99999) == 7200
    assert clamp_max_age("nope") == 600
    assert clamp_interval(1) == 15
    assert clamp_interval(99999) == 3600


async def test_run_clean_records_removed_counts(raw_app: CustomQuart):
    payload = {
        "autodj": {"removed": 2, "kept": 1},
        "jingles": {"removed": 0, "kept": 3},
        "carts": {"removed": 1, "kept": 0},
    }
    response = httpx.Response(200, json=payload)
    with patch.object(
        PlayoutGateway,
        "_call",
        new=AsyncMock(return_value=response),
    ):
        async with raw_app.test_app():
            result = await run_clean(raw_app)
    assert result["ok"] is True
    snapshot = runtime_metrics.snapshot()["queue_clean_removed_total"]
    assert snapshot["autodj"] == 2
    assert snapshot["jingles"] == 0
    assert snapshot["carts"] == 1


async def test_run_clean_playout_error(raw_app: CustomQuart):
    with patch.object(
        PlayoutGateway,
        "_call",
        new=AsyncMock(side_effect=httpx.ConnectError("down")),
    ):
        async with raw_app.test_app():
            result = await run_clean(raw_app)
    assert result["ok"] is False
    assert result["error"] == "playout_unavailable"


async def test_flush_queues_records_removed_counts(raw_app: CustomQuart):
    payload = {
        "autodj": {"removed": 3, "kept": 1},
        "jingles": {"removed": 12, "kept": 0},
        "carts": {"removed": 0, "kept": 1},
    }
    response = httpx.Response(200, json=payload)
    with patch.object(
        PlayoutGateway,
        "_call",
        new=AsyncMock(return_value=response),
    ) as mocked:
        async with raw_app.test_app():
            gateway = PlayoutGateway(raw_app.config["PLAYOUT_CLIENT"])
            result = await gateway.flush_queues()
    assert result.ok is True
    mocked.assert_awaited()
    assert mocked.await_args.args[0] == "POST"
    assert mocked.await_args.args[1] == "/queue/flush"
    snapshot = runtime_metrics.snapshot()["queue_clean_removed_total"]
    assert snapshot["autodj"] == 3
    assert snapshot["jingles"] == 12


async def test_flush_queues_targets_named_queues(raw_app: CustomQuart):
    response = httpx.Response(
        200,
        json={
            "jingles": {"removed": 4, "kept": 0},
            "autodj": {"removed": 1, "kept": 0},
        },
    )
    with patch.object(
        PlayoutGateway,
        "_call",
        new=AsyncMock(return_value=response),
    ) as mocked:
        async with raw_app.test_app():
            gateway = PlayoutGateway(raw_app.config["PLAYOUT_CLIENT"])
            result = await gateway.flush_queues(["jingles", "autodj"])
    assert result.ok is True
    assert mocked.await_args.args[0] == "POST"
    assert mocked.await_args.args[1] == "/queue/flush"
    assert mocked.await_args.kwargs["json"] == {"queues": ["jingles", "autodj"]}

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

from unittest.mock import patch

import httpx
from quart.testing import QuartClient

from radiotomate.interface_app import app_factory as interface_factory
from radiotomate.quart import CustomQuart
from radiotomate.scheduler.metrics import runtime_metrics
from radiotomate.scheduler.watchdog import Watchdog


async def test_scheduler_liveness(client: QuartClient):
    result = await client.get("/health/live")
    assert result.status_code == 200
    payload = await result.get_json()
    assert payload["status"] == "live"


async def test_scheduler_ready_degraded_without_heartbeat(client: QuartClient):
    result = await client.get("/health/ready")
    assert result.status_code == 503
    payload = await result.get_json()
    assert payload["ready"] is False
    assert payload["db"] is True
    assert payload["heartbeat_ok"] is False
    assert "token" not in str(payload).lower()
    assert "cookie" not in str(payload).lower()


async def test_scheduler_ready_when_playout_and_heartbeat_ok(client: QuartClient):
    runtime_metrics.heartbeat()
    runtime_metrics.playout_connected = True
    result = await client.get("/health/ready")
    assert result.status_code == 200
    payload = await result.get_json()
    assert payload["ready"] is True
    assert payload["playout"] is True
    assert payload["heartbeat_ok"] is True


async def test_scheduler_ready_stale_heartbeat(client: QuartClient):
    runtime_metrics.heartbeat()
    runtime_metrics.live_heartbeat_last_unix -= 30
    runtime_metrics.playout_connected = True
    result = await client.get("/health/ready")
    assert result.status_code == 503
    payload = await result.get_json()
    assert payload["heartbeat_ok"] is False


async def test_metrics_json_after_live_heartbeat(
    client: QuartClient,
    auth: dict,
):
    fake_md = {
        "artist": "Health",
        "title": "Probe",
        "source": "unittests",
        "remaining": "1",
        "elapsed": "1",
        "time": "2026-09-16T00:30:00+02:00",
        "next_jingle": {"rid": 1},
        "next_autodj": {"rid": 1},
    }
    posted = await client.post("/live", headers=auth, json=fake_md)
    assert posted.status_code == 200
    metrics = await (await client.get("/metrics.json")).get_json()
    assert metrics["live_heartbeat_total"] >= 1
    assert metrics["live_heartbeat_age_seconds"] is not None
    assert "playout_push_total" in metrics
    assert "metadata_relay_total" in metrics
    assert "queue_clean_removed_total" in metrics


async def test_interface_health(
    raw_app: CustomQuart,
    app_configration: dict,
    beets_integration,
):
    client = interface_factory(
        app_configration,
        False,
        beets_integration,
    ).test_client()
    live = await client.get("/health/live")
    assert live.status_code == 200
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    payload = await ready.get_json()
    assert payload["ready"] is True
    assert payload["db"] is True


async def test_watchdog_updates_playout_connected():
    Watchdog.liquidsoap_version = "2.4.5"
    runtime_metrics.playout_connected = True
    assert runtime_metrics.snapshot()["playout_connected"] is True
    with patch(
        "httpx.AsyncClient.get",
        side_effect=httpx.ConnectError("down"),
    ):
        runtime_metrics.playout_connected = False
        Watchdog.liquidsoap_version = "disconnected"
        assert runtime_metrics.snapshot()["playout_connected"] is False

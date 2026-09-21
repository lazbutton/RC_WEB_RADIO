import json
from pathlib import Path

import httpx

from radiotomate.scheduler import alerts, metrics


class _Spy:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, **payload):
        self.sent.append(payload)


def _settings(**over) -> alerts.AlertSettings:
    raw = {"enabled": True, "silence_seconds": 20, "heartbeat_max_age_seconds": 10}
    raw.update(over)
    return alerts.AlertSettings.from_config(raw)


async def test_silence_opens_once_then_resolves():
    spy = _Spy()
    monitor = alerts.AlertMonitor(_settings(cooldown_seconds=600), spy)
    quiet = {"silence_s": "25.0", "source": "autodj"}
    assert await monitor.step(quiet, heartbeat_age=1.0) == ["open:silence"]
    # still quiet, inside cooldown → no second notification
    assert await monitor.step(quiet, heartbeat_age=1.0) == []
    assert await monitor.step({"silence_s": "0.0", "source": "carts"}, 1.0) == [
        "resolved:silence"
    ]
    assert [s["resolved"] for s in spy.sent] == [False, True]
    assert spy.sent[0]["kind"] == "silence"
    assert "25 s" in spy.sent[0]["body"]


async def test_playout_down_needs_consecutive_failures_and_heartbeat_alert():
    spy = _Spy()
    monitor = alerts.AlertMonitor(_settings(), spy)
    emitted = []
    for _ in range(alerts.PLAYOUT_DOWN_AFTER):
        emitted += await monitor.step(None, heartbeat_age=None)
    assert emitted.count("open:heartbeat") == 1
    assert emitted.count("open:playout_down") == 1
    assert set(monitor.open) == {"heartbeat", "playout_down"}
    back = await monitor.step({"silence_s": "0", "source": "carts"}, heartbeat_age=0.5)
    assert sorted(back) == ["resolved:heartbeat", "resolved:playout_down"]
    assert monitor.open == {}


async def test_settings_disabled_without_section():
    assert alerts.AlertSettings.from_config(None).enabled is False
    assert alerts.AlertSettings.from_config({}).enabled is False
    conf = alerts.AlertSettings.from_config({"webhook_url": "http://x/hook"})
    assert conf.enabled is True
    assert conf.has_target is True
    assert conf.interval == alerts.DEFAULT_INTERVAL


async def test_notifier_posts_webhook_and_vikunja_task():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"token": "jwt-1"})
        if request.url.path.endswith("/tasks") and request.method == "PUT":
            return httpx.Response(201, json={"id": 42})
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = _settings(
        webhook_url="http://hook.local/alert",
        vikunja={
            "url": "http://vikunja.local",
            "project_id": 3,
            "label_id": 6,
            "username": "bot",
            "password": "pw",
        },
    )
    notifier = alerts.Notifier(settings, client)
    await notifier.send(
        kind="silence", resolved=False, title="[ALERTE] silence", body="25 s", host="h"
    )
    paths = [(c.method, c.url.host, c.url.path) for c in calls]
    assert paths == [
        ("POST", "hook.local", "/alert"),
        ("POST", "vikunja.local", "/api/v1/login"),
        ("PUT", "vikunja.local", "/api/v1/projects/3/tasks"),
        ("PUT", "vikunja.local", "/api/v1/tasks/42/labels"),
    ]
    task = json.loads(calls[2].content)
    assert task["title"] == "[ALERTE] silence"
    assert calls[2].headers["Authorization"] == "Bearer jwt-1"
    assert json.loads(calls[3].content) == {"label_id": 6}


async def test_metrics_snapshot_roundtrip(tmp_path: Path):
    reg = metrics.runtime_metrics
    reg.reset()
    reg.record_push("carts", "ok")
    reg.record_push("carts", "ok")
    reg.record_alert("silence")
    reg.tick_run_total = 7
    saved = await metrics.save_snapshot(tmp_path, history=True)
    assert saved["playout_push_total"] == {"carts:ok": 2}
    assert (tmp_path / metrics.SNAPSHOT_FILE).exists()
    history = (tmp_path / metrics.HISTORY_FILE).read_text().splitlines()
    assert len(history) == 1

    reg.reset()
    assert reg.playout_push_total == {}
    metrics.load_snapshot(tmp_path)
    assert reg.playout_push_total["carts:ok"] == 2
    assert reg.alerts_total["silence"] == 1
    assert reg.tick_run_total == 7
    assert reg.restored_from == saved["saved_at"]
    reg.reset()

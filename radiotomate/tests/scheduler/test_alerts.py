import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from radiotomate.scheduler import alerts, metrics

PARIS = ZoneInfo("Europe/Paris")


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

    # Recovery closes the very task that was opened, no second task.
    calls.clear()
    await notifier.send(
        kind="silence", resolved=True, title="[RÉTABLI] silence", body="ok", host="h"
    )
    paths = [(c.method, c.url.host, c.url.path) for c in calls]
    assert paths == [
        ("POST", "hook.local", "/alert"),
        ("POST", "vikunja.local", "/api/v1/tasks/42"),
    ]
    assert json.loads(calls[1].content)["done"] is True


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


def _paris(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    # 2026-09-21 is a Monday (weekday 0)
    return datetime(2026, 9, 21 + day, hour, minute, second, tzinfo=PARIS)


async def test_ef01_live_slot_without_encoder_opens_after_grace_and_resolves():
    spy = _Spy()
    monitor = alerts.AlertMonitor(_settings(live_grace_seconds=90), spy)
    monitor.live_slots = [
        alerts.LiveSlot(
            title="QG St Aignan",
            day_of_week=2,
            start_minute=20 * 60,
            end_minute=22 * 60,
        )
    ]
    clock = {"silence_s": "0.0", "source": "carts"}

    # Tuesday, nothing scheduled: quiet.
    assert await monitor.step(clock, 1.0, now=_paris(1, 20, 1)) == []
    # Wednesday 20:00:30, inside the grace period: still quiet.
    assert await monitor.step(clock, 1.0, now=_paris(2, 20, 0, 30)) == []
    # 20:02 with the clock on air → EF-01 incident, naming the safety net.
    assert await monitor.step(clock, 1.0, now=_paris(2, 20, 2)) == ["open:live_absent"]
    body = spy.sent[0]["body"]
    assert "QG St Aignan 20:00-22:00" in body
    assert "carts" in body
    # Encoder finally connects → resolved.
    live = {"silence_s": "0.0", "source": "stream"}
    assert await monitor.step(live, 1.0, now=_paris(2, 20, 5)) == [
        "resolved:live_absent"
    ]
    # Slot over without encoder: no incident either.
    assert await monitor.step(clock, 1.0, now=_paris(2, 22, 30)) == []


def test_ef01_playout_down_does_not_double_report():
    slot = alerts.LiveSlot("x", 2, 20 * 60, 21 * 60)
    assert (
        alerts.live_slot_without_encoder([slot], None, _paris(2, 20, 10), 90.0) is None
    )


async def test_icecast_output_down_opens_and_resolves():
    spy = _Spy()
    monitor = alerts.AlertMonitor(_settings(), spy)
    ok = {
        "silence_s": "0.0",
        "source": "autodj",
        "icecast_targets": "icecast:8000/button.mp3,radio.example:8000/button.mp3",
        "icecast_connected": "icecast:8000/button.mp3,radio.example:8000/button.mp3",
        "icecast_down": "",
    }
    assert await monitor.step(ok, 1.0) == []
    lost = dict(
        ok,
        icecast_connected="icecast:8000/button.mp3",
        icecast_down="radio.example:8000/button.mp3",
    )
    assert await monitor.step(lost, 1.0) == ["open:icecast_down"]
    assert "radio.example:8000/button.mp3" in spy.sent[0]["body"]
    assert await monitor.step(ok, 1.0) == ["resolved:icecast_down"]

"""Antenna alerts: silence, playout unreachable, heartbeat lost, live slot empty,
Icecast output (local or public relay) disconnected.

Runs inside the scheduler process, polls Liquidsoap ``/health`` and the runtime
metrics, and notifies once per incident (plus a recovery note) through:

* a generic JSON webhook (``alerts.webhook_url``),
* a Vikunja task (``alerts.vikunja``: url, project_id, label_id, token *or*
  username/password).

Configuration (``radiotomate.yaml``)::

    alerts:
      enabled: true
      interval_seconds: 15
      silence_seconds: 20
      heartbeat_max_age_seconds: 10
      cooldown_seconds: 900
      live_grace_seconds: 90
      webhook_url: ""
      vikunja:
        url: "http://127.0.0.1:3456"
        project_id: 1
        label_id: 0
        username: "nasgul-bot"
        password: "…"

EF-01 (``live_absent``): a weekly live slot (``Emission`` scope ``weekly``) is
on, the harbor has not connected ``live_grace_seconds`` after the slot start,
and the antenna is held by the safety net — the daypart clock (or its
fallback cart). The incident names the slot and what is actually playing, and
resolves when the encoder connects or the slot ends. Nothing is pushed: the
net is the clock, by construction, never silence.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from datetime import datetime
from time import time

import httpx

from radiotomate.domain.emission import (
    is_harbor_source,
    minute_of_day,
    now_paris,
    weekly_contains,
)
from radiotomate.quart import ShutdownError, or_shutdown
from radiotomate.scheduler.metrics import runtime_metrics
from radiotomate.scheduler.playout import gateway_for

_log = logging.getLogger(__name__)

DEFAULT_INTERVAL = 15.0
MIN_INTERVAL = 5.0
DEFAULT_SILENCE_SECONDS = 20.0
DEFAULT_HEARTBEAT_MAX_AGE = 10.0
DEFAULT_COOLDOWN = 900.0
DEFAULT_LIVE_GRACE = 90.0
PLAYOUT_DOWN_AFTER = 3  # consecutive failed /health polls


def _float(raw, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass
class AlertSettings:
    enabled: bool = False
    interval: float = DEFAULT_INTERVAL
    silence_seconds: float = DEFAULT_SILENCE_SECONDS
    heartbeat_max_age: float = DEFAULT_HEARTBEAT_MAX_AGE
    cooldown: float = DEFAULT_COOLDOWN
    live_grace: float = DEFAULT_LIVE_GRACE
    webhook_url: str = ""
    vikunja: dict = field(default_factory=dict)

    @classmethod
    def from_config(cls, raw) -> AlertSettings:
        raw = raw if isinstance(raw, dict) else {}
        vikunja = raw.get("vikunja") if isinstance(raw.get("vikunja"), dict) else {}
        return cls(
            enabled=bool(raw.get("enabled", bool(raw))),
            interval=max(
                MIN_INTERVAL, _float(raw.get("interval_seconds"), DEFAULT_INTERVAL)
            ),
            silence_seconds=_float(raw.get("silence_seconds"), DEFAULT_SILENCE_SECONDS),
            heartbeat_max_age=_float(
                raw.get("heartbeat_max_age_seconds"), DEFAULT_HEARTBEAT_MAX_AGE
            ),
            cooldown=_float(raw.get("cooldown_seconds"), DEFAULT_COOLDOWN),
            live_grace=_float(raw.get("live_grace_seconds"), DEFAULT_LIVE_GRACE),
            webhook_url=str(raw.get("webhook_url") or "").strip(),
            vikunja=dict(vikunja),
        )

    @property
    def has_target(self) -> bool:
        return bool(self.webhook_url) or bool(self.vikunja.get("url"))


@dataclass
class Incident:
    kind: str
    detail: str
    opened_at: float
    notified_at: float | None = None


@dataclass(frozen=True)
class LiveSlot:
    """A weekly live window, as stored on ``Emission`` (scope ``weekly``)."""

    title: str
    day_of_week: int
    start_minute: int
    end_minute: int

    def active(self, now: datetime) -> bool:
        return weekly_contains(
            now, self.day_of_week, self.start_minute, self.end_minute
        )

    def elapsed_seconds(self, now: datetime) -> float:
        """Seconds since the slot started (only meaningful when active)."""
        local = now_paris(now)
        return (minute_of_day(local) - self.start_minute) * 60 + local.second

    @property
    def label(self) -> str:
        return (
            f"{self.title or 'créneau live'} "
            f"{self.start_minute // 60:02d}:{self.start_minute % 60:02d}"
            f"-{self.end_minute // 60:02d}:{self.end_minute % 60:02d}"
        )


def live_slot_without_encoder(
    slots: list[LiveSlot], health: dict | None, now: datetime, grace: float
) -> str | None:
    """EF-01: return the incident detail when a live slot runs without harbor."""
    if health is None:
        return None  # playout_down covers this
    source = str(health.get("source") or "")
    if is_harbor_source(source):
        return None
    for slot in slots:
        if not slot.active(now):
            continue
        if slot.elapsed_seconds(now) < grace:
            continue
        net = source or "?"
        return (
            f"EF-01 {slot.label} : encodeur absent, filet horloge à l'antenne ({net})"
        )
    return None


class AlertMonitor:
    """Evaluate the antenna state and keep one open incident per kind."""

    def __init__(self, settings: AlertSettings, notifier: Notifier | None = None):
        self.settings = settings
        self.notifier = notifier
        self.open: dict[str, Incident] = {}
        self.playout_failures = 0
        self.host = socket.gethostname()
        self.live_slots: list[LiveSlot] = []

    # -- evaluation ------------------------------------------------------------

    def evaluate(
        self,
        health: dict | None,
        heartbeat_age: float | None,
        now: datetime | None = None,
    ) -> dict[str, str]:
        """Return the conditions currently true, keyed by kind."""
        active: dict[str, str] = {}
        live_absent = live_slot_without_encoder(
            self.live_slots, health, now or datetime.now(), self.settings.live_grace
        )
        if live_absent:
            active["live_absent"] = live_absent
        if health is None:
            self.playout_failures += 1
            if self.playout_failures >= PLAYOUT_DOWN_AFTER:
                active["playout_down"] = (
                    f"Liquidsoap /health injoignable ({self.playout_failures} essais)"
                )
        else:
            self.playout_failures = 0
            silence = _float(health.get("silence_s"), 0.0)
            source = str(health.get("source") or "?")
            if silence >= self.settings.silence_seconds:
                active["silence"] = f"{silence:.0f} s de silence (source {source})"
            down = [t for t in str(health.get("icecast_down") or "").split(",") if t]
            if down:
                active["icecast_down"] = "Sortie Icecast déconnectée : " + ", ".join(
                    sorted(down)
                )
        if heartbeat_age is None or heartbeat_age > self.settings.heartbeat_max_age:
            age = "jamais" if heartbeat_age is None else f"{heartbeat_age:.0f} s"
            active["heartbeat"] = f"Dernier battement Liquidsoap : {age}"
        return active

    async def step(
        self,
        health: dict | None,
        heartbeat_age: float | None,
        now: datetime | None = None,
    ) -> list[str]:
        """Update incidents; returns the notifications emitted (for tests/logs)."""
        active = self.evaluate(health, heartbeat_age, now)
        now = time()
        emitted: list[str] = []
        for kind, detail in active.items():
            incident = self.open.get(kind)
            if incident is None:
                incident = Incident(kind=kind, detail=detail, opened_at=now)
                self.open[kind] = incident
            incident.detail = detail
            due = (
                incident.notified_at is None
                or now - incident.notified_at >= self.settings.cooldown
            )
            if due:
                incident.notified_at = now
                runtime_metrics.record_alert(kind)
                await self._notify(incident, resolved=False)
                emitted.append(f"open:{kind}")
        for kind in list(self.open):
            if kind in active:
                continue
            incident = self.open.pop(kind)
            if incident.notified_at is not None:
                await self._notify(incident, resolved=True)
                emitted.append(f"resolved:{kind}")
        return emitted

    async def _notify(self, incident: Incident, *, resolved: bool) -> None:
        state = "RÉTABLI" if resolved else "ALERTE"
        title = f"[{state}] antenne {incident.kind} — {self.host}"
        lasted = time() - incident.opened_at
        body = (
            incident.detail
            if not resolved
            else (f"{incident.detail}\nDurée : {lasted:.0f} s")
        )
        _log.warning("%s: %s", title, body.replace("\n", " | "))
        if self.notifier is None:
            return
        try:
            await self.notifier.send(
                kind=incident.kind,
                resolved=resolved,
                title=title,
                body=body,
                host=self.host,
            )
        except Exception:
            _log.exception("alert notification failed for %s", incident.kind)


class Notifier:
    """Fan-out to the webhook and/or Vikunja."""

    def __init__(
        self, settings: AlertSettings, client: httpx.AsyncClient | None = None
    ):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=10.0)
        self._vikunja_jwt: str | None = None

    async def send(
        self, *, kind: str, resolved: bool, title: str, body: str, host: str
    ) -> None:
        payload = {
            "kind": kind,
            "resolved": resolved,
            "title": title,
            "body": body,
            "host": host,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        if self.settings.webhook_url:
            await self._webhook(payload)
        if self.settings.vikunja.get("url"):
            await self._vikunja(payload)

    async def _webhook(self, payload: dict) -> None:
        response = await self.client.post(self.settings.webhook_url, json=payload)
        response.raise_for_status()

    async def _vikunja_headers(self, *, force_login: bool = False) -> dict:
        conf = self.settings.vikunja
        token = str(conf.get("token") or "").strip()
        if token:
            return {"Authorization": f"Bearer {token}"}
        if self._vikunja_jwt is None or force_login:
            base = str(conf["url"]).rstrip("/")
            response = await self.client.post(
                f"{base}/api/v1/login",
                json={
                    "username": conf.get("username", ""),
                    "password": conf.get("password", ""),
                    "long_token": True,
                },
            )
            response.raise_for_status()
            self._vikunja_jwt = response.json()["token"]
        return {"Authorization": f"Bearer {self._vikunja_jwt}"}

    async def _vikunja(self, payload: dict) -> None:
        conf = self.settings.vikunja
        base = str(conf["url"]).rstrip("/")
        try:
            project_id = int(conf.get("project_id") or 0)
        except (TypeError, ValueError):
            project_id = 0
        if project_id <= 0:
            return
        task = {
            "title": payload["title"],
            "description": payload["body"].replace("\n", "<br>"),
            "done": bool(payload["resolved"]),
        }
        headers = await self._vikunja_headers()
        response = await self.client.put(
            f"{base}/api/v1/projects/{project_id}/tasks", json=task, headers=headers
        )
        if response.status_code == 401 and not conf.get("token"):
            headers = await self._vikunja_headers(force_login=True)
            response = await self.client.put(
                f"{base}/api/v1/projects/{project_id}/tasks",
                json=task,
                headers=headers,
            )
        response.raise_for_status()
        try:
            label_id = int(conf.get("label_id") or 0)
        except (TypeError, ValueError):
            label_id = 0
        if label_id > 0:
            task_id = response.json().get("id")
            if task_id:
                await self.client.put(
                    f"{base}/api/v1/tasks/{task_id}/labels",
                    json={"label_id": label_id},
                    headers=headers,
                )


async def _poll_health(app) -> dict | None:
    gateway = gateway_for(app.config["PLAYOUT_CLIENT"])
    result = await gateway.health()
    if not result.ok or not isinstance(result.payload, dict):
        return None
    return result.payload


async def load_live_slots(app) -> list[LiveSlot]:
    """Weekly live windows from the DB (cheap: a handful of rows)."""
    from radiotomate.models.emission import Emission

    db = app.extensions.get("sqlalchemy")
    if db is None:
        return []
    async with db.session() as session:
        rows = await Emission.all_weekly(session)
    slots = []
    for row in rows:
        if (
            row.day_of_week is None
            or row.start_minute is None
            or row.end_minute is None
        ):
            continue
        slots.append(
            LiveSlot(
                title=row.title,
                day_of_week=int(row.day_of_week),
                start_minute=int(row.start_minute),
                end_minute=int(row.end_minute),
            )
        )
    return slots


async def loop(app) -> None:
    settings = AlertSettings.from_config(app.config.get("ALERTS"))
    if not settings.enabled:
        return
    notifier = Notifier(settings) if settings.has_target else None
    monitor = AlertMonitor(settings, notifier)
    app.extensions["alert_monitor"] = monitor
    _log.info(
        "alerts on: silence>=%.0fs heartbeat>%.0fs targets=%s",
        settings.silence_seconds,
        settings.heartbeat_max_age,
        "webhook,vikunja" if notifier else "log",
    )
    while True:
        try:
            await or_shutdown(asyncio.sleep(settings.interval))
            try:
                monitor.live_slots = await load_live_slots(app)
            except Exception:
                _log.exception("alerts: cannot load live slots")
            health = await _poll_health(app)
            await monitor.step(health, runtime_metrics.heartbeat_age())
        except (ShutdownError, asyncio.CancelledError):
            break
        except Exception:
            _log.exception("alerts loop crashed")

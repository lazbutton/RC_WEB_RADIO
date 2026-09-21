"""Antenna state for the console: one JSON that says how the antenna is doing.

``GET /antenne/state.json`` (token) aggregates, without blocking on any of it:

* ``playout``  — Liquidsoap ``/health`` (source, silence, level, Icecast outputs)
* ``engine``   — heartbeat age, readiness, tick counters
* ``incidents``— open alerts from :mod:`radiotomate.scheduler.alerts`
* ``listeners``— per Icecast target (``host:port/mount``) from ``status-json.xsl``
* ``asrun``    — the last played rows of ``metadata_log``
* ``next_live_slot`` — next weekly live window (EF-01)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from time import monotonic, time

import httpx
from quart import Blueprint, current_app, jsonify
from sqlalchemy import select

from radiotomate.auth import token_required
from radiotomate.domain.emission import now_paris
from radiotomate.health import database_is_ready
from radiotomate.models import MetadataLog
from radiotomate.scheduler.alerts import LiveSlot, load_live_slots
from radiotomate.scheduler.health import _heartbeat_max_age, _playout_connected
from radiotomate.scheduler.metrics import runtime_metrics
from radiotomate.scheduler.playout import gateway_for

_log = logging.getLogger(__name__)

blueprint = Blueprint("antenne", __name__)

ASRUN_LIMIT = 8
LISTENERS_TTL = 10.0
LISTENERS_TIMEOUT = 3.0


def _float(raw, default: float | None = None) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _split(raw) -> list[str]:
    return [part for part in str(raw or "").split(",") if part]


def playout_state(health: dict | None) -> dict:
    if not health:
        return {"reachable": False}
    return {
        "reachable": True,
        "source": str(health.get("source") or ""),
        "silence_s": _float(health.get("silence_s"), 0.0),
        "rms_db": _float(health.get("rms_db")),
        "harbor_s": _float(health.get("harbor_s")),
        "voiceover": str(health.get("on") or "") == "true",
        "icecast_targets": _split(health.get("icecast_targets")),
        "icecast_connected": _split(health.get("icecast_connected")),
        "icecast_down": _split(health.get("icecast_down")),
    }


def incidents_state(app) -> list[dict]:
    monitor = app.extensions.get("alert_monitor")
    if monitor is None:
        return []
    now = time()
    rows = []
    for kind, incident in monitor.open.items():
        rows.append(
            {
                "kind": kind,
                "detail": incident.detail,
                "since_s": max(0, int(now - incident.opened_at)),
                "notified": incident.notified_at is not None,
            }
        )
    rows.sort(key=lambda row: -row["since_s"])
    return rows


class ListenerCache:
    """``status-json.xsl`` per Icecast host, refreshed at most every ``ttl`` s."""

    def __init__(self, ttl: float = LISTENERS_TTL):
        self.ttl = ttl
        self._at: dict[str, float] = {}
        self._data: dict[str, dict] = {}

    async def fetch(self, client: httpx.AsyncClient, host: str, port: int) -> dict:
        key = f"{host}:{port}"
        now = monotonic()
        if key in self._data and now - self._at.get(key, 0.0) < self.ttl:
            return self._data[key]
        result: dict = {}
        try:
            response = await client.get(
                f"http://{host}:{port}/status-json.xsl", timeout=LISTENERS_TIMEOUT
            )
            response.raise_for_status()
            stats = response.json().get("icestats") or {}
            sources = stats.get("source") or []
            if isinstance(sources, dict):
                sources = [sources]
            for src in sources:
                url = str(src.get("listenurl") or "")
                mount = url.rsplit("/", 1)[-1] if "/" in url else url
                result[mount] = {
                    "listeners": src.get("listeners"),
                    "listener_peak": src.get("listener_peak"),
                    "title": src.get("title"),
                }
        except Exception as exc:
            _log.debug("icecast status %s unreachable: %s", key, exc)
            result = {"__error__": str(exc)[:120]}
        self._at[key] = now
        self._data[key] = result
        return result


_listener_cache = ListenerCache()


def icecast_targets_from_config(outputs) -> list[dict]:
    targets = []
    for out in outputs or []:
        if not isinstance(out, dict) or out.get("driver") != "icecast":
            continue
        host = str(out.get("host") or "").strip()
        mount = str(out.get("mount") or "").strip().lstrip("/")
        try:
            port = int(float(out.get("port") or 8000))
        except (TypeError, ValueError):
            port = 8000
        if host and mount:
            targets.append({"host": host, "port": port, "mount": mount})
    return targets


async def listeners_state(app, targets: list[dict]) -> list[dict]:
    client: httpx.AsyncClient = (
        app.config.get("ANTENNE_HTTP_CLIENT") or httpx.AsyncClient()
    )
    owns_client = "ANTENNE_HTTP_CLIENT" not in app.config
    try:
        hosts = {(t["host"], t["port"]) for t in targets}
        stats = dict(
            zip(
                hosts,
                await asyncio.gather(
                    *(_listener_cache.fetch(client, host, port) for host, port in hosts)
                ),
                strict=True,
            )
        )
    finally:
        if owns_client:
            await client.aclose()
    rows = []
    for target in targets:
        per_host = stats.get((target["host"], target["port"]), {})
        mount = per_host.get(target["mount"])
        rows.append(
            {
                "target": f"{target['host']}:{target['port']}/{target['mount']}",
                "host": target["host"],
                "mount": target["mount"],
                "reachable": "__error__" not in per_host,
                "mounted": mount is not None,
                "listeners": (mount or {}).get("listeners"),
                "listener_peak": (mount or {}).get("listener_peak"),
            }
        )
    return rows


async def asrun_rows(session, limit: int = ASRUN_LIMIT) -> list[dict]:
    rows = await session.scalars(
        select(MetadataLog).order_by(MetadataLog.on_air.desc()).limit(limit)
    )
    out = []
    for log in rows:
        out.append(
            {
                "on_air": log.on_air.isoformat(timespec="seconds")
                if log.on_air
                else None,
                "source": log.source,
                "artist": log.artist or "",
                "title": log.title or "",
                "path": log.source_url or None,
                "rundown_item_id": log.rundown_item_id,
            }
        )
    return out


def next_live_slot(slots: list[LiveSlot], now: datetime) -> dict | None:
    """Closest weekly live window starting after ``now`` (or active right now)."""
    local = now_paris(now)
    best: tuple[float, LiveSlot] | None = None
    for slot in slots:
        if slot.active(local):
            return {
                "title": slot.title,
                "label": slot.label,
                "starts_in_s": 0,
                "active": True,
            }
        for days in range(8):
            day = local + timedelta(days=days)
            if day.weekday() != slot.day_of_week:
                continue
            start = day.replace(
                hour=slot.start_minute // 60,
                minute=slot.start_minute % 60,
                second=0,
                microsecond=0,
            )
            delta = (start - local).total_seconds()
            if delta <= 0:
                continue
            if best is None or delta < best[0]:
                best = (delta, slot)
            break
    if best is None:
        return None
    delta, slot = best
    return {
        "title": slot.title,
        "label": slot.label,
        "starts_in_s": int(delta),
        "active": False,
    }


async def build_state(app) -> dict:
    gateway = gateway_for(app.config["PLAYOUT_CLIENT"])
    health = await gateway.health()
    health_payload = (
        health.payload if health.ok and isinstance(health.payload, dict) else None
    )
    playout = playout_state(health_payload)

    heartbeat_age = runtime_metrics.heartbeat_age()
    engine = {
        "heartbeat_age_s": heartbeat_age,
        "heartbeat_ok": heartbeat_age is not None
        and heartbeat_age <= _heartbeat_max_age(),
        "playout_connected": _playout_connected(),
        "db_ok": await database_is_ready(),
        "tick_run_total": runtime_metrics.tick_run_total,
        "tick_gated_total": runtime_metrics.tick_gated_total,
    }

    targets = icecast_targets_from_config(app.config.get("PLAYOUT_OUTPUTS"))
    if not targets and playout.get("icecast_targets"):
        for raw in playout["icecast_targets"]:
            hostport, _, mount = raw.partition("/")
            host, _, port = hostport.partition(":")
            targets.append({"host": host, "port": int(port or 8000), "mount": mount})
    listeners = await listeners_state(app, targets) if targets else []
    connected = set(playout.get("icecast_connected") or [])
    for row in listeners:
        row["connected"] = (
            row["target"] in connected if playout.get("reachable") else None
        )

    db = app.extensions["sqlalchemy"]
    async with db.session() as session:
        asrun = await asrun_rows(session)
    try:
        slots = await load_live_slots(app)
    except Exception:
        _log.exception("antenne state: live slots unavailable")
        slots = []

    return {
        "at": datetime.now().isoformat(timespec="seconds"),
        "playout": playout,
        "engine": engine,
        "incidents": incidents_state(app),
        "listeners": listeners,
        "asrun": asrun,
        "next_live_slot": next_live_slot(slots, datetime.now()),
    }


@blueprint.get("/antenne/state.json")
@token_required
async def state_json():
    return jsonify(await build_state(current_app))

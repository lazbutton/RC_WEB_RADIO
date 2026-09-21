"""Small in-process runtime metrics registry for health and diagnostics.

Counters survive a restart: a JSON snapshot is written under ``DATA_ROOT`` at a
fixed cadence and reloaded at start, and one line per hour is appended to
``exports/metrics.jsonl`` so a week of antenna can be graphed after the fact.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from time import time

_log = logging.getLogger(__name__)

SNAPSHOT_FILE = "metrics.json"
HISTORY_FILE = Path("exports") / "metrics.jsonl"
DEFAULT_SNAPSHOT_INTERVAL = 30.0
MIN_SNAPSHOT_INTERVAL = 5.0
HISTORY_EVERY_SECONDS = 3600.0


class RuntimeMetrics:
    """Metrics deliberately kept dependency-free for the P0."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.live_heartbeat_total = 0
        self.live_heartbeat_last_unix: float | None = None
        self.tick_duration_seconds = 0.0
        self.tick_skipped_total = 0
        self.tick_run_total = 0
        self.tick_gated_total = 0
        self.playout_push_total: Counter[str] = Counter()
        self.metadata_relay_total: Counter[str] = Counter()
        self.playout_connected = False
        self.queue_clean_removed_total: Counter[str] = Counter()
        self.alerts_total: Counter[str] = Counter()
        self.started_unix = time()
        self.restored_from: str | None = None

    def heartbeat(self) -> None:
        self.live_heartbeat_total += 1
        self.live_heartbeat_last_unix = time()

    def heartbeat_age(self) -> float | None:
        if self.live_heartbeat_last_unix is None:
            return None
        return max(0.0, time() - self.live_heartbeat_last_unix)

    def record_push(self, queue: str, status: str) -> None:
        self.playout_push_total[f"{queue}:{status}"] += 1

    def record_metadata_relay(self, status: str) -> None:
        self.metadata_relay_total[status] += 1

    def record_queue_clean(self, queue: str, removed: int) -> None:
        self.queue_clean_removed_total[queue] += max(0, removed)

    def record_alert(self, kind: str) -> None:
        self.alerts_total[kind] += 1

    def snapshot(self) -> dict:
        return {
            "live_heartbeat_total": self.live_heartbeat_total,
            "live_heartbeat_age_seconds": self.heartbeat_age(),
            "tick_duration_seconds": self.tick_duration_seconds,
            "tick_skipped_total": self.tick_skipped_total,
            "tick_run_total": self.tick_run_total,
            "tick_gated_total": self.tick_gated_total,
            "playout_push_total": dict(self.playout_push_total),
            "metadata_relay_total": dict(self.metadata_relay_total),
            "playout_connected": self.playout_connected,
            "queue_clean_removed_total": dict(self.queue_clean_removed_total),
            "alerts_total": dict(self.alerts_total),
            "uptime_seconds": max(0.0, time() - self.started_unix),
            "restored_from": self.restored_from,
        }

    # -- persistence ---------------------------------------------------------

    def restore(self, data: dict) -> None:
        """Carry cumulative counters over a restart (gauges start fresh)."""
        if not isinstance(data, dict):
            return
        for name in (
            "live_heartbeat_total",
            "tick_skipped_total",
            "tick_run_total",
            "tick_gated_total",
        ):
            try:
                setattr(self, name, int(data.get(name) or 0))
            except (TypeError, ValueError):
                continue
        for name in (
            "playout_push_total",
            "metadata_relay_total",
            "queue_clean_removed_total",
            "alerts_total",
        ):
            raw = data.get(name)
            if isinstance(raw, dict):
                counter: Counter[str] = getattr(self, name)
                for key, value in raw.items():
                    try:
                        counter[str(key)] = int(value)
                    except (TypeError, ValueError):
                        continue
        self.restored_from = str(data.get("saved_at") or "") or None


runtime_metrics = RuntimeMetrics()


def _write_snapshot(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _append_history(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_snapshot(data_root: Path) -> None:
    path = Path(data_root) / SNAPSHOT_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    runtime_metrics.restore(raw)
    _log.info("metrics restored from %s", path)


async def save_snapshot(data_root: Path, *, history: bool = False) -> dict:
    payload = runtime_metrics.snapshot()
    payload["saved_at"] = datetime.now().isoformat(timespec="seconds")
    root = Path(data_root)
    await asyncio.to_thread(_write_snapshot, root / SNAPSHOT_FILE, payload)
    if history:
        await asyncio.to_thread(_append_history, root / HISTORY_FILE, payload)
    return payload


def _interval(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_SNAPSHOT_INTERVAL
    return max(MIN_SNAPSHOT_INTERVAL, value)


async def snapshot_loop(app) -> None:
    from radiotomate.quart import ShutdownError, or_shutdown

    root = Path(app.config["DATA_ROOT"])
    interval = _interval(app.config.get("METRICS_SNAPSHOT_INTERVAL"))
    load_snapshot(root)
    last_history = time()
    while True:
        try:
            await or_shutdown(asyncio.sleep(interval))
            history = time() - last_history >= HISTORY_EVERY_SECONDS
            await save_snapshot(root, history=history)
            if history:
                last_history = time()
        except (ShutdownError, asyncio.CancelledError):
            break
        except Exception:
            _log.exception("metrics snapshot loop crashed")

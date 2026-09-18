"""Small in-process runtime metrics registry for health and diagnostics."""

from __future__ import annotations

from collections import Counter
from time import time


class RuntimeMetrics:
    """Metrics deliberately kept dependency-free for the P0."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.live_heartbeat_total = 0
        self.live_heartbeat_last_unix: float | None = None
        self.tick_duration_seconds = 0.0
        self.tick_skipped_total = 0
        self.playout_push_total: Counter[str] = Counter()
        self.metadata_relay_total: Counter[str] = Counter()
        self.playout_connected = False
        self.queue_clean_removed_total: Counter[str] = Counter()

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

    def snapshot(self) -> dict:
        return {
            "live_heartbeat_total": self.live_heartbeat_total,
            "live_heartbeat_age_seconds": self.heartbeat_age(),
            "tick_duration_seconds": self.tick_duration_seconds,
            "tick_skipped_total": self.tick_skipped_total,
            "playout_push_total": dict(self.playout_push_total),
            "metadata_relay_total": dict(self.metadata_relay_total),
            "playout_connected": self.playout_connected,
            "queue_clean_removed_total": dict(self.queue_clean_removed_total),
        }


runtime_metrics = RuntimeMetrics()

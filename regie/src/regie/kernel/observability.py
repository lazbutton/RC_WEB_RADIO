from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from typing import Any

request_id: ContextVar[str] = ContextVar("request_id", default="")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id.get("")
        if rid:
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)[-2000:]
        for key in ("job_id", "user_id", "path", "status", "ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(as_json: bool = True, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if as_json else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


class Metrics:
    """Compteurs en mémoire, exposés sur /api/v1/metrics (texte façon Prometheus + JSON)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[str, float] = defaultdict(float)
        self.timings: dict[str, list[float]] = defaultdict(list)
        self.started = time.time()

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            self.counters[self._key(name, labels)] += value

    def observe(self, name: str, ms: float, **labels: str) -> None:
        with self._lock:
            bucket = self.timings[self._key(name, labels)]
            bucket.append(ms)
            if len(bucket) > 500:
                del bucket[: len(bucket) - 500]

    def _key(self, name: str, labels: dict[str, str]) -> str:
        if not labels:
            return name
        inner = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{inner}}}"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            timings = {}
            for key, values in self.timings.items():
                if not values:
                    continue
                ordered = sorted(values)
                timings[key] = {
                    "count": len(ordered),
                    "p50": round(ordered[len(ordered) // 2], 1),
                    "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1),
                    "max": round(ordered[-1], 1),
                }
            return {"uptime_s": int(time.time() - self.started), "counters": dict(self.counters), "timings": timings}

    def prometheus(self) -> str:
        snap = self.snapshot()
        lines = [f"regie_uptime_seconds {snap['uptime_s']}"]
        for key, value in snap["counters"].items():
            lines.append(f"regie_{key} {value}")
        for key, stats in snap["timings"].items():
            base, _, labels = key.partition("{")
            suffix = "{" + labels if labels else ""
            for stat, val in stats.items():
                lines.append(f"regie_{base}_{stat}{suffix} {val}")
        return "\n".join(lines) + "\n"

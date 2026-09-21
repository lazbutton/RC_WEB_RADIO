from __future__ import annotations

import itertools
import json
import queue
import threading
from collections import deque
from typing import Any

from regie.kernel.db import iso, utcnow


class EventBus:
    """Diffusion en mémoire vers les abonnés SSE, avec rejeu court (Last-Event-ID)."""

    def __init__(self, history: int = 500) -> None:
        self._lock = threading.Lock()
        self._subs: list[tuple[queue.Queue, str | None]] = []
        self._seq = itertools.count(1)
        self.history: deque[dict[str, Any]] = deque(maxlen=history)

    def subscribe(self, user_id: str | None = None) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.append((q, user_id))
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs = [(sub, uid) for sub, uid in self._subs if sub is not q]

    def publish(self, kind: str, data: dict[str, Any] | None = None, user_id: str | None = None) -> dict[str, Any]:
        event = {"id": next(self._seq), "type": kind, "at": iso(utcnow()), "data": data or {}, "user_id": user_id}
        with self._lock:
            self.history.append(event)
            subs = list(self._subs)
        for q, uid in subs:
            if user_id and uid and uid != user_id:
                continue
            try:
                q.put_nowait(event)
            except queue.Full:
                pass
        return event

    def since(self, last_id: int, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self.history if int(e["id"]) > last_id and (not e.get("user_id") or e.get("user_id") == user_id)]

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)


def sse_format(event: dict[str, Any]) -> str:
    body = json.dumps(event.get("data") or {}, ensure_ascii=False, default=str)
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {body}\n\n"
